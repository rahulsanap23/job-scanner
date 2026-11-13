"""Fill in posting dates the careers-site list didn't give.

Some careers platforms (Goldman Sachs, Jobvite, JazzHR, Kula and a few page scrapers) list
roles with no date at all. For roles that already passed the title and location filters we
open the posting page and read `datePosted` from its schema.org JobPosting data, which most
careers pages embed (hidden) for Google's job search. If that fails, the daily scan later
tries LinkedIn's date for the same company and title (see scanner/run.py, crosscheck), and
if that fails too the role stays on the board as "date not given".
"""
import json
import re

from .filters import parse_posted

LD_RE = re.compile(r'(?is)<script[^>]+application/ld\+json[^>]*>(.*?)</script>')
LOOSE_RE = re.compile(r'"datePosted"\s*:\s*"([^"]{8,40})"')
MAX_LOOKUPS = 15  # per company and scan


def ld_date(page):
    """datePosted from a page's JobPosting JSON-LD (or a bare "datePosted" field), as a string."""
    for m in LD_RE.finditer(page or ""):
        try:
            data = json.loads(m.group(1).strip())
        except ValueError:
            continue
        stack = [data]
        while stack:
            d = stack.pop()
            if isinstance(d, dict):
                if d.get("datePosted"):
                    return str(d["datePosted"])
                stack.extend(v for v in d.values() if isinstance(v, (dict, list)))
            elif isinstance(d, list):
                stack.extend(d)
    m = LOOSE_RE.search(page or "")
    return m.group(1) if m else None


def fill_missing(jobs, http, today, log=None):
    """Look up datePosted for jobs with no date. Sets posted/days_ago/date_source in place.
    Returns how many dates were found."""
    found = 0
    for j in [j for j in jobs if not j.get("posted") and j.get("url")][:MAX_LOOKUPS]:
        try:
            page = http.get(j["url"], headers={"Accept": "text/html,*/*;q=0.8"}).text
        except Exception:  # noqa: BLE001 - a page that won't load just stays undated
            continue
        d = parse_posted(ld_date(page), today)
        if d and d <= today:
            j["posted"], j["days_ago"], j["date_source"] = d.isoformat(), (today - d).days, "posting page"
            found += 1
    return found
