"""Newer SuccessFactors Career Site Builder sites that load results from
/services/recruiting/v1/jobs (e.g. jobs.standardchartered.com, jobs.tetrapak.com).

Some sites (e.g. Atlas Copco) leave the location out of most search results. For
those, set `detail_locations: true`: the search runs without a location filter and
the location of each data-titled job is read from its job page instead (jobs whose
listed currency isn't INR are skipped as not in India).
"""
import html
import re

import requests

from ..filters import title_match

MAX_PAGES_PER_TERM = 10
LABELLED = re.compile(r'joblayouttoken-label[^>]*>\s*([^<]+?)\s*</span>\s*<span[^>]*rtltextaligneligible[^>]*>'
                      r'\s*([^<]*?)\s*</span>', re.S)


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


def detail_location(page):
    """(locations, remote) from a job page's labelled fields (City / Province, Country/Region, Job type)."""
    fields = {html.unescape(k).strip(" :\xa0").lower(): html.unescape(v).strip() for k, v in LABELLED.findall(page)}
    locs = [v for k, v in fields.items() if v and ("city" in k or "country" in k or "location" in k)]
    remote = True if "remote" in (fields.get("job type") or "").lower() else None
    return locs, remote


def scan(src, http):
    from . import SEARCH_TERMS
    base = src["base"].rstrip("/")
    locale = src.get("locale", "en_GB")
    detail = src.get("detail_locations")
    max_pages = src.get("max_pages", MAX_PAGES_PER_TERM)
    seen = {}
    for term in src.get("terms") or SEARCH_TERMS:
        for page in range(max_pages):
            data = http.post_json(f"{base}/services/recruiting/v1/jobs", {
                "locale": locale, "pageNumber": page, "sortBy": "", "keywords": term,
                "location": "" if detail else src.get("location", "India"), "facetFilters": {}, "brand": "", "skills": [],
                "categoryId": 0, "alertId": "", "rcmCandidateId": ""})
            rows = [r.get("response") or {} for r in data.get("jobSearchResult") or []]
            for r in rows:
                seen.setdefault(str(r.get("id")), r)
            total = data.get("totalJobs") or 0
            if not rows or (page + 1) * len(rows) >= total:
                break
    jobs = []
    for jid, r in seen.items():
        slug = html.unescape(r.get("urlTitle") or r.get("unifiedUrlTitle") or "job")
        url = f"{base}/job/{slug}/{jid}-{locale}/"
        locs = [re.sub(r"<[^>]+>", " ", l).strip() for l in r.get("jobLocationShort") or []]
        remote = None
        if detail and not locs:
            currency = r.get("currency") or []
            if currency and "INR" not in currency:
                continue  # paid in another currency: not an India job
            if not title_match(r.get("unifiedStandardTitle"))[0]:
                continue  # only open the pages of jobs the title filter would keep
            try:
                locs, remote = detail_location(http.get(url, headers={"Accept": "text/html"}).text)
            except requests.RequestException:
                continue  # job page gone or unreachable: skip this one job
        jobs.append({"id": jid, "title": r.get("unifiedStandardTitle", ""), "locations": locs,
                     "url": url, "posted": _date(r.get("unifiedStandardStart"), locale), "remote": remote,
                     "country": (r.get("jobLocationCountry") or [None])[0]})
    return jobs
