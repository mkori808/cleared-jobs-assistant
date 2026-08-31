"""Workable scraper for the newer jobs.workable.com company-page platform (distinct from the
classic apply.workable.com widget handled by workable.py -- some accounts only exist on this
newer platform, identified by an opaque company ID rather than a readable account slug).

List+detail in one call:
  GET https://jobs.workable.com/api/v1/companies/{company_id}
  -- returns the full job list with full HTML descriptions already inlined (no separate detail
  fetch needed, unlike workable.py's widget API).

identifier: the opaque company ID from the careers URL, e.g. "k8YJs7wVVAXbKwcrA6c2Qo" for
https://jobs.workable.com/company/k8YJs7wVVAXbKwcrA6c2Qo/jobs-at-....
"""
import httpx

from ._location import normalize_state, normalize_country

API_URL = "https://jobs.workable.com/api/v1/companies/{company_id}"


def fetch_jobs(company_id: str, client: httpx.Client) -> list[dict]:
    resp = client.get(API_URL.format(company_id=company_id), timeout=20)
    resp.raise_for_status()
    data = resp.json()

    jobs = []
    for j in data.get("jobs", []):
        job = _parse_job(j)
        if job:
            jobs.append(job)
    return jobs


def _parse_job(j: dict) -> dict | None:
    job_id = j.get("id")
    title = j.get("title")
    url = j.get("url")
    if not job_id or not title or not url:
        return None

    loc = j.get("location") or {}
    city = loc.get("city")
    state = normalize_state(loc.get("subregion"))
    country = normalize_country(loc.get("countryName"))
    remote = (j.get("workplace") or "").lower() == "remote"

    locations = j.get("locations") or []
    location = locations[0] if locations else None

    return {
        "external_id": job_id,
        "title": title.strip(),
        "location": location,
        "city": city,
        "state": state,
        "country": country,
        "remote": remote,
        "url": url,
        "description_html": j.get("description") or "",
    }
