"""Workable careers scraper. Public unauthenticated JSON API behind Workable's
embeddable job widget:
  GET https://apply.workable.com/api/v1/widget/accounts/{account}?details=true

Without `details=true` the response omits `description` entirely, so it's
always included here. Locations come back as a list (a job can span several);
we use the first one for city/state/country, matching the single-location
shape the rest of this app expects.
"""
import httpx
from ._matching import verify_board

API_URL = "https://apply.workable.com/api/v1/widget/accounts/{account}"


def probe(account: str, client: httpx.Client, company_name: str | None = None,
          weak_slug: bool = False) -> bool:
    try:
        resp = client.get(API_URL.format(account=account), timeout=10)
        if resp.status_code != 200:
            return False
        data = resp.json()
        # An unknown account still returns 200 with no "jobs" key, so require it explicitly
        # rather than just checking for a non-empty list -- a company with zero current
        # openings is a valid (if temporarily empty) match, an unknown account is not.
        if "jobs" not in data:
            return False
        return verify_board(company_name, data.get("name"), weak_slug)
    except Exception:
        return False


def fetch_jobs(account: str, client: httpx.Client) -> list[dict]:
    resp = client.get(API_URL.format(account=account), params={"details": "true"}, timeout=20)
    resp.raise_for_status()
    data = resp.json()

    jobs = []
    for j in data.get("jobs", []):
        locations = j.get("locations") or []
        loc = locations[0] if locations else {}
        city = loc.get("city") or j.get("city") or None
        state = loc.get("region") or j.get("state") or None
        country = loc.get("country") or j.get("country") or None

        jobs.append({
            "external_id": j.get("shortcode"),
            "title": (j.get("title") or "").strip(),
            "location": ", ".join(filter(None, [city, state, country])) or None,
            "city": city,
            "state": state,
            "country": country,
            "remote": bool(j.get("telecommuting")),
            "url": j.get("shortlink") or j.get("url"),
            "description_html": j.get("description") or "",
        })
    return jobs
