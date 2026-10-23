"""SmartRecruiters — public postings API, filtered to India server-side."""

PAGE = 100


def scan(src, http):
    cid = src["company_id"]
    jobs, offset = [], 0
    while True:
        data = http.get_json(f"https://api.smartrecruiters.com/v1/companies/{cid}/postings",
                             params={"country": "in", "limit": PAGE, "offset": offset})
        content = data.get("content") or []
        for j in content:
            loc = j.get("location") or {}
            text = loc.get("fullLocation") or ", ".join(
                x for x in (loc.get("city"), loc.get("region"), loc.get("country")) if x)
            jobs.append({
                "id": j.get("id"), "title": j.get("name", ""), "locations": [text],
                "url": f"https://jobs.smartrecruiters.com/{cid}/{j.get('id')}",
                "posted": j.get("releasedDate"), "remote": bool(loc.get("remote")) or None,
                "country": loc.get("country"),
            })
        offset += PAGE
        if not content or offset >= (data.get("totalFound") or 0):
            break
    return jobs
