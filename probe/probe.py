"""Endpoint probe: fetch each target in probe/targets.json and record what came back.

Used while mapping companies to their careers platforms. Runs in GitHub Actions
(open internet), commits probe/results.json + probe/out/*.txt for inspection.
"""
import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/129.0 Safari/537.36")


def summarize_json(obj, depth=0):
    if depth > 3:
        return "..."
    if isinstance(obj, dict):
        return {k: summarize_json(v, depth + 1) for k, v in list(obj.items())[:25]}
    if isinstance(obj, list):
        return [len(obj), summarize_json(obj[0], depth + 1)] if obj else [0]
    if isinstance(obj, str):
        return obj[:80]
    return obj


def run(t):
    name = t["name"]
    headers = {"User-Agent": UA, "Accept": "application/json, text/html;q=0.9, */*;q=0.8",
               "Accept-Language": "en-US,en;q=0.9"}
    headers.update(t.get("headers", {}))
    started = time.time()
    rec = {"name": name, "url": t["url"], "method": t.get("method", "GET")}
    try:
        r = requests.request(rec["method"], t["url"], headers=headers,
                             json=t.get("json"), data=t.get("data"),
                             timeout=t.get("timeout", 30), allow_redirects=True)
        rec.update(status=r.status_code, final_url=r.url,
                   ctype=r.headers.get("content-type", ""), length=len(r.content))
        text = r.text
        try:
            rec["json_shape"] = summarize_json(r.json())
        except Exception:
            title = re.search(r"<title[^>]*>(.*?)</title>", text, re.S | re.I)
            rec["html_title"] = title.group(1).strip()[:150] if title else None
        rec["snippet"] = text[:1500]
        if t.get("save"):
            os.makedirs(os.path.join(HERE, "out"), exist_ok=True)
            safe = re.sub(r"[^A-Za-z0-9_.-]", "_", name)
            with open(os.path.join(HERE, "out", safe + ".txt"), "w", encoding="utf-8") as f:
                f.write(text[: t.get("save_bytes", 400_000)])
    except Exception as e:  # noqa: BLE001
        rec["error"] = f"{type(e).__name__}: {e}"[:400]
    rec["secs"] = round(time.time() - started, 2)
    return rec


def main():
    with open(os.path.join(HERE, "targets.json"), encoding="utf-8") as f:
        targets = json.load(f)
    with ThreadPoolExecutor(max_workers=12) as ex:
        results = list(ex.map(run, targets))
    with open(os.path.join(HERE, "results.json"), "w", encoding="utf-8") as f:
        json.dump(results, f, indent=1, ensure_ascii=False)
    ok = sum(1 for r in results if r.get("status") == 200)
    print(f"{ok}/{len(results)} returned 200")


if __name__ == "__main__":
    main()
