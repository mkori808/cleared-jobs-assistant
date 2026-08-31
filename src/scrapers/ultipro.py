"""UKG Pro Recruiting (formerly UltiPro) job board scraper.
List:   POST https://recruiting.ultipro.com/{tenant}/JobBoard/{board}/JobBoardView/LoadSearchResults
Detail: GET  https://recruiting.ultipro.com/{tenant}/JobBoard/{board}/OpportunityDetail?opportunityId={id}
        -- the detail page embeds a `var opportunity = new X({...});` JS object with the full HTML
        description and structured salary fields, none of which the search endpoint provides.

identifier: "tenant={tenant};board={board-guid}" (semicolon-separated, same convention as the
Workday/Taleo scrapers' identifiers). Find these by opening the company's careers page network
tab and reading them off the LoadSearchResults request URL, e.g.
"tenant=APP1010ARAI;board=07442cec-d18e-4589-ab15-8342edc29af7" for
recruiting.ultipro.com/APP1010ARAI/JobBoard/07442cec-d18e-4589-ab15-8342edc29af7/...
"""
import json

import httpx

from ._location import normalize_state

BASE = "https://recruiting.ultipro.com"
LIST_PATH = "/{tenant}/JobBoard/{board}/JobBoardView/LoadSearchResults"
DETAIL_PATH = "/{tenant}/JobBoard/{board}/OpportunityDetail"


def _parse_identifier(identifier: str) -> dict:
    return dict(kv.split("=", 1) for kv in identifier.split(";") if "=" in kv)


def fetch_jobs(identifier: str, client: httpx.Client) -> list[dict]:
    cfg = _parse_identifier(identifier)
    tenant, board = cfg["tenant"], cfg["board"]

    list_url = f"{BASE}{LIST_PATH.format(tenant=tenant, board=board)}"
    resp = client.post(
        list_url,
        json={"opportunitySearch": {"Top": 1000, "Skip": 0, "QueryString": "", "Filters": []}},
        headers={"Content-Type": "application/json", "Accept": "application/json, text/plain, */*"},
        timeout=20,
    )
    resp.raise_for_status()
    postings = resp.json().get("opportunities", [])

    detail_url = f"{BASE}{DETAIL_PATH.format(tenant=tenant, board=board)}"
    jobs = []
    for p in postings:
        job = _fetch_detail(p, detail_url, client)
        if job:
            jobs.append(job)
    return jobs


def _fetch_detail(p: dict, detail_url: str, client: httpx.Client) -> dict | None:
    job_id = p.get("Id")
    title = p.get("Title")
    if not job_id or not title:
        return None

    url = f"{detail_url}?opportunityId={job_id}"
    locations = p.get("Locations") or []
    addr = (locations[0].get("Address") if locations else {}) or {}
    city = addr.get("City")
    state = normalize_state((addr.get("State") or {}).get("Code") or (addr.get("State") or {}).get("Name"))
    country = (addr.get("Country") or {}).get("Name")
    # JobLocationType is an undocumented int enum (0/1/2 seen, no label distinguishes remote from
    # onsite/hybrid in this data) -- no location name here ever says "Remote" either, so there's
    # no reliable signal to key off. Default False rather than guess at the enum's meaning.
    remote = False
    location = ", ".join(part for part in [city, state] if part) or None

    description_html = ""
    salary_min = salary_max = salary_currency = salary_interval = None
    try:
        d = client.get(url, timeout=15)
        if d.status_code == 200:
            data = _extract_opportunity_json(d.text)
            if data:
                description_html = data.get("Description") or ""
                if data.get("CompensationAnnualMinimum") is not None:
                    salary_min = data.get("CompensationAnnualMinimum")
                    salary_max = data.get("CompensationAnnualMaximum")
                    salary_interval = "year"
                elif data.get("CompensationHourlyMinimum") is not None:
                    salary_min = data.get("CompensationHourlyMinimum")
                    salary_max = data.get("CompensationHourlyMaximum")
                    salary_interval = "hour"
                if salary_min is not None:
                    salary_currency = data.get("CompensationCurrencyCode")
    except Exception:
        pass

    job = {
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
        job.update({
            "salary_min": salary_min,
            "salary_max": salary_max,
            "salary_currency": salary_currency,
            "salary_interval": salary_interval,
            "salary_source": "structured",
        })
    return job


def _extract_opportunity_json(html: str) -> dict | None:
    """The detail page has no JSON-LD; it embeds `var opportunity = new X({...});` instead.
    Braces inside the description HTML make a non-greedy regex match unreliable, so find the
    opening brace and let json.raw_decode walk to the matching close instead."""
    idx = html.find("var opportunity = new ")
    if idx == -1:
        return None
    paren = html.find("(", idx)
    brace = html.find("{", paren)
    if paren == -1 or brace == -1:
        return None
    try:
        data, _ = json.JSONDecoder().raw_decode(html, brace)
    except (ValueError, TypeError):
        return None
    return data
