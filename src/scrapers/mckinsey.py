"""McKinsey job API scraper.

McKinsey's careers API is publicly accessible at gateway.mckinsey.com and returns
complete job data including title, location, salary, and full description in JSON.
No authentication required.
"""
import re

import httpx


def fetch_jobs(identifier: str, client: httpx.Client) -> list[dict]:
    """Fetch jobs from McKinsey's public API.

    identifier: unused (McKinsey API URL is the same for all; kept for interface compatibility)
    """
    base_url = "https://gateway.mckinsey.com/apigw-x0cceuow60/v1/api/jobs/search"
    jobs = []
    page_size = 100  # Increased from 50 to fetch more per request
    start = 0  # Try 0-indexing first; adjust if needed

    while True:
        try:
            resp = client.get(
                base_url,
                params={"pageSize": page_size, "start": start, "lang": "en"},
                timeout=15,
            )
            resp.raise_for_status()
        except Exception as e:
            if start == 0:
                raise
            break

        data = resp.json()
        docs = data.get("docs", [])
        total = data.get("total")  # Check if API returns total count

        if not docs:
            break

        for doc in docs:
            job = _parse_job(doc)
            if job:
                jobs.append(job)

        # Log pagination info for debugging
        if total is not None:
            print(f"[mckinsey] Fetched {len(jobs)}/{total} jobs (page start={start})")

        # Check if we've reached the end
        if len(docs) < page_size:
            break

        start += page_size

    return jobs


def _parse_job(doc: dict) -> dict | None:
    """Extract job data from a McKinsey API job record."""
    try:
        job_id = doc.get("jobID")
        title = doc.get("title")
        if not job_id or not title:
            return None

        # Build URL from friendlyURL or jobApplyURL
        apply_url = doc.get("jobApplyURL", "")
        friendly_url = doc.get("friendlyURL", "")
        url = apply_url if apply_url else f"https://mckinsey.avature.net/careers/{friendly_url}"

        # Location: "cities"/"countries" are NOT this job's location -- they're a list of every
        # office McKinsey considers eligible for the role (often 100+ entries for globally
        # flexible postings), so cities[0] was picking an essentially arbitrary city. The
        # actual single posting location (when one exists) is linkedInPostingCity/Country.
        # Genuinely single-city postings (a 1-item cities/countries list) still resolve via that
        # list; roles open to many cities are left unstructured rather than guessing wrong.
        city = doc.get("linkedInPostingCity")
        country = doc.get("linkedInPostingCountry")
        cities = doc.get("cities") or []
        countries = doc.get("countries") or []
        if not city and len(cities) == 1:
            city = cities[0]
        if not country and len(countries) == 1:
            country = countries[0]

        # Description from multiple fields, plus salary/benefits text so the standard
        # downstream salary-range regex can pick it up like it does for every other scraper.
        description_parts = []
        for key in ["whatYouWillDo", "yourBackground", "whoYouWillWorkWith", "jobSalaryBenefits"]:
            text = doc.get(key, "")
            if text:
                clean = re.sub(r"<[^>]+>", " ", text)
                clean = re.sub(r"\s+", " ", clean).strip()
                description_parts.append(clean)
        description_html = "\n\n".join(description_parts)

        return {
            "external_id": job_id,
            "title": title,
            "location": ", ".join(filter(None, [city, country])),
            "city": city,
            "state": None,
            "country": country,
            "url": url,
            "description_html": description_html[:8000],  # Truncate to reasonable size
        }
    except Exception:
        return None
