#!/usr/bin/env python3
"""Set the bot's public profile: name, descriptions and avatar.

Everything here is done through the Bot API, so you never need to click
through @BotFather. Run it again any time you want to change the wording:

    python setup_profile.py              # apply
    python setup_profile.py --show       # just read back what's live now

Telegram's limits are enforced before anything is sent:
    name 64 · short description 120 · description 512 characters
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv

HERE = Path(__file__).resolve().parent
load_dotenv(HERE / ".env")

AVATAR = HERE / "assets" / "bot_avatar_512.png"

# ── what people see ───────────────────────────────────────────────────────────

# Shown as the bot's title everywhere.
NAME = "Football Tickets — Daily"

# The one-liner under the name on the profile card, and in link previews
# when someone shares the bot.
SHORT = (
    "Two football accumulators every morning at 07:00: one safe, "
    "one long shot. Free data, backtested, settled honestly."
)

# Shown in the empty chat, above the Start button — this is the pitch.
DESCRIPTION = (
    "Every morning at 07:00 I read the day's fixtures and post two accumulators.\n\n"
    "⚽ SAFE — 3 to 5 high-probability legs, around 2.00 total\n"
    "💣 BOMB — a long shot priced near 30.00\n\n"
    "Odds are taken at the best available book, de-vigged and blended with a "
    "form model. Every ticket is settled automatically, so /roi shows the real "
    "running record — wins and losses both.\n\n"
    "Tap the menu for /today, /safe, /bomb, /matches, /roi and /status.\n\n"
    "For entertainment only. Never stake what you can't afford to lose."
)

LIMITS = {"name": 64, "short": 120, "description": 512}

GREEN, RED, YELLOW, BOLD, OFF = "\033[32m", "\033[31m", "\033[33m", "\033[1m", "\033[0m"
ok, bad, warn = f"{GREEN}✅{OFF}", f"{RED}❌{OFF}", f"{YELLOW}⚠{OFF}"


def api(client: httpx.Client, base: str, method: str, **kw) -> dict:
    r = client.post(f"{base}/{method}", **kw).json()
    if not r.get("ok"):
        print(f"  {bad} {method}: {r.get('description')}")
    return r


def show(client: httpx.Client, base: str) -> None:
    me = client.get(f"{base}/getMe").json()["result"]
    name = client.get(f"{base}/getMyName").json()["result"]["name"]
    short = client.get(f"{base}/getMyShortDescription").json()["result"]["short_description"]
    desc = client.get(f"{base}/getMyDescription").json()["result"]["description"]
    photos = client.get(f"{base}/getUserProfilePhotos",
                        params={"user_id": me["id"]}).json()["result"]

    print(f"{BOLD}\n  @{me['username']}{OFF}")
    print(f"  name   {name}")
    print(f"  short  {short}")
    print("  about  " + "\n         ".join(desc.splitlines()))
    print(f"  avatar {'set' if photos['total_count'] else 'NOT SET'}")
    cmds = client.get(f"{base}/getMyCommands").json()["result"]
    print(f"  menu   {len(cmds)} commands: " + " ".join("/" + c["command"] for c in cmds))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--show", action="store_true", help="read back the live profile, change nothing")
    ap.add_argument("--skip-avatar", action="store_true", help="text only, leave the picture alone")
    args = ap.parse_args()

    token = os.getenv("BOT_TOKEN")
    if not token:
        print(f"{bad} BOT_TOKEN missing from .env")
        return 1
    base = f"https://api.telegram.org/bot{token}"

    with httpx.Client(timeout=60) as client:
        if args.show:
            show(client, base)
            return 0

        for field, value, limit in (("name", NAME, 64),
                                    ("short", SHORT, 120),
                                    ("description", DESCRIPTION, 512)):
            if len(value) > limit:
                print(f"{bad} {field} is {len(value)} chars, Telegram allows {limit}")
                return 1

        print(f"{BOLD}\n✏️  Updating the bot profile{OFF}\n")
        if api(client, base, "setMyName", data={"name": NAME}).get("ok"):
            print(f"  {ok} name ({len(NAME)}/64)")
        if api(client, base, "setMyShortDescription",
               data={"short_description": SHORT}).get("ok"):
            print(f"  {ok} short description ({len(SHORT)}/120)")
        if api(client, base, "setMyDescription",
               data={"description": DESCRIPTION}).get("ok"):
            print(f"  {ok} description ({len(DESCRIPTION)}/512)")

        if args.skip_avatar:
            print(f"  {warn} avatar skipped")
        elif not AVATAR.exists():
            print(f"  {warn} no avatar at {AVATAR.relative_to(HERE)}")
        else:
            with AVATAR.open("rb") as fh:
                r = api(client, base, "setMyProfilePhoto",
                        data={"photo": json.dumps({"type": "static",
                                                   "photo": "attach://pic"})},
                        files={"pic": ("avatar.png", fh, "image/png")})
            if r.get("ok"):
                print(f"  {ok} avatar uploaded")

        print(f"{BOLD}\n  live now:{OFF}")
        show(client, base)
        print("\n  Telegram caches profiles hard — if your own client still shows")
        print("  the old picture, clear the chat cache or just wait a few minutes.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
