#!/usr/bin/env python3
"""Backtest the strategy on real historical matches and real pre-match odds.

football-data.co.uk publishes, for every played match, the final score AND the
bookmaker odds that were available before kick-off. So we can replay the bot
day by day over past seasons and measure what it would actually have returned.

No look-ahead bias: team form and league profile for a given match day are
computed only from matches played strictly BEFORE that day.

    python backtest.py                        # last season, default settings
    python backtest.py --seasons 2425 2526
    python backtest.py --target 50            # 50x bomb instead of 30x
    python backtest.py --min-edge 0.03        # only bet real value
"""
from __future__ import annotations

import argparse
import asyncio
import statistics
from collections import defaultdict
from datetime import date
from typing import Dict, List, Tuple

import config
from engine import build_bomb, build_safe, build_selections
from engine.grading import settle_ticket, summarise
from providers.base import Match
from providers.footballdata_uk import DIVISIONS, FootballDataUKProvider, _f

DEFAULT_DIVS = ["E0", "E1", "SP1", "I1", "D1", "F1", "N1", "P1", "T1", "B1", "SC0", "G1"]


class HistoricalProvider(FootballDataUKProvider):
    """Rebuilds the card for any past date out of the results files."""

    name = "football-data.co.uk (historical)"

    def __init__(self, divisions: List[str], season: str):
        super().__init__()
        self.divisions = divisions
        self.season = season
        self.by_day: Dict[date, List[Tuple[str, dict]]] = defaultdict(list)
        self.scores: Dict[str, Tuple[int, int]] = {}

    async def load(self) -> None:
        for div in self.divisions:
            rows = await super().results(div, self.season)
            for r in rows:
                self.by_day[r["_date"]].append((div, r))
                key = f"{div}|{r['Date'].strip()}|{r['HomeTeam'].strip()}|{r['AwayTeam'].strip()}"
                self.scores[key] = (r["_hg"], r["_ag"])

    async def card(self, day: date) -> List[Match]:
        out: List[Match] = []
        divs_today = {d for d, _ in self.by_day.get(day, [])}
        profiles = {}
        for d in divs_today:
            prior = [r for r in await super().results(d, self.season) if r["_date"] < day]
            profiles[d] = self._league_profile(prior)

        for div, r in self.by_day.get(day, []):
            home_t, away_t = r["HomeTeam"].strip(), r["AwayTeam"].strip()
            home = await self._form(div, home_t, day, self.season)
            away = await self._form(div, away_t, day, self.season)
            if not home or not away or home.matches < 4 or away.matches < 4:
                continue
            odds = self._odds(r)
            if len(odds) < 3:
                continue
            avg, adv = profiles[div]
            home.home_advantage = adv
            country, league = DIVISIONS[div]
            out.append(Match(
                match_id=f"{div}|{r['Date'].strip()}|{home_t}|{away_t}",
                league=league, country=country,
                kickoff=(r.get("Time") or "--:--").strip(),
                day=day.strftime("%a %d %b"),
                home=home, away=away, league_avg_goals=avg, odds=odds,
            ))
        return out


def bar(x: float, width: int = 28) -> str:
    n = int(max(min(x, 1.0), 0.0) * width)
    return "#" * n + "." * (width - n)


async def run(seasons: List[str], divisions: List[str], target: float,
              min_edge: float, verbose: bool, quiet: bool = False) -> None:
    config.MIN_EDGE = min_edge
    overall = {"safe": [], "bomb": []}
    equity_curve: List[float] = []

    for season in seasons:
        prov = HistoricalProvider(divisions, season)
        await prov.load()
        days = sorted(prov.by_day)
        if not days:
            print(f"season {season}: no data")
            continue
        # skip the first 5 weeks so form estimates are meaningful
        days = [d for d in days if (d - days[0]).days >= 35]

        graded = {"safe": [], "bomb": []}
        for day in days:
            matches = await prov.card(day)
            if len(matches) < 6:            # too thin to build a real ticket
                continue
            sels = build_selections(matches)
            if not sels:
                continue
            for kind, ticket in (("safe", build_safe(sels)),
                                 ("bomb", build_bomb(sels, target))):
                if not ticket or len(ticket.legs) < 2:
                    continue
                if kind == "bomb" and ticket.total_odds < target * 0.9:
                    continue
                g = settle_ticket(ticket.as_dict(), prov.scores)
                g["date"], g["odds"] = day.isoformat(), round(ticket.total_odds, 2)
                g["model_prob"] = round(ticket.combined_prob, 4)
                graded[kind].append(g)
                if kind == "bomb":
                    equity_curve.append(g["profit"])
                if verbose and g["status"] == "won" and kind == "bomb":
                    print(f"    💥 {day} BOMB LANDED @ {g['odds']:.2f}")

        if not quiet:
            print(f"\n{'='*66}\nSEASON 20{season[:2]}/{season[2:]}   "
                  f"{len(divisions)} leagues   {len(days)} match days")
        for kind in ("safe", "bomb"):
            s = summarise(graded[kind])
            overall[kind] += graded[kind]
            if quiet:
                continue
            if not s["n"]:
                print(f"  {kind.upper():5} no tickets")
                continue
            odds = [g["odds"] for g in graded[kind]]
            exp = statistics.mean(g["model_prob"] for g in graded[kind])
            print(f"  {kind.upper():5} {s['n']:4} tickets | avg odds {statistics.mean(odds):7.2f} | "
                  f"won {s['won']:3} ({s['hit_rate']*100:5.2f}%) | "
                  f"model said {exp*100:5.2f}% | "
                  f"P/L {s['profit']:+8.2f}u | ROI {s['roi']*100:+7.2f}% | "
                  f"maxDD {s['max_drawdown']:.1f}u")
        await prov.close()

    if quiet:
        for kind in ("safe", "bomb"):
            g = overall[kind]
            s = summarise(g)
            if not s["n"]:
                print(f"{kind:5} no tickets"); continue
            exp = statistics.mean(x["model_prob"] for x in g)
            print(f"{kind:5} n={s['n']:4} odds={statistics.mean([x['odds'] for x in g]):7.2f} "
                  f"pred={exp*100:6.2f}% act={s['hit_rate']*100:6.2f}% "
                  f"calib={(s['hit_rate']/exp if exp else 0):5.2f}x "
                  f"ROI={s['roi']*100:+7.2f}%")
        return

    print(f"\n{'='*66}\nOVERALL ({len(seasons)} season(s), 1 unit flat stake per ticket)")
    for kind in ("safe", "bomb"):
        s = summarise(overall[kind])
        if not s["n"]:
            continue
        print(f"\n  {kind.upper()} TICKET")
        print(f"    tickets      {s['n']}")
        print(f"    hit rate     {s['hit_rate']*100:.2f}%   [{bar(s['hit_rate'])}]")
        print(f"    staked       {s['staked']}u")
        print(f"    profit       {s['profit']:+.2f}u")
        print(f"    ROI          {s['roi']*100:+.2f}% per bet")
        print(f"    worst run    {s['max_drawdown']:.2f}u drawdown")
        print(f"    biggest win  {s['best']:+.2f}u")

    print("\n" + "-" * 66)
    print("Reminder: ROI below 0 means the strategy loses money at these settings.")
    print("Tune with --min-edge / --target and re-run before betting anything.")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seasons", nargs="+", default=["2526"])
    ap.add_argument("--divisions", nargs="+", default=DEFAULT_DIVS)
    ap.add_argument("--target", type=float, default=config.TARGET_ODDS)
    ap.add_argument("--min-edge", type=float, default=-1.0)
    ap.add_argument("-v", "--verbose", action="store_true")
    ap.add_argument("-q", "--quiet", action="store_true", help="one line per ticket type")
    ap.add_argument("--model-weight", type=float, default=None)
    ap.add_argument("--safe-min-prob", type=float, default=None)
    ap.add_argument("--max-leg-odds", type=float, default=None)
    ap.add_argument("--devig", default=None, choices=["power", "proportional"])
    ap.add_argument("--price", default=None, choices=["b365", "max", "avg"])
    ap.add_argument("--safe-markets", default=None)
    a = ap.parse_args()
    if a.model_weight is not None:
        config.MODEL_WEIGHT = a.model_weight
    if a.safe_min_prob is not None:
        config.SAFE_MIN_PROB = a.safe_min_prob
    if a.max_leg_odds is not None:
        config.MAX_LEG_ODDS = a.max_leg_odds
    if a.devig is not None:
        config.DEVIG_METHOD = a.devig
    if a.price is not None:
        config.PRICE_SOURCE = a.price
    if a.safe_markets is not None:
        config.SAFE_MARKETS = [m for m in a.safe_markets.split(",") if m]
    asyncio.run(run(a.seasons, a.divisions, a.target, a.min_edge, a.verbose, a.quiet))


if __name__ == "__main__":
    main()
