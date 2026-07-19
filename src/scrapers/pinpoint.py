"""Pinpoint (pinpointhq.com) job board scraper. Public JSON feed used by their embeddable
careers widget -- no ATS marker appears in the wrapping company site's raw HTML (job links are
plain <a href> tags to {slug}.pinpointhq.com with no "job"/"career"/"position" hint in the URL
path, so they're missed by the generic scraper's link-discovery heuristic too).

Pattern: https://{slug}.pinpointhq.com/postings.json

identifier: the pinpointhq.com subdomain slug (e.g. "impulsespace" for
impulsespace.pinpointhq.com).
"""
import httpx

from ._location import normalize_state, US_STATE_NAME_TO_ABBR, US_STATE_ABBR


def fetch_jobs(identifier: str, client: httpx.Client) -> list[dict]:
    resp = client.get(f"https://{identifier}.pinpointhq.com/postings.json", timeout=20)
    resp.raise_for_status()
    postings = resp.json().get("data", [])

    jobs = []
    for p in postings:
        parsed = _parse_job(p)
        if parsed:
            jobs.append(parsed)
    return jobs


def _parse_job(p: dict) -> dict | None:
    try:
        job_id = p.get("id")
        title = p.get("title")
        if not job_id or not title:
            return None

        loc = p.get("location") or {}
        city = (loc.get("city") or "").strip() or None
        province = (loc.get("province") or "").strip()
        state = normalize_state(province) if province else None
        # Pinpoint gives a free-text province, not a country code -- if it resolves to a
        # real US state/DC, the posting is domestic; otherwise leave country unset rather
        # than guess (international Pinpoint customers use this same province field for
        # non-US regions, e.g. Canadian provinces, which normalize_state won't match).
        is_us_state = state in US_STATE_ABBR or province.lower() in US_STATE_NAME_TO_ABBR
        country = "United States" if is_us_state else None

        description_parts = [p.get("description") or "", p.get("key_responsibilities") or "", p.get("benefits") or ""]
        description_html = "\n\n".join(part for part in description_parts if part)

        job = {
            "external_id": str(job_id),
            "title": title.strip(),
            "location": loc.get("name") or city,
            "city": city,
            "state": state,
            "country": country,
            "remote": p.get("workplace_type") == "remote",
            "url": p.get("url"),
            "description_html": description_html[:20000],
        }

        # Pinpoint's own compensation fields are already structured (min/max/currency/
        # frequency) -- pass them straight through instead of leaving it to the regex
        # fallback to re-derive the same numbers from prose.
        if p.get("compensation_minimum") is not None:
            job.update({
                "salary_min": p.get("compensation_minimum"),
                "salary_max": p.get("compensation_maximum"),
                "salary_currency": p.get("compensation_currency"),
                "salary_interval": p.get("compensation_frequency"),
                "salary_source": "structured",
            })

        return job
    except Exception:
        return None
