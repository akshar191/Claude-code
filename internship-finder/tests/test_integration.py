"""End-to-end test of the crawl -> people -> email pipeline against a local
fixture site, so it runs without touching the internet.

    python -m unittest tests.test_integration -v
"""

import http.server
import os
import socketserver
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from finder import config  # noqa: E402

config.DB_PATH = os.path.join(tempfile.mkdtemp(), "integration.db")
config.CRAWL_DELAY = 0.0  # no need to be polite to ourselves

from finder import pipeline  # noqa: E402

PAGES = {
    "/robots.txt": ("text/plain", "User-agent: *\nAllow: /\nDisallow: /private\n"),
    "/": (
        "text/html",
        """<html><body>
             <h1>Acme Robotics</h1>
             <p>We build surgical robotics for small hospitals.</p>
             <nav>
               <a href="/leadership">Leadership</a>
               <a href="/careers">Careers</a>
               <a href="/private/secrets">Internal</a>
             </nav>
           </body></html>""",
    ),
    "/leadership": (
        "text/html",
        """<html><body><section>
             <div><h3>Priya Raghavan</h3><p>Co-Founder &amp; CEO</p></div>
             <div><h3>Daniel O'Brien</h3><p>VP of Engineering</p></div>
             <div><h3>Marcus Webb</h3><p>Junior Technician</p></div>
             <p>Reach Priya at
                <a href="mailto:priya.raghavan@acmerobotics.test">this address</a>.</p>
           </section></body></html>""",
    ),
    "/careers": ("text/html", "<html><body><p>Email jobs@acmerobotics.test</p></body></html>"),
    "/private/secrets": ("text/html", "<html><body>should never be fetched</body></html>"),
}


class FixtureHandler(http.server.BaseHTTPRequestHandler):
    hits = []

    def do_GET(self):
        FixtureHandler.hits.append(self.path)
        content_type, body = PAGES.get(self.path, (None, None))
        if body is None:
            self.send_error(404)
            return
        encoded = body.encode()
        self.send_response(200)
        self.send_header("Content-Type", "%s; charset=utf-8" % content_type)
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, *args):
        pass


class Pipeline(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = socketserver.TCPServer(("127.0.0.1", 0), FixtureHandler)
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

        FixtureHandler.hits = []
        cls.company = pipeline.process_company(
            {
                "name": "Acme Robotics",
                "domain": "acmerobotics.test",
                "website": "http://127.0.0.1:%d/" % cls.port,
                "source": "openstreetmap",
            },
            pipeline.normalize({"min_seniority": 3, "verify_emails": False,
                                "max_employees": 200}),
        )
        cls.contacts = {c["name"]: c for c in cls.company["contacts"]}

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def test_follows_the_leadership_link(self):
        self.assertIn("/leadership", FixtureHandler.hits)

    def test_honours_robots_disallow(self):
        self.assertNotIn("/private/secrets", FixtureHandler.hits)

    def test_finds_the_senior_people(self):
        self.assertIn("Priya Raghavan", self.contacts)
        self.assertIn("Daniel O'Brien", self.contacts)

    def test_filters_out_juniors(self):
        self.assertNotIn("Marcus Webb", self.contacts)

    def test_uses_the_published_address_verbatim(self):
        priya = self.contacts["Priya Raghavan"]
        self.assertEqual(priya["email"], "priya.raghavan@acmerobotics.test")
        self.assertIn("published", priya["email_basis"])
        self.assertGreaterEqual(priya["email_confidence"], 0.9)

    def test_learns_the_pattern_and_applies_it_to_the_others(self):
        self.assertEqual(self.company["email_pattern"], "{first}.{last}")
        daniel = self.contacts["Daniel O'Brien"]
        self.assertEqual(daniel["email"], "daniel.obrien@acmerobotics.test")
        self.assertIn("guessed", daniel["email_basis"])

    def test_a_guess_is_less_confident_than_a_published_address(self):
        self.assertLess(
            self.contacts["Daniel O'Brien"]["email_confidence"],
            self.contacts["Priya Raghavan"]["email_confidence"],
        )

    def test_no_alternates_unless_guessing_is_enabled(self):
        # Default is one pattern-backed address, not a spread of guesses.
        self.assertEqual(self.contacts["Daniel O'Brien"]["alternates"], [])

    def test_alternates_come_back_when_guessing_is_asked_for(self):
        company = pipeline.process_company(
            {
                "name": "Acme Robotics",
                "domain": "acmerobotics.test",
                "website": "http://127.0.0.1:%d/" % self.port,
                "source": "openstreetmap",
            },
            pipeline.normalize({"min_seniority": 3, "verify_emails": False,
                                "guess_emails": True}),
        )
        daniel = {c["name"]: c for c in company["contacts"]}["Daniel O'Brien"]
        self.assertTrue(daniel["alternates"])

    def test_collects_published_shared_inboxes(self):
        self.assertIn("jobs@acmerobotics.test", self.company["published_emails"])


HUNTER_PAGES = {
    "/robots.txt": ("text/plain", "User-agent: *\nAllow: /\n"),
    "/": (
        "text/html",
        """<html><body><h1>Vanderweil Engineers</h1>
             <nav><a href="/leadership">Leadership</a></nav></body></html>""",
    ),
    "/leadership": (
        "text/html",
        """<html><body>
             <div><h3>Alex Vanderweil</h3><p>LEED AP BD+C President</p></div>
             <div><h3>Eli Sherman</h3><p>PE CEO Emeritus and Chairman</p></div>
           </body></html>""",
    ),
}


class HunterHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        content_type, body = HUNTER_PAGES.get(self.path, (None, None))
        if body is None:
            self.send_error(404)
            return
        encoded = body.encode()
        self.send_response(200)
        self.send_header("Content-Type", "%s; charset=utf-8" % content_type)
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, *args):
        pass


class HunterPatternIsUsed(unittest.TestCase):
    """The live run guessed alex.vanderweil@ when Hunter said the format is
    {f}{last}, i.e. avanderweil@. Every named address was wrong."""

    @classmethod
    def setUpClass(cls):
        from finder import people as people_mod
        from finder import providers

        cls.server = socketserver.TCPServer(("127.0.0.1", 0), HunterHandler)
        port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

        cls.original_search = providers.hunter_domain_search
        cls.original_key = config.HUNTER_API_KEY
        config.HUNTER_API_KEY = "test-key"

        def fake_domain_search(domain, limit=25):
            return {
                "pattern": "{f}{last}",
                "organization": "Vanderweil Engineers",
                # A confirmed address whose title does not clear "director and up".
                "people": [{
                    "email": "dmonahan@vanderweil.com",
                    "first_name": "Dennis", "last_name": "Monahan",
                    "title": "", "confidence": 99, "source": "hunter",
                }],
            }, None

        providers.hunter_domain_search = fake_domain_search
        people_mod.providers.hunter_domain_search = fake_domain_search

        cls.company = pipeline.process_company(
            {
                "name": "Vanderweil Engineers",
                "domain": "vanderweil.com",
                "website": "http://127.0.0.1:%d/" % port,
                "source": "google_places",
            },
            pipeline.normalize({"min_seniority": 3, "verify_emails": False,
                                "contacts_per_company": 3}),
        )
        cls.contacts = {c["name"]: c for c in cls.company["contacts"]}

        providers.hunter_domain_search = cls.original_search
        people_mod.providers.hunter_domain_search = cls.original_search
        config.HUNTER_API_KEY = cls.original_key

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def test_adopts_the_pattern_hunter_reported(self):
        self.assertEqual(self.company["email_pattern"], "{f}{last}")

    def test_builds_addresses_in_that_format_not_the_default(self):
        self.assertEqual(self.contacts["Alex Vanderweil"]["email"],
                         "avanderweil@vanderweil.com")
        self.assertEqual(self.contacts["Eli Sherman"]["email"],
                         "esherman@vanderweil.com")

    def test_pattern_backed_guesses_beat_blind_ones(self):
        self.assertGreaterEqual(
            self.contacts["Alex Vanderweil"]["email_confidence"], 0.5
        )

    def test_keeps_a_confirmed_address_with_a_weak_title(self):
        self.assertIn("Dennis Monahan", self.contacts)
        self.assertEqual(self.contacts["Dennis Monahan"]["email"],
                         "dmonahan@vanderweil.com")

    def test_credentials_are_stripped_from_the_crawled_titles(self):
        self.assertEqual(self.contacts["Alex Vanderweil"]["title"], "President")


if __name__ == "__main__":
    unittest.main(verbosity=2)
