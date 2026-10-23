"""Phenom People careers sites (careers.<company>.com/.../search-results).

The search-results page embeds the first page of results as JSON in `phApp.ddo`
(key eagerLoadRefineSearch). We page with `from=`.
"""
import json
import re

PAGE = 10
MAX_PAGES_PER_TERM = 15


def _extract_obj(text, key):
    i = text.find(f'"{key}"')
    if i < 0:
        return None
    j = text.find("{", i)
    depth, k, in_str, esc = 0, j, False, False
    while k < len(text):
        ch = text[k]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[j:k + 1])
                except json.JSONDecodeError:
                    return None
        k += 1
    return None


def scan(src, http):
    from . import SEARCH_TERMS
    base = src["base"].rstrip("/")  # e.g. https://careers.honeywell.com/us/en
    root = re.match(r"https?://[^/]+", base).group(0)
    seen = {}
    for term in src.get("terms") or SEARCH_TERMS:
        offset = 0
        for _ in range(MAX_PAGES_PER_TERM):
            params = {"keywords": term, "from": offset, "s": 1}
            if src.get("location_param"):
                params.update(src["location_param"])
            page = http.get(f"{base}/search-results", params=params).text
            ers = _extract_obj(page, "eagerLoadRefineSearch") or {}
            data = ers.get("data") or {}
            rows = data.get("jobs") or []
            for j in rows:
                seen.setdefault(str(j.get("jobId") or j.get("reqId") or j.get("jobSeqNo")), j)
            offset += len(rows) or PAGE
            if not rows or offset >= (ers.get("totalHits") or ers.get("hits") or 0):
                break
    jobs = []
    for jid, j in seen.items():
        locs = [j.get("location") or j.get("cityStateCountry") or ""]
        for ml in j.get("multi_location") or []:
            locs.append(ml if isinstance(ml, str) else json.dumps(ml))
        if j.get("city"):
            locs.append(", ".join(x for x in (j.get("city"), j.get("state"), j.get("country")) if x))
        url = j.get("applyUrl") if src.get("use_apply_url") else None
        if not url:
            slug = re.sub(r"[^A-Za-z0-9]+", "-", j.get("title", "")).strip("-")
            url = f"{base}/job/{jid}/{slug}"
        jobs.append({"id": jid, "title": j.get("title", ""), "locations": locs, "url": url or root,
                     "posted": j.get("postedDate") or j.get("dateCreated"),
                     "remote": ("remote" in (j.get("workLocation") or j.get("type") or "").lower()) or None,
                     "country": j.get("country")})
    return jobs
