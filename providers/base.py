"""Data model + provider interface.

A provider's only job: return a list of `Match` objects for a given date,
each one carrying team strength inputs and the bookmaker odds we can bet.
Swap providers by changing PROVIDER in .env — the engine never changes.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Dict, List, Optional


@dataclass
class TeamForm:
    """Recent-form aggregates used to estimate attack / defence strength."""
    name: str
    goals_for: float          # avg goals scored per game (recent window)
    goals_against: float      # avg goals conceded per game
    matches: int = 10
    home_advantage: float = 1.0  # multiplier applied when playing at home


@dataclass
class Match:
    match_id: str
    league: str
    country: str
    kickoff: str              # "HH:MM" local
    home: TeamForm
    away: TeamForm
    league_avg_goals: float = 2.70
    # market key -> decimal odds. Keys the engine understands:
    #   1, X, 2, 1X, X2, 12, O1.5, U1.5, O2.5, U2.5, O3.5, U3.5, BTTS, NOBTTS
    odds: Dict[str, float] = field(default_factory=dict)
    day: str = ""             # "Sat 10 Oct" — shown when a card spans several days

    @property
    def label(self) -> str:
        return f"{self.home.name} vs {self.away.name}"

    @property
    def when(self) -> str:
        return f"{self.day} {self.kickoff}".strip()


class BaseProvider:
    name = "base"

    async def fixtures(self, day: Optional[date] = None) -> List[Match]:
        raise NotImplementedError

    async def close(self) -> None:
        pass
