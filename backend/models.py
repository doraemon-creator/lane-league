"""Request bodies for the club API."""

from __future__ import annotations

from pydantic import BaseModel, Field


class PrepareIn(BaseModel):
    name: str
    vehicle_id: str
    note: str = ""


class CloseIn(BaseModel):
    race_id: str
    name: str
    samples: list[dict] = Field(default_factory=list)
    ended_early: bool = False
    # What the browser counted. Shown next to the server's recount, never posted.
    claimed_coins: int = 0
    claimed_hurdles: int = 0


class DrillIn(BaseModel):
    name: str = ""
    text: str
    field: str = "brief"
