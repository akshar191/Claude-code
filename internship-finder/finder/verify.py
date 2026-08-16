"""Email verification, cheapest checks first.

Levels of certainty, in order:
  1. syntax + MX      -- free, instant, rules out dead domains
  2. Hunter verifier  -- authoritative-ish, costs a credit
  3. SMTP probe       -- off by default (see .env.example for why)

Anything we could not positively confirm comes back as "unknown", never as
"valid". A guessed address that merely has a working MX record is still a guess.
"""

import random
import re
import smtplib
import string
import threading

import dns.exception
import dns.resolver

from . import config, emails, providers, text

SYNTAX_RE = re.compile(r"^[A-Za-z0-9._%+'-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,24}$")

DISPOSABLE_DOMAINS = {
    "mailinator.com", "guerrillamail.com", "10minutemail.com", "tempmail.com",
    "throwawaymail.com", "yopmail.com", "trashmail.com", "sharklasers.com",
    "getnada.com", "temp-mail.org", "dispostable.com", "maildrop.cc",
}

_lock = threading.Lock()
_mx_cache = {}
_catch_all_cache = {}

_resolver = dns.resolver.Resolver()
_resolver.lifetime = 5.0
_resolver.timeout = 5.0


def mx_hosts(domain):
    """Mail exchangers for a domain, best priority first. Empty means no mail."""
    if not domain:
        return []
    with _lock:
        if domain in _mx_cache:
            return _mx_cache[domain]

    hosts = []
    try:
        answers = _resolver.resolve(domain, "MX")
        hosts = [
            str(record.exchange).rstrip(".")
            for record in sorted(answers, key=lambda r: r.preference)
        ]
    except (dns.resolver.NoAnswer, dns.resolver.NXDOMAIN, dns.resolver.NoNameservers,
            dns.exception.Timeout, dns.exception.DNSException):
        hosts = []

    if not hosts:
        # Some small domains take mail on the A record with no MX.
        try:
            _resolver.resolve(domain, "A")
            hosts = [domain]
        except dns.exception.DNSException:
            hosts = []

    with _lock:
        _mx_cache[domain] = hosts
    return hosts


def _smtp_rcpt(host, address):
    """Ask a mail server whether it would accept mail for an address.

    Returns True/False/None (None = server would not tell us).
    """
    try:
        server = smtplib.SMTP(host, 25, timeout=config.SMTP_TIMEOUT)
    except (OSError, smtplib.SMTPException):
        return None

    try:
        server.helo(config.SMTP_FROM.split("@")[-1] or "localhost")
        server.mail(config.SMTP_FROM)
        code, _message = server.rcpt(address)
    except smtplib.SMTPException:
        return None
    finally:
        try:
            server.quit()
        except smtplib.SMTPException:
            pass

    if code in (250, 251):
        return True
    if code in (550, 551, 553): # mailbox unknown / rejected
        return False
    return None


def is_catch_all(domain, host):
    """True if the domain accepts mail for addresses that cannot exist."""
    with _lock:
        if domain in _catch_all_cache:
            return _catch_all_cache[domain]

    nonsense = "".join(random.choices(string.ascii_lowercase, k=18))
    verdict = _smtp_rcpt(host, "%s@%s" % (nonsense, domain))

    with _lock:
        _catch_all_cache[domain] = verdict
    return verdict


def verify(email, use_paid=True):
    """Check one address. Never returns 'valid' without positive evidence."""
    email = (email or "").strip().lower()
    checks = {
        "syntax": False,
        "mx": False,
        "role_account": False,
        "disposable": False,
        "free_provider": False,
        "smtp": None,
        "catch_all": None,
    }
    result = {"email": email, "status": "invalid", "checks": checks, "detail": None}

    if not SYNTAX_RE.match(email):
        result["detail"] = "not a valid address format"
        return result
    checks["syntax"] = True

    domain = email.split("@")[1]
    checks["role_account"] = emails.is_role_account(email)
    checks["disposable"] = domain in DISPOSABLE_DOMAINS
    checks["free_provider"] = domain in text.FREE_MAIL_DOMAINS

    if checks["disposable"]:
        result["status"] = "invalid"
        result["detail"] = "disposable address domain"
        return result

    hosts = mx_hosts(domain)
    checks["mx"] = bool(hosts)
    if not hosts:
        result["detail"] = "domain does not accept mail (no MX record)"
        return result

    if use_paid and config.HUNTER_API_KEY:
        payload, error = providers.hunter_verify(email)
        if payload and payload.get("status"):
            hunter_status = payload["status"]
            mapping = {
                "valid": "valid",
                "accept_all": "risky",
                "webmail": "risky",
                "disposable": "invalid",
                "invalid": "invalid",
                "unknown": "unknown",
            }
            result["status"] = mapping.get(hunter_status, "unknown")
            result["detail"] = "Hunter says: %s (score %s)" % (
                hunter_status, payload.get("score"),
            )
            checks["catch_all"] = payload.get("accept_all")
            return result
        if error and error != "not configured":
            result["detail"] = "Hunter verify failed: %s" % error

    if config.ENABLE_SMTP_PROBE:
        catch_all = is_catch_all(domain, hosts[0])
        checks["catch_all"] = catch_all
        if catch_all:
            result["status"] = "risky"
            result["detail"] = "domain accepts all addresses, so acceptance proves nothing"
            return result

        accepted = _smtp_rcpt(hosts[0], email)
        checks["smtp"] = accepted
        if accepted is True:
            result["status"] = "valid"
            result["detail"] = "mail server accepted this recipient"
            return result
        if accepted is False:
            result["status"] = "invalid"
            result["detail"] = "mail server rejected this recipient"
            return result

    result["status"] = "unknown"
    result["detail"] = result["detail"] or "domain accepts mail; mailbox not confirmed"
    return result


def score(verification, base_confidence):
    """Blend how the address was obtained with how well it verified."""
    status = (verification or {}).get("status")
    multiplier = {"valid": 1.0, "unknown": 0.75, "risky": 0.5, "invalid": 0.0}.get(status, 0.6)
    if (verification or {}).get("checks", {}).get("role_account"):
        multiplier *= 0.85
    return round(min(0.99, (base_confidence or 0.5) * multiplier), 2)
