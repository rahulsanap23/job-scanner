"""amazon.jobs search JSON (India only)."""

PAGE = 100
MAX_PAGES_PER_TERM = 5


def scan(src, http):
    from . import SEARCH_TERMS
    seen = {}
    for term in src.get("terms") or SEARCH_TERMS:
        offset = 0
        for _ in range(MAX_PAGES_PER_TERM):
            data = http.get_json("https://www.amazon.jobs/en/search.json", params={
                "base_query": term, "country": "IND", "result_limit": PAGE,
                "offset": offset, "sort": "recent"})
            rows = data.get("jobs") or []
            for j in rows:
                seen.setdefault(j.get("id_icims") or j.get("job_path"), j)
            offset += PAGE
            if not rows or offset >= (data.get("hits") or 0):
                break
    return [{
        "id": jid, "title": j.get("title", ""),
        "locations": [j.get("normalized_location") or "", j.get("location") or ""],
        "url": "https://www.amazon.jobs" + (j.get("job_path") or ""),
        "posted": j.get("posted_date"), "remote": None, "country": "India",
    } for jid, j in seen.items()]
