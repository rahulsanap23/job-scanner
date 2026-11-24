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
POLITE_429_WAITS = (15,)     # polite mode: one short wait before retrying a 429. Longer waits don't help:
                             # a 24 Nov test run waited 3 min per 429 and most sites still refused


class RequestBudgetExceeded(Exception):
    pass


class RateLimited(requests.HTTPError):
    """The site kept answering 429 after the polite waits."""


class Http:
    """One per company scan. Counts requests so a broken endpoint can't loop forever.

    Polite sessions (Workday) share one pacer: every polite request in the process is spaced
    POLITE_GAP apart, and a 429 seen by one company pauses all of them (all Workday sites
    sit behind the same rate limiter, which counts requests per IP address). After the
    deadline set with polite_deadline(), polite requests fail at once, so a site that keeps
    refusing can never stretch the run."""
    _gate = threading.Lock()
    _next_at = 0.0     # monotonic time before which no polite request may start
    _deadline = None   # monotonic time after which polite requests are not sent

    @classmethod
    def polite_deadline(cls, secs):
        """Polite requests are skipped once `secs` seconds from now have passed (None: no limit)."""
        with cls._gate:
            cls._deadline = None if secs is None else time.monotonic() + secs

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
        after = 0
        for wait in (0,) + POLITE_429_WAITS:
            if wait:
                self._cool_down(max(wait + random.random() * 5, after))
            self._tick()
            self._pace()
            if Http._deadline is not None and time.monotonic() > Http._deadline:
                raise RateLimited("429: not tried, the time allowed for this platform was used up")
            r = self.s.request(method, url, **kw)
            if r.status_code != 429:
                r.raise_for_status()
                return r
            try:
                after = min(int(r.headers.get("Retry-After", "0")), 60)
            except ValueError:
                after = 0
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
