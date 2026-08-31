"""ClearCompany "HRMDirect" job board scraper.
List:   GET https://{subdomain}.hrmdirect.com/employment/job-openings.php?search=true& --
        server-rendered <table class="reqResultTable">. Column set/order varies per tenant
        (e.g. separate City/State columns vs. a single combined "Job Location" column), so
        location is found by matching header text rather than a fixed column position; the
        title cell is always <td class="posTitle">, so that one's found directly regardless
        of position.
Detail: GET each job's listing link -- description text usually lives in the page's meta
        description tag (format "<title> <description>", cut at "Position Overview" when
        present), with a DOM fallback for tenants that don't populate that meta tag.

identifier: the company's hrmdirect.com subdomain, e.g. "elderresearch" or "btscommercial".
"""
import re

import httpx
from bs4 import BeautifulSoup

LOCATION_HEADERS = {"city", "cities", "state", "job location", "location"}


def fetch_jobs(identifier: str, client: httpx.Client) -> list[dict]:
    base = f"https://{identifier}.hrmdirect.com"
    resp = client.get(f"{base}/employment/job-openings.php", params={"search": "true"}, timeout=15)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")
    table = soup.find("table", class_="reqResultTable")
    if not table:
        return []

    rows = table.find_all("tr")
    if len(rows) < 2:
        return []
    header_cells = [c.get_text(strip=True).lower() for c in rows[0].find_all(["th", "td"])]
    location_idxs = [i for i, h in enumerate(header_cells) if h in LOCATION_HEADERS]

    jobs = []
    for row in rows[1:]:
        job = _parse_row(row, location_idxs, base, client)
        if job:
            jobs.append(job)
    return jobs


def _parse_row(row, location_idxs: list[int], base: str, client: httpx.Client) -> dict | None:
    title_cell = row.find("td", class_=re.compile(r"posTitle"))
    if not title_cell:
        return None
    title_link = title_cell.find("a")
    if not title_link:
        return None

    title = title_link.get_text(strip=True)
    job_url = title_link.get("href", "")
    if not job_url:
        return None
    if not job_url.startswith("http"):
        job_url = base + "/employment/" + job_url.lstrip("/")

    cells = row.find_all("td")
    location_parts = [cells[i].get_text(strip=True) for i in location_idxs if i < len(cells)]
    location = ", ".join(p for p in location_parts if p) or None

    m = re.search(r"req=(\d+)", job_url)
    external_id = m.group(1) if m else title.lower().replace(" ", "-")

    return {
        "external_id": external_id,
        "title": title,
        "location": location,
        "url": job_url,
        "description_html": _fetch_description(job_url, client),
    }


def _fetch_description(job_url: str, client: httpx.Client) -> str:
    try:
        resp = client.get(job_url, timeout=15)
        resp.raise_for_status()
    except Exception:
        return ""

    soup = BeautifulSoup(resp.text, "html.parser")

    meta_desc = soup.find("meta", {"name": "description"})
    if meta_desc and meta_desc.get("content"):
        description = meta_desc["content"]
        parts = description.split("Position Overview")
        return ("Position Overview" + parts[1])[:8000] if len(parts) > 1 else description[:8000]

    for selector in ["div.jobDescriptionContent", "div.jobContent", "div.description", "article"]:
        desc_elem = soup.select_one(selector)
        if desc_elem:
            text = desc_elem.get_text(separator=" ", strip=True)
            if len(text) > 100:
                return text[:8000]
    return ""
