"""Visa careers search (search.visa.com) — the JSON API behind corporate.visa.com/jobs."""


def scan(src, http):
    seen = {}
    for term in src.get("terms") or ["data", "etl", "spark", "big data", "analytics engineer"]:
        data = http.post_json("https://search.visa.com/CAREERS/careers/jobs",
                              {"from": 0, "size": 1000, "query": term, "filters": []})
        for j in data.get("jobDetails") or []:
            seen.setdefault(j.get("postingId") or j.get("refNumber"), j)
    jobs = []
    for jid, j in seen.items():
        loc = ", ".join(x for x in (j.get("city"), j.get("region"), j.get("country")) if x)
        ref = j.get("refNumber")
        jobs.append({"id": str(jid), "title": j.get("jobTitle", ""), "locations": [loc],
                     "url": f"https://www.visa.co.in/en_in/jobs/{ref}" if ref else j.get("applyUrl"),
                     "posted": j.get("createdOn"), "remote": None, "country": j.get("countryCode")})
    return jobs
