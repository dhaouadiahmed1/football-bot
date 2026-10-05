#!/usr/bin/env python3
"""Telegram football accumulator bot.

Commands
  /start            register + help
  /today            full daily analysis (safe + bomb)
  /safe             safe ticket only
  /bomb [odds]      30x ticket (or a custom target, e.g. /bomb 50)
  /value            top value selections of the day
  /matches          today's scanned fixtures
  /subscribe        get the tickets pushed automatically every morning
  /unsubscribe      stop the daily push
  /stats            how many tickets have been generated so far
"""
from __future__ import annotations

import asyncio
import json
import importlib
import logging
import threading
from datetime import date, datetime, time as dtime
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from telegram import BotCommand, Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes

import config
import formatting as fmt
import settle
from engine import build_selections, build_bomb, build_safe
from engine.markets import Selection
from providers import get_provider
from providers.base import Match

logging.basicConfig(
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s", level=logging.INFO
)
log = logging.getLogger("footybot")
logging.getLogger("httpx").setLevel(logging.WARNING)

TZ = ZoneInfo(config.TIMEZONE)
PROVIDER = get_provider()

# day -> (matches, selections); avoids re-hitting the API for every command
_CACHE: Dict[str, Tuple[List[Match], List[Selection]]] = {}
_LOCK = asyncio.Lock()


# --------------------------------------------------------------- analysis --
async def analyse(day: Optional[date] = None, force: bool = False):
    day = day or datetime.now(TZ).date()
    key = day.isoformat()
    async with _LOCK:
        if force or key not in _CACHE:
            matches = await PROVIDER.fixtures(day)
            sels = build_selections(matches)
            _CACHE.clear()
            _CACHE[key] = (matches, sels)
            log.info("Analysed %s: %d matches, %d selections",
                     key, len(matches), len(sels))
        return _CACHE[key]


# ------------------------------------------------------------ subscribers --
def load_subs() -> List[int]:
    if config.SUBS_FILE.exists():
        try:
            return json.loads(config.SUBS_FILE.read_text())
        except Exception:  # noqa: BLE001
            pass
    return [int(c) for c in config.DEFAULT_CHAT_IDS]


def save_subs(subs: List[int]) -> None:
    config.SUBS_FILE.write_text(json.dumps(sorted(set(subs))))


def log_history(day: date, safe, bomb) -> None:
    rec = {"date": day.isoformat(), "generated_at": datetime.now(TZ).isoformat(),
           "safe": safe.as_dict() if safe else None,
           "bomb": bomb.as_dict() if bomb else None}
    with config.HISTORY_FILE.open("a") as fh:
        fh.write(json.dumps(rec) + "\n")


# ---------------------------------------------------------------- handlers --
HELP = (
    "⚽ <b>Daily Football Accumulator Bot</b>\n\n"
    "Every day I model every fixture with a Dixon-Coles Poisson engine, compare "
    "my probabilities to the bookmaker's, and build two tickets:\n"
    "🛡 a <b>safe</b> one (3-5 high-probability legs)\n"
    "💣 a <b>bomb</b> one (total odds 30.00+, built to give away as little "
    "win probability as possible)\n\n"
    "<b>Commands</b>\n"
    "/today — full analysis\n"
    "/safe — safe ticket only\n"
    "/bomb [target] — 30x ticket, or /bomb 50 for a custom target\n"
    "/value — biggest model-vs-bookmaker edges\n"
    "/matches — fixtures scanned today\n"
    "/subscribe — daily push at "
    f"{config.DAILY_HOUR:02d}:{config.DAILY_MINUTE:02d} {config.TIMEZONE}\n"
    "/unsubscribe — stop it\n"
    "/stats — generation history\n"
    "/roi — <b>real settled results</b>: hit rate, profit, ROI\n"
    "/status — data source, API quota left, settings, record\n\n" + fmt.DISCLAIMER
)


async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_html(HELP)


async def _send_daily(chat_send, day: date, force: bool = False, record: bool = False):
    matches, sels = await analyse(day, force=force)
    if not matches:
        await chat_send("No fixtures available for today in the configured leagues.")
        return
    safe, bomb = build_safe(sels), build_bomb(sels)
    if record:
        log_history(day, safe, bomb)
    for msg in fmt.render_daily(day, len(matches), PROVIDER.name, safe, bomb):
        await chat_send(msg)


async def cmd_today(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.chat.send_action("typing")
    day = datetime.now(TZ).date()

    async def send(text: str):
        await update.message.reply_html(text, disable_web_page_preview=True)

    await _send_daily(send, day)


async def cmd_safe(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.chat.send_action("typing")
    _, sels = await analyse()
    await update.message.reply_html(
        fmt.render_ticket(build_safe(sels), "SAFE TICKET", "🛡",
                          "High-probability legs only.") + "\n\n" + fmt.DISCLAIMER
    )


async def cmd_bomb(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.chat.send_action("typing")
    target = config.TARGET_ODDS
    if ctx.args:
        try:
            target = max(2.0, min(float(ctx.args[0].replace(",", ".")), 5000.0))
        except ValueError:
            pass
    _, sels = await analyse()
    await update.message.reply_html(
        fmt.render_ticket(build_bomb(sels, target), f"BOMB TICKET ({target:.0f}x+)", "💣",
                          "Cheapest route to the target odds.") + "\n\n" + fmt.DISCLAIMER
    )


async def cmd_value(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    _, sels = await analyse()
    await update.message.reply_html(fmt.render_value_board(sels))


async def cmd_matches(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    matches, _ = await analyse()
    await update.message.reply_html(fmt.render_matches(matches))


async def cmd_subscribe(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    subs = load_subs()
    cid = update.effective_chat.id
    if cid not in subs:
        subs.append(cid)
        save_subs(subs)
    await update.message.reply_html(
        f"✅ Subscribed. Daily tickets at "
        f"<b>{config.DAILY_HOUR:02d}:{config.DAILY_MINUTE:02d}</b> ({config.TIMEZONE})."
    )


async def cmd_unsubscribe(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    subs = [c for c in load_subs() if c != update.effective_chat.id]
    save_subs(subs)
    await update.message.reply_text("🔕 Unsubscribed.")


async def collect_status() -> dict:
    """Gather everything /status reports. Never raises — this is the command
    you run when things are already broken."""
    import json as _json

    st = {
        "now": datetime.now(TZ).strftime("%a %d %b, %H:%M"),
        "provider_mode": config.PROVIDER,
        "source": PROVIDER.name,
        "min_card": config.MIN_CARD_SIZE,
        "fixtures": None, "selections": 0,
        "api_key": bool(config.APIFOOTBALL_KEY),
        "local_used": 0, "local_budget": config.APIFOOTBALL_DAILY_BUDGET,
        "target": config.TARGET_ODDS,
        "price_source": config.PRICE_SOURCE,
        "model_weight": config.MODEL_WEIGHT,
        "devig": config.DEVIG_METHOD,
        "daily_time": f"{config.DAILY_HOUR:02d}:{config.DAILY_MINUTE:02d}",
        "tz": config.TIMEZONE,
        "subscribers": len(load_subs()),
        "history_days": 0, "record": {},
    }

    try:
        matches, sels = await analyse()
        st["fixtures"], st["selections"] = len(matches), len(sels)
        st["source"] = PROVIDER.name          # hybrid renames itself after a run
    except Exception as exc:  # noqa: BLE001
        log.warning("status: card load failed: %s", exc)

    if config.APIFOOTBALL_KEY:
        try:
            from providers.apifootball import APIFootballProvider
            api = APIFootballProvider()
            try:
                st["local_used"] = api.used_today()
                acc = await api.account_status()
                if acc.get("error"):
                    st["api_error"] = acc["error"]
                else:
                    st["api_plan"] = acc.get("plan")
                    st["api_used"] = acc.get("used")
                    st["api_limit"] = acc.get("limit")
            finally:
                await api.close()
        except Exception as exc:  # noqa: BLE001
            st["api_error"] = str(exc)

    if config.HISTORY_FILE.exists():
        try:
            rows = [_json.loads(l) for l in
                    config.HISTORY_FILE.read_text().splitlines() if l.strip()]
            st["history_days"] = len(rows)
            from engine.grading import summarise
            for kind in ("safe", "bomb"):
                st["record"][kind] = summarise(
                    [r[f"{kind}_result"] for r in rows if r.get(f"{kind}_result")])
        except Exception as exc:  # noqa: BLE001
            log.warning("status: history unreadable: %s", exc)

    return st


async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Data source, API quota, settings and track record — all in one place."""
    await update.message.chat.send_action("typing")
    st = await collect_status()
    await update.message.reply_html(fmt.render_status(st))


async def cmd_roi(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Real, settled results — not the model's opinion of itself."""
    await update.message.chat.send_action("typing")
    try:
        report = await settle.grade_all()
    except Exception as exc:  # noqa: BLE001
        await update.message.reply_text(f"Could not settle tickets: {exc}")
        return
    await update.message.reply_html(settle.render_report(report))


async def cmd_stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not config.HISTORY_FILE.exists():
        await update.message.reply_text("No tickets generated yet.")
        return
    rows = [json.loads(l) for l in config.HISTORY_FILE.read_text().splitlines() if l.strip()]
    bombs = [r["bomb"]["total_odds"] for r in rows if r.get("bomb")]
    safes = [r["safe"]["total_odds"] for r in rows if r.get("safe")]
    probs = [r["bomb"]["combined_prob"] for r in rows if r.get("bomb")]
    lines = [f"📈 <b>History</b>", f"Days generated: <b>{len(rows)}</b>"]
    if safes:
        lines.append(f"Avg safe ticket odds: <b>{sum(safes)/len(safes):.2f}</b>")
    if bombs:
        lines.append(f"Avg bomb ticket odds: <b>{sum(bombs)/len(bombs):.2f}</b>")
        lines.append(f"Avg bomb win prob: <b>{sum(probs)/len(probs)*100:.1f}%</b>")
    lines.append(f"\nLast run: <code>{rows[-1]['generated_at'][:16]}</code>")
    await update.message.reply_html("\n".join(lines))


# ----------------------------------------------------------------- daily ----
async def daily_job(ctx: ContextTypes.DEFAULT_TYPE) -> None:
    day = datetime.now(TZ).date()
    subs = load_subs()
    if not subs:
        log.info("Daily job: no subscribers")
        return
    try:
        rep = await settle.grade_all()
        if rep.get("newly_settled"):
            for cid in subs:
                await ctx.bot.send_message(cid, settle.render_report(rep),
                                           parse_mode=ParseMode.HTML)
    except Exception as exc:  # noqa: BLE001
        log.warning("settlement failed: %s", exc)

    matches, sels = await analyse(day, force=True)
    if not matches:
        return
    safe, bomb = build_safe(sels), build_bomb(sels)
    log_history(day, safe, bomb)
    msgs = fmt.render_daily(day, len(matches), PROVIDER.name, safe, bomb)
    for cid in subs:
        for m in msgs:
            try:
                await ctx.bot.send_message(cid, m, parse_mode=ParseMode.HTML)
            except Exception as exc:  # noqa: BLE001
                log.warning("send to %s failed: %s", cid, exc)


# ------------------------------------------------- health server (hosting) --
class _Health(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        # /healthz stays plain text for uptime probes; / is the dashboard.
        if self.path.rstrip("/") in ("/healthz", "/health"):
            body, ctype = b"football-bot: ok", "text/plain"
        else:
            try:
                import dashboard
                importlib.reload(dashboard)      # pick up edits without a restart
                body = dashboard.render().encode()
            except Exception as exc:             # noqa: BLE001
                log.exception("dashboard render failed")
                body = f"dashboard error: {exc}".encode()
            ctype = "text/html; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):  # silence
        pass


def start_health_server() -> None:
    """Free web hosts (Render/Railway) expect an open port. Harmless elsewhere."""
    try:
        srv = HTTPServer(("0.0.0.0", config.PORT), _Health)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        log.info("Health server on :%d", config.PORT)
    except OSError as exc:
        log.warning("Health server not started: %s", exc)


COMMAND_MENU = [
    ("today", "Full analysis: safe + bomb ticket"),
    ("safe", "Safe ticket only"),
    ("bomb", "30x ticket (/bomb 50 for a custom target)"),
    ("value", "Biggest model vs bookmaker gaps"),
    ("matches", "Fixtures scanned today"),
    ("roi", "Real settled results: hit rate, profit"),
    ("status", "Data source, API quota, settings"),
    ("subscribe", "Get the tickets every morning"),
    ("unsubscribe", "Stop the daily push"),
    ("help", "What this bot does"),
]


async def _post_init(app: Application) -> None:
    """Register the command menu so Telegram shows it in the UI."""
    try:
        await app.bot.set_my_commands([BotCommand(c, d) for c, d in COMMAND_MENU])
        me = await app.bot.get_me()
        log.info("Connected as @%s — command menu registered", me.username)
    except Exception as exc:  # noqa: BLE001
        log.warning("could not register command menu: %s", exc)


# ------------------------------------------------------------------- main ---
def main() -> None:
    if not config.BOT_TOKEN:
        raise SystemExit("BOT_TOKEN missing — copy .env.example to .env and fill it in.")

    start_health_server()
    app = Application.builder().token(config.BOT_TOKEN).post_init(_post_init).build()

    app.add_handler(CommandHandler(["start", "help"], cmd_start))
    app.add_handler(CommandHandler("today", cmd_today))
    app.add_handler(CommandHandler("safe", cmd_safe))
    app.add_handler(CommandHandler("bomb", cmd_bomb))
    app.add_handler(CommandHandler(["value", "edges"], cmd_value))
    app.add_handler(CommandHandler("matches", cmd_matches))
    app.add_handler(CommandHandler("subscribe", cmd_subscribe))
    app.add_handler(CommandHandler("unsubscribe", cmd_unsubscribe))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CommandHandler(["roi", "results"], cmd_roi))
    app.add_handler(CommandHandler(["status", "health"], cmd_status))

    if config.DAILY_PUSH:
        app.job_queue.run_daily(
            daily_job,
            time=dtime(hour=config.DAILY_HOUR, minute=config.DAILY_MINUTE, tzinfo=TZ),
            name="daily_tickets",
        )
        log.info("Bot starting — provider=%s, daily=%02d:%02d %s",
                 PROVIDER.name, config.DAILY_HOUR, config.DAILY_MINUTE, config.TIMEZONE)
    else:
        log.info("Bot starting — provider=%s, daily push OFF "
                 "(GitHub Actions sends it); commands still work", PROVIDER.name)
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()
