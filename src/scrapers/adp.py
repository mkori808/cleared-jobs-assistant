"""ADP WorkforceNow job board scraper. Public JSON API behind the embeddable
"recruitment.html" career center widget.
Pattern: https://workforcenow.adp.com/mascsr/default/careercenter/public/events/staffing/v1/job-requisitions?cid={cid}

identifier: the ADP "cid" (a UUID identifying the employer's WorkforceNow instance),
e.g. "3825f7a8-dec1-43e7-a9d0-eaac67e830d1" for Trident Systems.

IMPORTANT LIMITATION: unlike every other ATS this app scrapes, ADP's public API does not
expose the job posting's actual description/body text anywhere -- only structured metadata
(title, location, salary range, employment type, posting date). Every attempt to find a
description field or a detail endpoint that returns one came back empty; the full text is
apparently only rendered client-side by the JS widget from a source this scraper can't reach.
That means clearance_level/citizenship_required/polygraph_mentioned can never be detected for
ADP-hosted companies via the normal regex-over-description pipeline (there's no text to scan) --
those fields will always come back "None mentioned" here, which is a platform gap, not a sign
the company has no cleared roles.
"""
import httpx

from ._location import normalize_state, US_STATE_ABBR

API_URL = "https://workforcenow.adp.com/mascsr/default/careercenter/public/events/staffing/v1/job-requisitions"


def probe(cid: str, client: httpx.Client, company_name: str | None = None) -> bool:
    try:
        resp = client.get(API_URL, params={"cid": cid}, timeout=10)
        if resp.status_code != 200:
            return False
        data = resp.json()
        return bool(data.get("jobRequisitions"))
    except Exception:
        return False


def fetch_jobs(cid: str, client: httpx.Client) -> list[dict]:
    jobs = []
    skip = 0
    total = None
    while total is None or skip < total:
        resp = client.get(API_URL, params={"cid": cid, "$skip": skip}, timeout=20)
        resp.raise_for_status()
        data = resp.json()
        reqs = data.get("jobRequisitions", [])
        if not reqs:
            break
        for req in reqs:
            parsed = _parse_job(req, cid)
            if parsed:
                jobs.append(parsed)
        total = data.get("meta", {}).get("totalNumber", len(reqs))
        skip += len(reqs)
    return jobs


def _parse_job(req: dict, cid: str) -> dict | None:
    try:
        item_id = req.get("itemID")
        title = req.get("requisitionTitle")
        if not item_id or not title:
            return None

        locs = req.get("requisitionLocations") or []
        city = state = None
        if locs:
            addr = locs[0].get("address", {})
            city = addr.get("cityName")
            state_code = (addr.get("countrySubdivisionLevel1") or {}).get("codeValue")
            state = normalize_state(state_code) if state_code else None
        country = "United States" if state in US_STATE_ABBR else None
        location = ", ".join(l.get("nameCode", {}).get("shortName", "").strip() for l in locs if l.get("nameCode"))

        pay = req.get("payGradeRange") or {}
        salary_min = (pay.get("minimumRate") or {}).get("amountValue")
        salary_max = (pay.get("maximumRate") or {}).get("amountValue")
        salary_currency = (pay.get("minimumRate") or {}).get("currencyCode")

        job = {
            "external_id": item_id,
            "title": title.strip(),
            "location": location or None,
            "city": city,
            "state": state,
            "country": country,
            "remote": False,
            "url": f"https://workforcenow.adp.com/mascsr/default/mdf/recruitment/recruitment.html?cid={cid}&jobId={item_id}&lang=en_US",
            # No description text is available from this API -- see module docstring.
            "description_html": "",
        }
        if salary_min is not None:
            job.update({
                "salary_min": salary_min,
                "salary_max": salary_max,
                "salary_currency": salary_currency,
                "salary_interval": "year",
                "salary_source": "structured",
            })
        return job
    except Exception:
        return None
