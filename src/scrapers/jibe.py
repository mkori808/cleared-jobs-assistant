"""Jibe-powered careers site scraper. Some companies front an iCIMS (or other) backend with a
Jibe-built JS SPA (e.g. VT Group's careers.vtgdefense.com), which is why the existing icims.py
scraper can't reach them -- the page iCIMS itself serves just redirects into the Jibe SPA, with
none of the server-rendered iCIMS_JobCardItem markup icims.py parses. Jibe's own API behind that
SPA is public and unauthenticated, and already inlines the full HTML description, so no separate
detail fetch is needed.

List+detail in one call:
  GET {base_url}/api/jobs?page={n}&sortBy=relevance&descending=false&internal=false

identifier: the company's Jibe-hosted careers site base URL, e.g.
"https://careers.vtgdefense.com" for VT Group.
"""
import httpx

from ._location import normalize_state, normalize_country


def fetch_jobs(base_url: str, client: httpx.Client) -> list[dict]:
    base_url = base_url.rstrip("/")
    jobs = []
    page = 1
    total = None
    while total is None or len(jobs) < total:
        resp = client.get(
            f"{base_url}/api/jobs",
            params={"page": page, "sortBy": "relevance", "descending": "false", "internal": "false"},
            timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()
        batch = data.get("jobs", [])
        if not batch:
            break
        for wrapper in batch:
            job = _parse_job(wrapper.get("data") or {})
            if job:
                jobs.append(job)
        total = data.get("totalCount", len(jobs))
        page += 1
    return jobs


def _parse_job(j: dict) -> dict | None:
    req_id = j.get("req_id")
    title = j.get("title")
    if not req_id or not title:
        return None

    city = j.get("city")
    state = normalize_state(j.get("state"))
    country = normalize_country(j.get("country"))
    location = j.get("full_location") or j.get("short_location")

    description_parts = [j.get("description") or "", j.get("qualifications") or "",
                          j.get("responsibilities") or ""]
    description_html = "\n".join(p for p in description_parts if p)

    return {
        "external_id": str(req_id),
        "title": title.strip(),
        "location": location,
        "city": city,
        "state": state,
        "country": country,
        # location_type is an opaque enum ("LAT_LNG" was the only value seen across VT Group's
        # full listing) with no observed remote-specific value -- no reliable signal to key off.
        "remote": False,
        "url": j.get("apply_url") or "",
        "description_html": description_html,
    }
