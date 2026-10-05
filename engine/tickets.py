"""Ticket construction.

SAFE ticket  -> maximise combined probability, 3-5 legs, short odds.
BOMB ticket  -> reach TARGET_ODDS (default 30.00) while giving away as little
                probability as possible.

The bomb is a constrained optimisation:
    maximise  sum(log p_i)      subject to   sum(log o_i) >= log(30)
Sorting candidates by  -log(p)/log(o)  (`cost_ratio`) and taking the cheapest
first is the greedy solution to that fractional problem; a pruning pass then
removes any leg that is no longer needed. In practice this lands within a
fraction of a percent of the exact DP optimum but runs instantly.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from math import log, prod
from typing import Dict, List, Optional, Tuple

import config
from engine.markets import Selection


@dataclass
class Ticket:
    kind: str                      # "SAFE" | "BOMB"
    legs: List[Selection] = field(default_factory=list)

    @property
    def total_odds(self) -> float:
        return prod(s.odds for s in self.legs) if self.legs else 0.0

    @property
    def combined_prob(self) -> float:
        return prod(s.prob for s in self.legs) if self.legs else 0.0

    @property
    def ev(self) -> float:
        """Expected value per 1 unit staked on the whole ticket."""
        return self.combined_prob * self.total_odds - 1.0

    @property
    def fair_odds(self) -> float:
        return 1 / self.combined_prob if self.combined_prob else 0.0

    def as_dict(self) -> Dict:
        return {
            "kind": self.kind,
            "total_odds": round(self.total_odds, 2),
            "combined_prob": round(self.combined_prob, 4),
            "ev": round(self.ev, 4),
            "legs": [
                {
                    "match_id": s.match_id,          # settlement key
                    "match": s.label, "league": s.league,
                    "kickoff": s.kickoff, "day": s.day,
                    "market": s.market, "market_name": s.market_name,
                    "odds": s.odds, "prob": round(s.prob, 4),
                    "edge": round(s.edge, 4),
                    "xg": f"{s.xg_home:.2f}-{s.xg_away:.2f}",
                }
                for s in self.legs
            ],
        }


def _best_per_match(sels: List[Selection], key) -> List[Selection]:
    """At most one selection per fixture (legs of an acca must be independent)."""
    best: Dict[str, Selection] = {}
    for s in sels:
        cur = best.get(s.match_id)
        if cur is None or key(s) > key(cur):
            best[s.match_id] = s
    return list(best.values())


# ------------------------------------------------------------------ SAFE ----
def build_safe(sels: List[Selection]) -> Optional[Ticket]:
    if not sels:
        return None
    allowed = set(config.SAFE_MARKETS)
    usable = [s for s in sels if not allowed or s.market in allowed] or sels
    score = lambda s: s.prob * (1 + max(s.edge, 0) * 3)   # noqa: E731
    ranked = sorted(_best_per_match(usable, score), key=score, reverse=True)

    strong = [s for s in ranked if s.prob >= config.SAFE_MIN_PROB]
    # A "safe" ticket must be built from safe legs. If there aren't enough of
    # them today, build a SHORTER ticket - never pad it with risky picks.
    if len(strong) >= 2:
        pool, legs_cap = strong, min(config.SAFE_MAX_LEGS, len(strong))
    else:
        pool, legs_cap = ranked, 2
    legs = pool[:legs_cap]
    # trim the weakest leg while the ticket is worse than a coin flip
    while len(legs) > config.SAFE_MIN_LEGS and prod(s.prob for s in legs) < 0.45:
        legs.pop()
    # a 1.25 "safe" ticket is not worth a slip: extend it while it stays likely
    i = len(legs)
    while (len(legs) < legs_cap
           and prod(s.odds for s in legs) < config.SAFE_MIN_TOTAL_ODDS
           and i < len(pool)):
        cand = pool[i]
        i += 1
        if prod(s.prob for s in legs) * cand.prob >= 0.42:
            legs.append(cand)

    legs.sort(key=lambda s: s.kickoff)
    return Ticket("SAFE", legs) if len(legs) >= 2 else None


# ------------------------------------------------------------------ BOMB ----
BUCKETS = 240  # resolution of the odds axis in the DP


def build_bomb(sels: List[Selection], target: Optional[float] = None) -> Optional[Ticket]:
    """Exact-ish solution to:  maximise Σ log(pᵢ)  s.t.  Σ log(oᵢ) ≥ log(target).

    Why this matters. If a book charges margin m on every leg, a k-leg
    accumulator priced at `target` returns (1+m)^-k - 1 in expectation. The
    margin compounds **per leg**, so three legs at 3.15 are strictly better
    value than seven legs at 1.65 even though both pay 30x. Maximising
    Σ log p automatically discovers that and keeps the ticket short.

    Solved as a multiple-choice knapsack: each fixture contributes at most one
    leg, the odds axis is discretised into BUCKETS steps, and anything past the
    target collapses into the final bucket (we only need to *reach* 30.00).
    """
    target = target or config.TARGET_ODDS
    pool = [s for s in sels
            if s.prob >= config.BOMB_MIN_PROB and s.odds <= config.MAX_LEG_ODDS]
    if not pool:
        return None

    by_match: Dict[str, List[Selection]] = {}
    for s in pool:
        by_match.setdefault(s.match_id, []).append(s)
    # keep only the two most efficient markets per fixture: smaller search,
    # same answer in practice
    groups = [sorted(v, key=lambda s: s.cost_ratio)[:2] for v in by_match.values()]
    if not groups:
        return None

    L = log(target)
    step = L / BUCKETS
    NEG = float("-inf")
    # dp[b] = (best Σlog p, legs) for "odds bucket b"; bucket BUCKETS == target met
    dp: List[Tuple[float, List[Selection]]] = [(NEG, []) for _ in range(BUCKETS + 1)]
    dp[0] = (0.0, [])

    for group in groups:
        nxt = list(dp)
        for b in range(BUCKETS + 1):
            val, legs = dp[b]
            if val == NEG or len(legs) >= config.BOMB_MAX_LEGS:
                continue
            for s in group:
                nb = min(BUCKETS, b + int(log(s.odds) / step))
                nv = val + log(s.prob)
                if nv > nxt[nb][0]:
                    nxt[nb] = (nv, legs + [s])
        dp = nxt

    val, legs = dp[BUCKETS]
    if val == NEG or not legs:
        # target unreachable with today's card: give the best we can actually get
        best = max(
            (d for d in dp if d[0] != NEG and d[1]),
            key=lambda d: prod(s.odds for s in d[1]), default=None,
        )
        if not best:
            return None
        legs = best[1]

    # bucket rounding can leave us a hair under the target - top the ticket up
    if prod(s.odds for s in legs) < target:
        used = {s.match_id for s in legs}
        spare = sorted((s for s in pool if s.match_id not in used),
                       key=lambda s: -s.prob)
        for s in spare:
            if prod(x.odds for x in legs) >= target:
                break
            if len(legs) >= config.BOMB_MAX_LEGS:
                break
            legs = legs + [s]
            used.add(s.match_id)

    # and drop anything that turns out to be redundant
    changed = True
    while changed and len(legs) > 1:
        changed = False
        for s in sorted(legs, key=lambda x: x.prob):
            rest = [x for x in legs if x is not s]
            if rest and prod(x.odds for x in rest) >= target:
                legs, changed = rest, True
                break

    legs.sort(key=lambda s: (s.day, s.kickoff))
    return Ticket("BOMB", legs) if legs else None


def build_all(sels: List[Selection]) -> Dict[str, Optional[Ticket]]:
    return {"safe": build_safe(sels), "bomb": build_bomb(sels)}
