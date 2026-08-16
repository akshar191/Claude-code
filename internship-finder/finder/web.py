"""Polite HTTP: one shared session, robots.txt gating, per-host rate limiting.

Every request to a company website goes through get(). Provider APIs go through
api() instead, which skips the robots check since we are a client there.
"""

import threading
import time
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import requests

from . import config

_session = requests.Session()
_session.headers.update(
    {
        "User-Agent": config.USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
)

_lock = threading.Lock()
_next_allowed = {}
_robots_cache = {}


def host_of(url):
    return urlparse(url).netloc.lower()


def _throttle(host):
    """Block until this host is allowed another request."""
    with _lock:
        now = time.time()
        earliest = max(now, _next_allowed.get(host, 0.0))
        _next_allowed[host] = earliest + config.CRAWL_DELAY
        wait = earliest - now
    if wait > 0:
        time.sleep(wait)


def _robots_for(url):
    parts = urlparse(url)
    host = parts.netloc.lower()
    with _lock:
        cached = _robots_cache.get(host)
    if cached is not None:
        return cached

    parser = RobotFileParser()
    _throttle(host)
    try:
        resp = _session.get(
            "%s://%s/robots.txt" % (parts.scheme or "https", host),
            timeout=config.REQUEST_TIMEOUT,
        )
        # A missing or server-errored robots.txt means "no rules", not "deny".
        parser.parse(resp.text.splitlines() if resp.status_code < 400 else [])
    except requests.RequestException:
        parser.parse([])

    with _lock:
        _robots_cache[host] = parser
    return parser


def robots_allows(url):
    if not config.RESPECT_ROBOTS:
        return True
    try:
        return _robots_for(url).can_fetch(config.USER_AGENT, url)
    except Exception:
        return True


def get(url, html_only=True):
    """Fetch a page, or None if it is disallowed, missing, or not HTML."""
    if not url or not url.startswith(("http://", "https://")):
        return None
    if not robots_allows(url):
        return None

    _throttle(host_of(url))
    try:
        resp = _session.get(url, timeout=config.REQUEST_TIMEOUT, allow_redirects=True)
    except requests.RequestException:
        return None

    if resp.status_code != 200:
        return None
    if html_only and "html" not in resp.headers.get("Content-Type", "").lower():
        return None
    return resp


def api(method, url, **kwargs):
    """Call a provider API. Returns (payload, error_string)."""
    kwargs.setdefault("timeout", config.REQUEST_TIMEOUT)
    try:
        resp = _session.request(method, url, **kwargs)
    except requests.RequestException as exc:
        return None, str(exc)

    if resp.status_code in (401, 403):
        return None, "auth rejected (check the API key)"
    if resp.status_code == 429:
        return None, "rate limited / quota exhausted"
    if resp.status_code >= 400:
        return None, "HTTP %s: %s" % (resp.status_code, resp.text[:160])

    try:
        return resp.json(), None
    except ValueError:
        return None, "response was not JSON"
