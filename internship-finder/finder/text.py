"""Small shared helpers for cleaning names, domains and company titles."""

import re
import unicodedata
from urllib.parse import urlparse

# Hosts that are never a company's own domain, so never a place to guess email at.
GENERIC_HOSTS = {
    "facebook.com", "www.facebook.com", "linkedin.com", "www.linkedin.com",
    "twitter.com", "x.com", "instagram.com", "youtube.com", "wixsite.com",
    "sites.google.com", "wordpress.com", "squarespace.com", "godaddysites.com",
    "business.site", "yelp.com", "indeed.com", "glassdoor.com", "crunchbase.com",
    "github.com", "medium.com", "notion.site", "weebly.com", "blogspot.com",
}

# Free mailbox providers: an address here is personal, not "someone @ the company".
FREE_MAIL_DOMAINS = {
    "gmail.com", "googlemail.com", "yahoo.com", "hotmail.com", "outlook.com",
    "live.com", "aol.com", "icloud.com", "me.com", "msn.com", "protonmail.com",
    "proton.me", "gmx.com", "mail.com", "yandex.com", "zoho.com",
}

_SUFFIX_RE = re.compile(
    r"[\s,]+(inc|llc|l\.l\.c|ltd|limited|corp|corporation|co|company|gmbh|plc|"
    r"lp|llp|pllc|p\.c|pc|group|holdings|s\.a|b\.v|ab|oy|as)\.?$",
    re.IGNORECASE,
)

_NAME_PREFIXES = {"mr", "mrs", "ms", "miss", "dr", "prof", "professor", "sir", "rev"}
_NAME_SUFFIXES = {
    "jr", "sr", "ii", "iii", "iv", "v", "phd", "ph.d", "md", "mba", "pe", "p.e",
    "eit", "esq", "cpa", "rn", "dds", "cfa", "pmp",
}


def strip_accents(value):
    decomposed = unicodedata.normalize("NFKD", value or "")
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def slug(value):
    """Lowercase ASCII letters/digits only -- what an email local part is built from."""
    return re.sub(r"[^a-z0-9]", "", strip_accents(value or "").lower())


def normalize_domain(value):
    """Pull a bare registrable-ish domain out of a URL or domain string."""
    if not value:
        return None
    value = value.strip()
    if "://" not in value:
        value = "https://" + value

    host = (urlparse(value).netloc or "").lower().strip()
    host = host.split("@")[-1].split(":")[0]
    if host.startswith("www."):
        host = host[4:]

    if not host or "." not in host or host in GENERIC_HOSTS:
        return None
    if host.replace(".", "").isdigit():
        return None
    return host


def is_company_domain(domain):
    """False for free mail providers and social/hosting hosts."""
    if not domain:
        return False
    return domain not in FREE_MAIL_DOMAINS and domain not in GENERIC_HOSTS


def clean_company_name(name):
    name = re.sub(r"\s+", " ", (name or "")).strip(" -|,")
    previous = None
    while previous != name:
        previous = name
        name = _SUFFIX_RE.sub("", name).strip(" ,.")
    return name or (previous or "")


def company_key(name, domain):
    """Dedupe key: prefer the domain, fall back to a normalized name."""
    if domain:
        return "d:" + domain
    return "n:" + slug(clean_company_name(name))


def split_name(full_name):
    """'Dr. Jane A. Doe Jr.' -> ('Jane', 'Doe'). Returns (None, None) if unusable."""
    if not full_name:
        return None, None

    tokens = [t for t in re.split(r"\s+", strip_accents(full_name).strip()) if t]
    tokens = [t.strip(",") for t in tokens]
    while tokens and tokens[0].lower().strip(".") in _NAME_PREFIXES:
        tokens.pop(0)
    while tokens and tokens[-1].lower().strip(".") in _NAME_SUFFIXES:
        tokens.pop()

    # Drop middle initials like "A." but keep real middle names out of the pair.
    tokens = [t for t in tokens if not re.fullmatch(r"[A-Za-z]\.?", t)] or tokens
    if len(tokens) < 2:
        return None, None

    first = re.sub(r"[^A-Za-z'-]", "", tokens[0])
    last = re.sub(r"[^A-Za-z'-]", "", tokens[-1])
    if len(first) < 2 or len(last) < 2:
        return None, None
    return first, last


def titlecase_name(full_name):
    """Tidy casing without flattening names that are already mixed case."""
    fixed = []
    for part in re.split(r"\s+", (full_name or "").strip()):
        if not part:
            continue
        if part.islower() or (part.isupper() and len(part) > 3):
            fixed.append(part.capitalize())
        else:
            fixed.append(part)  # O'Brien, McDonald, JD -- leave alone
    return " ".join(fixed)
