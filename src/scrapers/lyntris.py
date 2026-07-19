"""Lyntris job board scraper.

Lyntris uses JobBoardHQ platform at lyntris.jobboardhq.com
with a public job search page. Jobs include location data and detail pages contain
full description with clearance requirements, salary, and employment details.
"""
import httpx
from bs4 import BeautifulSoup
import re
import json


def fetch_jobs(identifier: str, client: httpx.Client) -> list[dict]:
    """Fetch jobs from Lyntris careers portal.

    identifier: unused (URL is always lyntris.jobboardhq.com)
    Fetches all jobs from search page.
    """
    base_url = "https://lyntris.jobboardhq.com/search"

    try:
        resp = client.get(base_url, timeout=15)
        resp.raise_for_status()
    except Exception as e:
        raise

    soup = BeautifulSoup(resp.text, "html.parser")

    # Find the job list container
    job_list = soup.find("div", {"id": "jobList"})
    if not job_list:
        return []

    jobs = []

    # Find all job title links
    job_links = job_list.find_all("a", {"id": "lnkJobId"})

    for link in job_links:
        job = _parse_job_link(link, client)
        if job:
            jobs.append(job)

    return jobs


def _parse_job_link(link, client: httpx.Client) -> dict | None:
    """Extract job data from a job listing link and fetch detail page."""
    try:
        title = link.get_text(strip=True)
        job_url = link.get("href", "")

        if not job_url:
            return None

        if not job_url.startswith("http"):
            job_url = "https://lyntris.jobboardhq.com" + job_url

        # Extract external_id from URL (e.g., /job/vubkvb/...)
        external_id = re.search(r"/job/([a-z0-9]+)/", job_url)
        external_id = external_id.group(1) if external_id else title.lower().replace(" ", "-")

        # Extract location from URL (e.g., /falls-church/va)
        location_match = re.search(r"/([^/]+)/([a-z]{2})/?$", job_url)
        city = None
        state = None
        location = None
        if location_match:
            city = location_match.group(1).replace("-", " ").title()
            state = location_match.group(2).upper()
            location = f"{city}, {state}"
        elif job_url.endswith("/-/-"):
            # Some jobs have no location specified
            pass

        # Fetch detail page for full description and salary
        details = _fetch_job_details(job_url, client)

        return {
            "external_id": external_id,
            "title": title,
            "location": location,
            "city": city,
            "state": state,
            "url": job_url,
            "description_html": details.get("description_html"),
            "salary_min": details.get("salary_min"),
            "salary_max": details.get("salary_max"),
            "salary_currency": details.get("salary_currency"),
            "salary_interval": details.get("salary_interval"),
        }
    except Exception:
        return None


def _fetch_job_details(job_url: str, client: httpx.Client) -> dict:
    """Fetch and parse a job detail page."""
    details = {
        "description_html": "",
        "salary_min": None,
        "salary_max": None,
        "salary_currency": "USD",
        "salary_interval": "year",
    }

    try:
        resp = client.get(job_url, timeout=15)
        resp.raise_for_status()
    except Exception:
        return details

    soup = BeautifulSoup(resp.text, "html.parser")

    # Try to extract JSON-LD structured data first
    json_ld = soup.find("script", {"type": "application/ld+json"})
    if json_ld:
        try:
            data = json.loads(json_ld.string)
            if isinstance(data, dict):
                # Extract salary
                if "baseSalary" in data and isinstance(data["baseSalary"], dict):
                    salary_data = data["baseSalary"].get("value", {})
                    if isinstance(salary_data, dict):
                        details["salary_min"] = salary_data.get("minValue")
                        details["salary_max"] = salary_data.get("maxValue")
                        if isinstance(details["salary_min"], str):
                            details["salary_min"] = float(details["salary_min"])
                        if isinstance(details["salary_max"], str):
                            details["salary_max"] = float(details["salary_max"])

                # Extract description
                if "description" in data:
                    desc = data["description"]
                    details["description_html"] = desc[:8000]
        except (json.JSONDecodeError, TypeError):
            pass

    # Fallback: extract from HTML if JSON-LD didn't work
    if not details["description_html"]:
        desc_div = soup.find("div", {"id": "description"})
        if desc_div:
            # Get the description content
            desc_content = desc_div.find("span", {"id": "lblOutDescription"})
            if desc_content:
                text = desc_content.get_text(separator=" ", strip=True)
                details["description_html"] = text[:8000]

    return details
