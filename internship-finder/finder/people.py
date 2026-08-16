"""Find the senior people at a company.

Small companies almost always list their leadership on their own website, which
is why this works without paying anyone: crawl the team/about/leadership pages
and pull out name + title pairs. Apollo and Hunter fill in the rest when keys
are configured.

We only ever read pages the site allows (robots.txt is honoured in web.get) and
we never touch LinkedIn -- scraping it breaks their terms and gets accounts
banned. Use the RocketReach passthrough for LinkedIn URLs instead.
"""

import re
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from . import config, emails, industries, providers, text, web

# Path/anchor fragments that suggest a page lists people, and how much we
# want it. Higher wins when we can only afford a handful of fetches.
PAGE_HINTS = [
    (5, ("leadership", "our-team", "ourteam", "meet-the-team", "our-people", "founders")),
    (4, ("team", "people", "staff", "management", "executives", "who-we-are")),
    (3, ("about-us", "aboutus", "about", "company", "our-story")),
    (2, ("contact", "contact-us", "careers", "jobs")),
]

_SEGMENT_RE = re.compile(r"\s+[|•·—–]\s+|\s+-\s+|\s*[,;]\s*|\n+")

# One name token: starts capitalised, and keeps the capital after an apostrophe
# or hyphen so O'Brien and Smith-Jones survive intact.
_NAME_TOKEN = r"(?=[A-Za-z'’\-]{2,25}\b)[A-Z][a-z]*(?:['’\-][A-Z]?[a-z]+)*"
_NAME_RE = re.compile(r"^(%s)(?:\s+[A-Z][a-z]?\.?)?\s+(%s)$" % (_NAME_TOKEN, _NAME_TOKEN))
_NAME_IN_TEXT = re.compile(r"\b(%s)(?:\s+[A-Z]\.?)?\s+(%s)\b" % (_NAME_TOKEN, _NAME_TOKEN))
_LINKEDIN_RE = re.compile(r"linkedin\.com/in/([A-Za-z0-9\-_%]+)", re.IGNORECASE)

# Professional credentials that sit next to a name on a team page. Splitting
# "Bill Townsend, PhD, CEO" on the comma leaves "PhD" glued to the front of the
# title, so strip them off both ends.
_CREDENTIALS = (
    r"ph\.?d|m\.?d|m\.?b\.?a|p\.?e|otr/?l|r\.?n|d\.?d\.?s|d\.?v\.?m|c\.?f\.?a|pmp|"
    r"m\.?sc?|b\.?sc?|b\.?a|m\.?a|eit|leed(?:\s+ap)?|aia|ncarb|cpa|esq|j\.?d|"
    r"m\.?p\.?h|d\.?p\.?t|p\.?g|l\.?e\.?e\.?d|se|cissp|cfp"
)
_CREDENTIAL_HEAD_RE = re.compile(r"^(?:(?:%s)\b[\s,./|-]*)+" % _CREDENTIALS, re.IGNORECASE)
_CREDENTIAL_TAIL_RE = re.compile(r"(?:[\s,./|-]*\b(?:%s))+$" % _CREDENTIALS, re.IGNORECASE)


def strip_credentials(title):
    """'PhD CEO & Chairman' -> 'CEO & Chairman'."""
    cleaned = _CREDENTIAL_HEAD_RE.sub("", " ".join((title or "").split()))
    cleaned = _CREDENTIAL_TAIL_RE.sub("", cleaned).strip(" ,.|-")
    # If the title was nothing but credentials, keep the original rather than
    # returning an empty string.
    return cleaned or " ".join((title or "").split())

# Words that show up in title case on websites but are never part of a person's
# name. Any candidate containing one of these is rejected.
_NOT_NAME_WORDS = {
    "the", "our", "your", "team", "about", "contact", "us", "we", "read", "learn",
    "more", "view", "get", "join", "why", "how", "what", "who", "all", "home",
    "services", "products", "solutions", "careers", "blog", "news", "privacy",
    "terms", "cookie", "policy", "copyright", "rights", "reserved", "street",
    "avenue", "road", "suite", "floor", "united", "states", "america", "new",
    "york", "san", "los", "inc", "llc", "ltd", "corp", "company", "group",
    "partners", "associates", "consulting", "engineering", "technologies",
    "systems", "industries", "labs", "studio", "design", "capital", "ventures",
    "email", "phone", "call", "click", "here", "sign", "log", "search", "menu",
    "skip", "content", "next", "previous", "page", "case", "study", "studies",
    "project", "projects", "client", "clients", "work", "portfolio", "let",
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
    "january", "february", "march", "april", "may", "june", "july", "august",
    "september", "october", "november", "december",
}


def _looks_like_name(value):
    match = _NAME_RE.fullmatch((value or "").strip())
    if not match:
        return None
    full = " ".join((value or "").split())
    if len(full) > 40:
        return None
    for token in re.split(r"[\s'’\-]+", full.lower()):
        if token in _NOT_NAME_WORDS:
            return None
    # "Senior Mechanical" parses as a name but is the front half of a job title.
    if industries.rank_title(full)[0] > 0:
        return None
    return full


def _pair_from_block(block):
    """Pull (name, title) out of one short chunk of page text."""
    rank, _phrase = industries.rank_title(block)
    if rank == 0:
        return None

    segments = [s.strip() for s in _SEGMENT_RE.split(block) if s.strip()]
    name = None
    title = None
    for segment in segments:
        candidate = _looks_like_name(segment)
        if candidate and not name:
            name = candidate
            continue
        if industries.rank_title(segment)[0] > 0 and not title:
            title = segment

    # One unsegmented run like "Jane Doe Co-Founder & CTO".
    if not name:
        match = _NAME_IN_TEXT.search(block)
        if match:
            candidate = _looks_like_name(match.group(0))
            if candidate:
                name = candidate
                remainder = block[match.end():].strip(" ,-|·—–•")
                if industries.rank_title(remainder)[0] > 0:
                    title = remainder

    if not name or not title:
        return None
    title = " ".join(title.split())[:120]
    if _looks_like_name(title):  # two names in a row, not a name/title pair
        return None
    return name, title


def _structural_pairs(soup):
    """Team cards: a name in a heading, the title in the next element."""
    pairs = []
    for heading in soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6", "strong", "b"]):
        if heading.find_parent(["nav", "footer"]) is not None:
            continue
        name = _looks_like_name(heading.get_text(" ", strip=True))
        if not name:
            continue
        for sibling in list(heading.next_siblings)[:3]:
            following = getattr(sibling, "get_text", lambda *a, **k: str(sibling))(" ", strip=True)
            following = " ".join((following or "").split())
            if not following or len(following) > 120:
                continue
            if industries.rank_title(following)[0] > 0:
                pairs.append((name, following))
                break
    return pairs


def extract_people(html):
    """All plausible name/title pairs on one page, best title per person."""
    soup = BeautifulSoup(html or "", "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    # Nav and footer are noise for people, but the caller reuses this soup to
    # find team-page links -- which usually live in the nav. So skip those
    # subtrees here rather than removing them.
    def in_chrome(element):
        return element.find_parent(["nav", "footer"]) is not None

    found = {}

    def record(name, title):
        title = strip_credentials(title)
        rank, phrase = industries.rank_title(title)
        if rank == 0:
            return
        key = text.slug(name)
        existing = found.get(key)
        # Prefer the more senior reading, then the tighter title string.
        if existing and (existing["rank"], -len(existing["title"])) >= (rank, -len(title)):
            return
        first, last = text.split_name(name)
        found[key] = {
            "name": text.titlecase_name(name),
            "first_name": first,
            "last_name": last,
            "title": title,
            "rank": rank,
            "seniority": industries.SENIORITY_LABELS[rank],
            "matched_phrase": phrase,
            "source": "website",
        }

    for element in soup.find_all(["div", "li", "article", "section", "td", "p", "figcaption", "span"]):
        block = " ".join(element.get_text(" ", strip=True).split())
        if not (6 <= len(block) <= 200) or in_chrome(element):
            continue
        pair = _pair_from_block(block)
        if pair:
            record(*pair)

    for name, title in _structural_pairs(soup):
        record(name, title)

    # Attach LinkedIn profiles by matching the URL slug against the name.
    profiles = {}
    for match in _LINKEDIN_RE.finditer(html or ""):
        profiles[text.slug(match.group(1))] = "https://www.linkedin.com/in/%s" % match.group(1)
    for key, person in found.items():
        for profile_slug, url in profiles.items():
            if key and (key in profile_slug or profile_slug.startswith(key)):
                person["linkedin_url"] = url
                break

    return list(found.values()), soup


def candidate_pages(base_url, soup, limit):
    """Same-site pages most likely to list people, best first."""
    home_host = urlparse(base_url).netloc.lower().replace("www.", "")
    scored = {}

    for anchor in soup.find_all("a", href=True):
        href = anchor["href"].split("#")[0].strip()
        if not href or href.startswith(("mailto:", "tel:", "javascript:")):
            continue

        url = urljoin(base_url, href)
        parts = urlparse(url)
        if parts.scheme not in ("http", "https"):
            continue
        if parts.netloc.lower().replace("www.", "") != home_host:
            continue
        if re.search(r"\.(pdf|jpg|jpeg|png|gif|svg|zip|mp4|docx?|xlsx?)$", parts.path, re.I):
            continue

        haystack = (parts.path + " " + anchor.get_text(" ", strip=True)).lower()
        best = 0
        for weight, fragments in PAGE_HINTS:
            if any(fragment in haystack for fragment in fragments):
                best = max(best, weight)
        if best:
            url = url.rstrip("/")
            scored[url] = max(scored.get(url, 0), best)

    ordered = sorted(scored.items(), key=lambda item: (-item[1], len(item[0])))
    return [url for url, _ in ordered[:limit]]


def from_website(company, on_progress=None):
    """Crawl a company site for people, published emails and descriptive text."""
    domain = company.get("domain")
    result = {
        "people": [],
        "emails": [],
        "site_text": "",
        "pages_crawled": [],
        "error": None,
    }
    if not domain:
        result["error"] = "no website"
        return result

    home = None
    for url in [company.get("website"), "https://%s" % domain, "http://%s" % domain]:
        if not url:
            continue
        home = web.get(url)
        if home is not None:
            break
    if home is None:
        result["error"] = "site unreachable or disallowed by robots.txt"
        return result

    people_by_key = {}
    text_chunks = []

    def ingest(html, url):
        found, soup = extract_people(html)
        for person in found:
            key = text.slug(person["name"])
            existing = people_by_key.get(key)
            if not existing or person["rank"] > existing["rank"]:
                person["source_url"] = url
                people_by_key[key] = person
        result["emails"].extend(emails.harvest(html, soup))
        text_chunks.append(soup.get_text(" ", strip=True)[:4000])
        result["pages_crawled"].append(url)
        return soup

    home_soup = ingest(home.text, home.url)
    budget = max(1, int(config.MAX_PAGES_PER_SITE)) - 1
    for url in candidate_pages(home.url, home_soup, budget):
        if on_progress:
            on_progress("  reading %s" % url)
        page = web.get(url)
        if page is not None:
            ingest(page.text, page.url)

    result["people"] = sorted(people_by_key.values(), key=lambda p: -p["rank"])
    result["emails"] = sorted(set(result["emails"]))
    result["site_text"] = " ".join(text_chunks)[:20000]
    return result


def from_providers(company, criteria):
    """Ask Hunter and Apollo about this domain. Returns (people, emails, pattern, notes)."""
    domain = company.get("domain")
    people, harvested, notes = [], [], []
    pattern = None

    hunter, error = providers.hunter_domain_search(domain)
    if error and error != "not configured":
        notes.append("Hunter (%s): %s" % (domain, error))
    elif hunter:
        pattern = hunter.get("pattern")
        for person in hunter["people"]:
            if person.get("email"):
                harvested.append(person["email"].lower())
            name = " ".join(
                part for part in [person.get("first_name"), person.get("last_name")] if part
            )
            if not name:
                continue
            rank, _ = industries.rank_title(person.get("title") or "")
            people.append(
                {
                    "name": name,
                    "first_name": person.get("first_name"),
                    "last_name": person.get("last_name"),
                    "title": person.get("title") or "",
                    "rank": rank,
                    "seniority": industries.SENIORITY_LABELS[rank],
                    "email": person.get("email"),
                    "email_confidence": (person.get("confidence") or 0) / 100.0,
                    "email_basis": "published/verified by Hunter",
                    "linkedin_url": person.get("linkedin_url"),
                    "source": "hunter",
                }
            )

    titles = criteria.get("titles") or [
        "founder", "ceo", "president", "owner", "cto", "vice president",
        "director", "head of engineering", "engineering manager", "principal",
    ]
    apollo, error = providers.apollo_search_people(domain, titles)
    if error and error != "not configured":
        notes.append("Apollo (%s): %s" % (domain, error))
    elif apollo:
        for person in apollo:
            rank, _ = industries.rank_title(person.get("title") or "")
            people.append(
                {
                    "name": person.get("name"),
                    "first_name": person.get("first_name"),
                    "last_name": person.get("last_name"),
                    "title": person.get("title") or "",
                    "rank": rank,
                    "seniority": industries.SENIORITY_LABELS[rank],
                    "email": person.get("email"),
                    "email_confidence": 0.9 if person.get("email") else 0.0,
                    "email_basis": "provided by Apollo" if person.get("email") else None,
                    "linkedin_url": person.get("linkedin_url"),
                    "source": "apollo",
                }
            )

    return people, harvested, pattern, notes
