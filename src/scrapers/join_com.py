"""Join.com job board scraper.
List:   GET https://join.com/companies/{slug} -- job list lives in the __NEXT_DATA__ script tag
        at props.pageProps.initialState.jobs.items, but each item only has city/country, no
        state or description.
Detail: GET https://join.com/companies/{slug}/{idParam} -- same __NEXT_DATA__ structure, but
        props.pageProps.initialState.job carries the full markdown description plus a region
        name (state), which the list page omits.

identifier: the company's join.com slug, e.g. "smacktechnologiescom" for
join.com/companies/smacktechnologiescom.
"""
import json

import httpx
from bs4 import BeautifulSoup

from ._location import normalize_state

BASE = "https://join.com/companies/{slug}"


def _next_data(html: str) -> dict | None:
    soup = BeautifulSoup(html, "html.parser")
    tag = soup.find("script", id="__NEXT_DATA__")
    if not tag or not tag.string:
        return None
    try:
        return json.loads(tag.string)
    except (TypeError, ValueError):
        return None


def fetch_jobs(slug: str, client: httpx.Client) -> list[dict]:
    resp = client.get(BASE.format(slug=slug), timeout=20)
    resp.raise_for_status()
    data = _next_data(resp.text)
    if not data:
        return []
    postings = (
        data.get("props", {}).get("pageProps", {}).get("initialState", {}).get("jobs", {}).get("items", [])
    )

    jobs = []
    for p in postings:
        job = _fetch_detail(p, slug, client)
        if job:
            jobs.append(job)
    return jobs


def _fetch_detail(p: dict, slug: str, client: httpx.Client) -> dict | None:
    job_id = p.get("id")
    id_param = p.get("idParam")
    title = p.get("title")
    if not job_id or not id_param or not title:
        return None

    url = f"{BASE.format(slug=slug)}/{id_param}"

    city = state = country = None
    remote = False
    description_html = ""
    salary_min = salary_max = salary_currency = salary_interval = None

    try:
        d = client.get(url, timeout=15)
        if d.status_code == 200:
            data = _next_data(d.text)
            job = ((data or {}).get("props", {}).get("pageProps", {})
                   .get("initialState", {}).get("job"))
            if job:
                c = job.get("city") or {}
                city = c.get("cityName")
                state = normalize_state(c.get("regionName"))
                country = c.get("countryName")
                remote = (job.get("workplaceType") or "").upper() == "REMOTE"
                description_html = job.get("description") or ""

                sf = job.get("salaryAmountFrom") or {}
                st = job.get("salaryAmountTo") or {}
                if sf.get("amount") is not None:
                    # Amounts are in minor units (cents) -- 14000000 == $140,000.00.
                    salary_min = sf["amount"] / 100
                    salary_max = st.get("amount") / 100 if st.get("amount") is not None else None
                    salary_currency = sf.get("currency")
                    freq = (job.get("salaryFrequency") or "").upper()
                    salary_interval = "year" if freq == "PER_YEAR" else ("hour" if freq == "PER_HOUR" else None)
    except Exception:
        pass

    location = ", ".join(part for part in [city, state] if part) or None

    job_out = {
        "external_id": str(job_id),
        "title": title.strip(),
        "location": location,
        "city": city,
        "state": state,
        "country": country,
        "remote": remote,
        "url": url,
        "description_html": description_html,
    }
    if salary_min is not None:
        job_out.update({
            "salary_min": salary_min,
            "salary_max": salary_max,
            "salary_currency": salary_currency,
            "salary_interval": salary_interval,
            "salary_source": "structured",
        })
    return job_out
