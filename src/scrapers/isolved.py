"""isolved Hire (isolvedhire.com) job board scraper -- also covers ApplicantPro
(applicantpro.com), which turns out to share the exact same "core/jobs/{siteId}" backend API
and response shape (confirmed directly: same field names, same getParams-driven list endpoint).
List:   GET https://{subdomain}.{domain}/core/jobs/{site_id}?getParams={} -- returns the full
        job list with structured location and salary, but no description text.
Detail: GET each job's jobUrl -- a schema.org JobPosting JSON-LD block on the page has the full
        HTML description.

identifier: "subdomain={subdomain};siteId={site_id};domain={domain}" (semicolon-separated, same
convention as the Workday/Taleo scrapers' identifiers) -- domain defaults to isolvedhire.com if
omitted, e.g. "subdomain=s5analytics;siteId=9941" for s5analytics.isolvedhire.com, or
"subdomain=oneidatechnicalsolutions;siteId=9280;domain=applicantpro.com" for
oneidatechnicalsolutions.applicantpro.com.
"""
import json
import re

import httpx

from ._location import normalize_state

LIST_URL = "https://{subdomain}.{domain}/core/jobs/{site_id}"


def _parse_identifier(identifier: str) -> dict:
    return dict(kv.split("=", 1) for kv in identifier.split(";") if "=" in kv)


def fetch_jobs(identifier: str, client: httpx.Client) -> list[dict]:
    cfg = _parse_identifier(identifier)
    resp = client.get(
        LIST_URL.format(subdomain=cfg["subdomain"], site_id=cfg["siteId"],
                         domain=cfg.get("domain", "isolvedhire.com")),
        params={"getParams": "{}"}, timeout=20,
    )
    resp.raise_for_status()
    postings = resp.json().get("data", {}).get("jobs", [])

    jobs = []
    for p in postings:
        job = _parse_job(p, client)
        if job:
            jobs.append(job)
    return jobs


def _parse_job(p: dict, client: httpx.Client) -> dict | None:
    job_id = p.get("id")
    title = p.get("title")
    url = p.get("jobUrl")
    if not job_id or not title or not url:
        return None

    city = p.get("city")
    state = normalize_state(p.get("abbreviation"))
    country = "United States" if p.get("iso3") == "USA" else p.get("iso3")

    description_html = ""
    try:
        d = client.get(url, timeout=15)
        if d.status_code == 200:
            m = re.search(r'<script type="application/ld\+json">(.*?)</script>', d.text, re.S)
            if m:
                data = json.loads(m.group(1), strict=False)
                if data.get("@type") == "JobPosting":
                    description_html = data.get("description") or ""
    except Exception:
        pass

    job = {
        "external_id": str(job_id),
        "title": title.strip(),
        "location": p.get("jobLocation"),
        "city": city,
        "state": state,
        "country": country,
        "remote": (p.get("workplaceType") or "").lower() == "remote",
        "url": url,
        "description_html": description_html,
    }

    min_salary = _parse_money(p.get("minSalary"))
    max_salary = _parse_money(p.get("maxSalary"))
    if min_salary is not None:
        freq = (p.get("payTypeFrame") or "").lower()
        job.update({
            "salary_min": min_salary,
            "salary_max": max_salary if max_salary is not None else min_salary,
            "salary_currency": "USD",
            "salary_interval": "year" if "year" in freq else ("hour" if "hour" in freq else None),
            "salary_source": "structured",
        })
    return job


def _parse_money(raw: str | None) -> float | None:
    if not raw:
        return None
    try:
        return float(raw.replace(",", ""))
    except ValueError:
        return None
