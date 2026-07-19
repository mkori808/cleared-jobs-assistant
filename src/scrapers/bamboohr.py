"""BambooHR careers scraper. Public unauthenticated JSON API behind BambooHR's
hosted careers page:
  List:   GET https://{subdomain}.bamboohr.com/careers/list
  Detail: GET https://{subdomain}.bamboohr.com/careers/{job_id}/detail

The list endpoint gives title/department/location; the full HTML description
(needed for clearance/salary/equity keyword scanning) requires a per-job detail
fetch. Some postings carry a structured `compensation` field, but its shape
isn't documented and was null on every posting seen during testing -- rather
than guess field names, it's folded into the description text (same trick as
ashby.py's compensationTierSummary) so the existing regex salary fallback can
still pick it up if a company does populate it.
"""
import httpx

from ._matching import verify_board

LIST_URL = "https://{subdomain}.bamboohr.com/careers/list"
DETAIL_URL = "https://{subdomain}.bamboohr.com/careers/{job_id}/detail"


def probe(subdomain: str, client: httpx.Client, company_name: str | None = None,
          require_verification: bool = False) -> bool:
    try:
        resp = client.get(LIST_URL.format(subdomain=subdomain), timeout=10)
        if resp.status_code != 200:
            return False
        data = resp.json()
        if not data.get("result"):
            return False
        # BambooHR's list feed carries no reliable org-name field, so identity can't be
        # confirmed -- strong (full-name) subdomains pass, weak slugs are rejected.
        return verify_board(company_name, None, require_verification)
    except Exception:
        return False


def _location_fields(item: dict) -> dict:
    # atsLocation carries full names (e.g. "Virginia"); refresh.py normalizes state
    # abbreviations for us, so we don't need to do it here.
    ats_loc = item.get("atsLocation") or {}
    loc = item.get("location") or {}
    city = ats_loc.get("city") or loc.get("city")
    remote = bool(city) and city.strip().lower() == "remote"
    return {
        "city": None if remote else city,
        "state": ats_loc.get("state") or loc.get("state"),
        "country": ats_loc.get("country"),
        "remote": remote,
    }


def fetch_jobs(subdomain: str, client: httpx.Client) -> list[dict]:
    resp = client.get(LIST_URL.format(subdomain=subdomain), timeout=20)
    resp.raise_for_status()
    data = resp.json()

    jobs = []
    for item in data.get("result", []):
        job_id = item.get("id")
        if not job_id:
            continue

        description_html = ""
        try:
            d = client.get(DETAIL_URL.format(subdomain=subdomain, job_id=job_id), timeout=15)
            if d.status_code == 200:
                opening = (d.json().get("result") or {}).get("jobOpening") or {}
                comp = opening.get("compensation")
                comp_text = " ".join(str(v) for v in comp.values() if v) if isinstance(comp, dict) else (comp or "")
                description_html = (opening.get("description") or "") + " " + comp_text
        except Exception:
            pass

        loc = _location_fields(item)
        jobs.append({
            "external_id": job_id,
            "title": (item.get("jobOpeningName") or "").strip(),
            "location": ", ".join(filter(None, [loc["city"], loc["state"], loc["country"]])) or None,
            "city": loc["city"],
            "state": loc["state"],
            "country": loc["country"],
            "remote": loc["remote"],
            "url": f"https://{subdomain}.bamboohr.com/careers/{job_id}",
            "description_html": description_html,
        })
    return jobs
