"""SQLite event store and the banker's write path.

The store keeps one row per event. The current game is the newest row in
`games`; starting a new game leaves older games in the file untouched.
Writes are serialised by a lock: fold, validate, then insert.
"""

import json
import re
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .board import Board
from .state import Event, LogEntry, RuleError, State, fold

SCHEMA = """
CREATE TABLE IF NOT EXISTS games (
    id         INTEGER PRIMARY KEY,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    id         INTEGER PRIMARY KEY,
    game_id    INTEGER NOT NULL REFERENCES games(id),
    created_at TEXT NOT NULL,
    type       TEXT NOT NULL,
    payload    TEXT NOT NULL,
    voided_at  TEXT
);
CREATE INDEX IF NOT EXISTS events_by_game ON events (game_id, id);
"""


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Store:
    def __init__(self, db_path: str, board: Board):
        self.db_path = db_path
        self.board = board
        self.lock = threading.Lock()
        if db_path != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._memory = sqlite3.connect(db_path, check_same_thread=False) if db_path == ":memory:" else None
        with self._connect() as db:
            db.executescript(SCHEMA)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        """One transaction: commit on success, roll back on error."""
        db = self._memory or sqlite3.connect(self.db_path, timeout=10)
        try:
            db.execute("PRAGMA foreign_keys = ON")
            with db:
                yield db
        finally:
            if db is not self._memory:
                db.close()

    # --- reading -----------------------------------------------------------------

    def game_id(self) -> int | None:
        with self._connect() as db:
            row = db.execute("SELECT max(id) FROM games").fetchone()
        return row[0]

    def events(self, game_id: int | None = None) -> list[Event]:
        game_id = game_id or self.game_id()
        if game_id is None:
            return []
        with self._connect() as db:
            rows = db.execute(
                "SELECT id, type, payload, created_at, voided_at FROM events "
                "WHERE game_id = ? ORDER BY id", (game_id,)).fetchall()
        return [Event(r[0], r[1], json.loads(r[2]), r[3], r[4] is not None) for r in rows]

    def state(self) -> State | None:
        events = self.events()
        return fold(self.board, events) if events else None

    def next_id(self, prefix: str) -> str:
        """A player ("p") or company ("c") id never used in this game, voided events included."""
        pattern = re.compile(rf'"{prefix}(\d+)"')
        used = [int(n) for ev in self.events() for n in pattern.findall(json.dumps(ev.payload))]
        return f"{prefix}{max(used, default=0) + 1}"

    # --- writing -----------------------------------------------------------------

    def new_game(self, players: list[str], settings: dict) -> State:
        payload = {"players": [{"id": f"p{i}", "name": n} for i, n in enumerate(players, 1)],
                   "settings": settings}
        return self.import_events([{"type": "game_start", "payload": payload}])

    def import_events(self, raw_events: list[dict]) -> State:
        """Start a new game from an exported event list (voided events stay voided)."""
        now = utcnow()
        events = [Event(None, e["type"], e["payload"], e.get("created_at") or now,
                        bool(e.get("voided"))) for e in raw_events]
        if not events or events[0].type != "game_start" or events[0].voided:
            raise RuleError("The first event must be an active game_start")
        state = fold(self.board, events)
        with self.lock, self._connect() as db:
            game_id = db.execute("INSERT INTO games (created_at) VALUES (?)", (now,)).lastrowid
            for ev in events:
                db.execute(
                    "INSERT INTO events (game_id, created_at, type, payload, voided_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (game_id, ev.created_at, ev.type, json.dumps(ev.payload),
                     now if ev.voided else None))
        return state

    def record(self, *items: tuple[str, dict]) -> tuple[State, list[LogEntry]]:
        """Validate and append one or more events atomically (all or nothing)."""
        with self.lock:
            game_id = self.game_id()
            if game_id is None:
                raise RuleError("Start a game first")
            state = fold(self.board, self.events(game_id))
            now = utcnow()
            new = [Event(None, type_, payload, now) for type_, payload in items]
            entries = [state.apply(ev) for ev in new]
            with self._connect() as db:
                for ev in new:
                    ev.id = db.execute(
                        "INSERT INTO events (game_id, created_at, type, payload) VALUES (?, ?, ?, ?)",
                        (game_id, ev.created_at, ev.type, json.dumps(ev.payload))).lastrowid
        return state, entries

    def set_voided(self, event_id: int, voided: bool) -> State:
        """Void or restore an event, refusing if any later event would become invalid."""
        with self.lock:
            events = self.events()
            target = next((ev for ev in events if ev.id == event_id), None)
            if target is None:
                raise RuleError(f"No event #{event_id} in this game")
            if target.type == "game_start":
                raise RuleError("The game start can't be voided; start a new game instead")
            if target.voided == voided:
                raise RuleError(f"Event #{event_id} is already {'voided' if voided else 'active'}")
            target.voided = voided
            try:
                state = fold(self.board, events)
            except RuleError as e:
                verb = "void" if voided else "restore"
                at = f"event #{e.event.id} ({e.event.type.replace('_', ' ')})" if e.event else "a later event"
                raise RuleError(f"Can't {verb} #{event_id}: {at} would no longer be valid. {e}") from e
            with self._connect() as db:
                db.execute("UPDATE events SET voided_at = ? WHERE id = ?",
                           (utcnow() if voided else None, event_id))
        return state

    def undo_last(self) -> tuple[State, Event]:
        last = next((ev for ev in reversed(self.events())
                     if not ev.voided and ev.type != "game_start"), None)
        if last is None:
            raise RuleError("Nothing to undo")
        return self.set_voided(last.id, True), last

    def export(self) -> dict:
        return {"format": "monopoly-plus-events", "version": 1, "edition": self.board.edition,
                "events": [{"id": ev.id, "type": ev.type, "payload": ev.payload,
                            "created_at": ev.created_at, "voided": ev.voided}
                           for ev in self.events()]}
