# Internship Finder

Finds small companies in a given field and city, works out who's senior enough
there to answer a cold email, and finds their email address — labelling every
address with how confident it is and *why*.

Built for the student internship hunt, where the useful targets are 10–50 person
companies whose founder reads their own inbox, and the hard part is getting from
"this company looks interesting" to "here is a person and an address".

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

## Cost control

The free tiers are small (Hunter is 50 lookups/month), and this is deployed
publicly, so spending is capped in several independent places:

- **Provider cache** — Hunter `domain-search` and `email-finder` responses are
  cached in SQLite for 30 days, so a repeated search costs nothing.
- **Per-run budgets** — `HUNTER_FINDER_BUDGET` (6) per-person lookups and
  `HUNTER_VERIFY_BUDGET` (10) verifications per search.
- **Rate limits** — `DAILY_SEARCH_LIMIT` (5) per session *and* per IP, whichever
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
| `DAILY_SEARCH_LIMIT` | no | Default 5 |
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

Runs on Render as a single web service; see `render.yaml`. The start command is:

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

118 tests, all offline.

- `test_finder.py` — parsing, title ranking, pattern inference, size and
  directory filtering, caching, drafting, storage.
- `test_integration.py` — spins up a local fixture site and runs the whole
  pipeline: follows the leadership link, obeys robots.txt, takes the published
  address, learns the pattern, applies it to everyone else.
- `test_app.py` — the password gate, rate limits, quota gate, CSV columns.

Most tests exist because something failed in a live run. `test_finder.py`
contains the literal strings that broke it: `PhD CEO & Chairman`,
`LEED AP BD+C President Boston`, `Supply Chain`, `M Dinne Flansbury`,
`Welcoming Hiten Sonpal: RISE Robotics' New CEO`.
