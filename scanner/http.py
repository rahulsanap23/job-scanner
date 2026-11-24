"""Shared HTTP session with retries and a browser-like User-Agent."""
import random
import threading
import time

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/129.0 Safari/537.36")
TIMEOUT = 30


POLITE_GAP = 0.8             # polite mode: at least this many seconds between requests (all polite sessions together)
POLITE_429_WAITS = (60, 120)  # polite mode: waits before retrying a 429 (Retry-After wins if longer)
POLITE_GIVE_UP = 3            # after this many polite requests fail on 429, the rest fail at once
                              # (the site is blocking us; waiting minutes per company would only time out)


class RequestBudgetExceeded(Exception):
    pass


class RateLimited(requests.HTTPError):
    """The site kept answering 429 after the polite waits."""


class Http:
    """One per company scan. Counts requests so a broken endpoint can't loop forever.

    Polite sessions (Workday) share one pacer: every polite request in the process is spaced
    POLITE_GAP apart, and a 429 seen by one company pauses all of them (all Workday sites
    sit behind the same rate limiter, which counts requests per IP address)."""
    _gate = threading.Lock()
    _next_at = 0.0   # monotonic time before which no polite request may start
    _failures = 0    # polite requests that ended in RateLimited since the last reset_polite()

    @classmethod
    def reset_polite(cls):
        """Forget earlier 429 failures (before a second try, after a pause)."""
        with cls._gate:
            cls._failures = 0

    def __init__(self, max_requests=250, polite=False):
        """polite=True is for sites that rate-limit hard (Workday): requests are spaced
        POLITE_GAP apart, and a 429 is waited out (POLITE_429_WAITS) instead of retried at once."""
        self.s = requests.Session()
        self.polite = polite
        codes = (500, 502, 503, 504) if polite else (429, 500, 502, 503, 504)
        retry = Retry(total=3, backoff_factor=1.5, status_forcelist=codes,
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

    @classmethod
    def _pace(cls):
        with cls._gate:
            wait = cls._next_at - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            cls._next_at = time.monotonic() + POLITE_GAP

    @classmethod
    def _cool_down(cls, secs):
        with cls._gate:
            cls._next_at = max(cls._next_at, time.monotonic() + secs)

    def _send(self, method, url, **kw):
        kw.setdefault("timeout", TIMEOUT)
        if not self.polite:
            self._tick()
            r = self.s.request(method, url, **kw)
            r.raise_for_status()
            return r
        if Http._failures >= POLITE_GIVE_UP:
            raise RateLimited(f"429: skipped, the site refused {Http._failures} requests in a row")
        after = 0
        for wait in (0,) + POLITE_429_WAITS:
            if wait:
                self._cool_down(max(wait + random.random() * 10, after))
            self._tick()
            self._pace()
            r = self.s.request(method, url, **kw)
            if r.status_code != 429:
                Http._failures = 0
                r.raise_for_status()
                return r
            try:
                after = min(int(r.headers.get("Retry-After", "0")), 300)
            except ValueError:
                after = 0
        with Http._gate:
            Http._failures += 1
        raise RateLimited(f"429 Too Many Requests after waiting {sum(POLITE_429_WAITS)} s", response=r)

    def get(self, url, **kw):
        return self._send("GET", url, **kw)

    def post(self, url, **kw):
        return self._send("POST", url, **kw)

    def get_json(self, url, **kw):
        kw.setdefault("headers", {}).setdefault("Accept", "application/json")
        return self.get(url, **kw).json()

    def post_json(self, url, payload, **kw):
        h = kw.setdefault("headers", {})
        h.setdefault("Accept", "application/json")
        h.setdefault("Content-Type", "application/json")
        return self.post(url, json=payload, **kw).json()
