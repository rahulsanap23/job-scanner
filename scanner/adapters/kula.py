"""Kula job boards (careers.kula.ai/<slug>) — the board page is server-rendered HTML.

Each job card is a title paragraph, a "Department • Location • Type • Workplace"
line and an "Apply Now" link to /<slug>/<id>-<title-slug>. No posting date is shown.
"""
import html
import re

PARA = re.compile(r"<p\b[^>]*>(.*?)</p>", re.S | re.I)
NOISE = re.compile(r"<(style|script)\b.*?</\1>", re.S | re.I)


def _text(s):
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", s or "")).split())


def parse(page, slug, base="https://careers.kula.ai"):
    page = NOISE.sub(" ", page)
    link = re.compile(r'href="/' + re.escape(slug) + r'/(\d+)-[^"]*"')
    jobs, prev = {}, 0
    for m in link.finditer(page):
        chunk, prev = page[prev:m.start()], m.end()
        paras = [_text(p) for p in PARA.findall(chunk)]
        paras = [p for p in paras if p]
        if len(paras) < 2:
            continue
        title, meta = paras[-2], paras[-1]
        parts = [p.strip() for p in meta.split("•") if p.strip()]
        # parts: department, location(s), employment type, workplace type (some may be missing)
        mode = parts[-1].lower() if parts else ""
        locs = [p for p in parts[1:] if p.lower() not in ("full time", "part time", "contract", "internship",
                                                          "on-site", "onsite", "hybrid", "remote")]
        jid = m.group(1)
        jobs.setdefault(jid, {"id": jid, "title": title, "locations": locs or parts,
                              "url": f"{base}{m.group(0)[6:-1]}", "posted": None,
                              "remote": True if mode == "remote" else None, "country": None})
    return list(jobs.values())


def scan(src, http):
    slug = src["slug"]
    page = http.get(f"https://careers.kula.ai/{slug}", headers={"Accept": "text/html"}).text
    return parse(page, slug)
