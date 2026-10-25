"""SQLite board: drivers, open races, ads, and what the agents spent."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from club import HOUSE_DRIVERS, choose_ad

HERE = Path(__file__).resolve().parent
DEFAULT_DB = HERE / "data" / "league.db"
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
                wins INTEGER NOT NULL DEFAULT 0,
                checkin_day TEXT,
                streak INTEGER NOT NULL DEFAULT 0,
                house INTEGER NOT NULL DEFAULT 0,
                last_mode TEXT,
                best_time REAL,
                best_mode TEXT
            );
            CREATE TABLE IF NOT EXISTS results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                player TEXT NOT NULL,
                mode TEXT,
                vehicle_id TEXT,
                match_type TEXT,
                opponent TEXT,
                time_s REAL,
                points INTEGER NOT NULL,
                won INTEGER NOT NULL DEFAULT 0,
                detail TEXT,
                created TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS open_races (
                id TEXT PRIMARY KEY,
                player TEXT NOT NULL,
                vehicle_id TEXT NOT NULL,
                mode TEXT NOT NULL,
                seed INTEGER NOT NULL,
                match_type TEXT NOT NULL,
                opponent TEXT,
                duel_id TEXT,
                rival_cruise REAL NOT NULL,
                created TEXT NOT NULL,
                consumed INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS impressions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT,
                ad_id TEXT NOT NULL,
                placement TEXT NOT NULL,
                mode TEXT NOT NULL,
                created TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS agent_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kind TEXT NOT NULL,
                player TEXT,
                input_tokens INTEGER NOT NULL DEFAULT 0,
                output_tokens INTEGER NOT NULL DEFAULT 0,
                cache_read INTEGER NOT NULL DEFAULT 0,
                cache_write INTEGER NOT NULL DEFAULT 0,
                cost_usd REAL NOT NULL DEFAULT 0,
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
            CREATE TABLE IF NOT EXISTS duels (
                id TEXT PRIMARY KEY,
                created TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS duel_times (
                duel_id TEXT NOT NULL,
                player TEXT NOT NULL,
                time_s REAL NOT NULL,
                h2h_paid INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (duel_id, player)
            );
            """
        )
        count = conn.execute("SELECT COUNT(*) AS n FROM players").fetchone()["n"]
        if count == 0:
            conn.executemany(
                """
                INSERT INTO players (name, points, races, wins, house, last_mode, streak)
                VALUES (?, ?, ?, ?, 1, ?, 0)
                """,
                HOUSE_DRIVERS,
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


def rank_of(name: str) -> int | None:
    init()
    with _LOCK, _connect() as conn:
        rows = conn.execute(
            "SELECT name FROM players ORDER BY points DESC, wins DESC, name COLLATE NOCASE"
        ).fetchall()
    for index, row in enumerate(rows, start=1):
        if row["name"] == name:
            return index
    return None


def standings(limit: int = 20) -> list[dict]:
    init()
    with _LOCK, _connect() as conn:
        rows = conn.execute(
            """
            SELECT name, points, races, wins, streak, house, last_mode, best_time, best_mode
            FROM players
            ORDER BY points DESC, wins DESC, name COLLATE NOCASE
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    table = []
    for index, row in enumerate(rows, start=1):
        item = dict(row)
        item["rank"] = index
        table.append(item)
    return table


def checkin(name: str) -> dict:
    """One award per club day (UTC). The amount is fixed here, not by the model."""
    ensure_player(name)
    today = utc_day()
    yesterday = (utc_now() - timedelta(days=1)).date().isoformat()
    with _LOCK, _connect() as conn:
        row = dict(_player_row(conn, name))
        if row.get("house"):
            return {"ok": False, "points": 0, "reason": "House drivers do not check in.", "streak": 0, "total": row["points"]}
        if row.get("checkin_day") == today:
            return {
                "ok": True,
                "points": 0,
                "reason": "Already checked in today.",
                "streak": row["streak"],
                "total": row["points"],
                "already": True,
            }
        streak = row["streak"] + 1 if row.get("checkin_day") == yesterday else 1
        points = 15 + min(streak - 1, 5) * 2
        total = row["points"] + points
        conn.execute(
            "UPDATE players SET points = ?, checkin_day = ?, streak = ? WHERE name = ?",
            (total, today, streak, name),
        )
    return {
        "ok": True,
        "points": points,
        "reason": "Checked in.",
        "streak": streak,
        "total": total,
        "already": False,
        "day": today,
    }


def save_open_race(row: dict) -> None:
    init()
    with _LOCK, _connect() as conn:
        conn.execute(
            """
            INSERT INTO open_races
                (id, player, vehicle_id, mode, seed, match_type, opponent, duel_id, rival_cruise, created, consumed)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
            """,
            (
                row["id"],
                row["player"],
                row["vehicle_id"],
                row["mode"],
                row["seed"],
                row["match_type"],
                row.get("opponent"),
                row.get("duel_id"),
                row["rival_cruise"],
                utc_now().isoformat(timespec="seconds"),
            ),
        )


def open_race(race_id: str) -> dict | None:
    init()
    with _LOCK, _connect() as conn:
        row = conn.execute("SELECT * FROM open_races WHERE id = ?", (race_id,)).fetchone()
    return dict(row) if row else None


def consume_race(race_id: str) -> bool:
    with _LOCK, _connect() as conn:
        cur = conn.execute(
            "UPDATE open_races SET consumed = 1 WHERE id = ? AND consumed = 0",
            (race_id,),
        )
        return cur.rowcount == 1


def post_result(player_name: str, points: int, won: bool, summary: dict) -> dict:
    ensure_player(player_name)
    with _LOCK, _connect() as conn:
        row = dict(_player_row(conn, player_name))
        if row.get("house"):
            return {"ok": False, "points": 0, "total": row["points"], "reason": "House card."}
        total = row["points"] + int(points)
        races = row["races"] + 1
        wins = row["wins"] + (1 if won else 0)
        best_time = row["best_time"]
        best_mode = row["best_mode"]
        time_s = summary.get("time_s")
        if summary.get("finished") and time_s:
            if best_time is None or time_s < best_time:
                best_time = time_s
                best_mode = summary.get("mode")
        conn.execute(
            """
            UPDATE players
            SET points = ?, races = ?, wins = ?, last_mode = ?, best_time = ?, best_mode = ?
            WHERE name = ?
            """,
            (total, races, wins, summary.get("mode"), best_time, best_mode, player_name),
        )
        conn.execute(
            """
            INSERT INTO results
                (player, mode, vehicle_id, match_type, opponent, time_s, points, won, detail, created)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                player_name,
                summary.get("mode"),
                summary.get("vehicle_id"),
                summary.get("match_type"),
                summary.get("opponent"),
                time_s,
                int(points),
                1 if won else 0,
                json.dumps(summary, default=str),
                utc_now().isoformat(timespec="seconds"),
            ),
        )
    return {"ok": True, "points": int(points), "total": total, "wins": wins, "races": races}


def record_duel_time(duel_id: str, player_name: str, time_s: float) -> dict | None:
    """Store a human finish time. Returns the opponent row if they already posted."""
    if not duel_id:
        return None
    init()
    with _LOCK, _connect() as conn:
        conn.execute("INSERT OR IGNORE INTO duels (id, created) VALUES (?, ?)", (duel_id, utc_now().isoformat()))
        conn.execute(
            """
            INSERT INTO duel_times (duel_id, player, time_s, h2h_paid)
            VALUES (?, ?, ?, 0)
            ON CONFLICT(duel_id, player) DO UPDATE SET time_s = excluded.time_s
            """,
            (duel_id, player_name, time_s),
        )
        other = conn.execute(
            "SELECT player, time_s, h2h_paid FROM duel_times WHERE duel_id = ? AND player != ?",
            (duel_id, player_name),
        ).fetchone()
    return dict(other) if other else None


def mark_h2h_paid(duel_id: str, player_name: str) -> None:
    with _LOCK, _connect() as conn:
        conn.execute(
            "UPDATE duel_times SET h2h_paid = 1 WHERE duel_id = ? AND player = ?",
            (duel_id, player_name),
        )


def settle_duel(duel_id: str) -> dict:
    """Pay the faster human once. A tie pays nobody. Safe to call twice."""
    if not duel_id:
        return {"ready": False, "drivers": []}
    init()
    with _LOCK, _connect() as conn:
        rows = conn.execute(
            "SELECT player, time_s, h2h_paid FROM duel_times WHERE duel_id = ?",
            (duel_id,),
        ).fetchall()
        if len(rows) < 2:
            return {"ready": False, "drivers": [dict(row) for row in rows]}
        ordered = sorted(rows, key=lambda row: row["time_s"])
        tied = ordered[0]["time_s"] == ordered[1]["time_s"]
        winner = None if tied else ordered[0]["player"]
        paid_now = []
        for row in rows:
            won = row["player"] == winner
            if not row["h2h_paid"] and won:
                conn.execute(
                    "UPDATE players SET points = points + 25, wins = wins + 1 WHERE name = ?",
                    (row["player"],),
                )
                paid_now.append(row["player"])
            conn.execute(
                "UPDATE duel_times SET h2h_paid = 1 WHERE duel_id = ? AND player = ?",
                (duel_id, row["player"]),
            )
        drivers = [
            dict(row)
            for row in conn.execute(
                "SELECT player, time_s, h2h_paid FROM duel_times WHERE duel_id = ?",
                (duel_id,),
            )
        ]
    return {"ready": True, "winner": winner, "tied": tied, "paid_now": paid_now, "drivers": drivers}


def h2h_status(duel_id: str, player_name: str) -> dict:
    init()
    with _LOCK, _connect() as conn:
        mine = conn.execute(
            "SELECT time_s, h2h_paid FROM duel_times WHERE duel_id = ? AND player = ?",
            (duel_id, player_name),
        ).fetchone()
        other = conn.execute(
            "SELECT player, time_s FROM duel_times WHERE duel_id = ? AND player != ?",
            (duel_id, player_name),
        ).fetchone()
    return {
        "your_time": None if mine is None else mine["time_s"],
        "paid": False if mine is None else bool(mine["h2h_paid"]),
        "opponent": None if other is None else other["player"],
        "opponent_time": None if other is None else other["time_s"],
    }


def add_points(name: str, points: int, won: bool) -> dict:
    """Used when a head-to-head bonus lands after the clerk has already posted."""
    if points <= 0 and not won:
        row = player(name) or {"points": 0, "wins": 0}
        return {"points": row["points"], "wins": row["wins"]}
    with _LOCK, _connect() as conn:
        row = dict(_player_row(conn, name))
        total = row["points"] + int(points)
        wins = row["wins"] + (1 if won else 0)
        conn.execute("UPDATE players SET points = ?, wins = ? WHERE name = ?", (total, wins, name))
    return {"points": total, "wins": wins}


def recent_results(name: str, limit: int = 5) -> list[dict]:
    init()
    with _LOCK, _connect() as conn:
        rows = conn.execute(
            """
            SELECT mode, vehicle_id, match_type, opponent, time_s, points, won, created
            FROM results WHERE player = ? ORDER BY id DESC LIMIT ?
            """,
            (name, limit),
        ).fetchall()
    return [dict(row) for row in rows]


def log_run(kind: str, player_name: str, usage: dict, trace: list) -> None:
    init()
    with _LOCK, _connect() as conn:
        conn.execute(
            """
            INSERT INTO agent_runs
                (kind, player, input_tokens, output_tokens, cache_read, cache_write, cost_usd, trace, created)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                kind,
                player_name,
                int(usage.get("input_tokens") or 0),
                int(usage.get("output_tokens") or 0),
                int(usage.get("cache_read_tokens") or 0),
                int(usage.get("cache_write_tokens") or 0),
                float(usage.get("cost_usd") or 0),
                json.dumps(trace, default=str)[:20000],
                utc_now().isoformat(timespec="seconds"),
            ),
        )


def log_block(player_name: str, field: str, reason: str, snippet: str) -> None:
    init()
    with _LOCK, _connect() as conn:
        conn.execute(
            "INSERT INTO blocks (player, field, reason, snippet, created) VALUES (?, ?, ?, ?, ?)",
            (player_name, field, reason, snippet[:120], utc_now().isoformat(timespec="seconds")),
        )


def budget_today() -> dict:
    init()
    today = utc_day()
    start = today + "T00:00:00"
    with _LOCK, _connect() as conn:
        row = conn.execute(
            """
            SELECT
                COALESCE(SUM(input_tokens + output_tokens), 0) AS tokens,
                COUNT(*) AS runs,
                COALESCE(SUM(cost_usd), 0) AS cost
            FROM agent_runs
            WHERE created >= ?
            """,
            (start,),
        ).fetchone()
    return {
        "day": today,
        "tokens": int(row["tokens"]),
        "runs": int(row["runs"]),
        "cost_usd": round(float(row["cost"]), 6),
    }


def recent_runs(limit: int = 8) -> list[dict]:
    init()
    with _LOCK, _connect() as conn:
        rows = conn.execute(
            """
            SELECT kind, player, input_tokens, output_tokens, cache_read, cache_write, cost_usd, created
            FROM agent_runs ORDER BY id DESC LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]


def recent_blocks(limit: int = 8) -> list[dict]:
    init()
    with _LOCK, _connect() as conn:
        rows = conn.execute(
            "SELECT player, field, reason, snippet, created FROM blocks ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]


def save_impression(session_id: str, ad_id: str, placement: str, mode: str) -> dict:
    """Count one view of a sponsor the server already chose."""
    from club import ads as catalog

    ad = next((item for item in catalog() if item["id"] == ad_id), None)
    if ad is None:
        return {}
    init()
    moment = utc_now()
    cutoff = (moment - timedelta(seconds=60)).isoformat(timespec="seconds")
    with _LOCK, _connect() as conn:
        recent = conn.execute(
            """
            SELECT id FROM impressions
            WHERE session_id = ? AND placement = ? AND ad_id = ? AND created >= ?
            LIMIT 1
            """,
            (session_id or "guest", placement, ad_id, cutoff),
        ).fetchone()
        counted = recent is None
        if counted:
            conn.execute(
                "INSERT INTO impressions (session_id, ad_id, placement, mode, created) VALUES (?, ?, ?, ?, ?)",
                (session_id or "guest", ad_id, placement, mode, moment.isoformat(timespec="seconds")),
            )
    public = {key: ad[key] for key in ("id", "sponsor", "line", "cpm")}
    return {**public, "counted": counted}


def record_impression(session_id: str, mode: str, placement: str) -> dict | None:
    """One counted view per session, placement, and minute. Copy comes from the catalog."""
    init()
    moment = utc_now()
    cutoff = (moment - timedelta(seconds=60)).isoformat(timespec="seconds")
    with _LOCK, _connect() as conn:
        recent = conn.execute(
            """
            SELECT ad_id FROM impressions
            WHERE session_id = ? AND placement = ? AND created >= ?
            ORDER BY id DESC LIMIT 1
            """,
            (session_id or "guest", placement, cutoff),
        ).fetchone()
        counts = {
            row["ad_id"]: row["n"]
            for row in conn.execute("SELECT ad_id, COUNT(*) AS n FROM impressions GROUP BY ad_id")
        }
        chosen = choose_ad(mode, placement, counts)
        if recent and recent["ad_id"] == chosen["id"]:
            return {**chosen, "counted": False}
        conn.execute(
            "INSERT INTO impressions (session_id, ad_id, placement, mode, created) VALUES (?, ?, ?, ?, ?)",
            (session_id or "guest", chosen["id"], placement, mode, moment.isoformat(timespec="seconds")),
        )
    return {**chosen, "counted": True}


def ledger() -> dict:
    init()
    with _LOCK, _connect() as conn:
        rows = conn.execute(
            """
            SELECT ad_id, placement, COUNT(*) AS impressions
            FROM impressions
            GROUP BY ad_id, placement
            ORDER BY impressions DESC
            """
        ).fetchall()
        total = conn.execute("SELECT COUNT(*) AS n FROM impressions").fetchone()["n"]
    from club import ads as catalog

    by_id = {ad["id"]: ad for ad in catalog()}
    lines = []
    revenue = 0.0
    for row in rows:
        ad = by_id.get(row["ad_id"])
        if not ad:
            continue
        dollars = row["impressions"] * ad["cpm"] / 1000
        revenue += dollars
        lines.append(
            {
                "ad_id": ad["id"],
                "sponsor": ad["sponsor"],
                "placement": row["placement"],
                "impressions": row["impressions"],
                "cpm": ad["cpm"],
                "revenue_usd": round(dollars, 4),
            }
        )
    return {"impressions": total, "revenue_usd": round(revenue, 4), "lines": lines}
