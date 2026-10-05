#!/usr/bin/env python3
"""Health check — run this whenever something doesn't work.

    python doctor.py              check everything
    python doctor.py --send       also send a real test message to Telegram

It checks every link in the chain in order and, when something is wrong,
tells you the exact command or setting that fixes it.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import date, datetime
from pathlib import Path

OK, BAD, WARN, INFO = "  ✅", "  ❌", "  ⚠️ ", "     "
problems: list[str] = []
warnings: list[str] = []


def step(n: int, title: str) -> None:
    print(f"\n\033[1m{n}. {title}\033[0m")


def fail(msg: str, fix: str) -> None:
    print(f"{BAD} {msg}")
    print(f"{INFO}→ fix: {fix}")
    problems.append(msg)


def warn(msg: str, fix: str = "") -> None:
    print(f"{WARN} {msg}")
    if fix:
        print(f"{INFO}→ {fix}")
    warnings.append(msg)


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--send", action="store_true",
                    help="send a real test message to your Telegram chat")
    args = ap.parse_args()

    print("\033[1m⚽ football-bot doctor\033[0m")
    print(f"   {datetime.now().strftime('%Y-%m-%d %H:%M')}")

    # 1 ----------------------------------------------------------- python --
    step(1, "Python and dependencies")
    v = sys.version_info
    if v < (3, 10):
        fail(f"Python {v.major}.{v.minor} is too old",
             "install Python 3.10 or newer (3.12 recommended)")
    else:
        print(f"{OK} Python {v.major}.{v.minor}.{v.micro}")

    missing = []
    for mod, pkg in (("httpx", "httpx"), ("dotenv", "python-dotenv")):
        try:
            __import__(mod)
            print(f"{OK} {pkg}")
        except ImportError:
            missing.append(pkg)
            fail(f"{pkg} is not installed", "pip install -r requirements.txt")
    try:
        import telegram  # noqa: F401
        print(f"{OK} python-telegram-bot (interactive mode available)")
    except ImportError:
        warn("python-telegram-bot not installed",
             "only needed for `python bot.py`; daily.py works without it")
    if missing:
        return 1

    import config
    from providers import get_provider

    # 2 -------------------------------------------------------------- env --
    step(2, "Configuration")
    env_file = Path(".env")
    if env_file.exists():
        print(f"{OK} .env found")
    elif os.getenv("BOT_TOKEN"):
        print(f"{OK} no .env, but environment variables are set (CI mode)")
    else:
        fail("no .env file and no environment variables",
             "cp .env.example .env   then edit it")

    print(f"{INFO}provider      : {config.PROVIDER}")
    print(f"{INFO}price source  : {config.PRICE_SOURCE}")
    print(f"{INFO}target odds   : {config.TARGET_ODDS}")
    print(f"{INFO}timezone      : {config.TIMEZONE}")
    print(f"{INFO}daily push    : {config.DAILY_HOUR:02d}:{config.DAILY_MINUTE:02d}")

    # 3 --------------------------------------------------------- telegram --
    step(3, "Telegram bot token")
    import httpx
    bot_ok = False
    if not config.BOT_TOKEN:
        fail("BOT_TOKEN is empty",
             "open Telegram, message @BotFather, send /newbot, paste the token into .env")
    else:
        try:
            async with httpx.AsyncClient(timeout=20) as c:
                r = await c.get(
                    f"https://api.telegram.org/bot{config.BOT_TOKEN}/getMe")
            data = r.json()
            if data.get("ok"):
                u = data["result"]
                print(f"{OK} token valid — bot is @{u.get('username')}")
                bot_ok = True
            else:
                fail(f"Telegram rejected the token: {data.get('description')}",
                     "get a fresh token from @BotFather and update BOT_TOKEN")
        except Exception as exc:  # noqa: BLE001
            fail(f"could not reach Telegram: {exc}", "check your internet connection")

    # 4 ------------------------------------------------------------ chats --
    step(4, "Where the daily message goes")
    ids = config.DEFAULT_CHAT_IDS
    subs = []
    if config.SUBS_FILE.exists():
        import json
        try:
            subs = json.loads(config.SUBS_FILE.read_text())
        except Exception:  # noqa: BLE001
            pass
    if ids or subs:
        print(f"{OK} {len(set(list(ids) + [str(s) for s in subs]))} recipient(s) configured")
    else:
        warn("no CHAT_IDS and nobody subscribed yet",
             "message @userinfobot to get your id, then put it in CHAT_IDS "
             "(required for the GitHub Action; /subscribe works for the live bot)")

    if args.send and bot_ok and (ids or subs):
        targets = list(dict.fromkeys([str(i) for i in ids] + [str(s) for s in subs]))
        async with httpx.AsyncClient(timeout=20) as c:
            for cid in targets:
                r = await c.post(
                    f"https://api.telegram.org/bot{config.BOT_TOKEN}/sendMessage",
                    json={"chat_id": cid, "parse_mode": "HTML",
                          "text": "✅ <b>football-bot doctor</b>\n"
                                  "If you can read this, delivery works."})
                if r.json().get("ok"):
                    print(f"{OK} test message delivered to {cid}")
                else:
                    fail(f"could not message {cid}: {r.json().get('description')}",
                         "send /start to your bot first — Telegram blocks "
                         "messages to users who never opened the chat")

    # 5 ----------------------------------------------------- free data src --
    step(5, "Free data source (football-data.co.uk)")
    from providers.footballdata_uk import FootballDataUKProvider
    free_n = 0
    try:
        fp = FootballDataUKProvider()
        fm = await fp.fixtures(date.today())
        await fp.close()
        free_n = len(fm)
        if free_n >= config.MIN_CARD_SIZE:
            print(f"{OK} {free_n} fixtures available — no API needed today")
        elif free_n:
            warn(f"only {free_n} fixtures in the free file today",
                 "normal on Mondays / international breaks; "
                 "an API key fills the gap automatically")
        else:
            warn("no fixtures in the free file right now",
                 "it refreshes a couple of times a week; an API key covers the gap")
    except Exception as exc:  # noqa: BLE001
        fail(f"could not read the free CSVs: {exc}", "check your internet connection")

    # 6 ------------------------------------------------------ api-football --
    step(6, "API-Football (optional live fallback)")
    if not config.APIFOOTBALL_KEY:
        if free_n >= config.MIN_CARD_SIZE:
            print(f"{INFO}no key set — not needed today, the free source is enough")
        else:
            warn("no APIFOOTBALL_KEY, and the free card is thin today",
                 "free key (100 req/day, no card): https://dashboard.api-football.com")
    else:
        try:
            from providers.apifootball import APIFootballProvider
            ap_ = APIFootballProvider()
            try:
                async with httpx.AsyncClient(
                    base_url=f"https://{ap_.host}",
                    headers=dict(ap_._c.headers), timeout=25,
                ) as c:
                    r = await c.get("/status")
                body = r.json()
                errs = body.get("errors") or {}
                if errs:
                    fail(f"API-Football rejected the key: {errs}",
                         "check APIFOOTBALL_KEY, and that APIFOOTBALL_HOST matches "
                         "where you registered (api-sports vs rapidapi)")
                else:
                    resp = body.get("response") or {}
                    sub = resp.get("subscription") or {}
                    req = resp.get("requests") or {}
                    used, limit = req.get("current", "?"), req.get("limit_day", "?")
                    print(f"{OK} key valid — plan '{sub.get('plan','?')}', "
                          f"{used}/{limit} requests used today")
                    if isinstance(used, int) and isinstance(limit, int):
                        if used >= limit * 0.9:
                            warn("you are close to the daily cap",
                                 "the bot caches aggressively; it resets at midnight UTC")
                    print(f"{INFO}local budget used: {ap_.used_today()}"
                          f"/{config.APIFOOTBALL_DAILY_BUDGET}")
            finally:
                await ap_.close()
        except Exception as exc:  # noqa: BLE001
            fail(f"API-Football check failed: {exc}",
                 "verify APIFOOTBALL_KEY and APIFOOTBALL_HOST")

    # 7 ----------------------------------------------------- the whole run --
    step(7, "Full pipeline (what runs every morning)")
    try:
        from engine import build_bomb, build_safe, build_selections
        prov = get_provider()
        matches = await prov.fixtures(date.today())
        await prov.close()
        if not matches:
            warn("no fixtures to analyse right now",
                 "the bot will post a 'no fixtures' notice; try again on a match day")
        else:
            sels = build_selections(matches)
            safe, bomb = build_safe(sels), build_bomb(sels)
            print(f"{OK} {len(matches)} fixtures → {len(sels)} selections "
                  f"via {prov.name}")
            if safe:
                print(f"{OK} SAFE ticket  {safe.total_odds:6.2f}  "
                      f"({len(safe.legs)} legs, {safe.combined_prob*100:.1f}% to land)")
            else:
                warn("no safe ticket could be built from today's card")
            if bomb:
                flag = OK if bomb.total_odds >= config.TARGET_ODDS else WARN
                print(f"{flag} BOMB ticket  {bomb.total_odds:6.2f}  "
                      f"({len(bomb.legs)} legs, {bomb.combined_prob*100:.1f}% to land)")
                if bomb.total_odds < config.TARGET_ODDS:
                    print(f"{INFO}card too thin to reach {config.TARGET_ODDS:.0f}x today")
            else:
                warn("no bomb ticket could be built from today's card")
    except Exception as exc:  # noqa: BLE001
        fail(f"pipeline error: {exc}", "run `python test_engine.py` to narrow it down")

    # 8 ------------------------------------------------------------- disk --
    step(8, "Storage")
    print(f"{OK} data dir: {config.DATA_DIR}")
    if config.HISTORY_FILE.exists():
        n = len([l for l in config.HISTORY_FILE.read_text().splitlines() if l.strip()])
        print(f"{OK} history: {n} day(s) recorded — /roi will work")
    else:
        print(f"{INFO}history: empty (fills up after the first daily run)")

    # ----------------------------------------------------------- verdict ---
    print("\n" + "─" * 58)
    if problems:
        print(f"\033[1m❌ {len(problems)} problem(s) to fix:\033[0m")
        for p in problems:
            print(f"   • {p}")
        print("\nFix those, then run `python doctor.py` again.")
        return 1
    if warnings:
        print(f"\033[1m✅ Working, with {len(warnings)} note(s):\033[0m")
        for w in warnings:
            print(f"   • {w}")
    else:
        print("\033[1m✅ Everything checks out.\033[0m")
    print("\nNext: `python daily.py --dry-run` to preview today's message.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
