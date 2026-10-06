#!/usr/bin/env python3
"""One-shot daily run — this is what GitHub Actions executes for free.

  1. settle yesterday's tickets against real results
  2. analyse today's card
  3. build the safe + bomb tickets
  4. push everything to Telegram
  5. append to data/history.jsonl so ROI keeps accumulating

Only needs httpx + python-dotenv (no python-telegram-bot), so the free
CI minutes go on analysis instead of installing packages.

    python daily.py            # send for real
    python daily.py --dry-run  # print to stdout, send nothing
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx

import config
import formatting as fmt
import settle
from engine import build_bomb, build_safe, build_selections
from providers import get_provider

logging.basicConfig(format="%(asctime)s %(levelname)s %(message)s", level=logging.INFO)
log = logging.getLogger("daily")
TZ = ZoneInfo(config.TIMEZONE)
API = "https://api.telegram.org/bot{token}/sendMessage"


def already_sent(day) -> bool:
    """True if today's card is already in history.

    GitHub's cron is best-effort and sometimes skips a slot, so the workflow
    runs twice on purpose. This is what stops the backup slot from sending a
    duplicate when the primary one worked.
    """
    if not config.HISTORY_FILE.exists():
        return False
    target = day.isoformat()
    for line in config.HISTORY_FILE.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            if json.loads(line).get("date") == target:
                return True
        except json.JSONDecodeError:
            continue
    return False


def record(day, safe, bomb, note: str = "") -> None:
    """Append one line to history — the ROI ledger and the duplicate guard."""
    rec = {"date": day.isoformat(),
           "generated_at": datetime.now(TZ).isoformat(),
           "safe": safe.as_dict() if safe else None,
           "bomb": bomb.as_dict() if bomb else None}
    if note:
        rec["note"] = note
    with config.HISTORY_FILE.open("a") as fh:
        fh.write(json.dumps(rec) + "\n")
    log.info("appended to %s", config.HISTORY_FILE)


def chat_ids() -> list[str]:
    ids = list(config.DEFAULT_CHAT_IDS)
    if config.SUBS_FILE.exists():
        try:
            ids += [str(c) for c in json.loads(config.SUBS_FILE.read_text())]
        except Exception:  # noqa: BLE001
            pass
    return sorted(set(i for i in ids if i))


async def send(text: str, dry: bool) -> None:
    if dry:
        print(re.sub(r"<[^>]+>", "", text))
        print("-" * 60)
        return
    targets = chat_ids()
    if not targets:
        log.warning("No CHAT_IDS configured — nothing to send")
        print(re.sub(r"<[^>]+>", "", text))
        return
    async with httpx.AsyncClient(timeout=30) as c:
        for cid in targets:
            try:
                r = await c.post(API.format(token=config.BOT_TOKEN), json={
                    "chat_id": cid, "text": text, "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                })
                if r.status_code != 200:
                    log.error("telegram %s: %s", r.status_code, r.text[:200])
            except Exception as exc:  # noqa: BLE001
                log.error("send failed for %s: %s", cid, exc)


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true",
                    help="send again even if today's card was already posted")
    args = ap.parse_args()

    today = datetime.now(TZ).date()
    if already_sent(today) and not (args.force or args.dry_run):
        log.info("%s already posted — nothing to do (use --force to resend)", today)
        return 0

    if not config.BOT_TOKEN and not args.dry_run:
        log.error("BOT_TOKEN missing")
        return 1

    # 1 ---------------------------------------------------- settle yesterday
    try:
        report = await settle.grade_all()
        if report.get("rows"):
            await send(settle.render_report(report), args.dry_run)
    except Exception as exc:  # noqa: BLE001
        log.error("settlement failed (continuing): %s", exc)

    # 2 ------------------------------------------------------ analyse today
    provider = get_provider()
    day = today
    try:
        matches = await provider.fixtures(day)
    finally:
        await provider.close()

    if not matches:
        await send(
            "😴 <b>No fixtures to analyse right now.</b>\n"
            "<i>International break, or the free fixtures file hasn't been "
            "refreshed yet. I'll check again tomorrow.</i>", args.dry_run)
        # Still record the day, so the backup cron slot knows it ran.
        if not args.dry_run:
            record(day, None, None, note="no fixtures")
        return 0

    sels = build_selections(matches)
    safe, bomb = build_safe(sels), build_bomb(sels)

    # 3 --------------------------------------------------------------- send
    for msg in fmt.render_daily(day, len(matches), provider.name, safe, bomb):
        await send(msg, args.dry_run)

    # 4 ------------------------------------------------------------ persist
    if not args.dry_run:
        record(day, safe, bomb)

    log.info("done: %d fixtures, safe=%s bomb=%s", len(matches),
             f"{safe.total_odds:.2f}" if safe else "-",
             f"{bomb.total_odds:.2f}" if bomb else "-")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
