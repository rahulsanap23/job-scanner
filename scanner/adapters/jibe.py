"""iCIMS Jibe careers sites (e.g. careers.ice.com, jobs.ajg.com) — /api/jobs JSON."""

MAX_PAGES_PER_TERM = 10


def scan(src, http):
    from . import SEARCH_TERMS
    base = src["base"].rstrip("/")
    seen = {}
    for term in src.get("terms") or SEARCH_TERMS:
        for page in range(1, MAX_PAGES_PER_TERM + 1):
            data = http.get_json(f"{base}/api/jobs", params={"keywords": term, "location": src.get("location", "India"),
                                                             "page": page})
            rows = data.get("jobs") or []
            for row in rows:
                j = row.get("data") or row
                seen.setdefault(str(j.get("req_id") or j.get("slug") or j.get("id")), j)
            total = data.get("totalCount") or data.get("count") or 0
            if not rows or page * max(len(rows), 1) >= total:
                break
    jobs = []
    for jid, j in seen.items():
        locs = [j.get("full_location") or j.get("location_name") or ""]
        locs.append(", ".join(x for x in (j.get("city"), j.get("state"), j.get("country")) if x))
        url = j.get("apply_url") if src.get("use_apply_url") else None
        url = url or f"{base}/jobs/{j.get('slug') or jid}"
        jobs.append({"id": jid, "title": j.get("title", ""), "locations": locs, "url": url,
                     "posted": j.get("posted_date") or j.get("create_date"),
                     "remote": None, "country": j.get("country_code") or j.get("country")})
    return jobs
