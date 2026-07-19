"""One-off scraper for Epsilon Systems' custom careers API (workforce.epsilonsystems.com).

Unlike every other module in this package, this isn't a multi-tenant platform --
it's this one company's own Angular/Kendo-backed job board, so the URL is
hardcoded rather than templated from an identifier. The API is a bare JSON array
(no top-level "jobs" key, which is why it doesn't fit careers_widget.py) and
already includes the full HTML description inline -- no separate detail fetch
needed, unlike bamboohr.py or ashby.py.
"""
import httpx

API_URL = "https://workforce.epsilonsystems.com/api/jobpostings"


def fetch_jobs(_identifier, client: httpx.Client) -> list[dict]:
    resp = client.get(API_URL, timeout=20)
    resp.raise_for_status()
    postings = resp.json()

    jobs = []
    for item in postings:
        job_id = item.get("id") or item.get("jobId")
        if not job_id:
            continue
        remote = (item.get("workType") or "").strip().lower() == "remote"
        jobs.append({
            "external_id": job_id,
            "title": (item.get("jobTitle") or "").strip(),
            "location": ", ".join(filter(None, [item.get("locationCity"), item.get("locationState")])) or None,
            "city": item.get("locationCity"),
            "state": item.get("locationState"),
            "country": "United States",
            "remote": remote,
            "url": f"https://workforce.epsilonsystems.com/jobs/{item.get('jobId') or job_id}",
            "description_html": item.get("jobSummary") or "",
        })
    return jobs
