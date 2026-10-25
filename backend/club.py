"""Catalog, track, scoring, and the checks that trust neither the model nor the browser."""

from __future__ import annotations

import json
import random
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"

SEGMENT_M = 16
TRACK_SEGMENTS = 200
TRACK_LENGTH_M = SEGMENT_M * TRACK_SEGMENTS
TIME_LIMIT = 60

COIN_POINTS = 100
HURDLE_POINTS = 25
FINISH_BONUS = 250
POINT_PER_SECOND = 8
COIN_REACH = 0.42
HURDLE_REACH = 0.24

# GPT-6 Luna list prices for a normal-length prompt, in USD per token.
# Source: genai-prices snapshot shipped with the course tools.
# input_tokens already include cached tokens, so uncached input is the remainder.
PRICE_INPUT = 0.10 / 1_000_000
PRICE_CACHE_READ = 0.01 / 1_000_000
PRICE_CACHE_WRITE = 0.125 / 1_000_000
PRICE_OUTPUT = 0.50 / 1_000_000

# One ranked minute is about 9,000 tokens and three agent runs.
DAILY_TOKEN_CAP = 300_000
DAILY_RUN_CAP = 120
DRIVER_DAILY_MINUTES = 10

PASSENGER_NAME = "Quiet Seat"
TRACK_NAMES = ["Paper Street", "Harbor Hairpin", "The Cut"]

WEATHER = {
    "Clear": {"grip": 1.0, "accel": 1.0, "wind": 0.0, "glare": 0},
    "Wet": {"grip": 0.8, "accel": 0.88, "wind": 0.0, "glare": 0},
    "Crosswind": {"grip": 1.0, "accel": 1.0, "wind": 0.55, "glare": 0},
    "Low sun": {"grip": 1.0, "accel": 1.0, "wind": 0.05, "glare": 1},
}

WEATHER_COACH = {
    "Clear": "The road is dry.",
    "Wet": "It is wet, so the same wheel does less. Lift earlier.",
    "Crosswind": "There is a crosswind. Hold a line instead of chasing every push.",
    "Low sun": "The sun is low. The coins are still where they were.",
}

INJECTION = re.compile(
    r"(ignore\s+(all\s+|any\s+|the\s+|your\s+)?(previous|prior|above|earlier|instructions|rules)|"
    r"disregard\s+(all\s+|any\s+|the\s+|your\s+)?(previous|prior|above|earlier|instructions|rules|guidance)|"
    r"forget\s+(all\s+|your\s+|the\s+)?(previous\s+|prior\s+)?(instructions|rules|guidance|prompt)|"
    r"new\s+instructions|override\s+(the\s+|your\s+)?(rules|instructions|system)|"
    r"system\s+(prompt|rules|message)|developer\s+message|you\s+are\s+now|pretend\s+(to\s+be|you\s+are)|"
    r"<\s*/?\s*(system|instructions|prompt)|"
    r"api[_\s-]?key|jailbreak|do\s+anything\s+now|"
    r"set\s+(my\s+)?(points|score)|award\s+\d+|give\s+me\s+\d+\s+points|points\s+(to|=)\s*\d{3,}|"
    r"reveal\s+(the\s+|your\s+)?(prompt|instructions|key|rules))",
    re.I,
)

NAME_OK = re.compile(r"[A-Za-z][A-Za-z .'\-]{1,22}$")


def load_json(name: str) -> list[dict]:
    return json.loads((DATA / name).read_text(encoding="utf-8"))


def vehicles() -> list[dict]:
    return load_json("vehicles.json")


def vehicle_by_id(vehicle_id: str) -> dict | None:
    for vehicle in vehicles():
        if vehicle["id"] == vehicle_id:
            return vehicle
    return None


def normalize_name(name: str) -> str:
    return " ".join((name or "").split())


def inspect_text(text: str, field: str = "any") -> dict:
    """Stop instructions-as-data before an agent is called."""
    raw = "" if text is None else str(text)
    cleaned = " ".join(raw.split())
    if INJECTION.search(cleaned):
        return {
            "allowed": False,
            "reason": "That text is an instruction to the crew, so it stopped here. No agent was called and no points moved.",
            "text": cleaned[:80],
        }
    if len(cleaned) > 280:
        return {
            "allowed": False,
            "reason": "That note is too long to be a name or a feeling.",
            "text": cleaned[:80],
        }
    if field == "name" and not NAME_OK.fullmatch(cleaned):
        return {
            "allowed": False,
            "reason": "Use 2–23 letters for the name on the board.",
            "text": cleaned[:80],
        }
    return {"allowed": True, "reason": "", "text": cleaned}


def clean_copy(text: str, limit: int = 220) -> str:
    cleaned = re.sub(r"[\x00-\x1f]", " ", text or "")
    cleaned = " ".join(cleaned.split())
    if INJECTION.search(cleaned):
        return ""
    return cleaned[:limit]


def pickup_plan() -> list[dict]:
    """Fourteen coins off the racing line, ten hurdles on it. Same places on every road."""
    pickups = []
    for index, seg in enumerate(range(14, TRACK_SEGMENTS - 8, 13)):
        pickups.append({"seg": seg, "x": -0.55 if index % 2 else 0.55, "kind": "coin"})
    for seg in range(20, TRACK_SEGMENTS - 10, 18):
        pickups.append({"seg": seg, "x": 0.0, "kind": "hurdle"})
    return pickups


def build_track(seed: int) -> dict:
    """The road for one ranked minute. The browser drives it; the server keeps the copy it scores against."""
    rng = random.Random(seed)
    curve = 0.0
    hold = 0
    segments = []
    for i in range(TRACK_SEGMENTS):
        if hold <= 0:
            curve = rng.choice([-1.0, -0.55, 0.0, 0.0, 0.55, 1.0])
            hold = rng.randint(8, 15)
        hold -= 1
        segments.append({"curve": curve, "altitude": 0, "gate": i % 18 == 9})
    weather = rng.choice(list(WEATHER))
    return {
        "name": TRACK_NAMES[seed % len(TRACK_NAMES)],
        "weather": weather,
        "seed": seed,
        "segment_m": SEGMENT_M,
        "length_m": TRACK_LENGTH_M,
        "time_limit": TIME_LIMIT,
        "segments": segments,
        "pickups": pickup_plan(),
    }


def score_minute(coins: int, hurdles: int, finished: bool, time_left: float) -> dict:
    """The arithmetic the board is allowed to keep. The model does not get a vote."""
    coins_n = max(0, min(14, int(coins)))
    hurdles_n = max(0, min(10, int(hurdles)))
    left = max(0.0, min(float(TIME_LIMIT), float(time_left)))
    lab = coins_n * COIN_POINTS
    pen = hurdles_n * HURDLE_POINTS
    finish = FINISH_BONUS if finished else 0
    clock = round(left * POINT_PER_SECOND) if finished else 0
    return {
        "coins": coins_n,
        "hurdles": hurdles_n,
        "finished": bool(finished),
        "time_left": round(left, 2) if finished else 0.0,
        "lab": lab,
        "pen": pen,
        "finish": finish,
        "clock": clock,
        "points": max(0, lab + finish + clock - pen),
    }


def _num(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None


def recount(samples, track: dict, vehicle: dict) -> dict:
    """Replay the browser's trail against the server's copy of the road and count what it actually hit."""

    def thrown_out(reason: str) -> dict:
        return {"ok": False, "reason": reason, "coins": 0, "hurdles": 0, "finished": False, "time_s": None, "time_left": 0.0}

    if not isinstance(samples, list) or len(samples) < 2:
        return thrown_out("The drive did not send a trail.")
    if len(samples) > 2600:
        return thrown_out("The trail was longer than one minute allows.")
    trail = []
    for row in samples:
        if not isinstance(row, dict):
            return thrown_out("A point in the trail was unreadable.")
        t, seg, x = _num(row.get("t")), _num(row.get("seg")), _num(row.get("x"))
        if t is None or seg is None or x is None:
            return thrown_out("A point in the trail was unreadable.")
        trail.append((t, seg, x))

    total = len(track["segments"])
    top_mps = float(vehicle["max_speed"])
    sideways = float(vehicle["steer"]) + 2.2
    if trail[0][0] > 0.5 or trail[0][1] > 0.5:
        return thrown_out("The trail did not start on the grid.")
    if trail[-1][0] > TIME_LIMIT + 0.1:
        return thrown_out("The trail ran past one minute.")
    fastest = 0.0
    anchor = 0
    for i, ((t0, s0, x0), (t1, s1, x1)) in enumerate(zip(trail, trail[1:]), start=1):
        dt = t1 - t0
        if abs(x1) > 1.66:
            return thrown_out("The trail left the road envelope.")
        if dt <= 0 or dt > 1.0:
            return thrown_out("The trail had a gap in time.")
        if s1 < s0 - 0.01:
            return thrown_out("The trail ran backwards.")
        # Speed and steering are judged over at least 0.08 s, so frame-level rounding cannot fail an honest lap.
        while trail[i][0] - trail[anchor + 1][0] >= 0.08:
            anchor += 1
        span = t1 - trail[anchor][0]
        if span < 0.08:
            continue
        speed = (s1 - trail[anchor][1]) * track["segment_m"] / span
        if speed > top_mps * 1.03 + 0.5:
            return thrown_out("The trail was faster than this car can go.")
        if abs(x1 - trail[anchor][2]) / span > sideways:
            return thrown_out("The trail moved sideways faster than this car can steer.")
        fastest = max(fastest, speed)

    taken = {"left": [0, 0], "right": [0, 0]}
    coins = hurdles = 0
    hurdles_reached = 0
    for item in track["pickups"]:
        crossing = next((point for point in trail if point[1] >= item["seg"]), None)
        side = "left" if item["x"] < 0 else "right"
        if crossing is None:
            continue
        gap = abs(crossing[2] - item["x"])
        if item["kind"] == "coin":
            taken[side][1] += 1
            if gap < COIN_REACH:
                coins += 1
                taken[side][0] += 1
        else:
            hurdles_reached += 1
            if gap < HURDLE_REACH:
                hurdles += 1

    end_t, end_seg, _ = trail[-1]
    finished = end_seg >= total - 0.01 and end_t <= TIME_LIMIT
    return {
        "ok": True,
        "reason": "",
        "coins": coins,
        "hurdles": hurdles,
        "finished": finished,
        "time_s": round(end_t, 2),
        "time_left": max(0.0, TIME_LIMIT - end_t) if finished else 0.0,
        "coins_reached": taken["left"][1] + taken["right"][1],
        "coins_left": taken["left"],
        "coins_right": taken["right"],
        "hurdles_reached": hurdles_reached,
        "distance_m": round(min(end_seg, total) * track["segment_m"]),
        "top_mph": round(fastest * 2.236936),
    }


def fallback_coach(weather: str, vehicle: dict) -> str:
    return f"{vehicle['brand']} {vehicle['model']}. One minute. Put the day down. Steer into the coins and lift before a hurdle. {WEATHER_COACH.get(weather, '')}".strip()


def fallback_radio(vehicle: dict) -> str:
    return f"{PASSENGER_NAME} in the {vehicle['model']}. One quiet minute. Coins off the line, hurdles on it."


def price_usage(input_tokens: int, output_tokens: int, cache_read: int, cache_write: int) -> float:
    uncached = max(0, int(input_tokens) - int(cache_read) - int(cache_write))
    cost = (
        uncached * PRICE_INPUT
        + int(cache_read) * PRICE_CACHE_READ
        + int(cache_write) * PRICE_CACHE_WRITE
        + int(output_tokens) * PRICE_OUTPUT
    )
    return round(cost, 8)


def official_points(model_points, server_points: int) -> dict:
    try:
        said = int(model_points)
    except (TypeError, ValueError):
        said = -1
    posted = int(server_points)
    return {"model_said": said, "posted": posted, "corrected": said != posted}


ALWAYS_OK_NUMBERS = {8, 10, 14, 25, 60, 100, 250}
NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")


def numbers_from(value) -> set[int]:
    """Every whole number inside a tool payload, so a sentence can be checked against it."""
    found: set[int] = set()
    if isinstance(value, bool):
        return found
    if isinstance(value, (int, float)):
        found.add(int(round(value)))
        found.add(int(value))
    elif isinstance(value, str):
        found |= {int(float(n.replace(",", ""))) for n in NUMBER.findall(value)}
    elif isinstance(value, dict):
        for item in value.values():
            found |= numbers_from(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            found |= numbers_from(item)
    return found


def invented_numbers(text: str, allowed: set[int]) -> list[int]:
    """Numbers in the clerk's copy that no tool handed it."""
    said = [int(float(n.replace(",", ""))) for n in NUMBER.findall(text or "")]
    return [n for n in said if n not in allowed and n not in ALWAYS_OK_NUMBERS]
