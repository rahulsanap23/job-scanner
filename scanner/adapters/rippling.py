"""Rippling ATS (ats.rippling.com/<slug>/jobs): the public job-board API behind it.

The list (one request) gives title, url and one location per posting. For postings with a
data title, the posting's own record adds every location (workLocations) and the posting
date (createdOn), which the list doesn't have.
"""
import re

from ..filters import title_match

API = "https://api.rippling.com/platform/api/ats/v1/board/{slug}/jobs"
INDIA_RE = re.compile(r"\b(india|in)\b\s*\)?\s*$", re.I)


def where(label):
    """(remote, country) from a Rippling location label such as 'Remote (Mumbai, India)'."""
    label = label or ""
    country = "India" if INDIA_RE.search(label) else None
    return (label.strip().lower().startswith("remote") or None), country


def scan(src, http):
    slug = src["slug"]
    api = API.format(slug=slug)
    jobs = []
    for p in http.get_json(api) or []:
        label = (p.get("workLocation") or {}).get("label") or ""
        title = " ".join((p.get("name") or "").split())
        job = {"id": p.get("uuid"), "title": title, "locations": [label],
               "url": p.get("url") or f"https://ats.rippling.com/{slug}/jobs/{p.get('uuid')}",
               "posted": None}
        if title_match(title)[0]:
            try:  # the posting's own record: all locations and the posting date
                info = http.get_json(f"{api}/{p.get('uuid')}")
                job["locations"] = list(info.get("workLocations") or []) or [label]
                job["posted"] = info.get("createdOn")
            except Exception:  # noqa: BLE001 - keep the posting with what the list gave
                pass
        remote = [where(l) for l in job["locations"]]
        job["remote"] = any(r for r, _ in remote) or None
        job["country"] = "India" if any(c for _, c in remote) else None
        jobs.append(job)
    return jobs


def description(http, url):
    """Full description HTML for a posting URL (used by scanner/descriptions.py)."""
    m = re.match(r"https://ats\.rippling\.com/([^/]+)/jobs/([0-9a-f-]+)", url or "")
    if not m:
        raise ValueError("not a Rippling posting URL")
    info = http.get_json(f"{API.format(slug=m.group(1))}/{m.group(2)}")
    d = info.get("description") or {}
    return "\n".join(x for x in (d.get("role"), d.get("company")) if x)
