"""Oracle Recruiting Cloud (*.oraclecloud.com CandidateExperience sites) REST API."""

PAGE = 25
MAX_PAGES_PER_TERM = 12


def _query(http, base, site, term, offset, location_id=None, location=None):
    finder = (f"findReqs;siteNumber={site},facetsList=LOCATIONS,limit={PAGE},offset={offset},"
              f'keyword="{term}",sortBy=POSTING_DATES_DESC')
    if location_id:
        finder += f",locationId={location_id}"
    elif location:
        finder += f",location={location}"
    data = http.get_json(base, params={"onlyData": "true",
                                       "expand": "requisitionList.secondaryLocations",
                                       "finder": finder})
    items = data.get("items") or [{}]
    return items[0]


def scan(src, http):
    from . import SEARCH_TERMS
    host, site = src["host"], src["site"]
    base = f"https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitions"
    first = _query(http, base, site, "data", 0)
    india = next((f.get("Id") for f in first.get("locationsFacet") or []
                  if (f.get("Name") or "").strip().lower() == "india"), None)
    src["_note"] = "locationId=India" if india else "location=India (text search)"

    seen = {}
    for term in src.get("terms") or SEARCH_TERMS:
        offset = 0
        for _ in range(MAX_PAGES_PER_TERM):
            item = _query(http, base, site, term, offset, india, None if india else "India")
            reqs = item.get("requisitionList") or []
            for r in reqs:
                seen.setdefault(str(r.get("Id")), r)
            offset += PAGE
            if not reqs or offset >= (item.get("TotalJobsCount") or 0):
                break

    jobs = []
    for rid, r in seen.items():
        locs = [r.get("PrimaryLocation") or ""] + [s.get("Name", "") for s in r.get("secondaryLocations") or []]
        jobs.append({
            "id": rid, "title": r.get("Title", ""), "locations": locs,
            "url": f"https://{host}/hcmUI/CandidateExperience/en/sites/{site}/job/{rid}",
            "posted": r.get("PostedDate"),
            "remote": (r.get("WorkplaceType") or r.get("WorkplaceTypeCode") or "").lower().startswith("remote") or None,
            "country": r.get("PrimaryLocationCountry"),
        })
    return jobs
