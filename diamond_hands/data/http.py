"""Shared HTTP session with retries. Failures become DataError, never guesses."""

from __future__ import annotations

import time

import requests

# Generic browser user agent; some public endpoints reject library defaults.
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
RETRY_STATUS = {429, 500, 502, 503, 504}


class DataError(Exception):
    def __init__(self, source: str, detail: str):
        super().__init__(f"no data from {source}: {detail}")
        self.source = source
        self.detail = detail


_session: requests.Session | None = None


def session() -> requests.Session:
    global _session
    if _session is None:
        _session = requests.Session()
        _session.headers["User-Agent"] = USER_AGENT
    return _session


def get(url: str, source: str, params: dict | None = None, timeout: float = 15, retries: int = 2) -> requests.Response:
    last = "unknown error"
    for attempt in range(retries + 1):
        try:
            r = session().get(url, params=params, timeout=timeout)
        except requests.RequestException as e:
            last = type(e).__name__
        else:
            if r.status_code == 200:
                return r
            last = f"HTTP {r.status_code}"
            if r.status_code not in RETRY_STATUS:
                break
        if attempt < retries:
            time.sleep(1.5 * (attempt + 1))
    raise DataError(source, last)
