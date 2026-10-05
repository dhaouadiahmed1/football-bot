"""Sanity tests:  python test_engine.py"""
import asyncio
from datetime import date
from math import prod

import config
from engine import build_bomb, build_safe, build_selections
from engine.grading import settle_leg, settle_ticket, summarise
from engine.markets import devig
from engine.poisson import lambdas, market_probabilities, score_matrix
from providers.mock import MockProvider


def test_probabilities_are_coherent():
    lh, la = lambdas(1.8, 1.1, 1.2, 1.5, 2.7, 1.12)
    p = market_probabilities(score_matrix(lh, la))
    assert abs(p["1"] + p["X"] + p["2"] - 1) < 1e-6, "1X2 must sum to 1"
    assert abs(p["1X"] - (p["1"] + p["X"])) < 1e-9
    assert abs(p["O2.5"] + p["U2.5"] - 1) < 1e-6
    assert abs(p["BTTS"] + p["NOBTTS"] - 1) < 1e-6
    assert p["O1.5"] > p["O2.5"] > p["O3.5"], "over lines must be monotonic"
    assert p["1"] > p["2"], "stronger home side should be favourite"
    print("  probability coherence                        OK")


def test_shrinkage():
    """A hot streak must not be taken at face value."""
    hot_small = lambdas(3.0, 0.4, 1.3, 1.3, 2.7, 1.1, n_home=3, n_away=20)
    hot_big = lambdas(3.0, 0.4, 1.3, 1.3, 2.7, 1.1, n_home=30, n_away=20)
    assert hot_small[0] < hot_big[0], "small samples must be regressed harder"
    assert 0.3 <= hot_big[0] <= 3.4, "lambda must stay in football's range"
    print("  sample-size shrinkage                        OK")


def test_devig():
    odds = {"1": 2.00, "X": 3.40, "2": 4.00}
    raw = sum(1 / o for o in odds.values())
    fair = devig(odds)
    assert raw > 1.0, "bookmaker odds must imply more than 100%"
    assert abs(fair["1"] + fair["X"] + fair["2"] - 1) < 1e-6, "de-vigged must sum to 1"
    assert all(fair[k] < 1 / odds[k] for k in odds), "fair prob < implied prob"
    assert abs(fair["1X"] - (fair["1"] + fair["X"])) < 1e-9
    # power method must take proportionally more off the long shot
    assert (1 / odds["2"] - fair["2"]) / (1 / odds["2"]) > \
           (1 / odds["1"] - fair["1"]) / (1 / odds["1"]), "long shots carry more vig"
    print("  de-vigging (power method)                    OK")


def test_settlement():
    assert settle_leg("1", 2, 1) and not settle_leg("1", 1, 2)
    assert settle_leg("1X", 1, 1) and settle_leg("1X", 3, 0)
    assert settle_leg("O2.5", 2, 1) and not settle_leg("O2.5", 1, 1)
    assert settle_leg("BTTS", 1, 1) and not settle_leg("BTTS", 2, 0)

    ticket = {"total_odds": 10.0, "legs": [
        {"match_id": "a", "market": "1"}, {"match_id": "b", "market": "O2.5"}]}
    assert settle_ticket(ticket, {"a": (1, 0), "b": (2, 2)})["status"] == "won"
    assert settle_ticket(ticket, {"a": (0, 1), "b": (2, 2)})["status"] == "lost"
    assert settle_ticket(ticket, {"a": (1, 0)})["status"] == "pending"
    assert settle_ticket(ticket, {"a": (1, 0), "b": (2, 2)})["profit"] == 9.0

    s = summarise([{"status": "won", "profit": 9.0}, {"status": "lost", "profit": -1.0},
                   {"status": "lost", "profit": -1.0}, {"status": "pending", "profit": 0}])
    assert s["n"] == 3 and s["won"] == 1 and abs(s["profit"] - 7.0) < 1e-9
    print("  settlement + bankroll maths                  OK")


def test_tickets(day: date):
    matches = asyncio.run(MockProvider().fixtures(day))
    sels = build_selections(matches)
    assert matches and sels

    safe = build_safe(sels)
    bomb = build_bomb(sels)
    assert safe and bomb

    for t in (safe, bomb):
        ids = [l.match_id for l in t.legs]
        assert len(ids) == len(set(ids)), "one leg per fixture only"
        assert abs(t.total_odds - prod(l.odds for l in t.legs)) < 1e-6
        assert all(l.odds <= config.MAX_LEG_ODDS + 1e-9 for l in t.legs)

    assert bomb.total_odds >= config.TARGET_ODDS, "bomb must clear the target"
    assert len(bomb.legs) <= config.BOMB_MAX_LEGS
    assert safe.combined_prob > bomb.combined_prob, "safe must be safer"
    assert safe.total_odds < bomb.total_odds
    if config.SAFE_MARKETS:
        assert all(l.market in config.SAFE_MARKETS for l in safe.legs)

    # no redundant legs: dropping any one must break the target
    for l in bomb.legs:
        assert prod(x.odds for x in bomb.legs if x is not l) < config.TARGET_ODDS

    # the knapsack must not be beaten by a trivial greedy alternative
    assert len(bomb.legs) <= 8, "a 30x ticket should not need 9+ legs"

    print(f"  {day}  safe {safe.total_odds:5.2f} @ {safe.combined_prob*100:4.1f}%"
          f"   bomb {bomb.total_odds:6.2f} @ {bomb.combined_prob*100:4.1f}%"
          f"  ({len(bomb.legs)} legs)   OK")


if __name__ == "__main__":
    print("Running engine tests…")
    test_probabilities_are_coherent()
    test_shrinkage()
    test_devig()
    test_settlement()
    for d in range(1, 15):
        test_tickets(date(2026, 10, d))
    print("All tests passed ✅")
