"""Fetch the full job description for every role on the board, once per role.

    python -m scanner.descriptions            # after scanner.run; updates data/descriptions.json
    python -m scanner.descriptions --debug

data/descriptions.json maps each job URL to
    {"company", "title", "status": "ok" | "failed", "text", "error", "tries", "fetched_at"}
Roles already fetched are skipped, failed ones are retried up to MAX_TRIES runs, and roles
that left the board are dropped. The scheduled Claude task reads this file to pick each
role's top skills.
"""
import argparse
import html
import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

import yaml

from .http import Http

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
OUT = os.path.join(DATA, "descriptions.json")
MAX_TRIES = 3
MAX_CHARS = 12000
MIN_CHARS = 200  # shorter than this is a page shell, not a description


# ---------- text helpers ----------

BLOCK = re.compile(r"</?(p|div|br|li|ul|ol|h[1-6]|tr|section|article)\b[^>]*>", re.I)


def to_text(s):
    """HTML (possibly entity-escaped, as Greenhouse sends it) -> readable plain text."""
    if not s:
        return ""
    s = html.unescape(s) if "&lt;" in s else s
    s = re.sub(r"(?is)<(script|style|noscript)\b.*?</\1>", " ", s)
    s = re.sub(r"(?i)<li\b[^>]*>", "\n- ", s)
    s = BLOCK.sub("\n", s)
    s = re.sub(r"<[^>]+>", " ", s)
    s = html.unescape(s).replace("\xa0", " ")
    lines = [" ".join(l.split()) for l in s.splitlines()]
    out = "\n".join(l for l in lines if l)
    return out[:MAX_CHARS]


def join(*parts):
    return to_text("\n".join(p for p in parts if p))


def ld_description(page):
    """description from schema.org JobPosting JSON-LD, which most careers pages embed for Google Jobs."""
    for m in re.finditer(r'(?is)<script[^>]+application/ld\+json[^>]*>(.*?)</script>', page):
        try:
            data = json.loads(m.group(1).strip())
        except ValueError:
            continue
        stack = data if isinstance(data, list) else [data]
        while stack:
            d = stack.pop()
            if isinstance(d, dict):
                if d.get("@type") == "JobPosting" and d.get("description"):
                    return d["description"]
                stack.extend(v for v in d.values() if isinstance(v, (dict, list)))
            elif isinstance(d, list):
                stack.extend(d)
    return None


def main_text(page):
    """Last resort: visible text of the page's main content area."""
    for pat in (r'(?is)<main\b[^>]*>(.*?)</main>', r'(?is)<article\b[^>]*>(.*?)</article>',
                r'(?is)<body\b[^>]*>(.*?)</body>'):
        m = re.search(pat, page)
        if m:
            return m.group(1)
    return page


def from_page(http, url):
    page = http.get(url).text
    return ld_description(page) or main_text(page)


# ---------- one fetcher per careers platform ----------

def workday(http, job, cfg):
    u = urlparse(job["url"])
    m = re.match(r"^/(?:[a-z]{2}-[A-Z]{2}/)?([^/]+)(/job/.+)$", u.path)
    if not m:
        raise ValueError("unrecognised Workday URL")
    site, path = m.group(1), m.group(2)
    tenant = (cfg.get("tenants") or {}).get((u.netloc, site)) or u.netloc.split(".")[0]
    info = http.get_json(f"https://{u.netloc}/wday/cxs/{tenant}/{site}{path}").get("jobPostingInfo") or {}
    return info.get("jobDescription")


def greenhouse(http, job, cfg):
    q = parse_qs(urlparse(job["url"]).query)
    jid = (q.get("gh_jid") or [None])[0] or re.search(r"/jobs/(\d+)", job["url"]).group(1)
    token = cfg["company"].get("greenhouse")
    data = http.get_json(f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs/{jid}")
    return data.get("content")


def lever(http, job, cfg):
    u = urlparse(job["url"])
    site, pid = [p for p in u.path.split("/") if p][:2]
    host = "api.eu.lever.co" if ".eu." in u.netloc else "api.lever.co"
    d = http.get_json(f"https://{host}/v0/postings/{site}/{pid}")
    lists = "".join(f"<h3>{l.get('text', '')}</h3><ul>{l.get('content', '')}</ul>" for l in d.get("lists") or [])
    return "\n".join(x for x in (d.get("description"), lists, d.get("additional")) if x)


_ashby_cache = {}


def ashby(http, job, cfg):
    org = cfg["company"].get("ashby")
    if org not in _ashby_cache:
        _ashby_cache[org] = http.get_json(f"https://api.ashbyhq.com/posting-api/job-board/{org}").get("jobs") or []
    for j in _ashby_cache[org]:
        if j.get("jobUrl") == job["url"] or (j.get("id") and j["id"] in job["url"]):
            return j.get("descriptionHtml") or j.get("descriptionPlain")
    raise ValueError("role not in Ashby board")


def smartrecruiters(http, job, cfg):
    cid, pid = [p for p in urlparse(job["url"]).path.split("/") if p][:2]
    d = http.get_json(f"https://api.smartrecruiters.com/v1/companies/{cid}/postings/{pid}")
    sec = (d.get("jobAd") or {}).get("sections") or {}
    return join(*((sec.get(k) or {}).get("text") for k in ("jobDescription", "qualifications", "additionalInformation")))


def oracle(http, job, cfg):
    u = urlparse(job["url"])
    m = re.search(r"/sites/([^/]+)/job/(\d+)", u.path)
    site, rid = m.group(1), m.group(2)
    d = http.get_json(f"https://{u.netloc}/hcmRestApi/resources/latest/recruitingCEJobRequisitionDetails",
                      params={"expand": "all", "onlyData": "true",
                              "finder": f'ById;Id="{rid}",siteNumber={site}'})
    it = (d.get("items") or [{}])[0]
    return join(it.get("ExternalDescriptionStr"), it.get("ExternalResponsibilitiesStr"),
                it.get("ExternalQualificationsStr"), it.get("CorporateDescriptionStr"))


def eightfold(http, job, cfg):
    u = urlparse(job["url"])
    pid = re.search(r"/job/(\d+)", u.path).group(1)
    domain = cfg["company"].get("eightfold_domain")
    try:
        d = http.get_json(f"https://{u.netloc}/api/apply/v2/jobs/{pid}", params={"domain": domain})
        if d.get("job_description"):
            return d["job_description"]
    except Exception:  # noqa: BLE001 - newer sites only have the pcsx API
        pass
    d = http.get_json(f"https://{u.netloc}/api/pcsx/position_details",
                      params={"position_id": pid, "domain": domain, "hl": "en"})
    return ((d.get("data") or {}).get("jobDescription")) or from_page(http, job["url"])


_atlassian = []


def atlassian(http, job, cfg):
    if not _atlassian:
        _atlassian.extend(http.get_json("https://www.atlassian.com/endpoint/careers/listings"))
    jid = re.search(r"/jobs/(\d+)/", job["url"])
    for j in _atlassian:
        post = j.get("portalJobPost") or {}
        if post.get("portalUrl") == job["url"] or (jid and str(post.get("id")) == jid.group(1)):
            return join(j.get("overview"), j.get("responsibilities"), j.get("qualifications"))
    return from_page(http, job["url"])


def jibe(http, job, cfg):
    u = urlparse(job["url"])
    slug = job["url"].rstrip("/").split("/")[-1]
    try:
        d = http.get_json(f"https://{u.netloc}/api/jobs/{slug}", params={"lang": "en-us"})
        d = d.get("data") or d
        if d.get("description"):
            return join(d.get("description"), d.get("qualifications"), d.get("responsibilities"))
    except Exception:  # noqa: BLE001
        pass
    return from_page(http, job["url"])


def linkedin(http, job, cfg):
    jid = re.search(r"(\d{6,})/?$", job["url"].split("?")[0]).group(1)
    page = http.get(f"https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{jid}").text
    m = re.search(r'(?is)class="[^"]*show-more-less-html__markup[^"]*"[^>]*>(.*?)</div>', page)
    return m.group(1) if m else ld_description(page)


FETCHERS = {"workday": workday, "greenhouse": greenhouse, "lever": lever, "ashby": ashby,
            "smartrecruiters": smartrecruiters, "oracle": oracle, "eightfold": eightfold,
            "atlassian": atlassian, "jibe": jibe, "linkedin": linkedin}


def platform(job):
    """Which fetcher to use: the scan's source, else a guess from the URL."""
    for part in (job.get("source") or "").split("+"):
        if part in FETCHERS:
            return part
    host = urlparse(job.get("url") or "").netloc
    for key, name in (("myworkdayjobs", "workday"), ("greenhouse", "greenhouse"), ("lever.co", "lever"),
                      ("ashbyhq", "ashby"), ("smartrecruiters", "smartrecruiters"), ("oraclecloud", "oracle"),
                      ("eightfold", "eightfold"), ("linkedin.com", "linkedin")):
        if key in host:
            return name
    return "page"


# ---------- config: ids some fetchers need ----------

def load_config():
    with open(os.path.join(ROOT, "config", "companies.yaml"), encoding="utf-8") as f:
        companies = yaml.safe_load(f)["companies"]
    per, tenants = {}, {}
    for c in companies:
        d = {}
        for s in c.get("sources") or []:
            t = s.get("type")
            if t == "greenhouse":
                d.setdefault("greenhouse", s.get("token"))
            elif t == "ashby":
                d.setdefault("ashby", s.get("org"))
            elif t == "eightfold":
                d.setdefault("eightfold_domain", s.get("domain"))
            elif t == "workday" and s.get("host"):
                tenants[(s["host"], s.get("site"))] = s.get("tenant")
        per[c["name"]] = d
    return per, tenants


def fetch_one(job, cfg):
    http = Http(max_requests=6)
    name = platform(job)
    try:
        raw = FETCHERS[name](http, job, cfg) if name in FETCHERS else from_page(http, job["url"])
        text = to_text(raw)
        if len(text) < MIN_CHARS:
            raise ValueError(f"description too short ({len(text)} chars)")
        return {"status": "ok", "text": text, "via": name}
    except Exception as e:  # noqa: BLE001
        return {"status": "failed", "error": f"{name}: {type(e).__name__}: {str(e)[:160]}", "via": name}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--debug", action="store_true")
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()

    with open(os.path.join(DATA, "jobs.json"), encoding="utf-8") as f:
        jobs = json.load(f)["jobs"]
    try:
        with open(OUT, encoding="utf-8") as f:
            store = json.load(f)
    except (FileNotFoundError, ValueError):
        store = {}

    per, tenants = load_config()
    live = {j["url"]: j for j in jobs if j.get("url")}
    store = {u: v for u, v in store.items() if u in live}  # drop roles that left the board
    todo = [j for u, j in live.items()
            if store.get(u, {}).get("status") != "ok" and store.get(u, {}).get("tries", 0) < MAX_TRIES]

    started = time.time()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        results = list(ex.map(lambda j: (j, fetch_one(j, {"company": per.get(j["company"], {}),
                                                           "tenants": tenants})), todo))
    for j, r in results:
        prev = store.get(j["url"], {})
        store[j["url"]] = {"company": j["company"], "title": j["title"], **r,
                           "tries": prev.get("tries", 0) + 1, "fetched_at": now}
        if args.debug or r["status"] != "ok":
            print(f"{r['status']:6} {j['company'][:24]:24} {j['title'][:50]:50} {r.get('error', '')}", flush=True)

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(dict(sorted(store.items())), f, indent=1, ensure_ascii=False)
    ok = sum(1 for v in store.values() if v["status"] == "ok")
    print(f"descriptions: {ok}/{len(live)} roles have one; fetched {len(todo)} this run "
          f"({sum(1 for _, r in results if r['status'] == 'ok')} ok) in {time.time() - started:.0f}s")


if __name__ == "__main__":
    main()
