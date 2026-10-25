"""SQLite board: drivers, ranked minutes, and what the agents spent."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_DB = HERE / "data" / "lane.db"
_LOCK = threading.Lock()


def db_path() -> Path:
    return Path(os.environ.get("CLUB_DB", DEFAULT_DB))


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_day(moment: datetime | None = None) -> str:
    return (moment or utc_now()).date().isoformat()


def _connect() -> sqlite3.Connection:
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init() -> None:
    with _LOCK, _connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS players (
                name TEXT PRIMARY KEY,
                points INTEGER NOT NULL DEFAULT 0,
                races INTEGER NOT NULL DEFAULT 0,
                best_points INTEGER,
                last_vehicle TEXT
            );
            CREATE TABLE IF NOT EXISTS results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                race_id TEXT,
                player TEXT NOT NULL,
                vehicle_id TEXT,
                points INTEGER NOT NULL,
                coins INTEGER NOT NULL DEFAULT 0,
                hurdles INTEGER NOT NULL DEFAULT 0,
                finished INTEGER NOT NULL DEFAULT 0,
                time_s REAL,
                detail TEXT,
                created TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS open_races (
                id TEXT PRIMARY KEY,
                player TEXT NOT NULL,
                vehicle_id TEXT NOT NULL,
                seed INTEGER NOT NULL,
                crew INTEGER NOT NULL DEFAULT 1,
                created TEXT NOT NULL,
                consumed INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS agent_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                race_id TEXT,
                kind TEXT NOT NULL,
                player TEXT,
                input_tokens INTEGER NOT NULL DEFAULT 0,
                output_tokens INTEGER NOT NULL DEFAULT 0,
                cache_read INTEGER NOT NULL DEFAULT 0,
                cache_write INTEGER NOT NULL DEFAULT 0,
                cost_usd REAL NOT NULL DEFAULT 0,
                seconds REAL,
                trace TEXT,
                created TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS blocks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                player TEXT,
                field TEXT,
                reason TEXT,
                snippet TEXT,
                created TEXT NOT NULL
            );
            """
        )


def _player_row(conn: sqlite3.Connection, name: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM players WHERE name = ?", (name,)).fetchone()


def ensure_player(name: str) -> dict:
    init()
    with _LOCK, _connect() as conn:
        row = _player_row(conn, name)
        if row is None:
            conn.execute("INSERT INTO players (name) VALUES (?)", (name,))
            row = _player_row(conn, name)
        return dict(row)


def player(name: str) -> dict | None:
    init()
    with _LOCK, _connect() as conn:
        row = _player_row(conn, name)
        return dict(row) if row else None


def standings(limit: int = 20) -> list[dict]:
    """Drivers with at least one ranked minute, best total first."""
    init()
    with _LOCK, _connect() as conn:
        rows = conn.execute(
            """
            SELECT name, points, races, best_points FROM players
            WHERE races > 0
            ORDER BY points DESC, name COLLATE NOCASE
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [{**dict(row), "rank": index} for index, row in enumerate(rows, start=1)]


def rank_of(name: str) -> int | None:
    for row in standings(10_000):
        if row["name"] == name:
            return row["rank"]
    return None


def save_open_race(row: dict) -> None:
    init()
    with _LOCK, _connect() as conn:
        conn.execute(
            "INSERT INTO open_races (id, player, vehicle_id, seed, crew, created, consumed) VALUES (?, ?, ?, ?, ?, ?, 0)",
            (row["id"], row["player"], row["vehicle_id"], row["seed"], 1 if row.get("crew", True) else 0, utc_now().isoformat(timespec="seconds")),
        )


def open_race(race_id: str) -> dict | None:
    init()
    with _LOCK, _connect() as conn:
        row = conn.execute("SELECT * FROM open_races WHERE id = ?", (race_id,)).fetchone()
    return dict(row) if row else None


def consume_race(race_id: str) -> bool:
    with _LOCK, _connect() as conn:
        cur = conn.execute("UPDATE open_races SET consumed = 1 WHERE id = ? AND consumed = 0", (race_id,))
        return cur.rowcount == 1


def minutes_today(name: str) -> int:
    """Ranked minutes this driver has opened since midnight UTC."""
    init()
    with _LOCK, _connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM open_races WHERE player = ? AND created >= ?",
            (name, utc_day() + "T00:00:00"),
        ).fetchone()
    return int(row["n"])


def recent_results(name: str, limit: int = 5) -> list[dict]:
    init()
    with _LOCK, _connect() as conn:
        rows = conn.execute(
            """
            SELECT vehicle_id, points, coins, hurdles, finished, time_s, created
            FROM results WHERE player = ? ORDER BY id DESC LIMIT ?
            """,
            (name, limit),
        ).fetchall()
    return [{**dict(row), "finished": bool(row["finished"])} for row in rows]


def post_result(name: str, points: int, summary: dict) -> dict:
    ensure_player(name)
    with _LOCK, _connect() as conn:
        row = dict(_player_row(conn, name))
        total = row["points"] + int(points)
        races = row["races"] + 1
        best = max(int(points), row["best_points"] or 0)
        conn.execute(
            "UPDATE players SET points = ?, races = ?, best_points = ?, last_vehicle = ? WHERE name = ?",
            (total, races, best, summary.get("vehicle_id"), name),
        )
        conn.execute(
            """
            INSERT INTO results (race_id, player, vehicle_id, points, coins, hurdles, finished, time_s, detail, created)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                summary.get("race_id"),
                name,
                summary.get("vehicle_id"),
                int(points),
                int(summary.get("coins") or 0),
                int(summary.get("hurdles") or 0),
                1 if summary.get("finished") else 0,
                summary.get("time_s"),
                json.dumps(summary, default=str),
                utc_now().isoformat(timespec="seconds"),
            ),
        )
    return {"ok": True, "points": int(points), "total": total, "races": races, "best_points": best}


def log_run(kind: str, player_name: str, usage: dict, trace: list, race_id: str = "", seconds: float | None = None) -> None:
    init()
    with _LOCK, _connect() as conn:
        conn.execute(
            """
            INSERT INTO agent_runs
                (race_id, kind, player, input_tokens, output_tokens, cache_read, cache_write, cost_usd, seconds, trace, created)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                race_id,
                kind,
                player_name,
                int(usage.get("input_tokens") or 0),
                int(usage.get("output_tokens") or 0),
                int(usage.get("cache_read_tokens") or 0),
                int(usage.get("cache_write_tokens") or 0),
                float(usage.get("cost_usd") or 0),
                None if seconds is None else round(seconds, 2),
                json.dumps(trace, default=str)[:20000],
                utc_now().isoformat(timespec="seconds"),
            ),
        )


def log_block(player_name: str, field: str, reason: str, snippet: str) -> None:
    init()
    with _LOCK, _connect() as conn:
        conn.execute(
            "INSERT INTO blocks (player, field, reason, snippet, created) VALUES (?, ?, ?, ?, ?)",
            (player_name, field, reason, (snippet or "")[:120], utc_now().isoformat(timespec="seconds")),
        )


def budget_today() -> dict:
    init()
    with _LOCK, _connect() as conn:
        row = conn.execute(
            """
            SELECT COALESCE(SUM(input_tokens + output_tokens), 0) AS tokens,
                   COUNT(*) AS runs,
                   COALESCE(SUM(cost_usd), 0) AS cost
            FROM agent_runs WHERE created >= ?
            """,
            (utc_day() + "T00:00:00",),
        ).fetchone()
    return {"day": utc_day(), "tokens": int(row["tokens"]), "runs": int(row["runs"]), "cost_usd": round(float(row["cost"]), 6)}


def minute_ledger(limit: int = 10) -> list[dict]:
    """Token use per ranked minute: the steward, passenger, and clerk runs added together."""
    init()
    with _LOCK, _connect() as conn:
        rows = conn.execute(
            """
            SELECT race_id, MIN(player) AS player, MIN(created) AS created,
                   SUM(input_tokens) AS input_tokens, SUM(cache_read) AS cache_read,
                   SUM(cache_write) AS cache_write, SUM(output_tokens) AS output_tokens,
                   SUM(cost_usd) AS cost_usd, SUM(COALESCE(seconds, 0)) AS seconds,
                   GROUP_CONCAT(kind) AS kinds
            FROM agent_runs WHERE race_id != ''
            GROUP BY race_id ORDER BY MAX(id) DESC LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [{**dict(row), "cost_usd": round(float(row["cost_usd"]), 6), "seconds": round(float(row["seconds"]), 1)} for row in rows]


def recent_blocks(limit: int = 8) -> list[dict]:
    init()
    with _LOCK, _connect() as conn:
        rows = conn.execute(
            "SELECT player, field, reason, snippet, created FROM blocks ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]
