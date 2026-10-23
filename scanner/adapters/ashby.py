"""Ashby job boards — public posting API returns every listed job."""


def scan(src, http):
    data = http.get_json(f"https://api.ashbyhq.com/posting-api/job-board/{src['org']}")
    jobs = []
    for j in data.get("jobs", []):
        if j.get("isListed") is False:
            continue
        locs = [j.get("location", "")] + [s.get("location", "") for s in j.get("secondaryLocations") or []]
        addr = ((j.get("address") or {}).get("postalAddress") or {})
        jobs.append({
            "id": j.get("id"), "title": j.get("title", ""), "locations": locs,
            "url": j.get("jobUrl"), "posted": j.get("publishedAt"),
            "remote": bool(j.get("isRemote")) or (j.get("workplaceType") == "Remote") or None,
            "country": addr.get("addressCountry"),
        })
    return jobs
