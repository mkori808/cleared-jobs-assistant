# Clearance Job Tracker

Scrapes job listings from ~140 companies (mostly defense/AI/gov-tech contractors)
and flags security clearance, citizenship, and polygraph requirements. Refreshes
every 72 hours in the background and shows results in a local web dashboard.

## How it works

Career sites vary wildly, but most companies run on one of a handful of
applicant-tracking systems (ATS) that expose predictable public APIs:

- **Greenhouse** (`boards-api.greenhouse.io`)
- **Lever** (`api.lever.co`)
- **Ashby** (`api.ashbyhq.com`)
- **SmartRecruiters** (`api.smartrecruiters.com`)
- **Workday** (`*.myworkdayjobs.com`) — needs manual setup, see below
- **Rippling** (`ats.rippling.com`) — needs manual setup, see below (its board slugs
  often don't match the company name pattern, e.g. Accelint's board is
  `accelintjobboardtest`, not `accelint`)
- Anything else falls back to a **generic scraper** that best-effort-parses a
  careers page's HTML

On each refresh, the app:
1. For companies without a manual override, tries to guess their ATS slug
   (e.g. "Second Front Systems" → `secondfrontsystems`, `second-front-systems`,
   `sfs`) and probes each known ATS's public API until one resolves.
2. Fetches all current job postings for resolved companies.
3. Scans each job description for clearance-related language (TS/SCI, Secret,
   Public Trust, polygraph, US citizenship requirements) using keyword/regex
   matching — see `src/extractor.py` to tune these patterns.
4. Extracts salary, equity, and structured location (city/state/country/remote)
   where available — using each ATS's structured fields when it has them
   (Lever's `salaryRange`, Ashby's `compensation`/address fields, SmartRecruiters'
   location object), and falling back to regex-parsing the job description text
   otherwise (Greenhouse embeds pay-transparency ranges as text, not a field).
5. Stores everything in a local SQLite database (`data/tracker.db`).
6. Serves it all through a small FastAPI app + dashboard.

**Salary/equity coverage varies a lot by company.** Most employers still don't
publish pay ranges at all, so `salary` will legitimately be blank for a large
share of postings — that's not a bug, it's what's actually published. Where
present, the dashboard shows whether a figure came from a structured ATS field
or was parsed out of free text (`salary_source`).

**Realistic expectations:** the big well-known tech/AI companies (Anthropic,
Anduril, Scale AI, Applied Intuition, Cribl, Palantir, xAI, etc.) are likely on
Greenhouse, Lever, or Ashby and should mostly auto-resolve. Large primes and
consultancies (Boeing, Lockheed, Northrop, L3Harris, Leidos, Deloitte, EY,
McKinsey, IBM, Dell, etc.) almost always run **Workday** or fully custom
in-house systems, which can't be auto-discovered — you'll need to add a few
manual overrides for those (instructions below). Small/niche shops (Rackner,
Radiance Technologies, Toyon, STR, etc.) are a mixed bag — some run on smaller
ATS platforms like BambooHR/JazzHR/iCIMS that this version doesn't support yet.
The dashboard has an "unresolved companies" panel so you always know what still
needs attention, rather than silently missing data.

## Setup

```bash
cd clearance_tracker
pip install -r requirements.txt
```

## Running

```bash
uvicorn app:app --host 0.0.0.0 --port 8000
```

Then open **http://localhost:8000** in your browser.

- The first request starts an immediate refresh in the background (this can
  take a few minutes for ~140 companies — the dashboard will populate as data
  comes in if you refresh the page).
- After that, it automatically re-runs every 72 hours for as long as the
  process is running.
- Click **"Refresh now"** in the dashboard to trigger an out-of-cycle refresh.
- To run it as a proper background service (so it survives terminal/reboot),
  wrap the `uvicorn` command in a systemd service, a `screen`/`tmux` session,
  or a process manager like `pm2` / `supervisord`.

If you'd rather run a refresh once from the command line without starting the
web server:

```bash
python -m src.refresh
```

## Adding companies manually (Workday / custom sites)

Auto-discovery only works for Greenhouse/Lever/Ashby/SmartRecruiters. For
everything else, open `config/companies.json` and add an entry under
`"overrides"`.

**Workday** — find the tenant info from any job posting URL on the company's
careers site, e.g. `https://boeing.wd1.myworkdayjobs.com/en-US/EXTERNAL_CAREERS/job/.../R12345`:

```json
"overrides": {
  "Boeing": {"ats": "workday", "id": "host=wd1.myworkdayjobs.com;tenant=boeing;site=EXTERNAL_CAREERS"}
}
```

If you can't find a job URL to copy from, the `site` segment is easy to get wrong by
guessing (it's case-sensitive and varies a lot between companies — `External`, `Search`,
`Careers`, `EXTERNAL_CAREERS` have all been seen). Verify any guess by POSTing to
`https://{tenant}.{host}/wday/cxs/{tenant}/{site}/jobs` with body `{"limit":1,"offset":0}`
— a real site segment returns `{"total": N, ...}`; a wrong one returns a 404 `errorCode: "S21"`.

- `host` = the `wdN.myworkdayjobs.com` part
- `tenant` = the subdomain right before `.wdN`
- `site` = the path segment right after the tenant (before `/job/...`)

**Greenhouse/Lever/Ashby/SmartRecruiters** — if you already know the exact
slug (faster than waiting on auto-discovery), pin it directly:

```json
"overrides": {
  "Anthropic": {"ats": "greenhouse", "id": "anthropic"}
}
```

**Rippling** — find the board id from the company's embedded job board URL,
e.g. `https://ats.rippling.com/embed/accelintjobboardtest/jobs?s=...` →
the board id is the path segment right after `/embed/`:

```json
"overrides": {
  "Accelint": {"ats": "rippling", "id": "accelintjobboardtest"}
}
```

**Custom `careers.{domain}/api/jobs` sites** — some companies run a custom
careers subdomain that exposes a clean JSON API at `/api/jobs` (BigBear.ai is
one example; there may be others on this list using the same underlying
platform). If you find one, pin the base URL:

```json
"overrides": {
  "BigBear.ai": {"ats": "careers_widget", "id": "https://careers.bigbear.ai"}
}
```
To check if a company has one, try fetching `https://careers.{their-domain}/api/jobs`
directly in a browser or via curl — if it returns JSON with a `"jobs"` array,
it's a match. This is worth checking for any company whose careers page URL
looks like a `careers.` subdomain, since the API is usually one path segment
away from the page you'd browse normally.

**Fully custom career pages** — point the generic scraper at the careers URL.
It does a best-effort job of finding job links and scanning their text; for
messier sites you can pass a regex to narrow down which links count as job
postings:

```json
"overrides": {
  "SomeCompany": {"ats": "generic", "id": "https://example.com/careers", "link_pattern": "/careers/job/"}
}
```

(Note: the current generic scraper reads `id` as the careers URL; the
`link_pattern` field isn't yet wired through `refresh.py` — see "Known
limitations" below if you want to extend it.)

## Project structure

```
clearance_tracker/
├── app.py                  # FastAPI app + API routes
├── config/companies.json   # company list + manual ATS overrides
├── data/tracker.db         # SQLite DB (created on first run)
├── src/
│   ├── db.py                # storage layer
│   ├── discovery.py          # auto ATS/slug detection
│   ├── extractor.py          # clearance keyword extraction
│   ├── refresh.py            # orchestrates a full refresh cycle
│   ├── scheduler.py          # 72h background scheduler
│   └── scrapers/              # one module per ATS platform
│       ├── greenhouse.py
│       ├── lever.py
│       ├── ashby.py
│       ├── smartrecruiters.py
│       ├── workday.py
│       └── generic.py
└── static/                  # dashboard (HTML/CSS/JS, no build step)
```

## Known limitations / good next steps

- **Generic scraper is best-effort.** Sites with heavy client-side JS
  rendering (React/Vue career pages with no server-rendered HTML) won't yield
  job links from a plain HTTP GET. If you hit this a lot, swap in Playwright
  for those specific companies.
- **No iCIMS/BambooHR/JazzHR/SuccessFactors support yet** — several of the
  smaller companies on your list likely use these. Adding a module for one is
  the same pattern as `src/scrapers/lever.py`: a `fetch_jobs()` function that
  returns a list of `{external_id, title, location, url, description_html}`.
- **`link_pattern` override isn't wired through yet** for the generic scraper
  — `process_company()` in `refresh.py` would need to pass it through from the
  override dict.
- **Rate limiting/politeness**: this hits each company's public API/site once
  per refresh cycle, which is normal traffic, but if you shorten the interval
  a lot or add retries-on-failure, be mindful of hammering smaller companies'
  servers.
- I couldn't test any of this against live company websites while building it
  (this environment's network is sandboxed to package registries only) — the
  logic is tested against synthetic data, but real ATS responses occasionally
  have quirks the code doesn't handle yet. Watch the terminal output on your
  first refresh for per-company errors.
