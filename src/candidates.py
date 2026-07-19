"""Discovers cleared-job employers that aren't in config/companies.json yet.

Source: USAspending.gov's public award API (no key, no auth). Companies winning federal
professional-services and R&D contracts are, near enough, the population of employers that
hire cleared staff -- so the set difference between "recipients of those contracts" and
"companies we already track" is a working candidate list.

This runs as its own pass, not as part of refresh.run_refresh: it hits a third-party API on
a completely different cadence from the ATS scrapers, and a failure here shouldn't be able
to take down a job refresh.
"""
import json
from pathlib import Path

import httpx

from . import db
from .scrapers._matching import company_tokens, same_company

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "companies.json"

API_URL = "https://api.usaspending.gov/api/v2/search/spending_by_category/recipient/"

# Professional/technical services and R&D -- where cleared analyst, engineer, and intel jobs
# actually sit. Excludes shipbuilding, fuel, construction, and munitions manufacturing, which
# dominate raw defense spending but hire relatively few cleared knowledge workers.
#   5413 architectural/engineering, 5415 computer systems design,
#   5416 management/technical consulting, 5417 scientific R&D
NAICS_CODES = ["5413", "5415", "5416", "5417"]

# Contract award types (definitive contracts, purchase orders, delivery orders, IDVs) --
# excludes grants and loans, which aren't employment signals.
AWARD_TYPE_CODES = ["A", "B", "C", "D"]

# Agencies worth mining. DoD dominates; the others carry meaningful cleared populations.
AGENCIES = [
    "Department of Defense",
    "Department of Homeland Security",
    "Department of State",
    "Department of Energy",
]

# The API pages at 100; 3 pages per agency is deep enough to reach the ~$50M/2yr floor, well
# past the point where a recipient is big enough to run a real careers site.
PAGES_PER_AGENCY = 3

# Entity-name fragments that indicate something that isn't a hiring employer: joint ventures
# and pass-through vehicles that award work but employ nobody under that name.
_NON_EMPLOYER_MARKERS = ("joint venture", " jv", "jv ", " j/v")


# Acronyms and short names can't be matched to legal entity names by any string rule --
# "SAIC" shares no token with "SCIENCE APPLICATIONS INTERNATIONAL CORPORATION", and the
# national labs contract under their operating-company names ("LAWRENCE LIVERMORE NATIONAL
# SECURITY, LLC" runs LLNL). Without this table those show up as candidates even though
# they're already tracked. Each group is a set of names for one employer: if any member is
# in companies.json, a federal-award hit on any other member is treated as already tracked.
ALIAS_GROUPS = [
    {"SAIC", "Science Applications International"},
    {"Draper", "Charles Stark Draper Laboratory"},
    {"LLNL", "Lawrence Livermore National Security", "Lawrence Livermore National Laboratory"},
    {"Sandia", "National Technology and Engineering Solutions of Sandia"},
    {"Los Alamos", "Triad National Security"},
    {"APL", "Johns Hopkins University Applied Physics Laboratory"},
    {"JHU APL", "Johns Hopkins University Applied Physics Laboratory"},
    {"GTRI", "Georgia Tech Applied Research", "Georgia Tech Research"},
    {"MIT Lincoln Laboratory", "Massachusetts Institute of Technology"},
    {"HII", "Huntington Ingalls", "HII Mission Technologies"},
    {"GDIT", "General Dynamics Information Technology"},
    {"BAH", "Booz Allen Hamilton"},
    {"PSU ARL", "Pennsylvania State University"},
    {"ARLIS", "University of Maryland"},
    {"IBM", "International Business Machines"},
    {"Raytheon", "RTX", "Raytheon Technologies"},
    {"BAE Systems", "BAE Systems Technology Solutions & Services"},
    {"L3 Harris", "L3Harris Technologies"},
    {"Northrop Grumman", "Northrop Grumman Systems"},
]


def _alias_expansions(tracked: list[str]) -> list[str]:
    """Every alias of a tracked company, so award-data legal names resolve to it."""
    out = []
    for group in ALIAS_GROUPS:
        if any(same_company(t, member) for t in tracked for member in group):
            out.extend(group)
    return out


def _looks_like_employer(name: str) -> bool:
    low = (name or "").lower()
    return bool(low.strip()) and not any(m in low for m in _NON_EMPLOYER_MARKERS)


def tracked_companies() -> list[str]:
    with open(CONFIG_PATH) as f:
        return json.load(f)["companies"]


def fetch_recipients(agency: str, pages: int = PAGES_PER_AGENCY,
                     start_date: str = "2024-01-01", end_date: str = "2026-12-31",
                     client: httpx.Client | None = None) -> list[dict]:
    """Top contract recipients for one agency, newest-first by obligated amount. Returns
    [] on any API failure -- a candidate list is a nice-to-have, never worth raising into
    a caller that's mid-refresh."""
    owns_client = client is None
    client = client or httpx.Client(timeout=60.0)
    out = []
    try:
        for page in range(1, pages + 1):
            body = {
                "filters": {
                    "time_period": [{"start_date": start_date, "end_date": end_date}],
                    "award_type_codes": AWARD_TYPE_CODES,
                    "agencies": [{"type": "awarding", "tier": "toptier", "name": agency}],
                    # NOTE: this filter takes a flat list of code prefixes. The tiered
                    # {"require": [[...]]} form used by other USAspending filters is
                    # rejected here with a 422 whose message misleadingly says an object
                    # is valid.
                    "naics_codes": NAICS_CODES,
                },
                "category": "recipient",
                "limit": 100,
                "page": page,
            }
            resp = client.post(API_URL, json=body)
            resp.raise_for_status()
            data = resp.json()
            for r in data.get("results", []):
                out.append({"name": r.get("name") or "", "amount": r.get("amount") or 0.0,
                            "agency": agency})
            if not data.get("page_metadata", {}).get("hasNext"):
                break
    except Exception as e:
        print(f"[candidates] USAspending fetch failed for {agency}: {e}")
    finally:
        if owns_client:
            client.close()
    return out


def discover(verbose: bool = False) -> dict:
    """Fetches recipients, drops the ones already tracked, and upserts the rest as
    candidates. Returns a summary dict."""
    tracked = tracked_companies()
    known = tracked + _alias_expansions(tracked)

    recipients = []
    for agency in AGENCIES:
        rows = fetch_recipients(agency)
        if verbose:
            print(f"[candidates] {agency}: {len(rows)} recipients")
        recipients.extend(rows)

    # Collapse duplicates: the same parent appears under multiple UEIs and legal-name
    # variants ("LOCKHEED MARTIN CORP" / "LOCKHEED MARTIN CORPORATION"). Keep the largest
    # award total and the name that came with it.
    best: dict[tuple, dict] = {}
    for r in recipients:
        if not _looks_like_employer(r["name"]):
            continue
        key = company_tokens(r["name"])
        if not key:
            continue
        if key not in best or r["amount"] > best[key]["amount"]:
            best[key] = r

    candidates = [r for r in best.values()
                  if not any(same_company(t, r["name"]) for t in known)]
    candidates.sort(key=lambda r: r["amount"], reverse=True)

    for r in candidates:
        db.upsert_candidate_company(r["name"], source=f"usaspending:{r['agency']}",
                                    award_amount=r["amount"])

    # Reconcile: companies.json and ALIAS_GROUPS change underneath the stored list, so a row
    # written by an earlier run may no longer be a candidate (the user added that company, or
    # an alias now resolves its legal name). Without this, suggestions the user already acted
    # on linger in the panel forever -- upsert alone can't retract anything.
    pruned = 0
    for stored in db.all_candidate_company_names():
        if any(same_company(t, stored) for t in known):
            db.delete_candidate_company(stored)
            pruned += 1

    summary = {"recipients": len(recipients), "distinct": len(best),
               "already_tracked": len(best) - len(candidates), "candidates": len(candidates),
               "pruned": pruned}
    if verbose:
        print(f"[candidates] {summary}")
    return summary


def add_to_tracker(name: str) -> bool:
    """Appends a candidate to config/companies.json so the next refresh scrapes it, and
    marks it added. Returns False if it's already there (by strict name match)."""
    with open(CONFIG_PATH) as f:
        cfg = json.load(f)
    if any(same_company(t, name) for t in cfg["companies"]):
        db.mark_candidate_added(name)
        return False
    cfg["companies"].append(name)
    cfg["companies"].sort(key=str.lower)
    tmp = CONFIG_PATH.with_suffix(".json.tmp")
    with open(tmp, "w") as f:
        json.dump(cfg, f, indent=2)
        f.write("\n")
    tmp.replace(CONFIG_PATH)  # atomic -- never leave a half-written company list on disk
    db.mark_candidate_added(name)
    return True
