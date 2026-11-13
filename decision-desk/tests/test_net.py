"""The feed layer: retries, backing off, leaving a dead host alone, keeping a copy, and answering from a capture."""
import asyncio
import gzip
import json

import aiohttp
import pytest

import capture
import clock
import net
import results as LIVE

URL = "https://feeds.example/results.txt"


class FakeResponse:
    def __init__(self, status=200, body=b"ok", headers=None):
        self.status, self._body, self.headers = status, body, headers or {}

    async def read(self):
        return self._body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class FakeNet:
    """Stands in for aiohttp.ClientSession: answers each GET with the next scripted response or exception."""

    def __init__(self, *script):
        self.script, self.urls, self.waits = list(script), [], []

    def __call__(self, **_kwargs):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def get(self, url):
        self.urls.append(url)
        step = self.script.pop(0)
        if isinstance(step, Exception):
            raise step
        return step

    async def sleep(self, seconds):
        self.waits.append(seconds)


@pytest.fixture
def wire(monkeypatch, tmp_path):
    """Returns a function that scripts the network. Captures go to a temp folder; nothing real is contacted."""
    capture.start(tmp_path / "cap")
    net.reset()
    net.replay_from(None)

    def script(*steps):
        fake = FakeNet(*steps)
        monkeypatch.setattr(net, "_session", fake)
        monkeypatch.setattr(net, "_sleep", fake.sleep)
        return fake
    yield script
    capture.start(None)
    net.reset()
    net.replay_from(None)


def get(url=URL, **kw):
    return asyncio.run(net.fetch(url, source="test/feed", **kw))


def test_a_good_answer_comes_straight_back(wire):
    fake = wire(FakeResponse(body=b"votes"))
    assert get() == b"votes" and fake.waits == [] and fake.urls == [URL]


def test_a_rate_limit_waits_as_long_as_the_server_asks(wire):
    fake = wire(FakeResponse(429, headers={"Retry-After": "7"}), FakeResponse(body=b"votes"))
    assert get() == b"votes"
    assert fake.waits == [7.0]


def test_a_long_retry_after_is_capped(wire):
    fake = wire(FakeResponse(503, headers={"Retry-After": "3600"}), FakeResponse())
    get()
    assert fake.waits == [net.MAX_WAIT]


def test_timeouts_and_dropped_connections_are_retried_with_growing_waits(wire):
    fake = wire(asyncio.TimeoutError(), aiohttp.ClientConnectionError("reset"), FakeResponse(body=b"votes"))
    assert get() == b"votes"
    assert len(fake.waits) == 2 and 1.5 <= fake.waits[0] <= 2.0 and 3.0 <= fake.waits[1] <= 3.5


def test_a_missing_file_is_not_retried(wire):
    fake = wire(FakeResponse(404))
    with pytest.raises(RuntimeError, match="HTTP 404"):
        get()
    assert fake.waits == [] and len(fake.urls) == 1


def test_it_gives_up_after_the_last_try(wire):
    fake = wire(*[FakeResponse(429)] * 3)
    with pytest.raises(RuntimeError, match="HTTP 429"):
        get()
    assert len(fake.urls) == 3 and len(fake.waits) == 2      # no wait after the final try


def test_a_host_that_keeps_failing_is_left_alone_then_tried_again(wire):
    clock.set(1000)
    fake = wire(*[FakeResponse(500)] * 3, FakeResponse(body=b"back"))
    for _ in range(3):
        with pytest.raises(RuntimeError):
            get(tries=1)
    with pytest.raises(net.FeedDown, match="feeds.example"):
        get(tries=1)
    assert len(fake.urls) == 3                              # the fourth call never reached the network
    clock.set(1000 + net.COOL_OFF[0] + 1)
    assert get(tries=1) == b"back"
    clock.set(1000 + net.COOL_OFF[0] + 2)
    wire(FakeResponse(body=b"still fine"))
    assert get(tries=1) == b"still fine"                    # a success clears the count


def test_one_bad_host_does_not_block_another(wire):
    wire(*[FakeResponse(500)] * 3, FakeResponse(body=b"other"))
    for _ in range(3):
        with pytest.raises(RuntimeError):
            get(tries=1)
    assert get("https://elsewhere.example/x", tries=1) == b"other"


def test_bust_adds_a_changing_query_string(wire):
    clock.set(1793750000)
    fake = wire(FakeResponse())
    get(bust=True)
    assert fake.urls == [URL + "?t=1793750000"]


# ------------------------------------------------------------------ capture
def test_every_response_is_kept_and_unchanged_bodies_are_stored_once(wire, tmp_path):
    clock.set(100)
    wire(FakeResponse(body=b"v1"), FakeResponse(body=b"v1"), FakeResponse(429), FakeResponse(body=b"v2"))
    get()
    clock.set(160)
    get()
    clock.set(220)
    get()
    rows = [json.loads(x) for x in (tmp_path / "cap" / "index.jsonl").read_text().splitlines()]
    assert [(r["t"], r["status"], r.get("same", False)) for r in rows] == \
        [(100, 200, False), (160, 200, True), (220, 429, False), (220, 200, False)]
    assert rows[0]["file"] == rows[1]["file"] and rows[0]["source"] == "test/feed"
    saved = sorted(p.name for p in (tmp_path / "cap" / "test_feed").iterdir())
    assert len(saved) == 3                                  # v1 once, the 429's empty body, v2
    assert gzip.decompress((tmp_path / "cap" / rows[0]["file"]).read_bytes()) == b"v1"


def test_a_zip_is_saved_as_it_came_and_a_huge_body_is_only_noted(wire, tmp_path, monkeypatch):
    wire(FakeResponse(body=b"PK\x03\x04zipped"))
    get()
    monkeypatch.setattr(capture, "MAX_BODY", 4)
    capture.payload("big/file", URL, 200, b"12345")
    reader = capture.Reader(tmp_path / "cap")
    row = reader.row("test/feed", clock.time())
    assert row["file"].endswith(".bin") and reader.body(row) == b"PK\x03\x04zipped"
    big = reader.row("big/file", clock.time())
    assert big["skipped"] == "too big" and reader.body(big) is None


def test_capture_can_be_switched_off_and_never_raises(wire, tmp_path, monkeypatch):
    monkeypatch.setenv("DESK_CAPTURE", "0")
    capture.event("command", name="call")
    assert not (tmp_path / "cap" / "events.jsonl").exists()
    monkeypatch.setenv("DESK_CAPTURE", "1")
    monkeypatch.setattr(capture, "_append", lambda *a: (_ for _ in ()).throw(OSError("disk full")))
    capture.event("command", name="call")                   # logged, not raised


def test_events_are_logged_with_the_desk_clock(wire, tmp_path):
    clock.set(500)
    capture.event("command", user=1, name="call", options={"race": "GA-SEN", "winner": "D"})
    capture.event("post", channel="board_channel", content="hello")
    reader = capture.Reader(tmp_path / "cap")
    assert reader.events("command") == [{"t": 500, "kind": "command", "user": 1, "name": "call",
                                         "options": {"race": "GA-SEN", "winner": "D"}}]
    assert len(reader.events()) == 2


# ------------------------------------------------------------------ replaying a capture
def test_a_replay_answers_from_the_capture_by_time_and_never_touches_the_network(wire, tmp_path):
    for t, body in ((100, b"7pm count"), (200, b"8pm count")):
        clock.set(t)
        wire(FakeResponse(body=body))
        get()
    fake = wire()                                           # an empty script: any real GET would fail
    net.replay_from(capture.Reader(tmp_path / "cap"))
    clock.set(150)
    assert get() == b"7pm count"
    clock.set(999)
    assert get() == b"8pm count"
    clock.set(50)
    with pytest.raises(LookupError):
        get()
    assert fake.urls == []


# ------------------------------------------------------------------ results.py on top of it
FL_ROW = "11/03/2026\tDEM\tDemocrat\tUSS\tUnited States Senator\t1\tAlachua\t0\t0\t10\t{rep}\tNixon\tAlex\t\t{d}\n" \
         "11/03/2026\tREP\tRepublican\tUSS\tUnited States Senator\t1\tAlachua\t0\t0\t10\t{rep}\tMoody\tAshley\t\t{r}\n"


def test_a_feed_that_stops_answering_shows_its_last_good_count_marked_stale(wire, monkeypatch):
    monkeypatch.setattr(LIVE, "_cache", {})
    monkeypatch.setattr(LIVE, "_locks", {})
    wire(FakeResponse(body=FL_ROW.format(rep=4, d=1200, r=1000).encode()), *[FakeResponse(503)] * 3)
    first = asyncio.run(LIVE.fetch_state("FL", force=True))
    assert first["races"]["FL-SEN"]["cands"][0]["votes"] == 1200 and "stale" not in first
    again = asyncio.run(LIVE.fetch_state("FL", force=True))
    assert again["races"] == first["races"] and "HTTP 503" in again["stale"]
    race = asyncio.run(LIVE.race_result("FL-SEN"))            # within the cache time: the good pull, not marked
    assert race["stale"] is None and LIVE.summary(race)["lead"]["party"] == "D"


def test_a_feed_that_never_answered_still_raises(wire, monkeypatch):
    monkeypatch.setattr(LIVE, "_cache", {})
    monkeypatch.setattr(LIVE, "_locks", {})
    wire(FakeResponse(404))
    with pytest.raises(RuntimeError, match="HTTP 404"):
        asyncio.run(LIVE.fetch_state("FL", force=True))
