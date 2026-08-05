"""Ashby job board scraper. Public GraphQL-ish posting API used by their embeddable job board.
Pattern: https://api.ashbyhq.com/posting-api/job-board/{slug}
"""
import httpx
from ._matching import verify_board

API_URL = "https://api.ashbyhq.com/posting-api/job-board/{slug}"


def probe(slug: str, client: httpx.Client, company_name: str | None = None,
          weak_slug: bool = False) -> bool:
    try:
        resp = client.get(API_URL.format(slug=slug), timeout=10)
        if resp.status_code != 200:
            return False
        data = resp.json()
        if "jobs" not in data or not data["jobs"]:
            return False
        # Many Ashby boards omit the org name entirely (both fields None). With no name to
        # contradict, verify_board accepts -- so a weak slug landing on a no-name board it
        # doesn't own (the "applied" / Applied Research Associates collision) can't be caught
        # here; that's resolved by a manual override, while a *named* board that disagrees is.
        org_name = data.get("organizationName") or data.get("companyName")
        return verify_board(company_name, org_name, weak_slug)
    except Exception:
        return False


def fetch_jobs(slug: str, client: httpx.Client) -> list[dict]:
    resp = client.get(API_URL.format(slug=slug), params={"includeCompensation": "true"}, timeout=20)
    resp.raise_for_status()
    data = resp.json()
    jobs = []
    for j in data.get("jobs", []):
        postal = ((j.get("address") or {}).get("postalAddress")) or {}
        comp = j.get("compensation") or {}
        comp_summary = comp.get("compensationTierSummary") or ""

        jobs.append({
            "external_id": j.get("id"),
            "title": (j.get("title") or "").strip(),
            "location": j.get("location"),
            "city": postal.get("addressLocality"),
            "state": postal.get("addressRegion"),
            "country": postal.get("addressCountry"),
            "remote": bool(j.get("isRemote")) or j.get("workplaceType") == "Remote",
            "url": j.get("jobUrl") or j.get("applyUrl"),
            # fold the compensation summary into the text so the shared salary/equity
            # extractor (which already handles "$81K - $87K" style ranges) picks it up
            "description_html": (j.get("descriptionHtml") or j.get("description") or "") + " " + comp_summary,
        })
    return jobs
