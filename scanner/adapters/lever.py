"""Lever job sites — public postings API returns every open job."""


def scan(src, http):
    host = "api.eu.lever.co" if src.get("eu") else "api.lever.co"
    data = http.get_json(f"https://{host}/v0/postings/{src['site']}?mode=json")
    jobs = []
    for j in data:
        cat = j.get("categories") or {}
        locs = [cat.get("location", "")] + list(cat.get("allLocations") or [])
        jobs.append({
            "id": j.get("id"), "title": j.get("text", ""), "locations": locs,
            "url": j.get("hostedUrl"), "posted": j.get("createdAt"),
            "remote": (j.get("workplaceType") == "remote") or None,
            "country": j.get("country"),
        })
    return jobs
