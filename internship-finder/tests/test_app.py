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


class CompanyCap(unittest.TestCase):
    def setUp(self):
        web_app.app.config["TESTING"] = True
        store.init_db()
        config.APP_PASSWORD = "test-password"
        config.HUNTER_API_KEY = ""
        web_app._quota_cache.update(at=0, value=None)
        self.client = web_app.app.test_client()
        self.client.post("/login", data={"password": "test-password"})

    def test_companies_are_capped_server_side(self):
        captured = {}
        original_start = web_app.pipeline.start
        web_app.pipeline.start = lambda criteria, label=None: captured.update(criteria) or "job"
        try:
            self.client.post("/api/search",
                             json={"location": "Boston", "max_companies": 50})
        finally:
            web_app.pipeline.start = original_start
        self.assertEqual(captured["max_companies"], config.MAX_COMPANIES_PER_SEARCH)

    def test_a_search_needs_a_location_or_keyword(self):
        self.assertEqual(self.client.post("/api/search", json={}).status_code, 400)

    def test_no_daily_limit_remains(self):
        """The daily cap was removed; repeated searches must not be blocked."""
        original_start = web_app.pipeline.start
        web_app.pipeline.start = lambda criteria, label=None: "job"
        try:
            for _ in range(8):
                response = self.client.post("/api/search", json={"location": "Boston"})
                self.assertEqual(response.status_code, 202)
        finally:
            web_app.pipeline.start = original_start


class QuotaGate(unittest.TestCase):
    """Two tiers: a hard floor, and a confirmation that is never suppressed."""

    def setUp(self):
        web_app.app.config["TESTING"] = True
        store.init_db()
        config.APP_PASSWORD = "test-password"
        config.HUNTER_API_KEY = "test-key"
        self.client = web_app.app.test_client()
        self.client.post("/login", data={"password": "test-password"})
        self.original_account = web_app.providers.hunter_account
        self.original_start = web_app.pipeline.start
        web_app.pipeline.start = lambda criteria, label=None: "job-test"

    def tearDown(self):
        web_app.providers.hunter_account = self.original_account
        web_app.pipeline.start = self.original_start
        web_app._quota_cache.update(at=0, value=None)
        config.HUNTER_API_KEY = ""

    def with_credits(self, remaining):
        web_app._quota_cache.update(at=0, value=None)
        web_app.providers.hunter_account = lambda: (
            {"searches_used": 50 - remaining, "searches_available": 50,
             "reset_date": "2026-09-01"}, None)

    # --- the hard floor --------------------------------------------------

    def test_blocked_only_when_below_the_minimum(self):
        self.with_credits(0)
        response = self.client.post("/api/search", json={"location": "Boston"})
        self.assertEqual(response.status_code, 429)
        self.assertIn("No Hunter credits left", response.get_json()["error"])

    def test_one_credit_is_enough_to_run(self):
        # Previously blocked at anything under 5; the last credits are spendable.
        self.with_credits(1)
        response = self.client.post(
            "/api/search", json={"location": "Boston", "confirm_credits": True})
        self.assertEqual(response.status_code, 202)

    def test_the_floor_is_configurable(self):
        original = config.MIN_HUNTER_CREDITS
        config.MIN_HUNTER_CREDITS = 10
        try:
            self.with_credits(4)
            response = self.client.post("/api/search", json={"location": "Boston"})
            self.assertEqual(response.status_code, 429)
        finally:
            config.MIN_HUNTER_CREDITS = original

    # --- the confirmation ------------------------------------------------

    def test_low_credits_require_confirmation_first(self):
        self.with_credits(3)
        response = self.client.post("/api/search", json={"location": "Boston"})
        self.assertEqual(response.status_code, 409)
        payload = response.get_json()
        self.assertTrue(payload["needs_confirmation"])
        self.assertIn("remaining Hunter credits", payload["error"])
        self.assertEqual(payload["remaining_credits"], 3)
        self.assertGreater(payload["estimated_credits"], 0)

    def test_confirming_lets_the_search_through(self):
        self.with_credits(3)
        response = self.client.post(
            "/api/search", json={"location": "Boston", "confirm_credits": True})
        self.assertEqual(response.status_code, 202)

    def test_the_warning_is_never_suppressed(self):
        """Confirming one search must not stop the next one asking."""
        self.with_credits(3)
        self.client.post("/api/search",
                         json={"location": "Boston", "confirm_credits": True})
        self.with_credits(3)
        again = self.client.post("/api/search", json={"location": "Boston"})
        self.assertEqual(again.status_code, 409)
        self.assertTrue(again.get_json()["needs_confirmation"])

    def test_plenty_of_credits_needs_no_confirmation(self):
        self.with_credits(40)
        response = self.client.post("/api/search", json={"location": "Boston"})
        self.assertEqual(response.status_code, 202)

    # --- what the page is told -------------------------------------------

    def test_meta_reports_the_warning_without_disabling_search(self):
        self.with_credits(2)
        meta = self.client.get("/api/meta").get_json()
        self.assertTrue(meta["can_search"])
        self.assertTrue(meta["needs_confirmation"])
        self.assertIn("remaining Hunter credits", meta["credit_warning"])
        self.assertIsNone(meta["blocked_reason"])

    def test_meta_reports_a_block_when_empty(self):
        self.with_credits(0)
        meta = self.client.get("/api/meta").get_json()
        self.assertFalse(meta["can_search"])
        self.assertIn("No Hunter credits left", meta["blocked_reason"])

    def test_the_estimate_names_a_real_cost(self):
        self.with_credits(40)
        meta = self.client.get("/api/meta").get_json()
        self.assertEqual(meta["estimated_credits"],
                         web_app.estimate_credits({"max_companies": 5}))
        self.assertEqual(meta["thresholds"]["min_credits"], config.MIN_HUNTER_CREDITS)


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


class PublicDemo(unittest.TestCase):
    """The demo link is the one that gets shared, so it must work signed out."""

    def setUp(self):
        web_app.app.config["TESTING"] = True
        store.init_db()
        config.APP_PASSWORD = "test-password"
        self.client = web_app.app.test_client()  # deliberately not signed in

    def test_demo_is_public(self):
        response = self.client.get("/demo")
        self.assertEqual(response.status_code, 200)

    def test_demo_says_it_is_sample_data(self):
        body = self.client.get("/demo").get_data(as_text=True)
        self.assertIn("Sample data", body)
        self.assertIn("invented", body)

    def test_demo_shows_the_confidence_range(self):
        body = self.client.get("/demo").get_data(as_text=True)
        # At least one verified and one unconfirmed, so the model is visible.
        self.assertIn("0.95", body)
        self.assertIn("0.34", body)

    def test_demo_uses_no_real_addresses(self):
        from finder import sample

        for company in sample.COMPANIES:
            self.assertTrue(company["domain"].endswith(".example"),
                            "demo data must not carry a real domain")
            for contact in company["contacts"]:
                self.assertTrue(contact["email"].endswith(".example"))

    def test_demo_does_not_expose_the_real_app(self):
        # Everything else still needs the password.
        self.assertEqual(self.client.get("/").status_code, 302)
        self.assertEqual(self.client.get("/api/meta").status_code, 401)

    def test_demo_needs_no_api_keys(self):
        original = config.HUNTER_API_KEY
        config.HUNTER_API_KEY = ""
        try:
            self.assertEqual(self.client.get("/demo").status_code, 200)
        finally:
            config.HUNTER_API_KEY = original
