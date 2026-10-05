#!/usr/bin/env python3
"""Wait for you to message the bot, then save your chat id into .env.

    python link_chat.py

Open Telegram, find your bot, press Start (or send anything). This picks up
the message, writes CHAT_IDS into .env, and replies to confirm delivery works.
"""
from __future__ import annotations

import re
import sys
import time
from pathlib import Path

import httpx

import config

ENV = Path(__file__).resolve().parent / ".env"
API = f"https://api.telegram.org/bot{config.BOT_TOKEN}"


def write_chat_ids(ids: list[str]) -> None:
    value = ",".join(dict.fromkeys(ids))
    text = ENV.read_text() if ENV.exists() else ""
    if re.search(r"^CHAT_IDS=.*$", text, flags=re.M):
        text = re.sub(r"^CHAT_IDS=.*$", f"CHAT_IDS={value}", text, flags=re.M)
    else:
        text += f"\nCHAT_IDS={value}\n"
    ENV.write_text(text)


def main(timeout_s: int = 900) -> int:
    if not config.BOT_TOKEN:
        print("BOT_TOKEN is not set — put it in .env first.")
        return 1

    me = httpx.get(f"{API}/getMe", timeout=20).json()
    if not me.get("ok"):
        print("Token rejected:", me.get("description"))
        return 1
    username = me["result"]["username"]

    print(f"Waiting for a message to @{username} …")
    print(f"  1. Open Telegram and search:  @{username}")
    print("  2. Press START (or send any text)")
    print(f"  (giving up after {timeout_s // 60} minutes)\n")
    sys.stdout.flush()

    deadline = time.time() + timeout_s
    offset = None
    found: list[str] = []

    while time.time() < deadline and not found:
        try:
            params = {"timeout": 25}
            if offset is not None:
                params["offset"] = offset
            r = httpx.get(f"{API}/getUpdates", params=params, timeout=40)
            data = r.json()
        except Exception as exc:  # noqa: BLE001
            print("poll error:", exc)
            time.sleep(3)
            continue

        for u in data.get("result", []):
            offset = u["update_id"] + 1
            msg = u.get("message") or u.get("edited_message") or {}
            chat = msg.get("chat") or {}
            cid = chat.get("id")
            if not cid:
                continue
            who = chat.get("first_name") or chat.get("title") or "?"
            print(f"✅ got a message from {who} (chat id {cid})")
            found.append(str(cid))

    if not found:
        print("\n⏰ Nobody messaged the bot. Run this again when you're ready.")
        return 1

    write_chat_ids(found)
    print(f"✅ saved CHAT_IDS={','.join(found)} to .env")

    for cid in found:
        httpx.post(f"{API}/sendMessage", json={
            "chat_id": cid, "parse_mode": "HTML",
            "text": ("✅ <b>Linked!</b>\n\nYour chat is now connected.\n"
                     "Try <b>/today</b> for the analysis, or <b>/status</b> "
                     "to see the data source and settings."),
        }, timeout=20)
    print("✅ confirmation message sent — check Telegram")
    return 0


if __name__ == "__main__":
    sys.exit(main())
