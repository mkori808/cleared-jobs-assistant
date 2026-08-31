"""Eightfold.ai job board scraper (talentwidget / careers platform used by some large
enterprises, e.g. Microsoft).
List:   GET https://{tenant}.eightfold.ai/api/pcsx/search
        ?domain={domain}&query=&location=&start={offset}&sort_by=timestamp
        -- hard-capped at 10 results per page regardless of any "num"/"len" param, so a large
        tenant needs many sequential requests; data.count gives the true total to paginate to.
Detail: GET https://{tenant}.eightfold.ai/api/apply/v2/jobs/{id}?domain={domain}
        -- full HTML description in job_description, no auth required.

identifier: "tenant={subdomain};domain={domain}" (semicolon-separated, same convention as the
Workday/Taleo scrapers' identifiers), e.g. "tenant=microsoft;domain=microsoft.com" for
microsoft.eightfold.ai searching domain=microsoft.com.
"""
import httpx

from ._location import normalize_state, normalize_country

BASE = "https://{tenant}.eightfold.ai"
SEARCH_PATH = "/api/pcsx/search"
DETAIL_PATH = "/api/apply/v2/jobs/{job_id}"
PAGE_SIZE = 10


def _parse_identifier(identifier: str) -> dict:
    return dict(kv.split("=", 1) for kv in identifier.split(";") if "=" in kv)


def fetch_jobs(identifier: str, client: httpx.Client) -> list[dict]:
    cfg = _parse_identifier(identifier)
    tenant, domain = cfg["tenant"], cfg["domain"]
    base = BASE.format(tenant=tenant)

    postings = []
    offset = 0
    total = None
    while total is None or offset < total:
        resp = client.get(
            f"{base}{SEARCH_PATH}",
            params={"domain": domain, "query": "", "location": "", "start": offset, "sort_by": "timestamp"},
            timeout=20,
        )
        resp.raise_for_status()
        data = resp.json().get("data", {})
        page = data.get("positions", [])
        if not page:
            break
        postings.extend(page)
        total = data.get("count", offset + len(page))
        offset += PAGE_SIZE

    jobs = []
    for p in postings:
        job = _fetch_detail(p, base, domain, client)
        if job:
            jobs.append(job)
    return jobs


def _fetch_detail(p: dict, base: str, domain: str, client: httpx.Client) -> dict | None:
    job_id = p.get("id")
    title = p.get("name")
    if not job_id or not title:
        return None

    url = f"https://{base.split('//', 1)[1]}/careers/job/{job_id}"

    std_locations = p.get("standardizedLocations") or []
    city = state = country = None
    if std_locations:
        parts = [part.strip() for part in std_locations[0].split(",")]
        if len(parts) >= 3:
            city, state, country = parts[0], normalize_state(parts[1]), normalize_country(parts[-1])
        elif len(parts) == 2:
            city, country = parts[0], normalize_country(parts[-1])
        elif parts:
            city = parts[0]

    remote = (p.get("workLocationOption") or "").lower() == "remote"
    location = ", ".join(loc for loc in std_locations) or None

    description_html = ""
    try:
        d = client.get(f"{base}{DETAIL_PATH.format(job_id=job_id)}", params={"domain": domain}, timeout=15)
        if d.status_code == 200:
            description_html = d.json().get("job_description") or ""
    except Exception:
        pass

    return {
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
