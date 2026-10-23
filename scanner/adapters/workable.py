"""Workable job boards (apply.workable.com/<account>) — public v3 jobs API."""

MAX_PAGES = 10


def scan(src, http):
    acct = src["account"]
    jobs, token = [], None
    for _ in range(MAX_PAGES):
        body = {"query": "", "location": [], "department": [], "worktype": [], "remote": []}
        if token:
            body["token"] = token
        data = http.post_json(f"https://apply.workable.com/api/v3/accounts/{acct}/jobs", body)
        for j in data.get("results") or []:
            locs = []
            for l in [j.get("location") or {}] + list(j.get("locations") or []):
                locs.append(", ".join(x for x in (l.get("city"), l.get("region"), l.get("country")) if x))
            jobs.append({"id": j.get("shortcode"), "title": j.get("title", ""), "locations": locs,
                         "url": f"https://apply.workable.com/{acct}/j/{j.get('shortcode')}/",
                         "posted": j.get("published"), "remote": bool(j.get("remote")) or None,
                         "country": (j.get("location") or {}).get("countryCode")})
        token = data.get("nextPage")
        if not token:
            break
    return jobs
