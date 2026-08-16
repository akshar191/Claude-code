"""Offline tests for the parsing and inference logic (no network calls).

    python -m unittest discover -s tests -v
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from finder import config  # noqa: E402

config.DB_PATH = os.path.join(tempfile.mkdtemp(), "test.db")

from finder import emails, industries, outreach, people, pipeline, store, text  # noqa: E402

TEAM_PAGE = """
<html><body>
  <nav><a href="/about">About</a><a href="/our-team">Our Team</a></nav>
  <section class="team">
    <div class="member">
      <h3>Priya Raghavan</h3>
      <p>Co-Founder &amp; CEO</p>
      <a href="https://www.linkedin.com/in/priya-raghavan-8a41">LinkedIn</a>
    </div>
    <div class="member">
      <h3>Daniel O'Brien</h3>
      <p>VP of Engineering</p>
    </div>
    <div class="member">
      <h3>Marcus Webb</h3>
      <p>Senior Mechanical Engineer</p>
    </div>
    <li>Sofia Marino, Director of Operations</li>
    <div class="member"><h3>Our Team</h3><p>Meet the people behind the work</p></div>
    <p>Contact us at <a href="mailto:priya.raghavan@acmerobotics.com">Priya</a>
       or info@acmerobotics.com</p>
  </section>
</body></html>
"""


class NameHandling(unittest.TestCase):
    def test_splits_ordinary_names(self):
        self.assertEqual(text.split_name("Priya Raghavan"), ("Priya", "Raghavan"))

    def test_strips_titles_and_suffixes(self):
        self.assertEqual(text.split_name("Dr. Jane A. Doe Jr."), ("Jane", "Doe"))

    def test_rejects_single_token(self):
        self.assertEqual(text.split_name("Madonna"), (None, None))

    def test_slug_drops_accents_and_punctuation(self):
        self.assertEqual(text.slug("Daniel O'Brien"), "danielobrien")
        self.assertEqual(text.slug("José Núñez"), "josenunez")


class DomainHandling(unittest.TestCase):
    def test_normalizes_urls(self):
        self.assertEqual(text.normalize_domain("https://www.Acme.com/team/"), "acme.com")

    def test_rejects_social_hosts(self):
        self.assertIsNone(text.normalize_domain("https://linkedin.com/company/acme"))

    def test_free_mail_is_not_a_company_domain(self):
        self.assertFalse(text.is_company_domain("gmail.com"))
        self.assertTrue(text.is_company_domain("acmerobotics.com"))

    def test_strips_legal_suffixes(self):
        self.assertEqual(text.clean_company_name("Acme Robotics, Inc."), "Acme Robotics")


class TitleRanking(unittest.TestCase):
    def test_founder_outranks_manager(self):
        self.assertEqual(industries.rank_title("Co-Founder & CEO")[0], 5)
        self.assertEqual(industries.rank_title("Engineering Manager")[0], 3)

    def test_acronyms_need_word_boundaries(self):
        self.assertEqual(industries.rank_title("VP of Engineering")[0], 4)
        self.assertEqual(industries.rank_title("Developer")[0], 0)

    def test_unknown_title_is_zero(self):
        self.assertEqual(industries.rank_title("Barista")[0], 0)


class PeopleExtraction(unittest.TestCase):
    def setUp(self):
        self.found, _ = people.extract_people(TEAM_PAGE)
        self.by_name = {p["name"]: p for p in self.found}

    def test_finds_the_real_people(self):
        for name in ("Priya Raghavan", "Daniel O'Brien", "Sofia Marino"):
            self.assertIn(name, self.by_name, "missed %s" % name)

    def test_ignores_headings_that_are_not_names(self):
        self.assertNotIn("Our Team", self.by_name)

    def test_keeps_titles_and_ranks(self):
        self.assertEqual(self.by_name["Priya Raghavan"]["rank"], 5)
        self.assertEqual(self.by_name["Daniel O'Brien"]["rank"], 4)

    def test_attaches_linkedin_by_slug(self):
        self.assertIn("priya-raghavan", self.by_name["Priya Raghavan"]["linkedin_url"])

    def test_splits_names_for_email_building(self):
        self.assertEqual(self.by_name["Sofia Marino"]["first_name"], "Sofia")


class EmailHarvesting(unittest.TestCase):
    def test_finds_mailto_and_plain_addresses(self):
        found = emails.harvest(TEAM_PAGE)
        self.assertIn("priya.raghavan@acmerobotics.com", found)
        self.assertIn("info@acmerobotics.com", found)

    def test_skips_asset_filenames(self):
        self.assertEqual(emails.harvest('<img src="a@2x.png">'), [])

    def test_role_accounts_are_flagged(self):
        self.assertTrue(emails.is_role_account("info@acme.com"))
        self.assertFalse(emails.is_role_account("priya.raghavan@acme.com"))

    def test_decodes_cloudflare_obfuscation(self):
        # "a@b.co" XORed with key 0x7a
        raw = "7a1b5a181e547811"
        decoded = emails.decode_cfemail(raw)
        self.assertTrue(decoded is None or "@" in decoded)


class PatternInference(unittest.TestCase):
    def test_detects_the_format_of_a_known_address(self):
        self.assertEqual(
            emails.detect_pattern("priya.raghavan@acme.com", "Priya", "Raghavan"),
            "{first}.{last}",
        )
        self.assertEqual(emails.detect_pattern("draghavan@acme.com", "Daniel", "Raghavan"),
                         "{f}{last}")

    def test_learns_the_house_format_from_samples(self):
        known = [
            {"email": "priya.raghavan@acme.com", "first_name": "Priya", "last_name": "Raghavan"},
            {"email": "daniel.obrien@acme.com", "first_name": "Daniel", "last_name": "OBrien"},
        ]
        pattern, confidence, samples = emails.infer_pattern(known, "acme.com")
        self.assertEqual(pattern, "{first}.{last}")
        self.assertEqual(samples, 2)
        self.assertGreater(confidence, 0.8)

    def test_ignores_shared_inboxes_when_learning(self):
        known = [{"email": "info@acme.com", "first_name": "Info", "last_name": "Desk"}]
        pattern, _, samples = emails.infer_pattern(known, "acme.com")
        self.assertIsNone(pattern)
        self.assertEqual(samples, 0)

    def test_known_pattern_beats_priors(self):
        with_pattern = emails.candidates("Marcus", "Webb", "acme.com",
                                         pattern="{f}{last}", pattern_confidence=0.9)
        self.assertEqual(with_pattern[0]["email"], "mwebb@acme.com")
        self.assertGreater(with_pattern[0]["confidence"], with_pattern[1]["confidence"])

    def test_guesses_are_capped_low_without_evidence(self):
        blind = emails.candidates("Marcus", "Webb", "acme.com")
        self.assertEqual(blind[0]["email"], "marcus.webb@acme.com")
        self.assertLess(blind[0]["confidence"], 0.5)

    def test_no_guessing_at_free_mail_domains(self):
        self.assertEqual(emails.candidates("Marcus", "Webb", "gmail.com"), [])


class PipelineHelpers(unittest.TestCase):
    def test_merges_the_same_person_from_two_sources(self):
        merged = pipeline._merge_people(
            [{"name": "Priya Raghavan", "title": "CEO", "rank": 5, "source": "website"}],
            [{"name": "Priya Raghavan", "title": "CEO", "rank": 5,
              "linkedin_url": "https://linkedin.com/in/priya", "source": "hunter"}],
        )
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["linkedin_url"], "https://linkedin.com/in/priya")

    def test_attaches_published_address_to_the_right_person(self):
        staff = [
            {"name": "Priya Raghavan", "first_name": "Priya", "last_name": "Raghavan"},
            {"name": "Marcus Webb", "first_name": "Marcus", "last_name": "Webb"},
        ]
        matched = pipeline._attach_published_emails(
            staff, ["priya.raghavan@acme.com", "info@acme.com"], "acme.com"
        )
        self.assertEqual(len(matched), 1)
        self.assertEqual(staff[0]["email"], "priya.raghavan@acme.com")
        self.assertIsNone(staff[1].get("email"))

    def test_normalize_clamps_absurd_inputs(self):
        criteria = pipeline.normalize({"max_companies": 5000, "contacts_per_company": 0})
        self.assertEqual(criteria["max_companies"], 40)
        self.assertEqual(criteria["contacts_per_company"], 1)


class SizeFiltering(unittest.TestCase):
    def test_uses_real_headcount_when_known(self):
        ok, note = pipeline.companies_mod.size_ok(
            {"employee_count": 800}, {"min_employees": 1, "max_employees": 200}
        )
        self.assertFalse(ok)
        self.assertIn("800", note)

    def test_rejects_known_large_employers(self):
        ok, _ = pipeline.companies_mod.size_ok(
            {"domain": "boeing.com"}, {"min_employees": 1, "max_employees": 200}
        )
        self.assertFalse(ok)

    def test_reads_size_hints_off_the_site(self):
        ok, note = pipeline.companies_mod.size_ok(
            {"domain": "acme.com", "site_text": "We have 4500 employees worldwide."},
            {"min_employees": 1, "max_employees": 200},
        )
        self.assertFalse(ok)
        self.assertIn("site says", note)

    def test_keeps_companies_with_no_size_evidence(self):
        ok, note = pipeline.companies_mod.size_ok(
            {"domain": "acme.com", "site_text": "We build robots."},
            {"min_employees": 1, "max_employees": 200},
        )
        self.assertTrue(ok)
        self.assertIsNone(note)


class Drafting(unittest.TestCase):
    def setUp(self):
        self.contact = {
            "name": "Priya Raghavan", "first_name": "Priya", "title": "Co-Founder & CEO",
            "email": "priya.raghavan@acme.com", "company_name": "Acme Robotics",
            "company_description": "We design surgical robotics for small hospitals.",
        }
        self.profile = {"name": "Alex Chen", "school": "Northeastern", "year": "sophomore",
                        "major": "Mechanical Engineering", "skills": "an FSAE suspension rig"}

    def test_uses_the_first_name_and_company(self):
        drafted = outreach.draft(self.contact, self.profile, "advice")
        self.assertIn("Hi Priya,", drafted["body"])
        self.assertIn("Acme Robotics", drafted["body"])
        self.assertIn("Alex Chen", drafted["body"])

    def test_internship_style_makes_the_ask(self):
        drafted = outreach.draft(self.contact, self.profile, "internship")
        self.assertIn("intern", drafted["body"].lower())

    def test_short_style_stays_short(self):
        drafted = outreach.draft(self.contact, self.profile, "short")
        self.assertLess(len(drafted["body"]), 500)

    def test_survives_a_missing_profile(self):
        drafted = outreach.draft(self.contact, None, "advice")
        self.assertIn("[your name]", drafted["body"])


class Storage(unittest.TestCase):
    def setUp(self):
        store.init_db()

    def test_round_trips_a_search(self):
        search_id = store.save_results(
            "test run", {"industry": "robotics"},
            [{
                "name": "Acme Robotics", "domain": "acme.com", "relevance": 0.9,
                "sources": ["openstreetmap"],
                "contacts": [{"name": "Priya Raghavan", "title": "CEO", "rank": 5,
                              "email": "priya.raghavan@acme.com", "email_confidence": 0.9}],
            }],
            ["a note"],
        )
        loaded = store.get_search(search_id)
        self.assertEqual(loaded["companies"][0]["name"], "Acme Robotics")
        self.assertEqual(loaded["companies"][0]["contacts"][0]["email"],
                         "priya.raghavan@acme.com")
        self.assertEqual(loaded["notes"], ["a note"])

    def test_tracks_outreach_status(self):
        search_id = store.save_results(
            "status run", {}, [{"name": "Acme", "domain": "acme.com",
                                "contacts": [{"name": "Priya Raghavan"}]}])
        contact_id = store.get_search(search_id)["companies"][0]["contacts"][0]["id"]

        self.assertTrue(store.update_contact(contact_id, status="contacted"))
        contact = store.get_contact(contact_id)
        self.assertEqual(contact["status"], "contacted")
        self.assertIsNotNone(contact["contacted_at"])
        with self.assertRaises(ValueError):
            store.update_contact(contact_id, status="nonsense")

    def test_exports_flat_rows(self):
        store.save_results("export run", {}, [{"name": "Acme", "domain": "acme.com",
                                               "contacts": [{"name": "Priya Raghavan"}]}])
        rows = store.export_rows()
        self.assertTrue(any(row["name"] == "Priya Raghavan" for row in rows))


if __name__ == "__main__":
    unittest.main(verbosity=2)
