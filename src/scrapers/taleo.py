"""Oracle Taleo Business Edition (TBE) job board scraper.
Pattern: https://{subdomain}.tbe.taleo.net/{tenant}/ats/careers/v2/searchResults?org={org}&cws={cws}

identifier: "subdomain=phh;tenant=phh01;org=INSTITUTEDA;cws=39" (semicolon-separated, same
convention as the Workday scraper's identifier). Find these by opening the company's careers
page network tab and reading them off the searchResults request URL/hostname.

Taleo's search is a session-based POST, not a plain GET API: the search page must be loaded
first to get a session cookie and a per-session CSRF token (embedded as a JS variable,
TBE_OBJ.CSRF.tokenValue), which then has to be echoed back in the search POST body. The search
results come back as server-rendered HTML (not JSON) containing links to each job's detail
page; each detail page embeds a clean schema.org JobPosting as JSON-LD with structured
location and full description text, which is what we actually parse.
"""
import json
import re

import httpx

SEARCH_PATH = "/{tenant}/ats/careers/v2/searchResults"
DETAIL_PATH = "/{tenant}/ats/careers/v2/viewRequisition"


def _parse_identifier(identifier: str) -> dict:
    return dict(kv.split("=", 1) for kv in identifier.split(";") if "=" in kv)


def _base_url(cfg: dict) -> str:
    return f"https://{cfg['subdomain']}.tbe.taleo.net"


def _get_csrf(base: str, tenant: str, org: str, cws: str, client: httpx.Client) -> str | None:
    resp = client.get(f"{base}{SEARCH_PATH.format(tenant=tenant)}", params={"org": org, "cws": cws}, timeout=20)
    resp.raise_for_status()
    m = re.search(r"TBE_OBJ\.CSRF\.tokenValue\s*=\s*'([^']+)'", resp.text)
    return m.group(1) if m else None


def probe(identifier: str, client: httpx.Client) -> bool:
    try:
        cfg = _parse_identifier(identifier)
        base = _base_url(cfg)
        csrf = _get_csrf(base, cfg["tenant"], cfg["org"], cfg["cws"], client)
        return csrf is not None
    except Exception:
        return False


def fetch_jobs(identifier: str, client: httpx.Client) -> list[dict]:
    cfg = _parse_identifier(identifier)
    base = _base_url(cfg)
    tenant, org, cws = cfg["tenant"], cfg["org"], cfg["cws"]

    csrf = _get_csrf(base, tenant, org, cws, client)
    if not csrf:
        raise RuntimeError("Taleo: could not obtain CSRF token from search page")

    search_url = f"{base}{SEARCH_PATH.format(tenant=tenant)}"
    body = "&".join(
        f"{k}={v}" for k, v in [
            ("org", org), ("cws", cws), ("act", "search"),
            ("org", org), ("cws", cws),
            ("WebPage", "JSRCH_V2"), ("WebVersion", "3"),
            ("CUSTOM_1218_total", ""), ("CUSTOM_1066_total", ""), ("CUSTOM_1240_total", ""),
            ("_csrf", csrf),
        ]
    )
    resp = client.post(
        search_url, params={"org": org, "cws": cws}, content=body,
        headers={"Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"}, timeout=20,
    )
    resp.raise_for_status()

    rids = sorted(set(re.findall(rf"viewRequisition\?org={re.escape(org)}&cws={re.escape(cws)}&rid=(\d+)", resp.text)))

    jobs = []
    detail_url = f"{base}{DETAIL_PATH.format(tenant=tenant)}"
    for rid in rids:
        try:
            d = client.get(detail_url, params={"org": org, "cws": cws, "rid": rid}, timeout=15)
            if d.status_code != 200:
                continue
            job = _parse_detail(d.text, rid, d.url)
            if job:
                jobs.append(job)
        except Exception:
            continue
    return jobs


def _parse_detail(html: str, rid: str, url: str) -> dict | None:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string)
        except (TypeError, ValueError):
            continue
        if data.get("@type") != "JobPosting":
            continue

        title = data.get("title")
        if not title:
            return None

        addr = (data.get("jobLocation") or {}).get("address", {})
        # addressLocality here is already "City, ST" (e.g. "Alexandria, VA"), not a bare city.
        locality = addr.get("addressLocality") or ""
        city = locality.split(",")[0].strip() if "," in locality else (locality or None)
        state = locality.split(",")[-1].strip() if "," in locality else None
        country_code = (addr.get("addressCountry") or {}).get("name")
        country = "United States" if country_code == "US" else country_code

        return {
            "external_id": rid,
            "title": title.strip(),
            "location": locality or None,
            "city": city,
            "state": state,
            "country": country,
            "remote": False,
            "url": str(url),
            "description_html": data.get("description", ""),
        }
    return None
