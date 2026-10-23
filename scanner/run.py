"""Run the scan: every company in config/companies.yaml -> data/*.json.

    python -m scanner.run                 # full scan
    python -m scanner.run --only Citi,Amazon --debug
"""
import argparse
import csv
import json
import os
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone

import yaml

from .adapters import ADAPTERS
from .filters import is_recent, location_tags, norm, parse_posted, title_match, today_utc
from .http import Http

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
NEW_DAYS = 2  # a job counts as NEW for this many days after we first see it


def load_companies():
    with open(os.path.join(ROOT, "config", "companies.yaml"), encoding="utf-8") as f:
        return yaml.safe_load(f)["companies"]


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
            if src.get("fallback"):
                rep["notes"].append(f"used fallback source {src['type']}")
        except Exception as e:  # noqa: BLE001
            rep["errors"].append(f"{src['type']}: {type(e).__name__}: {str(e)[:200]}")
            if debug:
                traceback.print_exc()
    rep["fetched"] = len(raw)

    kept, uncertain, rejects = [], [], []
    for r in raw:
        ok, how, borderline = title_match(r.get("title"))
        if not ok:
            continue
        rep["title_matches"] += 1
        tags, notes = location_tags(r.get("locations"), r.get("remote"), r.get("country"))
        posted = parse_posted(r.get("posted"), today)
        if not tags and not notes:
            rejects.append(f"location: {r.get('title')} | {'; '.join(l for l in r.get('locations') or [] if l)[:80]}")
            continue
        if not is_recent(posted, today):
            rejects.append(f"older than 30d: {r.get('title')} | {r.get('posted')}")
            continue
        flags = list(notes)
        if borderline:
            flags.append("title borderline")
        loc_text = "; ".join(dict.fromkeys(l.strip() for l in r.get("locations") or [] if l and l.strip()))
        job = {
            "company": c["name"], "title": " ".join((r.get("title") or "").split()),
            "location": loc_text, "location_tags": sorted(tags),
            "posted": posted.isoformat() if posted else None,
            "days_ago": (today - posted).days if posted else None,
            "url": r.get("url"), "source": "+".join(dict.fromkeys(rep["platforms"])),
            "match": how, "flags": flags,
        }
        (uncertain if flags else kept).append(job)

    rep["matched"] = len(kept) + len(uncertain)
    rep["rejected_examples"] = rejects[:8]  # title matched but location/date didn't: helps spot parsing bugs
    if rep["errors"]:
        rep["status"] = "error" if not ok_sources else "partial"
    rep["requests"] = http.count
    rep["secs"] = round(time.time() - started, 1)
    return kept + uncertain, rep, uncertain


def job_key(j):
    return f"{norm(j['company'])}|{norm(j['title'])}|{j.get('url') or ''}"


def dedupe(jobs):
    out, seen = [], set()
    for j in jobs:
        k = (norm(j["company"]), norm(j["title"]), tuple(j["location_tags"]), norm(j["location"]))
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

    jobs = dedupe(jobs)

    # first-seen tracking -> NEW badge
    seen_path = os.path.join(DATA, "seen.json")
    try:
        with open(seen_path, encoding="utf-8") as f:
            seen = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        seen = {}
    first_run = not seen
    for j in jobs:
        k = job_key(j)
        seen.setdefault(k, today.isoformat())
        j["first_seen"] = seen[k]
        j["new"] = (not first_run) and (today - datetime.fromisoformat(seen[k]).date()).days < NEW_DAYS
    cutoff = (today - timedelta(days=120)).isoformat()
    live = {job_key(j) for j in jobs}
    seen = {k: v for k, v in seen.items() if k in live or v >= cutoff}

    jobs.sort(key=lambda j: (j["company"], j["days_ago"] if j["days_ago"] is not None else 999, j["title"]))
    reports.sort(key=lambda r: r["company"])
    meta = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "date": today.isoformat(), "recency_days": 30,
        "companies_total": len(reports),
        "companies_scanned_ok": sum(1 for r in reports if r["status"] in ("ok", "partial")),
        "companies_failed": sorted(r["company"] for r in reports if r["status"] == "error"),
        "companies_not_scanned": sorted(r["company"] for r in reports if r["status"] == "not scanned"),
        "jobs": len(jobs), "companies_with_jobs": len({j["company"] for j in jobs}),
        "uncertain": sum(1 for j in jobs if j["flags"]),
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
