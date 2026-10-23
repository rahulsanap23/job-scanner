"""SuccessFactors Career Site Builder RSS feeds (/services/rss/job/?keywords=(term)).

Works for CSB sites whose search page renders client-side. Item titles look like
"Software Engineer (Chennai, IN)"; the last bracket is the location.
"""
import html
import re
import xml.etree.ElementTree as ET


def scan(src, http):
    from . import SEARCH_TERMS
    base = src["base"].rstrip("/")
    seen = {}
    for term in src.get("terms") or SEARCH_TERMS:
        r = http.get(f"{base}/services/rss/job/", params={"locale": src.get("locale", "en_US"),
                                                          "keywords": f"({term})"})
        root = ET.fromstring(r.content)
        for item in root.iter("item"):
            raw = html.unescape((item.findtext("title") or "").strip())
            link = (item.findtext("link") or "").strip()
            m = re.match(r"^(.*)\(([^()]*)\)\s*$", raw)
            title, loc = (m.group(1).strip(), m.group(2).strip()) if m else (raw, "")
            seen.setdefault(link or raw, {"id": link or raw, "title": title, "locations": [loc], "url": link,
                                          "posted": item.findtext("pubDate"), "remote": None, "country": None})
    return list(seen.values())
