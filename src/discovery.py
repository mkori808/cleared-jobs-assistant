"""Given just a company name, try to figure out which ATS they use and what their
slug/board-id is, by generating plausible slug candidates and probing each known
ATS's public API until one resolves.
"""
import re
import httpx

from .scrapers import greenhouse, lever, ashby, smartrecruiters, rippling, bamboohr, workable

# Order matters: cheaper/more-common platforms first.
ATS_PROBES = [
    ("greenhouse", greenhouse.probe),
    ("lever", lever.probe),
    ("ashby", ashby.probe),
    ("smartrecruiters", smartrecruiters.probe),
    ("rippling", rippling.probe),
    ("bamboohr", bamboohr.probe),
    ("workable", workable.probe),
]


def _slug_words(name: str) -> list[str]:
    base = re.sub(r"\b(inc\.?|llc|ltd\.?|corp\.?|corporation|company|co\.?)\b", "", name.strip(), flags=re.I)
    base = re.sub(r"[.,/]", " ", base)
    return re.findall(r"[A-Za-z0-9]+", base)


def _dedupe(cands: list[str]) -> list[str]:
    # drop anything <=3 chars: short/generic slugs most often collide with an unrelated
    # real company already registered under that identifier on a given ATS.
    seen, out = set(), []
    for c in cands:
        if c and c not in seen and len(c) > 3:
            seen.add(c)
            out.append(c)
    return out


def strong_slugs(name: str) -> list[str]:
    """Slugs derived from the *whole* name (all words joined or hyphenated). A full-name
    slug resolving to a board is very unlikely to be a different company, so these are
    trusted without an org-name confirmation."""
    words = _slug_words(name)
    if not words:
        return []
    joined = "".join(w.lower() for w in words)
    hyphenated = "-".join(w.lower() for w in words)
    return _dedupe([joined, hyphenated])


def weak_slugs(name: str) -> list[str]:
    """Slugs that throw away most of the name -- a bare first word, or an acronym. These
    routinely collide with unrelated companies (a "trase"/"agile"/"applied" board owned by
    someone else), so a match on one of these is only accepted when the ATS positively
    confirms the org name. For a single-word company the first word IS the whole name, so
    there are no weak slugs -- it's covered by strong_slugs."""
    words = _slug_words(name)
    if len(words) < 2:
        return []
    first_word = words[0].lower()
    acronym = "".join(w[0].lower() for w in words)
    strong = set(strong_slugs(name))
    return [s for s in _dedupe([first_word, acronym]) if s not in strong]


def discover(company_name: str, client: httpx.Client) -> dict | None:
    """Returns {"ats": ..., "identifier": ...} on success, or None if nothing resolved.

    Full-name slugs are tried first and trusted on existence alone. Only if none resolve do
    we fall back to weak slugs (first word / acronym), and those must be confirmed by the
    board's own reported org name -- see _matching.verify_board. This ordering is what keeps
    a company like Applied Research Associates off Applied Intuition's "applied" board."""
    attempts = [(s, False) for s in strong_slugs(company_name)]
    attempts += [(s, True) for s in weak_slugs(company_name)]
    for slug, require_verification in attempts:
        for ats_name, probe_fn in ATS_PROBES:
            try:
                if probe_fn(slug, client, company_name=company_name,
                            require_verification=require_verification):
                    return {"ats": ats_name, "identifier": slug}
            except Exception:
                continue
    return None
