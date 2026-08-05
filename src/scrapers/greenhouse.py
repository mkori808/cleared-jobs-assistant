"""Greenhouse job board scraper. Public API, no auth needed.
Board API docs pattern: https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true

Each job in the response includes a company_name field, which we cross-check
against the target company to guard against a guessed slug coincidentally
matching an unrelated real Greenhouse customer.
"""
import httpx
from ._matching import verify_board

API_URL = "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"


def probe(slug: str, client: httpx.Client, company_name: str | None = None,
          weak_slug: bool = False) -> bool:
    """Returns True if this slug resolves to a real Greenhouse board for the target company."""
    try:
        resp = client.get(API_URL.format(slug=slug), params={"content": "false"}, timeout=10)
        if resp.status_code != 200:
            return False
        jobs = resp.json().get("jobs")
        if jobs is None:
            return False
        # company_name is stored per-posting; empty board -> no name to confirm against.
        org_name = (jobs[0].get("company_name") or "").strip() if jobs else None
        return verify_board(company_name, org_name, weak_slug)
    except Exception:
        return False


def fetch_jobs(slug: str, client: httpx.Client) -> list[dict]:
    # pay_transparency=true asks Greenhouse to include any configured pay range
    # in the job's `content` HTML -- Greenhouse's public Job Board API does not
    # expose salary as a separate structured field, so it gets parsed out of
    # the description text downstream (see src/extractor.py extract_salary).
    resp = client.get(
        API_URL.format(slug=slug),
        params={"content": "true", "pay_transparency": "true"},
        timeout=20,
    )
    resp.raise_for_status()
    data = resp.json()
    jobs = []
    for j in data.get("jobs", []):
        location = (j.get("location") or {}).get("name")
        jobs.append({
            "external_id": str(j.get("id")),
            "title": j.get("title", "").strip(),
            "location": location,
            "url": j.get("absolute_url"),
            "description_html": j.get("content", ""),
        })
    return jobs
