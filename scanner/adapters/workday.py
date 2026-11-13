"""Workday (*.myworkdayjobs.com) — the public CXS JSON API behind every Workday careers site."""
import re

from ..filters import location_tags, title_match

INDIA_ID = "c4f78be1a8f14da0ab49ce1162348a5e"  # Workday's global id for India
PAGE = 20
MAX_PAGES_PER_TERM = 50  # 1,000 postings per search term; a scan that still hits this is flagged
COUNTRY_RE = re.compile(r"^\s*(india|ind|in)\s*(\(.*\))?\s*$", re.I)
CITY_RE = re.compile(r"pune|mumbai|bombay|maharashtra|\bindia\b|\bind\b", re.I)
MULTI_RE = re.compile(r"^\d+\s+locations?$", re.I)
VAGUE_DATE_RE = re.compile(r"\d+\s*\+", re.I)  # "Posted 30+ Days Ago": the list doesn't say how old


def needs_detail(job):
    """Open a posting's own data only for roles we might keep: a data title, and either
    hidden cities ("3 Locations") or a vague date ("30+ Days Ago") on a Pune/Mumbai/remote role."""
    if not title_match(job["title"])[0]:
        return False
    loc = job["locations"][0] if job["locations"] else ""
    if MULTI_RE.match(loc.strip()):
        return True
    if VAGUE_DATE_RE.search(str(job.get("posted") or "")):
        tags, notes = location_tags(job["locations"], job.get("remote"), job.get("country"))
        return bool(tags or notes)
    return False


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

    seen, capped = {}, []
    for term in terms:
        offset, total = 0, None
        for page in range(MAX_PAGES_PER_TERM):
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
            if page == MAX_PAGES_PER_TERM - 1:
                capped.append(f"'{term}' stopped at {offset} of {total}")
    if capped:
        src["_capped"] = capped  # paging cap hit: some postings were never read

    jobs = []
    for path, p in seen.items():
        loc = p.get("locationsText") or ""
        job = {
            "id": path, "title": p.get("title", ""), "locations": [loc],
            "url": f"https://{host}/en-US/{site}{path}", "posted": p.get("postedOn"),
            "remote": (p.get("remoteType") or "").lower().startswith("remote") or None,
            "country": "India" if how == "country=India" else None,
        }
        # "3 Locations" hides the cities and "30+ Days Ago" hides the date: the posting's own
        # data has both (location + additionalLocations, and startDate, the exact posting date)
        if needs_detail(job):
            try:
                info = http.get_json(f"https://{host}/wday/cxs/{tenant}/{site}{path}").get("jobPostingInfo", {})
                if MULTI_RE.match(loc.strip()):
                    job["locations"] = [info.get("location", "")] + list(info.get("additionalLocations") or [])
                job["posted"] = info.get("startDate") or job["posted"]
                if info.get("remoteType"):
                    job["remote"] = info["remoteType"].lower().startswith("remote")
            except Exception:  # noqa: BLE001 - keep the posting with what we have
                pass
        jobs.append(job)
    return jobs
