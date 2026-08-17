"""Read a company's own website for concrete details worth naming in an email.

The whole value of a cold email to a 20-person company is showing you looked at
what they actually build. Generic praise ("I admire your innovative work") reads
as a mail merge; "the lithium-6 detectors you build for port screening" does not.

So: fetch the pages that describe the product, pull out sentences that make a
specific claim, and hand those to the drafter. Every detail keeps the URL it
came from so the claim can be checked before sending.
"""

import logging
import re

from bs4 import BeautifulSoup

from . import web

log = logging.getLogger("internship-finder.research")

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


# Elements that hold one idea. Reading text per block rather than flattening the
# whole page is the difference between "Alfred is a collaborative robot arm that
# preps food in commercial kitchens" and one 300-character run-on of every
# heading and button on the page.
_BLOCK_TAGS = ["p", "li", "h1", "h2", "h3", "h4", "blockquote", "figcaption",
               "td", "dd", "span", "div"]


def _sentences(soup):
    """Candidate sentences from a page, read block by block.

    Destructive: it strips nav/footer out of the soup it is given. Collect any
    links you need BEFORE calling this -- product links usually live in the nav.

    Marketing pages are headings and divs with almost no full stops, so
    flattening the document produces a single unusable blob. Instead take each
    block's own text; a block without sentence punctuation counts as one
    sentence in its own right.
    """
    for tag in soup(["script", "style", "noscript", "nav", "footer", "form",
                     "header", "aside"]):
        tag.decompose()

    found = []
    seen = set()
    for element in soup.find_all(_BLOCK_TAGS):
        # Only leaf-ish blocks: if a child holds the same text, let the child
        # supply it rather than counting the wrapper too.
        if element.find(_BLOCK_TAGS):
            continue
        text = " ".join(element.get_text(" ", strip=True).split())
        if not text:
            continue
        for sentence in _SENTENCE_RE.split(text):
            sentence = sentence.strip()
            key = sentence.lower()
            if sentence and key not in seen:
                seen.add(key)
                found.append(sentence)

    # Fall back to the flattened document if the markup gave us nothing.
    if not found:
        blob = " ".join(soup.get_text(" ", strip=True).split())
        found = [s.strip() for s in _SENTENCE_RE.split(blob) if s.strip()]
    return found


def _score(sentence):
    """How quotable is this sentence? Returns (score, reason). 0 means don't.

    The reason is kept so a failed research run can say what it rejected and
    why, instead of silently producing a generic email.
    """
    words = len(sentence.split())
    if words < 4:
        return 0, "too short"
    if words > 45 or len(sentence) > 300:
        return 0, "too long (probably a run-on of several page elements)"
    if _FLUFF.search(sentence):
        return 0, "marketing filler"
    if not _CONCRETE.search(sentence):
        return 0, "no concrete product noun"

    score = len(_CONCRETE.findall(sentence))
    if re.match(r"^(we|our)\b", sentence, re.IGNORECASE):
        score += 2  # first-person claims are the company describing itself
    if re.match(r"^[A-Z][a-zA-Z0-9-]* (is|are) an? ", sentence):
        score += 2  # "Alfred is a collaborative robot arm ..."
    if re.search(r"\d", sentence):
        score += 1  # numbers are specific
    return score, "kept"


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
    """Return {details, summary, pages, error, diagnostics}.

    `diagnostics` records what was fetched and what each candidate scored, so a
    run that finds nothing can explain itself rather than quietly handing back
    an email with no company in it.
    """
    result = {
        "details": [],
        "summary": None,
        "pages": [],
        "error": None,
        "diagnostics": {"fetched": [], "rejected": [], "considered": 0, "kept": 0},
    }
    diagnostics = result["diagnostics"]
    domain = company.get("domain")
    if not domain:
        result["error"] = "no website on file for this company"
        return result

    home = None
    tried = []
    for url in [company.get("website"), "https://%s" % domain, "http://%s" % domain]:
        if not url:
            continue
        tried.append(url)
        home = web.get(url)
        if home is not None:
            break

    if home is None:
        result["error"] = (
            "could not fetch the site (tried %s) -- unreachable, blocked, or "
            "disallowed by robots.txt" % ", ".join(tried)
        )
        log.info("research %s: fetch failed for %s", domain, tried)
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

    def read(page_soup, url, raw_length):
        sentences = _sentences(page_soup)
        diagnostics["fetched"].append({
            "url": url, "html_bytes": raw_length, "blocks": len(sentences),
        })
        diagnostics["considered"] += len(sentences)
        for sentence in sentences:
            weight, reason = _score(sentence)
            if weight:
                scored.append((weight, sentence, url))
            elif len(diagnostics["rejected"]) < 12:
                diagnostics["rejected"].append({
                    "text": sentence[:120], "why": reason,
                })
        log.info("research %s: %s -> %d blocks, %d candidates",
                 domain, url, len(sentences), len(scored))

    read(soup, home.url, len(home.text))
    result["pages"].append(home.url)

    for url in follow:
        page = web.get(url)
        if page is None:
            diagnostics["fetched"].append({"url": url, "error": "not fetched"})
            continue
        result["pages"].append(page.url)
        read(BeautifulSoup(page.text, "html.parser"), page.url, len(page.text))

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

    diagnostics["kept"] = len(result["details"])
    if not result["details"]:
        total_text = sum(f.get("blocks", 0) for f in diagnostics["fetched"])
        if total_text <= 2:
            # A React/Vue shell serves an empty <div id="root"> to a plain
            # fetch; there is no copy to read without running JavaScript.
            result["error"] = (
                "the site returned almost no readable text -- it is probably "
                "rendered by JavaScript, which this crawler does not execute"
            )
        else:
            result["error"] = (
                "read %d blocks across %d page(s) but none made a specific, "
                "quotable claim about what they build"
                % (diagnostics["considered"], len(result["pages"]))
            )
        log.info("research %s: no usable detail -- %s", domain, result["error"])

    return result
