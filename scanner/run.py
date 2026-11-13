"""Run the scan: every company in config/companies.yaml -> data/*.json.

    python -m scanner.run                 # full scan
    python -m scanner.run --only Citi,Amazon --debug
"""
import argparse
import csv
import json
import os
import re
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone

import yaml

from .adapters import ADAPTERS
from .filters import RECENCY_DAYS, is_recent, location_tags, norm, parse_posted, title_match, today_utc
from . import dates, linkedin
from .http import Http

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
NEW_DAYS = 2  # a job counts as NEW for this many days after we first see it
HISTORY_KEEP = 14   # scans of per-company posting counts kept in data/history.json
DROP_SHARE = 0.4    # flag a company whose postings fall under 40% of its recent average
DROP_MIN_AVG = 5    # ...but only when that average is at least this many postings
VAGUE_RE = re.compile(r"(\d+)\s*\+")  # "30+ Days Ago": the site only says it is older than N days


def load_companies():
    with open(os.path.join(ROOT, "config", "companies.yaml"), encoding="utf-8") as f:
        return yaml.safe_load(f)["companies"]


def reject(company, r, reason, source, tags=(), posted=None):
    return {"company": company, "title": " ".join((r.get("title") or "").split()),
            "location": "; ".join(l for l in r.get("locations") or [] if l)[:120],
            "posted": posted.isoformat() if hasattr(posted, "isoformat") else (r.get("posted") if posted is None else posted),
            "reason": reason, "tags": sorted(tags), "url": r.get("url"), "source": source}


def apply_filters(company, raw, today, source):
    """Part 1 filters. Returns (jobs, title_match_count, rejects).

    rejects are dicts (see reject()) for every posting that had a data title but was dropped,
    plus titles with the word "data" in Pune/Mumbai/remote that the title rules left out.
    They go to data/rejected.json so a missing role can always be explained.
    """
    jobs, rejects, title_matches = [], [], 0
    for r in raw:
        ok, how, borderline = title_match(r.get("title"))
        if not ok:
            if re.search(r"\bdata\b", norm(r.get("title"))):
                tags, _ = location_tags(r.get("locations"), r.get("remote"), r.get("country"))
                if tags:
                    rejects.append(reject(company, r, "title not a data-engineering role", source, tags))
            continue
        title_matches += 1
        tags, notes = location_tags(r.get("locations"), r.get("remote"), r.get("country"))
        raw_posted = r.get("posted")
        vague = VAGUE_RE.search(raw_posted) if isinstance(raw_posted, str) else None
        if vague and int(vague.group(1)) < RECENCY_DAYS:
            posted = None  # "30+ days" could be 31 or 300 days: unknown, not 31
        else:
            posted = parse_posted(raw_posted, today)
        if not tags and not notes:
            rejects.append(reject(company, r, "location", source))
            continue
        if not is_recent(posted, today):
            rejects.append(reject(company, r, f"older than {RECENCY_DAYS} days", source, tags, posted))
            continue
        flags = list(notes)
        if borderline:
            flags.append("title borderline")
        if r.get("name_match") == "loose":
            flags.append(f"company listed as '{r.get('company')}'")
        loc_text = "; ".join(dict.fromkeys(l.strip() for l in r.get("locations") or [] if l and l.strip()))
        job = {
            "company": company, "title": " ".join((r.get("title") or "").split()),
            "location": loc_text, "location_tags": sorted(tags),
            "posted": posted.isoformat() if posted else None,
            "days_ago": (today - posted).days if posted else None,
            "url": r.get("url"), "source": source, "match": how, "flags": flags,
        }
        if vague and not posted:
            job["date_note"] = f"site only says {raw_posted.strip()}"
        jobs.append(job)
    return jobs, title_matches, rejects


def example(rj):
    """Short text for scan_report.json's rejected_examples."""
    if rj["reason"] == "location":
        return f"location: {rj['title']} | {rj['location'][:80]}"
    return f"{rj['reason']}: {rj['title']} | {rj['posted'] or rj['location'][:80]}"


def scan_company(c, today, debug=False):
    started = time.time()
    rep = {"company": c["name"], "platforms": [], "status": "ok", "fetched": 0,
           "title_matches": 0, "matched": 0, "errors": [], "notes": [],
           "careers_url": c.get("careers_url")}
    sources = c.get("sources") or []
    if not sources:
        rep["status"] = "not scanned"
        rep["notes"].append(c.get("note") or "no public job API found")
        return [], rep, []

    http = Http(max_requests=c.get("max_requests", 400))
    raw, ok_sources = [], 0
    for src in sources:
        if src.get("fallback") and raw:
            continue  # primary source worked; fallback not needed
        rep["platforms"].append(src["type"])
        s = dict(src)
        try:
            got = ADAPTERS[src["type"]](s, http)
            raw.extend(got)
            ok_sources += 1
            if s.get("_note"):
                rep["notes"].append(f"{src['type']}: {s['_note']}")
            if s.get("_capped"):  # paging stopped before the site's total: postings were never read
                rep.setdefault("capped", []).extend(f"{src['type']} {x}" for x in s["_capped"])
            if src.get("fallback"):
                rep["notes"].append(f"used fallback source {src['type']}")
        except Exception as e:  # noqa: BLE001
            rep["errors"].append(f"{src['type']}: {type(e).__name__}: {str(e)[:200]}")
            if debug:
                traceback.print_exc()
    rep["fetched"] = len(raw)

    source = "+".join(dict.fromkeys(rep["platforms"]))
    found, rep["title_matches"], rejects = apply_filters(c["name"], raw, today, source)
    # roles with no date: read datePosted from the posting page; drop it if that's too old
    if any(not j["posted"] for j in found):
        if dates.fill_missing(found, http, today):
            for j in [j for j in found if j["posted"] and j["days_ago"] > RECENCY_DAYS]:
                found.remove(j)
                rejects.append({**reject(c["name"], {"title": j["title"], "locations": [j["location"]], "url": j["url"]},
                                         f"older than {RECENCY_DAYS} days", source, j["location_tags"]),
                                "posted": j["posted"]})
            for j in found:
                if j.get("date_source"):
                    j.pop("date_note", None)
    kept = [j for j in found if not j["flags"]]
    uncertain = [j for j in found if j["flags"]]

    rep["matched"] = len(kept) + len(uncertain)
    rep["rejected_examples"] = [example(x) for x in rejects if x["reason"] != "title not a data-engineering role"][:8]
    rep["_rejected"] = rejects  # all of them go to data/rejected.json (popped before the report is written)
    if rep["errors"]:
        rep["status"] = "error" if not ok_sources else "partial"
    rep["requests"] = http.count
    rep["secs"] = round(time.time() - started, 1)
    return kept + uncertain, rep, uncertain


def title_key(title):
    """Comparable title: words sorted, abbreviations expanded, cities and bare numbers dropped
    ("Senior Data Engineer-4" == "Senior Data Engineer"; levels such as II vs III still differ)."""
    from .descriptions import title_words
    return tuple(w for w in title_words(title) if not w.isdigit())


def crosscheck(name, cards, site_jobs, site_drops, today):
    """LinkedIn roles for a company whose careers site was scanned today.

    A LinkedIn role that passes the filters is already covered when the careers site gave a role
    with the same title in an overlapping location (Pune / Mumbai / Navi Mumbai / Remote). If the
    careers site gave that role but dropped it (for example older than the window by its exact
    date), the careers site wins and the LinkedIn copy is logged, not added. Anything else is a
    role the careers-site scan missed: returned with crosscheck=True.
    """
    js, _, rej = apply_filters(name, cards, today, "linkedin")
    seen = [(title_key(j["title"]), set(j["location_tags"]), None) for j in site_jobs] + \
           [(title_key(x["title"]), set(x.get("tags") or []), x["reason"]) for x in site_drops
            if x["reason"] != "title not a data-engineering role"]
    added = []
    for j in js:
        k, tags = title_key(j["title"]), set(j["location_tags"])
        same = [why for key, t, why in seen if key == k and ((t & tags) if tags else t)]
        if same:
            if all(same):  # only dropped copies on the careers site
                rej.append({**reject(name, {"title": j["title"], "locations": [j["location"]], "url": j["url"]},
                                     f"on LinkedIn, but the careers site dropped it ({same[0]})", "linkedin",
                                     j["location_tags"]), "posted": j["posted"]})
            continue
        j["crosscheck"] = True
        added.append(j)
    return added, rej


def fill_dates_from_linkedin(site_jobs, cards, today, jobs, rejected):
    """Careers-site roles still without a date take LinkedIn's date for the same title and city."""
    for j in [j for j in site_jobs if not j.get("posted")]:
        for card in cards or []:
            tags, _ = location_tags(card.get("locations"), card.get("remote"), card.get("country"))
            d = parse_posted(card.get("posted"), today)
            if d and title_key(card.get("title")) == title_key(j["title"]) and set(tags) & set(j["location_tags"]):
                j["posted"], j["days_ago"], j["date_source"] = d.isoformat(), (today - d).days, "LinkedIn"
                j.pop("date_note", None)
                if j["days_ago"] > RECENCY_DAYS:
                    jobs.remove(j)
                    rejected.append({**reject(j["company"], {"title": j["title"], "locations": [j["location"]],
                                                             "url": j["url"]}, f"older than {RECENCY_DAYS} days",
                                              j["source"], j["location_tags"]), "posted": j["posted"]})
                break


def scanner_alerts(reports, history, stamp):
    """Flag companies whose scan looks broken: postings far below their recent average, or
    paging that stopped before the site's total. Also appends this scan's counts to history."""
    alerts = []
    for r in reports:
        name = r["company"]
        if r.get("status") == "not scanned":
            continue
        past = [n for _, n in history.get(name, [])][-7:]
        n = r.get("fetched", 0)
        if r.get("status") == "error":
            pass  # already shown as "couldn't be reached"
        elif len(past) >= 3:
            avg = sum(past) / len(past)
            if avg >= DROP_MIN_AVG and n < DROP_SHARE * avg:
                alerts.append({"company": name, "why": f"read {n} postings, usually about {round(avg)}"})
        if r.get("capped"):
            alerts.append({"company": name, "why": "stopped paging early: " + "; ".join(r["capped"])[:150]})
        if r.get("status") != "error":
            history[name] = (history.get(name, []) + [[stamp, n]])[-HISTORY_KEEP:]
    for a in alerts:
        next(r for r in reports if r["company"] == a["company"]).setdefault("alerts", []).append(a["why"])
    return alerts


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        return default


def job_key(j):
    return f"{norm(j['company'])}|{norm(j['title'])}|{j.get('url') or ''}"


def dedupe(jobs):
    """Drop repeats of one posting (reached through two search terms or two sources). Two openings
    with the same title and city but different posting dates are different jobs and both stay."""
    out, seen = [], set()
    for j in jobs:
        k = (norm(j["company"]), norm(j["title"]), tuple(j["location_tags"]), norm(j["location"]),
             j.get("posted") or j.get("url"))
        if k in seen:
            continue
        seen.add(k)
        out.append(j)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="comma-separated company names")
    ap.add_argument("--workers", type=int, default=10)
    ap.add_argument("--debug", action="store_true")
    ap.add_argument("--no-write", action="store_true")
    ap.add_argument("--no-linkedin", action="store_true", help="skip the LinkedIn fallback")
    args = ap.parse_args()

    today = today_utc()
    companies = load_companies()
    if args.only:
        wanted = {norm(x) for x in args.only.split(",")}
        companies = [c for c in companies if norm(c["name"]) in wanted]

    jobs, reports = [], []
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(scan_company, c, today, args.debug): c for c in companies}
        for fut in as_completed(futs):
            c = futs[fut]
            try:
                js, rep, _ = fut.result()
            except Exception as e:  # noqa: BLE001
                js, rep = [], {"company": c["name"], "status": "error", "errors": [str(e)[:200]]}
            jobs.extend(js)
            reports.append(rep)
            print(f"{rep['company'][:28]:28} {rep['status']:11} fetched={rep.get('fetched', 0):4} "
                  f"matched={rep.get('matched', 0):3} {'; '.join(rep.get('errors', []))[:120]}", flush=True)

    rejected = []
    for rep in reports:
        rejected.extend(rep.pop("_rejected", None) or [])

    # LinkedIn: (1) the only source for companies whose own site gave us nothing today;
    # (2) a cross-check for every other company: roles LinkedIn has that the careers site
    # didn't give us are added with crosscheck=true ("missed by careers site" on the board)
    linkedin_note, crosscheck_note = "skipped", "skipped"
    if not args.no_linkedin:
        now = datetime.now(timezone.utc)
        cache_path = os.path.join(DATA, "linkedin_cache.json")
        by_name = {c["name"]: c for c in companies}
        rep_by = {r["company"]: r for r in reports}
        targets = [by_name[r["company"]] for r in reports
                   if r["company"] in by_name and not by_name[r["company"]].get("skip_linkedin")
                   and linkedin.needs_linkedin(r)]
        keep = [c["name"] for c in load_companies()]
        found, when = {}, {}
        if targets:
            li_http = Http(max_requests=len(targets) * linkedin.PAGES * 3 + 10)
            found, when, linkedin_note = linkedin.run_fallback(targets, li_http, cache_path, now, keep=keep)
        cache = linkedin.load_cache(cache_path)
        remote, remote_at = linkedin.remote_jobs(cache, now)
        for c in targets:
            name = c["name"]
            cards = linkedin.add_unique(found.get(name), remote.get(name))
            if name not in when and not remote.get(name):
                continue
            rep = rep_by[name]
            js, tm, rej = apply_filters(name, cards, today, "linkedin")
            rep["linkedin"] = {"checked_at": when.get(name) or remote_at, "fetched": len(cards),
                               "matched": len(js), "rejected_examples": [example(x) for x in rej][:5]}
            rep["matched"] = rep.get("matched", 0) + len(js)
            rejected.extend(rej)
            jobs.extend(js)
        print(f"LinkedIn: {linkedin_note}; {sum(1 for j in jobs if j['source'] == 'linkedin')} roles", flush=True)

        fallback = {c["name"] for c in targets}
        site_jobs = {}
        for j in jobs:
            if j["source"] != "linkedin":
                site_jobs.setdefault(j["company"], []).append(j)
        site_drops = {}
        for x in rejected:
            if x["source"] != "linkedin":
                site_drops.setdefault(x["company"], []).append(x)
        checked, added = 0, []
        for c in companies:
            name = c["name"]
            if name in fallback or c.get("skip_linkedin"):
                continue
            own = linkedin.fresh_entry(cache, name, now)
            cards = linkedin.add_unique((own or {}).get("jobs"), remote.get(name))
            if own:
                checked += 1
            # a role with no date on the careers site: use LinkedIn's date for the same title
            fill_dates_from_linkedin(site_jobs.get(name, []), cards, today, jobs, rejected)
            if not own and not remote.get(name):
                continue
            new, rej = crosscheck(name, cards, site_jobs.get(name, []), site_drops.get(name, []), today)
            rejected.extend(rej)
            jobs.extend(new)
            added.extend(new)
            rep_by[name]["linkedin_crosscheck"] = {
                "checked_at": (own or {}).get("checked_at") or remote_at, "fetched": len(cards),
                "missed_by_site": len(new)}
            rep_by[name]["matched"] = rep_by[name].get("matched", 0) + len(new)
        crosscheck_note = (f"{checked} of {len(companies) - len(fallback)} scanned companies cross-checked on "
                           f"LinkedIn in the last {linkedin.CACHE_DAYS} days; {len(added)} roles the careers sites missed")
        print(f"LinkedIn cross-check: {crosscheck_note}", flush=True)

    jobs = dedupe(jobs)

    # first-seen tracking -> NEW badge
    seen_path = os.path.join(DATA, "seen.json")
    try:
        with open(seen_path, encoding="utf-8") as f:
            seen = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        seen = {}
    # roles found by the very first scan are the baseline, not "new"
    baseline = seen.pop("_baseline", None) or (min(seen.values()) if seen else today.isoformat())
    had_history = bool(seen)
    for j in jobs:
        k = job_key(j)
        j["fresh"] = had_history and k not in seen  # first appeared in this run (never on the first-ever run)
        seen.setdefault(k, today.isoformat())
        j["first_seen"] = seen[k]
        j["new"] = seen[k] != baseline and (today - datetime.fromisoformat(seen[k]).date()).days < NEW_DAYS
    cutoff = (today - timedelta(days=120)).isoformat()
    live = {job_key(j) for j in jobs}
    seen = {k: v for k, v in seen.items() if k in live or v >= cutoff}
    seen["_baseline"] = baseline

    jobs.sort(key=lambda j: (j["company"], j["days_ago"] if j["days_ago"] is not None else 999, j["title"]))
    reports.sort(key=lambda r: r["company"])
    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    history_path = os.path.join(DATA, "history.json")
    history = load_json(history_path, {})
    alerts = scanner_alerts(reports, history, generated_at) if not args.only else []
    meta = {
        "generated_at": generated_at,
        "date": today.isoformat(), "recency_days": RECENCY_DAYS,
        "companies_total": len(reports),
        "companies_scanned_ok": sum(1 for r in reports if r["status"] in ("ok", "partial")),
        "companies_failed": sorted(r["company"] for r in reports if r["status"] == "error"),
        "companies_not_scanned": sorted(r["company"] for r in reports if r["status"] == "not scanned"),
        "jobs": len(jobs), "companies_with_jobs": len({j["company"] for j in jobs}),
        "uncertain": sum(1 for j in jobs if j["flags"]),
        "fresh": sum(1 for j in jobs if j.get("fresh")),
        "linkedin": linkedin_note,
        "companies_checked_on_linkedin": sorted(r["company"] for r in reports if "linkedin" in r),
        "linkedin_crosscheck": crosscheck_note,
        "missed_by_site": sum(1 for j in jobs if j.get("crosscheck")),
        "scanner_alerts": alerts,
        "rejected": len(rejected),
    }
    if args.no_write:
        print(json.dumps(meta, indent=1))
        return

    os.makedirs(DATA, exist_ok=True)
    with open(os.path.join(DATA, "jobs.json"), "w", encoding="utf-8") as f:
        json.dump({"meta": meta, "jobs": jobs}, f, indent=1, ensure_ascii=False)
    with open(os.path.join(DATA, "scan_report.json"), "w", encoding="utf-8") as f:
        json.dump({"meta": meta, "companies": reports}, f, indent=1, ensure_ascii=False)
    with open(seen_path, "w", encoding="utf-8") as f:
        json.dump(dict(sorted(seen.items())), f, indent=0, ensure_ascii=False)
    # every role with a data title that was dropped, and why (so a missing role can be explained)
    rejected.sort(key=lambda x: (x["company"], x["reason"], x["title"]))
    with open(os.path.join(DATA, "rejected.json"), "w", encoding="utf-8") as f:
        json.dump({"meta": {"generated_at": generated_at, "date": today.isoformat(),
                            "recency_days": RECENCY_DAYS, "count": len(rejected)},
                   "rejected": rejected}, f, indent=0, ensure_ascii=False)
    if not args.only:
        with open(history_path, "w", encoding="utf-8") as f:
            json.dump(dict(sorted(history.items())), f, separators=(",", ":"), ensure_ascii=False)
    with open(os.path.join(DATA, "jobs.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["company", "title", "location", "location_tags", "posted", "days_ago",
                    "new", "flags", "url"])
        for j in jobs:
            w.writerow([j["company"], j["title"], j["location"], "|".join(j["location_tags"]),
                        j["posted"] or "", "" if j["days_ago"] is None else j["days_ago"],
                        "Y" if j["new"] else "", "; ".join(j["flags"]), j["url"]])
    print(json.dumps(meta, indent=1))


if __name__ == "__main__":
    main()
