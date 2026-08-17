"""Read a company's own website for concrete details worth naming in an email.

The whole value of a cold email to a 20-person company is showing you looked at
what they actually build. Generic praise ("I admire your innovative work") reads
as a mail merge; "the lithium-6 detectors you build for port screening" does not.

So: fetch the pages that describe the product, pull out sentences that make a
specific claim, and hand those to the drafter. Every detail keeps the URL it
came from so the claim can be checked before sending.
"""

import re

from bs4 import BeautifulSoup

from . import web

# Pages that describe what a company makes, as opposed to who works there.
PRODUCT_HINTS = [
    (5, ("products", "product", "technology", "what-we-do", "solutions", "platform")),
    (4, ("services", "capabilities", "applications", "systems", "research")),
    (3, ("about", "about-us", "our-story", "company", "overview")),
    (2, ("news", "blog", "press", "newsroom", "updates")),
]

# A sentence is worth quoting if it makes a claim about a thing that exists.
_CONCRETE = re.compile(
    r"\b(build|builds|design|designs|develop|develops|manufactur\w*|engineer\w*|"
    r"produce\w*|make|makes|deploy\w*|assembl\w*|detector|sensor|robot\w*|actuator|"
    r"instrument|device|module|platform|system|machine|controller|payload|"
    r"spectromet\w*|microscop\w*|laser|optic\w*|semiconductor|battery|drone|"
    r"prosthe\w*|implant|catheter|reactor|turbine|satellite|software|firmware)\b",
    re.IGNORECASE,
)

# Marketing filler that says nothing checkable.
_FLUFF = re.compile(
    r"\b(world[- ]class|cutting[- ]edge|state[- ]of[- ]the[- ]art|industry[- ]leading|"
    r"passionate about|committed to excellence|best[- ]in[- ]class|synerg\w*|"
    r"cookie|privacy policy|all rights reserved|terms of use|subscribe)\b",
    re.IGNORECASE,
)

_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")


def _sentences(soup):
    """Readable prose from a page.

    Destructive: it strips nav/footer out of the soup it is given. Collect any
    links you need BEFORE calling this -- product links usually live in the nav.
    """
    for tag in soup(["script", "style", "noscript", "nav", "footer", "form"]):
        tag.decompose()
    blob = " ".join(soup.get_text(" ", strip=True).split())
    return [s.strip() for s in _SENTENCE_RE.split(blob) if s.strip()]


def _score(sentence):
    """How quotable is this sentence? 0 means don't."""
    words = len(sentence.split())
    if not (6 <= words <= 45) or len(sentence) > 320:
        return 0
    if _FLUFF.search(sentence):
        return 0
    if not _CONCRETE.search(sentence):
        return 0

    score = len(_CONCRETE.findall(sentence))
    if re.match(r"^(we|our)\b", sentence, re.IGNORECASE):
        score += 2  # first-person claims are the company describing itself
    if re.search(r"\d", sentence):
        score += 1  # numbers are specific
    return score


def _candidate_pages(base_url, soup, limit):
    from urllib.parse import urljoin, urlparse

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

        segments = [s for s in parts.path.lower().strip("/").split("/") if s]
        last = segments[-1] if segments else ""
        anchor_text = anchor.get_text(" ", strip=True).lower().strip().replace(" ", "-")
        for weight, fragments in PRODUCT_HINTS:
            if last in fragments or anchor_text in fragments:
                url = url.rstrip("/")
                scored[url] = max(scored.get(url, 0), weight)
                break

    ordered = sorted(scored.items(), key=lambda item: (-item[1], len(item[0])))
    return [url for url, _ in ordered[:limit]]


def gather(company, max_pages=4, max_details=2):
    """Return {details: [{text, url}], summary, pages, error}.

    Cheap and best-effort: a company we cannot read still gets an email, just a
    less specific one.
    """
    result = {"details": [], "summary": None, "pages": [], "error": None}
    domain = company.get("domain")
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

    soup = BeautifulSoup(home.text, "html.parser")

    meta = soup.find("meta", attrs={"name": "description"}) or soup.find(
        "meta", attrs={"property": "og:description"}
    )
    if meta and meta.get("content"):
        summary = " ".join(meta["content"].split())
        if 20 <= len(summary) <= 300 and not _FLUFF.search(summary):
            result["summary"] = summary

    # Collect the links first: _sentences() strips the nav that holds them.
    follow = _candidate_pages(home.url, soup, max(0, max_pages - 1))

    scored = []
    for sentence in _sentences(soup):
        weight = _score(sentence)
        if weight:
            scored.append((weight, sentence, home.url))
    result["pages"].append(home.url)

    for url in follow:
        page = web.get(url)
        if page is None:
            continue
        result["pages"].append(page.url)
        page_soup = BeautifulSoup(page.text, "html.parser")
        for sentence in _sentences(page_soup):
            weight = _score(sentence)
            if weight:
                scored.append((weight, sentence, page.url))

    scored.sort(key=lambda item: -item[0])
    seen = set()
    for _weight, sentence, url in scored:
        key = sentence.lower()[:60]
        if key in seen:
            continue
        seen.add(key)
        result["details"].append({"text": sentence, "url": url})
        if len(result["details"]) >= max_details:
            break

    if not result["details"] and result["summary"]:
        result["details"].append({"text": result["summary"], "url": home.url})
    return result
