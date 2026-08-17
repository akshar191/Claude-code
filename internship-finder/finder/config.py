"""Settings, all sourced from the environment. See .env.example."""

import os


def _flag(name, default=False):
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _num(name, default):
    try:
        return type(default)(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


HUNTER_API_KEY = os.environ.get("HUNTER_API_KEY", "").strip()
APOLLO_API_KEY = os.environ.get("APOLLO_API_KEY", "").strip()
ROCKETREACH_API_KEY = os.environ.get("ROCKETREACH_API_KEY", "").strip()
GOOGLE_PLACES_API_KEY = os.environ.get("GOOGLE_PLACES_API_KEY", "").strip()

CONTACT_EMAIL = os.environ.get("CONTACT_EMAIL", "").strip()
USER_AGENT = os.environ.get(
    "USER_AGENT",
    "internship-finder/1.0 (personal job search; contact: %s)"
    % (CONTACT_EMAIL or "unset -- set CONTACT_EMAIL"),
)

REQUEST_TIMEOUT = _num("REQUEST_TIMEOUT", 12.0)
CRAWL_DELAY = _num("CRAWL_DELAY", 1.0)
MAX_PAGES_PER_SITE = _num("MAX_PAGES_PER_SITE", 8)
RESPECT_ROBOTS = _flag("RESPECT_ROBOTS", True)

# OpenStreetMap/Overpass is off by default. Both public mirrors time out
# routinely, and when Google Places is configured OSM adds nothing but 45
# seconds of waiting. Turn it back on if you have no Places key.
ENABLE_OPENSTREETMAP = _flag("ENABLE_OPENSTREETMAP", False)

# Hunter's free tier is small, and every email-verifier call spends from it.
# Cap how many a single search may spend so one run cannot drain the month.
HUNTER_VERIFY_BUDGET = _num("HUNTER_VERIFY_BUDGET", 10)

# Per-person Hunter lookups (email-finder). These are the highest-quality
# addresses available, and each one costs a search credit, so they are budgeted
# separately and spent on the most senior people first.
HUNTER_FINDER_BUDGET = _num("HUNTER_FINDER_BUDGET", 6)

ENABLE_SMTP_PROBE = _flag("ENABLE_SMTP_PROBE", False)
SMTP_FROM = os.environ.get("SMTP_FROM", "verify@example.com").strip()
SMTP_TIMEOUT = _num("SMTP_TIMEOUT", 8.0)

DB_PATH = os.environ.get(
    "DB_PATH", os.path.join(os.path.dirname(os.path.dirname(__file__)), "finder.db")
)

# --- Public deployment ------------------------------------------------------
# Single shared password. There are no accounts; this only exists to keep
# strangers from spending the API budget. Unset means the app refuses to serve.
APP_PASSWORD = os.environ.get("APP_PASSWORD", "").strip()

# Signs the session cookie. A random default means sessions do not survive a
# restart, which is survivable but annoying -- set it in production.
SECRET_KEY = os.environ.get("SECRET_KEY", "").strip() or os.urandom(32).hex()

# Set automatically on Render; forces cookies to HTTPS only.
IS_PRODUCTION = bool(os.environ.get("RENDER") or _flag("IS_PRODUCTION", False))

DAILY_SEARCH_LIMIT = _num("DAILY_SEARCH_LIMIT", 2)
MAX_COMPANIES_PER_SEARCH = _num("MAX_COMPANIES_PER_SEARCH", 5)
# Below this many Hunter searches left, the UI stops offering to run one.
MIN_QUOTA_TO_SEARCH = _num("MIN_QUOTA_TO_SEARCH", 5)

# --- Gmail drafts -----------------------------------------------------------
# OAuth client from the Google Cloud console. Scope is gmail.compose only:
# enough to save a draft, not enough to send one.
GOOGLE_OAUTH_CLIENT_ID = os.environ.get("GOOGLE_OAUTH_CLIENT_ID", "").strip()
GOOGLE_OAUTH_CLIENT_SECRET = os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET", "").strip()
GMAIL_SCOPE = "https://www.googleapis.com/auth/gmail.compose"

# --- Who you are (used in drafts) -------------------------------------------
APPLICANT = {
    "name": os.environ.get("APPLICANT_NAME", "").strip(),
    "year": os.environ.get("APPLICANT_YEAR", "high school junior").strip(),
    "town": os.environ.get("APPLICANT_TOWN", "Ashland, MA").strip(),
    "work": os.environ.get(
        "APPLICANT_WORK",
        "a contractor at Silverside Detectors, assembling lithium-6 neutron detectors",
    ).strip(),
    "venture": os.environ.get(
        "APPLICANT_VENTURE", "founder of a mobile detailing business"
    ).strip(),
    "target": os.environ.get("APPLICANT_TARGET", "summer 2027").strip(),
}


def providers():
    """Which optional data sources are configured right now."""
    return {
        "hunter": bool(HUNTER_API_KEY),
        "apollo": bool(APOLLO_API_KEY),
        "rocketreach": bool(ROCKETREACH_API_KEY),
        "google_places": bool(GOOGLE_PLACES_API_KEY),
        "openstreetmap": True,
        "site_crawl": True,
        "smtp_probe": ENABLE_SMTP_PROBE,
    }
