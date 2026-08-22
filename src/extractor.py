"""Extracts security-clearance and citizenship signals from job description text."""
import html as html_module
import json
import re
import time

import anthropic

from .scrapers._location import US_STATE_ABBR, normalize_state

# In-app LLM calls (job-info extraction, resume match scoring) run on Haiku -- these are
# high-volume, low-complexity extraction/classification tasks, not worth Opus-tier cost.
LLM_MODEL = "claude-haiku-4-5"

# Bump this whenever a change to extraction logic would produce different results for
# already-stored jobs -- regex patterns here (CLEARANCE_PATTERNS, EQUITY_RE, SALARY_RANGE_RE,
# strip_html), or a scraper's own field-derivation logic (e.g. how it maps raw ATS location
# data to city/state/country). refresh.py compares this against each job's stored
# extraction_version and forces re-extraction on a mismatch even if the description text is
# unchanged -- otherwise the "skip re-work when text hasn't changed" optimization means a fix
# here silently never applies to jobs already scraped before it landed, and stays wrong until
# someone remembers to run a manual backfill (this happened repeatedly before this existed).
# Rows last written by the manual "Re-parse with AI" pass are exempt (see refresh.py) so a
# version bump for a regex fix doesn't clobber deliberately LLM-curated data with a cheaper
# regex re-extraction.
EXTRACTION_VERSION = 12

# "Clearance Type: X" / "Clearance Required: X" is a common structured-field format on ATS
# platforms like Workday (e.g. Northrop Grumman), separate from prose phrasing like "secret
# clearance" -- each level pattern below has a "clearance type:" alternate to catch it.
CT = r"\bclearance\s*(?:type|level|required)?\s*:\s*"

# Ordered roughly by rank so we can report the "highest" clearance mentioned.
CLEARANCE_PATTERNS = [
    ("TS/SCI + Polygraph", re.compile(r"(?:\bTS[\s/-]*SCI\b|\btop[\s-]*secret\s*/?\s*sci\b).{0,40}\bpoly(graph)?\b", re.I | re.S)),
    ("TS/SCI", re.compile(rf"\bTS[\s/-]*SCI\b|\btop[\s-]*secret\s*/?\s*sci\b|{CT}ts[\s/-]*sci\b", re.I)),
    ("Top Secret", re.compile(rf"\btop[\s-]*secret\b|{CT}top[\s-]*secret\b", re.I)),
    # DOE clearances -- Q is roughly TS-equivalent, L roughly Secret-equivalent. The "Q/L" and
    # "L/Q" alternations catch the common combined phrasing ("must hold a Q or L clearance").
    ("Q Clearance", re.compile(
        rf"\bDOE\s+Q\b|\bQ[\s-]?(?:level\s+)?clearance\b|\bQ\s+access\s+authorization\b"
        rf"|\bQ\s*(?:/|or)\s*L\s+clearance\b|{CT}Q\b", re.I)),
    ("Secret", re.compile(rf"(?<!top )(?<!top-)\bsecret clearance\b|\bactive secret\b|\bDoD secret\b|{CT}secret\b", re.I)),
    ("L Clearance", re.compile(
        rf"\bDOE\s+L\b|\bL[\s-]?(?:level\s+)?clearance\b|\bL\s+access\s+authorization\b"
        rf"|\bL\s*(?:/|or)\s*Q\s+clearance\b|{CT}L\b", re.I)),
    ("Public Trust", re.compile(rf"\bpublic trust\b|{CT}public\s*trust\b", re.I)),
    ("Confidential", re.compile(rf"\bconfidential clearance\b|{CT}confidential\b", re.I)),
    ("Interim Clearance", re.compile(r"\binterim (secret|top secret|clearance)\b", re.I)),
    ("Clearance eligible / clearable", re.compile(r"\bclearance[- ]?eligible\b|\bable to obtain (a )?clearance\b|\bmust be able to obtain\b", re.I)),
    ("Security Clearance (unspecified)", re.compile(r"\bsecurity clearance\b", re.I)),
]

POLYGRAPH_RE = re.compile(r"\bpoly(graph)?\b", re.I)
CITIZENSHIP_RE = re.compile(
    r"\bU\.?S\.?\s*citizen(ship)?\b|\bmust be a citizen\b|\bcitizenship required\b",
    re.I,
)

SNIPPET_WINDOW = 160  # chars of context to keep around a match, for the dashboard


def extract(description_text: str) -> dict:
    """Returns clearance_level, clearance_keywords (matched phrases w/ context), citizenship_required, polygraph_mentioned."""
    text = description_text or ""
    matched_level = None
    matched_snippets = []

    for label, pattern in CLEARANCE_PATTERNS:
        m = pattern.search(text)
        if m:
            if matched_level is None:
                matched_level = label
            start = max(0, m.start() - SNIPPET_WINDOW // 2)
            end = min(len(text), m.end() + SNIPPET_WINDOW // 2)
            snippet = text[start:end].strip()
            if snippet not in matched_snippets:
                matched_snippets.append(snippet)

    polygraph = bool(POLYGRAPH_RE.search(text))
    citizenship = bool(CITIZENSHIP_RE.search(text))

    return {
        "clearance_level": matched_level or "None mentioned",
        "clearance_keywords": matched_snippets,
        "citizenship_required": citizenship,
        "polygraph_mentioned": polygraph,
    }


# "Equity" alone is ambiguous -- pay-transparency salary disclosures routinely list "internal
# equity" (pay fairness among existing staff) as a factor in setting the offered salary, and
# EEO boilerplate mentions "diversity, equity, and inclusion" -- neither is stock compensation.
# Excluded via lookaround rather than requiring a positive keyword (e.g. "equity grant") since
# real stock-equity mentions are phrased too many different ways to enumerate positively.
EQUITY_RE = re.compile(
    r"(?<!internal )(?<!pay )(?<!home )(?<!sweat )(?<!diversity, )(?<!diversity and )(?<!diversity & )"
    r"\bequity\b(?!\s*[,&]?\s*(?:and\s+)?(?:diversity|inclusion))"
    r"|\bstock options?\b|\bRSUs?\b|\brestricted stock\b|\bstock grants?\b",
    re.I,
)

# Matches "$120,000 - $150,000", "$120K-$150K", "$45/hr - $60/hr", "$130,000 — $155,000 USD",
# and "$199,500 USD - $370,500 USD" (Thomson Reuters -- currency code repeated after *each*
# number, not just trailing the whole range), with optional "per year"/"annually". Covers
# hyphen, en dash, em dash, minus sign, and "to".
DASH = r"(?:-|–|—|−|to)"
CURRENCY = r"(?:USD|CAD|GBP|EUR|AUD)"
SALARY_RANGE_RE = re.compile(
    r"\$\s?(?P<min>[\d,]+(?:\.\d+)?)\s?(?P<min_k>[kK])?"
    r"(?:\s*(?:/|per)\s*(?:hour|hr|year|yr|annum))?"
    rf"(?:\s*{CURRENCY})?"
    rf"\s*{DASH}\s*\$?\s?(?P<max>[\d,]+(?:\.\d+)?)\s?(?P<max_k>[kK])?"
    r"(?:\s*(?:per|/)\s*(?P<interval>hour|hr|year|yr|annum))?"
    rf"(?:\s*(?P<currency>{CURRENCY}))?",
    re.I,
)

# Some postings (e.g. NVIDIA: "The base salary range is 152,000 USD - 241,500 USD") state the
# range as bare numbers with the currency code as a trailing word instead of a "$" prefix.
# Kept as a separate fallback pattern (tried only if SALARY_RANGE_RE finds nothing) rather than
# folded into it, since making the "$" itself optional there would make it match far more
# loosely and start misfiring on unrelated number ranges that have nothing to do with salary.
SALARY_RANGE_NO_SYMBOL_RE = re.compile(
    rf"(?P<min>[\d,]+(?:\.\d+)?)\s?(?P<min_k>[kK])?\s*{CURRENCY}"
    rf"\s*{DASH}\s*(?P<max>[\d,]+(?:\.\d+)?)\s?(?P<max_k>[kK])?\s*(?P<currency>{CURRENCY})"
    r"(?:\s*(?:per|/)\s*(?P<interval>hour|hr|year|yr|annum))?",
    re.I,
)

# "This role is between $225,000 and $290,000 base salary." (Sphinx Defense) -- prose "between
# ... and ..." phrasing instead of a dash-separated range. Requiring the leading "between" (not
# just loosening the DASH separator to include "and") keeps this from mispairing two unrelated
# dollar figures elsewhere in a posting, e.g. "$500 wellness stipend and $200 commuter benefit".
SALARY_BETWEEN_RE = re.compile(
    r"\bbetween\s+\$\s?(?P<min>[\d,]+(?:\.\d+)?)\s?(?P<min_k>[kK])?"
    r"(?:\s*(?:/|per)\s*(?:hour|hr|year|yr|annum))?"
    r"\s+and\s+\$?\s?(?P<max>[\d,]+(?:\.\d+)?)\s?(?P<max_k>[kK])?"
    r"(?:\s*(?:per|/)\s*(?P<interval>hour|hr|year|yr|annum))?"
    rf"(?:\s*(?P<currency>{CURRENCY}))?",
    re.I,
)


def _to_number(raw: str, is_k: bool) -> float:
    val = float(raw.replace(",", ""))
    return val * 1000 if is_k else val


def extract_salary(text: str) -> dict:
    """Best-effort salary range parse from free text. Returns None values if nothing found."""
    text = text or ""
    m = SALARY_RANGE_RE.search(text) or SALARY_BETWEEN_RE.search(text) or SALARY_RANGE_NO_SYMBOL_RE.search(text)
    if not m:
        return {"salary_min": None, "salary_max": None, "salary_currency": None, "salary_interval": None}
    salary_min = _to_number(m.group("min"), bool(m.group("min_k")))
    salary_max = _to_number(m.group("max"), bool(m.group("max_k")))
    interval_raw = (m.group("interval") or "").lower()
    interval = "hour" if interval_raw in ("hour", "hr") else ("year" if interval_raw in ("year", "yr", "annum") else None)
    # If neither number looks like an annual salary (i.e. both are small, no 'k' suffix) and
    # no interval was stated, assume hourly -- otherwise assume yearly (most common in US postings).
    if interval is None:
        interval = "hour" if salary_max < 500 else "year"
    currency = (m.group("currency") or "USD").upper()
    return {"salary_min": salary_min, "salary_max": salary_max, "salary_currency": currency, "salary_interval": interval}


EQUITY_PCT_RANGE_RE = re.compile(r"\d+(?:\.\d+)?\s*%\s*(?:-|–|to)\s*\d+(?:\.\d+)?\s*%")


def extract_equity(text: str) -> dict:
    text = text or ""
    m = EQUITY_PCT_RANGE_RE.search(text)
    if m:
        start = max(0, m.start() - 60)
        end = min(len(text), m.end() + 20)
        return {"equity_mentioned": True, "equity_text": text[start:end].strip()}
    m = EQUITY_RE.search(text)
    if not m:
        return {"equity_mentioned": False, "equity_text": None}
    start = max(0, m.start() - 80)
    end = min(len(text), m.end() + 80)
    return {"equity_mentioned": True, "equity_text": text[start:end].strip()}


_CLEARANCE_LEVELS = [label for label, _ in CLEARANCE_PATTERNS] + ["None mentioned"]

_EXTRACTION_SCHEMA = {
    "type": "object",
    "properties": {
        "clearance_level": {"type": "string", "enum": _CLEARANCE_LEVELS},
        "clearance_snippet": {"type": ["string", "null"], "description": "Short quote from the text supporting clearance_level, or null."},
        "citizenship_required": {"type": "boolean"},
        "polygraph_mentioned": {"type": "boolean"},
        "salary_min": {"type": ["number", "null"]},
        "salary_max": {"type": ["number", "null"]},
        "salary_currency": {"type": ["string", "null"]},
        "salary_interval": {"anyOf": [{"type": "string", "enum": ["hour", "year"]}, {"type": "null"}]},
        "city": {"type": ["string", "null"], "description": "Just the city name, e.g. 'Arlington' -- never include the state or country in this field, even if the raw location string has them combined like 'Arlington, VA'."},
        "state": {"type": ["string", "null"], "description": "US state as a 2-letter abbreviation (e.g. 'VA'), or the region/province for non-US roles. Always split out from city into this separate field."},
        "country": {"type": ["string", "null"], "description": "Country, e.g. 'US', 'United Kingdom'."},
        "remote": {"type": "boolean", "description": "True if the role is remote (fully or hybrid-eligible as remote)."},
        "equity_mentioned": {"type": "boolean"},
        "equity_text": {"type": ["string", "null"], "description": "Short quote describing the equity/stock offering, or null."},
    },
    "required": [
        "clearance_level", "clearance_snippet", "citizenship_required", "polygraph_mentioned",
        "salary_min", "salary_max", "salary_currency", "salary_interval",
        "city", "state", "country", "remote", "equity_mentioned", "equity_text",
    ],
    "additionalProperties": False,
}


def llm_extract_job_info(text: str, location_text: str | None = None) -> dict | None:
    """Primary extraction path: ask an LLM to read a job posting and pull out clearance,
    citizenship, salary, location, and equity signals in one pass. Used for every newly-seen
    job during refresh, and by the manual "re-parse with AI" pass for jobs already in the DB.
    Returns None on any failure (network error, etc.) so callers can fall back to the regex
    extractors in this module as a safety net rather than losing the job's data entirely.
    """
    text = (text or "").strip()
    if not text:
        return None
    try:
        client = anthropic.Anthropic()
        location_line = f"Raw location field from the job listing: {location_text!r}\n\n" if location_text else ""
        response = client.messages.create(
            model=LLM_MODEL,
            max_tokens=512,
            output_config={"format": {"type": "json_schema", "schema": _EXTRACTION_SCHEMA}},
            messages=[{
                "role": "user",
                "content": (
                    "Read this job posting and extract security clearance, citizenship, salary, "
                    "work location, and equity/stock compensation. If nothing is mentioned, use "
                    "\"None mentioned\" for clearance_level, false for the booleans, and null for "
                    "every other field. For location, prefer the raw location field if given, but "
                    "cross-check against the posting text (e.g. a generic field like \"Multiple "
                    "Locations\" may be clarified in the body).\n\n"
                    + location_line + text[:8000]
                ),
            }],
        )
        text_block = next(b.text for b in response.content if b.type == "text")
        return json.loads(text_block)
    except Exception as e:
        print(f"[extractor] LLM extraction failed: {e}")
        return None


# How many job postings to fold into a single extraction call. Higher = fewer API round trips
# (which is most of what you're paying for in latency and per-request overhead -- the token
# cost of the descriptions themselves is roughly the same either way), but a bigger blast
# radius if one batch call fails (the whole batch falls back to regex, not just one job) and
# a bigger prompt to fit in context. 10 is a reasonable middle ground.
BATCH_SIZE = 10


def _normalize_llm_location(result: dict) -> None:
    """Defends against the model putting a combined 'City, ST' into city and leaving state
    null despite the schema and prompt asking for them separately -- splits it back apart
    in place, and normalizes state to its abbreviation either way."""
    city, state = result.get("city"), result.get("state")
    if city and not state and "," in city:
        parts = [p.strip() for p in city.split(",")]
        if len(parts) == 2 and parts[1].upper() in US_STATE_ABBR:
            result["city"], result["state"] = parts[0], parts[1].upper()
            if not result.get("country"):
                result["country"] = "US"
            return
    if state:
        result["state"] = normalize_state(state)

_BATCH_EXTRACTION_SCHEMA = {
    "type": "object",
    "properties": {
        "results": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer", "description": "0-based index matching the job's position in the input list below."},
                    **_EXTRACTION_SCHEMA["properties"],
                },
                "required": ["index"] + _EXTRACTION_SCHEMA["required"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["results"],
    "additionalProperties": False,
}


def llm_extract_job_info_batch(jobs: list[dict]) -> dict[int, dict]:
    """Same extraction as llm_extract_job_info, but for up to BATCH_SIZE postings in a single
    LLM call instead of one call per job -- the scraper already hands back every posting in
    one response, so there's no reason to round-trip the API once per job when they can be
    read together.

    jobs: list of {"text": ..., "location_text": ...}. Returns {index: result_dict} keyed by
    each job's position in the input list -- only for the jobs the model actually returned.
    An index missing from the result (parse failure, or the model dropping one under length
    pressure) should be treated as a per-job extraction failure by the caller, same as a
    single-job call returning None, and handled with the regex fallback.
    """
    if not jobs:
        return {}
    try:
        client = anthropic.Anthropic()
        parts = []
        for i, job in enumerate(jobs):
            location_line = f"Raw location field: {job['location_text']!r}\n" if job.get("location_text") else ""
            parts.append(f"--- JOB {i} ---\n{location_line}{(job.get('text') or '')[:3000]}")
        prompt = (
            "Read these job postings and extract security clearance, citizenship, salary, work "
            "location, and equity/stock compensation for EACH one. Return one object per job in "
            "\"results\", with \"index\" matching the job number below (0-based) -- return every "
            "index exactly once. If nothing is mentioned for a posting, use \"None mentioned\" "
            "for its clearance_level, false for its booleans, and null for its other fields. For "
            "location, prefer a job's raw location field when given, but cross-check against its "
            "posting text (e.g. a generic field like \"Multiple Locations\" may be clarified in "
            "the body). city, state, and country are always separate fields -- e.g. for "
            "\"Arlington, VA\", city is \"Arlington\" and state is \"VA\", never combine them "
            "into the city field.\n\n" + "\n\n".join(parts)
        )
        response = client.messages.create(
            model=LLM_MODEL,
            max_tokens=512 * len(jobs),
            output_config={"format": {"type": "json_schema", "schema": _BATCH_EXTRACTION_SCHEMA}},
            messages=[{"role": "user", "content": prompt}],
        )
        text_block = next(b.text for b in response.content if b.type == "text")
        parsed = json.loads(text_block)
        results = {}
        for r in parsed.get("results", []):
            if 0 <= r.get("index", -1) < len(jobs):
                _normalize_llm_location(r)
                results[r["index"]] = r
        return results
    except Exception as e:
        print(f"[extractor] Batch LLM extraction failed: {e}")
        return {}


_YOE_EXTRACTION_SCHEMA = {
    "type": "object",
    "properties": {
        "years_of_experience": {"anyOf": [{"type": "number"}, {"type": "null"}], "description": "Total years of professional experience as a single number. null if not determinable."},
    },
    "required": ["years_of_experience"],
    "additionalProperties": False,
}


_RESUME_MATCH_SCHEMA = {
    "type": "object",
    "properties": {
        "grade": {"type": "string", "enum": ["A", "B", "C", "D", "F"]},
        "rationale": {"type": "string", "description": "One or two sentences on why this grade, referencing specific overlaps or gaps between the resume and the posting."},
    },
    "required": ["grade", "rationale"],
    "additionalProperties": False,
}


def extract_resume_years_of_experience(resume_text: str) -> float | None:
    """Extracts total years of professional experience from a resume. Returns None on any failure."""
    resume_text = (resume_text or "").strip()
    if not resume_text:
        return None
    try:
        client = anthropic.Anthropic()
        response = client.messages.create(
            model=LLM_MODEL,
            max_tokens=100,
            output_config={"format": {"type": "json_schema", "schema": _YOE_EXTRACTION_SCHEMA}},
            messages=[{
                "role": "user",
                "content": (
                    "Extract the candidate's total years of professional experience from this resume. "
                    "Sum up years across all positions to get a single total number. "
                    "Return the number only (e.g. 8.5, 10, 5), or null if years of experience cannot be determined.\n\n"
                    + resume_text[:8000]
                ),
            }],
        )
        text_block = next(b.text for b in response.content if b.type == "text")
        data = json.loads(text_block)
        yoe = data.get("years_of_experience")
        return float(yoe) if yoe is not None else None
    except Exception as e:
        print(f"[extractor] Years of experience extraction failed: {e}")
        return None


def llm_score_resume_match(job_text: str, resume_text: str, years_of_experience: float | None = None) -> dict | None:
    """Grades how well a job posting matches a resume, A (excellent fit) to F (poor fit).
    Returns None on any failure so callers can skip and retry later.
    Retries on API overload (529) with exponential backoff."""
    job_text = (job_text or "").strip()
    resume_text = (resume_text or "").strip()
    if not job_text or not resume_text:
        return None

    yoe_line = f"\nCandidate has {years_of_experience} years of experience." if years_of_experience is not None else ""

    max_retries = 3
    for attempt in range(max_retries):
        try:
            client = anthropic.Anthropic()
            response = client.messages.create(
                model=LLM_MODEL,
                max_tokens=512,
                output_config={"format": {"type": "json_schema", "schema": _RESUME_MATCH_SCHEMA}},
                messages=[{
                    "role": "user",
                    "content": (
                        "You are screening a job posting against a candidate's resume. Grade how well "
                        "the candidate's skills and experience match the role's requirements: "
                        "A = excellent fit (meets key requirements including YOE), "
                        "B = good fit (has 75%+ of required YOE, strong on skills/clearance), "
                        "C = partial fit (has 50-75% of required YOE or some skill gaps), "
                        "D = weak fit (has <50% of required YOE or missing major qualifications), "
                        "F = not a match (fundamentally unqualified).\n\n"
                        "IMPORTANT: Years of experience matters significantly. Consider YOE as a key required qualification. "
                        "Grades should reflect YOE proportionally: if job requires 8 years and candidate has 6, that's ~75% (can be B if other factors strong). "
                        "If candidate has 4 years vs 8 required, that's ~50% (should be C). "
                        "If candidate has 2 years vs 8 required, that's ~25% (should be D). "
                        "BUT: balance YOE gaps against strong skills, relevant clearances, or exceptional background." + yoe_line + "\n\n"
                        "Required qualifications (including YOE) > nice-to-haves. Evaluate holistically.\n\n"
                        "--- RESUME ---\n" + resume_text[:10000] + "\n\n"
                        "--- JOB POSTING ---\n" + job_text[:8000]
                    ),
                }],
            )
            text_block = next(b.text for b in response.content if b.type == "text")
            return json.loads(text_block)
        except anthropic.APIStatusError as e:
            if e.status_code == 529 and attempt < max_retries - 1:
                # API overloaded -- wait and retry with exponential backoff
                wait_time = (2 ** attempt) * 2  # 2, 4, 8 seconds
                print(f"[extractor] API overloaded (529), retrying in {wait_time}s (attempt {attempt + 1}/{max_retries})")
                time.sleep(wait_time)
                continue
            print(f"[extractor] Resume match scoring failed: {e}")
            return None
        except Exception as e:
            print(f"[extractor] Resume match scoring failed: {e}")
            return None

    return None


# How many postings to score against the resume in a single call. Smaller than BATCH_SIZE
# because scoring sends far more text per job (8000 chars here vs 3000 for extraction) plus
# the resume on top -- 5 jobs lands at roughly the same prompt size as a 10-job extraction
# batch. Caching the resume prefix instead was considered and doesn't work: it's ~1500 tokens,
# well under Haiku's 4096-token minimum cacheable prefix, so it would silently never cache.
SCORE_BATCH_SIZE = 5

_BATCH_RESUME_MATCH_SCHEMA = {
    "type": "object",
    "properties": {
        "results": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer", "description": "0-based index matching the job's position in the input list below."},
                    **_RESUME_MATCH_SCHEMA["properties"],
                },
                "required": ["index"] + _RESUME_MATCH_SCHEMA["required"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["results"],
    "additionalProperties": False,
}


def llm_score_resume_match_batch(job_texts: list[str], resume_text: str,
                                 years_of_experience: float | None = None) -> dict[int, dict]:
    """Same grading as llm_score_resume_match, but for up to SCORE_BATCH_SIZE postings in a
    single LLM call instead of one call per job. The resume is the same text for every job, so
    scoring them one at a time re-sends it on every request -- batching amortizes it and cuts
    the call count by SCORE_BATCH_SIZE.

    Returns {index: result_dict} keyed by each job's position in job_texts, only for the jobs
    the model actually returned. A missing index (parse failure, or the model dropping one
    under length pressure) should be treated as a per-job failure by the caller, same as a
    single-job call returning None. Retries on API overload (529) with exponential backoff."""
    resume_text = (resume_text or "").strip()
    if not job_texts or not resume_text:
        return {}

    yoe_line = f"\nCandidate has {years_of_experience} years of experience." if years_of_experience is not None else ""
    parts = [f"--- JOB {i} ---\n{(text or '').strip()[:8000]}" for i, text in enumerate(job_texts)]

    max_retries = 3
    for attempt in range(max_retries):
        try:
            client = anthropic.Anthropic()
            response = client.messages.create(
                model=LLM_MODEL,
                max_tokens=512 * len(job_texts),
                output_config={"format": {"type": "json_schema", "schema": _BATCH_RESUME_MATCH_SCHEMA}},
                messages=[{
                    "role": "user",
                    "content": (
                        "You are screening several job postings against one candidate's resume. Grade "
                        "EACH posting on how well the candidate's skills and experience match that "
                        "role's requirements. Return one object per job in \"results\", with \"index\" "
                        "matching the job number below (0-based) -- return every index exactly once.\n\n"
                        "A = excellent fit (meets key requirements including YOE), "
                        "B = good fit (has 75%+ of required YOE, strong on skills/clearance), "
                        "C = partial fit (has 50-75% of required YOE or some skill gaps), "
                        "D = weak fit (has <50% of required YOE or missing major qualifications), "
                        "F = not a match (fundamentally unqualified).\n\n"
                        "IMPORTANT: Years of experience matters significantly. Consider YOE as a key required qualification. "
                        "Grades should reflect YOE proportionally: if a job requires 8 years and the candidate has 6, that's ~75% (can be B if other factors strong). "
                        "If the candidate has 4 years vs 8 required, that's ~50% (should be C). "
                        "If the candidate has 2 years vs 8 required, that's ~25% (should be D). "
                        "BUT: balance YOE gaps against strong skills, relevant clearances, or exceptional background." + yoe_line + "\n\n"
                        "Required qualifications (including YOE) > nice-to-haves. Evaluate each posting holistically "
                        "and independently -- grade each one against the resume, not against the other postings.\n\n"
                        "--- RESUME ---\n" + resume_text[:10000] + "\n\n"
                        + "\n\n".join(parts)
                    ),
                }],
            )
            text_block = next(b.text for b in response.content if b.type == "text")
            parsed = json.loads(text_block)
            results = {}
            for r in parsed.get("results", []):
                if 0 <= r.get("index", -1) < len(job_texts):
                    results[r["index"]] = r
            return results
        except anthropic.APIStatusError as e:
            if e.status_code == 529 and attempt < max_retries - 1:
                wait_time = (2 ** attempt) * 2  # 2, 4, 8 seconds
                print(f"[extractor] API overloaded (529), retrying in {wait_time}s (attempt {attempt + 1}/{max_retries})")
                time.sleep(wait_time)
                continue
            print(f"[extractor] Batch resume match scoring failed: {e}")
            return {}
        except Exception as e:
            print(f"[extractor] Batch resume match scoring failed: {e}")
            return {}

    return {}


def strip_html(html: str) -> str:
    """Very lightweight HTML-to-text for job descriptions (good enough for keyword scanning)."""
    # Some ATS content fields (observed on Greenhouse, e.g. Anthropic postings) store their
    # rich text already HTML-entity-escaped -- "&lt;div&gt;" as literal text instead of "<div>".
    # Left alone, the tag-stripping regexes below never fire (there are no literal "<...>"
    # characters to match) and entities like "&mdash;" break downstream regexes (e.g. salary
    # range matching expects a literal dash character, not the entity name). Some postings
    # (observed on Rocket Lab) go a level further and are *double*-escaped -- "&amp;mdash;" in
    # the raw source, which a single unescape() only turns into "&mdash;" (still an entity, not
    # a real character). Unescaping in a loop until the text stops changing handles any encoding
    # depth; it's a no-op for content that was never escaped in the first place.
    text = html or ""
    for _ in range(3):
        unescaped = html_module.unescape(text)
        if unescaped == text:
            break
        text = unescaped
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<br\s*/?>|</p>|</li>|</div>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()
