"""ColdStart -- web UI + JSON API for the company/contact finder.

Local:
    pip install -r requirements.txt
    cp .env.example .env && edit it     # APP_PASSWORD is required
    python app.py                       # http://127.0.0.1:5001

Production (Render): gunicorn app:app -- see README.

Searches take a minute or more, which is longer than a platform will hold an
HTTP request open, so /api/search starts a background job and returns an id
that the page polls.
"""

from finder.dotenv import load as load_env

load_env()  # must run before finder.config reads the environment

import csv  # noqa: E402
import datetime  # noqa: E402
import functools  # noqa: E402
import io  # noqa: E402
import logging  # noqa: E402
import secrets  # noqa: E402
import threading  # noqa: E402
import time  # noqa: E402

from flask import (  # noqa: E402
    Flask,
    jsonify,
    redirect,
    render_template,
    request,
    Response,
    session,
    url_for,
)

from finder import (  # noqa: E402
    config,
    gmail,
    industries,
    outreach,
    pipeline,
    providers,
    research,
    store,
    verify,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("internship-finder")

app = Flask(__name__)
app.secret_key = config.SECRET_KEY
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=config.IS_PRODUCTION,
    PERMANENT_SESSION_LIFETIME=datetime.timedelta(days=7),
)
store.init_db()


# --------------------------------------------------------------------------
# Auth: one shared password, no accounts. Fails closed when unconfigured.
# --------------------------------------------------------------------------


def login_required(view):
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        if not config.APP_PASSWORD:
            message = "APP_PASSWORD is not set, so the app is refusing to serve."
            if request.path.startswith("/api/"):
                return jsonify({"error": message}), 503
            return render_template("login.html", error=message, locked=True), 503
        if not session.get("authed"):
            if request.path.startswith("/api/"):
                return jsonify({"error": "not signed in"}), 401
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)

    return wrapped


@app.route("/login", methods=["GET", "POST"])
def login():
    if not config.APP_PASSWORD:
        return render_template(
            "login.html",
            error="APP_PASSWORD is not set on the server.",
            locked=True,
        ), 503

    error = None
    if request.method == "POST":
        supplied = (request.form.get("password") or "").encode()
        # compare_digest so a wrong password takes the same time as a right one.
        if secrets.compare_digest(supplied, config.APP_PASSWORD.encode()):
            session.clear()
            session["authed"] = True
            session["sid"] = secrets.token_hex(8)
            session.permanent = True
            return redirect(request.args.get("next") or url_for("index"))
        error = "That password is not right."
        time.sleep(1)  # take the edge off guessing

    return render_template("login.html", error=error, locked=False)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# --------------------------------------------------------------------------
# Rate limiting: per browser session and per IP, whichever is stricter
# --------------------------------------------------------------------------


def _today():
    return datetime.date.today().isoformat()


def _client_ip():
    forwarded = request.headers.get("X-Forwarded-For", "")
    return (forwarded.split(",")[0].strip() or request.remote_addr or "unknown")


def searches_used():
    """Highest of the session and IP counters, so clearing cookies gains little."""
    today = _today()
    by_session = (session.get("searches") or {}).get(today, 0)
    by_ip = store.rate_count("ip:%s" % _client_ip(), today)
    return max(by_session, by_ip)


def count_search():
    today = _today()
    counts = session.get("searches") or {}
    counts = {today: counts.get(today, 0) + 1}  # only today's count is worth keeping
    session["searches"] = counts
    store.rate_increment("ip:%s" % _client_ip(), today)


_quota_cache = {"at": 0.0, "value": None}
_quota_lock = threading.Lock()


def invalidate_quota():
    with _quota_lock:
        _quota_cache.update(at=0.0, value=None)


def hunter_quota(max_age=120):
    """Remaining Hunter allowance, cached briefly so page loads are cheap."""
    with _quota_lock:
        if _quota_cache["value"] and (time.time() - _quota_cache["at"]) < max_age:
            return _quota_cache["value"]

    quota, error = providers.hunter_account()
    if error:
        result = {"configured": bool(config.HUNTER_API_KEY), "error": error}
    else:
        used = quota.get("searches_used") or 0
        available = quota.get("searches_available") or 0
        result = {
            "configured": True,
            "used": used,
            "available": available,
            "remaining": max(0, available - used),
            "resets": quota.get("reset_date"),
            "plan": quota.get("plan"),
        }

    with _quota_lock:
        _quota_cache.update(at=time.time(), value=result)
    return result


def search_allowed():
    """(allowed, reason) for whether a new search may start right now."""
    remaining = config.DAILY_SEARCH_LIMIT - searches_used()
    if remaining <= 0:
        return False, "Daily limit reached (%d searches). Try again tomorrow." % (
            config.DAILY_SEARCH_LIMIT,
        )

    quota = hunter_quota()
    if quota.get("configured") and "remaining" in quota:
        if quota["remaining"] < config.MIN_QUOTA_TO_SEARCH:
            return False, (
                "Only %d Hunter lookups left this month (resets %s). Searching is "
                "paused so the remaining credits are not spent by accident."
                % (quota["remaining"], quota.get("resets") or "soon")
            )
    return True, None


# --------------------------------------------------------------------------
# Pages
# --------------------------------------------------------------------------


@app.route("/")
@login_required
def index():
    return render_template("index.html")


@app.route("/healthz")
def healthz():
    """Unauthenticated, so the platform can health-check the service."""
    return jsonify({"ok": True})


@app.route("/api/meta")
@login_required
def meta():
    allowed, reason = search_allowed()
    return jsonify(
        {
            "industries": industries.choices(),
            "seniority_levels": [
                {"value": rank, "label": label}
                for rank, label in sorted(industries.SENIORITY_LABELS.items(), reverse=True)
                if rank > 0
            ],
            "providers": config.providers(),
            "draft_styles": [{"value": k, "label": v} for k, v in outreach.STYLES.items()],
            "quota": hunter_quota(),
            "limits": {
                "max_companies": config.MAX_COMPANIES_PER_SEARCH,
                "daily_searches": config.DAILY_SEARCH_LIMIT,
                "searches_used_today": searches_used(),
            },
            "can_search": allowed,
            "blocked_reason": reason,
        }
    )


@app.route("/api/search", methods=["POST"])
@login_required
def start_search():
    allowed, reason = search_allowed()
    if not allowed:
        return jsonify({"error": reason}), 429

    payload = request.get_json(silent=True) or {}
    if not payload.get("location") and not payload.get("keyword"):
        return jsonify({"error": "Give me at least a location or a keyword."}), 400

    # Hard cap regardless of what the client sends.
    payload["max_companies"] = min(
        int(payload.get("max_companies") or config.MAX_COMPANIES_PER_SEARCH),
        config.MAX_COMPANIES_PER_SEARCH,
    )

    count_search()
    # A search spends credits, so the cached quota reading is stale the moment
    # it starts. Without this the gate could be bypassed for up to two minutes.
    invalidate_quota()
    job_id = pipeline.start(payload, label=payload.get("label"))
    log.info("search started job=%s criteria=%s", job_id,
             {k: payload.get(k) for k in ("industry", "location", "max_companies")})
    return jsonify({"job_id": job_id, "searches_used_today": searches_used()}), 202


@app.route("/api/search/<job_id>")
@login_required
def search_status(job_id):
    job = pipeline.get(job_id)
    if not job:
        return jsonify({"error": "unknown job"}), 404
    return jsonify(job)


@app.route("/api/searches")
@login_required
def saved_searches():
    return jsonify({"searches": store.list_searches()})


@app.route("/api/searches/<int:search_id>")
@login_required
def saved_search(search_id):
    search = store.get_search(search_id)
    if not search:
        return jsonify({"error": "not found"}), 404
    return jsonify(search)


@app.route("/api/contacts/<int:contact_id>", methods=["POST"])
@login_required
def update_contact(contact_id):
    payload = request.get_json(silent=True) or {}
    try:
        updated = store.update_contact(
            contact_id, status=payload.get("status"), notes=payload.get("notes")
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"updated": updated, "contact": store.get_contact(contact_id)})


@app.route("/api/draft", methods=["POST"])
@login_required
def draft_email():
    payload = request.get_json(silent=True) or {}
    contact = payload.get("contact")
    if payload.get("contact_id"):
        contact = store.get_contact(payload["contact_id"])
    if not contact:
        return jsonify({"error": "no contact given"}), 400

    drafted = outreach.draft(contact, payload.get("profile"), payload.get("style", "advice"))
    drafted["mailto"] = outreach.mailto_link(contact, drafted)
    return jsonify(drafted)


@app.route("/api/verify", methods=["POST"])
@login_required
def verify_email():
    payload = request.get_json(silent=True) or {}
    email = (payload.get("email") or "").strip()
    if not email:
        return jsonify({"error": "no email given"}), 400
    return jsonify(verify.verify(email))


@app.route("/api/lookup/linkedin", methods=["POST"])
@login_required
def lookup_linkedin():
    """RocketReach passthrough: profile URL in, address out.

    We hand the URL to RocketReach rather than fetching LinkedIn ourselves --
    scraping LinkedIn violates their terms and gets accounts banned.
    """
    payload = request.get_json(silent=True) or {}
    url = (payload.get("linkedin_url") or "").strip()
    if "linkedin.com/in/" not in url:
        return jsonify({"error": "that is not a LinkedIn profile URL"}), 400

    result, error = providers.rocketreach_lookup(linkedin_url=url)
    if error:
        status = 400 if error == "not configured" else 502
        message = (
            "Set ROCKETREACH_API_KEY to use this."
            if error == "not configured" else error
        )
        return jsonify({"error": message}), status
    return jsonify(result)


# --------------------------------------------------------------------------
# Drafting: research the company, then write the email
# --------------------------------------------------------------------------


@app.route("/api/research", methods=["POST"])
@login_required
def research_and_draft():
    """Read the company's site, then draft an internship email from what it says."""
    payload = request.get_json(silent=True) or {}
    contact = payload.get("contact") or {}
    company = payload.get("company") or {}
    if not company.get("domain") and not company.get("website"):
        return jsonify({"error": "no company website to read"}), 400

    try:
        found = research.gather(company)
    except Exception as exc:
        log.warning("research crashed for %s: %s", company.get("domain"), exc)
        found = {"details": [], "pages": [], "error": str(exc), "diagnostics": {}}

    log.info(
        "research %s: kept=%d considered=%d pages=%s error=%s",
        company.get("domain"),
        len(found.get("details") or []),
        (found.get("diagnostics") or {}).get("considered", 0),
        found.get("pages"),
        found.get("error"),
    )

    try:
        drafted = outreach.internship_draft(
            contact, company, found, payload.get("profile")
        )
    except outreach.ResearchFailed as failure:
        # Refuse rather than hand back something not worth sending: no specific
        # detail, an unreliable greeting, or a draft that came out broken.
        headline = {
            "name": "The contact's first name does not look trustworthy.",
            "grammar": "The draft came out malformed, so it was thrown away.",
        }.get(failure.kind,
              "Could not find anything specific to say about %s."
              % (company.get("name") or "this company"))
        log.info("draft refused (%s) for %s: %s",
                 failure.kind, company.get("domain"), failure.reason)
        return jsonify({
            "error": headline,
            "reason": failure.reason,
            "kind": failure.kind,
            "diagnostics": dict(found.get("diagnostics") or {}, **failure.diagnostics),
            "pages_read": found.get("pages") or [],
            "research_failed": True,
        }), 422

    drafted["mailto"] = outreach.mailto_link(contact, drafted)
    drafted["diagnostics"] = found.get("diagnostics") or {}
    return jsonify(drafted)


# --------------------------------------------------------------------------
# Gmail: save drafts only. gmail.compose is requested; gmail.send never is.
# --------------------------------------------------------------------------


def _gmail_redirect_uri():
    return url_for("gmail_callback", _external=True, _scheme=
                   "https" if config.IS_PRODUCTION else "http")


@app.route("/api/gmail/status")
@login_required
def gmail_status():
    if not gmail.configured():
        return jsonify({"configured": False, "connected": False})
    tokens, account = store.load_tokens(session.get("sid") or "", "gmail")
    return jsonify({
        "configured": True,
        "connected": bool(tokens),
        "account": account,
        "scope": config.GMAIL_SCOPE,
    })


@app.route("/gmail/connect")
@login_required
def gmail_connect():
    if not gmail.configured():
        return "Gmail OAuth is not configured on this server.", 503
    state = secrets.token_urlsafe(16)
    session["gmail_state"] = state
    return redirect(gmail.authorize_url(_gmail_redirect_uri(), state))


@app.route("/gmail/callback")
@login_required
def gmail_callback():
    if request.args.get("state") != session.pop("gmail_state", None):
        return "State mismatch -- start again from the app.", 400
    if request.args.get("error"):
        return redirect(url_for("index"))

    code = request.args.get("code")
    if not code:
        return "No authorization code returned.", 400

    tokens, error = gmail.exchange_code(code, _gmail_redirect_uri())
    if error:
        log.warning("gmail token exchange failed: %s", error)
        return "Could not connect Gmail: %s" % error, 502

    account, _ = gmail.account_email(tokens)
    store.save_tokens(session.get("sid") or "", "gmail", tokens, account)
    return redirect(url_for("index"))


@app.route("/api/gmail/disconnect", methods=["POST"])
@login_required
def gmail_disconnect():
    store.clear_tokens(session.get("sid") or "", "gmail")
    return jsonify({"connected": False})


@app.route("/api/gmail/draft", methods=["POST"])
@login_required
def gmail_draft():
    """Create a Gmail draft. Never sends -- the user opens Gmail and decides."""
    payload = request.get_json(silent=True) or {}
    to_address = (payload.get("to") or "").strip()
    subject = (payload.get("subject") or "").strip()
    body = payload.get("body") or ""
    if not to_address or not subject or not body:
        return jsonify({"error": "need a recipient, a subject and a body"}), 400

    session_id = session.get("sid") or ""
    tokens, _account = store.load_tokens(session_id, "gmail")
    if not tokens:
        return jsonify({"error": "Gmail is not connected", "needs_auth": True}), 401

    result, error = gmail.create_draft(
        tokens, to_address, subject, body, to_name=payload.get("to_name")
    )
    if error:
        log.warning("gmail draft failed: %s", error)
        return jsonify({"error": error}), 502

    # The access token may have been refreshed during the call.
    store.save_tokens(session_id, "gmail", result.pop("tokens"), _account)

    if payload.get("contact_id"):
        store.update_contact(
            payload["contact_id"], status="contacted",
            notes=payload.get("notes") or "Gmail draft created",
        )
    log.info("gmail draft created for %s", to_address)
    return jsonify(result)


CSV_COLUMNS = [
    "name", "title", "company", "email", "confidence", "source", "company_url",
    "email_status", "how_we_got_it", "seniority", "linkedin", "company_location",
    "contacted_on", "replied", "notes",
]


def _iso_date(timestamp):
    if not timestamp:
        return ""
    return datetime.datetime.fromtimestamp(timestamp).date().isoformat()


def _csv_rows_from_job(job):
    rows = []
    for company in job.get("companies") or []:
        for contact in company.get("contacts") or []:
            rows.append({
                "name": contact.get("name"),
                "title": contact.get("title"),
                "company": company.get("name"),
                "email": contact.get("email"),
                "confidence": contact.get("email_confidence"),
                "source": contact.get("source"),
                "company_url": company.get("website") or (
                    "https://%s" % company["domain"] if company.get("domain") else ""),
                "email_status": contact.get("email_status"),
                "how_we_got_it": contact.get("email_basis"),
                "seniority": contact.get("seniority"),
                "linkedin": contact.get("linkedin_url"),
                "company_location": company.get("address"),
                "contacted_on": "",
                "replied": "",
                "notes": "",
            })
    return rows


@app.route("/api/export.csv")
@login_required
def export_csv():
    """CSV for one finished job, one saved search, or everything stored."""
    rows = []
    job_id = request.args.get("job_id")
    search_id = request.args.get("search_id", type=int)

    if job_id:
        job = pipeline.get(job_id)
        if not job:
            return jsonify({"error": "unknown job"}), 404
        rows = _csv_rows_from_job(job)
    else:
        for row in store.export_rows(search_id):
            rows.append({
                "name": row.get("name"),
                "title": row.get("title"),
                "company": row.get("company"),
                "email": row.get("email"),
                "confidence": row.get("email_confidence"),
                "source": row.get("source"),
                "company_url": row.get("website") or "",
                "email_status": row.get("email_status"),
                "how_we_got_it": row.get("email_basis"),
                "seniority": row.get("seniority"),
                "linkedin": row.get("linkedin_url"),
                "company_location": row.get("address"),
                "contacted_on": _iso_date(row.get("contacted_at")),
                "replied": "yes" if row.get("status") == "replied" else "",
                "notes": row.get("notes") or "",
            })

    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=CSV_COLUMNS, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    return Response(
        buffer.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=coldstart-contacts.csv"},
    )


def free_port(host, preferred, tries=20):
    """First free port at or after `preferred`.

    macOS runs AirPlay Receiver on 5000 and 5001 by default, so the obvious
    Flask ports are usually taken on a Mac. Rather than dying with EADDRINUSE,
    step forward until something binds.
    """
    import socket

    for candidate in range(preferred, preferred + tries):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            try:
                probe.bind((host, candidate))
                return candidate
            except OSError:
                continue
    return preferred


if __name__ == "__main__":
    import argparse
    import os

    parser = argparse.ArgumentParser(description="Run the ColdStart web UI.")
    parser.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", 5001)))
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    if not config.APP_PASSWORD:
        print("WARNING: APP_PASSWORD is not set -- the app will refuse every request.")

    port = free_port(args.host, args.port)
    if port != args.port:
        print("Port %d was busy (AirPlay Receiver, if you're on a Mac)." % args.port)
    print("\n  ColdStart -> http://%s:%d\n" % (args.host, port))
    app.run(host=args.host, port=port, debug=args.debug)
