"""Jobvite careers pages (jobs.jobvite.com/careers/<company>/jobs) — server-rendered job tables."""
import html
import re
from urllib.parse import urljoin

ROW = re.compile(r'<tr[^>]*>(.*?)</tr>', re.S | re.I)
LINK = re.compile(r'<a[^>]*href="([^"]*/job/[^"]+)"[^>]*>(.*?)</a>', re.S | re.I)
LOC = re.compile(r'class="[^"]*jv-job-list-location[^"]*"[^>]*>(.*?)</td>', re.S | re.I)


def _t(s):
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", s or "")).split())


def scan(src, http):
    page = http.get(f"https://jobs.jobvite.com/careers/{src['company']}/jobs").text
    jobs = {}
    for row in ROW.findall(page):
        m = LINK.search(row)
        if not m:
            continue
        loc = LOC.search(row)
        url = urljoin("https://jobs.jobvite.com/", html.unescape(m.group(1)))
        jobs.setdefault(url, {"id": url, "title": _t(m.group(2)), "locations": [_t(loc.group(1)) if loc else ""],
                              "url": url, "posted": None, "remote": None, "country": None})
    return list(jobs.values())
