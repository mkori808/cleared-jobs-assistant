"""Elder Research job board scraper.

Elder Research uses ClearCompany/HRMDirect ATS at elderresearch.hrmdirect.com
with a public job board listing. Jobs show title, department, city, state, and
detail pages contain full description including clearance requirements.
"""
import httpx
from bs4 import BeautifulSoup
import re


def fetch_jobs(identifier: str, client: httpx.Client) -> list[dict]:
    """Fetch jobs from Elder Research careers portal.

    identifier: unused (URL is always elderresearch.hrmdirect.com)
    Fetches all jobs from the listing page (pagination via JS, so we get all on first load).
    """
    base_url = "https://elderresearch.hrmdirect.com/employment/job-openings.php?search=true&"
    jobs = []

    try:
        resp = client.get(base_url, timeout=15)
        resp.raise_for_status()
    except Exception as e:
        raise

    soup = BeautifulSoup(resp.text, "html.parser")

    # Find the job results table
    results_table = soup.find("table", class_="reqResultTable")
    if not results_table:
        return []

    # Find all job rows (skip header row)
    rows = results_table.find_all("tr")[1:]  # Skip header row

    for row in rows:
        job = _parse_job_row(row, client)
        if job:
            jobs.append(job)

    return jobs


def _parse_job_row(row, client: httpx.Client) -> dict | None:
    """Extract job data from a table row."""
    try:
        # Extract department
        dept_cell = row.find("td", class_=re.compile(r"departments"))
        department = dept_cell.get_text(strip=True) if dept_cell else None

        # Extract job title and URL
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
            job_url = "https://elderresearch.hrmdirect.com/employment/" + job_url

        # Extract city and state
        city_cell = row.find("td", class_=re.compile(r"cities"))
        city = city_cell.get_text(strip=True) if city_cell else None

        state_cell = row.find("td", class_=re.compile(r"state"))
        state = state_cell.get_text(strip=True) if state_cell else None

        # Extract external_id from URL
        external_id = re.search(r"req=(\d+)", job_url)
        external_id = external_id.group(1) if external_id else title.lower().replace(" ", "-")

        # Fetch detail page for description
        details = _fetch_job_details(job_url, client)

        location = city
        if city and state:
            location = f"{city}, {state}"
        elif city:
            location = city

        return {
            "external_id": external_id,
            "title": title,
            "location": location,
            "city": city,
            "state": state,
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

    # Try to get description from meta tag first
    meta_desc = soup.find("meta", {"name": "description"})
    if meta_desc:
        description = meta_desc.get("content", "")
        if description:
            # Clean up the meta description (it has job info concatenated)
            # Format: "Job Title Description"
            # Find where the actual description starts
            parts = description.split("Position Overview")
            if len(parts) > 1:
                details["description_html"] = ("Position Overview" + parts[1])[:8000]
            else:
                details["description_html"] = description[:8000]
            return details

    # Fallback: try to find description in page content
    # Look for job description sections
    for selector in ["div.jobDescriptionContent", "div.jobContent", "div.description", "article"]:
        desc_elem = soup.select_one(selector)
        if desc_elem:
            text = desc_elem.get_text(separator=" ", strip=True)
            if len(text) > 100:
                details["description_html"] = text[:8000]
                return details

    return details
