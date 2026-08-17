"""Email discovery: harvest published addresses, learn a company's address
format from them, then build addresses for the people we could not find directly.

This is the same idea Hunter/Apollo/RocketReach use. The difference is that when
we guess, we say so and attach a confidence, rather than presenting a guess as a
verified address.
"""

import re
from collections import Counter

from . import text

EMAIL_RE = re.compile(
    r"\b([A-Za-z0-9._%+-]+)@([A-Za-z0-9.-]+\.[A-Za-z]{2,24})\b"
)

# Addresses that are almost always assets, tracking pixels or placeholder copy.
_JUNK_LOCAL = re.compile(r"^(example|test|your|name|email|user|username|firstname|"
                         r"lastname|someone|noreply|no-reply|donotreply|mailer-daemon)$",
                         re.IGNORECASE)
_JUNK_DOMAIN = re.compile(r"\.(png|jpg|jpeg|gif|svg|webp|css|js|json|webmanifest)$",
                          re.IGNORECASE)

# Shared inboxes: useful as a fallback, but not a person.
ROLE_LOCALS = {
    "info", "contact", "hello", "hi", "admin", "office", "sales", "support",
    "help", "team", "mail", "enquiries", "inquiries", "general", "careers",
    "jobs", "hr", "recruiting", "press", "media", "marketing", "billing",
    "accounts", "accounting", "webmaster", "postmaster", "abuse", "legal",
    "privacy", "service", "customerservice", "reception", "welcome",
}

# Rough real-world frequency of each corporate address format. Used to rank
# guesses when we have no known address at the domain to learn from.
PATTERN_PRIORS = [
    ("{first}.{last}", 0.34),
    ("{first}", 0.18),
    ("{f}{last}", 0.13),
    ("{first}{last}", 0.09),
    ("{first}_{last}", 0.05),
    ("{f}.{last}", 0.05),
    ("{first}{l}", 0.03),
    ("{last}", 0.03),
    ("{last}.{first}", 0.02),
    ("{last}{f}", 0.02),
    ("{first}-{last}", 0.02),
    ("{f}{l}", 0.02),
    ("{first}.{l}", 0.02),
]

PATTERNS = [pattern for pattern, _ in PATTERN_PRIORS]


def decode_cfemail(token):
    """Undo Cloudflare's email obfuscation (data-cfemail attributes)."""
    try:
        raw = bytes.fromhex(token)
    except ValueError:
        return None
    key = raw[0]
    decoded = "".join(chr(byte ^ key) for byte in raw[1:])
    return decoded if "@" in decoded else None


def _is_plausible(local, domain):
    if _JUNK_LOCAL.match(local) or _JUNK_DOMAIN.search(domain):
        return False
    if len(local) > 64 or len(domain) > 255:
        return False
    # Long hex blobs are usually asset hashes that happen to contain an @.
    if re.fullmatch(r"[0-9a-f]{16,}", local, re.IGNORECASE):
        return False
    return True


def harvest(html, soup=None):
    """Every email address published on a page, deduped and lowercased."""
    found = set()

    for match in EMAIL_RE.finditer(html or ""):
        local, domain = match.group(1), match.group(2).rstrip(".")
        if _is_plausible(local, domain):
            found.add("%s@%s" % (local.lower(), domain.lower()))

    # Cloudflare-obfuscated addresses, common on small-business sites.
    for token in re.findall(r'data-cfemail="([0-9a-fA-F]+)"', html or ""):
        decoded = decode_cfemail(token)
        if decoded:
            local, _, domain = decoded.partition("@")
            if _is_plausible(local, domain):
                found.add(decoded.lower())

    if soup is not None:
        for anchor in soup.select('a[href^="mailto:"]'):
            address = (anchor.get("href") or "")[7:].split("?")[0].strip().lower()
            match = EMAIL_RE.fullmatch(address)
            if match and _is_plausible(match.group(1), match.group(2)):
                found.add(address)

    return sorted(found)


def is_role_account(email):
    local = (email or "").split("@")[0].lower()
    return local in ROLE_LOCALS or local.replace(".", "") in ROLE_LOCALS


def render(pattern, first, last, middle=None):
    """Build a local part from a pattern.

    Hunter returns its own patterns here, including ones with a middle initial
    ({f}{m}{last}). We rarely know the middle name, so {m} collapses to nothing
    and any separator it leaves behind is cleaned up. An unrecognised
    placeholder returns None rather than raising -- a bad pattern from a
    provider must not take the whole company's contacts down with it.
    """
    first_slug, last_slug = text.slug(first), text.slug(last)
    if not first_slug or not last_slug:
        return None

    middle_slug = text.slug(middle or "")
    fields = {
        "first": first_slug,
        "last": last_slug,
        "f": first_slug[0],
        "l": last_slug[0],
        "m": middle_slug[:1],
        "mi": middle_slug[:1],
        "middle": middle_slug,
    }
    try:
        local = pattern.format(**fields)
    except (KeyError, IndexError, ValueError):
        return None

    # An empty {m} can leave "alex..vanderweil" or a trailing dot behind.
    local = re.sub(r"([._-])\1+", r"\1", local).strip("._-")
    return local if 1 <= len(local) <= 64 else None


def detect_pattern(email, first, last):
    """Which pattern produces this address for this person? None if no match."""
    local = (email or "").split("@")[0].lower()
    if not local:
        return None
    for pattern in PATTERNS:
        if render(pattern, first, last) == local:
            return pattern
    return None


def infer_pattern(known_people, domain):
    """Learn the house format from addresses we already found at this domain.

    known_people: iterable of dicts with 'email' plus 'first_name'/'last_name'.
    Returns (pattern, confidence, sample_count).
    """
    votes = Counter()
    for person in known_people or []:
        email = (person.get("email") or "").lower()
        if not email.endswith("@" + (domain or "")):
            continue
        if is_role_account(email):
            continue

        first = person.get("first_name")
        last = person.get("last_name")
        if not first or not last:
            first, last = text.split_name(person.get("name") or "")
        if not first or not last:
            continue

        pattern = detect_pattern(email, first, last)
        if pattern:
            votes[pattern] += 1

    if not votes:
        return None, 0.0, 0

    pattern, count = votes.most_common(1)[0]
    total = sum(votes.values())
    agreement = count / total
    # Two agreeing samples is already strong evidence; one is suggestive.
    confidence = min(0.95, 0.55 + 0.2 * count) * agreement
    return pattern, round(confidence, 2), total


def candidates(first, last, domain, pattern=None, pattern_confidence=0.0, limit=4,
               allow_priors=True):
    """Ranked guesses for one person's address at one domain.

    With allow_priors=False the only address produced is one built from a
    pattern we have actual evidence for. Base-rate guessing ("most companies
    use first.last") is skipped entirely -- those are the addresses that bounce.
    """
    if not domain or not text.is_company_domain(domain):
        return []
    if not pattern and not allow_priors:
        return []

    ranked = []
    seen = set()

    if pattern:
        local = render(pattern, first, last)
        if local:
            ranked.append(
                {
                    "email": "%s@%s" % (local, domain),
                    "pattern": pattern,
                    "confidence": round(max(0.5, min(0.92, pattern_confidence or 0.7)), 2),
                    "basis": "matches this company's known address format",
                }
            )
            seen.add(local)

    if not allow_priors:
        return ranked[:limit]

    for candidate_pattern, prior in PATTERN_PRIORS:
        if len(ranked) >= limit:
            break
        local = render(candidate_pattern, first, last)
        if not local or local in seen:
            continue
        seen.add(local)
        ranked.append(
            {
                "email": "%s@%s" % (local, domain),
                "pattern": candidate_pattern,
                # A guess from priors alone is capped low on purpose.
                "confidence": round(prior * (0.45 if pattern else 0.8), 2),
                "basis": "common format (%s of company domains)" % ("%.0f%%" % (prior * 100)),
            }
        )

    return ranked[:limit]
