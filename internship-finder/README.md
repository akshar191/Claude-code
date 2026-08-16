# Internship Finder

Find small companies in your field and city, figure out who's senior enough to
say yes, and get an address you can actually email — then draft the email.

Built for the cold-outreach internship hunt: pick "Mechanical Engineering,
Boston, under 200 people", get back a list of 10-15 real companies with named
founders/VPs/directors and their email addresses, each labelled with how
confident we are and *why*.

```
python app.py          # web UI at http://127.0.0.1:5001
python cli.py --help   # same thing from the terminal
```

## Why not just use Apollo/RocketReach

You still can — this wraps both when you have keys. The difference:

- **It runs with zero API keys.** The free path (OpenStreetMap + crawling the
  company's own website) finds people and addresses without a subscription,
  which matters when you and your friends are splitting one free tier.
- **It filters for small.** The whole point is companies where a cold email
  reaches a decision-maker, not a careers portal.
- **It tells you when it's guessing.** A guessed address is labelled a guess
  with a confidence number and the reasoning. Nothing is presented as verified
  unless something actually verified it.
- **It drafts the email**, personalised from what it read on their site.

## How it works

```
criteria ──► company discovery ──► site crawl ──► people ──► emails ──► verify ──► draft
             Apollo / Places /     team pages     name +     published    MX /      mailto
             OpenStreetMap                        title      or inferred  Hunter
```

**1. Company discovery** (`finder/companies.py`) — runs every configured source
and merges by domain:

| Source | Key needed | What it's good for |
|---|---|---|
| Apollo | `APOLLO_API_KEY` | The only real employee-count filter |
| Google Places | `GOOGLE_PLACES_API_KEY` | Much better recall for local businesses |
| OpenStreetMap | none | Free; solid for engineering/architecture/manufacturing offices |

**2. Finding people** (`finder/people.py`) — small companies list their
leadership on their own site, so we fetch the homepage, follow links that look
like `/team`, `/leadership`, `/about`, and pull out name + title pairs. Titles
are ranked 1-5 (`finder/industries.py`) so you can say "director and above".

**3. Finding addresses** (`finder/emails.py`) — in order of trustworthiness:

1. Addresses published on the site (including Cloudflare-obfuscated ones).
2. If a published address matches a person we found, we learn the company's
   format — `priya.raghavan@` for Priya Raghavan means `{first}.{last}` — and
   apply it to everyone else. Two agreeing samples gets you ~90% confidence.
3. No pattern to learn from? Fall back to the frequency table of corporate
   address formats. These are capped below 50% confidence on purpose.

**4. Verification** (`finder/verify.py`) — syntax → MX record → Hunter's
verifier (if keyed) → SMTP probe (off by default; see below). Anything not
positively confirmed comes back `unknown`, never `valid`.

**5. Outreach** (`finder/outreach.py`) — three angles: ask for advice (highest
reply rate), ask about an internship directly, or a very short note. Tracks
`new → queued → contacted → replied` per contact.

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env     # optional — every key in it is optional
python app.py
```

The UI's source chips show green for whatever's configured. With no keys at all
you get OpenStreetMap + site crawling, which is enough to be useful.

### Terminal

```bash
python cli.py --industry mechanical_engineering --location "Boston, MA" \
              --max-employees 100 --companies 12 --min-seniority 3 --csv leads.csv
```

Results are saved to SQLite (`finder.db`) either way, so the web UI and CLI
share history. `GET /api/export.csv` exports everything.

### LinkedIn URL → email

The RocketReach flow you're used to is in the left sidebar and at
`POST /api/lookup/linkedin`. It hands the URL to RocketReach's API — we don't
fetch LinkedIn ourselves, because scraping it breaks their ToS and gets
accounts banned. Paste profile URLs you found by hand; that's fine.

## Reading the confidence numbers

| What you see | What it means |
|---|---|
| ~95%, "published on the company's own website" | We literally read this address off their site |
| 70-90%, "matches this company's known address format" | Built from a pattern proven by a real address at that domain |
| under 50%, "common format" | A guess from base rates. Expect roughly one in three to bounce |
| status `valid` | A mail server confirmed the mailbox |
| status `unknown` | Domain accepts mail; the mailbox itself is unconfirmed |
| status `risky` | Catch-all domain — it accepts everything, so acceptance proves nothing |

If a guessed address bounces, try the `alternates` on that contact before giving
up. A bounce is also information: it rules out a pattern.

## Rules this follows

- **robots.txt is honoured** on every company site (`RESPECT_ROBOTS=false` to
  override — don't).
- **One request per host per second** by default, with an identifying
  User-Agent. Set `CONTACT_EMAIL` so sites can reach you.
- **No LinkedIn scraping**, ever. API passthrough only.
- **SMTP probing is off by default.** Most cloud and campus networks block
  outbound port 25, and hammering mail servers with RCPT probes gets your IP
  blocklisted. Only enable it from a residential connection.
- **This is for you and your friends sending individual, personal emails.**
  Bulk-mailing scraped addresses is a different activity with different laws
  around it (CAN-SPAM, GDPR if you're mailing the EU). Ten thoughtful emails
  beat two hundred templated ones for this anyway.

## Tests

```bash
python -m unittest discover -s tests
```

49 tests, all offline. `test_finder.py` covers name/domain parsing, title
ranking, people extraction, email harvesting and pattern inference, size
filtering, drafting and storage. `test_integration.py` spins up a local fixture
site and runs the whole pipeline against it — verifying the crawler follows the
leadership link, obeys robots.txt, picks up the published address, learns
`{first}.{last}` from it, and applies it to the other person on the page.

**Not tested live:** this was developed in a sandbox whose network policy blocks
everything except package registries, so no real call to Nominatim, Overpass,
Apollo, Hunter, RocketReach or Google Places has been made. The request/response
shapes follow each provider's documented API, but expect to shake out small
issues on your first real run — those calls are isolated in
`finder/providers.py` and every one returns `(result, error)` rather than
throwing, so a broken provider degrades the search instead of killing it.

## Layout

```
app.py                  Flask UI + JSON API
cli.py                  command-line entry point
finder/
  config.py             env-driven settings
  web.py                polite HTTP: robots.txt, per-host rate limiting
  companies.py          discovery + merge + size filtering
  people.py             site crawl, name/title extraction
  emails.py             harvesting, pattern inference, candidate generation
  verify.py             syntax / MX / Hunter / optional SMTP
  providers.py          Hunter, Apollo, RocketReach, Google Places
  pipeline.py           orchestration + background jobs
  store.py              SQLite: searches, companies, contacts, outreach status
  outreach.py           email drafting
  industries.py         field taxonomy + seniority ladder
templates/index.html    single-page UI
tests/                  offline unit + integration tests
```

## Things worth adding next

- Bounce feedback: mark a guess dead and re-rank the alternates automatically.
- A "warm intro" pass — check whether anyone at the company shares your school.
- Gmail API send + reply detection, instead of `mailto:` and manual status.
- Per-domain crawl caching so re-running a search doesn't re-fetch every site.
