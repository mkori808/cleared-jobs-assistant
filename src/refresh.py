"""Orchestrates a full refresh cycle: for each company, resolve its ATS (using a
manual override if present, else cached discovery, else fresh discovery), fetch
jobs, run clearance extraction, and upsert into the database.
"""
import json
import time
import hashlib
import threading
from pathlib import Path

import httpx

from . import db, discovery, extractor
from .scrapers import (
    greenhouse, lever, ashby, smartrecruiters, workday, rippling, careers_widget, generic,
    bamboohr, epsilon_systems, workable, icims, mckinsey, goliath, deloitte, ey, elderresearch,
    evansandchambers, lyntris, endgame, teamtailor, pinpoint, adp, taleo, breezy,
)
from .scrapers._location import parse_location, normalize_state, normalize_country, extract_leaked_state

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "companies.json"

FETCHERS = {
    "greenhouse": greenhouse.fetch_jobs,
    "lever": lever.fetch_jobs,
    "ashby": ashby.fetch_jobs,
    "smartrecruiters": smartrecruiters.fetch_jobs,
    "workday": workday.fetch_jobs,
    "rippling": rippling.fetch_jobs,
    "careers_widget": careers_widget.fetch_jobs,
    "bamboohr": bamboohr.fetch_jobs,
    "epsilon_systems": epsilon_systems.fetch_jobs,
    "workable": workable.fetch_jobs,
    "icims": icims.fetch_jobs,
    "mckinsey": mckinsey.fetch_jobs,
    "goliath": goliath.fetch_jobs,
    "deloitte": deloitte.fetch_jobs,
    "ey": ey.fetch_jobs,
    "elderresearch": elderresearch.fetch_jobs,
    "evansandchambers": evansandchambers.fetch_jobs,
    "lyntris": lyntris.fetch_jobs,
    "endgame": endgame.fetch_jobs,
    "teamtailor": teamtailor.fetch_jobs,
    "pinpoint": pinpoint.fetch_jobs,
    "adp": adp.fetch_jobs,
    "taleo": taleo.fetch_jobs,
    "breezy": breezy.fetch_jobs,
}

HEADERS = {"User-Agent": "Mozilla/5.0 (clearance-job-tracker/1.0; personal use)"}


def load_config():
    with open(CONFIG_PATH) as f:
        return json.load(f)


def make_job_id(company: str, external_id: str) -> str:
    raw = f"{company}:{external_id}"
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


def resolve_company(name: str, override: dict | None, client: httpx.Client) -> dict:
    """Returns {"ats": ..., "identifier": ..., "status": ...}"""
    if override:
        # An override with no "ats" (e.g. {"ats": null}) pins a company as known-unresolved --
        # for cases where auto-discovery keeps landing on the wrong board (a slug collision)
        # or the company's real ATS isn't one we support. This takes priority over any cached
        # "resolved" row, so it self-corrects on the next refresh without a manual DB edit.
        if not override.get("ats"):
            return {"ats": None, "identifier": None, "status": "unresolved"}
        return {"ats": override["ats"], "identifier": override["id"], "status": "resolved"}

    cached = db.get_company(name)
    if cached and cached.get("status") == "resolved" and cached.get("ats"):
        return {"ats": cached["ats"], "identifier": cached["identifier"], "status": "resolved"}

    found = discovery.discover(name, client)
    if found:
        return {"ats": found["ats"], "identifier": found["identifier"], "status": "resolved"}

    return {"ats": None, "identifier": None, "status": "unresolved"}


def _clean_country_state(city, state, country, remote, location_source):
    """Normalizes country spelling (US/USA/United States -> one canonical value) and, when a
    scraper or the LLM has mistakenly put a US state into the country slot (a real, observed
    failure mode -- e.g. 'CA (Hybrid)' as a 'country'), promotes it into state instead."""
    state = normalize_state(state)
    leaked_state = extract_leaked_state(country)
    if leaked_state:
        country = "United States"
        if not state:
            state = leaked_state
    else:
        country = normalize_country(country)
    return {"city": city, "state": state, "country": country, "remote": remote,
            "location_source": location_source}


def _apply_llm_result(rj: dict, llm_result: dict, has_structured_loc: bool, has_structured_salary: bool):
    """Builds (signals, loc, salary, equity) from a successful LLM extraction, applying the
    same structured-data precedence as the regex fallback below."""
    signals = {
        "clearance_level": llm_result["clearance_level"],
        "clearance_keywords": [llm_result["clearance_snippet"]] if llm_result["clearance_snippet"] else [],
        "citizenship_required": llm_result["citizenship_required"],
        "polygraph_mentioned": llm_result["polygraph_mentioned"],
    }
    equity = {"equity_mentioned": llm_result["equity_mentioned"], "equity_text": llm_result["equity_text"]}
    if has_structured_loc:
        loc = _clean_country_state(rj.get("city"), rj.get("state"), rj.get("country"),
                                    bool(rj.get("remote")), "structured")
    else:
        loc = _clean_country_state(llm_result["city"], llm_result["state"], llm_result["country"],
                                    llm_result["remote"], "llm")
    if has_structured_salary:
        salary = {
            "salary_min": rj.get("salary_min"), "salary_max": rj.get("salary_max"),
            "salary_currency": rj.get("salary_currency"), "salary_interval": rj.get("salary_interval"),
            "salary_source": "structured",
        }
    else:
        salary = {
            "salary_min": llm_result["salary_min"], "salary_max": llm_result["salary_max"],
            "salary_currency": llm_result["salary_currency"], "salary_interval": llm_result["salary_interval"],
            "salary_source": "llm" if llm_result["salary_min"] is not None else None,
        }
    return signals, loc, salary, equity


def _apply_regex_fallback(rj: dict, text: str, has_structured_loc: bool, has_structured_salary: bool):
    """Same shape as _apply_llm_result, but derived with regex -- used only when the LLM call
    (single or batched) failed outright, so a hiccup doesn't leave a job with no data at all."""
    signals = extractor.extract(text)
    equity = extractor.extract_equity(text)
    if has_structured_loc:
        loc = _clean_country_state(rj.get("city"), rj.get("state"), rj.get("country"),
                                    bool(rj.get("remote")), "structured")
    else:
        loc = parse_location(rj.get("location") or "")
        if rj.get("workplace_type") == "remote":
            loc["remote"] = True
        loc["location_source"] = "text"
    if has_structured_salary:
        salary = {
            "salary_min": rj.get("salary_min"), "salary_max": rj.get("salary_max"),
            "salary_currency": rj.get("salary_currency"), "salary_interval": rj.get("salary_interval"),
            "salary_source": "structured",
        }
    else:
        parsed_salary = extractor.extract_salary(text)
        salary = {**parsed_salary, "salary_source": "text" if parsed_salary["salary_min"] else None}
    return signals, loc, salary, equity


def process_company(name: str, resolved: dict, client: httpx.Client) -> tuple[int, int, str | None]:
    """Fetch + store jobs for one company. Returns (jobs_found, jobs_new, error)."""
    ats = resolved["ats"]
    identifier = resolved["identifier"]
    if not ats:
        return 0, 0, "unresolved (no matching ATS found — add a manual override)"

    fetch_fn = FETCHERS.get(ats)
    try:
        if ats == "generic":
            raw_jobs = generic.fetch_jobs(identifier, client)
        else:
            raw_jobs = fetch_fn(identifier, client)
    except Exception as e:
        return 0, 0, f"fetch failed: {e}"

    # First pass: figure out each posting's job_id/text/existing status, without extracting
    # anything yet.
    prepared = []
    for rj in raw_jobs:
        text = extractor.strip_html(rj.get("description_html", ""))
        job_id = make_job_id(name, rj.get("external_id") or rj.get("url") or rj.get("title"))
        prepared.append({
            "rj": rj, "text": text, "job_id": job_id, "existing": db.get_job(job_id),
            "has_structured_loc": any(rj.get(k) is not None for k in ("city", "state", "country", "remote")),
            "has_structured_salary": rj.get("salary_source") == "structured",
        })

    seen_ids = []
    new_count = 0
    for i, p in enumerate(prepared):
        rj, text, job_id, existing = p["rj"], p["text"], p["job_id"], p["existing"]
        seen_ids.append(job_id)

        # A version mismatch means the regex/scraper logic that produced this row's signals has
        # since changed (see extractor.EXTRACTION_VERSION) -- force re-extraction even though
        # the description text itself is unchanged, so a bug fix actually reaches jobs that were
        # already scraped before it landed instead of silently staying wrong until someone
        # remembers to run a one-off backfill. Rows from the manual "Re-parse with AI" pass
        # (location_source == "llm") are unconditionally exempt from *both* checks below --
        # not just the version check -- so neither a version bump nor incidental text drift
        # (e.g. a "posted N days ago" string ticking over) can make a regex re-extraction
        # silently clobber deliberately LLM-curated data.
        is_llm_curated = bool(existing) and existing.get("location_source") == "llm"
        version_current = bool(existing) and existing.get("extraction_version") == extractor.EXTRACTION_VERSION
        text_unchanged = not text or (bool(existing) and existing.get("description_full") == text)
        if existing and (is_llm_curated or (version_current and text_unchanged)):
            # Already extracted from this exact text (with current-version logic) on a prior
            # refresh -- reuse those fields rather than redo the same work every cycle.
            # If the description text has changed (e.g. a scraper fix pulled in real content
            # where it previously stored an empty/partial description), fall through to
            # re-extract below instead of keeping stale signals. And if THIS run simply didn't
            # fetch a description at all (e.g. a detail-fetch coverage cap on large job boards --
            # `text` empty), keep whatever was already extracted rather than re-running
            # extraction against nothing and wiping out a previously-good result.
            signals = {
                "clearance_level": existing["clearance_level"],
                "clearance_keywords": existing["clearance_keywords"],
                "citizenship_required": bool(existing["citizenship_required"]),
                "polygraph_mentioned": bool(existing["polygraph_mentioned"]),
            }
            loc = {"city": existing["city"], "state": existing["state"],
                   "country": existing["country"], "remote": bool(existing["remote"]),
                   "location_source": existing["location_source"]}
            salary = {"salary_min": existing["salary_min"], "salary_max": existing["salary_max"],
                      "salary_currency": existing["salary_currency"], "salary_interval": existing["salary_interval"],
                      "salary_source": existing["salary_source"]}
            equity = {"equity_mentioned": bool(existing["equity_mentioned"]), "equity_text": existing["equity_text"]}
        else:
            # Use regex extraction for new jobs during refresh (cheap, fast). The manual
            # "Re-parse with AI" button uses LLM for already-stored jobs if you want higher
            # accuracy. This keeps refreshes at ~$0 cost while still offering LLM as an option.
            signals, loc, salary, equity = _apply_regex_fallback(
                rj, text, p["has_structured_loc"], p["has_structured_salary"])

        # Some scrapers only fetch full descriptions for a subset of jobs per run (e.g. Workday's
        # detail-page cap on large boards) -- on those runs `text` is empty for the rest even
        # though a prior refresh may have already stored a real description for that same job.
        # Don't let a coverage gap silently blank out description data that's still accurate;
        # keep the last real description on file instead of overwriting it with nothing.
        stored_text = text if text else (existing.get("description_full") if existing else text)

        is_new = db.upsert_job({
            "id": job_id,
            "company": name,
            "title": rj.get("title"),
            "location": rj.get("location"),
            "url": rj.get("url"),
            "description_snippet": (stored_text or "")[:500],
            "description_full": stored_text,
            "clearance_level": signals["clearance_level"],
            "clearance_keywords": signals["clearance_keywords"],
            "citizenship_required": signals["citizenship_required"],
            "polygraph_mentioned": signals["polygraph_mentioned"],
            "extraction_version": extractor.EXTRACTION_VERSION,
            **loc,
            **salary,
            **equity,
        })
        if is_new:
            new_count += 1

    db.mark_inactive_jobs(name, seen_ids)
    return len(raw_jobs), new_count, None


# Guards against two full refreshes running at once (e.g. the startup refresh overlapping a
# manual "Refresh now" click, or rapid double-clicks). Without this, concurrent runs both see
# the same jobs as not-yet-stored and both spend LLM calls extracting every one of them --
# paying twice (or more) for identical work. A refresh already in flight is a no-op.
_refresh_lock = threading.Lock()


def run_refresh(companies: list[str] = None, verbose: bool = True) -> dict:
    if not _refresh_lock.acquire(blocking=False):
        if verbose:
            print("[refresh] skipped -- a refresh is already running")
        return {"skipped": "a refresh is already running"}
    try:
        return _run_refresh(companies, verbose)
    finally:
        _refresh_lock.release()


def _run_refresh(companies: list[str] = None, verbose: bool = True) -> dict:
    db.init_db()
    config = load_config()
    overrides = config.get("overrides", {})
    all_companies = sorted(companies or config["companies"], reverse=True)

    started_at = time.time()
    total_jobs = 0
    total_new = 0
    errors = {}

    with httpx.Client(headers=HEADERS, timeout=30) as client:
        for name in all_companies:
            if verbose:
                print(f"[{name}] Starting refresh...")
            try:
                override = overrides.get(name)
                resolved = resolve_company(name, override, client)
                db.upsert_company(
                    name, ats=resolved["ats"], identifier=resolved["identifier"],
                    status=resolved["status"],
                )
                found, new, err = process_company(name, resolved, client)
                total_jobs += found
                total_new += new
                if err:
                    errors[name] = err
                    db.upsert_company(name, ats=resolved["ats"], identifier=resolved["identifier"],
                                       status=resolved["status"], last_error=err)
                if verbose:
                    status = f"{found} jobs ({new} new)" if not err else f"ERROR: {err}"
                    print(f"[{name}] ats={resolved['ats']} -> {status}")
            except Exception as e:
                errors[name] = f"Refresh failed: {e}"
                if verbose:
                    print(f"[{name}] FAILED: {e}")

    finished_at = time.time()
    db.log_refresh(started_at, finished_at, len(all_companies), total_jobs, total_new, errors)
    return {
        "companies_checked": len(all_companies),
        "jobs_found": total_jobs,
        "jobs_new": total_new,
        "duration_sec": round(finished_at - started_at, 1),
        "errors": errors,
    }


if __name__ == "__main__":
    result = run_refresh()
    print("\n--- Refresh complete ---")
    print(json.dumps(result, indent=2))
