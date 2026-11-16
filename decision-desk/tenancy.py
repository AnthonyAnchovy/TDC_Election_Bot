"""One bot, many Discord servers: each server keeps its own records, and shares only what it chooses to.

Every server gets its own database for each data pack (packs.db_path), so its calls, votes, outlet log and
predictions are its own. Two servers can run the same election side by side and never see each other's desk.

The code that reads and writes records doesn't pass a server around. It uses `store`, which stands for "the database
of whichever server this piece of work is for": scope(server) sets that for a block, and the bot sets it at the start
of every command and for each server in turn in its background loops. Outside any scope it is the home server's
(GUILD_ID), which is how a one-server bot has always worked.

The global scoreboard is opt-in, twice over:
  a server joins with /globalboard on (the setting "global_board" in its database), and until it does nothing of its
  leaves it; a person in a joined server can still keep their own entry off it ("global_optout").
Entries on the global board are scored against one shared set of results, not each server's own desk: final results
any joined server's admin entered, then the earliest outlet call seen by any of them. A desk's own calls are opinions
and stay home.
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from contextvars import ContextVar

import packs
import scoring as S
from store import Store

_stores: dict[str, Store] = {}
_current: ContextVar[Store | None] = ContextVar("desk_store", default=None)
_connect: dict = {}             # extra sqlite3.connect options (the website needs check_same_thread=False)


def configure(**sqlite):
    _connect.clear()
    _connect.update(sqlite)


def home() -> str:
    return str(os.getenv("GUILD_ID") or "local")


def store_for(server: int | str | None = None, pack_id: str | None = None) -> Store:
    """The database for a server (the home server if None) under a pack (the active one if None)."""
    path = str(packs.db_path(pack_id, server if server is not None else None))
    if path not in _stores:
        _stores[path] = Store(path, **_connect)
    return _stores[path]


def current() -> Store:
    return _current.get() or store_for()


def enter(server: int | str | None, pack_id: str | None = None) -> Store:
    """Make a server's database the current one for the rest of this task (a command handler). No way back is
    needed: each command runs in its own task."""
    s = store_for(server, pack_id)
    _current.set(s)
    return s


@contextmanager
def scope(server: int | str | None, pack_id: str | None = None):
    """Work on one server's records for a block."""
    token = _current.set(store_for(server, pack_id))
    try:
        yield _current.get()
    finally:
        _current.reset(token)


def forget():
    """Close every database (tests, and switching the bot to another pack)."""
    for s in _stores.values():
        s.db.close()
    _stores.clear()
    _current.set(None)


class _Proxy:
    """`store`: every attribute is the current server's Store's."""

    def __getattr__(self, name):
        return getattr(current(), name)


store = _Proxy()


# ------------------------------------------------------------------ the global scoreboard
def joined(server: int | str, pack_id: str | None = None) -> bool:
    return bool(store_for(server, pack_id).setting("global_board", False))


def set_joined(server: int | str, on: bool, pack_id: str | None = None):
    store_for(server, pack_id).set_setting("global_board", bool(on))


def opted_out(server: int | str, user_id: int, pack_id: str | None = None) -> bool:
    return str(user_id) in store_for(server, pack_id).setting("global_optout", [])


def set_opted_out(server: int | str, user_id: int, out: bool, pack_id: str | None = None):
    s = store_for(server, pack_id)
    listed = set(s.setting("global_optout", []))
    (listed.add if out else listed.discard)(str(user_id))
    s.set_setting("global_optout", sorted(listed))


def joined_servers(pack_id: str | None = None) -> list[str]:
    return [sv for sv in packs.servers(pack_id) if joined(sv, pack_id)]


def global_results(pack_id: str | None = None, gate: bool = True) -> dict[str, str]:
    """The results the global board scores against: finals from any joined server, then the earliest outlet call
    any of them logged. No server's own desk calls."""
    procalls, finals = [], {}
    for sv in joined_servers(pack_id):
        s = store_for(sv, pack_id)
        procalls += s.procalls()
        finals.update(s.setting("final_results", {}))
    return S.truth({}, procalls, finals=finals, gate=gate)


def global_board(pack_id: str | None = None, points: dict | None = None, gate: bool = True) -> list[dict]:
    """Everyone on the global board, best first. A person in two joined servers appears once, with the entry they
    saved last. Each row carries the server it came from and the name that server knows them by."""
    entries: dict[int, tuple] = {}
    origin: dict[int, tuple[str, str]] = {}
    for sv in joined_servers(pack_id):
        s = store_for(sv, pack_id)
        names, out = s.setting("names", {}), set(s.setting("global_optout", []))
        for uid in s.predictors():
            if str(uid) in out:
                continue
            picks, meta = s.user_predictions(uid)
            if uid not in entries or (meta or {}).get("saved_at", 0) > (entries[uid][1] or {}).get("saved_at", 0):
                entries[uid] = (picks, meta)
                origin[uid] = (sv, names.get(str(uid)) or f"user {str(uid)[-4:]}")
    rows = S.leaderboard(entries, global_results(pack_id, gate), points, None)
    for r in rows:
        r["server"], r["name"] = origin[r["user_id"]]
    return rows
