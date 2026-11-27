"""Rate-limit handling: polite HTTP for Workday, fewer Workday requests, second try,
carrying a failed site's roles forward, and the LinkedIn caps."""
import json
from datetime import date, datetime, timezone

import pytest

from scanner import http as http_mod
from scanner import linkedin, run
from scanner.adapters import workday

NOW = datetime(2026, 11, 24, 10, 0, tzinfo=timezone.utc)
TODAY = date(2026, 11, 24)


class Resp:
    def __init__(self, status, headers=None):
        self.status_code, self.headers = status, headers or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise http_mod.requests.HTTPError(str(self.status_code))


@pytest.fixture
def no_sleep(monkeypatch):
    slept = []
    monkeypatch.setattr(http_mod.time, "sleep", lambda s: slept.append(s))
    monkeypatch.setattr(http_mod.Http, "_next_at", 0.0)
    monkeypatch.setattr(http_mod.Http, "_deadline", None)
    return slept


def test_polite_http_waits_out_a_429_then_succeeds(no_sleep, monkeypatch):
    h = http_mod.Http(polite=True)
    answers = [Resp(429), Resp(200)]
    monkeypatch.setattr(h.s, "request", lambda *a, **k: answers.pop(0))
    assert h.get("https://x.myworkdayjobs.com/a").status_code == 200
    assert h.count == 2 and max(no_sleep) >= http_mod.POLITE_429_WAITS[0]


def test_polite_http_gives_up_with_rate_limited(no_sleep, monkeypatch):
    h = http_mod.Http(polite=True)
    monkeypatch.setattr(h.s, "request", lambda *a, **k: Resp(429))
    with pytest.raises(http_mod.RateLimited):
        h.post("https://x.myworkdayjobs.com/a")
    assert h.count == 1 + len(http_mod.POLITE_429_WAITS)


def test_plain_http_unchanged(monkeypatch):
    h = http_mod.Http()
    monkeypatch.setattr(h.s, "request", lambda *a, **k: Resp(404))
    with pytest.raises(http_mod.requests.HTTPError):
        h.get("https://example.com")


class FakeWorkday:
    """India facet available; `total` India postings, two of them data roles."""

    def __init__(self, total):
        self.total, self.calls = total, []
        self.posts = [{"title": "Data Engineer" if i in (3, total - 1) else f"Accountant {i}",
                       "externalPath": f"/job/Pune/{i}", "locationsText": "Pune, India",
                       "postedOn": "Posted Today"} for i in range(total)]

    def post_json(self, url, payload):
        self.calls.append(payload)
        if not payload["appliedFacets"] and payload["limit"] == 1:
            return {"facets": [{"facetParameter": "locationCountry",
                                "values": [{"id": workday.INDIA_ID, "descriptor": "India"}]}]}
        if payload["searchText"]:
            hits = [p for p in self.posts if payload["searchText"] in p["title"].lower()]
        else:
            hits = self.posts
        o = payload["offset"]
        return {"total": len(hits), "jobPostings": hits[o:o + payload["limit"]]}

    def get_json(self, url):
        return {}


def test_workday_reads_short_india_list_once_without_search_terms():
    fake = FakeWorkday(95)
    src = {"host": "x.wd1.myworkdayjobs.com", "tenant": "x", "site": "ext"}
    jobs = workday.scan(src, fake)
    assert len(jobs) == 95 and sum(j["title"] == "Data Engineer" for j in jobs) == 2
    assert len(fake.calls) == 1 + 5  # facets + 5 pages of 20; no per-term searches
    assert all(not c["searchText"] for c in fake.calls)
    assert "read all 95 India postings" in src["_note"]


def test_workday_long_india_list_falls_back_to_search_terms():
    fake = FakeWorkday(workday.FULL_LIST_MAX + 1)
    src = {"host": "x.wd1.myworkdayjobs.com", "tenant": "x", "site": "ext"}
    jobs = workday.scan(src, fake, terms=["data"])
    assert [j["title"] for j in jobs] == ["Data Engineer", "Data Engineer"]
    assert fake.calls[-1]["searchText"] == "data"


def test_retry_failed_replaces_a_recovered_site(monkeypatch):
    companies = [{"name": "A"}, {"name": "B"}]
    results = {"A": ([], {"company": "A", "status": "error", "errors": ["workday: 429"]}),
               "B": ([{"title": "x"}], {"company": "B", "status": "ok", "notes": []})}
    monkeypatch.setattr(run.time, "sleep", lambda s: None)
    monkeypatch.setattr(run, "scan_company", lambda c, today, debug=False:
                        ([{"title": "Data Engineer"}], {"company": c["name"], "status": "ok", "notes": []}, []))
    got = run.retry_failed(companies, results, TODAY, log=lambda m: None, wait=0)
    assert got == ["A"] and results["A"][0] == [{"title": "Data Engineer"}]
    assert "answered on the second try" in results["A"][1]["notes"]


def test_retry_failed_stops_when_its_time_is_used(monkeypatch):
    results = {"A": ([], {"company": "A", "status": "error"})}
    monkeypatch.setattr(run.time, "sleep", lambda s: None)
    monkeypatch.setattr(run, "scan_company", lambda *a, **k: pytest.fail("rescanned"))
    assert run.retry_failed([{"name": "A"}], results, TODAY, log=lambda m: None, wait=0, budget=-1) == []


def test_carry_forward_keeps_recent_site_roles_only(monkeypatch):
    prev = [{"company": "A", "title": "Data Engineer", "source": "workday", "posted": "2026-11-20",
             "days_ago": 3, "location_tags": ["Pune"], "fresh": True, "new": True, "first_seen": "2026-11-20"},
            {"company": "A", "title": "Old", "source": "workday", "posted": "2026-08-01", "location_tags": ["Pune"]},
            {"company": "A", "title": "From LinkedIn", "source": "linkedin", "posted": "2026-11-20"},
            {"company": "B", "title": "Data Engineer", "source": "workday", "posted": "2026-11-20"}]
    history = {"A": [["2026-11-23T10:00:00+00:00", 40]], "B": [["2026-11-20T10:00:00+00:00", 40]]}
    reports = [{"company": "A", "status": "error", "notes": []}, {"company": "B", "status": "error", "notes": []}]
    carried = run.carry_forward(reports, prev, history, TODAY, NOW, log=lambda m: None)
    assert list(carried) == ["A"]  # B's last good scan is older than CARRY_HOURS
    assert [j["title"] for j in carried["A"]] == ["Data Engineer", "Old"]  # no age limit: old roles kept too
    from scanner import filters
    monkeypatch.setattr(filters, "RECENCY_DAYS", 60)  # with the optional age limit on, the old role goes
    (j,) = run.carry_forward([{"company": "A", "status": "error", "notes": []}], prev, history, TODAY, NOW,
                             log=lambda m: None)["A"]
    assert j["title"] == "Data Engineer" and j["days_ago"] == 4 and j["carried_from"] == "2026-11-23T10:00:00+00:00"
    assert "fresh" not in j and reports[0]["carried"] == {"from": "2026-11-23T10:00:00+00:00", "roles": 2}


def test_linkedin_stops_at_first_429(monkeypatch):
    calls = []

    class Sess:
        def get(self, *a, **k):
            calls.append(1)
            return Resp(429)

    monkeypatch.setattr(linkedin.time, "sleep", lambda s: None)
    with pytest.raises(linkedin.RateLimited):
        linkedin.fetch_page(Sess(), {}, http_mod.Http())
    assert len(calls) == 1


def test_scan_searches_at_most_the_cap(tmp_path, monkeypatch):
    path = tmp_path / "cache.json"
    path.write_text("{}")
    targets = [{"name": f"C{i}"} for i in range(linkedin.SCAN_SEARCH_CAP + 5)]
    seen = []
    monkeypatch.setattr(linkedin, "scan_targets", lambda stale, http: (seen.extend(stale), ({}, [], "ok"))[1])
    linkedin.run_fallback(targets, None, str(path), NOW)
    assert len(seen) == linkedin.SCAN_SEARCH_CAP


def test_refresh_skips_when_searched_recently(tmp_path, monkeypatch):
    from scanner import linkedin_refresh as lr
    cache = tmp_path / "cache.json"
    cache.write_text(json.dumps({"A": {"checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                       "jobs": []}}))
    monkeypatch.setattr(lr, "CACHE", str(cache))
    monkeypatch.setattr(lr, "load_report", lambda: ({}, [{"company": "A", "status": "ok"}]))
    monkeypatch.setattr(lr, "load_companies", lambda: pytest.fail("planned a batch"))
    logs = []
    lr.refresh(min_gap=40, log=logs.append)
    assert logs and "skipping this run" in logs[0]


def test_polite_http_skips_requests_after_the_deadline(no_sleep, monkeypatch):
    calls = []
    h = http_mod.Http(polite=True)
    monkeypatch.setattr(h.s, "request", lambda *a, **k: calls.append(1) or Resp(200))
    http_mod.Http.polite_deadline(-1)  # already used up
    with pytest.raises(http_mod.RateLimited, match="not tried"):
        h.get("https://x.myworkdayjobs.com/a")
    assert calls == []
    http_mod.Http.polite_deadline(None)
    assert h.get("https://x.myworkdayjobs.com/a").status_code == 200


def test_workday_india_list_threshold_is_small():
    # reading everything only pays off for short lists; longer ones use the term searches
    assert workday.FULL_LIST_MAX <= 200
