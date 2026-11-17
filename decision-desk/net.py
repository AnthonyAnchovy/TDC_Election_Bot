"""One way to download a results feed: retry what's worth retrying, back off when told to, and keep a copy.

fetch() is for the plain "GET this file" feeds (the state results files, NYT's results data). It:
  - retries a timeout, a dropped connection or a 429/500/502/503/504, waiting as long as Retry-After says (up to
    MAX_WAIT), or a little longer each try
  - stops asking a host that keeps failing for a while (FeedDown), so a dead site doesn't hold up the others
  - hands every response to capture.py
  - in a replay, answers from a captured folder at the desk clock's time and never touches the network

A feed with its own rules (County Watch's conditional GETs, Bluesky's paging) keeps its own request code and calls
capture.payload() itself.
"""
from __future__ import annotations

import asyncio
import random
from urllib.parse import urlsplit

import aiohttp

import capture
import clock
import trust

UA = "DecisionDeskDiscordBot/1.0 (friend-group election night bot)"
RETRY = {429, 500, 502, 503, 504}
MAX_WAIT = 30.0                 # the longest one wait between tries, whatever Retry-After asks for
TRIP_AFTER = 3                  # failed fetches in a row before a host is left alone
COOL_OFF = (60.0, 600.0)        # how long it's left alone: the first time, and at most

_down: dict[str, dict] = {}     # host -> {"fails": n, "until": desk-clock time}
_replay: capture.Reader | None = None
_sleep = asyncio.sleep          # tests swap these two out
_session = aiohttp.ClientSession


class FeedDown(RuntimeError):
    """The host has failed several times in a row and is being left alone for now."""


def replay_from(reader: capture.Reader | None):
    """Answer every fetch() from this capture (None: go back to the network)."""
    global _replay
    _replay = reader


def reset():
    _down.clear()


def retry_after(value: str | None, attempt: int) -> float:
    """Seconds to wait before try number `attempt` + 1: what the server asked for, or 1.5s, 3s, 6s... with jitter."""
    try:
        asked = float(value) if value is not None else None
    except ValueError:
        asked = None                            # an HTTP date: not worth parsing for a 30-second ceiling
    wait = asked if asked is not None and asked >= 0 else 1.5 * 2 ** attempt + random.uniform(0, 0.5)
    return min(wait, MAX_WAIT)


def _host_ok(host: str):
    state = _down.get(host)
    if state and state["fails"] >= TRIP_AFTER and clock.time() < state["until"]:
        raise FeedDown(f"{host} failed {state['fails']} times in a row; trying again in "
                       f"{max(1, round((state['until'] - clock.time()) / 60))} min")


def _failed(host: str):
    state = _down.setdefault(host, {"fails": 0, "until": 0.0})
    state["fails"] += 1
    if state["fails"] >= TRIP_AFTER:
        state["until"] = clock.time() + min(COOL_OFF[0] * 2 ** (state["fails"] - TRIP_AFTER), COOL_OFF[1])


async def fetch(url: str, *, source: str, timeout: float = 45, tries: int = 3, bust: bool = False,
                headers: dict | None = None) -> bytes:
    """The body at url, or raises: FeedDown, RuntimeError("HTTP 404 from ...") or the last network error.
    bust adds a changing query string, for sites whose CDN serves hours-old copies of the plain URL (Florida's)."""
    if _replay is not None:
        row = _replay.row(source, clock.time())
        body = _replay.body(row) if row else None
        if row is None or body is None:
            raise LookupError(f"nothing captured from {source} by then")
        if row["status"] != 200:
            raise RuntimeError(f"HTTP {row['status']} from {url}")
        return body

    host = urlsplit(url).netloc
    _host_ok(host)
    last: Exception | None = None
    for attempt in range(tries):
        try:
            async with _session(timeout=aiohttp.ClientTimeout(total=timeout),
                                headers={"User-Agent": UA, **(headers or {})}) as s:
                async with s.get(f"{url}?t={int(clock.time())}" if bust else url, ssl=trust.ssl_for(url)) as r:
                    body = await r.read()
                    capture.payload(source, url, r.status, body, dict(r.headers))
                    if r.status == 200:
                        _down.pop(host, None)
                        return body
                    last = RuntimeError(f"HTTP {r.status} from {url}")
                    if r.status not in RETRY:
                        break
                    wait = retry_after(r.headers.get("Retry-After"), attempt)
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            last = exc if str(exc) else RuntimeError(f"{type(exc).__name__} from {url}")
            wait = retry_after(None, attempt)
        if attempt + 1 < tries:
            await _sleep(wait)
    _failed(host)
    raise last
