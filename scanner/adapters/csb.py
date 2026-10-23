"""SAP SuccessFactors Career Site Builder sites (e.g. jobs.sap.com, careers.nomura.com).

These render search results server-side as a table; we read the rows.
"""
import html
import re
from urllib.parse import urljoin

PAGE = 25
MAX_PAGES_PER_TERM = 8
ROW = re.compile(r'<tr[^>]*class="[^"]*data-row[^"]*"[^>]*>(.*?)</tr>', re.S | re.I)
LINK = re.compile(r'<a[^>]*href="([^"]+)"[^>]*class="[^"]*jobTitle-link[^"]*"[^>]*>(.*?)</a>'
                  r'|<a[^>]*class="[^"]*jobTitle-link[^"]*"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', re.S | re.I)
LOC = re.compile(r'<span[^>]*class="[^"]*jobLocation[^"]*"[^>]*>(.*?)</span>', re.S | re.I)
DATE = re.compile(r'<span[^>]*class="[^"]*jobDate[^"]*"[^>]*>(.*?)</span>', re.S | re.I)
TOTAL = re.compile(r'of\s*<b>\s*([\d,]+)\s*</b>|of\s+([\d,]+)\s*</span>', re.I)


def _text(s):
    return html.unescape(re.sub(r"<[^>]+>", " ", s or "")).strip()


def parse(page, base):
    jobs = []
    for row in ROW.findall(page):
        m = LINK.search(row)
        if not m:
            continue
        href = html.unescape(m.group(1) or m.group(3))
        title = _text(m.group(2) or m.group(4))
        locs = [" ".join(_text(x).split()) for x in LOC.findall(row)]
        dates = [_text(x) for x in DATE.findall(row)]
        jobs.append({"id": href, "title": title, "locations": list(dict.fromkeys(l for l in locs if l)),
                     "url": urljoin(base, href), "posted": dates[0] if dates else None,
                     "remote": None, "country": None})
    tm = TOTAL.search(page)
    total = int((tm.group(1) or tm.group(2)).replace(",", "")) if tm else None
    return jobs, total


def scan(src, http):
    from . import SEARCH_TERMS
    base = src["base"].rstrip("/")
    seen = {}
    for term in src.get("terms") or SEARCH_TERMS:
        for loc in src.get("locations") or ["India"]:
            start = 0
            for _ in range(MAX_PAGES_PER_TERM):
                r = http.get(f"{base}/search/", params={"q": term, "locationsearch": loc,
                                                       "startrow": start})
                rows, total = parse(r.text, base + "/")
                for j in rows:
                    seen.setdefault(j["id"], j)
                start += len(rows) or PAGE
                if not rows or total is None or start >= total:
                    break
    return list(seen.values())
