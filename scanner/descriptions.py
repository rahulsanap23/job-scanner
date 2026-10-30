"""Fetch the full job description for every role on the board, once per role.

    python -m scanner.descriptions            # after scanner.run; updates data/descriptions.json
    python -m scanner.descriptions --debug
    python -m scanner.descriptions --merge OURS   # merge OURS into data/descriptions.json (used before a push)

Same pattern as finding jobs: the daily scan tries each new role's own careers site; when
that gives no description the role is marked needs_linkedin, and the linkedin-refresh
workflow (every 2 hours) looks a few of those up on LinkedIn's public job search, accepting
only a posting with the same company and the same title (linkedin_lookup below). After
LI_MAX_TRIES misses the role is marked failed.

data/descriptions.json maps each job URL to
    {"company", "title", "status": "ok" | "needs_linkedin" | "failed", "text", "via", "error",
     "tries", "li_tries", "li_url", "fetched_at", "li_checked_at", "updated_at"}
Roles that left the board are dropped. The scheduled Claude task reads this file to pick
each role's top skills.
"""
import argparse
import html
import json
import os
import re
import time
import unicodedata
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


# ---------- file helpers ----------

def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_store(path=None):
    try:
        with open(path or OUT, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, ValueError):
        return {}


def save_store(store, path=None):
    with open(path or OUT, "w", encoding="utf-8") as f:
        json.dump(dict(sorted(store.items())), f, indent=1, ensure_ascii=False)


def live_jobs(path=os.path.join(DATA, "jobs.json")):
    with open(path, encoding="utf-8") as f:
        return {j["url"]: j for j in json.load(f)["jobs"] if j.get("url")}


def _rank(v):
    """Which of two entries for the same role to keep: a description beats none, then the newest write."""
    return (v.get("status") == "ok", v.get("updated_at") or v.get("fetched_at") or "")


def merge_stores(*stores, keep=None):
    """Per role URL keep the better entry (see _rank). keep: only these URLs (the roles on the board)."""
    out = {}
    for store in stores:
        for url, v in (store or {}).items():
            if not isinstance(v, dict) or (keep is not None and url not in keep):
                continue
            if url not in out or _rank(v) > _rank(out[url]):
                out[url] = v
    return out


def merge(ours_path, into=None):
    """Merge a descriptions file into `into`, keeping only roles in the current data/jobs.json.

    Both the daily scan and the linkedin-refresh workflow write data/descriptions.json, so each
    merges its results into the latest version on the branch instead of overwriting it.
    """
    into = into or OUT
    save_store(merge_stores(load_store(into), load_store(ours_path), keep=set(live_jobs())), into)


# ---------- daily scan step ----------

def scan_step(workers=8, debug=False):
    """Careers-site fetch for roles without a description. A careers-site miss becomes
    needs_linkedin (linkedin-refresh then looks the role up on LinkedIn, a few per run);
    a role that came from LinkedIn and still failed is retried here up to MAX_TRIES runs."""
    live = live_jobs()
    store = {u: v for u, v in load_store().items() if u in live}  # drop roles that left the board
    for v in store.values():  # entries written before LinkedIn lookups existed
        if v.get("status") == "failed" and v.get("via") != "linkedin" and "li_tries" not in v:
            v.update(status="needs_linkedin", li_tries=0, updated_at=now_iso())
    todo = [j for u, j in live.items()
            if u not in store or (store[u].get("status") == "failed" and store[u].get("via") == "linkedin"
                                  and store[u].get("tries", 0) < MAX_TRIES)]

    per, tenants = load_config()
    started, now = time.time(), now_iso()
    with ThreadPoolExecutor(max_workers=workers) as ex:
        results = list(ex.map(lambda j: (j, fetch_one(j, {"company": per.get(j["company"], {}),
                                                           "tenants": tenants})), todo))
    for j, r in results:
        prev = store.get(j["url"], {})
        entry = {"company": j["company"], "title": j["title"], **r,
                 "tries": prev.get("tries", 0) + 1, "fetched_at": now, "updated_at": now}
        if r["status"] != "ok" and r.get("via") != "linkedin":
            entry.update(status="needs_linkedin", li_tries=0)
        store[j["url"]] = entry
        if debug or entry["status"] != "ok":
            print(f"{entry['status']:14} {j['company'][:24]:24} {j['title'][:50]:50} {r.get('error', '')}", flush=True)

    save_store(store)
    ok = sum(1 for v in store.values() if v["status"] == "ok")
    waiting = sum(1 for v in store.values() if v["status"] == "needs_linkedin")
    print(f"descriptions: {ok}/{len(live)} roles have one; {waiting} waiting for LinkedIn; fetched {len(todo)} "
          f"this run ({sum(1 for _, r in results if r['status'] == 'ok')} ok) in {time.time() - started:.0f}s")


# ---------- LinkedIn lookup (run by linkedin-refresh) ----------

LI_BATCH = 5        # roles looked up per linkedin-refresh run
LI_MAX_TRIES = 3    # then give up: the role probably isn't on LinkedIn
LI_GAP_HOURS = 4    # wait at least this long between tries for the same role
ABBREV = {"sr": "senior", "snr": "senior", "jr": "junior", "eng": "engineer", "engg": "engineering",
          "mgr": "manager", "dev": "developer", "assoc": "associate", "avp": "assistant vice president",
          "vp": "vice president", "svp": "senior vice president", "mgmt": "management"}
TITLE_NOISE = {"pune", "mumbai", "navi", "bangalore", "bengaluru", "hyderabad", "chennai", "india", "remote",
               "hybrid", "onsite", "m", "f", "d", "x", "w", "the", "and", "of"}


def title_words(t):
    """Job title reduced to comparable words: lower case, abbreviations expanded, locations dropped."""
    t = unicodedata.normalize("NFKD", t or "").encode("ascii", "ignore").decode().lower().replace("&", " and ")
    words = []
    for w in re.findall(r"[a-z0-9+#]+", t):
        words.extend(ABBREV.get(w, w).split())
    return sorted(w for w in words if w not in TITLE_NOISE)


def same_title(a, b):
    """True when two titles name the same job: same words after normalising (so a different level
    such as Senior vs Lead, or II vs III, never matches)."""
    wa, wb = title_words(a), title_words(b)
    return bool(wa) and wa == wb


def lookup_due(store, live, now=None, batch=LI_BATCH):
    """needs_linkedin roles still on the board, fewest tries first, not tried in the last LI_GAP_HOURS."""
    from datetime import timedelta
    now = now or datetime.now(timezone.utc)
    gap = (now - timedelta(hours=LI_GAP_HOURS)).isoformat(timespec="seconds")
    due = [(u, v) for u, v in store.items()
           if u in live and v.get("status") == "needs_linkedin" and v.get("li_tries", 0) < LI_MAX_TRIES
           and (v.get("li_checked_at") or "") < gap]
    due.sort(key=lambda uv: (uv[1].get("li_tries", 0), uv[1].get("li_checked_at") or ""))
    return due[:batch]


def linkedin_lookup(batch=LI_BATCH, log=print):
    """Look up a few needs_linkedin roles on LinkedIn's public job search: same company and same
    title only. Paced and stops at the first sign of rate limiting, like the company search."""
    import random
    import requests
    from . import linkedin as li

    live = live_jobs()
    store = load_store()
    due = lookup_due(store, live, batch=batch)
    if not due:
        log("descriptions: no roles waiting for a LinkedIn lookup")
        return 0
    cfg = {c["name"]: c for c in yaml.safe_load(open(os.path.join(ROOT, "config", "companies.yaml"),
                                                       encoding="utf-8"))["companies"]}
    http = Http(max_requests=batch * 3 + 5)
    sess = requests.Session()  # no automatic retries: back off at the first 429
    sess.headers.update(http.s.headers)
    found = 0
    for url, v in due:
        job = live[url]
        c = cfg.get(job["company"]) or {"name": job["company"]}
        names = [c["name"], li.search_name(c)] + list(c.get("aliases") or [])
        v["li_tries"] = v.get("li_tries", 0) + 1
        v["li_checked_at"] = v["updated_at"] = now_iso()
        try:
            time.sleep(li.PAUSE_SECS + random.random() * 4)
            r = sess.get(li.API, timeout=20, params={"keywords": f"{job['title']} {li.search_name(c)}",
                                                    "location": "India", "f_TPR": "r2592000"})
            if r.status_code in (429, 999) or r.status_code >= 400:
                v["li_tries"] -= 1  # not the role's fault: try it again next run
                log(f"descriptions: LinkedIn answered {r.status_code}; stopping this run")
                break
            card = next((k for k in li.parse(r.text)
                         if li.name_match(names, k["company"]) and same_title(job["title"], k["title"])), None)
            if card:
                time.sleep(li.PAUSE_SECS + random.random() * 4)
                text = to_text(linkedin(http, {"url": card["url"]}, {}))
                if len(text) >= MIN_CHARS:
                    v.update(status="ok", text=text, via="linkedin-search", li_url=card["url"])
                    v.pop("error", None)
                    found += 1
                    log(f"descriptions: found on LinkedIn: {job['company']} | {job['title']}")
                    continue
            v["error"] = f"LinkedIn try {v['li_tries']}: no posting with the same company and title"
            if v["li_tries"] >= LI_MAX_TRIES:
                v["status"] = "failed"
            log(f"descriptions: not on LinkedIn (try {v['li_tries']}): {job['company']} | {job['title']}")
        except requests.RequestException as e:
            v["li_tries"] -= 1  # network trouble, not a miss: try the role again next run
            log(f"descriptions: LinkedIn unreachable ({type(e).__name__}); stopping this run")
            break
        except Exception as e:  # noqa: BLE001
            v["error"] = f"LinkedIn try {v['li_tries']}: {type(e).__name__}: {str(e)[:120]}"
            if v["li_tries"] >= LI_MAX_TRIES:
                v["status"] = "failed"
            log(f"descriptions: LinkedIn lookup error for {job['company']} | {job['title']}: {e}")
    save_store(store)
    return found


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--debug", action="store_true")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--merge", metavar="OURS", help="merge this descriptions file into data/descriptions.json and exit")
    args = ap.parse_args()
    if args.merge:
        merge(args.merge)
    else:
        scan_step(args.workers, args.debug)


if __name__ == "__main__":
    main()
