"""Lane League API. The site and the agents live on one process."""

from __future__ import annotations

import os
import random
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from agents import Bag, close_with_clerk, prepare_race
from club import (
    DAILY_RUN_CAP,
    DAILY_TOKEN_CAP,
    DRIVER_DAILY_MINUTES,
    PRICE_CACHE_READ,
    PRICE_CACHE_WRITE,
    PRICE_INPUT,
    PRICE_OUTPUT,
    TIME_LIMIT,
    WEATHER,
    build_track,
    inspect_text,
    normalize_name,
    official_points,
    recount,
    score_minute,
    vehicle_by_id,
    vehicles,
)
from models import CloseIn, DrillIn, PrepareIn
import store

FRONTEND = HERE.parent / "frontend"
app = FastAPI(title="Lane League")


def _fresh(created: str) -> bool:
    try:
        moment = datetime.fromisoformat(created)
    except ValueError:
        return False
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - moment < timedelta(minutes=20)


def _blocked(name: str, field: str, scan: dict) -> dict:
    store.log_block(name, field, scan["reason"], scan.get("text", ""))
    return {"ok": False, "blocked": True, "reason": scan["reason"], "agent_called": False, "trace": []}


@app.get("/api/health")
def health():
    return {"ok": True, "model": "gpt-6-luna", "has_key": bool(os.getenv("PORTKEY_API_KEY", "").strip())}


@app.get("/api/catalog")
def catalog():
    return {"vehicles": vehicles()}


@app.get("/api/league")
def league():
    return {"standings": store.standings(30)}


@app.get("/api/budget")
def budget(name: str = ""):
    """What the crew has spent today, per ranked minute, against the caps."""
    cleaned = normalize_name(name)
    return {
        "today": store.budget_today(),
        "token_cap": DAILY_TOKEN_CAP,
        "run_cap": DAILY_RUN_CAP,
        "driver_cap": DRIVER_DAILY_MINUTES,
        "driver_minutes_today": store.minutes_today(cleaned) if cleaned else 0,
        "minutes": store.minute_ledger(10),
        "blocks": store.recent_blocks(5),
        "prices_per_million": {
            "model": "gpt-6-luna",
            "input": PRICE_INPUT * 1_000_000,
            "cache_read": PRICE_CACHE_READ * 1_000_000,
            "cache_write": PRICE_CACHE_WRITE * 1_000_000,
            "output": PRICE_OUTPUT * 1_000_000,
        },
    }


@app.post("/api/safety/drill")
def safety_drill(body: DrillIn):
    field = body.field if body.field in {"name", "brief"} else "brief"
    result = inspect_text(body.text, field)
    if not result["allowed"]:
        store.log_block(body.name or "drill", field, result["reason"], result.get("text", ""))
    return {"firewall": result, "agent_called": False, "points_moved": False}


@app.post("/api/safety/malfunction")
def safety_malfunction():
    shown = official_points(999999, 0)
    return {"defense": "A clerk's number cannot change the official points.", "agent_called": False, "points_moved": False, **shown}


@app.post("/api/race/prepare")
async def race_prepare(body: PrepareIn):
    name = normalize_name(body.name)
    scan = inspect_text(name, "name")
    if not scan["allowed"]:
        return _blocked(name, "name", scan)
    vehicle = vehicle_by_id(body.vehicle_id)
    if vehicle is None:
        raise HTTPException(status_code=400, detail="Pick a car from the garage.")
    note = " ".join((body.note or "").split())
    if note:
        note_scan = inspect_text(note, "brief")
        if not note_scan["allowed"]:
            return _blocked(name, "brief", note_scan)
    store.ensure_player(name)
    seed = random.randint(1, 9_999_999)
    race_id = uuid.uuid4().hex[:12]
    track = build_track(seed)
    bag = Bag(trace=[], player=name, race_id=race_id, vehicle_id=vehicle["id"], seed=seed, track=track, note=note)
    outcome = await prepare_race(bag)
    store.save_open_race({"id": race_id, "player": name, "vehicle_id": vehicle["id"], "seed": seed, "crew": outcome["crew"]})
    return {
        "ok": True,
        "blocked": False,
        "race_id": race_id,
        "vehicle": vehicle,
        "track": track,
        "modifiers": WEATHER[track["weather"]],
        "card": {
            **outcome["plan"],
            "track_name": track["name"],
            "weather": track["weather"],
        },
        "crew": outcome["crew"],
        "corrections": bag.corrections,
        "trace": bag.trace,
        "usage": outcome["usage"],
        "seconds": outcome.get("seconds"),
        "agent_called": outcome["crew"],
    }


@app.post("/api/reset/close")
async def reset_close(body: CloseIn):
    """Recount the minute from its trail, post that number, then let the clerk speak."""
    name = normalize_name(body.name)
    scan = inspect_text(name, "name")
    if not scan["allowed"]:
        return _blocked(name, "name", scan)
    row = store.open_race(body.race_id)
    if row is None or row["player"] != name:
        raise HTTPException(status_code=404, detail="That minute is closed.")
    if row["consumed"]:
        raise HTTPException(status_code=409, detail="That minute was already posted.")
    if not _fresh(row["created"]):
        raise HTTPException(status_code=409, detail="That minute expired. Start another.")
    if not store.consume_race(body.race_id):
        raise HTTPException(status_code=409, detail="That minute was already posted.")

    vehicle = vehicle_by_id(row["vehicle_id"])
    track = build_track(int(row["seed"]))
    counted = recount(body.samples, track, vehicle)
    opened = datetime.fromisoformat(row["created"])
    waited = (datetime.now(timezone.utc) - opened).total_seconds()
    if counted["ok"] and counted["time_s"] is not None and counted["time_s"] > waited + 5:
        counted = {**recount([], track, vehicle), "reason": "The trail claimed more driving time than had passed."}
    scored = score_minute(counted["coins"], counted["hurdles"], counted["finished"], counted["time_left"])
    official = {**counted, **scored, "ended_early": bool(body.ended_early)}
    claimed = {"coins": max(0, int(body.claimed_coins)), "hurdles": max(0, int(body.claimed_hurdles))}
    mismatch = claimed["coins"] != scored["coins"] or claimed["hurdles"] != scored["hurdles"]
    car = f"{vehicle['brand']} {vehicle['model']}"
    summary = {
        "race_id": row["id"],
        "vehicle_id": row["vehicle_id"],
        "car": car,
        "coins": scored["coins"],
        "hurdles": scored["hurdles"],
        "finished": scored["finished"],
        "time_s": counted["time_s"] if scored["finished"] else None,
        "trail_ok": counted["ok"],
    }
    bag = Bag(
        trace=[],
        player=name,
        race_id=row["id"],
        vehicle_id=row["vehicle_id"],
        seed=int(row["seed"]),
        track=track,
        official=official,
        summary=summary,
        history=store.recent_results(name, 5),
    )
    if not counted["ok"]:
        bag.corrections.append(f"The trail failed the server's check: {counted['reason']} The board kept 0.")
    elif mismatch:
        bag.corrections.append(
            f"The browser said {claimed['coins']} coins and {claimed['hurdles']} hurdles. "
            f"The trail shows {scored['coins']} and {scored['hurdles']}. The board kept the trail's count."
        )
    note = await close_with_clerk(bag, crew=bool(row.get("crew", 1)))
    me = store.player(name) or {"points": 0}
    return {
        "ok": True,
        "blocked": False,
        "scored": scored,
        "recount": counted,
        "claimed": claimed,
        "clerk": {key: note[key] for key in ("headline", "body", "tip", "rank_line", "points", "fallback", "corrected", "model_said")},
        "usage": note["usage"],
        "seconds": note.get("seconds"),
        "trace": bag.trace,
        "corrections": bag.corrections,
        "rank": store.rank_of(name),
        "total": me["points"],
        "standings": store.standings(30),
        "agent_called": not note["fallback"],
        "vehicle": {"brand": vehicle["brand"], "model": vehicle["model"]},
        "time_limit": TIME_LIMIT,
    }


@app.get("/")
def index():
    return FileResponse(FRONTEND / "index.html")


app.mount("/static", StaticFiles(directory=FRONTEND), name="static")
