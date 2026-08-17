"""Tests for the deployed web app: the password gate, the rate limits, and
the CSV export. No network involved.

    python -m unittest tests.test_app -v
"""

import datetime
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from finder import config  # noqa: E402

config.DB_PATH = os.path.join(tempfile.mkdtemp(), "app.db")
config.APP_PASSWORD = "test-password"

import app as web_app  # noqa: E402
from finder import store  # noqa: E402


class PasswordGate(unittest.TestCase):
    def setUp(self):
        web_app.app.config["TESTING"] = True
        store.init_db()
        config.APP_PASSWORD = "test-password"
        self.client = web_app.app.test_client()

    def test_pages_redirect_to_login_when_signed_out(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.headers["Location"])

    def test_api_returns_401_when_signed_out(self):
        self.assertEqual(self.client.get("/api/meta").status_code, 401)
        self.assertEqual(self.client.post("/api/search", json={"location": "Boston"})
                         .status_code, 401)
        self.assertEqual(self.client.get("/api/export.csv").status_code, 401)

    def test_health_check_stays_open(self):
        # The platform has to reach this without credentials.
        self.assertEqual(self.client.get("/healthz").status_code, 200)

    def test_wrong_password_does_not_sign_you_in(self):
        response = self.client.post("/login", data={"password": "not-it"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.get("/").status_code, 302)

    def test_right_password_signs_you_in(self):
        response = self.client.post("/login", data={"password": "test-password"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.get("/").status_code, 200)

    def test_logout_ends_the_session(self):
        self.client.post("/login", data={"password": "test-password"})
        self.client.get("/logout")
        self.assertEqual(self.client.get("/").status_code, 302)

    def test_serves_nothing_when_no_password_is_configured(self):
        config.APP_PASSWORD = ""
        try:
            fresh = web_app.app.test_client()
            self.assertEqual(fresh.get("/login").status_code, 503)
            self.assertEqual(fresh.get("/api/meta").status_code, 503)
        finally:
            config.APP_PASSWORD = "test-password"


class RateLimits(unittest.TestCase):
    def setUp(self):
        web_app.app.config["TESTING"] = True
        store.init_db()
        config.APP_PASSWORD = "test-password"
        config.HUNTER_API_KEY = ""  # skip the quota gate for these
        web_app._quota_cache.update(at=0, value=None)
        self.client = web_app.app.test_client()
        self.client.post("/login", data={"password": "test-password"})

    def _clear_counters(self):
        store.init_db()
        with store.connect() as conn:
            conn.execute("DELETE FROM rate_limit")

    def test_companies_are_capped_server_side(self):
        self._clear_counters()
        with web_app.app.test_request_context():
            pass
        # Ask for 50; the server must clamp to the configured maximum.
        captured = {}
        original_start = web_app.pipeline.start

        def fake_start(criteria, label=None):
            captured.update(criteria)
            return "job-test"

        web_app.pipeline.start = fake_start
        try:
            self.client.post("/api/search", json={"location": "Boston", "max_companies": 50})
        finally:
            web_app.pipeline.start = original_start

        self.assertEqual(captured["max_companies"], config.MAX_COMPANIES_PER_SEARCH)

    def test_daily_limit_blocks_further_searches(self):
        self._clear_counters()
        original_start = web_app.pipeline.start
        web_app.pipeline.start = lambda criteria, label=None: "job-test"
        try:
            for _ in range(config.DAILY_SEARCH_LIMIT):
                response = self.client.post("/api/search", json={"location": "Boston"})
                self.assertEqual(response.status_code, 202)

            blocked = self.client.post("/api/search", json={"location": "Boston"})
            self.assertEqual(blocked.status_code, 429)
            self.assertIn("Daily limit", blocked.get_json()["error"])
        finally:
            web_app.pipeline.start = original_start

    def test_a_search_needs_a_location_or_keyword(self):
        self._clear_counters()
        response = self.client.post("/api/search", json={})
        self.assertEqual(response.status_code, 400)

    def test_ip_counter_survives_a_cleared_cookie(self):
        self._clear_counters()
        original_start = web_app.pipeline.start
        web_app.pipeline.start = lambda criteria, label=None: "job-test"
        try:
            for _ in range(config.DAILY_SEARCH_LIMIT):
                self.client.post("/api/search", json={"location": "Boston"})

            # A brand-new session from the same address is still counted.
            fresh = web_app.app.test_client()
            fresh.post("/login", data={"password": "test-password"})
            blocked = fresh.post("/api/search", json={"location": "Boston"})
            self.assertEqual(blocked.status_code, 429)
        finally:
            web_app.pipeline.start = original_start


class QuotaGate(unittest.TestCase):
    def setUp(self):
        web_app.app.config["TESTING"] = True
        store.init_db()
        config.APP_PASSWORD = "test-password"
        self.client = web_app.app.test_client()
        self.client.post("/login", data={"password": "test-password"})
        self.original_account = web_app.providers.hunter_account

    def tearDown(self):
        web_app.providers.hunter_account = self.original_account
        web_app._quota_cache.update(at=0, value=None)
        config.HUNTER_API_KEY = ""

    def test_search_is_refused_when_quota_is_nearly_gone(self):
        config.HUNTER_API_KEY = "test-key"
        web_app._quota_cache.update(at=0, value=None)
        web_app.providers.hunter_account = lambda: (
            {"searches_used": 48, "searches_available": 50, "reset_date": "2026-09-01"},
            None,
        )
        response = self.client.post("/api/search", json={"location": "Boston"})
        self.assertEqual(response.status_code, 429)
        self.assertIn("Hunter lookups left", response.get_json()["error"])

    def test_meta_reports_the_block_so_the_button_can_be_disabled(self):
        config.HUNTER_API_KEY = "test-key"
        web_app._quota_cache.update(at=0, value=None)
        web_app.providers.hunter_account = lambda: (
            {"searches_used": 49, "searches_available": 50, "reset_date": "2026-09-01"},
            None,
        )
        meta = self.client.get("/api/meta").get_json()
        self.assertFalse(meta["can_search"])
        self.assertIn("Hunter lookups left", meta["blocked_reason"])
        self.assertEqual(meta["quota"]["remaining"], 1)


class CsvExport(unittest.TestCase):
    def setUp(self):
        web_app.app.config["TESTING"] = True
        store.init_db()
        config.APP_PASSWORD = "test-password"
        self.client = web_app.app.test_client()
        self.client.post("/login", data={"password": "test-password"})

    def test_exports_the_requested_columns(self):
        job = {
            "companies": [{
                "name": "Acme Robotics", "domain": "acme.test",
                "website": "https://acme.test", "address": "Boston, MA",
                "contacts": [{
                    "name": "Priya Raghavan", "title": "CEO", "seniority": "Founder / CEO",
                    "email": "priya@acme.test", "email_confidence": 0.95,
                    "email_status": "valid", "email_basis": "published",
                    "source": "website",
                }],
            }],
        }
        rows = web_app._csv_rows_from_job(job)
        self.assertEqual(len(rows), 1)
        for column in ("name", "title", "company", "email", "confidence",
                       "source", "company_url"):
            self.assertIn(column, rows[0])
        self.assertEqual(rows[0]["company"], "Acme Robotics")
        self.assertEqual(rows[0]["company_url"], "https://acme.test")
        self.assertEqual(rows[0]["confidence"], 0.95)

    def test_leaves_outreach_columns_blank_to_fill_in(self):
        rows = web_app._csv_rows_from_job({"companies": [{
            "name": "Acme", "contacts": [{"name": "Priya Raghavan"}]}]})
        for column in ("contacted_on", "replied", "notes"):
            self.assertEqual(rows[0][column], "")

    def test_unknown_job_is_a_404(self):
        self.assertEqual(
            self.client.get("/api/export.csv?job_id=nope").status_code, 404
        )


class SessionCookieHardening(unittest.TestCase):
    def test_cookie_flags(self):
        self.assertTrue(web_app.app.config["SESSION_COOKIE_HTTPONLY"])
        self.assertEqual(web_app.app.config["SESSION_COOKIE_SAMESITE"], "Lax")
        self.assertIsInstance(
            web_app.app.config["PERMANENT_SESSION_LIFETIME"], datetime.timedelta
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
