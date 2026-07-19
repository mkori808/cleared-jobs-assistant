"""Generic fallback scraper for companies with a custom careers page.

This can't reliably paginate or find job-detail links for arbitrary sites, so it
takes a best-effort approach: fetch the careers_url, pull out anything that looks
like a job link, then fetch each linked page and scan its raw text for clearance
keywords. Expect to hand-tune 'link_pattern' per company for anything beyond the
simplest listing pages -- see README for guidance.
"""
import re
import httpx
from urllib.parse import urljoin
from bs4 import BeautifulSoup

from ._location import US_STATE_ABBR

JOB_LINK_HINTS = re.compile(r"(job|career|position|opening|req[-_]?id)", re.I)

# Many custom career pages print the location as a short, self-contained "City, ST" element
# (its own <div>/<span>, not embedded in a sentence) right near the job title. Matching against
# each element's *own* text individually -- rather than the page's flattened text -- avoids
# accidentally swallowing adjacent title/tag words that end up merged together once tags are
# stripped (e.g. a tech-stack tag like "GhostMachine" sitting right before the location text
# with no separator once HTML structure is discarded). Anchored (fullmatch) on purpose: this
# only fires for elements containing *just* a location, not prose that mentions one in passing.
_LOCATION_ELEMENT_RE = re.compile(
    rf"([A-Z][A-Za-z.'\- ]{{1,30}},\s*(?:{'|'.join(US_STATE_ABBR)}))"
)


def _guess_location(soup: BeautifulSoup) -> str | None:
    for el in soup.find_all(string=_LOCATION_ELEMENT_RE):
        text = el.strip()
        m = _LOCATION_ELEMENT_RE.fullmatch(text)
        if m:
            return m.group(1)
    return None


def fetch_jobs(careers_url: str, client: httpx.Client, link_pattern: str = None) -> list[dict]:
    resp = client.get(careers_url, timeout=20, follow_redirects=True)
    resp.raise_for_status()
    html = resp.text

    pattern = re.compile(link_pattern, re.I) if link_pattern else None
    links = set(re.findall(r'href=["\']([^"\']+)["\']', html))

    # Filter out asset/resource files (CSS, JS, images, fonts, etc.)
    asset_pattern = re.compile(r'\.(css|js|png|jpg|jpeg|gif|svg|woff|woff2|ttf|eot|map|ico|webp)(\?|#|$)', re.I)

    job_links = []
    for link in links:
        if asset_pattern.search(link):
            continue  # Skip asset files
        if pattern:
            if pattern.search(link):
                job_links.append(urljoin(careers_url, link))
        elif JOB_LINK_HINTS.search(link):
            job_links.append(urljoin(careers_url, link))

    jobs = []
    deduped_links = list(dict.fromkeys(job_links))[:50]  # dedupe, cap to 50 for safety
    for url in deduped_links:
        try:
            r = client.get(url, timeout=10, follow_redirects=True)
            try:
                if r.status_code != 200:
                    continue
                soup = BeautifulSoup(r.text, "html.parser")
                title = soup.title.get_text(strip=True) if soup.title else url
                # Remove script/style tags via the DOM rather than a regex over raw HTML --
                # truncating raw HTML *before* stripping (the old approach) could cut a
                # <script> tag in half, leaving its unstripped contents (often a large inline
                # JSON/JS blob on SPA-rendered career pages) to leak into the description.
                for tag in soup(["script", "style"]):
                    tag.decompose()
                text = soup.get_text(separator=" ", strip=True)[:20000]
                jobs.append({
                    "external_id": url,
                    "title": title,
                    "location": _guess_location(soup),
                    "url": url,
                    "description_html": text,
                })
            finally:
                r.close()
        except Exception:
            continue
    return jobs
