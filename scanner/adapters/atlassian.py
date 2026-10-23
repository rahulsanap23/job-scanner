"""Atlassian publishes every open role as one JSON list on its careers site."""


def scan(src, http):
    data = http.get_json("https://www.atlassian.com/endpoint/careers/listings")
    jobs = []
    for j in data:
        post = j.get("portalJobPost") or {}
        jobs.append({
            "id": str(j.get("id")), "title": j.get("title", ""),
            "locations": list(j.get("locations") or []),
            "url": post.get("portalUrl") or f"https://www.atlassian.com/company/careers/details/{j.get('id')}",
            "posted": post.get("updatedDate"), "remote": None, "country": None,
        })
    return jobs
