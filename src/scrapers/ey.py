"""EY (Ernst & Young) careers scraper.

EY uses SuccessFactors ATS with a public search page at careers.ey.com/search/
Jobs are rendered in the HTML with pagination via startrow parameter.
Each job has a detail page with full description.
"""
import httpx
from bs4 import BeautifulSoup


def fetch_jobs(identifier: str, client: httpx.Client) -> list[dict]:
    """Fetch jobs from EY careers portal.

    identifier: unused (URL is always careers.ey.com)
    Fetches jobs with pagination (25 per page, max 10 pages).
    """
    base_url = "https://careers.ey.com/search/"
    jobs = []
    page_size = 25
    max_pages = 10

    for page in range(max_pages):
        start_row = page * page_size
        params = {"location": "US", "startrow": start_row} if start_row > 0 else {"location": "US"}

        try:
            url = base_url if start_row == 0 else f"{base_url}?location=US&startrow={start_row}"
            resp = client.get(url, timeout=15)
            resp.raise_for_status()
        except Exception as e:
            if page == 0:
                raise
            break

        soup = BeautifulSoup(resp.text, "html.parser")
        job_links = soup.find_all("a", class_="jobTitle-link")

        if not job_links:
            break

        for link in job_links:
            job = _parse_job_link(link, client)
            if job:
                jobs.append(job)

        # If we got fewer than page_size results, we've hit the end
        if len(job_links) < page_size:
            break

    return jobs


def _parse_job_link(link, client: httpx.Client) -> dict | None:
    """Extract job data from a search result link and fetch detail page."""
    try:
        title = link.get_text(strip=True)
        job_url = link.get("href", "")

        if not job_url:
            return None

        if not job_url.startswith("http"):
            job_url = "https://careers.ey.com" + job_url

        # Extract external_id from URL (last part)
        external_id = job_url.split("/")[-2] if "/" in job_url else title.lower().replace(" ", "-")

        # Fetch detail page to get location and description
        details = _fetch_job_details(job_url, client)

        location = details.get("location")
        remote = details.get("remote", False)
        return {
            "external_id": external_id,
            "title": title,
            "location": location,
            # EY's own "Location:" line is a bare city name (e.g. "New York City") with no
            # state/country -- parse_location() can't infer a country from that alone, so every
            # job ended up with country=None despite fetch_jobs always querying location=US.
            # Providing city/country directly (rather than leaving it to parse_location) puts
            # this on the "structured" location path, so both must be set together here.
            # "Anywhere in Country At EY" is EY's own placeholder for a nationwide-remote role,
            # not a real city -- _fetch_job_details() catches that and reports it via `remote`
            # instead, with `location` left None so it doesn't pollute the city filter.
            "city": location,
            "country": "United States" if (location or remote) else None,
            "remote": remote,
            "url": job_url,
            "description_html": details.get("description_html"),
        }
    except Exception:
        return None


def _fetch_job_details(job_url: str, client: httpx.Client) -> dict:
    """Fetch and parse a job detail page."""
    details = {
        "location": None,
        "remote": False,
        "description_html": "",
    }

    try:
        resp = client.get(job_url, timeout=15)
        resp.raise_for_status()
    except Exception:
        return details

    soup = BeautifulSoup(resp.text, "html.parser")

    # Extract job description from the jobdescription span
    desc_span = soup.find("span", class_="jobdescription")
    if desc_span:
        # Get the full text content of the description
        desc_text = desc_span.get_text(separator=" ", strip=True)
        details["description_html"] = desc_text[:8000]  # Truncate to 8000 chars

        # Extract location from the description (usually at the beginning)
        # Format: "Location: City1, City2, ... CityN"
        if "Location:" in desc_text:
            location_part = desc_text.split("Location:")[1].split("\n")[0].strip()
            # Take first city mentioned
            first_city = location_part.split(",")[0].strip()
            if first_city.lower().startswith("anywhere"):
                details["remote"] = True
            elif first_city:
                details["location"] = first_city

    return details
