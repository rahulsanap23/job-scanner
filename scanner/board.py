"""Build board/index.html (Parts 2-3 of prompt/job_board_prompt.md) from data/jobs.json.

    python -m scanner.board                     # build from data/
    python -m scanner.board --drop <url> ...    # leave specific postings out (review decisions)

The page is written as Artifact content: no <html>/<head>/<body> wrapper, the
publishing step adds that.
"""
import argparse
import html
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATE = os.path.join(ROOT, "board", "template.html")
OUT = os.path.join(ROOT, "board", "index.html")


def build(drop=(), out=OUT):
    with open(os.path.join(ROOT, "data", "jobs.json"), encoding="utf-8") as f:
        data = json.load(f)
    with open(os.path.join(ROOT, "data", "scan_report.json"), encoding="utf-8") as f:
        report = json.load(f)
    drop = set(drop)
    jobs = [j for j in data["jobs"] if j.get("url") not in drop]

    coverage = []
    for r in report["companies"]:
        if r["status"] in ("error", "not scanned"):
            coverage.append({"company": r["company"], "status": r["status"],
                             "url": r.get("careers_url"),
                             "why": "; ".join(r.get("errors") or r.get("notes") or [])[:160]})
    payload = {"meta": data["meta"], "jobs": jobs, "coverage": coverage}
    blob = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    with open(TEMPLATE, encoding="utf-8") as f:
        page = f.read()
    page = page.replace("/*__DATA__*/{}", blob)
    page = page.replace("__SCAN_DATE__", html.escape(data["meta"]["date"]))
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        f.write(page)
    return out, len(jobs)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--drop", nargs="*", default=[], help="posting URLs to leave out")
    ap.add_argument("--out", default=OUT)
    a = ap.parse_args()
    out, n = build(a.drop, a.out)
    print(f"wrote {out} with {n} roles")


if __name__ == "__main__":
    main()
