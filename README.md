# ⚽ Daily Football Accumulator Bot — 100% free

A Telegram bot that analyses every football match of the day, models it, prices
it against the bookmakers, and posts two tickets:

| | |
|---|---|
| 🛡 **SAFE ticket** | 3–5 short legs, total odds ~2.0, lands about half the time |
| 💣 **BOMB ticket** | total odds **30.00+**, built to be the *shortest* ticket that reaches 30x |

Then it **grades its own tickets against real results** and reports true ROI, so
you can hold it accountable instead of trusting it.

**Free means free:** no API key, no registration, no credit card, no trial, no
hosting bill, no keep-alive pinger. Nothing in this repo has a paid tier.

| Piece | How it's free |
|---|---|
| Fixtures, odds, results | [football-data.co.uk](https://www.football-data.co.uk) public CSVs — no key, no rate limit |
| Live fallback (optional) | [API-Football](https://dashboard.api-football.com) free plan — 100 req/day, no credit card |
| Daily delivery | GitHub Actions cron (public repo = unlimited minutes; ~1 min/day) |
| Storage | `data/history.jsonl`, committed back to your own repo |
| Telegram | Bot API, free |

---

> **New here? Follow [SETUP.md](SETUP.md)** — a numbered, 15-minute walkthrough
> from zero to a bot posting every morning, with a verification command after
> every step and a troubleshooting table at the end.

## 1. Quick start (2 minutes, nothing to sign up for)

```bash
cd football-bot
pip install -r requirements.txt

python demo.py          # real fixtures, real odds, printed to your terminal
python test_engine.py   # sanity checks on the maths
python test_api.py      # API parsing checks (no key needed, no quota used)
python doctor.py        # checks every link in the chain, tells you what to fix
```

Then connect Telegram:

```bash
cp .env.example .env    # paste the token @BotFather gives you after /newbot
python bot.py
```

Send `/start`, then `/today`.

---

## 2. Run it daily for free, with no server

No Render, no Railway, no sleeping dyno, no uptime pinger. One command:

```bash
pip install pynacl
python deploy_github.py --token ghp_YOUR_TOKEN --public
```

It creates the repo, uploads your secrets (encrypted locally with libsodium),
pushes, and triggers the first run. Use `--dry-run` to preview. Full manual
steps are in [SETUP.md](SETUP.md) if you'd rather click through it.

It now runs every morning at 08:00 UTC (09:00 Tunis), settles yesterday's
tickets, posts today's, and commits the results history back to your repo.
Edit the `cron:` line in `.github/workflows/daily.yml` to change the time.

`python bot.py` (interactive commands) still works anywhere — your laptop, a
Raspberry Pi, any free container host — but it is optional. The Action is the
free, always-on path.

---

## 3. Commands

| Command | What it does |
|---|---|
| `/today` | Full analysis: safe + bomb ticket |
| `/safe` | Safe ticket only |
| `/bomb [target]` | 30x ticket, or `/bomb 50` for a custom target |
| `/value` | Biggest model-vs-bookmaker disagreements |
| `/matches` | Every fixture scanned |
| `/roi` | **Real settled results** — hit rate, profit, ROI, worst drawdown |
| `/status` | Data source in use, API quota left, settings, track record |
| `/subscribe` · `/unsubscribe` | Daily auto-push |

---

## 4. Read this before you bet anything

The strategy was backtested on **11,800 real matches over 3 seasons** with the
actual pre-match odds. Full write-up in [BACKTEST.md](BACKTEST.md).

| Ticket | Tickets | Hit rate | Predicted | ROI |
|---|---|---|---|---|
| 🛡 SAFE | 439 | 48.29% | 47.13% | **−3.87%** |
| 💣 BOMB | 439 | 3.64% | 3.38% | **+14.00%** |

Three things to take from that:

1. **The probabilities are trustworthy** (calibration 1.02x and 1.08x). When
   the bot says a ticket is 3.4% to land, it really is about 3.4%.
2. **The +14% on the bomb is noise, not skill.** Season by season it was
   +1.1%, −32.5%, +72.1%. That's 16 winners where 14.8 were expected — the
   whole "edge" is one extra win. At 30x, 439 bets cannot distinguish a good
   strategy from a lucky one.
3. **A 30x ticket lands ~1 day in 28.** Expect long, brutal losing runs. The
   worst drawdown in the backtest was 132 units.

The most valuable finding is not a model at all: **the same picks returned
−28% at one bookmaker and +14% at best-available prices.** Where you place the
bet matters more than what you pick. Open accounts at several books and always
take the best price — that is worth ~4% per leg, every leg, forever.

---

## 5. How it works

```
free CSVs ──► form (decay-weighted, last 8 games)
          ──► strengths regressed to league mean by n/(n+7)   ← stops hot streaks lying
          ──► λ_home, λ_away
          ──► 8×8 Dixon-Coles score matrix
          ──► every market priced consistently
                                   │
bookmaker odds ──► power de-vig ───┤  (strips margin, corrects long-shot bias)
                                   ▼
                      blend: 10% model + 90% market
                                   ▼
          knapsack: max Σ log(p)  s.t.  Σ log(o) ≥ log(30)
                                   ▼
                        SAFE + BOMB tickets
                                   ▼
                    settle vs real results → ROI
```

The bot deliberately trusts the market over its own model (`MODEL_WEIGHT=0.10`).
That is not laziness — at `MODEL_WEIGHT=1.0` the engine claimed 14.88% on
tickets that won 1.87% of the time. The market is sharp; the model's job is a
nudge, and the builder's job is to waste as little margin as possible.

---

## 6. Configuration

Everything lives in `.env` (see `.env.example`). The ones that matter:

| Setting | Default | Why |
|---|---|---|
| `PRICE_SOURCE` | `max` | Best available price. Worth more than everything else combined. |
| `MODEL_WEIGHT` | `0.10` | How much to trust our model over the market. Higher = worse. |
| `SAFE_MARKETS` | `1,2,O2.5,U2.5` | Double chance excluded: it costs ~7 points of ROI. |
| `MAX_LEG_ODDS` | `2.30` | Keeps the bomb short without reaching for long shots. |
| `TARGET_ODDS` | `30.0` | Your 30x target. |
| `DEVIG_METHOD` | `power` | Corrects the favourite–long-shot bias. |

### Data sources

`PROVIDER` picks where matches come from:

| Value | Behaviour | API requests/day |
|---|---|---|
| `hybrid` *(default)* | Free CSVs; calls API-Football **only** when the free card has <14 fixtures | 0 on a normal match day, ~20 on a thin one |
| `free` | football-data.co.uk only — no key, no limits, ever | 0 |
| `apifootball` | Live API only (needs `APIFOOTBALL_KEY`) | ~17 |
| `mock` | Offline generated data, for tests | 0 |

The free CSVs are *preferred* even when a key is present, because their odds are
best-available across many books — worth ~4% a leg, which beats any extra
coverage. API-Football is a safety net, not the main source.

Everything is cached on disk (fixtures 3h, odds 2h, standings 12h), only the
10 busiest leagues of the day are priced, and a local counter in
`data/api_usage.json` hard-stops at 80 requests — so the 100/day free cap
cannot be blown by accident.

Note the free plan only serves a **±1 day date window**; the bot detects that
limit and degrades gracefully instead of erroring.

To add your own source, subclass `BaseProvider`, return `Match` objects, and
register it in `providers/__init__.py` — the engine never changes.

---

## 7. Files

```
doctor.py        health check: verifies token, chat, data, API, pipeline
deploy_github.py one-command deploy: repo + secrets + push + first run
link_chat.py     waits for your Telegram message, saves your chat id
bot.py           interactive Telegram bot (polling)
daily.py         one-shot: settle → analyse → send   (what CI runs)
backtest.py      replay the strategy over past seasons with real odds
settle.py        grade stored tickets against real results, compute ROI
demo.py          run everything in the terminal, no token needed
engine/          poisson · markets (de-vig) · tickets (knapsack) · grading
providers/       hybrid · footballdata_uk (free) · apifootball · mock · base
.github/workflows/daily.yml    the free daily cron
SETUP.md         step-by-step install guide
BACKTEST.md      what the strategy actually returned, and why
```

---

## 8. Honest expectations ⚠️

- A 30x accumulator lands roughly **1 time in 28**. That's the maths, not a bug.
- "Safe" means ~48%, not a guarantee.
- The backtest is break-even-ish *at best prices*. At one bookmaker's prices it
  loses money. There is no setting in this repo that reliably beats the market.
- Flat-stake small amounts, never chase, and check `/roi` regularly — it is
  designed to tell you uncomfortable truths.
- **18+.** If it stops being fun, stop: [begambleaware.org](https://www.begambleaware.org).

A modelling and bookkeeping tool. Not financial advice, not a prediction service.
