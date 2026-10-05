"""Best of both: free CSVs first, live API only when needed.

The free football-data.co.uk file covers ~22 divisions but is refreshed a
couple of times a week, so on a Monday or during an international break it
can be nearly empty. API-Football always knows today's fixtures but costs
requests from a 100/day budget.

So: try free first. If the free card is big enough, we spend ZERO API
requests. Only when it comes up short do we call the API — and even then we
merge both sources, preferring the free one (its odds are best-available
prices across many books, which the backtest showed is worth ~4% a leg).

Result: typically 0 API requests a day, with the API as a safety net.
"""
from __future__ import annotations

import logging
from datetime import date
from typing import List, Optional

import config
from providers.base import BaseProvider, Match
from providers.footballdata_uk import FootballDataUKProvider

log = logging.getLogger(__name__)


class HybridProvider(BaseProvider):
    name = "free CSVs + API-Football fallback"

    def __init__(self):
        self.free = FootballDataUKProvider()
        self._api = None

    def _api_provider(self):
        if self._api is None:
            from providers.apifootball import APIFootballProvider
            self._api = APIFootballProvider()
        return self._api

    async def fixtures(self, day: Optional[date] = None) -> List[Match]:
        day = day or date.today()

        free_matches: List[Match] = []
        try:
            free_matches = await self.free.fixtures(day)
        except Exception as exc:  # noqa: BLE001
            log.error("free provider failed: %s", exc)

        if len(free_matches) >= config.MIN_CARD_SIZE:
            log.info("hybrid: free source is enough (%d fixtures), 0 API requests used",
                     len(free_matches))
            self.name = "football-data.co.uk (free)"
            return free_matches

        if not config.APIFOOTBALL_KEY:
            log.info("hybrid: free card is thin (%d) and no API key set — "
                     "set APIFOOTBALL_KEY to fill the gap", len(free_matches))
            self.name = "football-data.co.uk (free, thin card)"
            return free_matches

        log.info("hybrid: free card thin (%d fixtures) — topping up from API-Football",
                 len(free_matches))
        try:
            api = self._api_provider()
            api_matches = await api.fixtures(day)
        except Exception as exc:  # noqa: BLE001
            log.error("API-Football fallback failed: %s", exc)
            self.name = "football-data.co.uk (free; API unavailable)"
            return free_matches

        if not api_matches:
            log.info("hybrid: API-Football had nothing to add either — "
                     "there genuinely are no matches right now")
            self.name = "football-data.co.uk + API-Football (no fixtures today)"
            return free_matches

        # de-duplicate on team names, keeping the free (best-priced) version
        seen = {self._key(m) for m in free_matches}
        merged = list(free_matches)
        for m in api_matches:
            if self._key(m) not in seen:
                merged.append(m)
                seen.add(self._key(m))

        merged.sort(key=lambda m: (m.day, m.kickoff))
        self.name = (f"free CSVs + API-Football "
                     f"({len(free_matches)} free, {len(merged) - len(free_matches)} live)")
        log.info("hybrid: %d fixtures total", len(merged))
        return merged

    @staticmethod
    def _key(m: Match) -> str:
        return (m.home.name.lower()[:8] + "|" + m.away.name.lower()[:8])

    async def close(self) -> None:
        await self.free.close()
        if self._api is not None:
            await self._api.close()
