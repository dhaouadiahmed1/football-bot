"""Settlement: did a leg / a ticket actually win?

Works on the match_id produced by the football-data.co.uk provider:
    "E0|05/10/2026|Arsenal|Everton"
so a ticket stored months ago can still be graded against the results CSVs.
"""
from __future__ import annotations

from typing import Dict, Optional, Tuple


def settle_leg(market: str, hg: int, ag: int) -> bool:
    """True if the selection won, given the final score."""
    total = hg + ag
    return {
        "1": hg > ag, "X": hg == ag, "2": hg < ag,
        "1X": hg >= ag, "X2": hg <= ag, "12": hg != ag,
        "O1.5": total > 1.5, "U1.5": total < 1.5,
        "O2.5": total > 2.5, "U2.5": total < 2.5,
        "O3.5": total > 3.5, "U3.5": total < 3.5,
        "BTTS": hg > 0 and ag > 0, "NOBTTS": hg == 0 or ag == 0,
    }.get(market, False)


def settle_ticket(ticket: dict, results: Dict[str, Tuple[int, int]]) -> dict:
    """Grade a stored ticket dict (from history.jsonl).

    Returns {status: won|lost|pending, legs_won, legs_total, profit}
    Profit assumes a 1 unit stake on the whole accumulator.
    """
    legs = ticket.get("legs", [])
    won = pending = 0
    for leg in legs:
        res = results.get(leg.get("match_id", ""))
        if res is None:
            pending += 1
            continue
        if settle_leg(leg["market"], *res):
            won += 1

    total = len(legs)
    lost = total - won - pending
    if lost > 0:
        status, profit = "lost", -1.0
    elif pending > 0:
        status, profit = "pending", 0.0
    else:
        status, profit = "won", ticket["total_odds"] - 1.0

    return {"status": status, "legs_won": won, "legs_total": total,
            "legs_pending": pending, "profit": round(profit, 2)}


def summarise(graded: list[dict]) -> dict:
    """Bankroll stats over a list of settled tickets (1 unit flat stakes)."""
    done = [g for g in graded if g["status"] in ("won", "lost")]
    if not done:
        return {"n": 0, "won": 0, "hit_rate": 0.0, "staked": 0,
                "profit": 0.0, "roi": 0.0, "max_drawdown": 0.0, "best": 0.0}
    wins = [g for g in done if g["status"] == "won"]
    profit = sum(g["profit"] for g in done)

    equity = peak = dd = 0.0
    for g in done:
        equity += g["profit"]
        peak = max(peak, equity)
        dd = min(dd, equity - peak)

    return {
        "n": len(done), "won": len(wins),
        "hit_rate": len(wins) / len(done),
        "staked": len(done), "profit": round(profit, 2),
        "roi": profit / len(done),
        "max_drawdown": round(dd, 2),
        "best": round(max((g["profit"] for g in done), default=0.0), 2),
    }
