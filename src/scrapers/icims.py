"""Generic iCIMS job board scraper (works for any company using careers-{company}.icims.com).

iCIMS serves as the ATS for many companies (LMI, IBM, etc.). The public careers page
renders job listings as HTML with clean <li class="iCIMS_JobCardItem"> elements containing
structured job data (title, location, date, ID, description).

Usage: Pass identifier as "company_slug" (e.g. "lmi" for careers-lmi.icims.com)
"""
import httpx
from bs4 import BeautifulSoup


def _field_value(card, label_matches) -> str | None:
    """Finds a field-label span whose text satisfies label_matches, then reads its value from
    whichever DOM shape this tenant uses: a sibling span in the same wrapping element, or a
    sibling <dd class="iCIMS_JobHeaderData"> of the label's parent <dt>.

    At least one tenant (QinetiQ US) mislabels its "Posted Date" field's wrapping span as "Job
    Locations" (a template bug, not a real second location field) sitting right next to the
    real "Job Location" field -- so a naive first-match-wins search over every label containing
    "location" can return "4 days ago" instead of the city/state. The posted-date value span is
    reliably identifiable across every tenant seen (LMI included) by its `title="<timestamp>"`
    attribute (used for the hover tooltip), which no real location value carries -- skip it."""
    for label in card.find_all("span", class_="field-label"):
        if not label_matches(label.get_text(strip=True)):
            continue
        value_span = label.find_next_sibling("span")
        if value_span and not value_span.has_attr("title"):
            text = value_span.get_text(strip=True)
            if text:
                return text
        dt = label.find_parent("dt")
        dd = dt.find_next_sibling("dd", class_="iCIMS_JobHeaderData") if dt else None
        if dd:
            text = dd.get_text(strip=True)
            if text:
                return text
    return None


def fetch_jobs(identifier: str, client: httpx.Client) -> list[dict]:
    """Fetch jobs from an iCIMS careers site.

    identifier: company slug (e.g. "lmi" -> careers-lmi.icims.com), or the full subdomain for
    tenants that don't use the usual "careers-" prefix (e.g. "jobs-bylight" ->
    jobs-bylight.icims.com, ByLight's actual subdomain) -- any identifier already starting with
    a known iCIMS subdomain prefix is used as-is instead of getting "careers-" prepended again.
    """
    subdomain = identifier if identifier.startswith(("careers-", "jobs-")) else f"careers-{identifier}"
    base_url = f"https://{subdomain}.icims.com"
    search_url = f"{base_url}/jobs/search?ss=1&in_iframe=1"

    jobs = []
    seen_ids = set()
    offset = 0
    page_size = None
    max_total_jobs = 1000  # safety net against runaway pagination, not a real expected count

    while offset < max_total_jobs:
        url = f"{search_url}&offset={offset}" if offset > 0 else search_url
        try:
            resp = client.get(url, timeout=15)
            resp.raise_for_status()
        except Exception as e:
            if offset == 0:
                raise
            break

        html = resp.text
        soup = BeautifulSoup(html, "html.parser")

        # Find all job cards
        job_cards = soup.find_all("li", class_="iCIMS_JobCardItem")
        if not job_cards:
            break
        # The real page size varies by tenant (observed 10 for some, 50 for others) -- the old
        # hardcoded "+= 10" step meant tenants with a 50-card page re-fetched the same ~48/50
        # cards on every "next page" request, ballooning a ~50-job company into 1000 duplicate
        # card parses (and, now that detail pages are fetched too, 1000 wasted detail requests).
        if page_size is None:
            page_size = len(job_cards)

        new_count = 0
        for card in job_cards:
            job = _parse_job_card(card, base_url)
            if not job or job["external_id"] in seen_ids:
                continue
            seen_ids.add(job["external_id"])
            # The card only has a short teaser blurb -- fetch the detail page for the real
            # posting text (requirements, clearance level, etc. all live there, not in the
            # card). This was previously dead code (defined but never called), so every
            # iCIMS-hosted company was silently getting teaser-only descriptions.
            detail = _fetch_job_detail(job["url"], client)
            if detail.get("description"):
                job["description_html"] = detail["description"]
            if not job["location"] and detail.get("location"):
                job["location"] = detail["location"]
            jobs.append(job)
            new_count += 1

        # End of results once a page comes back short or entirely made of cards we've
        # already seen (both indicate we've reached/looped past the last real page).
        if len(job_cards) < page_size or new_count == 0:
            break
        offset += page_size

    return jobs


def _parse_job_card(card, base_url: str) -> dict | None:
    """Extract job data from a single iCIMS job card element."""
    try:
        # Title and URL
        title_elem = card.find("a", class_="iCIMS_Anchor")
        if not title_elem or not title_elem.get("href"):
            return None

        title = title_elem.find("h3")
        if not title:
            return None
        title_text = title.get_text(strip=True)

        job_url = title_elem.get("href", "").strip()
        if not job_url.startswith("http"):
            job_url = base_url + job_url

        # Extract job ID from URL (jobs/14275/... or jobs?id=14275)
        external_id = _extract_job_id(job_url)

        # Location: read from whichever field-label names it, rather than guessing by content
        # (a prior version scanned for any span containing "US-"/"Remote"/a dash -- but a bare
        # "US" location has no dash to match, so the loop kept scanning and could land on an
        # unrelated dash-containing span first, e.g. the requisition number "2026-169831").
        # Tenants vary in both label text and DOM shape:
        #   - LMI/Peraton/TekSynap: label "Job Locations", value is a *sibling span* in the same
        #     wrapping div.
        #   - FTI Defense: label "Location : Location", value sits in a sibling <dd> of the
        #     label's parent <dt> instead (not a sibling of the label span itself).
        #   - Brandes Associates: no single location label at all -- separate "City" and "State"
        #     labels, each dt/dd-shaped like FTI's.
        # _field_value handles both DOM shapes; try a combined location label first, and fall
        # back to combining City+State only if no single label matches.
        location = _field_value(card, lambda text: "location" in text.lower())
        if not location:
            parts = {}
            for name in ("City", "State"):
                value = _field_value(card, lambda text, name=name: text.lower() == name.lower())
                if value:
                    parts[name] = value
            if parts:
                location = ", ".join(parts[k] for k in ("City", "State") if k in parts)

        # Posted date
        posted_span = card.find("span", title=True)
        posted_date = None
        if posted_span:
            posted_date = posted_span.get("title", "")

        # Description (truncated on listing page)
        desc_div = card.find("div", class_="description")
        description_html = desc_div.get_text(strip=True) if desc_div else ""

        # Job ID from the structured data
        job_id_dd = card.find("dd", class_="iCIMS_JobHeaderData")
        job_id_text = job_id_dd.get_text(strip=True) if job_id_dd else external_id

        return {
            "external_id": external_id or job_id_text,
            "title": title_text,
            "location": location,
            "url": job_url,
            "description_html": description_html,
            # Fetch full description from detail page
        }
    except Exception:
        return None


def _extract_job_id(url: str) -> str:
    """Extract job ID from iCIMS URL like /jobs/14275/... or ?id=14275"""
    import re
    # Pattern: /jobs/14275/
    m = re.search(r"/jobs/(\d+)/", url)
    if m:
        return m.group(1)
    # Pattern: id=14275
    m = re.search(r"[?&]id=(\d+)", url)
    if m:
        return m.group(1)
    return url.split("/")[-1][:50]  # Fallback to last URL segment


def _fetch_job_detail(job_url: str, client: httpx.Client) -> dict:
    """Fetch a job's detail page for its full description and (as a fallback) its location.
    iCIMS detail pages don't have a single reliably-named description container (no class with
    "description" in it, despite what an older version of this function assumed -- it silently
    fell back to raw HTML/JS instead of real text), but the content is server-rendered directly
    into the page body, so stripping script/style and taking the whole visible text works and
    picks up the labeled "Clearance" field iCIMS shows alongside "Job Locations"/"Requisition
    ID"/etc. Location is extracted from the same page (before script/style are stripped) for
    tenants like QinetiQ US whose search-result cards carry no location field at all -- only the
    detail page does."""
    try:
        resp = client.get(job_url, timeout=15)
        if resp.status_code != 200:
            return {}
        soup = BeautifulSoup(resp.text, "html.parser")

        location = _field_value(soup, lambda text: "location" in text.lower())

        for tag in soup(["script", "style"]):
            tag.decompose()
        description = soup.get_text(separator=" ", strip=True)[:8000]

        return {"description": description, "location": location}
    except Exception:
        return {}
