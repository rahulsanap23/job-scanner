"""Workday (*.myworkdayjobs.com) — the public CXS JSON API behind every Workday careers site."""
import re

from ..filters import title_match

INDIA_ID = "c4f78be1a8f14da0ab49ce1162348a5e"  # Workday's global id for India
PAGE = 20
MAX_PAGES_PER_TERM = 15
COUNTRY_RE = re.compile(r"^\s*(india|ind|in)\s*(\(.*\))?\s*$", re.I)
CITY_RE = re.compile(r"pune|mumbai|bombay|maharashtra|\bindia\b|\bind\b", re.I)


def _walk(facets):
    for f in facets or []:
        param = f.get("facetParameter", "")
        for v in f.get("values", []) or []:
            if "values" in v:  # nested facet group
                yield from _walk([v])
            else:
                yield param, v


def india_facets(facets):
    """Pick ONE facet filter that limits results to India (or failing that, to Indian cities).

    Only one facet parameter is applied: Workday ANDs different parameters together,
    so mixing a city facet with a region facet can wipe out every result.
    """
    values = list(_walk(facets))
    for param, v in values:  # Workday's own country id for India
        if v.get("id") == INDIA_ID:
            return {param: [v["id"]]}, f"{param}=India"
    for param, v in values:  # a facet value that is just "India"
        if COUNTRY_RE.match(v.get("descriptor") or "") and "location" in param.lower() or \
                ("country" in param.lower() and COUNTRY_RE.match(v.get("descriptor") or "")):
            return {param: [v["id"]]}, f"{param}={v.get('descriptor')}"
    by_param = {}
    for param, v in values:
        d = v.get("descriptor") or ""
        if "location" in param.lower() and CITY_RE.search(d) and "indiana" not in d.lower():
            by_param.setdefault(param, []).append(v)
    if by_param:
        param, vs = max(by_param.items(), key=lambda kv: len(kv[1]))
        names = ", ".join(v.get("descriptor", "") for v in vs[:6])
        return {param: [v["id"] for v in vs]}, f"{param} in [{names}{'…' if len(vs) > 6 else ''}]"
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
