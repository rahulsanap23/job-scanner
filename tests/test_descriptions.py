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
