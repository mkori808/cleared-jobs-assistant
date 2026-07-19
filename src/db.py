"""SQLite storage layer for the clearance tracker."""
import sqlite3
import json
import time
from pathlib import Path
from contextlib import contextmanager

from . import extractor

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "tracker.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS companies (
    name TEXT PRIMARY KEY,
    ats TEXT,               -- 'greenhouse' | 'lever' | 'ashby' | 'smartrecruiters' | 'workday' | 'generic' | NULL
    identifier TEXT,        -- slug / tenant info for the ATS, JSON-encoded if multiple fields needed
    careers_url TEXT,       -- used by the generic scraper / as a fallback link
    status TEXT DEFAULT 'pending',   -- 'pending' | 'resolved' | 'unresolved'
    last_checked REAL,
    last_error TEXT
);

CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,        -- company + external job id/url hash
    company TEXT NOT NULL,
    title TEXT NOT NULL,
    location TEXT,
    city TEXT,
    state TEXT,
    country TEXT,
    remote INTEGER DEFAULT 0,
    location_source TEXT,        -- 'structured' (from ATS field) | 'llm' | 'text' (regex-parsed) | NULL
    url TEXT,
    description_snippet TEXT,
    description_full TEXT,       -- full stripped description text, kept for the manual LLM fill-in-the-blanks pass
    clearance_level TEXT,        -- e.g. 'TS/SCI', 'Secret', 'Public Trust', 'None mentioned'
    clearance_keywords TEXT,     -- JSON list of matched phrases
    citizenship_required INTEGER DEFAULT 0,
    polygraph_mentioned INTEGER DEFAULT 0,
    salary_min REAL,
    salary_max REAL,
    salary_currency TEXT,
    salary_interval TEXT,        -- 'year' | 'hour' | etc.
    salary_source TEXT,          -- 'structured' (from ATS field) | 'text' (regex-parsed) | NULL
    equity_mentioned INTEGER DEFAULT 0,
    equity_text TEXT,
    resume_match_grade TEXT,     -- 'A'..'F', from the manual "score against resume" pass
    resume_match_rationale TEXT,
    extraction_version INTEGER DEFAULT 0,  -- see extractor.EXTRACTION_VERSION
    first_seen REAL,
    last_seen REAL,
    is_active INTEGER DEFAULT 1,
    FOREIGN KEY(company) REFERENCES companies(name)
);

CREATE TABLE IF NOT EXISTS refresh_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at REAL,
    finished_at REAL,
    companies_checked INTEGER,
    jobs_found INTEGER,
    jobs_new INTEGER,
    errors TEXT
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);

-- Employers found in federal award data that aren't in config/companies.json yet.
-- See src/candidates.py. Kept separate from `companies` because these are suggestions the
-- user hasn't accepted: nothing here is ever scraped until it's added to the config.
CREATE TABLE IF NOT EXISTS candidate_companies (
    name TEXT PRIMARY KEY,
    source TEXT,                     -- e.g. 'usaspending:Department of Defense'
    award_amount REAL,               -- obligated $ over the queried window; a rough
                                     -- proxy for size, NOT for cleared hiring volume
    first_seen REAL,
    last_seen REAL,
    dismissed INTEGER DEFAULT 0,     -- user rejected it; never show again
    added INTEGER DEFAULT 0          -- user accepted it into config/companies.json
);
"""


@contextmanager
def get_conn():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with get_conn() as conn:
        conn.executescript(SCHEMA)
        existing_cols = {row["name"] for row in conn.execute("PRAGMA table_info(jobs)")}
        if "description_full" not in existing_cols:
            conn.execute("ALTER TABLE jobs ADD COLUMN description_full TEXT")
        if "resume_match_grade" not in existing_cols:
            conn.execute("ALTER TABLE jobs ADD COLUMN resume_match_grade TEXT")
        if "resume_match_rationale" not in existing_cols:
            conn.execute("ALTER TABLE jobs ADD COLUMN resume_match_rationale TEXT")
        if "location_source" not in existing_cols:
            conn.execute("ALTER TABLE jobs ADD COLUMN location_source TEXT")
        if "applied_at" not in existing_cols:
            conn.execute("ALTER TABLE jobs ADD COLUMN applied_at REAL")
        if "application_status" not in existing_cols:
            conn.execute("ALTER TABLE jobs ADD COLUMN application_status TEXT")
        if "application_notes" not in existing_cols:
            conn.execute("ALTER TABLE jobs ADD COLUMN application_notes TEXT")
        if "extraction_version" not in existing_cols:
            conn.execute("ALTER TABLE jobs ADD COLUMN extraction_version INTEGER DEFAULT 0")


def upsert_company(name, ats=None, identifier=None, careers_url=None, status="pending", last_error=None):
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO companies (name, ats, identifier, careers_url, status, last_checked, last_error)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(name) DO UPDATE SET
                 ats=excluded.ats, identifier=excluded.identifier,
                 careers_url=COALESCE(excluded.careers_url, companies.careers_url),
                 status=excluded.status, last_checked=excluded.last_checked,
                 last_error=excluded.last_error""",
            (name, ats, identifier, careers_url, status, time.time(), last_error),
        )


def get_companies():
    with get_conn() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM companies ORDER BY name")]


def get_company(name):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM companies WHERE name = ?", (name,)).fetchone()
        return dict(row) if row else None


def get_job(job_id):
    """Single job lookup by id -- used during refresh to check whether a posting has already
    been seen (and thus already extracted) before deciding whether to spend an LLM call on it."""
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if not row:
        return None
    d = dict(row)
    d["clearance_keywords"] = json.loads(d["clearance_keywords"] or "[]")
    return d


def upsert_job(job):
    """job: dict with id, company, title, location, url, description_snippet,
    clearance_level, clearance_keywords (list), citizenship_required, polygraph_mentioned,
    city, state, country, remote, location_source, salary_min, salary_max, salary_currency,
    salary_interval, salary_source, equity_mentioned, equity_text, extraction_version"""
    now = time.time()
    with get_conn() as conn:
        existing = conn.execute("SELECT id, first_seen FROM jobs WHERE id = ?", (job["id"],)).fetchone()
        first_seen = existing["first_seen"] if existing else now
        conn.execute(
            """INSERT INTO jobs (id, company, title, location, city, state, country, remote,
                 location_source, url,
                 description_snippet, description_full, clearance_level, clearance_keywords, citizenship_required,
                 polygraph_mentioned, salary_min, salary_max, salary_currency, salary_interval,
                 salary_source, equity_mentioned, equity_text, extraction_version, first_seen, last_seen, is_active)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
               ON CONFLICT(id) DO UPDATE SET
                 title=excluded.title, location=excluded.location, city=excluded.city,
                 state=excluded.state, country=excluded.country, remote=excluded.remote,
                 location_source=excluded.location_source,
                 url=excluded.url, description_snippet=excluded.description_snippet,
                 description_full=excluded.description_full,
                 clearance_level=excluded.clearance_level,
                 clearance_keywords=excluded.clearance_keywords,
                 citizenship_required=excluded.citizenship_required,
                 polygraph_mentioned=excluded.polygraph_mentioned,
                 salary_min=excluded.salary_min, salary_max=excluded.salary_max,
                 salary_currency=excluded.salary_currency, salary_interval=excluded.salary_interval,
                 salary_source=excluded.salary_source,
                 equity_mentioned=excluded.equity_mentioned, equity_text=excluded.equity_text,
                 extraction_version=excluded.extraction_version,
                 last_seen=excluded.last_seen, is_active=1""",
            (job["id"], job["company"], job["title"], job.get("location"),
             job.get("city"), job.get("state"), job.get("country"), int(job.get("remote", False)),
             job.get("location_source"), job.get("url"), job.get("description_snippet"), job.get("description_full"),
             job.get("clearance_level"), json.dumps(job.get("clearance_keywords", [])),
             int(job.get("citizenship_required", False)), int(job.get("polygraph_mentioned", False)),
             job.get("salary_min"), job.get("salary_max"), job.get("salary_currency"),
             job.get("salary_interval"), job.get("salary_source"),
             int(job.get("equity_mentioned", False)), job.get("equity_text"),
             job.get("extraction_version", 0),
             first_seen, now),
        )
        return existing is None  # True if this is a newly-seen job


def get_jobs_for_llm_reparse(job_ids=None):
    """Active jobs with description text, for the manual "re-parse with AI" pass. Returns
    EVERY matching job regardless of what's currently stored -- this intentionally covers
    jobs the old regex pass already populated (possibly incorrectly), not just blank ones,
    since a wrong value looks the same as a right one until someone re-checks it. Pass job_ids
    to restrict to a specific set (e.g. the currently filtered view in the UI); an empty list
    means "nothing to process", not "no restriction"."""
    if job_ids is not None and not job_ids:
        return []
    query = """SELECT id, description_full, location, salary_min, salary_max, salary_currency,
                      salary_interval, salary_source, city, state, country, remote, location_source
               FROM jobs
               WHERE is_active = 1 AND description_full IS NOT NULL AND description_full != ''"""
    params = []
    if job_ids is not None:
        query += f" AND id IN ({','.join('?' for _ in job_ids)})"
        params.extend(job_ids)
    with get_conn() as conn:
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]


def apply_llm_fill(job_id, *, clearance_level, clearance_keywords, citizenship_required,
                    polygraph_mentioned, salary_min, salary_max, salary_currency,
                    salary_interval, salary_source, city, state, country, remote,
                    location_source, equity_mentioned, equity_text):
    """Overwrites every listed field for one job with freshly extracted values (from the LLM,
    or from existing DB values the caller wants preserved -- e.g. structured ATS-provided
    salary/location). All fields are required and unconditionally written, including None/False,
    so the caller must pass a complete, deliberate set of values rather than a partial patch."""
    with get_conn() as conn:
        conn.execute(
            """UPDATE jobs SET
                 clearance_level = ?, clearance_keywords = ?, citizenship_required = ?,
                 polygraph_mentioned = ?, salary_min = ?, salary_max = ?, salary_currency = ?,
                 salary_interval = ?, salary_source = ?, city = ?, state = ?, country = ?,
                 remote = ?, location_source = ?, equity_mentioned = ?, equity_text = ?,
                 extraction_version = ?
               WHERE id = ?""",
            (clearance_level, json.dumps(clearance_keywords), int(citizenship_required),
             int(polygraph_mentioned), salary_min, salary_max, salary_currency,
             salary_interval, salary_source, city, state, country,
             int(remote), location_source, int(equity_mentioned), equity_text,
             extractor.EXTRACTION_VERSION, job_id),
        )


def get_resume():
    with get_conn() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = 'resume_text'").fetchone()
        return row["value"] if row else None


def get_resume_years_of_experience():
    with get_conn() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = 'resume_years_of_experience'").fetchone()
        if row and row["value"]:
            try:
                return float(row["value"])
            except ValueError:
                return None
        return None


def set_resume(text, years_of_experience=None):
    """Saves the resume text and clears every job's existing match grade, since past grades
    were scored against the old resume and no longer mean anything."""
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES ('resume_text', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (text,),
        )
        if years_of_experience is not None:
            conn.execute(
                "INSERT INTO settings (key, value) VALUES ('resume_years_of_experience', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (str(years_of_experience),),
            )
        conn.execute("UPDATE jobs SET resume_match_grade = NULL, resume_match_rationale = NULL")


def get_jobs_needing_resume_score(job_ids=None):
    """Active jobs with description text but no resume match grade yet. Pass job_ids to
    restrict to a specific set (e.g. the currently filtered view in the UI) -- an empty
    list means "nothing to score", not "no restriction"."""
    if job_ids is not None and not job_ids:
        return []
    query = """SELECT id, description_full FROM jobs
               WHERE is_active = 1 AND description_full IS NOT NULL AND description_full != ''
                 AND resume_match_grade IS NULL"""
    params = []
    if job_ids is not None:
        query += f" AND id IN ({','.join('?' for _ in job_ids)})"
        params.extend(job_ids)
    with get_conn() as conn:
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]


def apply_resume_score(job_id, grade, rationale):
    with get_conn() as conn:
        conn.execute(
            "UPDATE jobs SET resume_match_grade = ?, resume_match_rationale = ? WHERE id = ?",
            (grade, rationale, job_id),
        )


def upsert_candidate_company(name, source=None, award_amount=None):
    """Records a discovered candidate. Preserves first_seen, and preserves the user's
    dismissed/added decisions -- a later discovery run must not resurrect something the
    user already rejected."""
    now = time.time()
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO candidate_companies (name, source, award_amount, first_seen, last_seen)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(name) DO UPDATE SET
                 source=excluded.source,
                 award_amount=MAX(COALESCE(candidate_companies.award_amount, 0), COALESCE(excluded.award_amount, 0)),
                 last_seen=excluded.last_seen""",
            (name, source, award_amount, now, now),
        )


def get_candidate_companies(include_dismissed=False, include_added=False):
    query = "SELECT * FROM candidate_companies WHERE 1=1"
    if not include_dismissed:
        query += " AND dismissed = 0"
    if not include_added:
        query += " AND added = 0"
    query += " ORDER BY award_amount DESC NULLS LAST, name"
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(query)]


def delete_candidate_company(name):
    with get_conn() as conn:
        conn.execute("DELETE FROM candidate_companies WHERE name = ?", (name,))


def all_candidate_company_names():
    """Every stored candidate regardless of dismissed/added state -- used to reconcile the
    stored list against companies.json, which changes underneath it."""
    with get_conn() as conn:
        return [r["name"] for r in conn.execute("SELECT name FROM candidate_companies")]


def dismiss_candidate_company(name):
    with get_conn() as conn:
        conn.execute("UPDATE candidate_companies SET dismissed = 1 WHERE name = ?", (name,))


def mark_candidate_added(name):
    with get_conn() as conn:
        conn.execute("UPDATE candidate_companies SET added = 1 WHERE name = ?", (name,))


def mark_inactive_jobs(company, seen_ids):
    """Flag jobs for a company that weren't seen in the latest refresh as inactive (likely closed)."""
    with get_conn() as conn:
        rows = conn.execute("SELECT id FROM jobs WHERE company = ? AND is_active = 1", (company,)).fetchall()
        for r in rows:
            if r["id"] not in seen_ids:
                conn.execute("UPDATE jobs SET is_active = 0 WHERE id = ?", (r["id"],))


def get_jobs(company=None, clearance_only=False, clearance_level=None, active_only=True,
             cities=None, states=None, countries=None, remote_only=False, include_remote=False,
             salary_min=None, salary_max=None, grades=None, equity_only=False,
             sort_by=None, sort_order="desc"):
    """cities/states/countries are each an optional list of values -- multiple values within
    one of them are OR'd together (any match), but the three dimensions are AND'd against each
    other (so picking City=Reston + State=CA correctly returns nothing, since no job is both).

    include_remote loosens that: when set alongside a city/state/country filter, it OR's in
    every remote job regardless of its own location, so e.g. State=CA + include_remote returns
    on-site CA jobs *and* all remote jobs, not just remote jobs that happen to also be tagged
    CA. It's a no-op with no location filters set (nothing to OR against). remote_only is the
    opposite kind of filter -- it narrows to *only* remote jobs, AND'd with everything else --
    and takes precedence if both are somehow set at once.

    salary_min/salary_max filter on the job's own posted range: salary_min keeps jobs whose
    top-of-range (salary_max) reaches at least that much, salary_max keeps jobs whose
    bottom-of-range (salary_min) doesn't exceed it. Jobs with no salary data are excluded once
    either bound is set, since there's no way to tell if they'd qualify.

    grades: optional list of resume_match_grade values (e.g. ["A", "B"]) -- OR'd together.

    sort_by: 'clearance_level', 'salary_max', 'resume_match_grade', or None (defaults to last_seen)
    sort_order: 'asc' or 'desc'
    """
    query = "SELECT * FROM jobs WHERE 1=1"
    params = []
    if company:
        query += " AND company = ?"
        params.append(company)
    if clearance_level:
        query += " AND clearance_level = ?"
        params.append(clearance_level)
    elif clearance_only:
        query += " AND clearance_level IS NOT NULL AND clearance_level != 'None mentioned'"
    if active_only:
        query += " AND is_active = 1"

    location_clauses = []
    location_params = []
    if cities:
        location_clauses.append(f"city IN ({','.join('?' for _ in cities)})")
        location_params.extend(cities)
    if states:
        location_clauses.append(f"state IN ({','.join('?' for _ in states)})")
        location_params.extend(states)
    if countries:
        location_clauses.append(f"country IN ({','.join('?' for _ in countries)})")
        location_params.extend(countries)
    if location_clauses:
        location_sql = " AND ".join(location_clauses)
        if include_remote:
            query += f" AND (({location_sql}) OR remote = 1)"
        else:
            query += f" AND {location_sql}"
        params.extend(location_params)

    if remote_only:
        query += " AND remote = 1"
    if salary_min is not None:
        query += " AND salary_max IS NOT NULL AND salary_max >= ?"
        params.append(salary_min)
    if salary_max is not None:
        query += " AND salary_min IS NOT NULL AND salary_min <= ?"
        params.append(salary_max)
    if grades:
        query += f" AND resume_match_grade IN ({','.join('?' for _ in grades)})"
        params.extend(grades)
    if equity_only:
        query += " AND equity_mentioned = 1"

    # Handle sorting. Blank cells always sort last regardless of direction: the CASE
    # expressions below rank real values 0..N and bucket anything else (NULL or an
    # unrecognized string) into one ELSE value higher than every real rank, so a leading
    # "is this the ELSE bucket" sort key (always ASC, real values 0 / blank 1) keeps blanks
    # at the end no matter which direction the rank itself is sorted in.
    order_dir = "DESC" if sort_order.lower() == "desc" else "ASC"
    if sort_by == "clearance_level":
        # Sort by clearance level: highest (most restrictive) first
        # Order: TS/SCI > Top Secret > Secret > Confidential > None mentioned > NULL
        clearance_order = "CASE clearance_level WHEN 'TS/SCI' THEN 0 WHEN 'Top Secret' THEN 1 WHEN 'Secret' THEN 2 WHEN 'Confidential' THEN 3 WHEN 'None mentioned' THEN 4 ELSE 5 END"
        rank_dir = "ASC" if order_dir == "DESC" else "DESC"
        query += f" ORDER BY (CASE WHEN ({clearance_order}) = 5 THEN 1 ELSE 0 END) ASC, {clearance_order} {rank_dir}, last_seen DESC"
    elif sort_by == "salary_max":
        # Sort by max salary (highest first by default) -- NULLS LAST already keeps blanks
        # last in both directions here, no CASE/bucket trick needed.
        query += f" ORDER BY salary_max {order_dir} NULLS LAST, last_seen DESC"
    elif sort_by == "resume_match_grade":
        # Sort by match grade: A > B > C > D > F > NULL
        match_order = "CASE resume_match_grade WHEN 'A' THEN 0 WHEN 'B' THEN 1 WHEN 'C' THEN 2 WHEN 'D' THEN 3 WHEN 'F' THEN 4 ELSE 5 END"
        rank_dir = "ASC" if order_dir == "DESC" else "DESC"
        query += f" ORDER BY (CASE WHEN ({match_order}) = 5 THEN 1 ELSE 0 END) ASC, {match_order} {rank_dir}, last_seen DESC"
    else:
        # Default: sort by last_seen
        query += " ORDER BY last_seen DESC"

    with get_conn() as conn:
        rows = [dict(r) for r in conn.execute(query, params)]
    for r in rows:
        r["clearance_keywords"] = json.loads(r["clearance_keywords"] or "[]")
    return rows


def get_locations():
    """Distinct city/state/country facets for populating the dashboard's location filters,
    each ordered by how many active jobs match it. Cities are capped to the top 60 by count
    since city cardinality can get large; state/country lists are naturally small."""
    with get_conn() as conn:
        cities = [dict(r) for r in conn.execute(
            """SELECT city AS value, COUNT(*) AS count FROM jobs
               WHERE is_active = 1 AND city IS NOT NULL AND city != ''
               GROUP BY city ORDER BY count DESC LIMIT 60""")]
        states = [dict(r) for r in conn.execute(
            """SELECT state AS value, COUNT(*) AS count FROM jobs
               WHERE is_active = 1 AND state IS NOT NULL AND state != ''
               GROUP BY state ORDER BY count DESC""")]
        countries = [dict(r) for r in conn.execute(
            """SELECT country AS value, COUNT(*) AS count FROM jobs
               WHERE is_active = 1 AND country IS NOT NULL AND country != ''
               GROUP BY country ORDER BY count DESC""")]
        remote_count = conn.execute(
            "SELECT COUNT(*) AS c FROM jobs WHERE is_active = 1 AND remote = 1"
        ).fetchone()["c"]
    return {"cities": cities, "states": states, "countries": countries, "remote_count": remote_count}


def log_refresh(started_at, finished_at, companies_checked, jobs_found, jobs_new, errors):
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO refresh_log (started_at, finished_at, companies_checked, jobs_found, jobs_new, errors)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (started_at, finished_at, companies_checked, jobs_found, jobs_new, json.dumps(errors)),
        )


def get_last_refresh():
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM refresh_log ORDER BY id DESC LIMIT 1").fetchone()
        return dict(row) if row else None


def mark_job_applied(job_id: str, status: str = "applied", notes: str = ""):
    """Mark a job as applied. status: 'applied', 'rejected', 'interview', 'offer', 'withdrawn', etc."""
    with get_conn() as conn:
        conn.execute(
            "UPDATE jobs SET applied_at = ?, application_status = ?, application_notes = ? WHERE id = ?",
            (time.time(), status, notes, job_id),
        )


def update_application_status(job_id: str, status: str, notes: str = ""):
    """Update the application status and notes for a job."""
    with get_conn() as conn:
        conn.execute(
            "UPDATE jobs SET application_status = ?, application_notes = ? WHERE id = ?",
            (status, notes, job_id),
        )


def get_applied_jobs():
    """Get all jobs that have been applied to, ordered by most recent first."""
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT * FROM jobs WHERE applied_at IS NOT NULL
               ORDER BY applied_at DESC""").fetchall()
        jobs = [dict(r) for r in rows]
    for j in jobs:
        j["clearance_keywords"] = json.loads(j["clearance_keywords"] or "[]")
    return jobs


def unmark_job_applied(job_id: str):
    """Remove applied status from a job."""
    with get_conn() as conn:
        conn.execute(
            "UPDATE jobs SET applied_at = NULL, application_status = NULL, application_notes = NULL WHERE id = ?",
            (job_id,),
        )
