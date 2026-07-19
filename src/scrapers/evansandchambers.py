"""Evans & Chambers job board scraper.

Evans & Chambers uses ApplicantStack ATS at evanschambers.applicantstack.com
with a public job listing page. Jobs include title, department, location, and
detail pages contain full description with clearance requirements explicitly stated.
"""
import httpx
from bs4 import BeautifulSoup
import re


def fetch_jobs(identifier: str, client: httpx.Client) -> list[dict]:
    """Fetch jobs from Evans & Chambers careers portal.

    identifier: unused (URL is always evanschambers.applicantstack.com)
    Fetches all jobs from listing page (all shown at once).
    """
    base_url = "https://evanschambers.applicantstack.com/x/openings"

    try:
        resp = client.get(base_url, timeout=15)
        resp.raise_for_status()
    except Exception as e:
        raise

    soup = BeautifulSoup(resp.text, "html.parser")

    # Find the job results table
    data_table = soup.find("table", {"id": "data-table"})
    if not data_table:
        return []

    jobs = []
    rows = data_table.find_all("tr")[1:]  # Skip header row

    for row in rows:
        job = _parse_job_row(row, client)
        if job:
            jobs.append(job)

    return jobs


def _parse_job_row(row, client: httpx.Client) -> dict | None:
    """Extract job data from a table row."""
    try:
        # Find all cells
        cells = row.find_all("td")
        if len(cells) < 3:
            return None

        # Extract title and URL (first cell)
        title_link = cells[0].find("a")
        if not title_link:
            return None

        title = title_link.get_text(strip=True)
        job_url = title_link.get("href", "")

        if not job_url.startswith("http"):
            job_url = "https://evanschambers.applicantstack.com" + job_url

        # Extract department (second cell)
        department = cells[1].get_text(strip=True) if len(cells) > 1 else None

        # Extract location (third cell)
        location = cells[2].get_text(strip=True) if len(cells) > 2 else None

        # Extract external_id from URL
        external_id = re.search(r"/detail/([a-z0-9]+)", job_url)
        external_id = external_id.group(1) if external_id else title.lower().replace(" ", "-")

        # Fetch detail page for full description
        details = _fetch_job_details(job_url, client)

        return {
            "external_id": external_id,
            "title": title,
            "location": location,
            "url": job_url,
            "description_html": details.get("description_html"),
        }
    except Exception:
        return None


def _fetch_job_details(job_url: str, client: httpx.Client) -> dict:
    """Fetch and parse a job detail page."""
    details = {"description_html": ""}

    try:
        resp = client.get(job_url, timeout=15)
        resp.raise_for_status()
    except Exception:
        return details

    soup = BeautifulSoup(resp.text, "html.parser")

    # Find the job-post section
    job_post = soup.find("section", {"id": "job-post"})
    if job_post:
        # Get all paragraphs and structured content
        description_parts = []

        # Get title
        title_elem = job_post.find("h1")
        if title_elem:
            description_parts.append(title_elem.get_text(strip=True))

        # Get all paragraph text
        for p in job_post.find_all("p"):
            text = p.get_text(strip=True)
            if text:
                description_parts.append(text)

        if description_parts:
            full_desc = "\n".join(description_parts)
            details["description_html"] = full_desc[:8000]  # Truncate to 8000 chars

    return details
