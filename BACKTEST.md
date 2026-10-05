# Backtest — what this strategy actually did

Everything below is reproducible on your machine with free data:

```bash
python backtest.py --seasons 2324 2425 2526
```

**Setup:** 12 leagues, 3 seasons, ~11,800 real matches, real pre-match odds
published alongside each result. 1 unit flat stake per ticket. Team form and
league profile for any match day use *only* matches played before that day, so
there is no look-ahead bias. The first 5 weeks of each season are skipped while
form estimates are still meaningless.

---

## Headline result

| Ticket | Tickets | Avg odds | Hit rate | Model predicted | ROI | Worst drawdown |
|---|---|---|---|---|---|---|
| 🛡 SAFE | 439 | 2.06 | 48.29% | 47.13% | **−3.87%** | −39u |
| 💣 BOMB (30x) | 439 | 31.10 | 3.64% | 3.38% | **+14.00%** | −132u |

Calibration (actual ÷ predicted) is **1.02** and **1.08** — the model's stated
probabilities are honest. That is the part worth trusting.

The ROI is not. Look at the bomb season by season:

| Season | Bomb wins | Hit rate | ROI |
|---|---|---|---|
| 2023/24 | 5 / 153 | 3.27% | **+1.1%** |
| 2024/25 | 3 / 140 | 2.14% | **−32.5%** |
| 2025/26 | 8 / 146 | 5.48% | **+72.1%** |

16 wins against ~14.8 expected. The entire +14% "edge" is **one extra winner**.
At 30x odds you cannot measure skill with 439 bets — the confidence interval on
that ROI is roughly ±50 points. Treat the bomb as a lottery ticket with
honest odds, not as an investment.

---

## The three findings that actually moved the needle

### 1. Where you bet beats what you bet

Identical selections, priced at different books:

| Price used | Overround/leg | SAFE ROI | BOMB ROI |
|---|---|---|---|
| Single book (Bet365) | 106.4% | −13.08% | −28.43% |
| Market average | 106.3% | −10.99% | −15.33% |
| **Best available** | **102.3%** | **−3.87%** | **+14.00%** |

4 points of margin per leg, compounded across 5–6 legs, is the difference
between a losing strategy and a break-even one. Line shopping costs nothing
except opening accounts at a few books. **This is the single highest-value
thing in this entire repo.**

### 2. "Safe" double-chance legs are the expensive kind of safe

| Safe ticket built from | Avg odds | Hit rate | ROI |
|---|---|---|---|
| incl. double chance | 1.50 | 59.68% | **−10.90%** |
| no double chance | 2.06 | 48.29% | **−3.87%** |

Double chance *feels* safer and wins more often — and loses nearly three times
as much money, because one DC leg carries the margin of two outcomes. The
comfortable bet is the costly one. DC is excluded by default (`SAFE_MARKETS`).

### 3. Every extra leg burns another margin

With margin *m* per leg, a *k*-leg accumulator paying 30.00 returns
`(1+m)^-k − 1` in expectation. Legs are not free: at 6.4% margin, 3 legs cost
you ~18%, 8 legs cost ~41% — for the *same* 30x payout. The ticket builder is
therefore a knapsack that maximises `Σ log(pᵢ)` subject to
`Σ log(oᵢ) ≥ log(30)`, which naturally produces the shortest ticket that
reaches the target (typically 5–6 legs instead of the 8–10 a greedy builder
picks).

---

## What did NOT work

Honest negative results, because they shaped the defaults:

- **Trusting our own model.** At `MODEL_WEIGHT=1.0` (pure Poisson, ignore the
  market) the engine claimed 14.88% on tickets that won 1.87% of the time —
  calibration 0.13x. The naive model is not merely imprecise, it is confidently
  wrong. Default is now 0.10: the de-vigged market price does the work and the
  model only nudges it.
- **Optimising for "value".** Selecting legs where the model most disagreed
  with the book made results *worse* (−51% ROI), because with a weak model,
  disagreement is error, not edge. Textbook adverse selection.
- **Unregressed form.** Using raw 8-game form produced λ values like 3.4 for a
  mid-table side on a hot streak. Shrinking strengths toward the league mean by
  `n/(n+7)` was worth ~20 points of calibration on its own.
- **Proportional de-vigging.** Dividing by the overround assumes margin is
  spread evenly; it isn't. The power method (solve `Σ pᵢᵏ = 1`) strips more
  from long shots and fixed most of the remaining bias.

---

## Reproduce / tune it yourself

```bash
python backtest.py --seasons 2425 2526 --price b365   # what one book costs you
python backtest.py --target 50                        # 50x instead of 30x
python backtest.py --safe-markets 1,2,O2.5,U2.5,1X    # let DC back in
python backtest.py --model-weight 0.5 -q              # trust the model more
```

Change nothing on the basis of one good season. The bomb's season-to-season
swing (−32% → +72%) is what noise looks like.
