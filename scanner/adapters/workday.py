"""Workday (*.myworkdayjobs.com) — the public CXS JSON API behind every Workday careers site."""
import re

from ..filters import title_match

INDIA_ID = "c4f78be1a8f14da0ab49ce1162348a5e"  # Workday's global id for India
PAGE = 20
MAX_PAGES_PER_TERM = 15
CITY_WORDS = ("pune", "mumbai", "bombay", "navi mumbai", "india", "remote")


def _walk(facets):
    for f in facets or []:
        param = f.get("facetParameter", "")
        for v in f.get("values", []) or []:
            if "values" in v:  # nested facet group
                yield from _walk([v])
            else:
                yield param, v


def india_facets(facets):
    """Pick the facet filter that limits results to India (or failing that, our cities)."""
    country = {}
    for param, v in _walk(facets):
        d = (v.get("descriptor") or "").strip().lower()
        if v.get("id") == INDIA_ID or ("country" in param.lower() and d in ("india", "ind", "in")):
            country.setdefault(param, []).append(v["id"])
    if country:
        return country, "country=India"
    cities = {}
    for param, v in _walk(facets):
        d = (v.get("descriptor") or "").lower()
        if "location" in param.lower() and any(w in d for w in CITY_WORDS):
            cities.setdefault(param, []).append(v["id"])
    if cities:
        return cities, "location facet"
    return {}, "no India facet"


def scan(src, http, terms=None):
    from . import SEARCH_TERMS
    terms = terms or src.get("terms") or SEARCH_TERMS
    host, tenant, site = src["host"], src["tenant"], src["site"]
    api = f"https://{host}/wday/cxs/{tenant}/{site}/jobs"
    first = http.post_json(api, {"appliedFacets": {}, "limit": 1, "offset": 0, "searchText": ""})
    applied, how = india_facets(first.get("facets"))
    src["_note"] = how

    seen = {}
    for term in terms:
        offset, total = 0, None
        for _ in range(MAX_PAGES_PER_TERM):
            data = http.post_json(api, {"appliedFacets": applied, "limit": PAGE,
                                        "offset": offset, "searchText": term})
            if total is None:
                total = data.get("total") or 0
            posts = data.get("jobPostings") or []
            for p in posts:
                path = p.get("externalPath")
                if path and path not in seen:
                    seen[path] = p
            offset += PAGE
            if not posts or offset >= total:
                break

    jobs = []
    for path, p in seen.items():
        loc = p.get("locationsText") or ""
        job = {
            "id": path, "title": p.get("title", ""), "locations": [loc],
            "url": f"https://{host}/en-US/{site}{path}", "posted": p.get("postedOn"),
            "remote": (p.get("remoteType") or "").lower().startswith("remote") or None,
            "country": "India" if how == "country=India" else None,
        }
        # "3 Locations" hides the cities: look them up, but only for roles we'd keep
        if re.match(r"^\d+\s+locations?$", loc.strip(), re.I) and title_match(job["title"])[0]:
            try:
                info = http.get_json(f"https://{host}/wday/cxs/{tenant}/{site}{path}").get("jobPostingInfo", {})
                job["locations"] = [info.get("location", "")] + list(info.get("additionalLocations") or [])
                job["posted"] = info.get("startDate") or job["posted"]
                if info.get("remoteType"):
                    job["remote"] = info["remoteType"].lower().startswith("remote")
            except Exception:  # noqa: BLE001 - keep the posting with what we have
                pass
        jobs.append(job)
    return jobs
