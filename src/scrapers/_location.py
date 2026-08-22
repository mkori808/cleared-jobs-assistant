"""Best-effort parser for free-text location strings into city/state/country/remote.
Handles common patterns like 'Arlington, VA', 'Austin, TX, United States', 'Remote - US',
'Remote', 'London, United Kingdom'.
"""
import re

US_STATE_ABBR = {
    "AL","AK","AZ","AR","CA","CO","CT","DE","FL","GA","HI","ID","IL","IN","IA","KS","KY","LA",
    "ME","MD","MA","MI","MN","MS","MO","MT","NE","NV","NH","NJ","NM","NY","NC","ND","OH","OK",
    "OR","PA","RI","SC","SD","TN","TX","UT","VT","VA","WA","WV","WI","WY","DC",
}

US_STATE_NAME_TO_ABBR = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR", "california": "CA",
    "colorado": "CO", "connecticut": "CT", "delaware": "DE", "florida": "FL", "georgia": "GA",
    "hawaii": "HI", "idaho": "ID", "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS",
    "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD", "massachusetts": "MA",
    "michigan": "MI", "minnesota": "MN", "mississippi": "MS", "missouri": "MO", "montana": "MT",
    "nebraska": "NE", "nevada": "NV", "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM",
    "new york": "NY", "north carolina": "NC", "north dakota": "ND", "ohio": "OH", "oklahoma": "OK",
    "oregon": "OR", "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC",
    "south dakota": "SD", "tennessee": "TN", "texas": "TX", "utah": "UT", "vermont": "VT",
    "virginia": "VA", "washington": "WA", "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY",
    "district of columbia": "DC", "washington, dc": "DC", "washington dc": "DC",
    "washington d.c.": "DC", "washington, d.c.": "DC",
    "d.c.": "DC", "d.c": "DC", "d. c.": "DC",
}


def normalize_state(state: str | None) -> str | None:
    """Converts a full US state name to its two-letter abbreviation when recognized,
    so different scrapers' location fields (e.g. 'Maryland' vs 'MD') don't fragment
    the location filter dropdown. Leaves non-US or already-abbreviated values as-is."""
    if not state:
        return state
    if state.upper() in US_STATE_ABBR:
        return state.upper()
    return US_STATE_NAME_TO_ABBR.get(state.strip().lower(), state)


# Different scrapers (and the LLM extraction path) express the same country in different
# ways -- "US", "USA", "U.S.A", "us" all mean the same thing as "United States", but land
# in the DB as distinct strings and fragment the country filter dropdown into duplicates.
COUNTRY_ALIASES = {
    "us": "United States", "usa": "United States", "u.s.a": "United States",
    "u.s": "United States", "united states of america": "United States",
    "united states": "United States",
    "uk": "United Kingdom", "gb": "United Kingdom", "united kingdom": "United Kingdom",
    "uae": "United Arab Emirates", "united arab emirates": "United Arab Emirates",
    "can": "Canada", "canada": "Canada",
    # ISO-2 codes with no US-state-abbreviation collision, so safe to resolve unconditionally
    # (unlike e.g. "IN" or "CO", which are ambiguous with Indiana/Colorado in this US-heavy
    # dataset and are deliberately left to the leaked-state check instead).
    "ie": "Ireland", "mx": "Mexico", "ch": "Switzerland", "jp": "Japan",
    "sg": "Singapore", "cn": "China", "sa": "Saudi Arabia", "ae": "United Arab Emirates",
    "qa": "Qatar", "my": "Malaysia", "th": "Thailand", "nz": "New Zealand",
}

# Noise commonly appended to a location field by scrapers/LLM extraction that doesn't
# affect the underlying country/state value: parenthetical remarks ("(Hybrid)", "(Remote)"),
# "or Remote", and semicolon-joined multi-location strings (take the first location only).
_TRAILING_NOISE_RE = re.compile(r"\s*\(.*?\)|\s+or\s+remote\b", re.I)

# Full country names as they appear verbatim in a raw location string, either as the sole
# token for a country-level posting ("Singapore") or as the leading token in a "Country, City"
# format (Intel's Workday text "Malaysia, Kulim") -- without this, the first branch below reads
# straight into "city", so a country name silently lands in the city filter instead (and, for
# the 2-part case, whatever's left lands in the country field, e.g. country="Kulim").
KNOWN_COUNTRY_NAMES = {
    "united states", "united kingdom", "united arab emirates", "ireland", "germany", "india",
    "japan", "australia", "france", "canada", "mexico", "singapore", "china", "netherlands",
    "switzerland", "israel", "poland", "romania", "portugal", "spain", "italy", "sweden",
    "south korea", "taiwan", "philippines", "costa rica", "brazil", "argentina", "chile",
    "colombia", "saudi arabia", "qatar", "malaysia", "thailand", "new zealand", "belgium",
    "austria", "denmark", "norway", "finland", "greece", "hungary", "czech republic", "vietnam",
    "indonesia", "south africa", "egypt", "turkey", "luxembourg",
} | set(COUNTRY_ALIASES.keys()) | {v.lower() for v in COUNTRY_ALIASES.values()}

# Placeholder text some ATSs use in place of a real location: Workday's "N Locations" summary
# for multi-site postings (the individual sites aren't resolvable from this string) and EY's
# "Anywhere in Country At EY" for nationwide-remote roles. Both used to fall straight into the
# single-token "city" branch below, polluting the city filter with fake entries.
_PLACEHOLDER_LOCATION_RE = re.compile(r"^\d+\s+locations?$|^anywhere\b", re.I)

# iCIMS commonly encodes location as "US-VA-Chantilly" (country code - state code - city,
# dash-joined, no spaces/commas) -- e.g. Peraton, LMI, TekSynap, IBM. With no comma to split
# on, this used to fall into the single-token branch below and dump the whole raw string in
# as a garbled "city" ("US-VA-Chantilly"), leaving state/country blank. Matched narrowly (two
# short all-letter codes before the city) so it doesn't misfire on unrelated dash-containing
# strings like "New York-based" or a company name.
_DASH_COUNTRY_STATE_CITY_RE = re.compile(r"^([A-Za-z]{2})-([A-Za-z]{2,3})-(.+)$")


def _clean_location_fragment(raw: str) -> str:
    fragment = raw.split(";")[0]
    fragment = _TRAILING_NOISE_RE.sub("", fragment)
    return fragment.strip().rstrip(")").strip()


def normalize_country(raw: str | None) -> str | None:
    """Canonicalizes country spelling variants (see COUNTRY_ALIASES) so the country filter
    doesn't fragment into duplicates like 'US' / 'USA' / 'United States'."""
    if not raw:
        return raw
    cleaned = _clean_location_fragment(raw)
    key = cleaned.strip(".").lower()
    return COUNTRY_ALIASES.get(key, cleaned or raw.strip())


def extract_leaked_state(raw: str | None) -> str | None:
    """Detects when a 'country' field actually holds a US state (name or abbreviation,
    possibly with noise like 'CA (Hybrid)') -- a symptom of scrapers/LLM extraction
    misplacing state info, not a real country. Returns the state's 2-letter abbreviation
    if so, else None."""
    if not raw:
        return None
    cleaned = _clean_location_fragment(raw)
    if cleaned.upper() in US_STATE_ABBR:
        return cleaned.upper()
    return US_STATE_NAME_TO_ABBR.get(cleaned.lower())


REMOTE_RE = re.compile(r"\bremote\b", re.I)


def _is_known_country(token: str) -> bool:
    return _clean_location_fragment(token).rstrip(".").lower() in KNOWN_COUNTRY_NAMES


def _match_state(token: str) -> str | None:
    """Recognizes a state token whether given as an abbreviation ('TX') or full name
    ('Texas') -- multi-location postings (e.g. Deloitte's "City, State, US; City2, ...")
    commonly spell states out in full, which the abbreviation-only check used to miss.
    Cleaned first since a state token commonly carries trailing noise like "VA (Hybrid)"
    or "D.C. (Remote in Continental U.S. considered)" that would otherwise block the match."""
    token = _clean_location_fragment(token)
    if token.upper() in US_STATE_ABBR:
        return token.upper()
    return US_STATE_NAME_TO_ABBR.get(token.lower())


def parse_location(raw: str) -> dict:
    if not raw:
        return {"city": None, "state": None, "country": None, "remote": False}

    remote = bool(REMOTE_RE.search(raw))
    # Some scrapers join multiple valid locations for one posting with "; " (e.g. Deloitte
    # roles open to 19 offices), " or " (e.g. "Seattle, WA or McLean, VA or Remote (USA)"), or
    # " | " (iCIMS, e.g. "US-VA-Chantilly | US-VA-Springfield") -- there's no single "correct"
    # location to store structured fields for, so take just the first one rather than letting
    # its parts bleed into the parsing below (which previously mangled a trailing "... or
    # Remote (USA)" down to a bare state code landing in the country field, and separately let
    # the dash-format branch's greedy city match swallow a second "|"-joined location whole).
    first_location = re.split(r";|\s+or\s+|\s*\|\s*", raw, flags=re.I)[0].strip()
    if _PLACEHOLDER_LOCATION_RE.match(first_location):
        return {"city": None, "state": None, "country": None, "remote": remote}

    dash_match = _DASH_COUNTRY_STATE_CITY_RE.match(first_location)
    if dash_match:
        country_code, region_code, city_part = dash_match.groups()
        matched_state = _match_state(region_code)
        if country_code.upper() == "US" and matched_state:
            return {"city": city_part.strip(), "state": matched_state,
                    "country": "United States", "remote": remote}

    parts = [p.strip() for p in first_location.split(",") if p.strip()]

    city = state = country = None
    if len(parts) == 1:
        token = parts[0]
        matched_state = _match_state(token)
        if matched_state:
            state, country = matched_state, "United States"
        elif _is_known_country(token):
            country = normalize_country(token)
        elif not REMOTE_RE.search(token):
            city = token
    elif len(parts) == 2:
        first, second = parts
        if _is_known_country(first):
            # "Country, City" (e.g. Intel's Workday text "Malaysia, Kulim") -- without this
            # check the country name would land in "city" and whatever's left (a real city)
            # would get misread as "country" below.
            country = normalize_country(first)
            if second.strip().lower() != "remote":
                city = second
        else:
            city = first
            matched_state = _match_state(second)
            if matched_state:
                state, country = matched_state, "United States"
            else:
                country = normalize_country(second)
    elif len(parts) >= 3:
        if _is_known_country(parts[0]):
            # Leading-country formats disagree on where the state sits: "Country, State, City"
            # (Intel's "US, Arizona, Phoenix") puts it right after the country, but "Country,
            # City, State" (Thomson Reuters' "United States of America, Eagan, Minnesota")
            # puts it last. Try both positions and go with whichever actually matches a real
            # state -- assuming one fixed order silently swapped city/state for the other.
            country = normalize_country(parts[0])
            state_mid, state_last = _match_state(parts[1]), _match_state(parts[-1])
            if state_last and not state_mid:
                state, city = state_last, parts[1]
            else:
                state, city = state_mid, parts[-1]
        else:
            city, second, third = parts[0], parts[1], parts[-1]
            matched_state = _match_state(second)
            if matched_state:
                state = matched_state
            country = normalize_country(third)

    return {"city": city, "state": state, "country": country, "remote": remote}
