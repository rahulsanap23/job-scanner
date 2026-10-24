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
PAUSE_SECS = 3.0   # plus up to 3 s of jitter between requests
BACKOFF = (30, 90)  # waits before retrying a 429; then give up for this run
PAGES = 2          # 10 results per page
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
    try:
        for c in targets:
            for page in range(PAGES):
                params = {"keywords": f"data engineer {search_name(c)}", "location": "India",
                          "f_TPR": "r2592000", "start": page * 10}
                for wait in (0,) + BACKOFF:
                    time.sleep(wait or PAUSE_SECS + random.random() * 3)
                    r = sess.get(API, params=params, timeout=30)
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
