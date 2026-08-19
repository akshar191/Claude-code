"""Offline tests for the parsing and inference logic (no network calls).

    python -m unittest discover -s tests -v
"""

import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from finder import config  # noqa: E402

config.DB_PATH = os.path.join(tempfile.mkdtemp(), "test.db")

from finder import emails, industries, outreach, people, pipeline, store, text, verify  # noqa: E402

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


class CredentialStripping(unittest.TestCase):
    """Real titles from a live barrett.com run came back as 'PhD CEO & Chairman'."""

    def test_strips_leading_credentials(self):
        self.assertEqual(people.strip_credentials("PhD CEO & Chairman"), "CEO & Chairman")
        self.assertEqual(people.strip_credentials("OTR/L President and CCO"),
                         "President and CCO")

    def test_strips_trailing_credentials(self):
        self.assertEqual(people.strip_credentials("Director of Engineering, PE"),
                         "Director of Engineering")

    def test_leaves_clean_titles_alone(self):
        self.assertEqual(people.strip_credentials("VP of Engineering"), "VP of Engineering")
        self.assertEqual(people.strip_credentials("Co-Founder & CEO"), "Co-Founder & CEO")

    def test_keeps_something_when_the_title_is_only_credentials(self):
        self.assertEqual(people.strip_credentials("PhD"), "PhD")

    def test_strips_stacked_certifications(self):
        # Real titles from a live vanderweil.com run.
        self.assertEqual(people.strip_credentials("LEED AP BD+C President Boston"),
                         "President Boston")
        self.assertEqual(people.strip_credentials("PE CEO Emeritus and Chairman"),
                         "CEO Emeritus and Chairman")

    def test_never_strips_the_job_itself(self):
        for title in ("CEO", "CTO & Co-Founder", "VP Engineering", "COO"):
            self.assertEqual(people.strip_credentials(title), title)

    def test_cleans_titles_during_extraction(self):
        html = """<html><body><div>
                  <h3>Bill Townsend</h3><p>PhD, CEO &amp; Chairman</p></div></body></html>"""
        found, _ = people.extract_people(html)
        person = {p["name"]: p for p in found}["Bill Townsend"]
        self.assertEqual(person["title"], "CEO & Chairman")
        self.assertEqual(person["rank"], 5)


class DepartmentsAreNotPeople(unittest.TestCase):
    """rwsullivan.com produced a contact called 'Facilities Management'."""

    def test_rejects_a_name_contained_in_its_own_title(self):
        html = """<html><body><div><h3>Facilities Management</h3>
                  <p>Director of Facilities Management</p></div></body></html>"""
        found, _ = people.extract_people(html)
        self.assertEqual(found, [])

    def test_still_accepts_a_real_person(self):
        html = """<html><body><div><h3>Sofia Marino</h3>
                  <p>Director of Facilities Management</p></div></body></html>"""
        found, _ = people.extract_people(html)
        self.assertEqual([p["name"] for p in found], ["Sofia Marino"])


class NoBlindGuessing(unittest.TestCase):
    """The point of the tool is addresses worth sending to, not plausible ones."""

    def test_suppresses_base_rate_guesses(self):
        self.assertEqual(
            emails.candidates("Marcus", "Webb", "acme.com", allow_priors=False), []
        )

    def test_still_builds_from_a_real_pattern(self):
        built = emails.candidates("Alex", "Vanderweil", "vanderweil.com",
                                  pattern="{f}{last}", pattern_confidence=0.85,
                                  allow_priors=False)
        self.assertEqual([c["email"] for c in built], ["avanderweil@vanderweil.com"])

    def test_offers_alternates_only_when_guessing_is_allowed(self):
        self.assertGreater(
            len(emails.candidates("Alex", "Vanderweil", "vanderweil.com",
                                  pattern="{f}{last}", allow_priors=True)),
            1,
        )


class ProviderPatternsDoNotCrash(unittest.TestCase):
    """A pattern Hunter sends that we do not understand must not take the
    company's whole contact list down with it."""

    def test_middle_initial_collapses_instead_of_raising(self):
        self.assertEqual(emails.render("{f}{m}{last}", "Alex", "Vanderweil"),
                         "avanderweil")
        self.assertEqual(emails.render("{first}.{m}.{last}", "Alex", "Vanderweil"),
                         "alex.vanderweil")

    def test_uses_the_middle_initial_when_we_have_one(self):
        self.assertEqual(emails.render("{f}{m}{last}", "Alex", "Vanderweil", middle="Jay"),
                         "ajvanderweil")

    def test_unknown_placeholder_returns_none(self):
        self.assertIsNone(emails.render("{nickname}.{last}", "Alex", "Vanderweil"))


class DepartmentNamesRejected(unittest.TestCase):
    """roboticstechno.com produced 'Supply Chain -- Principal Engineer'."""

    def test_rejects_department_style_names(self):
        for heading in ("Supply Chain", "Human Resources", "Business Development",
                        "Quality Assurance"):
            html = "<html><body><div><h3>%s</h3><p>Principal Engineer</p></div></body></html>" % heading
            found, _ = people.extract_people(html)
            self.assertEqual(found, [], "accepted %r as a person" % heading)

    def test_still_accepts_a_real_person_with_that_title(self):
        html = """<html><body><div><h3>Marcus Webb</h3>
                  <p>Principal Engineer</p></div></body></html>"""
        found, _ = people.extract_people(html)
        self.assertEqual([p["name"] for p in found], ["Marcus Webb"])


class ProviderProblemsSurface(unittest.TestCase):
    """A rejected API key must not look like 'this company has no staff'."""

    def test_company_level_notes_reach_the_caller(self):
        processed = [
            {"name": "A", "notes": ["Hunter (a.com): auth rejected (check the API key)"],
             "contacts": [], "size_ok": True},
            {"name": "B", "notes": ["Hunter (b.com): auth rejected (check the API key)"],
             "contacts": [], "size_ok": True},
        ]
        problems = {}
        for company in processed:
            for note in company["notes"]:
                provider, _, reason = note.partition(": ")
                summary = "%s: %s" % (provider.split(" (")[0], reason)
                problems[summary] = problems.get(summary, 0) + 1
        self.assertEqual(problems,
                         {"Hunter: auth rejected (check the API key)": 2})


class FakeContactsFromLiveRuns(unittest.TestCase):
    """Every string here is one this tool actually produced as a 'person'."""

    def extract(self, html):
        found, _ = people.extract_people("<html><body><div>%s</div></body></html>" % html)
        return [(p["name"], p["title"]) for p in found]

    def test_rejects_a_name_with_a_dropped_leading_token(self):
        # Produced "Dinne Flansbury" -- silently losing the "M".
        self.assertEqual(
            self.extract("<h3>M Dinne Flansbury</h3><p>Director of Operations</p>"), []
        )

    def test_rejects_headlines_with_a_colon(self):
        self.assertEqual(
            self.extract("<h3>Welcoming Hiten</h3><p>Sonpal: RISE Robotics' New CEO</p>"),
            [],
        )

    def test_rejects_an_announcement_run_together(self):
        self.assertEqual(
            self.extract("<div>Welcoming Hiten Sonpal: RISE Robotics' New CEO</div>"), []
        )

    def test_rejects_department_headings(self):
        self.assertEqual(
            self.extract("<h3>Supply Chain</h3><p>Principal Engineer</p>"), []
        )
        self.assertEqual(
            self.extract(
                "<h3>Facilities Management</h3><p>Director of Facilities Management</p>"
            ),
            [],
        )

    def test_still_finds_real_people(self):
        self.assertEqual(
            self.extract("<h3>Marcus Webb</h3><p>Principal Engineer</p>"),
            [("Marcus Webb", "Principal Engineer")],
        )
        self.assertEqual(
            self.extract("<h3>Daniel O'Brien</h3><p>VP of Engineering</p>"),
            [("Daniel O'Brien", "VP of Engineering")],
        )

    def test_keeps_a_genuine_middle_initial(self):
        self.assertEqual(
            self.extract("<h3>William J. Leuci</h3><p>President</p>"),
            [("William J. Leuci", "President")],
        )


class DirectorySitesSkipped(unittest.TestCase):
    """MassRobotics is a hub; its site lists other companies' founders, which
    produced colin.angle@massrobotics.org for the founder of iRobot."""

    def test_detects_a_membership_organisation(self):
        is_directory, evidence = pipeline.companies_mod.looks_like_directory({
            "name": "MassRobotics",
            "domain": "massrobotics.org",
            "site_text": "We are a nonprofit hub for the startup community. "
                         "Our members include...",
        })
        self.assertTrue(is_directory)
        self.assertIsNotNone(evidence)

    def test_leaves_an_ordinary_company_alone(self):
        is_directory, _ = pipeline.companies_mod.looks_like_directory({
            "name": "Barrett Technology",
            "domain": "barrett.com",
            "site_text": "We build robotic arms and haptic devices for research.",
        })
        self.assertFalse(is_directory)

    def test_one_weak_signal_is_not_enough(self):
        is_directory, _ = pipeline.companies_mod.looks_like_directory({
            "name": "Acme Robotics",
            "domain": "acme.com",
            "site_text": "We serve the robotics ecosystem with precision parts.",
        })
        self.assertFalse(is_directory)


class CrawlBudget(unittest.TestCase):
    """Runs were spending fetches on /careers/workplace-culture."""

    def pages_from(self, hrefs):
        from bs4 import BeautifulSoup
        html = "<html><body>%s</body></html>" % "".join(
            '<a href="%s">link</a>' % href for href in hrefs
        )
        return people.candidate_pages(
            "https://acme.com/", BeautifulSoup(html, "html.parser"), 20
        )

    def test_skips_deep_pages_that_never_list_people(self):
        followed = self.pages_from([
            "/careers/workplace-culture",
            "/who-we-are/giving-back",
            "/careers/internship-program",
            "/who-we-are/honoring-our-founder",
        ])
        self.assertEqual(followed, [])

    def test_still_follows_the_pages_that_do(self):
        followed = self.pages_from([
            "/who-we-are/our-people", "/leadership", "/about", "/contact",
        ])
        for path in ("/who-we-are/our-people", "/leadership", "/about", "/contact"):
            self.assertIn("https://acme.com" + path, followed)

    def test_ranks_leadership_above_contact(self):
        followed = self.pages_from(["/contact", "/leadership"])
        self.assertEqual(followed[0], "https://acme.com/leadership")


class ProviderCache(unittest.TestCase):
    """Hunter free tier is 50/month -- a repeated search must not re-spend."""

    def setUp(self):
        store.init_db()

    def test_round_trips_a_payload(self):
        store.cache_put("hunter:domain:acme.com", {"pattern": "{f}{last}"})
        self.assertEqual(store.cache_get("hunter:domain:acme.com"),
                         {"pattern": "{f}{last}"})

    def test_misses_on_an_unknown_key(self):
        self.assertIsNone(store.cache_get("hunter:domain:never-looked-up.com"))

    def test_expires_after_the_ttl(self):
        store.cache_put("hunter:domain:stale.com", {"pattern": "{first}"})
        self.assertIsNone(store.cache_get("hunter:domain:stale.com", ttl=-1))


class HunterCacheStopsSpending(unittest.TestCase):
    """A repeated domain must cost zero API calls -- Hunter's free tier is 50."""

    def setUp(self):
        from finder import providers, web

        store.init_db()
        self.providers = providers
        self.web = web
        self.original_api = web.api
        self.original_key = config.HUNTER_API_KEY
        config.HUNTER_API_KEY = "test-key"
        self.calls = []

        def counting_api(method, url, **kwargs):
            self.calls.append(url)
            return {"data": {"pattern": "{f}{last}", "organization": "Acme",
                             "emails": []}}, None

        web.api = counting_api
        providers.web.api = counting_api

    def tearDown(self):
        self.web.api = self.original_api
        self.providers.web.api = self.original_api
        config.HUNTER_API_KEY = self.original_key

    def test_second_lookup_of_a_domain_makes_no_api_call(self):
        domain = "cache-test-%d.com" % time.time()
        first, error = self.providers.hunter_domain_search(domain)
        self.assertIsNone(error)
        self.assertEqual(len(self.calls), 1, "first lookup should hit the API")

        second, error = self.providers.hunter_domain_search(domain)
        self.assertIsNone(error)
        self.assertEqual(len(self.calls), 1,
                         "cached domain must not trigger a second API call")
        self.assertEqual(second["pattern"], first["pattern"])

    def test_a_different_domain_still_costs_a_call(self):
        self.providers.hunter_domain_search("cache-a-%d.com" % time.time())
        self.providers.hunter_domain_search("cache-b-%d.com" % time.time())
        self.assertEqual(len(self.calls), 2)

    def test_per_person_lookups_are_cached_too(self):
        domain = "finder-cache-%d.com" % time.time()

        def finder_api(method, url, **kwargs):
            self.calls.append(url)
            return {"data": {"email": "mflansbury@%s" % domain, "score": 90}}, None

        self.web.api = finder_api
        self.providers.web.api = finder_api

        self.providers.hunter_email_finder(domain, "Dinne", "Flansbury")
        self.providers.hunter_email_finder(domain, "Dinne", "Flansbury")
        self.assertEqual(len(self.calls), 1,
                         "cached person lookup must not trigger a second API call")


class ProviderNamesAreValidated(unittest.TestCase):
    """Hunter returned "M Dinne Flansbury" and it went straight to output --
    provider names never passed through the parser's validation."""

    def test_bare_initial_is_punctuated(self):
        self.assertEqual(text.clean_person_name("M Dinne Flansbury"),
                         "M. Dinne Flansbury")

    def test_ordinary_names_are_untouched(self):
        self.assertEqual(text.clean_person_name("Ryan Jones"), "Ryan Jones")
        self.assertEqual(text.clean_person_name("Daniel O'Brien"), "Daniel O'Brien")

    def test_rejects_unusable_names(self):
        self.assertIsNone(text.clean_person_name("Flansbury"))
        self.assertIsNone(text.clean_person_name("M D"))
        self.assertIsNone(text.clean_person_name(""))
        self.assertIsNone(text.clean_person_name("Welcoming Hiten: New CEO"))


class OverpassQuery(unittest.TestCase):
    def test_includes_name_regex_clauses_for_recall(self):
        query = pipeline.companies_mod._overpass_query(
            ['["office"="engineering"]'], "[Rr]obotic", 42.36, -71.05, 40000
        )
        self.assertIn('["office"="engineering"]', query)
        self.assertIn('["name"~"[Rr]obotic"]', query)
        self.assertIn("around:40000", query)

    def test_name_clauses_are_scoped_to_business_tags(self):
        query = pipeline.companies_mod._overpass_query([], "[Rr]obotic", 42.36, -71.05, 40000)
        # Never a bare name search -- that would match every object in the city.
        for line in query.splitlines():
            if '["name"~' in line:
                self.assertTrue(
                    any(scope in line for scope in
                        ('["office"]', '["man_made"="works"]', '["craft"]', '["industrial"]')),
                    "unscoped name search: %s" % line,
                )

    def test_omits_name_clauses_when_there_is_no_hint(self):
        query = pipeline.companies_mod._overpass_query(
            ['["office"="company"]'], None, 42.36, -71.05, 40000
        )
        self.assertNotIn('["name"~', query)


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


class PaidVerificationBudget(unittest.TestCase):
    """Hunter's free tier is 25/month -- one search must not drain it."""

    def setUp(self):
        self.original_key = config.HUNTER_API_KEY
        self.original_budget = config.HUNTER_VERIFY_BUDGET
        config.HUNTER_API_KEY = "test-key"
        config.HUNTER_VERIFY_BUDGET = 3
        verify.reset_budget()

    def tearDown(self):
        config.HUNTER_API_KEY = self.original_key
        config.HUNTER_VERIFY_BUDGET = self.original_budget
        verify.reset_budget()

    def test_stops_spending_once_the_budget_is_gone(self):
        self.assertEqual([verify._claim_paid_call() for _ in range(5)],
                         [True, True, True, False, False])

    def test_budget_resets_between_searches(self):
        for _ in range(3):
            verify._claim_paid_call()
        self.assertFalse(verify._claim_paid_call())
        verify.reset_budget()
        self.assertTrue(verify._claim_paid_call())

    def test_no_spending_without_a_key(self):
        config.HUNTER_API_KEY = ""
        # An unroutable domain: falls out at the MX check before any paid call.
        result = verify.verify("someone@invalid-domain-that-cannot-exist-xyz.test")
        self.assertEqual(result["status"], "invalid")
        self.assertEqual(verify._paid_calls, 0)


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


class EmployeeRangeParsing(unittest.TestCase):
    """Hunter reports headcount as a range string, not a number."""

    def parse(self, value):
        from finder.providers import parse_employee_range
        return parse_employee_range(value)

    def test_parses_a_plain_range(self):
        self.assertEqual(self.parse("1-10"), (1, 10))
        self.assertEqual(self.parse("51-200"), (51, 200))

    def test_parses_an_open_ended_range(self):
        # A lower bound with no ceiling is still enough to reject a company.
        self.assertEqual(self.parse("10001+"), (10001, None))

    def test_parses_a_bare_number_as_exact(self):
        self.assertEqual(self.parse("50"), (50, 50))
        self.assertEqual(self.parse(10), (10, 10))

    def test_handles_thousands_separators(self):
        self.assertEqual(self.parse("1,001-5,000"), (1001, 5000))

    def test_unusable_values_parse_to_nothing(self):
        for value in ("", None, "unknown", "lots", "~"):
            self.assertEqual(self.parse(value), (None, None))


class HeadcountFilter(unittest.TestCase):
    """A range is judged by overlap: reject only what cannot fit."""

    def size_ok(self, company, maximum=200, minimum=1):
        return pipeline.companies_mod.size_ok(
            company, {"min_employees": minimum, "max_employees": maximum})

    def test_keeps_a_small_company(self):
        ok, note = self.size_ok({"employees_min": 1, "employees_max": 10,
                                 "employees_raw": "1-10"})
        self.assertTrue(ok)
        self.assertIn("1-10", note)

    def test_keeps_a_range_that_straddles_the_limit(self):
        # 51-200 against a 200 ceiling could fit, so it is not rejected.
        self.assertTrue(self.size_ok({"employees_min": 51, "employees_max": 200,
                                      "employees_raw": "51-200"})[0])

    def test_rejects_a_range_entirely_above_the_limit(self):
        ok, note = self.size_ok({"employees_min": 201, "employees_max": 500,
                                 "employees_raw": "201-500"})
        self.assertFalse(ok)
        self.assertIn("above the limit", note)

    def test_rejects_an_open_ended_large_range(self):
        self.assertFalse(self.size_ok({"employees_min": 10001, "employees_max": None,
                                       "employees_raw": "10001+"})[0])

    def test_rejects_a_range_below_a_minimum(self):
        ok, note = self.size_ok({"employees_min": 1, "employees_max": 10,
                                 "employees_raw": "1-10"}, minimum=50)
        self.assertFalse(ok)
        self.assertIn("below the minimum", note)

    def test_an_exact_count_still_wins(self):
        ok, note = self.size_ok({"employee_count": 12, "employees_min": 201,
                                 "employees_max": 500})
        self.assertTrue(ok)
        self.assertIn("headcount 12", note)

    def test_no_data_still_falls_through_to_the_heuristics(self):
        ok, note = self.size_ok({"domain": "acme.com", "site_text": "We build robots."})
        self.assertTrue(ok)
        self.assertIsNone(note)


class CategoryIsNeverUsed(unittest.TestCase):
    """Hunter classified a robotics company as "Beverages" because its robot
    handles food. Filtering on that would drop the target companies."""

    def test_the_provider_result_carries_no_category(self):
        from finder import providers, store as store_mod
        from finder import web as web_mod

        original_api, original_key = web_mod.api, config.HUNTER_API_KEY
        config.HUNTER_API_KEY = "test-key"
        store_mod.init_db()

        def fake_api(method, url, **kwargs):
            return {"data": {
                "name": "Dexai Robotics",
                "category": {"industry": "Beverages", "sector": "Consumer Goods"},
                "metrics": {"employees": "11-50"},
            }}, None

        web_mod.api = fake_api
        providers.web.api = fake_api
        try:
            found, error = providers.hunter_company_find(
                "dexai-category-test-%d.example" % time.time())
        finally:
            web_mod.api = original_api
            providers.web.api = original_api
            config.HUNTER_API_KEY = original_key

        self.assertIsNone(error)
        self.assertEqual(found["employees_min"], 11)
        # The classification must not travel with the record at all.
        self.assertNotIn("category", found)
        self.assertNotIn("industry", found)
        self.assertNotIn("sector", found)

    def test_relevance_scoring_ignores_any_category_field(self):
        # Even if a category leaked in, it must not decide whether a hardware
        # company survives the industry filter.
        company = {"name": "Dexai Robotics", "domain": "dexai.test",
                   "category": "Beverages",
                   "site_text": "We build a collaborative robot arm for kitchens."}
        score = pipeline.companies_mod.relevance(company, {"industry": "robotics"})
        self.assertGreater(score, 0)
