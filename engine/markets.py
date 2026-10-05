"""Turn fixtures into a pool of priced, rated selections."""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List

import config
from engine.poisson import MARKET_NAMES, lambdas, market_probabilities, score_matrix
from providers.base import Match


@dataclass
class Selection:
    match_id: str
    label: str            # "Arsenal vs Everton"
    league: str
    kickoff: str
    day: str              # "Sat 10 Oct" (blank when the card is a single day)
    market: str           # "1X"
    market_name: str      # "Home or Draw (DC)"
    prob: float           # our model's probability
    odds: float           # bookmaker decimal odds
    xg_home: float
    xg_away: float

    @property
    def implied(self) -> float:
        return 1.0 / self.odds

    @property
    def edge(self) -> float:
        """Value edge: how much our probability beats the book's implied one."""
        return self.prob - self.implied

    @property
    def ev(self) -> float:
        """Expected return per 1 unit staked."""
        return self.prob * self.odds - 1.0

    @property
    def cost_ratio(self) -> float:
        """-ln(p) / ln(odds): 'risk paid per unit of odds gained'.

        == 1.0 for a perfectly fair price, < 1.0 when the bet has value.
        Sorting by this builds the cheapest possible 30x accumulator.
        """
        if self.odds <= 1.0 or self.prob <= 0:
            return 99.0
        return (-math.log(self.prob)) / math.log(self.odds)

    @property
    def stars(self) -> str:
        c = self.prob * 0.75 + min(max(self.edge, 0), 0.2) * 1.25
        return "*" * max(1, min(5, int(round(c * 5))))


GROUPS = [
    (["1", "X", "2"], 1.0),
    (["O1.5", "U1.5"], 1.0),
    (["O2.5", "U2.5"], 1.0),
    (["O3.5", "U3.5"], 1.0),
    (["BTTS", "NOBTTS"], 1.0),
]


def _power_devig(raw: Dict[str, float]) -> Dict[str, float]:
    """Remove the margin with the power method:  find k so that Σ pᵢ^k = 1.

    Proportional de-vigging (just dividing by the overround) assumes the book
    loads its margin evenly. It doesn't: long shots carry far more of it
    (the favourite-long-shot bias). The power method strips proportionally
    more margin from big prices, which is what the data actually shows.
    """
    lo, hi = 0.5, 2.0
    for _ in range(60):
        k = (lo + hi) / 2
        s = sum(p ** k for p in raw.values())
        if s > 1.0:
            lo = k
        else:
            hi = k
    k = (lo + hi) / 2
    out = {m: p ** k for m, p in raw.items()}
    t = sum(out.values()) or 1.0
    return {m: v / t for m, v in out.items()}


def devig(odds: Dict[str, float]) -> Dict[str, float]:
    """The bookmaker's true opinion, with its margin removed.

    Odds imply probabilities that sum to >100% (the vig). Normalising each
    market group back to 100% recovers what the book actually thinks. That
    opinion is sharp — it aggregates far more information than our model — so
    we use it as a prior rather than pretending to beat it outright.
    """
    out: Dict[str, float] = {}
    for keys, norm in GROUPS:
        have = [k for k in keys if k in odds and odds[k] > 1.0]
        if len(have) < 2:
            continue
        raw = {k: 1.0 / odds[k] for k in have}
        s = sum(raw.values())
        if s <= 0:
            continue
        if config.DEVIG_METHOD == "power":
            fair = _power_devig(raw)
        else:
            fair = {k: v / s for k, v in raw.items()}
        for k in have:
            out[k] = fair[k] * norm
    # double chance follows from the de-vigged 1X2
    if {"1", "X", "2"} <= out.keys():
        out["1X"] = out["1"] + out["X"]
        out["X2"] = out["X"] + out["2"]
        out["12"] = out["1"] + out["2"]
    return out


def build_selections(matches: List[Match]) -> List[Selection]:
    out: List[Selection] = []
    w = config.MODEL_WEIGHT
    for m in matches:
        lh, la = lambdas(
            m.home.goals_for, m.home.goals_against,
            m.away.goals_for, m.away.goals_against,
            league_avg=m.league_avg_goals, home_adv=m.home.home_advantage,
            n_home=m.home.matches, n_away=m.away.matches,
        )
        model = market_probabilities(score_matrix(lh, la, config.MAX_GOALS))
        market = devig(m.odds)
        # blended estimate: mostly the market, nudged by our model
        probs = {
            k: (w * model[k] + (1 - w) * market[k]) if k in market else model[k]
            for k in model
        }
        for market, odds in m.odds.items():
            if market not in probs:
                continue
            if not (config.MIN_LEG_ODDS <= odds <= config.MAX_LEG_ODDS):
                continue
            sel = Selection(
                match_id=m.match_id, label=m.label, league=f"{m.country} - {m.league}",
                kickoff=m.kickoff, day=m.day, market=market,
                market_name=MARKET_NAMES.get(market, market),
                prob=probs[market], odds=odds, xg_home=lh, xg_away=la,
            )
            if sel.edge >= config.MIN_EDGE:
                out.append(sel)
    return out
