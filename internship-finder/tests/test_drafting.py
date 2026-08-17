"""Tests for company research, internship drafting, and the Gmail integration.

Nothing here touches the network: research runs against a local fixture site and
the Gmail calls are checked at the URL/MIME level.

    python -m unittest tests.test_drafting -v
"""

import base64
import http.server
import os
import socketserver
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from finder import config  # noqa: E402

config.DB_PATH = os.path.join(tempfile.mkdtemp(), "drafting.db")
config.CRAWL_DELAY = 0.0

from finder import gmail, outreach, research  # noqa: E402

SITE = {
    "/robots.txt": "User-agent: *\nAllow: /\n",
    "/": """<html><head>
              <meta name="description" content="Barrett Technology builds robotic
                arms for rehabilitation research." /></head>
            <body><h1>Barrett Technology</h1>
              <p>We are a world-class, cutting-edge leader passionate about innovation.</p>
              <p>We build robotic arms and haptic devices used in rehabilitation
                 research and surgical training.</p>
              <nav><a href="/products">Products</a><a href="/careers">Careers</a></nav>
            </body></html>""",
    "/products": """<html><body>
              <p>Our WAM arm delivers 7 degrees of freedom with cable drives that
                 eliminate backlash.</p>
              <p>Subscribe to our newsletter. All rights reserved.</p>
            </body></html>""",
    "/careers": "<html><body><p>Come work with us.</p></body></html>",
}


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = SITE.get(self.path)
        if body is None:
            self.send_error(404)
            return
        encoded = body.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, *args):
        pass


class Research(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = socketserver.TCPServer(("127.0.0.1", 0), Handler)
        port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.found = research.gather({
            "name": "Barrett Technology",
            "domain": "barrett.test",
            "website": "http://127.0.0.1:%d/" % port,
        })

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def test_pulls_concrete_claims(self):
        texts = " ".join(d["text"] for d in self.found["details"])
        self.assertIn("robotic arms", texts)

    def test_skips_marketing_fluff(self):
        texts = " ".join(d["text"] for d in self.found["details"]).lower()
        for filler in ("world-class", "cutting-edge", "passionate about"):
            self.assertNotIn(filler, texts)

    def test_skips_boilerplate(self):
        texts = " ".join(d["text"] for d in self.found["details"]).lower()
        self.assertNotIn("all rights reserved", texts)
        self.assertNotIn("subscribe", texts)

    def test_follows_the_product_page(self):
        self.assertTrue(any("/products" in url for url in self.found["pages"]))

    def test_every_detail_cites_a_page(self):
        for detail in self.found["details"]:
            self.assertTrue(detail["url"].startswith("http"))

    def test_caps_the_number_of_details(self):
        self.assertLessEqual(len(self.found["details"]), 2)


class MarketingPageExtraction(unittest.TestCase):
    """A real Dexai-style page is headings and divs, not prose with full stops.

    Flattening it produced one run-on blob that the length cap rejected, so
    research returned nothing and the draft silently went generic.
    """

    def blocks(self, html):
        from bs4 import BeautifulSoup
        return research._sentences(BeautifulSoup(html, "html.parser"))

    def test_reads_divs_as_separate_claims(self):
        html = """<html><body><div class="hero">
                    <h1>Alfred</h1><h2>The robotic sous-chef</h2>
                    <div>Alfred is a collaborative robot arm that preps food in
                         commercial kitchens</div>
                  </div></body></html>"""
        blocks = self.blocks(html)
        self.assertIn(
            "Alfred is a collaborative robot arm that preps food in commercial kitchens",
            blocks,
        )

    def test_does_not_produce_one_run_on_blob(self):
        html = """<html><body>
                    <div><h1>Alfred</h1></div>
                    <div>Alfred is a collaborative robot arm</div>
                    <div>Works with your existing utensils</div>
                  </body></html>"""
        for block in self.blocks(html):
            self.assertLess(len(block), 120, "block is a run-on: %r" % block)

    def test_scores_a_product_claim_above_zero(self):
        score, why = research._score(
            "Alfred is a collaborative robot arm that preps food in commercial kitchens")
        self.assertGreater(score, 0, why)

    def test_explains_why_it_rejected_something(self):
        self.assertEqual(research._score("Learn more")[1], "too short")
        self.assertEqual(research._score("We are a world-class team of people")[1],
                         "marketing filler")
        self.assertEqual(research._score("Our mission is to change everything")[1],
                         "no concrete product noun")


class InternshipDraft(unittest.TestCase):
    def setUp(self):
        self.company = {"name": "Dexai Robotics", "domain": "dexai.test"}
        self.research = {
            "details": [{"text": "Alfred is a collaborative robot arm that preps "
                                 "food in commercial kitchens.",
                         "url": "https://dexai.test/"}],
            "pages": ["https://dexai.test/"],
        }
        self.profile = {"name": "Akshar Pathak"}
        self.ceo = {"name": "Dave Johnson", "first_name": "Dave", "rank": 5}
        self.engineer = {"name": "Sam Lee", "first_name": "Sam", "rank": 2}

    def draft(self, contact=None, research_data=None):
        return outreach.internship_draft(
            contact or self.ceo, self.company,
            self.research if research_data is None else research_data, self.profile)

    # --- the point of the whole feature ---------------------------------

    def test_contains_a_company_specific_line(self):
        body = self.draft()["body"]
        self.assertIn("collaborative robot arm", body)

    def test_swapping_the_company_changes_the_email(self):
        """The regression that started this: the draft was identical for any company."""
        first = self.draft()["body"]
        other = outreach.internship_draft(
            self.ceo, {"name": "Barrett Technology", "domain": "barrett.test"},
            {"details": [{"text": "We build robotic arms for rehabilitation research.",
                          "url": "https://barrett.test/"}], "pages": []},
            self.profile)["body"]
        self.assertNotEqual(first, other)
        self.assertNotIn("collaborative robot arm", other)

    def test_refuses_when_research_found_nothing(self):
        with self.assertRaises(outreach.ResearchFailed) as caught:
            self.draft(research_data={"details": [], "error": "site is JS-rendered"})
        self.assertIn("JS-rendered", caught.exception.reason)

    def test_refusal_carries_diagnostics(self):
        with self.assertRaises(outreach.ResearchFailed) as caught:
            self.draft(research_data={"details": [], "error": "nothing",
                                      "diagnostics": {"considered": 42}})
        self.assertEqual(caught.exception.diagnostics["considered"], 42)

    # --- applicant facts, verbatim --------------------------------------

    def test_uses_the_configured_wording_exactly(self):
        body = self.draft()["body"]
        self.assertIn(config.APPLICANT["work"], body)
        self.assertIn(config.APPLICANT["venture"], body)

    def test_does_not_call_the_intern_a_contractor(self):
        body = self.draft()["body"].lower()
        self.assertNotIn("contractor", body)
        self.assertIn("intern at silverside", body)
        self.assertIn("paid assembly work", body)

    # --- no hedging ------------------------------------------------------

    def test_never_offers_to_work_unpaid(self):
        for contact in (self.ceo, self.engineer):
            body = self.draft(contact)["body"].lower()
            for phrase in ("unpaid", "for free", "no pay", "without pay",
                           "free of charge", "not expecting to be paid"):
                self.assertNotIn(phrase, body, "hedging phrase %r in draft" % phrase)

    def test_no_self_deprecating_hedges(self):
        for contact in (self.ceo, self.engineer):
            body = self.draft(contact)["body"].lower()
            for phrase in ("something small", "just a student", "i know i'm only",
                           "sorry to bother", "i hate to ask", "even if it's just"):
                self.assertNotIn(phrase, body, "hedge %r in draft" % phrase)

    # --- the ask adapts to seniority -------------------------------------

    def test_founder_gets_the_direct_internship_ask(self):
        drafted = self.draft(self.ceo)
        self.assertEqual(drafted["ask"], "internship")
        self.assertIn("taking on an intern", drafted["body"])
        self.assertIn("summer 2027", drafted["body"])

    def test_engineer_gets_no_ask(self):
        drafted = self.draft(self.engineer)
        self.assertEqual(drafted["ask"], "about their work")
        self.assertNotIn("taking on an intern", drafted["body"])
        self.assertIn("not asking you for a job", drafted["body"])

    def test_the_two_drafts_are_actually_different(self):
        self.assertNotEqual(self.draft(self.ceo)["body"],
                            self.draft(self.engineer)["body"])
        self.assertNotEqual(self.draft(self.ceo)["subject"],
                            self.draft(self.engineer)["subject"])

    def test_director_is_below_the_direct_ask_line(self):
        director = {"name": "Sofia Marino", "first_name": "Sofia", "rank": 3}
        self.assertEqual(self.draft(director)["ask"], "about their work")

    def test_vp_is_at_or_above_it(self):
        vp = {"name": "Daniel O'Brien", "first_name": "Daniel", "rank": 4}
        self.assertEqual(self.draft(vp)["ask"], "internship")

    def test_flags_the_line_that_needs_checking(self):
        drafted = self.draft()
        self.assertTrue(drafted["unverified"])
        self.assertIn("Your site says", drafted["why"])


class GmailIntegration(unittest.TestCase):
    def setUp(self):
        config.GOOGLE_OAUTH_CLIENT_ID = "test-client"
        config.GOOGLE_OAUTH_CLIENT_SECRET = "test-secret"

    def test_requests_compose_scope_only(self):
        url = gmail.authorize_url("https://example.com/gmail/callback", "state123")
        self.assertIn("gmail.compose", url)
        # Sending is a scope we must never ask for.
        self.assertNotIn("gmail.send", url)
        self.assertNotIn("gmail.modify", url)
        self.assertNotIn("mail.google.com", url.split("scope=")[1].split("&")[0])

    def test_asks_for_a_refresh_token(self):
        url = gmail.authorize_url("https://example.com/gmail/callback", "state123")
        self.assertIn("access_type=offline", url)
        self.assertIn("state=state123", url)

    def test_builds_a_valid_message(self):
        raw = gmail.build_mime("bill@barrett.test", "Summer internship",
                               "Hi Bill,\n\nBody here.", to_name="Bill Townsend")
        decoded = base64.urlsafe_b64decode(raw).decode()
        self.assertIn("To: Bill Townsend <bill@barrett.test>", decoded)
        self.assertIn("Subject: Summer internship", decoded)
        self.assertIn("Body here.", decoded)

    def test_refuses_without_a_recipient(self):
        result, error = gmail.create_draft({"access_token": "x"}, "", "s", "b")
        self.assertIsNone(result)
        self.assertIn("recipient", error)

    def test_reports_when_it_cannot_refresh(self):
        tokens, error = gmail.refresh({"access_token": "expired"})
        self.assertIsNone(tokens)
        self.assertIn("reconnect", error)

    def test_unconfigured_is_detectable(self):
        config.GOOGLE_OAUTH_CLIENT_ID = ""
        self.assertFalse(gmail.configured())


if __name__ == "__main__":
    unittest.main(verbosity=2)
