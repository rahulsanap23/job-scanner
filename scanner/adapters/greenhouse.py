"""Greenhouse job boards — public boards API returns every open job in one call."""


def scan(src, http):
    host = "boards-api.eu.greenhouse.io" if src.get("eu") else "boards-api.greenhouse.io"
    data = http.get_json(f"https://{host}/v1/boards/{src['token']}/jobs")
    jobs = []
    for j in data.get("jobs", []):
        locs = [(j.get("location") or {}).get("name", "")]
        for o in j.get("offices") or []:
            locs.append(o.get("location") or o.get("name") or "")
        jobs.append({
            "id": str(j.get("id")), "title": j.get("title", ""), "locations": locs,
            "url": j.get("absolute_url"),
            # first_published is the real posting date; older boards only expose updated_at
            "posted": j.get("first_published") or j.get("updated_at"),
            "remote": None, "country": None,
        })
    return jobs
