"""DirectEmployers network (dejobs.org / <company>.jobs) search API.

Many US employers (Honeywell, Cummins, Teradata, HSBC, Google...) syndicate
their openings here. We filter by company name and city server-side.
"""
API = "https://prod-search-api.jobsyn.org/api/v1/solr/search"
MAX_PAGES = 6  # 15 results per page, relevance-sorted
TERMS = ["data engineer", "data", "etl", "spark", "big data", "analytics engineer", "databricks"]


def _url(j):
    slab = (j.get("city_slab_exact") or "").split("::")[0]
    parts = [p for p in slab.split("/") if p and p not in ("none", "jobs")]
    loc = "-".join(parts) or "jobs"
    return f"https://dejobs.org/{loc}/{j.get('title_slug') or 'job'}/{j.get('guid')}/job/"


def scan(src, http):
    seen = {}
    for name in src["companies"]:
        for loc in src.get("locations") or ["Pune", "Mumbai", "Navi Mumbai"]:
            for term in src.get("terms") or TERMS:
                for page in range(1, MAX_PAGES + 1):
                    data = http.get_json(API, headers={"x-origin": "dejobs.org"},
                                         params={"q": term, "location": loc, "company": name, "page": page})
                    rows = data.get("jobs") or []
                    for j in rows:
                        if (j.get("company_exact") or "").lower() != name.lower():
                            continue
                        seen.setdefault(j.get("guid"), j)
                    if not rows or not (data.get("pagination") or {}).get("has_more_pages"):
                        break
    jobs = []
    for guid, j in seen.items():
        locs = j.get("all_locations") or []
        if isinstance(locs, str):
            locs = [locs]
        locs = [j.get("location_exact") or ""] + [l for l in locs if isinstance(l, str)]
        jobs.append({"id": guid, "title": j.get("title_exact", ""), "locations": locs, "url": _url(j),
                     "posted": j.get("date_new") or j.get("date_added"), "remote": None,
                     "country": j.get("country_exact")})
    return jobs
