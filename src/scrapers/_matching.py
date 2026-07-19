"""Shared helper used by scraper probes to sanity-check that a resolved ATS
board actually belongs to the target company, not an unrelated company that
happens to share a guessed slug.
"""
import re
import difflib


def normalize(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def names_match(target: str, candidate: str, threshold: float = 0.5) -> bool:
    a, b = normalize(target), normalize(candidate)
    if not a or not b:
        return False
    if a in b or b in a:
        return True
    return difflib.SequenceMatcher(None, a, b).ratio() >= threshold


# Legal-form noise only ("Inc", "LLC", "Corp"), not descriptive words. Adding something like
# "technologies" or "systems" here would collapse "Trident Systems" into "Trident".
_LEGAL_SUFFIXES = {
    "inc", "incorporated", "llc", "llp", "lp", "ltd", "limited", "corp", "corporation",
    "company", "co", "plc", "gmbh", "sa", "nv", "ag", "holdings", "holding",
}


def company_tokens(name: str) -> tuple[str, ...]:
    """Identity tokens for a company name: lowercased words, a leading "the" dropped, and
    trailing legal suffixes stripped. "The Aerospace Corporation" -> ("aerospace",)."""
    t = re.findall(r"[a-z0-9]+", (name or "").lower())
    if t and t[0] == "the":
        t = t[1:]
    while t and t[-1] in _LEGAL_SUFFIXES:
        t = t[:-1]
    return tuple(t)


def same_company(a: str, b: str) -> bool:
    """True when one name's tokens are a leading run of the other's, anchored at the first
    token -- so "Lockheed Martin" == "LOCKHEED MARTIN CORP", and "General Dynamics" also
    covers "General Dynamics Mission Systems", but "General Atomics" never matches
    "General Dynamics".

    The prefix rule is intentional: it rolls subsidiary entities up into the parent, so
    tracking "General Dynamics" suppresses GD Mission Systems / GD IT / GD One Source as
    separate candidates rather than listing the same employer four times. The cost is that
    a short tracked name also absorbs an unrelated company sharing its first token
    ("STR" would swallow a hypothetical "STR Systems"). That errs toward hiding a candidate
    rather than polluting the tracked list, which is the cheaper mistake here -- but it is
    why this is a discovery-time filter and not an identity function.

    Use this, NOT names_match, when cross-matching two lists of company names.
    names_match is deliberately fuzzy because it answers a different question -- "is this
    ATS board plausibly the company I guessed?", where one bad match costs a retry. Its
    difflib ratio is dominated by shared legal suffixes when comparing arbitrary pairs:
    "ELECTRIC BOAT CORPORATION" scores over threshold against "Aerospace Corporation", and
    "BOOZ ALLEN HAMILTON INC" against "Stellarvision Inc." On a 245x165 cross-match that
    produced wrong answers on 8 of 11 hand-checked pairs; this function got 11 of 11.
    """
    x, y = company_tokens(a), company_tokens(b)
    if not x or not y:
        return False
    n = min(len(x), len(y))
    return x[:n] == y[:n]


def confirms_identity(target: str, org_name: str) -> bool:
    """Stricter than same_company, for verifying an ATS board's self-reported org name
    against the company we were looking for. same_company's prefix rule rolls a subsidiary
    up to its parent, but here that's a liability: a Greenhouse board slug "agile" that
    belongs to an unrelated company literally named "Agile" reports org name "Agile", and
    same_company("Agile Mission Integration", "Agile") is True on the shared first token.

    This requires the two names to be equal after tokenization, OR one to be a full prefix
    of the other with at least two shared tokens -- so "General Dynamics" still confirms
    "General Dynamics Mission Systems", but a lone "Agile" no longer confirms a three-word
    target. (Single-token company names never reach the weak-slug path that needs this --
    their only slug IS their full name -- so demanding two tokens here is safe.)"""
    a, b = company_tokens(target), company_tokens(org_name)
    if not a or not b:
        return False
    if a == b:
        return True
    n = min(len(a), len(b))
    return n >= 2 and a[:n] == b[:n]


def verify_board(company_name: str | None, org_name: str | None,
                 require_verification: bool) -> bool:
    """Shared accept/reject decision for every ATS probe once it has resolved a live board
    and (if the platform exposes one) the board's self-reported org name.

    - A name that's present but doesn't confirm identity is always rejected -- that's a
      real collision, regardless of slug strength.
    - When require_verification is set (the slug is a weak guess: a bare first word or an
      acronym), a board that exposes NO org name is rejected too -- a weak slug is only
      trustworthy when the platform can positively confirm who it belongs to. Lever and
      Rippling expose no org name at all, so weak slugs never resolve on them (by design).
    """
    if company_name and org_name and not confirms_identity(company_name, org_name):
        return False
    if require_verification and not org_name:
        return False
    return True
