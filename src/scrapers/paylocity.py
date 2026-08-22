"""Paylocity Recruiting job board scraper.

List:   GET https://recruiting.paylocity.com/Recruiting/Jobs/All/{cid} -- a React SPA, but the
        job list is still embedded server-side in the initial HTML as a `window.pageData.Jobs`
        JS object, so no separate API call is needed. Each entry's own `Description` field is a
        fixed ~110-char snippet cut off mid-sentence, not the full text.
Detail: GET https://recruiting.paylocity.com/Recruiting/Jobs/Details/{JobId} -- embeds a
        schema.org JobPosting JSON-LD block with the full description and, when the employer
        discloses one, a structured baseSalary (min/max/currency/unit -- YEAR and HOUR both
        seen).

identifier: the company's Paylocity "cid" (a UUID), e.g. "b67e2cb0-ade6-4297-9cdc-1b9801037631"
for Athenix Solutions Group -- found in the recruiting.paylocity.com/Recruiting/Jobs/All/{cid}
URL linked from the company's own careers page.
"""
import json
import re

import httpx

from ._location import normalize_state, normalize_country

LIST_URL = "https://recruiting.paylocity.com/Recruiting/Jobs/All/{cid}"
DETAIL_URL = "https://recruiting.paylocity.com/Recruiting/Jobs/Details/{job_id}"

_PAGE_DATA_RE = re.compile(r"window\.pageData\s*=\s*(\{.*?\});", re.S)
_JSONLD_RE = re.compile(r'<script type="application/ld\+json">(.*?)</script>', re.S)


def fetch_jobs(cid: str, client: httpx.Client) -> list[dict]:
    resp = client.get(LIST_URL.format(cid=cid), timeout=20)
    resp.raise_for_status()
    m = _PAGE_DATA_RE.search(resp.text)
    if not m:
        return []
    data = json.loads(m.group(1))

    jobs = []
    for item in data.get("Jobs", []):
        job = _parse_job(item, client)
        if job:
            jobs.append(job)
    return jobs


def _parse_job(item: dict, client: httpx.Client) -> dict | None:
    job_id = item.get("JobId")
    title = item.get("JobTitle")
    if not job_id or not title:
        return None

    loc = item.get("JobLocation") or {}
    city = loc.get("City")
    state = normalize_state(loc.get("State"))
    country = normalize_country(loc.get("Country")) if loc.get("Country") else None
    remote = bool(item.get("IsRemote"))
    url = DETAIL_URL.format(job_id=job_id)

    description_html = ""
    salary_min = salary_max = salary_currency = salary_interval = None
    try:
        d = client.get(url, timeout=15)
        if d.status_code == 200:
            for ld_match in _JSONLD_RE.finditer(d.text):
                try:
                    posting = json.loads(ld_match.group(1))
                except (TypeError, ValueError):
                    continue
                if posting.get("@type") != "JobPosting":
                    continue
                description_html = posting.get("description") or ""
                salary = posting.get("baseSalary") or {}
                value = salary.get("value") or {}
                if value.get("minValue") is not None:
                    salary_min = value.get("minValue")
                    salary_max = value.get("maxValue")
                    salary_currency = salary.get("currency")
                    unit = (value.get("unitText") or "").lower()
                    salary_interval = "year" if unit == "year" else ("hour" if unit == "hour" else None)
                break
    except Exception:
        pass

    job = {
        "external_id": str(job_id),
        "title": title.strip(),
        "location": item.get("LocationName"),
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
