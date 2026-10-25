"""Catalog, track, scoring, ads, and the checks that do not trust the model."""

from __future__ import annotations

import json
import math
import random
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"

SEGMENT_COUNT = 120
SEGMENT_M = 16
TRACK_LENGTH_M = SEGMENT_COUNT * SEGMENT_M

# GPT-6 Luna list prices for a normal-length prompt, in USD per token.
# Source: genai-prices snapshot shipped with the course tools.
# input_tokens already include cached tokens, so uncached input is the remainder.
PRICE_INPUT = 0.10 / 1_000_000
PRICE_CACHE_READ = 0.01 / 1_000_000
PRICE_CACHE_WRITE = 0.125 / 1_000_000
PRICE_OUTPUT = 0.50 / 1_000_000

DAILY_TOKEN_CAP = 80_000
DAILY_RUN_CAP = 40

TRACK_NAMES = {
    "supercar": ["Paper Street", "Harbor Hairpin", "The Cut"],
    "plane": ["North Ramp", "Coast Corridor", "Stack Four"],
    "bus": ["Route 12", "Market Loop", "School Hill"],
}

WEATHER = {
    "Clear": {"grip": 1.0, "accel": 1.0, "wind": 0.0, "pace": 1.0, "glare": 0},
    "Wet": {"grip": 0.8, "accel": 0.88, "wind": 0.0, "pace": 0.94, "glare": 0},
    "Crosswind": {"grip": 1.0, "accel": 1.0, "wind": 0.55, "pace": 0.97, "glare": 0},
    "Low sun": {"grip": 1.0, "accel": 1.0, "wind": 0.05, "pace": 1.0, "glare": 1},
}

TEMPERAMENT = {
    "professor": {"skill": -0.1, "mistake": 0.06, "label": "The Professor"},
    "local": {"skill": 0.0, "mistake": 0.12, "label": "Quiet company"},
    "wildcard": {"skill": 0.08, "mistake": 0.28, "label": "The Wildcard"},
}

RIVAL_NAMES = {
    "supercar": {"professor": "Gray Lecture", "local": "Quiet Seat", "wildcard": "Red Anon"},
    "plane": {"professor": "Left Seat", "local": "Tower", "wildcard": "Short Final"},
    "bus": {"professor": "Route Captain", "local": "The Regular", "wildcard": "Last Stop"},
}

HOUSE_DRIVERS = [
    ("Noor Hale", 520, 9, 6, "supercar"),
    ("Camille Ortiz", 410, 8, 4, "plane"),
    ("Jonah Pell", 360, 8, 3, "bus"),
    ("Inez Calder", 300, 6, 3, "supercar"),
    ("Theo Marsh", 240, 5, 2, "plane"),
    ("Priya Sen", 190, 5, 1, "bus"),
    ("Ellis Ward", 120, 3, 1, "supercar"),
    ("Mara Voss", 70, 2, 0, "plane"),
]

INJECTION = re.compile(
    r"(ignore\s+(all\s+|any\s+|the\s+)?(previous|prior|above|instructions)|"
    r"system\s+prompt|developer\s+message|you\s+are\s+now|"
    r"<\s*/?\s*(system|instructions|prompt)|"
    r"api[_\s-]?key|jailbreak|do\s+anything\s+now|"
    r"set\s+(my\s+)?points|award\s+\d+|give\s+me\s+\d+\s+points|"
    r"reveal\s+(the\s+)?(prompt|instructions|key)|"
    r"change\s+the\s+ad|crypto|bitcoin)",
    re.I,
)

NAME_OK = re.compile(r"[A-Za-z][A-Za-z .'\-]{1,22}$")

MODE_COACH = {
    "supercar": "One minute. Put the day down. Steer into the coins and lift before a hurdle.",
    "plane": "Pitch toward the corridor early, and keep the speed above the stall.",
    "bus": "Turn in early. The body is wide and the wheel is slow, so a late flick puts you in the grass.",
}

WEATHER_COACH = {
    "Clear": "The road is dry.",
    "Wet": "It is wet, so the same wheel does less.",
    "Crosswind": "There is a crosswind. Hold a line instead of chasing every push.",
    "Low sun": "The sun is low. The gates are still where they were.",
}


def load_json(name: str) -> list[dict]:
    return json.loads((DATA / name).read_text(encoding="utf-8"))


def vehicles() -> list[dict]:
    return load_json("vehicles.json")


def ads() -> list[dict]:
    return load_json("ads.json")


def vehicle_by_id(vehicle_id: str) -> dict | None:
    for vehicle in vehicles():
        if vehicle["id"] == vehicle_id:
            return vehicle
    return None


def house_names() -> set[str]:
    return {name.casefold() for name, *_rest in HOUSE_DRIVERS}


def normalize_name(name: str) -> str:
    return " ".join(name.split())


def inspect_text(text: str, field: str = "any") -> dict:
    """Stop instructions-as-data before an agent is called."""
    raw = "" if text is None else str(text)
    cleaned = " ".join(raw.split())
    if INJECTION.search(cleaned):
        return {
            "allowed": False,
            "reason": "That text is an instruction to the stewards, so it stopped here. No agent was called and no points moved.",
            "text": cleaned[:80],
        }
    if len(cleaned) > 280:
        return {
            "allowed": False,
            "reason": "That note is too long to be a name or a radio line.",
            "text": cleaned[:80],
        }
    if field == "name":
        if not NAME_OK.fullmatch(cleaned):
            return {
                "allowed": False,
                "reason": "Use 2–23 letters for the name on the board.",
                "text": cleaned[:80],
            }
        if cleaned.casefold() in house_names():
            return {
                "allowed": False,
                "reason": "That name is already painted on the club wall.",
                "text": cleaned[:80],
            }
    return {"allowed": True, "reason": "", "text": cleaned}


def clean_copy(text: str, limit: int = 220) -> str:
    cleaned = re.sub(r"[\x00-\x1f]", " ", text or "")
    cleaned = " ".join(cleaned.split())
    if INJECTION.search(cleaned):
        return ""
    return cleaned[:limit]


def recommend_skill(races: int, wins: int, points: int) -> float:
    if races <= 0:
        return 0.52
    win_rate = wins / races
    form = 0.42 + win_rate * 0.28 + min(points, 500) / 1400
    return max(0.36, min(0.86, form))


def skill_band(driver: dict, temperament: str) -> dict:
    base = recommend_skill(int(driver.get("races") or 0), int(driver.get("wins") or 0), int(driver.get("points") or 0))
    shift = TEMPERAMENT[temperament]["skill"]
    recommended = max(0.34, min(0.9, base + shift))
    return {
        "recommended": round(recommended, 3),
        "low": round(max(0.34, recommended - 0.08), 3),
        "high": round(min(0.9, recommended + 0.08), 3),
    }


def clamp_skill(model_skill: float, band: dict) -> tuple[float, bool]:
    try:
        skill = float(model_skill)
    except (TypeError, ValueError):
        return band["recommended"], True
    clamped = min(band["high"], max(band["low"], skill))
    return round(clamped, 3), abs(clamped - skill) > 0.001


def clamp_mistake(model_mistake: float, temperament: str) -> tuple[float, bool]:
    target = TEMPERAMENT[temperament]["mistake"]
    low = max(0.02, target - 0.05)
    high = min(0.4, target + 0.08)
    try:
        mistake = float(model_mistake)
    except (TypeError, ValueError):
        return target, True
    clamped = min(high, max(low, mistake))
    return round(clamped, 3), abs(clamped - mistake) > 0.001


def rival_cruise(max_speed: float, skill: float, mistake: float, pace: float) -> float:
    skill = min(1.0, max(0.0, skill))
    mistake = min(0.4, max(0.0, mistake))
    return round(max_speed * (0.75 + 0.16 * skill) * (1 - 0.2 * mistake) * pace, 3)


def build_track(mode: str, seed: int) -> dict:
    rng = random.Random(seed)
    curve = 0.0
    hold = 0
    segments = []
    for i in range(SEGMENT_COUNT):
        if hold <= 0:
            curve = rng.choice([-1.0, -0.65, -0.3, 0.0, 0.0, 0.3, 0.65, 1.0])
            if mode == "bus":
                curve *= 0.55
            elif mode == "plane":
                curve *= 0.75
            hold = rng.randint(8, 16)
        hold -= 1
        altitude = 0.0
        if mode == "plane":
            altitude = math.sin(i / 8.5) * 0.62 + curve * 0.12
        segments.append(
            {
                "curve": round(curve, 3),
                "altitude": round(altitude, 3),
                "gate": i % 20 == 10,
            }
        )
    weather = rng.choice(list(WEATHER))
    names = TRACK_NAMES[mode]
    return {
        "name": names[seed % len(names)],
        "weather": weather,
        "seed": seed,
        "segment_m": SEGMENT_M,
        "length_m": TRACK_LENGTH_M,
        "segments": segments,
    }


COIN_POINTS = 100
HURDLE_POINTS = 25
FINISH_BONUS = 250
POINT_PER_SECOND = 8
RESET_SECONDS = 60
MAX_COINS = 14
MAX_HURDLES = 10


def launch_pad(coins: int, hurdles: int, finished: bool, time_left: float) -> dict:
    """The score the board is allowed to keep. The model does not get a vote."""
    coins_n = max(0, min(MAX_COINS, int(coins)))
    hurdles_n = max(0, min(MAX_HURDLES, int(hurdles)))
    left = max(0.0, min(float(RESET_SECONDS), float(time_left)))
    lab = coins_n * COIN_POINTS
    pen = hurdles_n * HURDLE_POINTS
    finish = FINISH_BONUS if finished else 0
    clock = round(left * POINT_PER_SECOND) if finished else 0
    total = max(0, lab + finish + clock - pen)
    return {
        "coins": coins_n,
        "hurdles": hurdles_n,
        "finished": bool(finished),
        "time_left": round(left, 2),
        "lab": lab,
        "pen": pen,
        "finish": finish,
        "clock": clock,
        "points": total,
        "beat": bool(finished),
    }


def fallback_coach(mode: str, weather: str, vehicle: dict) -> str:
    return f"{vehicle['model']}. {MODE_COACH[mode]} {WEATHER_COACH.get(weather, '')}".strip()


def fallback_radio(temperament: str, vehicle: dict) -> str:
    label = TEMPERAMENT[temperament]["label"]
    if temperament == "professor":
        return f"{label} here. I'll take a clean line in the {vehicle['model']}. Try to match it."
    if temperament == "wildcard":
        return f"{label} here. I'm sending the {vehicle['model']} in hot. Don't flinch."
    return f"{label} in the {vehicle['model']}. One quiet minute. Coins off the line, hurdles on it."


def choose_ad(mode: str, placement: str, counts: dict[str, int]) -> dict:
    catalog = ads()
    pool = [ad for ad in catalog if mode in ad["modes"] and placement in ad["placements"]]
    if not pool:
        pool = [ad for ad in catalog if placement in ad["placements"]]
    if not pool:
        pool = catalog[:1]
    pool = sorted(pool, key=lambda ad: (counts.get(ad["id"], 0), ad["id"]))
    chosen = dict(pool[0])
    chosen.pop("modes", None)
    chosen.pop("placements", None)
    return chosen


def price_usage(input_tokens: int, output_tokens: int, cache_read: int, cache_write: int) -> float:
    uncached = max(0, int(input_tokens) - int(cache_read) - int(cache_write))
    cost = (
        uncached * PRICE_INPUT
        + int(cache_read) * PRICE_CACHE_READ
        + int(cache_write) * PRICE_CACHE_WRITE
        + int(output_tokens) * PRICE_OUTPUT
    )
    return round(cost, 8)


def official_points(model_points: int, server_points: int) -> dict:
    try:
        said = int(model_points)
    except (TypeError, ValueError):
        said = -1
    posted = int(server_points)
    return {"model_said": said, "posted": posted, "corrected": said != posted}


def _as_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def judge_lap(samples: list[dict], vehicle: dict, track: dict, rival_cruise_mps: float, match: str) -> dict:
    """Score a lap from the recorded trail. The client's own point total is ignored."""
    notes: list[str] = []
    segments = track["segments"]
    segment_m = float(track["segment_m"])
    max_speed = float(vehicle["max_speed"])
    mode = vehicle["mode"]
    empty = {
        "ok": False,
        "finished": False,
        "points": 0,
        "time_s": 0.0,
        "breakdown": {"finish": 0, "gates": 0, "clean": 0, "head_to_head": 0, "smooth": 0},
        "gates_hit": 0,
        "gates_total": sum(1 for seg in segments if seg["gate"]),
        "offtrack": 0.0,
        "beat": False,
        "notes": ["The watchers did not get a usable lap."],
    }
    if not isinstance(samples, list) or len(samples) < 8:
        return empty

    rows = list(samples[:2000])
    if len(samples) > 2000:
        rows.append(samples[-1])
    parsed = []
    for row in rows:
        if not isinstance(row, dict):
            empty["notes"] = ["A sample in the lap was unreadable."]
            return empty
        parsed.append(
            {
                "t": _as_float(row.get("t")),
                "seg": _as_float(row.get("seg")),
                "x": _as_float(row.get("x")),
                "alt": _as_float(row.get("alt")),
                "speed": _as_float(row.get("speed")),
            }
        )

    for sample in parsed:
        if abs(sample["x"]) > 2.2 or abs(sample["alt"]) > 2.2:
            empty["notes"] = ["The lap left the course envelope, so it was thrown out."]
            return empty

    for prev, nxt in zip(parsed, parsed[1:]):
        dt = nxt["t"] - prev["t"]
        if dt <= 0 or dt > 1.25:
            empty["notes"] = ["The lap recording had a gap, so it was thrown out."]
            return empty
        dseg = nxt["seg"] - prev["seg"]
        if dseg < -0.35:
            empty["notes"] = ["The lap ran backwards, so it was thrown out."]
            return empty
        if dseg * segment_m / dt > max_speed * 1.28:
            empty["notes"] = ["That lap was faster than this machine can go, so it was thrown out."]
            return empty

    end = parsed[-1]
    finished = end["seg"] >= SEGMENT_COUNT - 0.2
    time_s = round(end["t"], 3)
    offtrack_hits = 0
    jerks = []
    body = float(vehicle["body"])
    for prev, nxt in zip(parsed, parsed[1:]):
        dt = nxt["t"] - prev["t"]
        if abs(nxt["x"]) + body > 1:
            offtrack_hits += 1
        jerks.append(abs(nxt["x"] - prev["x"]) / dt)
    offtrack = offtrack_hits / max(1, len(parsed) - 1)

    gate_indexes = [i for i, seg in enumerate(segments) if seg["gate"]]
    hits = 0
    for index in gate_indexes:
        near = [sample for sample in parsed if index <= sample["seg"] <= index + 1.4]
        if not near:
            continue
        sample = min(near, key=lambda item: abs(item["seg"] - index))
        centered = abs(sample["x"]) < 0.58
        if mode == "plane":
            target = float(segments[index]["altitude"])
            centered = centered and abs(sample["alt"] - target) < 0.42 and sample["speed"] >= float(vehicle["stall_speed"]) * 0.98
        if centered:
            hits += 1

    breakdown = {"finish": 0, "gates": 0, "clean": 0, "head_to_head": 0, "smooth": 0}
    if not finished:
        notes.append("Did not finish the circuit.")
        return {
            "ok": True,
            "finished": False,
            "points": 0,
            "time_s": time_s,
            "breakdown": breakdown,
            "gates_hit": hits,
            "gates_total": len(gate_indexes),
            "offtrack": round(offtrack, 3),
            "beat": False,
            "notes": notes,
        }

    breakdown["finish"] = 45
    breakdown["gates"] = round(30 * hits / max(1, len(gate_indexes)))
    breakdown["clean"] = round(20 * (1 - min(1.0, offtrack / 0.28)))
    if mode == "bus":
        jerks.sort()
        median = jerks[len(jerks) // 2] if jerks else 0
        if median < 0.38:
            breakdown["smooth"] = 10
            notes.append("The passengers stayed in their seats.")
        else:
            notes.append("The ride was rough. Smooth is worth points in a bus.")

    beat = False
    if match == "ai" and rival_cruise_mps > 1:
        rival_time = TRACK_LENGTH_M / rival_cruise_mps
        beat = time_s < rival_time
        if beat:
            breakdown["head_to_head"] = 25
            notes.append("You were ahead of the rival's pace.")
        else:
            notes.append("The rival's pace was quicker.")
    elif match == "human":
        notes.append("Head-to-head points wait until the other driver posts a time.")

    total = sum(breakdown.values())
    return {
        "ok": True,
        "finished": True,
        "points": int(total),
        "time_s": time_s,
        "breakdown": breakdown,
        "gates_hit": hits,
        "gates_total": len(gate_indexes),
        "offtrack": round(offtrack, 3),
        "beat": beat,
        "rival_time_s": round(TRACK_LENGTH_M / rival_cruise_mps, 3) if rival_cruise_mps > 1 else None,
        "notes": notes,
    }


def apply_head_to_head(your_time: float | None, their_time: float | None, already_paid: bool) -> dict:
    """Award the duel bonus once both human times exist. Idempotent if already paid."""
    if already_paid or your_time is None or their_time is None:
        return {"points": 0, "won": False, "ready": your_time is not None and their_time is not None}
    won = your_time < their_time
    return {"points": 25 if won else 0, "won": won, "ready": True}


def projection(active_users: int, impressions_each: int = 8) -> dict:
    """Labeled estimate from the rate card, not money collected."""
    catalog = ads()
    blended = sum(ad["cpm"] for ad in catalog) / len(catalog)
    impressions = active_users * impressions_each
    return {
        "active_users": active_users,
        "impressions_each": impressions_each,
        "blended_cpm": round(blended, 2),
        "revenue_usd": round(impressions * blended / 1000, 2),
        "note": "Estimate from the house rate card. No sponsor has been billed.",
    }
