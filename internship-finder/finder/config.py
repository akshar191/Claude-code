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

ENABLE_SMTP_PROBE = _flag("ENABLE_SMTP_PROBE", False)
SMTP_FROM = os.environ.get("SMTP_FROM", "verify@example.com").strip()
SMTP_TIMEOUT = _num("SMTP_TIMEOUT", 8.0)

DB_PATH = os.environ.get(
    "DB_PATH", os.path.join(os.path.dirname(os.path.dirname(__file__)), "finder.db")
)


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
