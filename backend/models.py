"""Request bodies for the club API."""

from __future__ import annotations

from pydantic import BaseModel, Field


class PrepareIn(BaseModel):
    name: str
    vehicle_id: str
    match: str = "ai"
    temperament: str = "local"
    waiter_id: str = ""
    session_id: str = ""
    note: str = ""


class FinishIn(BaseModel):
    race_id: str
    name: str
    samples: list[dict] = Field(default_factory=list)
    session_id: str = ""


class CheckIn(BaseModel):
    name: str
    session_id: str = ""


class JoinIn(BaseModel):
    name: str
    vehicle_id: str


class StateIn(BaseModel):
    name: str
    seg: float = 0
    x: float = 0
    alt: float = 0
    speed: float = 0
    finished: bool = False


class ResetClose(BaseModel):
    race_id: str
    name: str
    coins: int = 0
    hurdles: int = 0
    finished: bool = False
    time_left: float = 0
    ended_early: bool = False


class ScoreIn(BaseModel):
    name: str
    points: int = 0
    coins: int = 0
    hurdles: int = 0
    finished: bool = False
    time_s: float = 0
    vehicle_id: str = ""


class DrillIn(BaseModel):
    name: str = ""
    text: str
    field: str = "name"
