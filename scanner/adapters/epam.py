"""EPAM careers (careers.epam.com) — public search API.

India jobs live in their own index ("careers-india"); the main "careers" index
has none, and the API ignores country filters, so the index is the filter.
"""

PAGE = 100
MAX_PAGES_PER_TERM = 5


def scan(src, http):
    from . import SEARCH_TERMS
    index = src.get("index", "careers-india")
    seen = {}
    for term in src.get("terms") or SEARCH_TERMS:
        for page in range(MAX_PAGES_PER_TERM):
            data = http.get_json(f"https://careers.epam.com/api/jobs/v2/search/{index}",
                                 params={"lang": "en", "from": page * PAGE, "size": PAGE, "query": term})
            data = data.get("data") or {}
            rows = data.get("jobs") or []
            for j in rows:
                seen.setdefault(j.get("uid") or j.get("_key"), j)
            if not rows or (page + 1) * PAGE >= (data.get("total") or 0):
                break
    return [to_job(uid, j) for uid, j in seen.items() if not (j.get("is_expired") or j.get("is_hidden"))]


def to_job(uid, j):
    countries = [c.get("name") for c in j.get("country") or [] if c.get("name")]
    cities = [c.get("name") for c in j.get("city") or [] if c.get("name")]
    suffix = ", ".join(countries)
    locs = [f"{c}, {suffix}" if suffix else c for c in cities] or countries
    seo = (j.get("seo") or {}).get("url") or f"/vacancy/{uid}"
    return {"id": uid, "title": j.get("name", ""), "locations": locs,
            "url": f"https://careers.epam.com/en{seo}_en",
            "posted": j.get("created_at"),
            "remote": True if (j.get("vacancy_type") or "").lower() == "remote" else None,
            "country": countries[0] if len(countries) == 1 else None}
