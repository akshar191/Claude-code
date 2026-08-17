"""SQLite persistence: saved searches, the companies/contacts they produced,
and where each contact stands in your outreach.

A new connection per call keeps this safe to use from the worker threads.
"""

import json
import sqlite3
import time

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS searches (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    label      TEXT NOT NULL,
    criteria   TEXT NOT NULL,
    notes      TEXT,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS companies (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    search_id      INTEGER NOT NULL REFERENCES searches(id) ON DELETE CASCADE,
    name           TEXT NOT NULL,
    domain         TEXT,
    website        TEXT,
    address        TEXT,
    industry       TEXT,
    description    TEXT,
    employee_count INTEGER,
    size_note      TEXT,
    relevance      REAL,
    email_pattern  TEXT,
    sources        TEXT,
    created_at     REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS contacts (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id       INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    name             TEXT NOT NULL,
    title            TEXT,
    seniority        TEXT,
    rank             INTEGER DEFAULT 0,
    email            TEXT,
    email_confidence REAL,
    email_status     TEXT,
    email_basis      TEXT,
    alternates       TEXT,
    linkedin_url     TEXT,
    source           TEXT,
    status           TEXT NOT NULL DEFAULT 'new',
    contacted_at     REAL,
    notes            TEXT,
    created_at       REAL NOT NULL,
    UNIQUE (company_id, name)
);

CREATE TABLE IF NOT EXISTS provider_cache (
    key        TEXT PRIMARY KEY,
    payload    TEXT NOT NULL,
    fetched_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_companies_search ON companies(search_id);
CREATE INDEX IF NOT EXISTS idx_contacts_company ON contacts(company_id);
"""

CONTACT_STATUSES = ("new", "queued", "contacted", "replied", "skipped")


def connect():
    conn = sqlite3.connect(config.DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    with connect() as conn:
        conn.executescript(SCHEMA)


CACHE_TTL_SECONDS = 30 * 24 * 3600


def cache_get(key, ttl=CACHE_TTL_SECONDS):
    """Cached provider response, or None if missing or stale.

    Hunter's free tier is 50 lookups a month, so re-running the same search
    must not re-spend credits on domains we already asked about.
    """
    try:
        with connect() as conn:
            row = conn.execute(
                "SELECT payload, fetched_at FROM provider_cache WHERE key = ?", (key,)
            ).fetchone()
    except sqlite3.Error:
        return None

    if not row or (time.time() - row["fetched_at"]) > ttl:
        return None
    try:
        return json.loads(row["payload"])
    except ValueError:
        return None


def cache_put(key, payload):
    try:
        with connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO provider_cache (key, payload, fetched_at) "
                "VALUES (?,?,?)",
                (key, json.dumps(payload), time.time()),
            )
    except (sqlite3.Error, TypeError, ValueError):
        pass  # a cache miss is never worth failing a search over


def save_results(label, criteria, companies, notes=None):
    """Persist a completed search. Returns the new search id."""
    now = time.time()
    with connect() as conn:
        cursor = conn.execute(
            "INSERT INTO searches (label, criteria, notes, created_at) VALUES (?,?,?,?)",
            (label, json.dumps(criteria), json.dumps(notes or []), now),
        )
        search_id = cursor.lastrowid

        for company in companies:
            cursor = conn.execute(
                """INSERT INTO companies
                   (search_id, name, domain, website, address, industry, description,
                    employee_count, size_note, relevance, email_pattern, sources, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    search_id,
                    company.get("name"),
                    company.get("domain"),
                    company.get("website"),
                    company.get("address"),
                    company.get("industry"),
                    (company.get("description") or "")[:500],
                    company.get("employee_count"),
                    company.get("size_note"),
                    company.get("relevance"),
                    company.get("email_pattern"),
                    json.dumps(company.get("sources") or []),
                    now,
                ),
            )
            company_id = cursor.lastrowid

            for contact in company.get("contacts") or []:
                conn.execute(
                    """INSERT OR IGNORE INTO contacts
                       (company_id, name, title, seniority, rank, email, email_confidence,
                        email_status, email_basis, alternates, linkedin_url, source,
                        status, created_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,'new',?)""",
                    (
                        company_id,
                        contact.get("name"),
                        contact.get("title"),
                        contact.get("seniority"),
                        contact.get("rank") or 0,
                        contact.get("email"),
                        contact.get("email_confidence"),
                        contact.get("email_status"),
                        contact.get("email_basis"),
                        json.dumps(contact.get("alternates") or []),
                        contact.get("linkedin_url"),
                        contact.get("source"),
                        now,
                    ),
                )
    return search_id


def list_searches(limit=50):
    with connect() as conn:
        rows = conn.execute(
            """SELECT s.*,
                      (SELECT COUNT(*) FROM companies c WHERE c.search_id = s.id) AS company_count,
                      (SELECT COUNT(*) FROM contacts ct
                         JOIN companies c2 ON c2.id = ct.company_id
                        WHERE c2.search_id = s.id) AS contact_count
                 FROM searches s ORDER BY s.created_at DESC LIMIT ?""",
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]


def get_search(search_id):
    with connect() as conn:
        search = conn.execute("SELECT * FROM searches WHERE id = ?", (search_id,)).fetchone()
        if not search:
            return None

        companies = []
        for company in conn.execute(
            "SELECT * FROM companies WHERE search_id = ? ORDER BY relevance DESC, name",
            (search_id,),
        ).fetchall():
            record = dict(company)
            record["sources"] = json.loads(record.get("sources") or "[]")
            record["contacts"] = [
                dict(contact, alternates=json.loads(contact["alternates"] or "[]"))
                for contact in conn.execute(
                    "SELECT * FROM contacts WHERE company_id = ? ORDER BY rank DESC, name",
                    (company["id"],),
                ).fetchall()
            ]
            companies.append(record)

    result = dict(search)
    result["criteria"] = json.loads(result["criteria"])
    result["notes"] = json.loads(result.get("notes") or "[]")
    result["companies"] = companies
    return result


def update_contact(contact_id, status=None, notes=None):
    if status and status not in CONTACT_STATUSES:
        raise ValueError("unknown status: %s" % status)

    fields, values = [], []
    if status:
        fields.append("status = ?")
        values.append(status)
        fields.append("contacted_at = ?")
        values.append(time.time() if status in ("contacted", "replied") else None)
    if notes is not None:
        fields.append("notes = ?")
        values.append(notes)
    if not fields:
        return False

    values.append(contact_id)
    with connect() as conn:
        cursor = conn.execute(
            "UPDATE contacts SET %s WHERE id = ?" % ", ".join(fields), values
        )
    return cursor.rowcount > 0


def get_contact(contact_id):
    with connect() as conn:
        row = conn.execute(
            """SELECT ct.*, c.name AS company_name, c.domain AS company_domain,
                      c.website AS company_website, c.description AS company_description,
                      c.industry AS company_industry, c.address AS company_address
                 FROM contacts ct JOIN companies c ON c.id = ct.company_id
                WHERE ct.id = ?""",
            (contact_id,),
        ).fetchone()
    return dict(row) if row else None


def export_rows(search_id=None):
    """Flat contact rows for CSV export."""
    query = """SELECT c.name AS company, c.domain, c.website, c.address,
                      c.employee_count, c.size_note,
                      ct.id AS contact_id, ct.name, ct.title, ct.seniority,
                      ct.email, ct.email_confidence, ct.email_status, ct.email_basis,
                      ct.linkedin_url, ct.source, ct.status, ct.notes
                 FROM contacts ct JOIN companies c ON c.id = ct.company_id"""
    params = ()
    if search_id:
        query += " WHERE c.search_id = ?"
        params = (search_id,)
    query += " ORDER BY c.name, ct.rank DESC"

    with connect() as conn:
        return [dict(row) for row in conn.execute(query, params).fetchall()]


def delete_search(search_id):
    with connect() as conn:
        cursor = conn.execute("DELETE FROM searches WHERE id = ?", (search_id,))
        conn.execute(
            "DELETE FROM contacts WHERE company_id IN "
            "(SELECT id FROM companies WHERE search_id = ?)", (search_id,)
        )
        conn.execute("DELETE FROM companies WHERE search_id = ?", (search_id,))
    return cursor.rowcount > 0
