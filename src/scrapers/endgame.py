"""Endgame Systems job API scraper.

Endgame Systems exposes jobs via a JSON API at elasticgov.com/api/fetchJobs.
"""
import httpx
import json


def fetch_jobs(identifier: str, client: httpx.Client) -> list[dict]:
    """Fetch jobs from Endgame Systems' JSON API.

    identifier: unused (URL is hardcoded)
    """
    base_url = "https://www.elasticgov.com/api/fetchJobs"

    try:
        resp = client.get(base_url, timeout=15)
        resp.raise_for_status()
    except Exception as e:
        raise

    try:
        data = resp.json()
    except json.JSONDecodeError:
        return []

    jobs = []

    # Handle different possible response formats
    job_list = data if isinstance(data, list) else data.get("jobs", [])

    for job in job_list:
        parsed = _parse_job(job)
        if parsed:
            jobs.append(parsed)

    return jobs


def _parse_job(job: dict) -> dict | None:
    """Extract job data from an Endgame Systems API response."""
    try:
        job_id = job.get("id") or job.get("jobId")
        title = job.get("title") or job.get("jobTitle")

        if not job_id or not title:
            return None

        url = job.get("url") or job.get("jobUrl") or f"https://www.elasticgov.com/careers"
        location = job.get("location") or job.get("city")
        description = job.get("description") or job.get("jobDescription") or ""

        return {
            "external_id": str(job_id),
            "title": title,
            "location": location,
            "url": url,
            "description_html": description[:8000],
        }
    except Exception:
        return None
