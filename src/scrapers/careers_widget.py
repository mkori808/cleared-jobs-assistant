"""Scraper for a career-site platform pattern seen at careers.bigbear.ai and
likely other companies: a custom `careers.{domain}` subdomain exposing
GET {base_url}/api/jobs -> {"jobs": [{"data": {...}}]}.

This wraps an underlying ATS (BigBear.ai's is iCIMS, per the `ats_code` field)
but exposes a much richer, more convenient JSON shape directly -- notably:
  - tags1: a direct clearance tag, e.g. "TS/SCI with Poly", "TS/SCI with FSP", "N/A"
  - tags2: work arrangement, e.g. "Onsite", "Remote", "Hybrid"
  - city / state / country: already split out (state is sometimes a full name
    like "Maryland" rather than an abbreviation -- normalized via _location.py)
  - salary_min_value / salary_max_value: structured salary when published
    (0 when not published, which is most of the time)

The exact JSON shape may not be identical across every company using this
platform, so fields are read defensively with .get() fallbacks.
"""
import httpx
from ._matching import names_match

JOBS_PATH = "/api/jobs"


def _job_dict(item: dict) -> dict:
    return item.get("data", item)


def probe(base_url: str, client: httpx.Client, company_name: str | None = None) -> bool:
    try:
        resp = client.get(base_url.rstrip("/") + JOBS_PATH, timeout=10)
        if resp.status_code != 200:
            return False
        data = resp.json()
        jobs = data.get("jobs") or []
        if not jobs:
            return False
        if company_name:
            org = _job_dict(jobs[0]).get("hiring_organization", "")
            if org and not names_match(company_name, org):
                return False
        return True
    except Exception:
        return False


def fetch_jobs(base_url: str, client: httpx.Client) -> list[dict]:
    resp = client.get(base_url.rstrip("/") + JOBS_PATH, timeout=20)
    resp.raise_for_status()
    data = resp.json()
    jobs = []
    for item in data.get("jobs", []):
        d = _job_dict(item)

        clearance_tag = d.get("tags1") or []
        clearance_tag_text = " ".join(t for t in clearance_tag if t and t.upper() != "N/A")

        work_type = d.get("tags2") or []
        remote = any("remote" in t.lower() for t in work_type)

        salary_min = d.get("salary_min_value") or None
        salary_max = d.get("salary_max_value") or None
        salary_source = "structured" if (salary_min or salary_max) else None

        description_html = " ".join(filter(None, [
            d.get("description", ""), d.get("qualifications", ""),
            d.get("responsibilities", ""), clearance_tag_text,
        ]))

        jobs.append({
            "external_id": d.get("slug") or d.get("req_id"),
            "title": (d.get("title") or "").strip(),
            "location": d.get("location_name") or d.get("full_location"),
            "city": d.get("city"),
            "state": d.get("state"),
            "country": d.get("country"),
            "remote": remote,
            "url": d.get("apply_url") or d.get("canonical_url"),
            "description_html": description_html,
            "salary_min": salary_min,
            "salary_max": salary_max,
            "salary_currency": "USD" if salary_source else None,
            "salary_interval": "year" if salary_source else None,
            "salary_source": salary_source,
        })
    return jobs
