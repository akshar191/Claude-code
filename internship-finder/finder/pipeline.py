"""The end-to-end run: criteria in, companies with contactable people out.

Searches run on a worker thread and report progress, because crawling a dozen
sites politely takes longer than an HTTP request should live for.
"""

import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed

from . import companies as companies_mod
from . import emails, industries, people as people_mod, store, text, verify

DEFAULTS = {
    "industry": "other",
    "location": "",
    "keyword": "",
    "min_employees": 1,
    "max_employees": 200,
    "min_seniority": 3,
    "max_companies": 15,
    "contacts_per_company": 3,
    "radius_m": 40000,
    "verify_emails": True,
    "include_general_inbox": True,
}


def normalize(criteria):
    merged = dict(DEFAULTS)
    merged.update({k: v for k, v in (criteria or {}).items() if v not in (None, "")})
    for field in ("min_employees", "max_employees", "min_seniority",
                  "max_companies", "contacts_per_company", "radius_m"):
        try:
            merged[field] = int(merged[field])
        except (TypeError, ValueError):
            merged[field] = DEFAULTS[field]

    merged["max_companies"] = max(1, min(merged["max_companies"], 40))
    merged["contacts_per_company"] = max(1, min(merged["contacts_per_company"], 10))
    merged["min_seniority"] = max(0, min(merged["min_seniority"], 5))
    return merged


def _merge_people(*groups):
    """One record per person, keeping the best evidence from each source."""
    merged = {}
    for group in groups:
        for person in group or []:
            if not person.get("name"):
                continue
            key = text.slug(person["name"])
            if not key:
                continue

            existing = merged.get(key)
            if not existing:
                if not person.get("first_name") or not person.get("last_name"):
                    first, last = text.split_name(person["name"])
                    person.setdefault("first_name", first)
                    person.setdefault("last_name", last)
                merged[key] = dict(person)
                continue

            for field, value in person.items():
                if value in (None, "", 0) or field in ("rank", "source"):
                    continue
                if not existing.get(field):
                    existing[field] = value
            if (person.get("rank") or 0) > (existing.get("rank") or 0):
                existing["rank"] = person["rank"]
                existing["title"] = person.get("title") or existing.get("title")
                existing["seniority"] = industries.SENIORITY_LABELS[existing["rank"]]
            if person.get("source") and person["source"] not in (existing.get("source") or ""):
                existing["source"] = "%s+%s" % (existing.get("source"), person["source"])
    return list(merged.values())


def _attach_published_emails(staff, harvested, domain):
    """Tie addresses found on the site to the people we found on the site."""
    matched = []
    for email in harvested:
        if not email.endswith("@" + (domain or "")) or emails.is_role_account(email):
            continue
        for person in staff:
            if person.get("email"):
                continue
            first, last = person.get("first_name"), person.get("last_name")
            if not first or not last:
                continue
            if emails.detect_pattern(email, first, last):
                person["email"] = email
                person["email_confidence"] = 0.95
                person["email_basis"] = "published on the company's own website"
                matched.append(dict(person))
                break
    return matched


def process_company(company, criteria, on_progress=None):
    """Crawl one company and attach its contacts. Mutates and returns it."""
    def progress(message):
        if on_progress:
            on_progress(message)

    progress("Reading %s" % (company.get("domain") or company.get("name")))

    site = people_mod.from_website(company, on_progress=on_progress)
    company["site_text"] = site["site_text"]
    company["pages_crawled"] = site["pages_crawled"]
    if site["error"]:
        company["crawl_error"] = site["error"]

    provider_people, provider_emails, provider_pattern, notes = people_mod.from_providers(
        company, criteria
    )
    company["notes"] = notes

    staff = _merge_people(site["people"], provider_people)
    harvested = sorted(set(site["emails"]) | set(provider_emails))
    company["published_emails"] = harvested

    # Learn the house email format, best evidence first.
    known = _attach_published_emails(staff, harvested, company.get("domain"))
    pattern, pattern_confidence, samples = emails.infer_pattern(known, company.get("domain"))
    if provider_pattern and not pattern:
        pattern, pattern_confidence, samples = provider_pattern, 0.85, 0
    company["email_pattern"] = pattern
    company["email_pattern_samples"] = samples

    staff = [p for p in staff if (p.get("rank") or 0) >= criteria["min_seniority"]]
    staff.sort(key=lambda p: (-(p.get("rank") or 0), p.get("name") or ""))
    staff = staff[: criteria["contacts_per_company"]]

    for person in staff:
        person["seniority"] = industries.SENIORITY_LABELS.get(person.get("rank") or 0)
        if not person.get("email"):
            guesses = emails.candidates(
                person.get("first_name"),
                person.get("last_name"),
                company.get("domain"),
                pattern=pattern,
                pattern_confidence=pattern_confidence,
            )
            if guesses:
                person["email"] = guesses[0]["email"]
                person["email_confidence"] = guesses[0]["confidence"]
                person["email_basis"] = "guessed — %s" % guesses[0]["basis"]
                person["alternates"] = [g["email"] for g in guesses[1:]]

        if person.get("email") and criteria.get("verify_emails"):
            # An address Hunter already gave us arrives scored -- re-verifying it
            # through Hunter spends a credit to learn nothing.
            from_hunter = "hunter" in (person.get("source") or "")
            check = verify.verify(person["email"], use_paid=not from_hunter)
            person["email_status"] = check["status"]
            person["email_detail"] = check["detail"]
            person["email_confidence"] = verify.score(check, person.get("email_confidence"))
        elif person.get("email"):
            person["email_status"] = "unchecked"

    # A shared inbox is a real fallback when nobody is named on the site.
    if criteria.get("include_general_inbox"):
        role_emails = [e for e in harvested if emails.is_role_account(e)]
        if role_emails and not any(p.get("email") for p in staff):
            staff.append(
                {
                    "name": "General inbox",
                    "title": "Shared company address",
                    "seniority": "Shared inbox",
                    "rank": 0,
                    "email": role_emails[0],
                    "email_confidence": 0.9,
                    "email_status": "published",
                    "email_basis": "published on the company's own website",
                    "source": "website",
                }
            )

    company["contacts"] = staff
    company["relevance"] = companies_mod.relevance(company, criteria)
    ok, size_note = companies_mod.size_ok(company, criteria)
    company["size_ok"] = ok
    company["size_note"] = size_note or (
        "headcount %s" % company["employee_count"] if company.get("employee_count")
        else "size not confirmed"
    )
    company.pop("site_text", None)
    return company


def run(criteria, on_progress=None):
    """Full search. Returns (companies, notes)."""
    criteria = normalize(criteria)
    verify.reset_budget()
    found, notes = companies_mod.discover(criteria, on_progress=on_progress)
    shortlist = found[: criteria["max_companies"]]

    if on_progress:
        on_progress("Checking %d companies for people to contact..." % len(shortlist))

    processed = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {
            pool.submit(process_company, company, criteria, on_progress): company
            for company in shortlist
        }
        for future in as_completed(futures):
            try:
                processed.append(future.result())
            except Exception as exc:
                company = futures[future]
                company["crawl_error"] = str(exc)
                company["contacts"] = []
                processed.append(company)

    kept = [c for c in processed if c.get("size_ok", True)]
    filtered_out = len(processed) - len(kept)
    if filtered_out:
        notes.append("%d company(ies) filtered out as too large." % filtered_out)

    kept.sort(
        key=lambda c: (
            -len([p for p in c.get("contacts") or [] if p.get("email")]),
            -(c.get("relevance") or 0),
        )
    )
    return kept, notes


# --------------------------------------------------------------------------
# Background jobs, so the browser can poll instead of waiting on one request
# --------------------------------------------------------------------------

_jobs = {}
_jobs_lock = threading.Lock()


def _snapshot(job):
    return {
        "id": job["id"],
        "status": job["status"],
        "progress": job["progress"][-40:],
        "criteria": job["criteria"],
        "companies": job["companies"],
        "notes": job["notes"],
        "error": job["error"],
        "search_id": job["search_id"],
        "started_at": job["started_at"],
        "finished_at": job["finished_at"],
    }


def start(criteria, label=None):
    job_id = uuid.uuid4().hex[:12]
    job = {
        "id": job_id,
        "status": "running",
        "progress": [],
        "criteria": normalize(criteria),
        "companies": [],
        "notes": [],
        "error": None,
        "search_id": None,
        "label": label,
        "started_at": time.time(),
        "finished_at": None,
    }
    with _jobs_lock:
        _jobs[job_id] = job

    def report(message):
        with _jobs_lock:
            job["progress"].append({"at": time.time(), "message": message})

    def worker():
        try:
            found, notes = run(job["criteria"], on_progress=report)
            with _jobs_lock:
                job["companies"] = found
                job["notes"] = notes
                job["status"] = "done"
                job["finished_at"] = time.time()
            search_label = label or _default_label(job["criteria"])
            job["search_id"] = store.save_results(search_label, job["criteria"], found, notes)
        except Exception as exc:
            with _jobs_lock:
                job["status"] = "error"
                job["error"] = str(exc)
                job["finished_at"] = time.time()

    threading.Thread(target=worker, daemon=True).start()
    return job_id


def _default_label(criteria):
    industry = industries.get(criteria.get("industry"))["label"]
    location = criteria.get("location") or "anywhere"
    return "%s in %s" % (industry, location)


def get(job_id):
    with _jobs_lock:
        job = _jobs.get(job_id)
        return _snapshot(job) if job else None
