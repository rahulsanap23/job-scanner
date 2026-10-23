"""Newer SuccessFactors Career Site Builder sites that load results from
/services/recruiting/v1/jobs (e.g. jobs.standardchartered.com, jobs.tetrapak.com)."""
MAX_PAGES_PER_TERM = 10


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
        start = r.get("unifiedStandardStart")  # dd/mm/yyyy
        posted = None
        if start and len(start.split("/")) == 3:
            d, m, y = start.split("/")
            posted = f"{y}-{m.zfill(2)}-{d.zfill(2)}"
        jobs.append({"id": jid, "title": r.get("unifiedStandardTitle", ""),
                     "locations": [l.strip() for l in r.get("jobLocationShort") or []],
                     "url": f"{base}/job/{slug}/{jid}-{locale}/",
                     "posted": posted, "remote": None,
                     "country": (r.get("jobLocationCountry") or [None])[0]})
    return jobs
