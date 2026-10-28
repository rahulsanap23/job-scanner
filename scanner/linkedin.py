"""LinkedIn fallback for target companies whose own careers site gave us nothing today.

Uses LinkedIn's public, logged-out job search (the same results anyone sees without
signing in). One search per company, two pages at most, paced, and it stops at the
first sign of rate limiting. No LinkedIn account is involved.

Results are kept per company in data/linkedin_cache.json. The linkedin-refresh workflow
(scanner/linkedin_refresh.py) fills it in small batches through the day; the daily scan
reads it and only searches companies the cache hasn't covered in the last FRESH_HOURS.
"""
import html
import json
import random
import re
import time
import unicodedata
from difflib import SequenceMatcher

import requests

API = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
PAUSE_SECS = 4.0   # plus up to 4 s of jitter between requests
BACKOFF = (30, 90)  # waits before retrying a 429; then give up for this run
PAGES = 1          # 10 results per page; the search is limited to Maharashtra, so one page is enough
CACHE_DAYS = 4     # companies LinkedIn didn't let us check this run reuse results this recent
FRESH_HOURS = 24   # a company checked this recently isn't searched again by the daily scan
TIME_BUDGET = 600  # seconds; the rest of the companies wait for the next run
CARD = re.compile(r"<li>(.*?)</li>", re.S)
FILLER = {"india", "pvt", "private", "ltd", "limited", "inc", "llc", "plc", "corp", "corporation", "co",
          "company", "the", "group", "technologies", "technology", "solutions", "solution", "services",
          "global", "systems", "software", "ventures", "centre", "center", "com", "and", "of", "gsc"}


class RateLimited(Exception):
    pass


def core(name):
    """Company name reduced to its identifying words (accents, punctuation, legal suffixes removed)."""
    t = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode().lower()
    t = re.sub(r"\(.*?\)", " ", t.replace("&", " and "))
    words = [w for w in re.findall(r"[a-z0-9]+", t) if w not in FILLER]
    return words


def name_match(target_names, company):
    """Return 'exact', 'loose' or None for a LinkedIn company name against a target's names."""
    c = core(company)
    if not c:
        return None
    for n in target_names:
        t = core(n)
        if not t:
            continue
        if t == c or SequenceMatcher(None, " ".join(t), " ".join(c)).ratio() >= 0.92:
            return "exact"
    for n in target_names:
        t = core(n)
        if t and set(t) <= set(c) and (len(t) >= 2 or len(c) <= 2):
            return "loose"
    return None


def _t(s):
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", s or "")).split())


def parse(page):
    cards = []
    for li in CARD.findall(page):
        title = re.search(r'class="base-search-card__title"[^>]*>(.*?)</h3>', li, re.S)
        comp = re.search(r'class="base-search-card__subtitle"[^>]*>(.*?)</h4>', li, re.S)
        loc = re.search(r'class="job-search-card__location"[^>]*>(.*?)</span>', li, re.S)
        when = re.search(r'<time[^>]*datetime="([^"]+)"', li)
        link = re.search(r'class="base-card__full-link[^"]*"[^>]*href="([^"]+)"', li) or \
            re.search(r'href="(https://[a-z]+\.linkedin\.com/jobs/view/[^"]+)"', li)
        if not (title and comp and link):
            continue
        url = html.unescape(link.group(1)).split("?")[0]
        cards.append({"id": url, "title": _t(title.group(1)), "company": _t(comp.group(1)),
                      "locations": [_t(loc.group(1)) if loc else ""], "url": url,
                      "posted": when.group(1) if when else None, "remote": None, "country": None})
    return cards


def search_name(c):
    return re.sub(r"\s*\(.*?\)", "", c["name"]).strip()


def scan_targets(targets, http, log=print):
    """targets: company dicts from config. Returns ({name: [raw jobs]}, [names checked], note)."""
    found = {c["name"]: [] for c in targets}
    names = {c["name"]: [c["name"], search_name(c)] + list(c.get("aliases") or []) for c in targets}
    seen = set()
    done, checked = 0, []
    sess = requests.Session()  # no automatic retries: back off at the first 429
    sess.headers.update(http.s.headers)
    sess.headers["Accept"] = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
    started = time.time()
    try:
        for c in targets:
            if time.time() - started > TIME_BUDGET:
                raise RateLimited(f"time budget of {TIME_BUDGET // 60} min used")
            for page in range(PAGES):
                params = {"keywords": f"data engineer {search_name(c)}", "location": "Maharashtra, India",
                          "f_TPR": "r2592000", "start": page * 10}
                for wait in (0,) + BACKOFF:
                    time.sleep(wait or PAUSE_SECS + random.random() * 4)
                    r = sess.get(API, params=params, timeout=20)
                    http.count += 1
                    if r.status_code not in (429, 999):
                        break
                if r.status_code in (429, 999) or (r.status_code >= 400 and r.status_code != 404):
                    raise RateLimited(f"LinkedIn answered {r.status_code}")
                cards = parse(r.text) if r.status_code == 200 else []
                for card in cards:
                    if card["id"] in seen:
                        continue
                    for name, alts in names.items():
                        how = name_match(alts, card["company"])
                        if how:
                            seen.add(card["id"])
                            card["name_match"] = how
                            found[name].append(card)
                            break
                if len(cards) < 10:
                    break
            done += 1
            checked.append(c["name"])
    except RateLimited as e:
        note = f"stopped after {done} of {len(targets)} companies: {e}"
        log(f"LinkedIn {note}")
        return found, checked, note
    except Exception as e:  # noqa: BLE001
        note = f"stopped after {done} of {len(targets)} companies: {type(e).__name__}: {str(e)[:120]}"
        log(f"LinkedIn {note}")
        return found, checked, note
    return found, checked, f"searched {done} companies"


def load_cache(path):
    try:
        with open(path, encoding="utf-8") as f:
            cache = json.load(f)
        return cache if isinstance(cache, dict) else {}
    except (FileNotFoundError, ValueError):
        return {}


def save_cache(path, cache):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(dict(sorted(cache.items())), f, indent=0, ensure_ascii=False)


def merge_caches(*caches):
    """Per company, keep the most recently checked entry (so two writers never lose each other's work)."""
    out = {}
    for cache in caches:
        for name, entry in (cache or {}).items():
            if not isinstance(entry, dict):
                continue
            if name not in out or entry.get("checked_at", "") > out[name].get("checked_at", ""):
                out[name] = entry
    return out


def record(cache, found, checked, now):
    stamp = now.isoformat(timespec="seconds")
    for name in checked:
        cache[name] = {"checked_at": stamp, "jobs": found.get(name) or []}
    return cache


def run_fallback(targets, http, cache_path, now):
    """Fill LinkedIn results for `targets` from the cache, searching only companies it hasn't covered lately.

    The refresh workflow normally keeps every company checked within FRESH_HOURS, so this
    usually searches few or none. Companies LinkedIn didn't let us check reuse results up to
    CACHE_DAYS old. Returns ({name: [raw jobs]}, {name: checked_at iso}, note).
    """
    from datetime import timedelta
    cache = load_cache(cache_path)
    fresh = (now - timedelta(hours=FRESH_HOURS)).isoformat(timespec="seconds")
    stale = [c for c in targets if (cache.get(c["name"]) or {}).get("checked_at", "") < fresh]
    stale.sort(key=lambda c: (cache.get(c["name"]) or {}).get("checked_at", ""))
    run_note = "searched 0 companies"
    if stale:
        found, checked, run_note = scan_targets(stale, http)
        record(cache, found, checked, now)
    cutoff = (now - timedelta(days=CACHE_DAYS)).isoformat(timespec="seconds")
    results, when = {}, {}
    for c in targets:
        entry = cache.get(c["name"])
        if entry and entry.get("checked_at", "") >= cutoff:
            results[c["name"]] = entry.get("jobs") or []
            when[c["name"]] = entry["checked_at"]
    live = {c["name"] for c in targets}
    save_cache(cache_path, {k: v for k, v in cache.items() if k in live and v.get("checked_at", "") >= cutoff})
    return results, when, coverage_note(targets, cache, now, run_note)


def coverage_note(targets, cache, now, run_note):
    """e.g. '45 of 45 companies checked in the last 24 h (this scan: searched 0 companies)'."""
    from datetime import timedelta
    fresh = (now - timedelta(hours=FRESH_HOURS)).isoformat(timespec="seconds")
    missing = [c["name"] for c in targets if (cache.get(c["name"]) or {}).get("checked_at", "") < fresh]
    note = f"{len(targets) - len(missing)} of {len(targets)} companies checked in the last {FRESH_HOURS} h"
    if missing:
        more = f" and {len(missing) - 8} more" if len(missing) > 8 else ""
        note += f"; not checked in {FRESH_HOURS} h: {', '.join(missing[:8])}{more}"
    return f"{note} (this scan: {run_note})"
