"""LinkedIn fallback for target companies whose own careers site gave us nothing today.

Uses LinkedIn's public, logged-out job search (the same results anyone sees without
signing in). One search per company, two pages at most, paced, and it stops at the
first sign of rate limiting. No LinkedIn account is involved.
"""
import html
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


def run_fallback(targets, http, cache_path, now):
    """Search LinkedIn for `targets`, least recently checked first, and fill gaps from the cache.

    LinkedIn usually stops answering after a dozen or so searches from one machine, so each
    run checks as many companies as it can and older results (up to CACHE_DAYS) cover the rest.
    Returns ({name: [raw jobs]}, {name: checked_at iso}, note).
    """
    import json
    from datetime import datetime, timedelta
    try:
        with open(cache_path, encoding="utf-8") as f:
            cache = json.load(f)
    except (FileNotFoundError, ValueError):
        cache = {}
    order = sorted(targets, key=lambda c: (cache.get(c["name"]) or {}).get("checked_at", ""))
    found, checked, note = scan_targets(order, http)
    stamp = now.isoformat(timespec="seconds")
    for name in checked:
        cache[name] = {"checked_at": stamp, "jobs": found.get(name) or []}
    cutoff = (now - timedelta(days=CACHE_DAYS)).isoformat(timespec="seconds")
    results, when = {}, {}
    for c in targets:
        entry = cache.get(c["name"])
        if entry and entry.get("checked_at", "") >= cutoff:
            results[c["name"]] = entry.get("jobs") or []
            when[c["name"]] = entry["checked_at"]
    live = {c["name"] for c in targets}
    cache = {k: v for k, v in cache.items() if k in live and v.get("checked_at", "") >= cutoff}
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=0, ensure_ascii=False)
    return results, when, f"{note}; {len(when)} of {len(targets)} companies have LinkedIn results from the last {CACHE_DAYS} days"
