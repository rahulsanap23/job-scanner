"""Refresh LinkedIn results for a few companies at a time, between the daily scans.

    python -m scanner.linkedin_refresh               # one batch (run by the linkedin-refresh workflow)
    python -m scanner.linkedin_refresh --batch 4 --dry-run
    python -m scanner.linkedin_refresh --merge OURS  # merge OURS into data/linkedin_cache.json (used before a push)

LinkedIn blocks a machine after a dozen or so searches, so the daily scan alone can't
check every company that needs it. This runs every couple of hours from a fresh GitHub
runner, searches a small batch and adds the results to data/linkedin_cache.json, which
the next daily scan reads. It never touches jobs.json, the scan report or the board.

It also looks up a few job descriptions on LinkedIn: roles whose own careers site gave the
daily scan no description (marked needs_linkedin in data/descriptions.json) are searched by
company and title, and only a posting with the same title is used (see
scanner/descriptions.py, linkedin_lookup). The scheduled Claude task then picks those roles'
skills on its next run.

Which companies: the ones the latest scan sent to LinkedIn (no usable careers feed, or a
site that failed or returned nothing). Companies whose site failed in that scan come
first, and their site is retried once before searching LinkedIn: if it answers now, the
next scan will read it and LinkedIn isn't needed. The rest go least recently checked first.
"""
import argparse
import os
from datetime import datetime, timezone

from . import descriptions, linkedin
from .http import Http
from .run import DATA, load_companies, scan_company
from .filters import today_utc

BATCH = 8
CACHE = os.path.join(DATA, "linkedin_cache.json")
REPORT = os.path.join(DATA, "scan_report.json")


def load_report(path=REPORT):
    import json
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data.get("meta") or {}, data.get("companies") or []
    except (FileNotFoundError, ValueError):
        return {}, []


def needs_linkedin(rep):
    """Same rule as the daily scan: no usable feed, a failed site, or a site that returned nothing."""
    return rep.get("status") in ("not scanned", "error") or rep.get("fetched", 0) == 0


def site_failed(rep):
    return rep.get("status") != "not scanned" and needs_linkedin(rep)


def plan(companies, reports, cache, scanned_at, batch=BATCH):
    """Return (retry_first, queue): sites to retry, then companies in LinkedIn search order."""
    by_name = {c["name"]: c for c in companies}
    wanted = [(by_name[r["company"]], r) for r in reports
              if r.get("company") in by_name and not by_name[r["company"]].get("skip_linkedin")
              and needs_linkedin(r)]

    def checked(c):
        return (cache.get(c["name"]) or {}).get("checked_at", "")

    # failed in the latest scan and not looked at on LinkedIn since: these go first
    urgent = [c for c, r in wanted if site_failed(r) and checked(c) < (scanned_at or "")]
    first = {c["name"] for c in urgent}
    rest = sorted((c for c, _ in wanted if c["name"] not in first), key=checked)
    return urgent, (urgent + rest)[:batch]


def refresh(batch=BATCH, dry_run=False, log=print):
    meta, reports = load_report()
    if not reports:
        log("no scan report yet; nothing to refresh")
        return
    companies = load_companies()
    cache = linkedin.load_cache(CACHE)
    urgent, queue = plan(companies, reports, cache, meta.get("generated_at"), batch)

    today = today_utc()
    recovered = set()
    for c in urgent:
        if c not in queue:
            continue
        try:
            _, rep, _ = scan_company(c, today)
        except Exception as e:  # noqa: BLE001
            log(f"{c['name']}: site retry failed ({type(e).__name__})")
            continue
        if not needs_linkedin(rep):
            recovered.add(c["name"])
            log(f"{c['name']}: careers site answers again ({rep.get('fetched')} postings); the next scan will read it")
    queue = [c for c in queue if c["name"] not in recovered]

    log(f"LinkedIn batch ({len(queue)}): {', '.join(c['name'] for c in queue) or '-'}")
    if dry_run:
        return
    if queue:
        http = Http(max_requests=len(queue) * linkedin.PAGES * 3 + 10)
        found, checked, note = linkedin.scan_targets(queue, http, log=log)
        linkedin.record(cache, found, checked, datetime.now(timezone.utc))
        linkedin.save_cache(CACHE, cache)
        log(f"LinkedIn: {note}; {sum(len(found.get(n) or []) for n in checked)} postings from {len(checked)} companies")
        if "LinkedIn answered" in note:
            log("descriptions: skipping LinkedIn lookups this run (rate limited)")
            return
    # roles whose careers site gave no description: look a few up on LinkedIn (same company + title)
    try:
        descriptions.linkedin_lookup(log=log)
    except Exception as e:  # noqa: BLE001 - never let this break the company refresh
        log(f"descriptions: LinkedIn lookup skipped ({type(e).__name__}: {str(e)[:120]})")


def merge(ours_path, into=CACHE, now=None):
    """Merge a cache file into `into` (newest entry per company wins), dropping entries older than CACHE_DAYS."""
    from datetime import timedelta
    now = now or datetime.now(timezone.utc)
    cutoff = (now - timedelta(days=linkedin.CACHE_DAYS)).isoformat(timespec="seconds")
    merged = linkedin.merge_caches(linkedin.load_cache(into), linkedin.load_cache(ours_path))
    linkedin.save_cache(into, {k: v for k, v in merged.items() if v.get("checked_at", "") >= cutoff})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch", type=int, default=BATCH)
    ap.add_argument("--dry-run", action="store_true", help="print the plan, search nothing")
    ap.add_argument("--merge", metavar="OURS", help="merge this cache file into data/linkedin_cache.json and exit")
    args = ap.parse_args()
    if args.merge:
        merge(args.merge)
    else:
        refresh(args.batch, args.dry_run)


if __name__ == "__main__":
    main()
