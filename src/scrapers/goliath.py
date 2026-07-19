"""Goliath Partners job board scraper.

Goliath Partners hosts job listings on their custom OctoberCMS job board
at goliathpartners.com/jobs. Jobs include location, salary range (GBP), and type.
"""
import httpx
from bs4 import BeautifulSoup


def fetch_jobs(identifier: str, client: httpx.Client) -> list[dict]:
    """Fetch jobs from Goliath Partners.

    identifier: unused (URL is always goliathpartners.com/jobs)
    """
    base_url = "https://www.goliathpartners.com/jobs"

    try:
        resp = client.get(base_url, timeout=15)
        resp.raise_for_status()
    except Exception as e:
        raise

    soup = BeautifulSoup(resp.text, "html.parser")
    jobs = []

    # Find all job cards
    job_cards = soup.find_all("div", class_="job")
    if not job_cards:
        return []

    for card in job_cards:
        job = _parse_job_card(card, base_url)
        if job:
            jobs.append(job)

    return jobs


def _parse_job_card(card, base_url: str) -> dict | None:
    """Extract job data from a single job card element."""
    try:
        # Title
        title_elem = card.find("h3", class_="job-heading")
        if not title_elem:
            return None

        title_link = title_elem.find("a")
        if not title_link:
            return None

        title = title_link.get_text(strip=True)
        url = title_link.get("href", "")
        if not url.startswith("http"):
            url = base_url + url

        # Extract external_id from URL (e.g., frontend19-software-engineer)
        external_id = url.split("/job/")[-1] if "/job/" in url else title.lower().replace(" ", "-")

        # Location
        location_span = card.find("span", class_="location")
        location = None
        if location_span:
            location_text = location_span.find("span")
            if location_text:
                location = location_text.get_text(strip=True)

        # Salary
        salary_min = None
        salary_max = None
        salary_currency = "GBP"
        salary_interval = "year"

        salary_span = card.find("span", class_="salary-figures")
        if salary_span:
            salary_text = salary_span.get_text(strip=True)
            # Format: "250K - 450K" or similar
            parts = salary_text.split("-")
            if len(parts) == 2:
                try:
                    min_str = parts[0].strip().replace("K", "000").replace(",", "")
                    max_str = parts[1].strip().replace("K", "000").replace(",", "")
                    salary_min = float(min_str)
                    salary_max = float(max_str)
                except (ValueError, AttributeError):
                    pass

        # Job type
        job_type = None
        type_span = card.find("span", class_="type")
        if type_span:
            type_text = type_span.get_text(strip=True).lower()
            job_type = type_text

        # Description (brief from listing, full on detail page)
        description_div = card.find("div", class_="job-description")
        description_html = ""
        if description_div:
            description_html = description_div.get_text(strip=True)

        return {
            "external_id": external_id,
            "title": title,
            "location": location,
            "url": url,
            "description_html": description_html,
            "salary_min": salary_min,
            "salary_max": salary_max,
            "salary_currency": salary_currency,
            "salary_interval": salary_interval,
            "workplace_type": "remote" if location and "remote" in location.lower() else None,
        }
    except Exception:
        return None
