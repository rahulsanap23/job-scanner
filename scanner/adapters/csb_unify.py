"""Newer SuccessFactors Career Site Builder sites that load results from
/services/recruiting/v1/jobs (e.g. jobs.standardchartered.com, jobs.tetrapak.com)."""
import re

MAX_PAGES_PER_TERM = 10


def _date(start, locale):
    """unifiedStandardStart follows the site locale: dd/mm/yyyy (en_GB) or m/d/yy (en_US)."""
    parts = (start or "").split("/")
    if len(parts) != 3 or not all(p.strip().isdigit() for p in parts):
        return None
    a, b, y = (int(p) for p in parts)
    if y < 100:
        y += 2000
    month_first = locale.lower().startswith("en_us")
    if a > 12:
        month_first = False
    elif b > 12:
        month_first = True
    d, m = (b, a) if month_first else (a, b)
    if not (1 <= m <= 12 and 1 <= d <= 31):
        return None
    return f"{y:04d}-{m:02d}-{d:02d}"


def scan(src, http):
    from . import SEARCH_TERMS
    base = src["base"].rstrip("/")
    locale = src.get("locale", "en_GB")
    seen = {}
    for term in src.get("terms") or SEARCH_TERMS:
        for page in range(MAX_PAGES_PER_TERM):
            data = http.post_json(f"{base}/services/recruiting/v1/jobs", {
                "locale": locale, "pageNumber": page, "sortBy": "", "keywords": term,
                "location": src.get("location", "India"), "facetFilters": {}, "brand": "", "skills": [],
                "categoryId": 0, "alertId": "", "rcmCandidateId": ""})
            rows = [r.get("response") or {} for r in data.get("jobSearchResult") or []]
            for r in rows:
                seen.setdefault(str(r.get("id")), r)
            total = data.get("totalJobs") or 0
            if not rows or (page + 1) * len(rows) >= total:
                break
    jobs = []
    for jid, r in seen.items():
        slug = r.get("urlTitle") or r.get("unifiedUrlTitle") or "job"
        posted = _date(r.get("unifiedStandardStart"), locale)
        jobs.append({"id": jid, "title": r.get("unifiedStandardTitle", ""),
                     "locations": [re.sub(r"<[^>]+>", " ", l).strip() for l in r.get("jobLocationShort") or []],
                     "url": f"{base}/job/{slug}/{jid}-{locale}/",
                     "posted": posted, "remote": None,
                     "country": (r.get("jobLocationCountry") or [None])[0]})
    return jobs
