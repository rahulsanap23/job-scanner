"""Refresh LinkedIn results for a few companies at a time, between the daily scans.

    python -m scanner.linkedin_refresh               # one batch (run by the linkedin-refresh workflow)
    python -m scanner.linkedin_refresh --batch 4 --dry-run
    python -m scanner.linkedin_refresh --merge OURS  # merge OURS into data/linkedin_cache.json (used before a push)

LinkedIn blocks a machine after a dozen or so searches, so the daily scan alone can't
check every company that needs it. This runs every hour from a fresh GitHub
runner, searches a small batch and adds the results to data/linkedin_cache.json, which
the next daily scan reads. It never touches jobs.json, the scan report or the board.

It also looks up a few job descriptions on LinkedIn: roles whose own careers site gave the
daily scan no description (marked needs_linkedin in data/descriptions.json) are searched by
company and title, and only a posting with the same title is used (see
scanner/descriptions.py, linkedin_lookup). The scheduled Claude task then picks those roles'
skills on its next run.

Which companies (8 per run, every hour):
- Fallback companies: the ones the latest scan sent to LinkedIn (no usable careers feed, or a
  site that failed or returned nothing). The list is rebuilt from the latest scan on every
  run, so it grows and shrinks as sites fail and recover. Each is due again FALLBACK_HOURS
  after its last check, so it is searched about twice a day. Companies whose site failed in
  that scan come first, and their site is retried once before searching LinkedIn: if it
  answers now, the next scan will read it and LinkedIn isn't needed.
- Cross-check companies: every other company. Their careers site is read by the scan; LinkedIn
  is a second source that catches roles the site scan missed. Least recently checked first.
  At least a third of each run's searches (CROSSCHECK_SHARE) are kept for them, so the
  cross-check never stops even when many sites fail on the same day.
- Every REMOTE_HOURS, one general "data engineer, remote, India" search (REMOTE_PAGES pages)
  matched against every target company, for remote roles the Maharashtra searches don't show.
Slots one group doesn't need go to the other.
"""
import argparse
import math
import os
from datetime import datetime, timedelta, timezone

from . import descriptions, linkedin
from .http import Http
from .run import DATA, load_companies, scan_company
from .filters import today_utc

BATCH = 8
FALLBACK_HOURS = 11     # a fallback company is due again this long after its last check (~twice a day)
CROSSCHECK_HOURS = 20   # a cross-check company isn't searched again sooner than this
CROSSCHECK_SHARE = 1 / 3
REMOTE_HOURS = 12
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


needs_linkedin = linkedin.needs_linkedin


def site_failed(rep):
    return rep.get("status") != "not scanned" and needs_linkedin(rep)


def remote_due(cache, now):
    last = (cache.get(linkedin.REMOTE_KEY) or {}).get("checked_at", "")
    return last < (now - timedelta(hours=REMOTE_HOURS)).isoformat(timespec="seconds")


def plan(companies, reports, cache, scanned_at, batch=BATCH, now=None):
    """Return (retry_first, queue): sites to retry, then the companies to search this run, in order.

    batch is the number of company searches this run. Fallback companies that are due (urgent
    first) take the slots not reserved for the cross-check; the cross-check takes at least
    ceil(batch * CROSSCHECK_SHARE); unused slots go to the other group.
    """
    now = now or datetime.now(timezone.utc)
    by_name = {c["name"]: c for c in companies}
    reported = {r.get("company") for r in reports}
    usable = [(by_name[r["company"]], r) for r in reports
              if r.get("company") in by_name and not by_name[r["company"]].get("skip_linkedin")]
    wanted = [(c, r) for c, r in usable if needs_linkedin(r)]
    others = [c for c, r in usable if not needs_linkedin(r)] + \
             [c for c in companies if c["name"] not in reported and not c.get("skip_linkedin")]

    def checked(c):
        return (cache.get(c["name"]) or {}).get("checked_at", "")

    def iso(hours):
        return (now - timedelta(hours=hours)).isoformat(timespec="seconds")

    # failed in the latest scan and not looked at on LinkedIn since: these go first
    urgent = [c for c, r in wanted if site_failed(r) and checked(c) < (scanned_at or "")]
    first = {c["name"] for c in urgent}
    rest = sorted((c for c, _ in wanted if c["name"] not in first), key=checked)
    fb_due = urgent + [c for c in rest if checked(c) < iso(FALLBACK_HOURS)]
    fb_later = [c for c in rest if c not in fb_due]
    xc = sorted(others, key=checked)
    xc_due = [c for c in xc if checked(c) < iso(CROSSCHECK_HOURS)]
    xc_later = [c for c in xc if c not in xc_due]

    reserve = min(len(xc_due), math.ceil(batch * CROSSCHECK_SHARE))
    queue = fb_due[:batch - reserve]
    queue += xc_due[:batch - len(queue)]
    for pool in (fb_due, fb_later, xc_later):  # fill any slots left over
        queue += [c for c in pool if c not in queue][:batch - len(queue)]
    return urgent, queue


def refresh(batch=BATCH, dry_run=False, log=print):
    meta, reports = load_report()
    if not reports:
        log("no scan report yet; nothing to refresh")
        return
    companies = load_companies()
    cache = linkedin.load_cache(CACHE)
    now = datetime.now(timezone.utc)
    do_remote = remote_due(cache, now)
    slots = max(1, batch - linkedin.REMOTE_PAGES) if do_remote else batch  # the remote search uses a few slots
    urgent, queue = plan(companies, reports, cache, meta.get("generated_at"), slots, now)
    fallback = {r["company"] for r in reports if needs_linkedin(r)}

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

    log(f"LinkedIn batch ({len(queue)}): " + (", ".join(
        c["name"] + ("" if c["name"] in fallback else " (cross-check)") for c in queue) or "-")
        + (" + remote-India search" if do_remote else ""))
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
    if do_remote:
        http = Http(max_requests=linkedin.REMOTE_PAGES * 3 + 5)
        cards, note = linkedin.scan_remote(companies, http, log=log)
        if not note.startswith("stopped") or cards:
            cache[linkedin.REMOTE_KEY] = {"checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                          "jobs": cards}
            linkedin.save_cache(CACHE, cache)
        log(f"LinkedIn remote-India search: {note}")
        if note.startswith("stopped: LinkedIn answered"):
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
