"""Teamtailor job board scraper.

Teamtailor-powered careers sites (e.g. careers.lloydslistintelligence.com) are JS single-page
apps, so the raw HTML has no job content -- but Teamtailor publishes a standard JSON Feed
(https://jsonfeed.org) at {domain}/jobs.json with full descriptions and structured
schema.org JobPosting data (including location), which we use instead.

Usage: pass identifier as the site's hostname, e.g. "careers.lloydslistintelligence.com".
"""
import httpx


def fetch_jobs(identifier: str, client: httpx.Client) -> list[dict]:
    feed_url = f"https://{identifier}/jobs.json"
    resp = client.get(feed_url, timeout=20)
    resp.raise_for_status()
    data = resp.json()

    jobs = []
    for item in data.get("items", []):
        job = _parse_item(item)
        if job:
            jobs.append(job)
    return jobs


def _parse_item(item: dict) -> dict | None:
    title = item.get("title")
    url = item.get("url")
    if not title or not url:
        return None

    posting = item.get("_jobposting", {})
    external_id = str(posting.get("identifier", {}).get("value") or item.get("id") or url)

    city = country = None
    job_locations = posting.get("jobLocation") or []
    if job_locations:
        addr = job_locations[0].get("address", {})
        city = addr.get("addressLocality")
        country = addr.get("addressCountry")
    location = ", ".join(p for p in (city, country) if p) or None

    return {
        "external_id": external_id,
        "title": title,
        "location": location,
        "url": url,
        "description_html": item.get("content_html", ""),
        "city": city,
        "state": None,
        "country": country,
        "remote": False,
    }
