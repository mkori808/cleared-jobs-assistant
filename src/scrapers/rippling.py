"""Rippling job board scraper.

Rippling exposes a public, unauthenticated JSON API behind its embeddable job
board (the page at ats.rippling.com/embed/{board_id}/jobs is a thin wrapper
around this):
  - List:   GET https://ats.rippling.com/api/v2/board/{board_id}/jobs?page=0&pageSize=50
  - Detail: GET https://ats.rippling.com/api/v2/board/{board_id}/jobs/{job_id}

The list endpoint returns titles/locations but not full descriptions, so we
fetch each job's detail page for the actual text (needed for clearance/salary/
equity keyword scanning). This is reverse-engineered from Rippling's embed
widget rather than official documentation, so field names are handled
defensively -- if Rippling changes their response shape, adjust the .get()
fallbacks below.
"""
import httpx

from ._matching import verify_board

LIST_URL = "https://ats.rippling.com/api/v2/board/{board_id}/jobs"
DETAIL_URL = "https://ats.rippling.com/api/v2/board/{board_id}/jobs/{job_id}"
PAGE_SIZE = 50


def probe(board_id: str, client: httpx.Client, company_name: str | None = None,
          require_verification: bool = False) -> bool:
    # The list response embeds no company-name field, so (like Lever) identity can't be
    # confirmed here. verify_board accepts a strong slug on existence, rejects weak ones.
    # Rippling board ids often don't match the company name anyway, so weak-slug guessing
    # was never reliable here -- most Rippling companies need a manual override.
    try:
        resp = client.get(LIST_URL.format(board_id=board_id), params={"page": 0, "pageSize": 1}, timeout=10)
        if resp.status_code != 200:
            return False
        data = resp.json()
        items = data.get("items", data if isinstance(data, list) else [])
        if not items:
            return False
        return verify_board(company_name, None, require_verification)
    except Exception:
        return False


def _extract_location(item: dict) -> dict:
    """The list item's actual location field is "locations" (a list of structured location
    objects with city/stateCode/country/workplaceType), not "workLocation"/"location" as
    originally guessed -- that mismatch meant every Rippling-hosted company's jobs had no
    location data at all. A posting can list multiple offices; only the first is used as the
    structured city/state/country (consistent with how other scrapers handle multi-location
    postings), but the raw "location" text keeps all of them for display."""
    locs = item.get("locations") or []
    if not locs:
        return {"location": None, "city": None, "state": None, "country": None, "remote": False}
    first = locs[0]
    return {
        "location": ", ".join(l.get("name", "").strip() for l in locs if l.get("name")),
        "city": first.get("city"),
        "state": first.get("stateCode") or first.get("state"),
        "country": first.get("country"),
        "remote": any(l.get("workplaceType") == "REMOTE" for l in locs),
    }


def fetch_jobs(board_id: str, client: httpx.Client) -> list[dict]:
    jobs = []
    page = 0
    while True:
        resp = client.get(LIST_URL.format(board_id=board_id), params={"page": page, "pageSize": PAGE_SIZE}, timeout=20)
        resp.raise_for_status()
        data = resp.json()
        items = data.get("items", data if isinstance(data, list) else [])
        if not items:
            break

        for item in items:
            job_id = item.get("id") or item.get("uuid")
            description_html = ""
            try:
                d = client.get(DETAIL_URL.format(board_id=board_id, job_id=job_id), timeout=15)
                if d.status_code == 200:
                    detail = d.json()
                    desc = detail.get("description") or {}
                    if isinstance(desc, dict):
                        description_html = (desc.get("company", "") or "") + " " + (desc.get("role", "") or "")
                    else:
                        description_html = desc or ""
            except Exception:
                pass

            jobs.append({
                "external_id": job_id,
                "title": (item.get("name") or item.get("title") or "").strip(),
                "url": item.get("url"),
                "description_html": description_html,
                **_extract_location(item),
            })

        total_pages = data.get("totalPages") if isinstance(data, dict) else None
        page += 1
        if total_pages is not None and page >= total_pages:
            break
        if total_pages is None and len(items) < PAGE_SIZE:
            break  # no pagination info given and this page wasn't full -> assume done
    return jobs
