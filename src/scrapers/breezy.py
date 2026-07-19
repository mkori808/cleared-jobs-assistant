"""Breezy HR job board scraper.
List:   GET https://{subdomain}.breezy.hr/json
Detail: GET https://{subdomain}.breezy.hr/p/{friendly_id} -- embeds a schema.org JobPosting
        as JSON-LD with full description text and structured baseSalary/jobLocation, none of
        which the list endpoint provides (it has location but no description or salary detail).

identifier: the company's breezy.hr subdomain, e.g. "matroid" for matroid.breezy.hr.
"""
import json

import httpx
from bs4 import BeautifulSoup

from ._location import normalize_state


def fetch_jobs(identifier: str, client: httpx.Client) -> list[dict]:
    resp = client.get(f"https://{identifier}.breezy.hr/json", timeout=20)
    resp.raise_for_status()
    postings = resp.json()

    jobs = []
    for p in postings:
        job = _parse_job(p, client)
        if job:
            jobs.append(job)
    return jobs


def _parse_job(p: dict, client: httpx.Client) -> dict | None:
    job_id = p.get("id")
    title = p.get("name")
    url = p.get("url")
    if not job_id or not title or not url:
        return None

    loc = p.get("location") or {}
    city = loc.get("city")
    state = normalize_state((loc.get("state") or {}).get("id") or (loc.get("state") or {}).get("name"))
    country = (loc.get("country") or {}).get("name")
    remote = bool(loc.get("is_remote"))

    description_html = ""
    salary_min = salary_max = salary_currency = salary_interval = None
    try:
        d = client.get(url, timeout=15)
        if d.status_code == 200:
            soup = BeautifulSoup(d.text, "html.parser")
            for script in soup.find_all("script", type="application/ld+json"):
                try:
                    data = json.loads(script.string)
                except (TypeError, ValueError):
                    continue
                if data.get("@type") != "JobPosting":
                    continue
                description_html = data.get("description", "")
                salary = (data.get("baseSalary") or {}).get("value") or {}
                if salary.get("minValue") is not None:
                    salary_min = salary.get("minValue")
                    salary_max = salary.get("maxValue")
                    salary_currency = (data.get("baseSalary") or {}).get("currency")
                    unit = (salary.get("unitText") or "").lower()
                    salary_interval = "year" if unit == "year" else ("hour" if unit == "hour" else None)
                break
    except Exception:
        pass

    job = {
        "external_id": job_id,
        "title": title.strip(),
        "location": loc.get("name"),
        "city": city,
        "state": state,
        "country": country,
        "remote": remote,
        "url": url,
        "description_html": description_html,
    }
    if salary_min is not None:
        job.update({
            "salary_min": salary_min,
            "salary_max": salary_max,
            "salary_currency": salary_currency,
            "salary_interval": salary_interval,
            "salary_source": "structured",
        })
    return job
