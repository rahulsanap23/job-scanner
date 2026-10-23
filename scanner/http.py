"""Shared HTTP session with retries and a browser-like User-Agent."""
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/129.0 Safari/537.36")
TIMEOUT = 30


class RequestBudgetExceeded(Exception):
    pass


class Http:
    """One per company scan. Counts requests so a broken endpoint can't loop forever."""

    def __init__(self, max_requests=250):
        self.s = requests.Session()
        retry = Retry(total=3, backoff_factor=1.5, status_forcelist=(429, 500, 502, 503, 504),
                      allowed_methods=frozenset(["GET", "POST"]), respect_retry_after_header=True)
        self.s.mount("https://", HTTPAdapter(max_retries=retry))
        self.s.mount("http://", HTTPAdapter(max_retries=retry))
        self.s.headers.update({"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"})
        self.count = 0
        self.max_requests = max_requests

    def _tick(self):
        self.count += 1
        if self.count > self.max_requests:
            raise RequestBudgetExceeded(f"stopped after {self.max_requests} requests")

    def get(self, url, **kw):
        self._tick()
        kw.setdefault("timeout", TIMEOUT)
        r = self.s.get(url, **kw)
        r.raise_for_status()
        return r

    def post(self, url, **kw):
        self._tick()
        kw.setdefault("timeout", TIMEOUT)
        r = self.s.post(url, **kw)
        r.raise_for_status()
        return r

    def get_json(self, url, **kw):
        kw.setdefault("headers", {}).setdefault("Accept", "application/json")
        return self.get(url, **kw).json()

    def post_json(self, url, payload, **kw):
        h = kw.setdefault("headers", {})
        h.setdefault("Accept", "application/json")
        h.setdefault("Content-Type", "application/json")
        return self.post(url, json=payload, **kw).json()
