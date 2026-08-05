"""SmartRecruiters job board scraper. Public API.
Pattern: https://api.smartrecruiters.com/v1/companies/{slug}/postings

IMPORTANT: this endpoint returns 200 OK with totalFound: 0 and an empty content
list for ANY identifier, valid or not -- there's no 404 for a bad slug. So we
must check totalFound > 0, not just that the response parses. We also cross-check
the company name embedded in each posting (content[].company.name) against the
target company name, since a guessed slug can coincidentally match a real but
unrelated company already registered under that identifier.
"""
import httpx
from ._matching import verify_board

LIST_URL = "https://api.smartrecruiters.com/v1/companies/{slug}/postings"
DETAIL_URL = "https://api.smartrecruiters.com/v1/companies/{slug}/postings/{posting_id}"


def probe(slug: str, client: httpx.Client, company_name: str | None = None,
          weak_slug: bool = False) -> bool:
    try:
        resp = client.get(LIST_URL.format(slug=slug), params={"limit": 1}, timeout=10)
        if resp.status_code != 200:
            return False
        data = resp.json()
        if data.get("totalFound", 0) <= 0 or not data.get("content"):
            return False
        org_name = (data["content"][0].get("company") or {}).get("name") or None
        return verify_board(company_name, org_name, weak_slug)
    except Exception:
        return False


def fetch_jobs(slug: str, client: httpx.Client) -> list[dict]:
    resp = client.get(LIST_URL.format(slug=slug), params={"limit": 200}, timeout=20)
    resp.raise_for_status()
    postings = resp.json().get("content", [])
    jobs = []
    for p in postings:
        posting_id = p.get("id")
        # Fetch full detail for the description text (list endpoint doesn't include it)
        description_html = ""
        try:
            d = client.get(DETAIL_URL.format(slug=slug, posting_id=posting_id), timeout=15)
            if d.status_code == 200:
                jd = d.json().get("jobAd", {}).get("sections", {})
                # Each section's "text" is a plain string (e.g. sections.qualifications.text),
                # not a nested object -- a non-string section like "videos" (which has "urls"
                # instead of "text") is simply skipped rather than concatenated.
                description_html = " ".join(
                    sec["text"] for sec in jd.values()
                    if isinstance(sec, dict) and isinstance(sec.get("text"), str)
                )
        except Exception:
            pass
        location = p.get("location", {}) or {}
        loc_str = ", ".join(filter(None, [location.get("city"), location.get("region"), location.get("country")]))
        jobs.append({
            "external_id": posting_id,
            "title": p.get("name", "").strip(),
            "location": loc_str,
            "city": location.get("city"),
            "state": location.get("region"),
            "country": location.get("country"),
            "remote": bool(location.get("remote")),
            "url": p.get("applyUrl") or p.get("ref"),
            "description_html": description_html,
        })
    return jobs
