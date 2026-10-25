"""Short-lived lobbies. Two drivers in the same mode share a seed."""

from __future__ import annotations

import random
import threading
import time
import uuid
from dataclasses import dataclass, field


@dataclass
class Waiter:
    id: str
    name: str
    vehicle_id: str
    mode: str
    created: float
    status: str = "waiting"
    match_id: str = ""
    opponent: str = ""
    seed: int = 0


@dataclass
class Match:
    id: str
    seed: int
    mode: str
    players: dict[str, str] = field(default_factory=dict)
    created: float = 0


class Lobby:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.waiters: dict[str, Waiter] = {}
        self.by_mode: dict[str, str] = {}
        self.matches: dict[str, Match] = {}
        self.states: dict[str, dict[str, dict]] = {}

    def _purge(self, now: float) -> None:
        stale = [item.id for item in self.waiters.values() if item.status == "waiting" and now - item.created > 12]
        for item_id in stale:
            item = self.waiters.pop(item_id, None)
            if item and self.by_mode.get(item.mode) == item_id:
                self.by_mode.pop(item.mode, None)
        old_matches = [key for key, match in self.matches.items() if now - match.created > 20 * 60]
        for key in old_matches:
            self.matches.pop(key, None)
            self.states.pop(key, None)

    def join(self, name: str, vehicle_id: str, mode: str) -> Waiter:
        now = time.time()
        with self._lock:
            self._purge(now)
            for item in self.waiters.values():
                if item.name.casefold() == name.casefold() and item.mode == mode and item.status == "waiting":
                    return item
            current_id = self.by_mode.get(mode)
            other = self.waiters.get(current_id) if current_id else None
            if other and other.status == "waiting" and other.name.casefold() != name.casefold():
                match_id = uuid.uuid4().hex[:12]
                seed = random.randint(1, 9_999_999)
                other.status = "matched"
                other.match_id = match_id
                other.opponent = name
                other.seed = seed
                me = Waiter(
                    id=uuid.uuid4().hex[:12],
                    name=name,
                    vehicle_id=vehicle_id,
                    mode=mode,
                    created=now,
                    status="matched",
                    match_id=match_id,
                    opponent=other.name,
                    seed=seed,
                )
                self.waiters[me.id] = me
                self.by_mode.pop(mode, None)
                self.matches[match_id] = Match(
                    id=match_id,
                    seed=seed,
                    mode=mode,
                    players={other.name: other.vehicle_id, name: vehicle_id},
                    created=now,
                )
                return me
            me = Waiter(
                id=uuid.uuid4().hex[:12],
                name=name,
                vehicle_id=vehicle_id,
                mode=mode,
                created=now,
            )
            self.waiters[me.id] = me
            self.by_mode[mode] = me.id
            return me

    def get(self, waiter_id: str) -> Waiter | None:
        with self._lock:
            self._purge(time.time())
            return self.waiters.get(waiter_id)

    def match_for(self, waiter_id: str, name: str) -> Match | None:
        with self._lock:
            waiter = self.waiters.get(waiter_id)
            if waiter is None or waiter.status != "matched":
                return None
            if waiter.name.casefold() != name.casefold():
                return None
            return self.matches.get(waiter.match_id)

    def post_state(self, match_id: str, name: str, state: dict) -> dict | None:
        now = time.time()
        with self._lock:
            if match_id not in self.matches:
                return None
            bucket = self.states.setdefault(match_id, {})
            bucket[name] = {**state, "name": name, "at": now}
            for other, row in bucket.items():
                if other.casefold() != name.casefold() and now - row.get("at", 0) < 3:
                    return {key: row[key] for key in ("name", "seg", "x", "alt", "speed", "finished")}
            return None
