"""ApplicantStack job board scraper.
List:   GET https://{subdomain}.applicantstack.com/x/openings -- server-rendered <table id="data-table">
        whose header row names its columns. Column set/order varies per tenant (e.g. some show
        "Department", others show "Clearance Required" in that slot), so location is found by
        matching the header text rather than assuming a fixed column position.
Detail: GET each job's listing link -- the full description sits in either <section id="job-post">
        or a <div class="listing_description"> depending on the tenant's ApplicantStack theme.

identifier: the company's applicantstack.com subdomain, e.g. "evanschambers" for
evanschambers.applicantstack.com or "compassinc" for compassinc.applicantstack.com.
"""
import re

import httpx
from bs4 import BeautifulSoup


def fetch_jobs(identifier: str, client: httpx.Client) -> list[dict]:
    base = f"https://{identifier}.applicantstack.com"
    resp = client.get(f"{base}/x/openings", timeout=15)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")
    table = soup.find("table", {"id": "data-table"})
    if not table:
        return []

    rows = table.find_all("tr")
    if len(rows) < 2:
        return []
    header_cells = [c.get_text(strip=True).lower() for c in rows[0].find_all(["th", "td"])]
    location_idx = header_cells.index("location") if "location" in header_cells else None

    jobs = []
    for row in rows[1:]:
        job = _parse_row(row, location_idx, base, client)
        if job:
            jobs.append(job)
    return jobs


def _parse_row(row, location_idx: int | None, base: str, client: httpx.Client) -> dict | None:
    cells = row.find_all("td")
    if not cells:
        return None

    title_link = cells[0].find("a")
    if not title_link:
        return None

    title = title_link.get_text(strip=True)
    job_url = title_link.get("href", "")
    if not job_url.startswith("http"):
        job_url = base + job_url

    location = None
    if location_idx is not None and location_idx < len(cells):
        location = cells[location_idx].get_text(strip=True)

    m = re.search(r"/detail/([a-z0-9]+)", job_url)
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
    job_post = soup.find("section", {"id": "job-post"}) or soup.find("div", class_="listing_description")
    if not job_post:
        return ""

    parts = []
    title_elem = job_post.find("h1")
    if title_elem:
        parts.append(title_elem.get_text(strip=True))
    paragraphs = job_post.find_all("p")
    if paragraphs:
        for p in paragraphs:
            text = p.get_text(strip=True)
            if text:
                parts.append(text)
    else:
        parts.append(job_post.get_text(separator="\n", strip=True))
    return "\n".join(parts)[:8000]
