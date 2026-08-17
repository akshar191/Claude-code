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


class InternshipDraft(unittest.TestCase):
    def setUp(self):
        self.contact = {"name": "Bill Townsend", "first_name": "Bill",
                        "email": "bill@barrett.test"}
        self.company = {"name": "Barrett Technology", "domain": "barrett.test"}
        self.research = {
            "details": [{"text": "We build robotic arms and haptic devices used in "
                                 "rehabilitation research.",
                         "url": "https://barrett.test/products"}],
            "pages": ["https://barrett.test"],
        }
        self.profile = {"name": "Akshar Pathak"}

    def draft(self, research_data=None):
        return outreach.internship_draft(
            self.contact, self.company, research_data if research_data is not None
            else self.research, self.profile)

    def test_names_the_person_and_company(self):
        drafted = self.draft()
        self.assertIn("Hi Bill,", drafted["body"])
        self.assertIn("Barrett Technology", drafted["body"])

    def test_quotes_a_specific_detail(self):
        self.assertIn("robotic arms", self.draft()["body"])

    def test_asks_for_the_configured_term(self):
        self.assertIn("summer 2027", self.draft()["body"])

    def test_includes_the_background_from_config(self):
        body = self.draft()["body"]
        self.assertIn("Silverside Detectors", body)
        self.assertIn("detailing", body)

    def test_background_reads_as_english(self):
        # An earlier version produced "I started and run founder of a business".
        body = self.draft()["body"]
        self.assertNotIn("run founder", body)
        self.assertNotIn("the founder of a mobile detailing business.", body.replace(
            "I'm also founder of a mobile detailing business.", ""))

    def test_flags_the_line_that_needs_checking(self):
        drafted = self.draft()
        self.assertTrue(drafted["unverified"])
        self.assertIn("Your site says", drafted["why"])

    def test_still_drafts_when_research_found_nothing(self):
        drafted = self.draft({"details": [], "pages": []})
        self.assertIsNone(drafted["why"])
        self.assertFalse(drafted["unverified"])
        self.assertIn("Hi Bill,", drafted["body"])
        self.assertIn("summer 2027", drafted["body"])

    def test_subject_is_specific(self):
        subject = self.draft()["subject"]
        self.assertIn("Akshar Pathak", subject)
        self.assertIn("internship", subject.lower())


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
