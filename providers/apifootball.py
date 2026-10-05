"""API-Football v3 (api-sports.io or RapidAPI) — live fixtures, odds and form.

FREE PLAN = 100 requests/day, every endpoint, no credit card.
This provider is built to live inside that budget:

  1   x /fixtures?date=...                  the whole day's card
  L   x /standings?league&season            goals for/against  (cached 12h)
  L   x /odds?date&league&season&bookmaker  prices             (cached 2h)

With the default 8 leagues that is ~17 requests per run, so you can run it
several times a day and still never hit the cap. Every response is cached on
disk, a hard daily budget is enforced locally, and the quota headers the API
returns are logged so you always know where you stand.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import httpx

import config
from providers.base import BaseProvider, Match, TeamForm

log = logging.getLogger(__name__)

BET_WINNER, BET_OU, BET_BTTS, BET_DC = 1, 5, 8, 12

VALUE_MAP = {
    (BET_WINNER, "home"): "1", (BET_WINNER, "draw"): "X", (BET_WINNER, "away"): "2",
    (BET_DC, "home/draw"): "1X", (BET_DC, "draw/away"): "X2", (BET_DC, "home/away"): "12",
    (BET_BTTS, "yes"): "BTTS", (BET_BTTS, "no"): "NOBTTS",
    (BET_OU, "over 1.5"): "O1.5", (BET_OU, "under 1.5"): "U1.5",
    (BET_OU, "over 2.5"): "O2.5", (BET_OU, "under 2.5"): "U2.5",
    (BET_OU, "over 3.5"): "O3.5", (BET_OU, "under 3.5"): "U3.5",
}

PREFIX = "AF"          # match_id namespace, so settlement knows who to ask
LEAGUE_AVG_FALLBACK = 2.70


def season_for(day: date) -> int:
    """European seasons are labelled by their starting year."""
    return day.year if day.month >= 7 else day.year - 1


class QuotaExceeded(RuntimeError):
    pass


class PlanDateLimited(RuntimeError):
    """The free plan only serves a narrow date window (today +/- 1 day).

    Not an error in our setup - just the edge of what the plan allows, so we
    stop scanning further days instead of failing the whole run.
    """


class APIFootballProvider(BaseProvider):
    name = "API-Football (live)"

    def __init__(self, key: str = "", host: str = ""):
        self.key = key or config.APIFOOTBALL_KEY
        self.host = host or config.APIFOOTBALL_HOST
        if not self.key:
            raise RuntimeError(
                "APIFOOTBALL_KEY is not set. Get a free key at "
                "https://dashboard.api-football.com (100 requests/day, no card)."
            )
        headers = (
            {"x-rapidapi-key": self.key, "x-rapidapi-host": self.host}
            if "rapidapi" in self.host
            else {"x-apisports-key": self.key}
        )
        self._c = httpx.AsyncClient(
            base_url=f"https://{self.host}", headers=headers, timeout=30.0
        )
        self.cache_dir = config.DATA_DIR / "cache" / "apifootball"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.usage_file = config.DATA_DIR / "api_usage.json"
        self.calls_this_run = 0
        self.remaining: Optional[str] = None

    # ------------------------------------------------------------- budget --
    def _usage(self) -> dict:
        today = date.today().isoformat()
        try:
            u = json.loads(self.usage_file.read_text())
        except Exception:  # noqa: BLE001
            u = {}
        if u.get("date") != today:
            u = {"date": today, "count": 0}
        return u

    def _bump(self) -> None:
        u = self._usage()
        u["count"] += 1
        self.usage_file.write_text(json.dumps(u))
        self.calls_this_run += 1

    def used_today(self) -> int:
        return self._usage()["count"]

    # --------------------------------------------------------------- http --
    async def _get(self, path: str, cache_key: str = "", ttl: int = 0,
                   **params) -> list:
        """GET with on-disk cache and a local daily budget guard."""
        cache_path = self.cache_dir / f"{cache_key}.json" if cache_key else None
        if cache_path and cache_path.exists() and ttl:
            if (time.time() - cache_path.stat().st_mtime) < ttl:
                try:
                    return json.loads(cache_path.read_text())
                except json.JSONDecodeError:
                    pass

        if self.used_today() >= config.APIFOOTBALL_DAILY_BUDGET:
            raise QuotaExceeded(
                f"local daily budget of {config.APIFOOTBALL_DAILY_BUDGET} "
                f"requests reached (free plan allows 100)"
            )

        try:
            r = await self._c.get(path, params=params)
            self._bump()
            self.remaining = r.headers.get("x-ratelimit-requests-remaining")
            if r.status_code == 429:
                raise QuotaExceeded("API-Football returned 429 (rate limited)")
            r.raise_for_status()
            body = r.json()
        except QuotaExceeded:
            raise
        except Exception as exc:  # noqa: BLE001
            log.error("API-Football %s %s failed: %s", path, params, exc)
            if cache_path and cache_path.exists():
                log.warning("serving stale cache for %s", cache_key)
                return json.loads(cache_path.read_text())
            return []

        errors = body.get("errors")
        if errors:
            # API-Football returns {} when fine, and a dict/list of messages when not
            if isinstance(errors, dict) and errors:
                msg = "; ".join(f"{k}: {v}" for k, v in errors.items())
                low = msg.lower()
                # the free plan's date window is a limit, NOT a bad key
                if "access to this date" in low or "try from" in low:
                    raise PlanDateLimited(msg)
                if any(w in low for w in ("token", "key", "invalid api")):
                    raise RuntimeError(f"API-Football rejected the key — {msg}")
                log.warning("API-Football %s -> %s", path, msg)
            elif isinstance(errors, list) and errors:
                log.warning("API-Football %s -> %s", path, errors)

        resp = body.get("response", []) or []
        if cache_path:
            cache_path.write_text(json.dumps(resp))
        return resp

    async def _paged(self, path: str, cache_key: str, ttl: int, max_pages: int = 6,
                     **params) -> list:
        cache_path = self.cache_dir / f"{cache_key}.json"
        if cache_path.exists() and ttl and (time.time() - cache_path.stat().st_mtime) < ttl:
            try:
                return json.loads(cache_path.read_text())
            except json.JSONDecodeError:
                pass

        out: list = []
        page, total = 1, 1
        while page <= total and page <= max_pages:
            if self.used_today() >= config.APIFOOTBALL_DAILY_BUDGET:
                log.warning("stopping pagination: daily budget reached")
                break
            try:
                r = await self._c.get(path, params={**params, "page": page})
                self._bump()
                self.remaining = r.headers.get("x-ratelimit-requests-remaining")
                r.raise_for_status()
                body = r.json()
            except Exception as exc:  # noqa: BLE001
                log.error("API-Football %s page %d failed: %s", path, page, exc)
                break
            out += body.get("response", []) or []
            total = (body.get("paging") or {}).get("total", 1)
            page += 1
            if page <= total:
                await asyncio.sleep(0.4)       # stay under 10 req/min
        cache_path.write_text(json.dumps(out))
        return out

    # ------------------------------------------------------- form (1/league)
    async def standings(self, league_id: int, season: int) -> Dict[str, TeamForm]:
        resp = await self._get(
            "/standings", cache_key=f"standings_{league_id}_{season}",
            ttl=config.APIFOOTBALL_STANDINGS_TTL, league=league_id, season=season,
        )
        table: Dict[str, TeamForm] = {}
        for lg in resp:
            for group in (lg.get("league", {}).get("standings") or []):
                for row in group:
                    all_ = row.get("all") or {}
                    played = max(all_.get("played") or 0, 0)
                    if played < 1:
                        continue
                    goals = all_.get("goals") or {}
                    tid = str((row.get("team") or {}).get("id"))
                    table[tid] = TeamForm(
                        name=(row.get("team") or {}).get("name", ""),
                        goals_for=round((goals.get("for") or 0) / played, 3),
                        goals_against=round((goals.get("against") or 0) / played, 3),
                        matches=played,
                    )
        return table

    @staticmethod
    def _profile(table: Dict[str, TeamForm]) -> Tuple[float, float]:
        if not table:
            return LEAGUE_AVG_FALLBACK, 1.12
        avg = sum(t.goals_for for t in table.values()) / len(table) * 2
        return min(max(avg, 2.0), 3.8), 1.12

    # ------------------------------------------------------ odds (1/league)
    async def odds_for(self, day: date, league_id: int, season: int) -> Dict[str, Dict[str, float]]:
        resp = await self._paged(
            "/odds", cache_key=f"odds_{day.isoformat()}_{league_id}",
            ttl=config.APIFOOTBALL_ODDS_TTL, max_pages=4,
            date=day.isoformat(), league=league_id, season=season,
            bookmaker=config.BOOKMAKER_ID,
        )
        out: Dict[str, Dict[str, float]] = {}
        for item in resp:
            fid = str((item.get("fixture") or {}).get("id"))
            markets = out.setdefault(fid, {})
            for bk in (item.get("bookmakers") or []):
                for bet in (bk.get("bets") or []):
                    bid = bet.get("id")
                    for v in (bet.get("values") or []):
                        key = VALUE_MAP.get((bid, str(v.get("value", "")).strip().lower()))
                        if not key:
                            continue
                        try:
                            o = float(v.get("odd"))
                        except (TypeError, ValueError):
                            continue
                        if o > 1.0:
                            markets[key] = o
        return out

    # ---------------------------------------------------------- fixtures ---
    async def _day_fixtures(self, day: date) -> list:
        """Upcoming fixtures on one date, filtered to the leagues we want."""
        raw = await self._get(
            "/fixtures", cache_key=f"fixtures_{day.isoformat()}",
            ttl=config.APIFOOTBALL_FIXTURES_TTL,
            date=day.isoformat(), timezone=config.TIMEZONE,
        )
        wanted = set(config.LEAGUE_IDS)
        return [
            f for f in raw
            if (not wanted or (f.get("league") or {}).get("id") in wanted)
            and ((f.get("fixture") or {}).get("status") or {}).get("short") in ("NS", "TBD")
        ]

    async def fixtures(self, day: Optional[date] = None,
                       lookahead: Optional[int] = None) -> List[Match]:
        """Build a card starting at `day`, rolling forward on empty days.

        Football doesn't play every day. Rather than returning nothing during
        an international break, scan forward for the next real match day(s).
        One /fixtures request per day scanned, and we stop as soon as the card
        is big enough — so on a normal Friday this costs a single request.
        """
        start = day or date.today()
        lookahead = config.APIFOOTBALL_LOOKAHEAD if lookahead is None else lookahead

        raw: list = []
        days_used: List[date] = []
        first_hit: Optional[int] = None
        for offset in range(lookahead + 1):
            d = start + timedelta(days=offset)
            try:
                todays = await self._day_fixtures(d)
            except PlanDateLimited as exc:
                log.info("API-Football: %s is outside the plan's date window "
                         "(%s) — stopping the scan here", d, exc)
                break
            except QuotaExceeded as exc:
                log.warning("quota while scanning days: %s", exc)
                break
            if todays:
                raw += todays
                days_used.append(d)
                if first_hit is None:
                    first_hit = offset
            if first_hit is None:
                continue
            # we have a match day: take at most one more, then stop burning
            # a request per day on an empty horizon
            if (len(raw) >= config.MIN_CARD_SIZE
                    or len(days_used) >= 2
                    or offset >= first_hit + 1):
                break

        if not raw:
            log.info("API-Football: no fixtures in the next %d days "
                     "for the chosen leagues", lookahead)
            return []

        day = days_used[0]
        season = season_for(day)
        if days_used != [start]:
            log.info("API-Football: card built from %s (%d fixtures)",
                     ", ".join(d.isoformat() for d in days_used), len(raw))

        # Spend our request budget where the fixtures actually are: rank
        # leagues by how many games they have today, keep the top N.
        counts = Counter(f["league"]["id"] for f in raw)
        leagues = [lid for lid, _ in counts.most_common(config.APIFOOTBALL_MAX_LEAGUES)]
        if len(counts) > len(leagues):
            log.info("API-Football: %d leagues today, pulling odds for the "
                     "busiest %d (request budget)", len(counts), len(leagues))
        tables, odds_maps = {}, {}
        for lid in leagues:
            try:
                tables[lid] = await self.standings(lid, season)
                merged: Dict[str, Dict[str, float]] = {}
                for d in days_used:
                    try:
                        merged.update(await self.odds_for(d, lid, season))
                    except PlanDateLimited:
                        pass
                odds_maps[lid] = merged
            except QuotaExceeded as exc:
                log.warning("quota: %s — using what we have", exc)
                break

        matches: List[Match] = []
        for f in raw:
            lid = f["league"]["id"]
            table, odds_map = tables.get(lid, {}), odds_maps.get(lid, {})
            fid = str(f["fixture"]["id"])
            odds = odds_map.get(fid, {})
            if not odds:
                continue
            hid = str(f["teams"]["home"]["id"])
            aid = str(f["teams"]["away"]["id"])
            home, away = table.get(hid), table.get(aid)
            if not home or not away or home.matches < 3 or away.matches < 3:
                continue          # not enough played games to model it honestly
            avg, adv = self._profile(table)
            home = TeamForm(**{**home.__dict__, "home_advantage": adv})
            when = (f["fixture"].get("date") or "")
            mday = when[:10] or day.isoformat()
            try:
                mdate = date.fromisoformat(mday)
            except ValueError:
                mdate = day
            matches.append(Match(
                match_id=f"{PREFIX}|{fid}|{mday}|"
                         f"{f['teams']['home']['name']} vs {f['teams']['away']['name']}",
                league=f["league"].get("name", ""),
                country=f["league"].get("country", ""),
                kickoff=when[11:16] or "--:--",
                day=mdate.strftime("%a %d %b"),
                home=home, away=away, league_avg_goals=avg, odds=odds,
            ))

        matches.sort(key=lambda m: (m.match_id.split("|")[2], m.kickoff))
        log.info("API-Football: %d fixtures, %d requests used this run "
                 "(%s left today per the API)",
                 len(matches), self.calls_this_run, self.remaining or "?")
        return matches

    # -------------------------------------------------------- settlement ---
    async def results(self, fixture_ids: List[str]) -> Dict[str, Tuple[int, int]]:
        """Final scores for settling past tickets. 20 fixtures per request."""
        out: Dict[str, Tuple[int, int]] = {}
        ids = [i for i in dict.fromkeys(fixture_ids) if i]
        for i in range(0, len(ids), 20):
            chunk = ids[i:i + 20]
            resp = await self._get("/fixtures", ids="-".join(chunk))
            for f in resp:
                status = ((f.get("fixture") or {}).get("status") or {}).get("short")
                if status not in ("FT", "AET", "PEN"):
                    continue
                goals = f.get("goals") or {}
                if goals.get("home") is None or goals.get("away") is None:
                    continue
                out[str(f["fixture"]["id"])] = (int(goals["home"]), int(goals["away"]))
        return out

    async def account_status(self, ttl: int = 600) -> dict:
        """Plan + quota straight from the API. Cached so /status can't drain it."""
        cache = self.cache_dir / "account_status.json"
        if cache.exists() and (time.time() - cache.stat().st_mtime) < ttl:
            try:
                return json.loads(cache.read_text())
            except json.JSONDecodeError:
                pass
        try:
            r = await self._c.get("/status")
            self.remaining = r.headers.get("x-ratelimit-requests-remaining")
            body = r.json()
            errs = body.get("errors") or {}
            if errs:
                return {"error": "; ".join(f"{k}: {v}" for k, v in errs.items())
                        if isinstance(errs, dict) else str(errs)}
            resp = body.get("response") or {}
            out = {
                "plan": (resp.get("subscription") or {}).get("plan", "?"),
                "active": (resp.get("subscription") or {}).get("active"),
                "used": (resp.get("requests") or {}).get("current"),
                "limit": (resp.get("requests") or {}).get("limit_day"),
            }
            cache.write_text(json.dumps(out))
            return out
        except Exception as exc:  # noqa: BLE001
            return {"error": str(exc)}

    async def close(self) -> None:
        await self._c.aclose()
