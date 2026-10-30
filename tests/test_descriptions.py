from scanner import descriptions as d


def test_to_text_unescapes_and_keeps_bullets():
    t = d.to_text("&lt;p&gt;Build &amp;amp; run&lt;/p&gt;&lt;ul&gt;&lt;li&gt;Spark&lt;/li&gt;&lt;li&gt;Airflow&lt;/li&gt;&lt;/ul&gt;")
    assert "Build & run" in t and "- Spark" in t and "- Airflow" in t


def test_ld_description():
    page = '<script type="application/ld+json">{"@context":"x","@type":"JobPosting","description":"<p>Hello</p>"}</script>'
    assert d.ld_description(page) == "<p>Hello</p>"


def test_platform_guess():
    assert d.platform({"source": "workday", "url": "x"}) == "workday"
    assert d.platform({"source": "avature", "url": "https://jobs.bmc.com/x"}) == "page"
    assert d.platform({"source": "", "url": "https://in.linkedin.com/jobs/view/a-1234567"}) == "linkedin"


def test_workday_url(monkeypatch):
    seen = {}

    class H:
        def get_json(self, url, **kw):
            seen["url"] = url
            return {"jobPostingInfo": {"jobDescription": "<p>x</p>"}}

    job = {"url": "https://citi.wd5.myworkdayjobs.com/en-US/2/job/Pune-Maharashtra-India/Data-Engineer_123"}
    d.workday(H(), job, {"tenants": {("citi.wd5.myworkdayjobs.com", "2"): "citi"}})
    assert seen["url"] == "https://citi.wd5.myworkdayjobs.com/wday/cxs/citi/2/job/Pune-Maharashtra-India/Data-Engineer_123"


def test_same_title_accepts_formatting_differences():
    assert d.same_title("Sr. Data Engineer - Pune", "Senior Data Engineer")
    assert d.same_title("Lead Systems Engineer - Data DevOps/MLOps", "Lead Systems Engineer – Data DevOps / MLOps")
    assert d.same_title("AVP – Data Engineer", "Assistant Vice President - Data Engineer")


def test_same_title_rejects_a_different_job():
    assert not d.same_title("Lead Systems Engineer - Data DevOps/MLOps", "Senior Systems Engineer - Data DevOps/MLOps")
    assert not d.same_title("Data Engineer II", "Data Engineer III")
    assert not d.same_title("Data Engineer", "Senior Data Engineer")
    assert not d.same_title("", "")


def test_lookup_due_order_gap_and_limit():
    from datetime import datetime, timezone
    now = datetime(2026, 10, 30, 12, 0, tzinfo=timezone.utc)
    store = {
        "a": {"status": "needs_linkedin", "li_tries": 1, "li_checked_at": "2026-10-30T06:00:00+00:00"},
        "b": {"status": "needs_linkedin", "li_tries": 0},
        "c": {"status": "needs_linkedin", "li_tries": 1, "li_checked_at": "2026-10-30T10:00:00+00:00"},  # too recent
        "d": {"status": "needs_linkedin", "li_tries": 3},  # gave up
        "e": {"status": "ok"},
        "f": {"status": "needs_linkedin", "li_tries": 0},  # left the board
    }
    live = {u: {} for u in "abcde"}
    assert [u for u, _ in d.lookup_due(store, live, now=now)] == ["b", "a"]
    assert len(d.lookup_due(store, live, now=now, batch=1)) == 1


def test_merge_keeps_found_descriptions_and_board_roles_only():
    scan = {"x": {"status": "needs_linkedin", "updated_at": "2026-10-30T09:40:00+00:00"},
            "y": {"status": "ok", "updated_at": "2026-10-30T09:40:00+00:00", "text": "scan"}}
    refresh = {"x": {"status": "ok", "updated_at": "2026-10-30T09:20:00+00:00", "text": "li"},
               "z": {"status": "ok", "updated_at": "2026-10-30T09:20:00+00:00"}}
    out = d.merge_stores(refresh, scan, keep={"x", "y"})
    assert out["x"]["text"] == "li"          # a found description beats a newer miss
    assert out["y"]["text"] == "scan"
    assert "z" not in out                     # role no longer on the board
    newer = d.merge_stores({"x": {"status": "needs_linkedin", "li_tries": 1, "updated_at": "1"}},
                           {"x": {"status": "needs_linkedin", "li_tries": 2, "updated_at": "2"}})
    assert newer["x"]["li_tries"] == 2        # otherwise the newest write wins
