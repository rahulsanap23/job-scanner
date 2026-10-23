"""Eightfold-powered careers sites (*.eightfold.ai and custom domains using it).

Two API generations exist: /api/apply/v2/jobs (older) and /api/pcsx/search (newer).
"""
PAGE = 10
MAX_PAGES_PER_TERM = 12


def _rows(data, api):
    if api == "pcsx":
        d = data.get("data") or {}
        return d.get("positions") or [], d.get("count") or 0
    return data.get("positions") or [], data.get("count") or 0


def scan(src, http):
    from . import SEARCH_TERMS
    host, domain, api = src["host"], src["domain"], src.get("api", "pcsx")
    path = "/api/pcsx/search" if api == "pcsx" else "/api/apply/v2/jobs"
    seen = {}
    for term in src.get("terms") or SEARCH_TERMS:
        for loc in src.get("locations") or ["India"]:
            start = 0
            for _ in range(MAX_PAGES_PER_TERM):
                params = {"domain": domain, "query": term, "location": loc, "start": start}
                if api != "pcsx":
                    params["num"] = PAGE
                rows, count = _rows(http.get_json(f"https://{host}{path}", params=params), api)
                for p in rows:
                    seen.setdefault(str(p.get("id")), p)
                start += len(rows) or PAGE
                if not rows or start >= count:
                    break
    jobs = []
    for pid, p in seen.items():
        locs = list(p.get("locations") or []) or [p.get("location") or ""]
        url = p.get("canonicalPositionUrl") or p.get("positionUrl") or f"/careers/job/{pid}"
        if url.startswith("/"):
            url = f"https://{host}{url}"
        wlo = (p.get("work_location_option") or p.get("workLocationOption") or "").lower()
        jobs.append({"id": pid, "title": p.get("name", ""), "locations": locs, "url": url,
                     "posted": p.get("t_create") or p.get("postedTs") or p.get("creationTs"),
                     "remote": (wlo == "remote") or None, "country": None})
    return jobs
