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

from dotenv import load_dotenv

ROOT = HERE.parent
load_dotenv(ROOT / ".env")
load_dotenv(ROOT.parent / ".env", override=not os.getenv("PORTKEY_API_KEY", "").strip())
os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from agents import Bag, close_with_clerk, empty_usage, prepare_race
from club import (
    DAILY_RUN_CAP,
    DAILY_TOKEN_CAP,
    PRICE_CACHE_READ,
    PRICE_CACHE_WRITE,
    PRICE_INPUT,
    PRICE_OUTPUT,
    TEMPERAMENT,
    WEATHER,
    build_track,
    inspect_text,
    judge_lap,
    launch_pad,
    normalize_name,
    official_points,
    projection,
    vehicle_by_id,
    vehicles,
)
from lobby import Lobby
from models import CheckIn, DrillIn, FinishIn, JoinIn, PrepareIn, ResetClose, ScoreIn, StateIn
import store

FRONTEND = ROOT / "frontend"
app = FastAPI(title="Lane League")
lobby = Lobby()


def _age_ok(created: str) -> bool:
    try:
        moment = datetime.fromisoformat(created)
    except ValueError:
        return False
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - moment < timedelta(minutes=20)


@app.get("/api/health")
def health():
    return {
        "ok": True,
        "model": "gpt-6-luna",
        "has_key": bool(os.getenv("PORTKEY_API_KEY", "").strip()),
    }


@app.get("/api/catalog")
def catalog():
    rows = vehicles()
    return {
        "vehicles": rows,
        "modes": [
            {
                "id": "supercar",
                "title": "Supercar",
                "kicker": "The fast road",
                "text": "Sixteen cars. Pace, launch, brakes, steering, grip, and width are different on each one.",
            },
        ],
    }


@app.get("/api/league")
def league():
    return {"standings": store.standings(30)}


@app.post("/api/score")
def post_score(body: ScoreIn):
    checked = inspect_text(body.name, "name")
    if not checked["allowed"]:
        raise HTTPException(status_code=400, detail=checked["reason"])
    points = max(0, min(5000, int(body.points)))
    saved = store.post_result(
        checked["text"],
        points,
        bool(body.finished),
        {
            "mode": "supercar",
            "vehicle_id": (body.vehicle_id or "")[:48],
            "match_type": "solo",
            "time_s": float(body.time_s) if body.finished else None,
            "finished": bool(body.finished),
            "coins": max(0, int(body.coins)),
            "hurdles": max(0, int(body.hurdles)),
        },
    )
    return {**saved, "rank": store.rank_of(checked["text"]), "standings": store.standings(30)}


@app.get("/api/ledger")
def ledger():
    board = store.ledger()
    board["projection_100"] = projection(100)
    board["projection_10000"] = projection(10_000)
    return board


@app.get("/api/me")
def me(name: str = ""):
    cleaned = normalize_name(name)
    if not cleaned:
        return {"player": None, "rank": None, "recent": []}
    row = store.player(cleaned)
    if row is None:
        return {"player": None, "rank": None, "recent": []}
    return {"player": row, "rank": store.rank_of(cleaned), "recent": store.recent_results(cleaned)}


@app.get("/api/ad")
def ad(placement: str = "clubhouse", mode: str = "supercar", session_id: str = ""):
    if placement not in {"clubhouse", "garage", "results", "checkin"}:
        placement = "clubhouse"
    if mode not in {"supercar", "plane", "bus"}:
        mode = "supercar"
    chosen = store.record_impression(session_id, mode, placement)
    return chosen or {}


@app.get("/api/office")
def office():
    budget = store.budget_today()
    return {
        "budget": budget,
        "token_cap": DAILY_TOKEN_CAP,
        "run_cap": DAILY_RUN_CAP,
        "runs": store.recent_runs(12),
        "blocks": store.recent_blocks(12),
        "prices": {
            "model": "gpt-6-luna",
            "input_per_million": PRICE_INPUT * 1_000_000,
            "cache_read_per_million": PRICE_CACHE_READ * 1_000_000,
            "cache_write_per_million": PRICE_CACHE_WRITE * 1_000_000,
            "output_per_million": PRICE_OUTPUT * 1_000_000,
        },
    }


@app.post("/api/safety/drill")
def safety_drill(body: DrillIn):
    field = body.field if body.field in {"name", "brief", "any"} else "name"
    result = inspect_text(body.text, field)
    if not result["allowed"]:
        store.log_block(body.name or "drill", field, result["reason"], result.get("text", ""))
    return {"firewall": result, "agent_called": False, "points_moved": False}


@app.post("/api/safety/malfunction")
def safety_malfunction():
    shown = official_points(999999, 0)
    return {
        "defense": "A clerk note cannot change the official points.",
        "agent_called": False,
        "points_moved": False,
        **shown,
    }


@app.post("/api/lobby/join")
def lobby_join(body: JoinIn):
    name = normalize_name(body.name)
    scan = inspect_text(name, "name")
    if not scan["allowed"]:
        store.log_block(name, "name", scan["reason"], scan.get("text", ""))
        return {"ok": False, "blocked": True, "reason": scan["reason"]}
    vehicle = vehicle_by_id(body.vehicle_id)
    if vehicle is None:
        raise HTTPException(status_code=400, detail="Pick a machine from the garage.")
    waiter = lobby.join(name, vehicle["id"], vehicle["mode"])
    return _waiter_payload(waiter)


@app.get("/api/lobby/{waiter_id}")
def lobby_get(waiter_id: str):
    waiter = lobby.get(waiter_id)
    if waiter is None:
        return {"ok": False, "status": "expired"}
    return _waiter_payload(waiter)


def _waiter_payload(waiter) -> dict:
    return {
        "ok": True,
        "waiter_id": waiter.id,
        "status": waiter.status,
        "match_id": waiter.match_id,
        "opponent": waiter.opponent,
        "mode": waiter.mode,
    }


@app.post("/api/race/prepare")
async def race_prepare(body: PrepareIn):
    name = normalize_name(body.name)
    scan = inspect_text(name, "name")
    if not scan["allowed"]:
        store.log_block(name, "name", scan["reason"], scan.get("text", ""))
        return {"ok": False, "blocked": True, "reason": scan["reason"], "trace": [], "agent_called": False}
    vehicle = vehicle_by_id(body.vehicle_id)
    if vehicle is None:
        raise HTTPException(status_code=400, detail="Pick a machine from the garage.")
    temperament = body.temperament if body.temperament in TEMPERAMENT else "local"
    match = "human" if body.match == "human" else "ai"
    seed = random.randint(1, 9_999_999)
    opponent = ""
    duel_id = ""
    if match == "human":
        found = lobby.match_for(body.waiter_id, name)
        if found is None or found.mode != vehicle["mode"]:
            return {
                "ok": False,
                "blocked": False,
                "reason": "No driver is waiting in this machine. Race the rival, or open the grid again.",
            }
        seed = found.seed
        opponent = next(person for person in found.players if person.casefold() != name.casefold())
        duel_id = found.id
    note = " ".join((body.note or "").split())
    if note:
        note_scan = inspect_text(note, "brief")
        if not note_scan["allowed"]:
            store.log_block(name, "brief", note_scan["reason"], note_scan.get("text", ""))
            return {
                "ok": False,
                "blocked": True,
                "reason": note_scan["reason"],
                "trace": [],
                "agent_called": False,
            }
    store.ensure_player(name)
    track = build_track(vehicle["mode"], seed)
    bag = Bag(
        trace=[],
        player=name,
        vehicle_id=vehicle["id"],
        mode=vehicle["mode"],
        seed=seed,
        match=match,
        opponent=opponent,
        temperament=temperament,
        track=track,
        note=note,
    )
    outcome = await prepare_race(bag)
    if outcome.get("blocked"):
        return {
            "ok": False,
            "blocked": True,
            "reason": outcome.get("reason") or "The steward stopped this grid.",
            "trace": bag.trace,
            "usage": outcome.get("usage"),
            "agent_called": True,
        }
    plan = outcome["plan"]
    race_id = uuid.uuid4().hex[:12]
    store.save_open_race(
        {
            "id": race_id,
            "player": name,
            "vehicle_id": vehicle["id"],
            "mode": vehicle["mode"],
            "seed": seed,
            "match_type": match,
            "opponent": opponent or plan["rival_name"],
            "duel_id": duel_id,
            "rival_cruise": plan["rival_cruise_mps"],
        }
    )
    weather = track["weather"]
    return {
        "ok": True,
        "blocked": False,
        "race_id": race_id,
        "duel_id": duel_id,
        "vehicle": vehicle,
        "track": track,
        "modifiers": WEATHER[weather],
        "card": {
            "coach_note": plan["coach_note"],
            "radio_open": plan["radio_open"],
            "rival_name": plan["rival_name"],
            "rival_skill": plan["rival_skill"],
            "mistake_rate": plan["mistake_rate"],
            "line": plan["line"],
            "rival_cruise_mps": plan["rival_cruise_mps"],
            "match": match,
            "opponent": opponent or plan["rival_name"],
            "temperament": temperament,
            "temperament_label": TEMPERAMENT[temperament]["label"],
            "weather": weather,
            "track_name": track["name"],
            "fallback": plan["fallback"],
        },
        "corrections": bag.corrections,
        "trace": bag.trace,
        "usage": outcome["usage"],
    }


@app.post("/api/race/{race_id}/state")
def race_state(race_id: str, body: StateIn):
    row = store.open_race(race_id)
    if row is None or not row.get("duel_id"):
        return {"opponent": None}
    name = normalize_name(body.name)
    if name.casefold() != row["player"].casefold():
        raise HTTPException(status_code=403, detail="That grid belongs to someone else.")
    opponent = lobby.post_state(
        row["duel_id"],
        name,
        {"seg": body.seg, "x": body.x, "alt": body.alt, "speed": body.speed, "finished": body.finished},
    )
    return {"opponent": opponent}


@app.post("/api/reset/close")
async def reset_close(body: ResetClose):
    """Post the one-minute score the server recomputed, then let the clerk speak."""
    name = normalize_name(body.name)
    scan = inspect_text(name, "name")
    if not scan["allowed"]:
        store.log_block(name, "name", scan["reason"], scan.get("text", ""))
        return {"ok": False, "blocked": True, "reason": scan["reason"], "agent_called": False, "trace": []}
    row = store.open_race(body.race_id)
    if row is None or row["player"] != name:
        raise HTTPException(status_code=404, detail="That minute is closed.")
    if row["consumed"]:
        raise HTTPException(status_code=409, detail="That minute was already posted.")
    if not _age_ok(row["created"]):
        raise HTTPException(status_code=409, detail="That minute expired. Start another.")
    if not store.consume_race(body.race_id):
        raise HTTPException(status_code=409, detail="That minute was already posted.")
    scored = launch_pad(body.coins, body.hurdles, body.finished and not body.ended_early, body.time_left)
    vehicle = vehicle_by_id(row["vehicle_id"])
    summary = {
        "mode": row["mode"],
        "vehicle_id": row["vehicle_id"],
        "match_type": "reset",
        "opponent": row["opponent"],
        "time_s": None if body.ended_early or not scored["finished"] else round(60 - scored["time_left"], 2),
        "finished": scored["finished"],
        "coins": scored["coins"],
        "hurdles": scored["hurdles"],
        "ended_early": bool(body.ended_early),
    }
    bag = Bag(
        trace=[],
        player=name,
        vehicle_id=row["vehicle_id"],
        mode=row["mode"],
        match=row["match_type"],
        opponent=row["opponent"] or "",
        task="race",
        official={
            "points": scored["points"],
            "finished": scored["finished"],
            "beat": scored["beat"],
            "time_s": summary["time_s"],
        },
        summary=summary,
    )
    note = await close_with_clerk(bag)
    me_row = store.player(name)
    return {
        "ok": True,
        "blocked": False,
        "scored": scored,
        "clerk": {
            "headline": note["headline"],
            "body": note["body"],
            "rank_line": note["rank_line"],
            "points": note["points"],
            "fallback": note["fallback"],
            "corrected": note["corrected"],
            "model_said": note["model_said"],
        },
        "usage": note["usage"],
        "trace": bag.trace,
        "corrections": bag.corrections,
        "rank": store.rank_of(name),
        "total": me_row["points"] if me_row else 0,
        "standings": store.standings(30),
        "agent_called": not note["fallback"],
        "vehicle": {"brand": vehicle["brand"], "model": vehicle["model"]} if vehicle else {},
    }


@app.post("/api/race/finish")
async def race_finish(body: FinishIn):
    name = normalize_name(body.name)
    row = store.open_race(body.race_id)
    if row is None or row["player"] != name:
        raise HTTPException(status_code=404, detail="That grid is closed.")
    if row["consumed"]:
        raise HTTPException(status_code=409, detail="That lap was already posted.")
    if not _age_ok(row["created"]):
        raise HTTPException(status_code=409, detail="That grid expired. Start another.")
    if not store.consume_race(body.race_id):
        raise HTTPException(status_code=409, detail="That lap was already posted.")
    vehicle = vehicle_by_id(row["vehicle_id"])
    track = build_track(row["mode"], int(row["seed"]))
    judged = judge_lap(body.samples, vehicle, track, float(row["rival_cruise"] or 0), row["match_type"])
    summary = {
        "mode": row["mode"],
        "vehicle_id": row["vehicle_id"],
        "match_type": row["match_type"],
        "opponent": row["opponent"],
        "time_s": judged["time_s"],
        "finished": judged["finished"],
        "breakdown": judged["breakdown"],
        "gates_hit": judged["gates_hit"],
        "gates_total": judged["gates_total"],
    }
    bag = Bag(
        trace=[],
        player=name,
        vehicle_id=row["vehicle_id"],
        mode=row["mode"],
        match=row["match_type"],
        opponent=row["opponent"] or "",
        task="race",
        official=judged,
        summary=summary,
    )
    note = await close_with_clerk(bag)
    h2h = {"ready": False, "you_won": False, "tied": False}
    if row["match_type"] == "human" and judged.get("finished") and row.get("duel_id"):
        store.record_duel_time(row["duel_id"], name, float(judged["time_s"]))
        settled = store.settle_duel(row["duel_id"])
        h2h = {
            "ready": settled.get("ready", False),
            "you_won": settled.get("winner") == name,
            "tied": settled.get("tied", False),
        }
    ad = note.get("ad") or {}
    if ad.get("id"):
        ad = store.save_impression(body.session_id, ad["id"], "results", row["mode"])
    me_row = store.player(name)
    return {
        "ok": True,
        "judged": judged,
        "clerk": {
            "headline": note["headline"],
            "body": note["body"],
            "rank_line": note["rank_line"],
            "points": note["points"],
            "fallback": note["fallback"],
            "corrected": note["corrected"],
            "model_said": note["model_said"],
        },
        "ad": ad,
        "usage": note["usage"],
        "trace": bag.trace,
        "corrections": bag.corrections,
        "rank": store.rank_of(name),
        "total": me_row["points"] if me_row else 0,
        "h2h": h2h,
        "vehicle": {"brand": vehicle["brand"], "model": vehicle["model"], "mode": vehicle["mode"], "color": vehicle["color"]},
    }


@app.get("/api/duel/{duel_id}")
def duel_poll(duel_id: str, name: str):
    settled = store.settle_duel(duel_id)
    cleaned = normalize_name(name)
    me_row = store.player(cleaned)
    return {
        "ready": settled.get("ready", False),
        "you_won": settled.get("winner") == cleaned,
        "tied": bool(settled.get("tied")),
        "total": me_row["points"] if me_row else 0,
        "rank": store.rank_of(cleaned),
    }


@app.post("/api/checkin")
async def check_in(body: CheckIn):
    name = normalize_name(body.name)
    scan = inspect_text(name, "name")
    if not scan["allowed"]:
        store.log_block(name, "name", scan["reason"], scan.get("text", ""))
        return {"ok": False, "blocked": True, "reason": scan["reason"], "agent_called": False, "trace": []}
    store.ensure_player(name)
    row = store.player(name)
    if row and row.get("checkin_day") == store.utc_day():
        return {
            "ok": True,
            "blocked": False,
            "already": True,
            "points": 0,
            "headline": "Already on today's sheet.",
            "body": "One check-in per club day. The day turns at midnight UTC.",
            "rank_line": "",
            "ad": None,
            "usage": empty_usage(),
            "trace": [],
            "corrections": ["A second check-in did not call the model."],
            "player": row,
            "rank": store.rank_of(name),
            "agent_called": False,
        }
    bag = Bag(
        trace=[],
        player=name,
        mode=row.get("last_mode") or "supercar",
        task="checkin",
    )
    note = await close_with_clerk(bag)
    ad = note.get("ad") or {}
    if ad.get("id"):
        ad = store.save_impression(body.session_id, ad["id"], "checkin", bag.mode or "supercar")
    me_row = store.player(name)
    return {
        "ok": True,
        "blocked": False,
        "already": False,
        "points": note["points"],
        "headline": note["headline"],
        "body": note["body"],
        "rank_line": note["rank_line"],
        "ad": ad,
        "usage": note["usage"],
        "trace": bag.trace,
        "corrections": bag.corrections,
        "player": me_row,
        "rank": store.rank_of(name),
        "corrected": note["corrected"],
        "agent_called": not note["fallback"],
    }


@app.get("/")
def index():
    return FileResponse(FRONTEND / "index.html")


app.mount("/static", StaticFiles(directory=FRONTEND), name="static")
