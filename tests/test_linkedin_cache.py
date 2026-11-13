import json
from datetime import datetime, timezone

from scanner import linkedin
from scanner.linkedin_refresh import plan

NOW = datetime(2026, 10, 28, 12, 0, tzinfo=timezone.utc)


def entry(when, jobs=()):
    return {"checked_at": when, "jobs": list(jobs)}


def test_merge_keeps_newest_per_company():
    a = {"A": entry("2026-10-28T03:00:00+00:00", ["a1"]), "B": entry("2026-10-28T09:00:00+00:00", ["b2"])}
    b = {"A": entry("2026-10-28T05:00:00+00:00", ["a2"]), "B": entry("2026-10-28T01:00:00+00:00", ["b1"]),
         "C": entry("2026-10-27T05:00:00+00:00")}
    m = linkedin.merge_caches(a, b)
    assert m["A"]["jobs"] == ["a2"] and m["B"]["jobs"] == ["b2"] and "C" in m
    assert linkedin.merge_caches(b, a) == m  # order doesn't matter


def test_plan_puts_failed_sites_first_then_oldest():
    companies = [{"name": n} for n in ("Feedless1", "Feedless2", "Broken", "Fine", "Skipped")]
    companies[4]["skip_linkedin"] = True
    reports = [{"company": "Feedless1", "status": "not scanned"},
               {"company": "Feedless2", "status": "not scanned"},
               {"company": "Broken", "status": "error", "fetched": 0},
               {"company": "Fine", "status": "ok", "fetched": 40},
               {"company": "Skipped", "status": "not scanned"}]
    cache = {"Feedless1": entry("2026-10-28T08:00:00+00:00"), "Feedless2": entry("2026-10-27T08:00:00+00:00")}
    urgent, queue = plan(companies, reports, cache, "2026-10-28T01:00:00+00:00", batch=8, now=NOW)
    assert [c["name"] for c in urgent] == ["Broken"]
    # due fallback first (Feedless1 was checked 4 h ago, so it only fills a spare slot); Fine is cross-checked
    assert [c["name"] for c in queue] == ["Broken", "Feedless2", "Fine", "Feedless1"]
    # once Broken has been checked on LinkedIn after that scan, it's no longer urgent
    cache["Broken"] = entry("2026-10-28T02:00:00+00:00")
    urgent, queue = plan(companies, reports, cache, "2026-10-28T01:00:00+00:00", batch=2, now=NOW)
    assert urgent == [] and [c["name"] for c in queue] == ["Feedless2", "Fine"]  # one slot kept for the cross-check


def test_plan_keeps_a_third_for_the_crosscheck_and_lends_spare_slots():
    names = [f"Feedless{i}" for i in range(20)] + [f"Site{i}" for i in range(20)]
    companies = [{"name": n} for n in names]
    reports = [{"company": n, "status": "not scanned"} for n in names[:20]] + \
              [{"company": n, "status": "ok", "fetched": 30} for n in names[20:]]
    _, queue = plan(companies, reports, {}, None, batch=8, now=NOW)
    got = [c["name"] for c in queue]
    assert len(got) == 8 and sum(n.startswith("Site") for n in got) == 3  # ceil(8/3) reserved
    # every cross-check company checked recently: all 8 slots go to fallback companies
    cache = {n: entry("2026-10-28T10:00:00+00:00") for n in names[20:]}
    _, queue = plan(companies, reports, cache, None, batch=8, now=NOW)
    assert all(c["name"].startswith("Feedless") for c in queue) and len(queue) == 8
    # no fallback company due: the cross-check gets every slot
    cache = {n: entry("2026-10-28T10:00:00+00:00") for n in names[:20]}
    _, queue = plan(companies, reports, cache, None, batch=8, now=NOW)
    assert [c["name"] for c in queue][:8] == [f"Site{i}" for i in range(8)]


def test_remote_due():
    from scanner.linkedin_refresh import remote_due
    assert remote_due({}, NOW)
    assert not remote_due({linkedin.REMOTE_KEY: entry("2026-10-28T06:00:00+00:00")}, NOW)
    assert remote_due({linkedin.REMOTE_KEY: entry("2026-10-27T23:00:00+00:00")}, NOW)


def test_run_fallback_uses_fresh_cache_without_searching(tmp_path):
    path = tmp_path / "cache.json"
    path.write_text(json.dumps({"A": entry("2026-10-28T06:00:00+00:00", [{"title": "Data Engineer"}]),
                                "B": entry("2026-10-26T06:00:00+00:00", [{"title": "Old"}]),
                                "Gone": entry("2026-10-28T06:00:00+00:00")}))
    targets = [{"name": "A"}, {"name": "B"}]

    def no_search(*a, **k):
        raise AssertionError("searched LinkedIn")

    orig = linkedin.scan_targets
    linkedin.scan_targets = lambda stale, http: ({}, [], "searched 0 companies") if stale else no_search()
    try:
        results, when, note = linkedin.run_fallback(targets, None, str(path), NOW)
    finally:
        linkedin.scan_targets = orig
    assert results["A"] == [{"title": "Data Engineer"}] and results["B"] == [{"title": "Old"}]
    assert note.startswith("1 of 2 companies checked in the last 24 h; not checked in 24 h: B")
    assert set(json.loads(path.read_text())) == {"A", "B"}  # companies no longer in the config are dropped


def test_merge_file_drops_expired(tmp_path):
    from scanner.linkedin_refresh import merge
    into, ours = tmp_path / "main.json", tmp_path / "ours.json"
    into.write_text(json.dumps({"A": entry("2026-10-28T01:00:00+00:00", ["old"]),
                                "Expired": entry("2026-10-20T01:00:00+00:00")}))
    ours.write_text(json.dumps({"A": entry("2026-10-28T05:00:00+00:00", ["new"]),
                                "B": entry("2026-10-28T05:00:00+00:00")}))
    merge(str(ours), str(into), NOW)
    out = json.loads(into.read_text())
    assert set(out) == {"A", "B"} and out["A"]["jobs"] == ["new"]
