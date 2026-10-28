"""JazzHR career pages (<company>.applytojob.com/apply) — server-rendered job list.

Each listing is an <li> with the job link in an <h3> and its location in the
next list item (marked with a map-marker icon). No posting date is shown.
"""
import html
import re

ITEM = re.compile(r'<li class="list-group-item">(.*?)</ul>\s*</li>', re.S | re.I)
LINK = re.compile(r'<a href="(https://[a-z0-9-]+\.applytojob\.com/apply/([A-Za-z0-9]+)/[^"]*)"[^>]*>(.*?)</a>',
                  re.S | re.I)
LOC = re.compile(r"fa-map-marker[^>]*></i>(.*?)</li>", re.S | re.I)


def _text(s):
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", s or "")).split())


def parse(page):
    jobs = {}
    for item in ITEM.findall(page):
        m = LINK.search(item)
        if not m:
            continue
        locs = [_text(x) for x in LOC.findall(item)]
        jobs.setdefault(m.group(2), {"id": m.group(2), "title": _text(m.group(3)), "locations": locs,
                                     "url": html.unescape(m.group(1)), "posted": None,
                                     "remote": None, "country": None})
    return list(jobs.values())


def scan(src, http):
    page = http.get(f"https://{src['account']}.applytojob.com/apply", headers={"Accept": "text/html"}).text
    return parse(page)
