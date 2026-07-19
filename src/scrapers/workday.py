"""Workday job board scraper.

Workday tenant URLs (e.g. https://boeing.wd1.myworkdayjobs.com/External) can't be
reliably guessed from a company name, so Workday companies need a manual override
in config/companies.json with an identifier of the form:
  {"ats": "workday", "id": "host=wd1.myworkdayjobs.com;tenant=boeing;site=External"}

To find these values for a given company:
  1. Open their careers page and find a job listing, e.g.
     https://boeing.wd1.myworkdayjobs.com/en-US/External/job/.../R12345
  2. host = "wd1.myworkdayjobs.com" (the wdN.myworkdayjobs.com part)
  3. tenant = "boeing" (subdomain before .wdN)
  4. site = "External" (the path segment right after the tenant, before /job/...)
"""
import re
import sys

import httpx
from tqdm import tqdm

from ._location import normalize_state, normalize_country, US_STATE_ABBR, US_STATE_NAME_TO_ABBR

# ISO alpha-2 -> country name for the codes seen in Workday's jobRequisitionLocation.country
# (that field is consistently structured across tenants, unlike locationsText -- see below).
_ALPHA2_COUNTRY = {
    "US": "United States", "GB": "United Kingdom", "IE": "Ireland", "DE": "Germany",
    "IN": "India", "JP": "Japan", "AU": "Australia", "FR": "France", "CA": "Canada",
    "MX": "Mexico", "SG": "Singapore", "CN": "China", "NL": "Netherlands", "CH": "Switzerland",
    "IL": "Israel", "PL": "Poland", "RO": "Romania", "PT": "Portugal", "ES": "Spain",
    "IT": "Italy", "SE": "Sweden", "KR": "South Korea", "TW": "Taiwan", "PH": "Philippines",
    "CR": "Costa Rica", "BR": "Brazil", "AR": "Argentina", "CL": "Chile", "CO": "Colombia",
    "SA": "Saudi Arabia", "AE": "United Arab Emirates", "QA": "Qatar",
    "MY": "Malaysia", "TH": "Thailand", "VN": "Vietnam", "ID": "Indonesia", "NZ": "New Zealand",
    "BE": "Belgium", "AT": "Austria", "DK": "Denmark", "NO": "Norway", "FI": "Finland",
    "HU": "Hungary", "CZ": "Czech Republic", "TR": "Turkey", "ZA": "South Africa", "EG": "Egypt",
    "GR": "Greece", "LU": "Luxembourg",
}

# Full country names as they appear verbatim in Workday's locationsText (as opposed to the
# alpha-2 codes above) -- used to recognize the "Country, State, City" format (e.g. Intel's
# "US, Arizona, Phoenix" / "Ireland, Leixlip") where the country comes *first*, unlike the
# "Street, City, ST" trailing-state format (e.g. AeroVironment).
_LEADING_COUNTRY_TOKENS = {"us", "usa", "u.s.", "u.s.a."} | {n.lower() for n in _ALPHA2_COUNTRY.values()}


def _parse_identifier(identifier: str) -> dict:
    parts = dict(kv.split("=", 1) for kv in identifier.split(";") if "=" in kv)
    return parts


def probe(identifier: str, client: httpx.Client) -> bool:
    try:
        cfg = _parse_identifier(identifier)
        url = f"https://{cfg['tenant']}.{cfg['host']}/wday/cxs/{cfg['tenant']}/{cfg['site']}/jobs"
        resp = client.post(url, json={"limit": 1, "offset": 0}, timeout=10)
        return resp.status_code == 200 and "total" in resp.json()
    except Exception:
        return False


def fetch_jobs(identifier: str, client: httpx.Client) -> list[dict]:
    cfg = _parse_identifier(identifier)
    base = f"https://{cfg['tenant']}.{cfg['host']}"
    list_url = f"{base}/wday/cxs/{cfg['tenant']}/{cfg['site']}/jobs"

    # Fetch all job listings -- no artificial cap, so a company with 2,000 postings (e.g.
    # NVIDIA) gets all 2,000, not a truncated subset. Termination relies on the API's own
    # "total" field (offset >= total), not just "did this page come back empty": at least one
    # tenant (RAND) returns the exact same non-empty page for every offset past the real
    # total instead of ever going empty, which would otherwise loop forever now that the old
    # fixed max_jobs cap is gone.
    all_postings = []
    offset = 0
    limit = 20
    total = None

    while total is None or offset < total:
        resp = client.post(list_url, json={"limit": limit, "offset": offset}, timeout=20)
        resp.raise_for_status()
        data = resp.json()
        postings = data.get("jobPostings", [])
        if not postings:
            break
        all_postings.extend(postings)
        offset += limit
        total = data.get("total", offset)

    # Process jobs with progress
    jobs = []
    postings_to_process = all_postings
    total = len(postings_to_process)

    for i, p in enumerate(tqdm(postings_to_process, desc="[workday] jobs", file=sys.stdout, mininterval=0.5)):
        path = p.get("externalPath", "")
        detail_api_url = f"{base}/wday/cxs/{cfg['tenant']}/{cfg['site']}{path}"
        detail = _fetch_job_detail(detail_api_url, client)

        # Some tenants (e.g. Parsons) don't populate locationsText on the list endpoint at
        # all -- fall back to the detail page's own "location" text field in that case.
        raw_location = p.get("locationsText") or p.get("location") or detail.get("location_text")
        job = {
            "external_id": path,
            "title": p.get("title", "").strip(),
            "location": raw_location,
            "url": f"{base}/{cfg['site']}{path}",
            "description_html": detail.get("description", ""),
        }
        job.update(_parse_workday_location(raw_location, country_hint=detail.get("country_code")))
        jobs.append(job)

    return jobs


def _parse_workday_location(raw: str | None, country_hint: str | None = None) -> dict:
    """Workday's locationsText format varies by tenant -- observed so far:
      - "Country-State-City" hyphens (Northrop Grumman: "United States-California-San Diego")
      - "Street, City, ST" commas, trailing state (AeroVironment: "308 Sentinel Dr, Jessup, MD")
      - "Country, State, City" commas, leading country (Intel: "US, Arizona, Phoenix")
      - "COUNTRY - STATE, City" dash+comma (Parsons, via bulletFields/detail location text:
        "US - AL, Huntsville")
    country_hint is the alpha-2 code from the detail page's jobRequisitionLocation.country,
    which -- unlike locationsText -- is consistently structured across every tenant; it's used
    to fill/confirm country when the text format is ambiguous or country-less (e.g. "Leixlip"
    alone, with the country only implied by hint). Multi-site postings ("9 Locations") can't be
    split into one place, so those fall back to text-based parsing downstream instead."""
    hint_country = _ALPHA2_COUNTRY.get((country_hint or "").upper())

    if not raw or "Locations" in raw:
        return {"country": hint_country} if hint_country else {}

    # "COUNTRY - STATE, City" e.g. "US - AL, Huntsville"
    m = re.match(r"^([A-Za-z.]{2,})\s*-\s*([A-Za-z]{2,}),\s*(.+)$", raw)
    if m:
        country_tok, state_tok, city_tok = (g.strip() for g in m.groups())
        if state_tok.upper() in US_STATE_ABBR:
            return {"country": hint_country or normalize_country(country_tok) or "United States",
                    "state": state_tok.upper(), "city": city_tok, "remote": False}

    if "," in raw:
        parts = [p.strip() for p in raw.split(",") if p.strip()]
        if parts and parts[-1].upper() in US_STATE_ABBR:
            # Trailing "..., City, ST" -- take the state and the part right before it as the
            # city, discarding any leading street-address parts.
            state = parts[-1].upper()
            city = parts[-2] if len(parts) >= 2 else None
            # Some tenants prefix that city with "COUNTRY - " using the same dash-tag format
            # seen elsewhere (Parsons' "SA - Riyadh") -- e.g. Boeing's "USA - Berkeley, MO".
            # The comma split above only separates on the outer comma, so without this the
            # "USA - " leaks straight into the city value.
            if city:
                prefix_m = re.match(r"^([A-Za-z.]{2,4})\s*-\s*(.+)$", city)
                if prefix_m and prefix_m.group(1).lower() in _LEADING_COUNTRY_TOKENS:
                    city = prefix_m.group(2).strip()
            return {"country": hint_country or "United States", "state": state, "city": city, "remote": False}
        if parts and parts[0].lower() in _LEADING_COUNTRY_TOKENS:
            # Leading "Country, State, City" or "Country, City"
            country = hint_country or normalize_country(parts[0])
            if len(parts) >= 3:
                state_tok = parts[1]
                state = state_tok.upper() if state_tok.upper() in US_STATE_ABBR else US_STATE_NAME_TO_ABBR.get(state_tok.lower())
                return {"country": country, "state": state, "city": parts[2], "remote": False}
            if len(parts) == 2:
                return {"country": country, "state": None, "city": parts[1], "remote": False}
        return {"country": hint_country} if hint_country else {}

    parts = [p.strip() for p in raw.split("-") if p.strip()]
    if len(parts) == 3:
        country, state, city = parts
        return {"country": hint_country or country, "state": normalize_state(state), "city": city, "remote": False}
    if len(parts) == 2:
        first, second = parts
        if first.lower() in ("virtual", "remote"):
            # "Virtual - Minnesota" (CDW): a remote role tied to a home state, not a literal
            # "Virtual" country -- the old fallthrough put "Virtual" itself into the country
            # slot and the actual state name into city.
            state = normalize_state(second)
            return {"country": hint_country or "United States", "state": state if state != second else None,
                    "city": None if state != second else second, "remote": True}
        country, city = first, second
        return {"country": hint_country or country, "state": None, "city": city, "remote": False}
    return {"country": hint_country} if hint_country else {}


def _fetch_job_detail(detail_api_url: str, client: httpx.Client) -> dict:
    """Fetch a job's description, fallback location text, and country code from the Workday
    JSON detail API, with a short timeout so one slow/broken posting can't stall a refresh."""
    try:
        resp = client.get(detail_api_url, timeout=5)
        if resp.status_code == 200:
            jpi = resp.json().get("jobPostingInfo", {})
            desc = jpi.get("jobDescription", "")
            country_code = ((jpi.get("jobRequisitionLocation") or {}).get("country") or {}).get("alpha2Code")
            return {
                "description": desc[:8000] if desc else "",
                "location_text": jpi.get("location"),
                "country_code": country_code,
            }
    except Exception:
        pass
    return {}
