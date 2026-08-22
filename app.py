"""FastAPI app: serves the dashboard UI + JSON API, and runs the 72h refresh scheduler."""
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI, Query
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
import threading

from src import db, scheduler, refresh, extractor, candidates

BASE_DIR = Path(__file__).resolve().parent

# In-memory progress for the manual "re-parse with AI" pass — deliberately not run as
# part of refresh/scheduling (and so never fires during tests); only a UI button starts it.
# Automatic refresh already runs the LLM as the primary extractor for newly-seen jobs (see
# refresh.process_company); this pass is for re-checking jobs already in the DB, since a
# wrong extracted value looks the same as a right one until someone re-derives it.
_reparse_lock = threading.Lock()
_reparse_status = {"running": False, "total": 0, "processed": 0, "updated": 0, "errors": 0}

# Same pattern for the manual "score against resume" pass.
_resume_score_lock = threading.Lock()
_resume_score_status = {"running": False, "total": 0, "processed": 0, "errors": 0}

# Same pattern for the manual "discover companies" pass (USAspending lookup).
_discover_lock = threading.Lock()
_discover_status = {"running": False, "candidates": 0, "error": None}


def _run_discovery():
    try:
        summary = candidates.discover(verbose=True)
        _discover_status["candidates"] = summary["candidates"]
    except Exception as e:  # never let a third-party API fault kill the worker silently
        _discover_status["error"] = str(e)
        print(f"[app] company discovery failed: {e}")
    finally:
        _discover_status["running"] = False


def _apply_reparse_result(job: dict, result: dict):
    """Writes one job's fresh LLM extraction to the DB, preserving structured ATS-provided
    salary/location rather than letting an LLM guess derived from text overwrite it."""
    if job.get("salary_source") == "structured":
        salary_min, salary_max = job["salary_min"], job["salary_max"]
        salary_currency, salary_interval, salary_source = job["salary_currency"], job["salary_interval"], "structured"
    else:
        salary_min, salary_max = result["salary_min"], result["salary_max"]
        salary_currency, salary_interval = result["salary_currency"], result["salary_interval"]
        salary_source = "llm" if salary_min is not None else None

    if job.get("location_source") == "structured":
        city, state, country, remote = job["city"], job["state"], job["country"], bool(job["remote"])
        location_source = "structured"
    else:
        city, state, country, remote = result["city"], result["state"], result["country"], result["remote"]
        location_source = "llm"

    db.apply_llm_fill(
        job["id"],
        clearance_level=result["clearance_level"],
        clearance_keywords=[result["clearance_snippet"]] if result["clearance_snippet"] else [],
        citizenship_required=result["citizenship_required"],
        polygraph_mentioned=result["polygraph_mentioned"],
        salary_min=salary_min, salary_max=salary_max,
        salary_currency=salary_currency, salary_interval=salary_interval, salary_source=salary_source,
        city=city, state=state, country=country, remote=remote, location_source=location_source,
        equity_mentioned=result["equity_mentioned"], equity_text=result["equity_text"],
    )


def _run_llm_reparse(job_ids=None):
    try:
        jobs = db.get_jobs_for_llm_reparse(job_ids=job_ids)
        _reparse_status["total"] = len(jobs)
        # Batch BATCH_SIZE jobs per LLM call rather than one call per job -- these are all
        # already sitting in the DB, so there's no reason not to read them together.
        for start in range(0, len(jobs), extractor.BATCH_SIZE):
            chunk = jobs[start:start + extractor.BATCH_SIZE]
            batch_input = [{"text": j["description_full"], "location_text": j.get("location")} for j in chunk]
            results = extractor.llm_extract_job_info_batch(batch_input)
            for offset, job in enumerate(chunk):
                result = results.get(offset)
                if not result:
                    _reparse_status["errors"] += 1
                else:
                    _apply_reparse_result(job, result)
                    _reparse_status["updated"] += 1
                _reparse_status["processed"] += 1
    finally:
        _reparse_status["running"] = False


def _run_resume_scoring(job_ids=None):
    try:
        resume_text = db.get_resume()
        jobs = db.get_jobs_needing_resume_score(job_ids=job_ids) if resume_text else []
        _resume_score_status["total"] = len(jobs)
        years_of_experience = db.get_resume_years_of_experience()
        # Batch SCORE_BATCH_SIZE jobs per LLM call rather than one call per job -- the resume is
        # identical for every job, so scoring one at a time re-sends it on every request.
        for start in range(0, len(jobs), extractor.SCORE_BATCH_SIZE):
            chunk = jobs[start:start + extractor.SCORE_BATCH_SIZE]
            results = extractor.llm_score_resume_match_batch(
                [j["description_full"] for j in chunk], resume_text, years_of_experience)
            for offset, job in enumerate(chunk):
                result = results.get(offset)
                if not result:
                    _resume_score_status["errors"] += 1
                else:
                    db.apply_resume_score(job["id"], result["grade"], result["rationale"])
                _resume_score_status["processed"] += 1
    finally:
        _resume_score_status["running"] = False


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Deliberately do NOT refresh on startup -- a full LLM extraction pass costs real money,
    # and auto-running it every time the app boots means every restart re-burns tokens. The
    # 72h background schedule still runs, and the "Refresh now" button triggers one on demand.
    db.init_db()
    scheduler.start_scheduler(run_immediately=False)
    yield


app = FastAPI(title="Clearance Job Tracker", lifespan=lifespan)
# /api/jobs ships the full filtered result set in one response (pagination/search are
# deliberately client-side for instant, no-round-trip interaction -- see static/app.js
# getVisibleJobs/renderJobs). That means payload size, not request count, is what makes it
# slow over a constrained link (e.g. WireGuard from off-network). JSON text compresses very
# well, so gzip is a transparent fix that doesn't touch that client-side design at all.
app.add_middleware(GZipMiddleware, minimum_size=1000)


@app.get("/api/jobs")
def api_jobs(company: str | None = None, clearance_only: bool = False, clearance_level: str | None = None,
             active_only: bool = True, cities: list[str] | None = Query(None),
             states: list[str] | None = Query(None), countries: list[str] | None = Query(None),
             remote_only: bool = False, include_remote: bool = False,
             salary_min: float | None = None, salary_max: float | None = None,
             grades: list[str] | None = Query(None), equity_only: bool = False,
             sort_by: str | None = None, sort_order: str = "desc"):
    return db.get_jobs(company=company, clearance_only=clearance_only, clearance_level=clearance_level,
                       active_only=active_only, cities=cities, states=states, countries=countries,
                       remote_only=remote_only, include_remote=include_remote, salary_min=salary_min,
                       salary_max=salary_max, grades=grades, equity_only=equity_only,
                       sort_by=sort_by, sort_order=sort_order)


@app.get("/api/locations")
def api_locations():
    return db.get_locations()


@app.get("/api/companies")
def api_companies():
    return db.get_companies()


@app.get("/api/status")
def api_status():
    return {"last_refresh": db.get_last_refresh()}


@app.post("/api/refresh")
def api_refresh():
    """Kick off a refresh in a background thread so the request returns immediately."""
    threading.Thread(target=refresh.run_refresh, kwargs={"verbose": True}, daemon=True).start()
    return {"status": "refresh started"}


class ReparseBody(BaseModel):
    job_ids: list[str] | None = None  # None = every active job; [] = nothing to do


@app.post("/api/reparse-jobs")
def api_reparse_jobs(body: ReparseBody = ReparseBody()):
    """Manually triggered: re-run LLM extraction (clearance/salary/location/equity) on active
    jobs, overwriting whatever's currently stored -- this re-checks jobs the old regex pass
    (or an earlier LLM pass) may have gotten wrong, not just ones with blank fields. Pass
    job_ids to restrict to a specific set (e.g. the UI's currently-filtered view). Never runs
    automatically — only this endpoint starts it."""
    with _reparse_lock:
        if _reparse_status["running"]:
            return JSONResponse({"status": "already running"}, status_code=409)
        _reparse_status.update({"running": True, "total": 0, "processed": 0, "updated": 0, "errors": 0})
    threading.Thread(target=_run_llm_reparse, kwargs={"job_ids": body.job_ids}, daemon=True).start()
    return {"status": "started"}


@app.get("/api/reparse-jobs/status")
def api_reparse_jobs_status():
    return _reparse_status


class ResumeBody(BaseModel):
    resume_text: str


@app.get("/api/resume")
def api_get_resume():
    return {"resume_text": db.get_resume() or ""}


@app.post("/api/resume")
def api_set_resume(body: ResumeBody):
    """Saves the resume text. This clears every job's existing match grade (they were scored
    against the old resume), so a re-score picks up all of them again."""
    yoe = extractor.extract_resume_years_of_experience(body.resume_text)
    db.set_resume(body.resume_text, yoe)
    return {"status": "saved", "years_of_experience": yoe}


class ScoreJobsBody(BaseModel):
    job_ids: list[str] | None = None  # None = every eligible active job; [] = nothing to do


@app.post("/api/score-jobs")
def api_score_jobs(body: ScoreJobsBody = ScoreJobsBody()):
    """Manually triggered: grade active jobs A-F against the saved resume. Pass job_ids to
    restrict to a specific set (e.g. the UI's currently-filtered view) instead of every active
    job. Never runs automatically — only this endpoint starts it."""
    if not db.get_resume():
        return JSONResponse({"status": "no resume saved"}, status_code=400)
    with _resume_score_lock:
        if _resume_score_status["running"]:
            return JSONResponse({"status": "already running"}, status_code=409)
        _resume_score_status.update({"running": True, "total": 0, "processed": 0, "errors": 0})
    threading.Thread(target=_run_resume_scoring, kwargs={"job_ids": body.job_ids}, daemon=True).start()
    return {"status": "started"}


@app.get("/api/score-jobs/status")
def api_score_jobs_status():
    return _resume_score_status


class MarkAppliedBody(BaseModel):
    job_ids: list[str]
    status: str = "applied"
    notes: str = ""


@app.post("/api/mark-applied")
def api_mark_applied(body: MarkAppliedBody):
    """Mark one or more jobs as applied."""
    for job_id in body.job_ids:
        db.mark_job_applied(job_id, body.status, body.notes)
    return {"status": "marked", "count": len(body.job_ids)}


class UpdateApplicationStatusBody(BaseModel):
    job_id: str
    status: str
    notes: str = ""


@app.post("/api/update-application-status")
def api_update_application_status(body: UpdateApplicationStatusBody):
    """Update the status of an application."""
    db.update_application_status(body.job_id, body.status, body.notes)
    return {"status": "updated"}


@app.post("/api/unmark-applied")
def api_unmark_applied(body: MarkAppliedBody):
    """Remove applied status from one or more jobs."""
    for job_id in body.job_ids:
        db.unmark_job_applied(job_id)
    return {"status": "unmarked", "count": len(body.job_ids)}


@app.get("/api/candidate-companies")
def api_candidate_companies():
    """Employers found in federal award data that aren't tracked yet (see src/candidates.py)."""
    return db.get_candidate_companies()


@app.post("/api/discover-companies")
def api_discover_companies():
    """Kick off a USAspending discovery pass in the background. Deliberately manual and
    separate from /api/refresh -- it hits a third-party API on a different cadence, and
    shouldn't be able to fail a job refresh."""
    with _discover_lock:
        if _discover_status["running"]:
            return JSONResponse({"status": "already running"}, status_code=409)
        _discover_status.update({"running": True, "candidates": 0, "error": None})
    threading.Thread(target=_run_discovery, daemon=True).start()
    return {"status": "started"}


@app.get("/api/discover-companies/status")
def api_discover_companies_status():
    return _discover_status


class CandidateBody(BaseModel):
    name: str


@app.post("/api/candidate-companies/add")
def api_add_candidate(body: CandidateBody):
    """Append a candidate to config/companies.json so the next refresh scrapes it."""
    added = candidates.add_to_tracker(body.name)
    return {"status": "added" if added else "already tracked"}


@app.post("/api/candidate-companies/dismiss")
def api_dismiss_candidate(body: CandidateBody):
    db.dismiss_candidate_company(body.name)
    return {"status": "dismissed"}


@app.get("/api/applied-jobs")
def api_applied_jobs():
    """Get all jobs marked as applied."""
    return db.get_applied_jobs()


# Serve the dashboard static files last, so /api/* routes above take priority.
app.mount("/", StaticFiles(directory=BASE_DIR / "static", html=True), name="static")
