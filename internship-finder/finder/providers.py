"""Thin wrappers around the optional paid APIs.

Each function returns (result, error). They are all no-ops returning
(None, "not configured") when the matching key is absent, so callers can just
try them and move on. API response shapes drift over time -- everything here
reads defensively.
"""

from . import config, store, text, web

HUNTER_BASE = "https://api.hunter.io/v2"
APOLLO_BASE = "https://api.apollo.io/api/v1"
ROCKETREACH_BASE = "https://api.rocketreach.co/api/v2"
PLACES_SEARCH = "https://places.googleapis.com/v1/places:searchText"


# --------------------------------------------------------------------------
# Hunter.io -- best source for "what does an email at this domain look like"
# --------------------------------------------------------------------------


def hunter_domain_search(domain, limit=10):
    """Known emails at a domain plus the domain's detected email pattern.

    limit must stay <= 10: the free plan rejects anything larger with a 400
    pagination_error, which fails the whole call -- including the pattern,
    which is the part we actually care about.
    """
    if not config.HUNTER_API_KEY:
        return None, "not configured"
    limit = max(1, min(int(limit), 10))

    cache_key = "hunter:domain:%s" % (domain or "").lower()
    cached = store.cache_get(cache_key)
    if cached is not None:
        return cached, None

    payload, error = web.api(
        "GET",
        HUNTER_BASE + "/domain-search",
        params={"domain": domain, "limit": limit, "api_key": config.HUNTER_API_KEY},
    )
    if error:
        return None, error

    data = (payload or {}).get("data") or {}
    people = []
    for item in data.get("emails") or []:
        people.append(
            {
                "email": item.get("value"),
                "first_name": item.get("first_name"),
                "last_name": item.get("last_name"),
                "title": item.get("position"),
                "linkedin_url": item.get("linkedin"),
                "confidence": item.get("confidence"),
                "type": item.get("type"),
                "source": "hunter",
            }
        )
    result = {
        "pattern": data.get("pattern"),
        "organization": data.get("organization"),
        "people": people,
    }
    store.cache_put(cache_key, result)
    return result, None


def hunter_account():
    """Remaining free-tier allowance, so a run can report what it spent."""
    if not config.HUNTER_API_KEY:
        return None, "not configured"

    payload, error = web.api(
        "GET", HUNTER_BASE + "/account", params={"api_key": config.HUNTER_API_KEY}
    )
    if error:
        return None, error

    requests_info = ((payload or {}).get("data") or {}).get("requests") or {}
    searches = requests_info.get("searches") or {}
    verifications = requests_info.get("verifications") or {}
    return {
        "plan": ((payload or {}).get("data") or {}).get("plan_name"),
        "searches_used": searches.get("used"),
        "searches_available": searches.get("available"),
        "verifications_used": verifications.get("used"),
        "verifications_available": verifications.get("available"),
        "reset_date": ((payload or {}).get("data") or {}).get("reset_date"),
    }, None


def hunter_email_finder(domain, first_name, last_name):
    if not config.HUNTER_API_KEY:
        return None, "not configured"

    cache_key = "hunter:finder:%s:%s:%s" % (
        (domain or "").lower(), (first_name or "").lower(), (last_name or "").lower())
    cached = store.cache_get(cache_key)
    if cached is not None:
        return cached, None

    payload, error = web.api(
        "GET",
        HUNTER_BASE + "/email-finder",
        params={
            "domain": domain,
            "first_name": first_name,
            "last_name": last_name,
            "api_key": config.HUNTER_API_KEY,
        },
    )
    if error:
        return None, error

    data = (payload or {}).get("data") or {}
    if not data.get("email"):
        return None, "no match"
    found = {"email": data["email"], "confidence": data.get("score"), "source": "hunter"}
    store.cache_put(cache_key, found)
    return found, None


def hunter_verify(email):
    if not config.HUNTER_API_KEY:
        return None, "not configured"

    payload, error = web.api(
        "GET",
        HUNTER_BASE + "/email-verifier",
        params={"email": email, "api_key": config.HUNTER_API_KEY},
    )
    if error:
        return None, error

    data = (payload or {}).get("data") or {}
    return {
        "status": data.get("status"),
        "result": data.get("result"),
        "score": data.get("score"),
        "accept_all": data.get("accept_all"),
        "disposable": data.get("disposable"),
    }, None


# --------------------------------------------------------------------------
# Apollo.io -- the only source here with a real employee-count filter
# --------------------------------------------------------------------------

APOLLO_SIZE_BUCKETS = ["1,10", "11,20", "21,50", "51,100", "101,200", "201,500",
                       "501,1000", "1001,2000", "2001,5000", "5001,10000", "10001,1000000"]


def _apollo_ranges(min_size, max_size):
    """Apollo takes discrete headcount buckets -- keep any that overlap the range."""
    keep = []
    for bucket in APOLLO_SIZE_BUCKETS:
        low, high = (int(part) for part in bucket.split(","))
        if high >= (min_size or 1) and low <= (max_size or 10 ** 9):
            keep.append(bucket)
    return keep


def apollo_search_companies(criteria, page=1, per_page=25):
    if not config.APOLLO_API_KEY:
        return None, "not configured"

    body = {
        "page": page,
        "per_page": per_page,
        "organization_num_employees_ranges": _apollo_ranges(
            criteria.get("min_employees"), criteria.get("max_employees")
        ),
    }
    if criteria.get("location"):
        body["organization_locations"] = [criteria["location"]]
    keywords = criteria.get("apollo_keywords") or []
    if criteria.get("keyword"):
        keywords = [criteria["keyword"]] + list(keywords)
    if keywords:
        body["q_organization_keyword_tags"] = keywords

    payload, error = web.api(
        "POST",
        APOLLO_BASE + "/mixed_companies/search",
        json=body,
        headers={
            "x-api-key": config.APOLLO_API_KEY,
            "Content-Type": "application/json",
            "Cache-Control": "no-cache",
        },
    )
    if error:
        return None, error

    raw = (payload or {}).get("organizations") or (payload or {}).get("accounts") or []
    companies = []
    for org in raw:
        domain = text.normalize_domain(org.get("website_url") or org.get("primary_domain") or "")
        companies.append(
            {
                "name": org.get("name"),
                "domain": domain,
                "website": org.get("website_url"),
                "employee_count": org.get("estimated_num_employees"),
                "industry": org.get("industry"),
                "description": (org.get("short_description") or "")[:400],
                "linkedin_url": org.get("linkedin_url"),
                "address": ", ".join(
                    part for part in [org.get("city"), org.get("state")] if part
                ),
                "apollo_id": org.get("id"),
                "source": "apollo",
            }
        )
    return companies, None


def apollo_search_people(domain, titles, per_page=10):
    if not config.APOLLO_API_KEY:
        return None, "not configured"

    payload, error = web.api(
        "POST",
        APOLLO_BASE + "/mixed_people/search",
        json={
            "q_organization_domains_list": [domain],
            "person_titles": titles,
            "page": 1,
            "per_page": per_page,
        },
        headers={
            "x-api-key": config.APOLLO_API_KEY,
            "Content-Type": "application/json",
            "Cache-Control": "no-cache",
        },
    )
    if error:
        return None, error

    people = []
    for person in (payload or {}).get("people") or []:
        email = person.get("email") or ""
        # Apollo returns this placeholder when your plan will not reveal the address.
        if "email_not_unlocked" in email:
            email = None
        people.append(
            {
                "name": person.get("name")
                or " ".join(p for p in [person.get("first_name"), person.get("last_name")] if p),
                "first_name": person.get("first_name"),
                "last_name": person.get("last_name"),
                "title": person.get("title"),
                "email": email,
                "linkedin_url": person.get("linkedin_url"),
                "source": "apollo",
            }
        )
    return people, None


# --------------------------------------------------------------------------
# RocketReach -- LinkedIn profile URL in, email out (the flow you already use)
# --------------------------------------------------------------------------


def rocketreach_lookup(linkedin_url=None, name=None, domain=None):
    if not config.ROCKETREACH_API_KEY:
        return None, "not configured"

    params = {}
    if linkedin_url:
        params["li_url"] = linkedin_url
    elif name and domain:
        params["name"] = name
        params["current_employer_domain"] = domain
    else:
        return None, "need a LinkedIn URL, or a name plus a company domain"

    payload, error = web.api(
        "GET",
        ROCKETREACH_BASE + "/person/lookup",
        params=params,
        headers={"Api-Key": config.ROCKETREACH_API_KEY},
    )
    if error:
        return None, error

    payload = payload or {}
    emails = []
    for entry in payload.get("emails") or []:
        if isinstance(entry, dict) and entry.get("email"):
            emails.append(
                {
                    "email": entry["email"],
                    "type": entry.get("type"),
                    "grade": entry.get("grade"),
                    "smtp_valid": entry.get("smtp_valid"),
                }
            )
        elif isinstance(entry, str):
            emails.append({"email": entry})

    # Professional addresses first, then by RocketReach's own confidence grade.
    emails.sort(key=lambda e: (e.get("type") != "professional", e.get("grade") or "Z"))
    return {
        "name": payload.get("name"),
        "title": payload.get("current_title"),
        "employer": payload.get("current_employer"),
        "linkedin_url": payload.get("linkedin_url"),
        "emails": emails,
        "status": payload.get("status"),
        "source": "rocketreach",
    }, None


# --------------------------------------------------------------------------
# Google Places -- company discovery with far better recall than OSM
# --------------------------------------------------------------------------


def places_text_search(query, lat=None, lon=None, radius_m=25000, limit=20):
    if not config.GOOGLE_PLACES_API_KEY:
        return None, "not configured"

    body = {"textQuery": query, "maxResultCount": min(limit, 20)}
    if lat is not None and lon is not None:
        body["locationBias"] = {
            "circle": {
                "center": {"latitude": lat, "longitude": lon},
                "radius": float(min(radius_m, 50000)),
            }
        }

    payload, error = web.api(
        "POST",
        PLACES_SEARCH,
        json=body,
        headers={
            "X-Goog-Api-Key": config.GOOGLE_PLACES_API_KEY,
            "X-Goog-FieldMask": (
                "places.id,places.displayName,places.formattedAddress,"
                "places.websiteUri,places.primaryTypeDisplayName,places.location"
            ),
            "Content-Type": "application/json",
        },
    )
    if error:
        return None, error

    companies = []
    for place in (payload or {}).get("places") or []:
        website = place.get("websiteUri")
        location = place.get("location") or {}
        companies.append(
            {
                "name": (place.get("displayName") or {}).get("text"),
                "domain": text.normalize_domain(website or ""),
                "website": website,
                "address": place.get("formattedAddress"),
                "industry": (place.get("primaryTypeDisplayName") or {}).get("text"),
                "lat": location.get("latitude"),
                "lon": location.get("longitude"),
                "place_id": place.get("id"),
                "source": "google_places",
            }
        )
    return companies, None
