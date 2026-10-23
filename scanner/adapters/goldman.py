"""Goldman Sachs careers (higher.gs.com) — the GraphQL API behind the site."""
API = "https://api-higher.gs.com/gateway/api/v1/graphql"
QUERY = ("query GetRoles($searchQueryInput: RoleSearchQueryInput!) { roleSearch(searchQueryInput: $searchQueryInput) "
         "{ totalCount items { roleId jobTitle corporateTitle division status locations { primary state country city } } } }")
PAGE = 50
MAX_PAGES_PER_TERM = 12


def scan(src, http):
    seen = {}
    for term in src.get("terms") or ["data engineer", "data", "etl", "spark", "big data"]:
        for page in range(MAX_PAGES_PER_TERM):
            payload = {"operationName": "GetRoles", "query": QUERY, "variables": {"searchQueryInput": {
                "page": {"pageSize": PAGE, "pageNumber": page},
                "sort": {"sortStrategy": "RELEVANCE", "sortOrder": "DESC"}, "filters": [],
                "experiences": ["EARLY_CAREER", "PROFESSIONAL"], "searchTerm": term}}}
            res = (http.post_json(API, payload).get("data") or {}).get("roleSearch") or {}
            items = res.get("items") or []
            for it in items:
                seen.setdefault(it.get("roleId"), it)
            if not items or (page + 1) * PAGE >= (res.get("totalCount") or 0):
                break
    jobs = []
    for rid, it in seen.items():
        locs = [", ".join(x for x in (l.get("city"), l.get("state"), l.get("country")) if x)
                for l in it.get("locations") or []]
        jobs.append({"id": rid, "title": it.get("jobTitle", ""), "locations": locs,
                     "url": f"https://higher.gs.com/roles/{str(rid).split('_')[0]}",
                     "posted": None, "remote": None, "country": None})
    return jobs
