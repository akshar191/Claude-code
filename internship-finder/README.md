# ColdStart

**Find the people behind the companies.**

Finds small companies in a given field and city, works out who's senior enough
there to answer a cold email, and finds their email address — labelling every
address with how confident it is and *why*. Then drafts the email, using
something specific the company says about itself.

It does not find job listings. It finds companies, and the humans at them you
could actually reach. The useful targets are 10–50 person companies whose
founder reads their own inbox, and the hard part is getting from "this company
looks interesting" to "here is a person and an address".

```bash
python app.py     # web UI
python cli.py     # same thing, scriptable
python demo.py    # offline demo, no keys, no network
```

## What problem it actually solves

Commercial tools (Apollo, RocketReach, Hunter) will sell you contact data, but
they are built for sales teams with budgets, and their free tiers are small.
This does three things they don't:

1. **Runs with no API keys at all.** The free path — OpenStreetMap for discovery
   plus crawling each company's own site — finds people and addresses without a
   subscription. Keys make it better; they aren't required.
2. **Filters for small.** The entire point is companies where a cold email
   reaches a decision-maker rather than an applicant-tracking system.
3. **Never presents a guess as a fact.** Every address carries a confidence
   score and a plain-English basis. A guessed address says so.

## Architecture

```
        criteria (field, city, size, seniority)
                        │
        ┌───────────────▼────────────────┐
        │  companies.discover()          │   Apollo ─┐
        │  merge + dedupe by domain      │   Places ─┼─► one company list
        └───────────────┬────────────────┘   OSM    ─┘
                        │
        ┌───────────────▼────────────────┐
        │  per company, in a thread pool │
        │                                │
        │  people.from_website()   ──────┼──► crawl /team, /leadership, /about
        │  people.from_providers() ──────┼──► Hunter domain-search, Apollo
        │  emails.infer_pattern()  ──────┼──► learn {f}{last} from real data
        │  providers.hunter_email_finder ┼──► ask Hunter about this person
        │  verify.verify()         ──────┼──► syntax → MX → Hunter verifier
        └───────────────┬────────────────┘
                        │
                  ranked contacts ──► SQLite ──► web UI / CSV
```

### Module map

| File | Responsibility |
|---|---|
| `app.py` | Flask UI + JSON API, password gate, rate limits |
| `finder/research.py` | Reads a company site for quotable, specific claims |
| `cli.py` | Command-line entry point |
| `finder/pipeline.py` | Orchestration; background jobs; per-run budgets |
| `finder/companies.py` | Discovery, merging, size + directory filtering |
| `finder/people.py` | Site crawl, name/title extraction, seniority |
| `finder/emails.py` | Harvesting, pattern inference, candidate generation |
| `finder/verify.py` | Syntax / MX / Hunter verifier / optional SMTP |
| `finder/providers.py` | Hunter, Apollo, RocketReach, Google Places + caching |
| `finder/store.py` | SQLite: searches, contacts, provider cache, rate limits |
| `finder/industries.py` | Field taxonomy and the 1–5 seniority ladder |
| `finder/outreach.py` | Email drafting |

### How an address is obtained, best first

1. **Published on the site.** Scraped from the company's own pages, including
   Cloudflare-obfuscated `data-cfemail` attributes. ~95%.
2. **Hunter, per person.** `email-finder` asked about that specific human.
   Carries Hunter's own score.
3. **Company pattern.** If a known address maps onto a person we found —
   `priya.raghavan@` for Priya Raghavan — the format is `{first}.{last}`, and it
   applies to everyone else at that domain. Hunter's reported `pattern` is used
   the same way. ~75%.
4. **Base-rate guessing.** "A third of companies use `first.last`." **Off by
   default** (`--guess` to enable), because these bounce, and a bounce on a cold
   introduction is worse than no address.

Verification then runs syntax → MX → Hunter's verifier (budgeted) → optional
SMTP probe. Anything not positively confirmed reports `unknown`, never `valid`.

### Why the searches run in the background

A search reads several pages per company at one request per second per host, so
it takes a minute or more — longer than a platform will hold an HTTP request
open. `POST /api/search` starts a worker thread and returns a job id;
`GET /api/search/<id>` returns status, progress lines and partial results, and
the page polls it.

This is why the service runs **one gunicorn worker with threads**: the job
registry lives in process memory, so a second worker wouldn't see jobs started
by the first.

## Drafting an email

Each contact row has a **Draft email** button. Pressing it:

1. **Reads the company's site** (`finder/research.py`) — homepage plus the
   product/technology/about pages — and pulls out sentences that make a
   checkable claim. Text is read **block by block**, not by flattening the
   page: marketing sites are headings and divs with almost no full stops, and
   flattening one produces a single run-on that no filter can use. Sentences
   are scored — first-person claims ("We build…"), "X is a Y" definitions, and
   numbers score up; marketing filler ("world-class", "cutting-edge") and
   boilerplate ("all rights reserved") score zero.
2. **Refuses if it found nothing.** No specific detail means no email. The API
   returns 422 with what it fetched, how many blocks it read, and why each
   candidate was rejected. A generic email dressed up as a personal one is
   worse than none: the contact is spent either way, and the generic version
   guarantees no reply.
3. **Writes the email** (`outreach.internship_draft`) **referencing** the detail
   in plain language — "What interests me about X is your work on Y" — never
   quoting it. Pasting a company's own marketing copy back at the person who
   wrote it is worse than saying nothing. `outreach.reference_phrase` reduces a
   site sentence to a noun phrase that reads as your own words, and flags the
   ones it could not normalise so you rewrite them yourself. Your background
   comes from `APPLICANT_*` config, inserted **exactly as written**.
4. **Adapts the ask to seniority.** Rank 4 and up (founder, CEO, VP, partner)
   can actually say yes, so they get the direct internship ask. Below that —
   directors, managers, engineers — the email asks about their work and makes
   no request, because asking them for a job is asking the wrong person.
5. **Shows it in an editable textarea.** The raw claim and the URL it came from
   sit beside the draft — not inside the email — so you can check the one thing
   a machine inferred from a web page. The address confidence is shown up front,
   and below ~50% it warns you before you spend time editing a draft to an
   address that will probably bounce.
6. **Says nothing about the outreach itself.** No claims about not mass-mailing,
   no explaining why you picked them, no narrating the personalisation — an
   email that insists it is not a template reads as exactly the thing it denies
   being. Tests assert those phrases never appear.
7. **Saves to Gmail Drafts** — never sends.

### Gmail scope

The OAuth scope requested is `gmail.compose` and nothing else. That permits
creating a draft; it does not permit sending. Even a stolen token cannot mail
anyone from the account. A test asserts `gmail.send` never appears in the
consent URL.

Tokens are stored in SQLite keyed by session, not in the cookie — Flask session
cookies are signed but not encrypted, and a refresh token does not belong in
something the browser can read.

Marking a draft as created sets the contact's status to `contacted`, which flows
through to the `contacted_on` / `replied` / `notes` columns in the CSV.

## Cost control

The free tiers are small (Hunter is 50 lookups/month), and this is deployed
publicly, so spending is capped in several independent places:

- **Provider cache** — Hunter `domain-search` and `email-finder` responses are
  cached in SQLite for 30 days, so a repeated search costs nothing.
- **Per-run budgets** — `HUNTER_FINDER_BUDGET` (6) per-person lookups and
  `HUNTER_VERIFY_BUDGET` (10) verifications per search.
- **Rate limits** — `DAILY_SEARCH_LIMIT` (2) per session *and* per IP, whichever
  is stricter, so clearing cookies gains little.
- **Hard cap** — `MAX_COMPANIES_PER_SEARCH` (5), enforced server-side.
- **Quota gate** — when fewer than `MIN_QUOTA_TO_SEARCH` Hunter lookups remain,
  the search button is disabled with an explanation.

## Rules it follows

- **robots.txt is honoured** on every company site.
- **One request per host per second**, with an identifying User-Agent.
- **No LinkedIn scraping.** Profile URLs go to RocketReach's API instead;
  scraping LinkedIn breaks their terms and gets accounts banned.
- **Directory sites are skipped.** An accelerator's website lists the founders
  of *other* companies; scraping one invents contacts at an organisation that
  never employed them.
- **SMTP probing is off by default** — most networks block port 25, and
  aggressive probing gets an IP blocklisted.
- This is for sending individual, personal emails. Bulk-mailing scraped
  addresses is a different activity with different laws around it.

## Configuration

Everything comes from environment variables; `.env` is read at startup and is
gitignored. No key appears anywhere in the repository or its history.

| Variable | Required | Purpose |
|---|---|---|
| `APP_PASSWORD` | **yes** | Shared password. Unset ⇒ the app serves nothing |
| `SECRET_KEY` | production | Signs the session cookie |
| `HUNTER_API_KEY` | no | Email patterns and per-person lookups |
| `GOOGLE_PLACES_API_KEY` | no | Much better company discovery than OSM alone |
| `APOLLO_API_KEY` | no | The only source with a real headcount filter |
| `ROCKETREACH_API_KEY` | no | LinkedIn URL → address |
| `CONTACT_EMAIL` | no | Identifies the crawler to sites it reads |
| `DAILY_SEARCH_LIMIT` | no | Default 2 |
| `MAX_COMPANIES_PER_SEARCH` | no | Default 5 |

## Local development

```bash
cd internship-finder
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

echo "APP_PASSWORD=whatever" > .env
python app.py          # prints the URL it bound to
```

On macOS, ports 5000/5001 belong to AirPlay Receiver; the server steps to the
next free port and prints where it landed.

## Deployment

Runs on Render as a single web service; see `render.yaml`. The service name
and URL stay as they were — renaming would break the Gmail OAuth redirect URI. The start command is:

```
gunicorn app:app --workers 1 --threads 8 --timeout 180 --bind 0.0.0.0:$PORT
```

**Known limitation:** on Render's free plan the filesystem is ephemeral, so
SQLite resets on every deploy and restart. Saved searches, the provider cache
and the rate-limit counters are all lost. Searching still works — the cache just
starts cold, which costs Hunter credits. A persistent disk or Postgres fixes it.

## Tests

```bash
python -m unittest discover -s tests
```

159 tests, all offline.

- `test_finder.py` — parsing, title ranking, pattern inference, size and
  directory filtering, caching, drafting, storage.
- `test_integration.py` — spins up a local fixture site and runs the whole
  pipeline: follows the leadership link, obeys robots.txt, takes the published
  address, learns the pattern, applies it to everyone else.
- `test_app.py` — the password gate, rate limits, quota gate, CSV columns.
- `test_drafting.py` — page extraction, the refusal contract, verbatim
  applicant facts, the no-hedging rule, and the seniority-adaptive ask.

Most tests exist because something failed in a live run. `test_finder.py`
contains the literal strings that broke it: `PhD CEO & Chairman`,
`LEED AP BD+C President Boston`, `Supply Chain`, `M Dinne Flansbury`,
`Welcoming Hiten Sonpal: RISE Robotics' New CEO`.
