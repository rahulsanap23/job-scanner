"""Avature careers portals (*.avature.net and custom domains such as jobs.bmc.com).

Search results are server-rendered `<article class="article--result">` blocks.
"""
import html
import re

PAGE = 50
MAX_PAGES_PER_TERM = 10
ART = re.compile(r'<article[^>]*class="[^"]*article--result[^"]*"[^>]*>(.*?)</article>', re.S | re.I)
TITLE = re.compile(r'<h3[^>]*>\s*<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', re.S | re.I)
LOC = re.compile(r'class="[^"]*list-item-location[^"]*"[^>]*>(.*?)</span>', re.S | re.I)
POSTED = re.compile(r'class="[^"]*list-item-posted[^"]*"[^>]*>(.*?)</span>', re.S | re.I)
TOTAL = re.compile(r'([\d,]+)\s+results', re.I)


def _t(s):
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", s or "")).split())


def parse(page):
    jobs = []
    for a in ART.findall(page):
        m = TITLE.search(a)
        if not m:
            continue
        url = html.unescape(m.group(1))
        loc = LOC.search(a)
        posted = POSTED.search(a)
        jobs.append({"id": url, "title": _t(m.group(2)), "locations": [_t(loc.group(1)) if loc else ""],
                     "url": url, "posted": _t(posted.group(1)).replace("Posted", "").strip() if posted else None,
                     "remote": None, "country": None})
    t = TOTAL.search(page)
    return jobs, int(t.group(1).replace(",", "")) if t else None


def scan(src, http):
    from . import SEARCH_TERMS
    base = src["base"].rstrip("/")  # e.g. https://bloomberg.avature.net/careers
    seen = {}
    for term in src.get("terms") or SEARCH_TERMS:
        offset = 0
        for _ in range(MAX_PAGES_PER_TERM):
            page = http.get(f"{base}/SearchJobs/", params={"search": term, "jobRecordsPerPage": PAGE,
                                                          "jobOffset": offset}).text
            rows, total = parse(page)
            for j in rows:
                seen.setdefault(j["id"], j)
            offset += PAGE
            if not rows or total is None or offset >= total:
                break
    return list(seen.values())
