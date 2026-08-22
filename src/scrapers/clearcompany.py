"""ClearCompany job board scraper. Public JSON API, no auth required.
List: GET https://app.clearcompany.com/api/v2/jobs/{slug}/json -- returns every posting in one
response with full HTML description inline.

identifier: the company's ClearCompany slug, e.g. "orbisoperations" for Orbis Operations
(app.clearcompany.com/api/v2/jobs/orbisoperations/json). Found either from a company-branded
subdomain (https://{slug}.clearcompany.com) or a legacy hrmdirect.com alias some older tenants
still expose.

The structured City/CountrySubdivisionName fields are inconsistently populated (often null even
when OfficeName has a real "City, ST" value), so location is parsed from the free-text
OfficeName via the shared parse_location() parser instead of trusting the structured fields.
No salary field is exposed by this API.
"""
import httpx

from ._location import parse_location

API_URL = "https://app.clearcompany.com/api/v2/jobs/{slug}/json"


def fetch_jobs(slug: str, client: httpx.Client) -> list[dict]:
    resp = client.get(API_URL.format(slug=slug), timeout=20)
    resp.raise_for_status()
    postings = resp.json()

    jobs = []
    for p in postings:
        job = _parse_job(p)
        if job:
            jobs.append(job)
    return jobs


def _parse_job(p: dict) -> dict | None:
    job_id = p.get("Id")
    title = p.get("PositionTitle")
    url = p.get("ApplyUrl")
    if not job_id or not title or not url:
        return None

    raw_location = p.get("OfficeName")
    loc = parse_location(raw_location or "")

    return {
        "external_id": str(job_id),
        "title": title.strip(),
        "location": raw_location,
        "city": loc["city"],
        "state": loc["state"],
        "country": loc["country"],
        "remote": loc["remote"],
        "url": url,
        "description_html": p.get("Description", ""),
    }
