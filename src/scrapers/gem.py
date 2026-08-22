"""Gem job board scraper. Public JSON API behind the jobs.gem.com-hosted career site.
List: GET https://api.gem.com/job_board/v0/{slug}/job_posts/ -- returns every posting in one
response, each with full HTML description inline (no separate detail-page fetch needed).

identifier: the board slug from the site's URL, e.g. "the-swift-group" for
jobs.gem.com/the-swift-group.

Location comes as a free-text "City, ST" string (offices[0].name) or, if a posting has no
office, "City, Country" (location.name) -- both handled by the shared parse_location() parser.
No structured salary field is exposed by this API.
"""
import httpx

from ._location import parse_location

API_URL = "https://api.gem.com/job_board/v0/{slug}/job_posts/"


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
    job_id = p.get("id")
    title = p.get("title")
    url = p.get("absolute_url")
    if not job_id or not title or not url:
        return None

    offices = p.get("offices") or []
    raw_location = offices[0].get("name") if offices else (p.get("location") or {}).get("name")
    loc = parse_location(raw_location or "")
    if p.get("location_type") == "remote":
        loc["remote"] = True

    return {
        "external_id": job_id,
        "title": title.strip(),
        "location": raw_location,
        "city": loc["city"],
        "state": loc["state"],
        "country": loc["country"],
        "remote": loc["remote"],
        "url": url,
        "description_html": p.get("content", ""),
    }
