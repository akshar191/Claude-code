"""Web UI + JSON API for the company/contact finder.

    pip install -r requirements.txt
    cp .env.example .env      # optional -- it runs with no keys at all
    python app.py             # http://127.0.0.1:5001
"""

from finder.dotenv import load as load_env

load_env()  # must run before finder.config reads the environment

import csv  # noqa: E402
import io  # noqa: E402

from flask import Flask, jsonify, render_template, request, Response  # noqa: E402
from flask_cors import CORS  # noqa: E402

from finder import (  # noqa: E402
    config,
    industries,
    outreach,
    pipeline,
    providers,
    store,
    verify,
)

app = Flask(__name__)
CORS(app)
store.init_db()


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/meta")
def meta():
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
            "defaults": pipeline.DEFAULTS,
        }
    )


@app.route("/api/search", methods=["POST"])
def start_search():
    payload = request.get_json(silent=True) or {}
    if not payload.get("location") and not payload.get("keyword"):
        return jsonify({"error": "Give me at least a location or a keyword."}), 400
    job_id = pipeline.start(payload, label=payload.get("label"))
    return jsonify({"job_id": job_id}), 202


@app.route("/api/search/<job_id>")
def search_status(job_id):
    job = pipeline.get(job_id)
    if not job:
        return jsonify({"error": "unknown job"}), 404
    return jsonify(job)


@app.route("/api/searches")
def saved_searches():
    return jsonify({"searches": store.list_searches()})


@app.route("/api/searches/<int:search_id>")
def saved_search(search_id):
    search = store.get_search(search_id)
    if not search:
        return jsonify({"error": "not found"}), 404
    return jsonify(search)


@app.route("/api/searches/<int:search_id>", methods=["DELETE"])
def remove_search(search_id):
    return jsonify({"deleted": store.delete_search(search_id)})


@app.route("/api/contacts/<int:contact_id>", methods=["POST"])
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
def verify_email():
    payload = request.get_json(silent=True) or {}
    email = (payload.get("email") or "").strip()
    if not email:
        return jsonify({"error": "no email given"}), 400
    return jsonify(verify.verify(email))


@app.route("/api/lookup/linkedin", methods=["POST"])
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
            "Set ROCKETREACH_API_KEY in .env to use this."
            if error == "not configured" else error
        )
        return jsonify({"error": message}), status
    return jsonify(result)


@app.route("/api/export.csv")
def export_csv():
    search_id = request.args.get("search_id", type=int)
    rows = store.export_rows(search_id)
    if not rows:
        return Response("no results yet\n", mimetype="text/csv")

    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(rows[0].keys()))
    writer.writeheader()
    writer.writerows(rows)
    return Response(
        buffer.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=contacts.csv"},
    )


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5001, debug=True)
