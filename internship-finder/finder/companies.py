"""Company discovery: turn "mechanical engineering, Boston, under 50 people"
into a list of real companies with websites.

Three sources, used together and merged:
  * Apollo        -- only source with a true employee-count filter (needs a key)
  * Google Places -- best recall for local businesses (needs a key)
  * OpenStreetMap -- free, no key, weaker coverage but genuinely useful for
                     engineering/architecture/manufacturing offices
"""

import re
import threading

from . import config, industries, providers, text, web

NOMINATIM = "https://nominatim.openstreetmap.org/search"

# overpass-api.de first: it is the reference instance and by far the most
# reliable. One fallback only -- the .ru mirror times out constantly, and
# retrying three mirrors at 25s each just makes a search feel broken.
# Timeouts are (connect, read): fail fast on connect, allow the query to run.
OVERPASS_MIRRORS = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
)
OVERPASS_TIMEOUT = (5, 40)

# Source ranking used when the same company shows up more than once.
_SOURCE_RANK = {"apollo": 3, "google_places": 2, "openstreetmap": 1}

# Obvious large employers, so a search for "Boston engineering" does not fill up
# with places nobody needs a warm intro to. Not exhaustive -- the headcount
# filter does the real work when Apollo is configured.
BIG_EMPLOYER_DOMAINS = {
    "google.com", "microsoft.com", "amazon.com", "apple.com", "meta.com",
    "ibm.com", "oracle.com", "intel.com", "cisco.com", "salesforce.com",
    "ge.com", "geaerospace.com", "raytheon.com", "rtx.com", "lockheedmartin.com",
    "boeing.com", "northropgrumman.com", "honeywell.com", "siemens.com", "bosch.com",
    "pfizer.com", "moderna.com", "novartis.com", "takeda.com", "sanofi.com",
    "jnj.com", "medtronic.com", "abbott.com", "thermofisher.com", "danaher.com",
    "deloitte.com", "pwc.com", "ey.com", "kpmg.com", "mckinsey.com", "bcg.com",
    "accenture.com", "aecom.com", "jacobs.com", "stantec.com", "wsp.com",
    "arup.com", "aecom.com", "hdrinc.com", "kiewit.com", "fluor.com",
    "mit.edu", "harvard.edu", "bu.edu", "northeastern.edu",
}

_LARGE_SITE_HINTS = [
    re.compile(r"\b(\d{3,4})\s*\+?\s*(employees|team members|people worldwide)\b", re.I),
    re.compile(r"offices in \d+\s+(countries|states)", re.I),
    re.compile(r"\b(fortune\s*(500|1000)|global leader|worldwide leader)\b", re.I),
]

_geo_lock = threading.Lock()
_geo_cache = {}


def geocode(location):
    """City/region string -> {lat, lon, label}. Free Nominatim, cached in-process."""
    key = (location or "").strip().lower()
    if not key:
        return None
    with _geo_lock:
        if key in _geo_cache:
            return _geo_cache[key]

    # Nominatim requires an identifying User-Agent; web's shared session sets one.
    payload, error = web.api(
        "GET", NOMINATIM, params={"q": location, "format": "jsonv2", "limit": 1}
    )
    result = None
    if not error and payload:
        top = payload[0]
        result = {
            "lat": float(top["lat"]),
            "lon": float(top["lon"]),
            "label": top.get("display_name", location),
        }

    with _geo_lock:
        _geo_cache[key] = result
    return result


# Scopes a name-regex search so it cannot match every named object in the city.
_NAME_SCOPES = ('["office"]', '["man_made"="works"]', '["craft"]', '["industrial"]')


def _overpass_query(filters, name_hint, lat, lon, radius_m):
    def around(tag_filter):
        return "  nwr%s(around:%d,%f,%f);" % (tag_filter, radius_m, lat, lon)

    clauses = [around(tag_filter) for tag_filter in filters]
    if name_hint:
        # Catches firms whose tags are generic but whose name gives them away.
        clauses += [around('%s["name"~"%s"]' % (scope, name_hint)) for scope in _NAME_SCOPES]

    return "[out:json][timeout:90];\n(\n%s\n);\nout center 400;" % "\n".join(clauses)


def from_openstreetmap(criteria, geo):
    """Free source. Returns (companies, error).

    Disabled by default -- see config.ENABLE_OPENSTREETMAP. Both public Overpass
    mirrors time out often enough that the source was contributing nothing but
    delay on every run.
    """
    if not config.ENABLE_OPENSTREETMAP:
        return [], None
    if not geo:
        return [], "could not geocode that location"

    industry = industries.get(criteria.get("industry"))
    query = _overpass_query(
        industry["osm"],
        industries.osm_name_hint(criteria.get("industry")),
        geo["lat"],
        geo["lon"],
        int(criteria.get("radius_m") or 25000),
    )
    payload, error = None, None
    for mirror in OVERPASS_MIRRORS:
        payload, error = web.api(
            "POST", mirror, data={"data": query}, timeout=OVERPASS_TIMEOUT
        )
        if not error:
            break
    if error:
        return [], "OpenStreetMap/Overpass unavailable (%s)" % error

    companies = []
    for element in (payload or {}).get("elements") or []:
        tags = element.get("tags") or {}
        name = tags.get("name")
        if not name:
            continue

        website = tags.get("website") or tags.get("contact:website") or tags.get("url")
        center = element.get("center") or {}
        address = " ".join(
            part
            for part in [
                tags.get("addr:housenumber"),
                tags.get("addr:street"),
                tags.get("addr:city"),
                tags.get("addr:state"),
            ]
            if part
        )
        companies.append(
            {
                "name": name,
                "domain": text.normalize_domain(website or ""),
                "website": website,
                "address": address or None,
                "industry": tags.get("office") or tags.get("craft") or tags.get("man_made"),
                "email": tags.get("email") or tags.get("contact:email"),
                "phone": tags.get("phone") or tags.get("contact:phone"),
                "lat": element.get("lat") or center.get("lat"),
                "lon": element.get("lon") or center.get("lon"),
                "source": "openstreetmap",
            }
        )
    return companies, None


def from_google_places(criteria, geo):
    industry = industries.get(criteria.get("industry"))
    queries = list(industry["places"])
    if criteria.get("keyword"):
        queries.insert(0, criteria["keyword"])

    location = criteria.get("location") or ""
    found, errors = [], []
    for query in queries[:3]:
        batch, error = providers.places_text_search(
            "%s in %s" % (query, location) if location else query,
            lat=(geo or {}).get("lat"),
            lon=(geo or {}).get("lon"),
            radius_m=int(criteria.get("radius_m") or 25000),
        )
        if error:
            if error != "not configured":
                errors.append("Google Places: %s" % error)
            break
        found.extend(batch or [])
    return found, "; ".join(errors) or None


def from_apollo(criteria):
    industry = industries.get(criteria.get("industry"))
    payload = dict(criteria)
    payload["apollo_keywords"] = industry["apollo"]
    companies, error = providers.apollo_search_companies(payload)
    if error:
        return [], None if error == "not configured" else "Apollo: %s" % error
    return companies or [], None


def _merge(into, extra):
    """Fill gaps in an existing record from a duplicate hit elsewhere."""
    for field, value in extra.items():
        if value in (None, "", []):
            continue
        if into.get(field) in (None, "", []):
            into[field] = value

    if _SOURCE_RANK.get(extra.get("source"), 0) > _SOURCE_RANK.get(into.get("source"), 0):
        into["source"] = extra["source"]
    sources = set(into.get("sources") or [into.get("source")])
    sources.add(extra.get("source"))
    into["sources"] = sorted(s for s in sources if s)
    return into


def relevance(company, criteria):
    """0-1 score for how well a company matches the requested field."""
    industry = industries.get(criteria.get("industry"))
    keywords = list(industry["keywords"])
    if criteria.get("keyword"):
        keywords += criteria["keyword"].lower().split()
    if not keywords:
        return 0.5

    haystack = " ".join(
        str(company.get(field) or "")
        for field in ("name", "description", "industry", "site_text")
    ).lower()
    hits = sum(1 for keyword in keywords if keyword in haystack)
    return min(1.0, hits / 3.0)


# Phrases that mark a site as a directory rather than an employer. MassRobotics
# is the case that motivated this: a nonprofit hub whose site lists the founders
# of *other* companies, which produced colin.angle@massrobotics.org for the
# person who founded iRobot.
_DIRECTORY_PHRASES = (
    "member companies", "our members", "membership", "accelerator", "incubator",
    "startup community", "innovation hub", "industry association",
    "trade association", "consortium", "coalition", "our startups",
    "resident companies", "portfolio companies", "our portfolio", "ecosystem",
    "nonprofit", "non-profit", "501(c)", "we connect", "founding sponsors",
)

_DIRECTORY_NAME_HINTS = (
    "robotics cluster", "association", "council", "foundation", "institute",
    "alliance", "network", "hub", "accelerator", "incubator", "chamber",
)


def looks_like_directory(company):
    """True when a site lists other people's companies rather than employing anyone.

    Returns (is_directory, evidence).
    """
    blob = " ".join(
        str(company.get(field) or "")
        for field in ("name", "description", "industry", "site_text")
    ).lower()

    hits = [phrase for phrase in _DIRECTORY_PHRASES if phrase in blob]
    name_hit = any(hint in (company.get("name") or "").lower()
                   for hint in _DIRECTORY_NAME_HINTS)
    is_org = (company.get("domain") or "").endswith((".org", ".edu"))

    # Two independent signals, or one plus a .org/.edu domain.
    if len(hits) >= 2 or (hits and (is_org or name_hit)):
        return True, hits[0]
    return False, None


def looks_large(site_text):
    """Cheap 'this is not a small company' check from the company's own copy."""
    for pattern in _LARGE_SITE_HINTS:
        match = pattern.search(site_text or "")
        if match:
            return True, match.group(0).strip()
    return False, None


def size_ok(company, criteria):
    """Apply the headcount filter with whatever size evidence we have."""
    minimum = criteria.get("min_employees") or 1
    maximum = criteria.get("max_employees") or 10 ** 9

    count = company.get("employee_count")
    if isinstance(count, int) and count > 0:
        return minimum <= count <= maximum, "headcount %s" % count

    if company.get("domain") in BIG_EMPLOYER_DOMAINS:
        return False, "known large employer"

    large, evidence = looks_large(company.get("site_text"))
    if large and maximum < 500:
        return False, "site says: %s" % evidence

    # No hard evidence -- keep it and mark the size as unconfirmed.
    return True, None


def discover(criteria, on_progress=None):
    """Run every configured source and merge the results.

    Returns (companies, notes) where notes lists non-fatal source problems.
    """

    def progress(message):
        if on_progress:
            on_progress(message)

    notes = []
    # Only OSM needs coordinates; Places takes the location as text. Skip the
    # geocode round-trip (and its failure mode) when OSM is off.
    geo = None
    if criteria.get("location") and config.ENABLE_OPENSTREETMAP:
        geo = geocode(criteria["location"])
    if criteria.get("location") and config.ENABLE_OPENSTREETMAP and not geo:
        notes.append("Could not place '%s' on the map; falling back to text search."
                     % criteria["location"])

    merged = {}
    for label, runner in (
        ("Apollo", lambda: from_apollo(criteria)),
        ("Google Places", lambda: from_google_places(criteria, geo)),
        ("OpenStreetMap", lambda: from_openstreetmap(criteria, geo)),
    ):
        progress("Searching %s..." % label)
        try:
            found, error = runner()
        except Exception as exc:  # a dead source must not kill the search
            found, error = [], "%s: %s" % (label, exc)
        if error:
            notes.append(error)

        for company in found:
            company.setdefault("sources", [company.get("source")])
            key = text.company_key(company.get("name"), company.get("domain"))
            if key in merged:
                _merge(merged[key], company)
            else:
                merged[key] = company

    companies = [c for c in merged.values() if c.get("name")]

    # A company with no website is a dead end -- there is nothing to crawl and no
    # domain to build an email address on.
    with_domain = [c for c in companies if c.get("domain")]
    dropped = len(companies) - len(with_domain)
    if dropped:
        notes.append("Skipped %d result(s) with no website." % dropped)

    for company in with_domain:
        company["name"] = text.clean_company_name(company["name"]) or company["name"]
        company["relevance"] = relevance(company, criteria)

    with_domain.sort(key=lambda c: (-c["relevance"], -_SOURCE_RANK.get(c.get("source"), 0)))
    progress("Found %d candidate companies." % len(with_domain))
    return with_domain, notes
