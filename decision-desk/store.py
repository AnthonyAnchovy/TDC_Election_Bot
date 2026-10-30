"""SQLite storage. Everything survives a bot restart."""
import json
import sqlite3
import time


class Store:
    def __init__(self, path: str = "desk.db"):
        self.db = sqlite3.connect(path)
        self.db.executescript(
            """
            -- official desk calls (what the maps show)
            CREATE TABLE IF NOT EXISTS calls (
                race_id TEXT PRIMARY KEY, party TEXT NOT NULL, called_at REAL NOT NULL, detail TEXT);
            CREATE TABLE IF NOT EXISTS history (
                id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, action TEXT NOT NULL,
                race_id TEXT NOT NULL, party TEXT, detail TEXT);
            -- individual /call and /uncall votes
            CREATE TABLE IF NOT EXISTS desk_votes (
                id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, race_id TEXT NOT NULL,
                kind TEXT NOT NULL, party TEXT, ts REAL NOT NULL, active INTEGER NOT NULL DEFAULT 1);
            -- calls by real outlets (AP, NYT, Wasserman...), one per outlet per race
            CREATE TABLE IF NOT EXISTS procalls (
                race_id TEXT NOT NULL, outlet TEXT NOT NULL, party TEXT NOT NULL, ts REAL NOT NULL,
                logged_by INTEGER, PRIMARY KEY (race_id, outlet));
            -- prediction game
            CREATE TABLE IF NOT EXISTS predictions (
                user_id INTEGER NOT NULL, race_id TEXT NOT NULL, party TEXT NOT NULL, ts REAL NOT NULL,
                PRIMARY KEY (user_id, race_id));
            CREATE TABLE IF NOT EXISTS prediction_meta (
                user_id INTEGER PRIMARY KEY, saved_at REAL NOT NULL, house_dem_seats INTEGER);
            CREATE TABLE IF NOT EXISTS sent_alerts (key TEXT PRIMARY KEY, ts REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
            """
        )
        self.db.commit()

    # ------------------------------------------------------------------ official calls
    def get(self, race_id: str):
        row = self.db.execute(
            "SELECT party, called_at, detail FROM calls WHERE race_id = ?", (race_id,)).fetchone()
        if not row:
            return None
        return {"party": row[0], "called_at": row[1], "detail": json.loads(row[2] or "{}")}

    def all(self) -> dict[str, str]:
        return dict(self.db.execute("SELECT race_id, party FROM calls").fetchall())

    def all_full(self) -> dict[str, dict]:
        rows = self.db.execute("SELECT race_id, party, called_at FROM calls").fetchall()
        return {r[0]: {"party": r[1], "called_at": r[2]} for r in rows}

    def call(self, race_id: str, party: str, detail: dict | None = None, ts: float | None = None):
        now = ts or time.time()
        d = json.dumps(detail or {})
        self.db.execute("INSERT OR REPLACE INTO calls VALUES (?,?,?,?)", (race_id, party, now, d))
        self.db.execute("INSERT INTO history (ts, action, race_id, party, detail) VALUES (?,?,?,?,?)",
                        (now, "call", race_id, party, d))
        # retraction votes are moot once the race is (re)called
        self.db.execute("UPDATE desk_votes SET active = 0 WHERE race_id = ? AND kind = 'uncall'", (race_id,))
        self.db.commit()

    def uncall(self, race_id: str, detail: dict | None = None, ts: float | None = None):
        prev = self.get(race_id)
        self.db.execute("DELETE FROM calls WHERE race_id = ?", (race_id,))
        self.db.execute("INSERT INTO history (ts, action, race_id, party, detail) VALUES (?,?,?,?,?)",
                        (ts or time.time(), "uncall", race_id, prev["party"] if prev else None,
                         json.dumps(detail or {})))
        # start the race fresh: all standing votes are cleared (kept for stats)
        self.db.execute("UPDATE desk_votes SET active = 0 WHERE race_id = ?", (race_id,))
        self.db.commit()

    def recent(self, n: int = 10):
        return self.db.execute(
            "SELECT ts, action, race_id, party FROM history ORDER BY id DESC LIMIT ?", (n,)).fetchall()

    # ------------------------------------------------------------------ individual votes
    def active_vote(self, user_id: int, race_id: str, kind: str):
        row = self.db.execute(
            "SELECT party, ts FROM desk_votes WHERE user_id=? AND race_id=? AND kind=? AND active=1",
            (user_id, race_id, kind)).fetchone()
        return {"party": row[0], "ts": row[1]} if row else None

    def cast(self, user_id: int, race_id: str, kind: str, party: str | None, ts: float | None = None):
        self.db.execute("UPDATE desk_votes SET active=0 WHERE user_id=? AND race_id=? AND kind=?",
                        (user_id, race_id, kind))
        self.db.execute("INSERT INTO desk_votes (user_id, race_id, kind, party, ts) VALUES (?,?,?,?,?)",
                        (user_id, race_id, kind, party, ts or time.time()))
        self.db.commit()

    def withdraw(self, user_id: int, race_id: str) -> bool:
        cur = self.db.execute("UPDATE desk_votes SET active=0 WHERE user_id=? AND race_id=? AND active=1",
                              (user_id, race_id))
        self.db.commit()
        return cur.rowcount > 0

    def active_votes(self, race_id: str, kind: str) -> dict[int, str]:
        """user_id -> party for standing votes on a race."""
        return dict(self.db.execute(
            "SELECT user_id, party FROM desk_votes WHERE race_id=? AND kind=? AND active=1",
            (race_id, kind)).fetchall())

    def all_call_votes(self):
        """Every call vote ever cast: (user_id, race_id, party, ts), oldest first."""
        return self.db.execute(
            "SELECT user_id, race_id, party, ts FROM desk_votes WHERE kind='call' ORDER BY ts").fetchall()

    # ------------------------------------------------------------------ pro calls
    def procall(self, race_id: str, outlet: str, party: str, user_id: int, ts: float | None = None):
        self.db.execute("INSERT OR REPLACE INTO procalls VALUES (?,?,?,?,?)",
                        (race_id, outlet, party, ts or time.time(), user_id))
        self.db.commit()

    def procalls(self, race_id: str | None = None) -> list[dict]:
        q = "SELECT race_id, outlet, party, ts FROM procalls"
        args = ()
        if race_id:
            q += " WHERE race_id = ?"
            args = (race_id,)
        return [{"race_id": r[0], "outlet": r[1], "party": r[2], "ts": r[3]}
                for r in self.db.execute(q + " ORDER BY ts", args).fetchall()]

    # ------------------------------------------------------------------ predictions
    def save_predictions(self, user_id: int, picks: dict[str, str], house_dem_seats: int | None):
        now = time.time()
        self.db.execute("DELETE FROM predictions WHERE user_id = ?", (user_id,))
        self.db.executemany("INSERT INTO predictions VALUES (?,?,?,?)",
                            [(user_id, rid, p, now) for rid, p in picks.items()])
        self.db.execute("INSERT OR REPLACE INTO prediction_meta VALUES (?,?,?)",
                        (user_id, now, house_dem_seats))
        self.db.commit()

    def user_predictions(self, user_id: int) -> tuple[dict[str, str], dict | None]:
        picks = dict(self.db.execute("SELECT race_id, party FROM predictions WHERE user_id = ?",
                                     (user_id,)).fetchall())
        row = self.db.execute("SELECT saved_at, house_dem_seats FROM prediction_meta WHERE user_id = ?",
                              (user_id,)).fetchone()
        return picks, ({"saved_at": row[0], "house_dem_seats": row[1]} if row else None)

    def predictors(self) -> list[int]:
        return [r[0] for r in self.db.execute("SELECT user_id FROM prediction_meta ORDER BY saved_at")]

    # ------------------------------------------------------------------ alerts
    def alert_sent(self, key: str) -> bool:
        return self.db.execute("SELECT 1 FROM sent_alerts WHERE key = ?", (key,)).fetchone() is not None

    def mark_alert(self, key: str):
        self.db.execute("INSERT OR IGNORE INTO sent_alerts VALUES (?,?)", (key, time.time()))
        self.db.commit()

    # ------------------------------------------------------------------ settings
    def setting(self, key: str, default=None):
        row = self.db.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set_setting(self, key: str, value):
        self.db.execute("INSERT OR REPLACE INTO settings VALUES (?,?)", (key, json.dumps(value)))
        self.db.commit()

    # ------------------------------------------------------------------ reset
    def reset(self, include_predictions: bool = False):
        for t in ("calls", "history", "desk_votes", "procalls", "sent_alerts"):
            self.db.execute(f"DELETE FROM {t}")
        if include_predictions:
            self.db.execute("DELETE FROM predictions")
            self.db.execute("DELETE FROM prediction_meta")
        self.db.commit()
