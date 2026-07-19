"""Deloitte careers RSS feed scraper.

Deloitte's Avature ATS provides an RSS feed at apply.deloitte.com/en_US/careers/SearchJobs/feed/
which lists all current job openings with title, link, and publish date. Each job link points to
a detail page with full description, location, salary, and clearance requirements.
"""
import httpx
import json
import re
from urllib.parse import urlencode
from bs4 import BeautifulSoup


def fetch_jobs(identifier: str, client: httpx.Client) -> list[dict]:
    """Fetch jobs from Deloitte RSS feed.

    identifier: unused (URL is always apply.deloitte.com)
    Fetches jobs in batches of 100 from the RSS feed.
    """
    base_feed_url = "https://apply.deloitte.com/en_US/careers/SearchJobs/feed/"
    jobs = []
    page_size = 100
    page = 1
    max_pages = 3  # Limit to prevent excessive requests

    while page <= max_pages:
        params = {"jobSort": "relevancy", "jobRecordsPerPage": page_size, "page": page}
        feed_url = f"{base_feed_url}?{urlencode(params)}"

        try:
            resp = client.get(feed_url, timeout=15)
            resp.raise_for_status()
        except Exception as e:
            if page == 1:
                raise
            break

        soup = BeautifulSoup(resp.text, "xml")
        items = soup.find_all("item")

        if not items:
            break

        for item in items:
            job = _parse_rss_item(item, client)
            if job:
                jobs.append(job)

        if len(items) < page_size:
            break

        page += 1

    return jobs


def _parse_rss_item(item, client: httpx.Client) -> dict | None:
    """Extract job data from an RSS item and fetch the detail page."""
    try:
        title_elem = item.find("title")
        if not title_elem:
            return None

        title = title_elem.get_text(strip=True)

        link_elem = item.find("link")
        if not link_elem:
            return None

        url = link_elem.get_text(strip=True)
        external_id = url.split("/")[-1] if "/" in url else title.lower().replace(" ", "-")

        pubdate_elem = item.find("pubDate")
        pub_date = pubdate_elem.get_text(strip=True) if pubdate_elem else None

        # Fetch the detail page to get full description, location, salary, etc.
        job_details = _fetch_job_details(url, client)

        return {
            "external_id": external_id,
            "title": title,
            "location": job_details.get("location"),
            "url": url,
            "description_html": job_details.get("description_html"),
            "salary_min": job_details.get("salary_min"),
            "salary_max": job_details.get("salary_max"),
            "salary_currency": job_details.get("salary_currency"),
            "salary_interval": job_details.get("salary_interval"),
        }
    except Exception:
        return None


def _fetch_job_details(job_url: str, client: httpx.Client) -> dict:
    """Fetch and parse a job detail page."""
    details = {
        "location": None,
        "description_html": "",
        "salary_min": None,
        "salary_max": None,
        "salary_currency": None,
        "salary_interval": None,
    }

    try:
        # Detail URLs 302-redirect to a locale-prefixed path (e.g. /en_US/careers/JobDetail/...);
        # without follow_redirects, we'd silently parse the tiny redirect-stub body instead of
        # the real page (raise_for_status() doesn't catch 3xx, so this failed silently before).
        resp = client.get(job_url, timeout=15, follow_redirects=True)
        resp.raise_for_status()
    except Exception:
        return details

    soup = BeautifulSoup(resp.text, "html.parser")

    # Deloitte embeds a schema.org JobPosting as JSON-LD -- far more reliable than scraping
    # HTML text/class selectors (which broke silently before). jobLocation in it is empty for
    # Deloitte's common multi-location postings though, so location still comes from the HTML.
    description_html = ""
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string)
        except (TypeError, ValueError):
            continue
        if data.get("@type") == "JobPosting" and data.get("description"):
            description_html = data["description"]
            break

    if not description_html:
        main = soup.find("main") or soup.find("article")
        if main:
            description_html = main.get_text(separator=" ", strip=True)[:8000]

    # Location: Deloitte commonly posts one role open to many locations, listed as
    # <div class="article__header--locations"><p class="paragraph">City, State, Country</p>...
    # A single visible location (no toggle) falls back to the header's own location line.
    locations_div = soup.select_one("div.article__header--locations")
    if locations_div:
        loc_list = [p.get_text(strip=True) for p in locations_div.select("p.paragraph") if p.get_text(strip=True)]
        details["location"] = "; ".join(loc_list) if loc_list else None

    # Salary isn't in the JSON-LD (baseSalary is always null in practice), so scan the visible
    # page text for a "$X - $Y" range the way the rest of the pipeline does for free-text roles.
    salary_pattern = re.compile(r"\$[\d,]+\s*(?:-|to)\s*\$[\d,]+")
    salary_match = salary_pattern.search(soup.get_text())
    if salary_match:
        amounts = re.findall(r"\$?([\d,]+)", salary_match.group())
        if len(amounts) >= 2:
            try:
                details["salary_min"] = float(amounts[0].replace(",", ""))
                details["salary_max"] = float(amounts[1].replace(",", ""))
                details["salary_interval"] = "year"
            except ValueError:
                pass

    details["description_html"] = description_html
    return details
