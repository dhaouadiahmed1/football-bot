"""100% FREE provider — football-data.co.uk.

No API key. No registration. No rate limit. No credit card. Ever.

Two public CSV endpoints:
  https://www.football-data.co.uk/fixtures.csv          upcoming ~7 days + odds
  https://www.football-data.co.uk/mmz4281/<season>/<div>.csv   played results + odds

The fixtures file carries real Bet365 prices (1X2 and Over/Under 2.5) plus the
market average and best-available price. Form is computed from the season
results file, so every number the model uses comes from actual played matches.

Markets published: 1, X, 2, O2.5, U2.5.
Double chance is derived as 1/(1/a + 1/b), which is how books price it (it
carries over the same margin), so those legs are honest prices too.
BTTS / O1.5 / O3.5 are NOT published here, so we simply never bet them —
better than inventing a price.
"""
from __future__ import annotations

import asyncio
import csv
import io
import logging
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import httpx

import config
from providers.base import BaseProvider, Match, TeamForm

log = logging.getLogger(__name__)

BASE = "https://www.football-data.co.uk"
FIXTURES_URL = f"{BASE}/fixtures.csv"

# division code -> (country, league name)
DIVISIONS: Dict[str, Tuple[str, str]] = {
    "E0": ("England", "Premier League"), "E1": ("England", "Championship"),
    "E2": ("England", "League One"), "E3": ("England", "League Two"),
    "EC": ("England", "National League"),
    "SC0": ("Scotland", "Premiership"), "SC1": ("Scotland", "Championship"),
    "SC2": ("Scotland", "League One"), "SC3": ("Scotland", "League Two"),
    "D1": ("Germany", "Bundesliga"), "D2": ("Germany", "2. Bundesliga"),
    "SP1": ("Spain", "La Liga"), "SP2": ("Spain", "Segunda"),
    "I1": ("Italy", "Serie A"), "I2": ("Italy", "Serie B"),
    "F1": ("France", "Ligue 1"), "F2": ("France", "Ligue 2"),
    "N1": ("Netherlands", "Eredivisie"), "B1": ("Belgium", "Pro League"),
    "P1": ("Portugal", "Primeira Liga"), "T1": ("Turkey", "Super Lig"),
    "G1": ("Greece", "Super League"),
}

FORM_WINDOW = 8        # matches of recent form
DECAY = 0.86           # weight of each older match
CACHE_TTL = 6 * 3600   # re-download a season file at most every 6h


def season_code(d: date) -> str:
    """2026-10-05 -> '2627' (European seasons start in July)."""
    start = d.year if d.month >= 7 else d.year - 1
    return f"{start % 100:02d}{(start + 1) % 100:02d}"


def parse_date(s: str) -> Optional[date]:
    for fmt in ("%d/%m/%Y", "%d/%m/%y"):
        try:
            return datetime.strptime(s.strip(), fmt).date()
        except (ValueError, AttributeError):
            continue
    return None


def _f(row: dict, *keys: str) -> Optional[float]:
    """First non-empty float among the given columns (price fallback chain)."""
    for k in keys:
        v = (row.get(k) or "").strip()
        if v:
            try:
                f = float(v)
                if f > 1.0:
                    return f
            except ValueError:
                pass
    return None


class FootballDataUKProvider(BaseProvider):
    name = "football-data.co.uk (free)"

    def __init__(self, cache_dir: Optional[Path] = None):
        self.cache = cache_dir or (config.DATA_DIR / "cache")
        self.cache.mkdir(parents=True, exist_ok=True)
        self._c = httpx.AsyncClient(
            timeout=30.0, follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 (football-bot)"},
        )
        self._results: Dict[str, List[dict]] = {}

    # --------------------------------------------------------------- http --
    async def _csv(self, url: str, cache_name: str, ttl: int = CACHE_TTL) -> List[dict]:
        path = self.cache / cache_name
        if path.exists() and (time.time() - path.stat().st_mtime) < ttl:
            text = path.read_text(encoding="utf-8", errors="ignore")
        else:
            try:
                r = await self._c.get(url)
                r.raise_for_status()
                text = r.content.decode("utf-8-sig", errors="ignore")
                path.write_text(text, encoding="utf-8")
            except Exception as exc:                       # noqa: BLE001
                log.warning("download failed %s (%s)", url, exc)
                if not path.exists():
                    return []
                text = path.read_text(encoding="utf-8", errors="ignore")
        rows = list(csv.DictReader(io.StringIO(text)))
        return [r for r in rows if (r.get("HomeTeam") or "").strip()]

    async def results(self, div: str, season: str) -> List[dict]:
        """Played matches of a division, chronologically."""
        key = f"{div}:{season}"
        if key not in self._results:
            rows = await self._csv(f"{BASE}/mmz4281/{season}/{div}.csv",
                                   f"{season}_{div}.csv")
            out = []
            for r in rows:
                d = parse_date(r.get("Date", ""))
                if d is None or not (r.get("FTHG") or "").strip():
                    continue
                try:
                    r["_date"] = d
                    r["_hg"] = int(float(r["FTHG"]))
                    r["_ag"] = int(float(r["FTAG"]))
                except (ValueError, KeyError):
                    continue
                out.append(r)
            out.sort(key=lambda r: r["_date"])
            self._results[key] = out
        return self._results[key]

    # --------------------------------------------------------------- form --
    @staticmethod
    def _team_form(rows: List[dict], team: str, before: date) -> Tuple[float, float, int]:
        """Decay-weighted goals for / against over the last FORM_WINDOW games."""
        games = [
            (r["_hg"], r["_ag"]) if r["HomeTeam"] == team else (r["_ag"], r["_hg"])
            for r in rows
            if r["_date"] < before and team in (r["HomeTeam"], r["AwayTeam"])
        ]
        games = games[-FORM_WINDOW:]
        if not games:
            return 0.0, 0.0, 0
        wf = wa = wsum = 0.0
        n = len(games)
        for i, (gf, ga) in enumerate(games):
            w = DECAY ** (n - 1 - i)
            wf += gf * w
            wa += ga * w
            wsum += w
        return wf / wsum, wa / wsum, n

    async def _form(self, div: str, team: str, day: date, season: str) -> Optional[TeamForm]:
        rows = await self.results(div, season)
        gf, ga, n = self._team_form(rows, team, day)
        if n < 5:  # early season: blend in last season's numbers
            prev = season_code(day - timedelta(days=365))
            prows = await self.results(div, prev)
            pgf, pga, pn = self._team_form(prows, team, date(2100, 1, 1))
            if pn:
                w = n / 5.0
                gf = gf * w + pgf * (1 - w) if n else pgf
                ga = ga * w + pga * (1 - w) if n else pga
                n = n + pn
        if n == 0:
            return None
        return TeamForm(name=team, goals_for=round(gf, 3),
                        goals_against=round(ga, 3), matches=n)

    @staticmethod
    def _league_profile(rows: List[dict]) -> Tuple[float, float]:
        """(avg goals per game, home advantage multiplier) measured from results."""
        if len(rows) < 20:
            return 2.70, 1.12
        hg = sum(r["_hg"] for r in rows) / len(rows)
        ag = sum(r["_ag"] for r in rows) / len(rows)
        avg = hg + ag
        adv = (hg / ag) if ag > 0.2 else 1.12
        return min(max(avg, 2.0), 3.8), min(max(adv, 1.0), 1.40)

    # ----------------------------------------------------------- fixtures --
    async def fixtures(self, day: Optional[date] = None,
                       lookahead: int = 6) -> List[Match]:
        """Build the card starting at `day`.

        Football doesn't play every day — international breaks and Mondays can
        leave 0-2 fixtures, which is not enough to build a 30x ticket honestly.
        So we start at the first day that has games and keep absorbing the next
        match days until the card is big enough (or `lookahead` runs out).
        An accumulator spanning Sat+Sun is completely normal.
        """
        day = day or date.today()
        rows = await self._csv(FIXTURES_URL, "fixtures.csv", ttl=3600)
        if not rows:
            log.error("fixtures.csv unavailable")
            return []

        parsed = []
        for r in rows:
            d = parse_date(r.get("Date", ""))
            if d and d >= day and r.get("Div") in DIVISIONS:
                r["_date"] = d
                parsed.append(r)
        if not parsed:
            log.warning("no upcoming fixtures in fixtures.csv (file may be stale)")
            return []

        days = sorted({r["_date"] for r in parsed})
        first = days[0]
        chosen: List[dict] = []
        for d in days:
            if (d - first).days > lookahead:
                break
            chosen += [r for r in parsed if r["_date"] == d]
            if len(chosen) >= config.MIN_CARD_SIZE:
                break
        day = first

        season = season_code(day)
        divs = {r["Div"] for r in chosen}
        await asyncio.gather(*[self.results(d, season) for d in divs])
        profiles = {d: self._league_profile(await self.results(d, season)) for d in divs}

        matches: List[Match] = []
        for r in chosen:
            div, home_t, away_t = r["Div"], r["HomeTeam"].strip(), r["AwayTeam"].strip()
            mday = r["_date"]
            home = await self._form(div, home_t, mday, season)
            away = await self._form(div, away_t, mday, season)
            if not home or not away:
                continue
            odds = self._odds(r)
            if len(odds) < 3:
                continue
            avg, adv = profiles[div]
            home.home_advantage = adv
            country, league = DIVISIONS[div]
            matches.append(Match(
                # match_id doubles as the settlement key used by engine/grading.py
                match_id=f"{div}|{r['Date'].strip()}|{home_t}|{away_t}",
                league=league, country=country,
                kickoff=(r.get("Time") or "--:--").strip(),
                day=mday.strftime("%a %d %b"),
                home=home, away=away, league_avg_goals=avg, odds=odds,
            ))
        matches.sort(key=lambda m: (m.match_id.split("|")[1][6:], m.match_id.split("|")[1][3:5], m.match_id.split("|")[1][:2], m.kickoff))
        log.info("football-data.co.uk: %d fixtures across %d day(s) from %s",
                 len(matches), len({m.day for m in matches}), day)
        return matches

    @staticmethod
    def _odds(r: dict) -> Dict[str, float]:
        """Price the fixture. PRICE_SOURCE decides whose price we take:

        b365 = one bookmaker (~105% overround)
        max  = best price available anywhere (~101%) - this is line shopping,
               it costs nothing but having accounts at a few books, and it is
               the single biggest real improvement available to a bettor.
        avg  = market average
        """
        src = config.PRICE_SOURCE
        pref = {"max": ("Max", "B365", "Avg"), "avg": ("Avg", "B365", "Max")}.get(
            src, ("B365", "Avg", "Max"))

        def pick(suffix: str, *extra: str):
            cols = [f"{p}{suffix}" for p in pref] + list(extra)
            return _f(r, *cols)

        h, d, a = pick("H", "PSH"), pick("D", "PSD"), pick("A", "PSA")
        o, u = pick(">2.5", "P>2.5"), pick("<2.5", "P<2.5")

        # Double chance needs care. 1/(1/H + 1/D) is only obtainable by
        # DUTCHING home and draw at two different books - impossible inside an
        # accumulator. So DC is always built from ONE book's prices (B365) and
        # given a small haircut, which is what a real DC market looks like.
        bh = _f(r, "B365H", "AvgH", "PSH")
        bd = _f(r, "B365D", "AvgD", "PSD")
        ba = _f(r, "B365A", "AvgA", "PSA")
        hc = 1.0 + config.DC_HAIRCUT

        def dc(x, y):
            if not x or not y:
                return None
            return round(1 / ((1 / x + 1 / y) * hc), 2)

        odds: Dict[str, float] = {}
        if h and d and a:
            odds.update({"1": h, "X": d, "2": a})
        for key, pair in (("1X", (bh, bd)), ("X2", (bd, ba)), ("12", (bh, ba))):
            v = dc(*pair)
            if v:
                odds[key] = v
        if o and u:
            odds.update({"O2.5": o, "U2.5": u})
        return {k: v for k, v in odds.items() if v and v > 1.0}

    async def close(self) -> None:
        await self._c.aclose()
