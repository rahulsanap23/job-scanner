"""Coverage changes: 60-day window, exact dates, dropped-roles log, LinkedIn cross-check, scanner alerts."""
from datetime import date, datetime, timezone

from scanner import dates, linkedin
from scanner.adapters.workday import needs_detail
from scanner.run import apply_filters, crosscheck, fill_dates_from_linkedin, scanner_alerts, title_key

T = date(2026, 11, 13)
NOW = datetime(2026, 11, 13, 6, 0, tzinfo=timezone.utc)


def raw(title, loc="Pune, India", posted="2026-11-01", url=None, **kw):
    return {"title": title, "locations": [loc], "posted": posted, "url": url or f"https://x/{title}/{loc}", **kw}


def test_window_is_60_days_and_rejects_are_logged():
    jobs, n, rej = apply_filters("Acme", [
        raw("Senior Data Engineer", posted="2026-09-20"),          # 54 days: kept
        raw("Data Engineer", posted="2026-09-01"),                 # 73 days: dropped, logged
        raw("Lead Data Engineer", loc="Hyderabad, India"),         # wrong city: logged
        raw("Data Analyst"),                                       # data title outside the rules, in Pune: logged
        raw("Accountant"),                                         # not logged at all
    ], T, "workday")
    assert [j["title"] for j in jobs] == ["Senior Data Engineer"] and n == 3
    reasons = {x["title"]: x["reason"] for x in rej}
    assert reasons == {"Data Engineer": "older than 60 days", "Lead Data Engineer": "location",
                       "Data Analyst": "title not a data-engineering role"}
    old = next(x for x in rej if x["title"] == "Data Engineer")
    assert old["posted"] == "2026-09-01" and old["tags"] == ["Pune"]


def test_vague_date_is_unknown_not_31_days():
    jobs, _, _ = apply_filters("Acme", [raw("Senior Data Engineer", posted="Posted 30+ Days Ago")], T, "workday")
    assert jobs[0]["posted"] is None and jobs[0]["date_note"] == "site only says Posted 30+ Days Ago"
    jobs, _, rej = apply_filters("Acme", [raw("Senior Data Engineer", posted="Posted 60+ Days Ago")], T, "x")
    assert not jobs and rej[0]["reason"] == "older than 60 days"


def test_workday_opens_detail_only_for_roles_we_might_keep():
    j = {"title": "Senior Data Engineer", "locations": ["Pune, India"], "posted": "Posted 30+ Days Ago"}
    assert needs_detail(j)
    assert not needs_detail({**j, "posted": "Posted 3 Days Ago"})
    assert not needs_detail({**j, "locations": ["Hyderabad, India"]})
    assert not needs_detail({**j, "title": "Accountant"})
    assert needs_detail({**j, "locations": ["3 Locations"], "posted": "Posted Today"})


def test_date_posted_from_page():
    page = '<script type="application/ld+json">{"@context":"https://schema.org","@type":"JobPosting",' \
           '"title":"Data Engineer","datePosted":"2026-10-02"}</script>'
    assert dates.ld_date(page) == "2026-10-02"
    assert dates.ld_date('{"x":1,"datePosted": "2026-10-05T00:00:00Z"}') == "2026-10-05T00:00:00Z"
    assert dates.ld_date("<html>nothing</html>") is None

    class H:
        def get(self, url, **kw):
            return type("R", (), {"text": page})()
    js = [{"url": "u", "posted": None, "days_ago": None}]
    assert dates.fill_missing(js, H(), T) == 1 and js[0]["posted"] == "2026-10-02" and js[0]["days_ago"] == 42


def test_title_key():
    assert title_key("Senior Data Engineer-4") == title_key("Sr. Data Engineer")
    assert title_key("Data Engineer II") != title_key("Data Engineer III")


def site(title, tags=("Pune",)):
    return {"company": "Acme", "title": title, "location_tags": list(tags), "posted": "2026-11-01",
            "days_ago": 12, "location": "Pune", "url": "https://site/" + title, "source": "workday", "flags": []}


def card(title, loc="Pune, Maharashtra, India", posted="2026-11-10"):
    return raw(title, loc=loc, posted=posted, url="https://www.linkedin.com/jobs/view/" + title.replace(" ", "-"),
               company="Acme")


def test_crosscheck_adds_only_what_the_site_missed():
    site_jobs = [site("Senior Data Engineer")]
    drops = [{"title": "Lead Data Engineer", "tags": ["Pune"], "reason": "older than 60 days"},
             {"title": "Data Engineer II", "tags": [], "reason": "location"}]
    cards = [card("Senior Data Engineer"),            # on the board already
             card("Lead Data Engineer"),              # the site dated it too old: site wins, logged
             card("Data Engineer II"),                # site had it elsewhere (Hyderabad): missed in Pune
             card("Principal Data Engineer"),         # not on the site at all: missed
             card("Senior Data Engineer", loc="Bengaluru, Karnataka, India")]  # wrong city: dropped
    added, rej = crosscheck("Acme", cards, site_jobs, drops, T)
    assert sorted(j["title"] for j in added) == ["Data Engineer II", "Principal Data Engineer"]
    assert all(j["crosscheck"] and j["source"] == "linkedin" and not j["flags"] for j in added)
    assert any(x["reason"].startswith("on LinkedIn, but the careers site dropped it (older than 60 days)") for x in rej)


def test_linkedin_date_fills_undated_site_role():
    j = {**site("Senior Data Engineer"), "posted": None, "days_ago": None}
    jobs, rejected = [j], []
    fill_dates_from_linkedin([j], [card("Sr Data Engineer", posted="2026-10-30")], T, jobs, rejected)
    assert j["posted"] == "2026-10-30" and j["date_source"] == "LinkedIn" and jobs == [j]
    k = {**site("Data Engineer"), "posted": None, "days_ago": None}
    jobs = [k]
    fill_dates_from_linkedin([k], [card("Data Engineer", posted="2026-08-01")], T, jobs, rejected)
    assert jobs == [] and rejected[-1]["reason"] == "older than 60 days"


def test_scanner_alerts_and_history():
    history = {"Acme": [["t", 40]] * 5, "New": [["t", 40]]}
    reports = [{"company": "Acme", "status": "ok", "fetched": 9},
               {"company": "New", "status": "ok", "fetched": 1},          # too little history to judge
               {"company": "Capped", "status": "ok", "fetched": 300, "capped": ["workday 'data' stopped at 300 of 420"]},
               {"company": "Feedless", "status": "not scanned"}]
    alerts = scanner_alerts(reports, history, "2026-11-13T00:40:00+00:00")
    assert [a["company"] for a in alerts] == ["Acme", "Capped"]
    assert alerts[0]["why"] == "read 9 postings, usually about 40" and reports[0]["alerts"]
    assert history["Acme"][-1] == ["2026-11-13T00:40:00+00:00", 9] and "Feedless" not in history


def test_remote_cache_entry():
    cache = {linkedin.REMOTE_KEY: {"checked_at": "2026-11-13T01:00:00+00:00",
                                   "jobs": [{"id": "1", "target": "Acme"}, {"id": "2", "target": "Beta"}]},
             "Acme": {"checked_at": "2026-11-01T00:00:00+00:00", "jobs": []}}
    by, when = linkedin.remote_jobs(cache, NOW)
    assert set(by) == {"Acme", "Beta"} and when == "2026-11-13T01:00:00+00:00"
    assert linkedin.fresh_entry(cache, "Acme", NOW) is None  # older than CACHE_DAYS
    assert linkedin.add_unique([{"id": "1"}], [{"id": "1"}, {"id": "3"}]) == [{"id": "1"}, {"id": "3"}]
    assert linkedin.POSTED_WITHIN == "r5184000"
