"""Bivariate-Poisson-ish scoreline model.

Classic Dixon-Coles style approach, simplified:

  lambda_home = league_avg/2 * attack(home) * defence(away) * home_advantage
  lambda_away = league_avg/2 * attack(away) * defence(home)

attack  = (goals scored per game) / (league average goals per team per game)
defence = (goals conceded per game) / (league average goals per team per game)

From the two lambdas we build a score matrix P(i,j) and read every market
off it, so all our probabilities are internally consistent.
A low-score correlation correction (rho) is applied to 0-0/1-0/0-1/1-1,
which is where the independent-Poisson assumption is known to be wrong.
"""
from __future__ import annotations

import math
from functools import lru_cache
from typing import Dict, List

RHO = -0.03  # Dixon-Coles low-score correction


@lru_cache(maxsize=4096)
def _pois(k: int, lam: float) -> float:
    return math.exp(-lam) * lam**k / math.factorial(k)


def _dc_tau(i: int, j: int, lh: float, la: float, rho: float) -> float:
    if i == 0 and j == 0:
        return 1 - lh * la * rho
    if i == 0 and j == 1:
        return 1 + lh * rho
    if i == 1 and j == 0:
        return 1 + la * rho
    if i == 1 and j == 1:
        return 1 - rho
    return 1.0


SHRINK_K = 7.0   # pseudo-matches of "league average" mixed into every team


def _shrink(ratio: float, n: int, k: float = SHRINK_K) -> float:
    """Regress a strength ratio toward 1.0 (the league average).

    Eight good games does NOT make a team twice as strong as average — it
    mostly makes it lucky. Without this, the model produces absurd
    probabilities (it claimed 30% on tickets that landed 1.4% of the time).
    """
    w = n / (n + k)
    return 1.0 + (ratio - 1.0) * w


def lambdas(
    home_gf: float, home_ga: float, away_gf: float, away_ga: float,
    league_avg: float = 2.70, home_adv: float = 1.12,
    n_home: int = 10, n_away: int = 10,
) -> tuple[float, float]:
    """Expected goals for each side, with sample-size shrinkage."""
    base = max(league_avg, 0.4) / 2.0
    atk_h = _shrink(home_gf / base, n_home)
    def_h = _shrink(home_ga / base, n_home)
    atk_a = _shrink(away_gf / base, n_away)
    def_a = _shrink(away_ga / base, n_away)
    lh = base * atk_h * def_a * home_adv
    la = base * atk_a * def_h
    # keep them inside the range real football actually occupies
    return min(max(lh, 0.35), 3.40), min(max(la, 0.28), 3.10)


def score_matrix(lh: float, la: float, max_goals: int = 8) -> List[List[float]]:
    m = [[_pois(i, lh) * _pois(j, la) * _dc_tau(i, j, lh, la, RHO)
          for j in range(max_goals + 1)] for i in range(max_goals + 1)]
    total = sum(sum(r) for r in m)
    return [[c / total for c in r] for r in m]


def market_probabilities(matrix: List[List[float]]) -> Dict[str, float]:
    """Every market we support, derived from one consistent score matrix."""
    n = len(matrix)
    p_home = sum(matrix[i][j] for i in range(n) for j in range(n) if i > j)
    p_draw = sum(matrix[i][i] for i in range(n))
    p_away = sum(matrix[i][j] for i in range(n) for j in range(n) if i < j)

    def over(line: float) -> float:
        return sum(matrix[i][j] for i in range(n) for j in range(n) if i + j > line)

    btts = sum(matrix[i][j] for i in range(1, n) for j in range(1, n))

    return {
        "1": p_home, "X": p_draw, "2": p_away,
        "1X": p_home + p_draw, "X2": p_draw + p_away, "12": p_home + p_away,
        "O1.5": over(1.5), "U1.5": 1 - over(1.5),
        "O2.5": over(2.5), "U2.5": 1 - over(2.5),
        "O3.5": over(3.5), "U3.5": 1 - over(3.5),
        "BTTS": btts, "NOBTTS": 1 - btts,
    }


MARKET_NAMES = {
    "1": "Home win", "X": "Draw", "2": "Away win",
    "1X": "Home or Draw (DC)", "X2": "Draw or Away (DC)", "12": "Home or Away (DC)",
    "O1.5": "Over 1.5 goals", "U1.5": "Under 1.5 goals",
    "O2.5": "Over 2.5 goals", "U2.5": "Under 2.5 goals",
    "O3.5": "Over 3.5 goals", "U3.5": "Under 3.5 goals",
    "BTTS": "Both teams to score", "NOBTTS": "Both teams to score - No",
}
