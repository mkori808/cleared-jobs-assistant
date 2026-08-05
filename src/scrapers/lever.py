"""Lever job board scraper. Public API, no auth needed.
Pattern: https://api.lever.co/v0/postings/{slug}?mode=json
"""
import httpx

from ._matching import verify_board

API_URL = "https://api.lever.co/v0/postings/{slug}"


def probe(slug: str, client: httpx.Client, company_name: str | None = None,
          weak_slug: bool = False) -> bool:
    # Lever's posting objects embed no company-name field, so identity can never be
    # confirmed here -- verify_board can only reject on a contradicting name, and there is
    # none, so any resolving slug (strong or weak) is accepted on existence alone. A weak
    # first-word slug on Lever therefore can't be told apart from an unrelated tenant; that
    # residual collision risk is handled by a manual override when found, not here.
    try:
        resp = client.get(API_URL.format(slug=slug), params={"mode": "json"}, timeout=10)
        if resp.status_code != 200:
            return False
        data = resp.json()
        if not (isinstance(data, list) and data):
            return False
        return verify_board(company_name, None, weak_slug)
    except Exception:
        return False


def fetch_jobs(slug: str, client: httpx.Client) -> list[dict]:
    resp = client.get(API_URL.format(slug=slug), params={"mode": "json"}, timeout=20)
    resp.raise_for_status()
    data = resp.json()
    jobs = []
    for j in data:
        categories = j.get("categories", {}) or {}
        location = categories.get("location")
        desc_html = (j.get("descriptionPlain") or "") + "\n" + (j.get("description") or "")
        # lever also gives structured "lists" (requirements etc.) - flatten them in
        lists_text = ""
        for lst in j.get("lists", []) or []:
            lists_text += " " + (lst.get("text") or "") + " "
            for item in lst.get("content", "").split("<li>"):
                lists_text += " " + item

        salary_min = salary_max = salary_currency = salary_interval = salary_source = None
        sr = j.get("salaryRange")
        if sr:
            salary_min, salary_max = sr.get("min"), sr.get("max")
            salary_currency = sr.get("currency")
            salary_interval = {"year-salary": "year", "hour-wage": "hour"}.get(sr.get("interval"), sr.get("interval"))
            salary_source = "structured"

        workplace_type = j.get("workplaceType")  # 'remote' | 'on-site' | 'hybrid' | 'unspecified'

        jobs.append({
            "external_id": j.get("id"),
            "title": j.get("text", "").strip(),
            "location": location,
            "url": j.get("hostedUrl"),
            "description_html": desc_html + lists_text,
            "salary_min": salary_min, "salary_max": salary_max,
            "salary_currency": salary_currency, "salary_interval": salary_interval,
            "salary_source": salary_source,
            "workplace_type": workplace_type,
        })
    return jobs
