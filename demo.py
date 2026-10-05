#!/usr/bin/env python3
"""Run the full pipeline in the terminal — no Telegram token needed.

    python demo.py            # today
    python demo.py 2026-10-07 # any date
    python demo.py --bomb 60  # custom target odds
"""
from __future__ import annotations

import asyncio
import re
import sys
from datetime import date, datetime

import config
from engine import build_bomb, build_safe, build_selections
from providers import get_provider


def strip_html(s: str) -> str:
    return re.sub(r"<[^>]+>", "", s)


async def main() -> None:
    args = sys.argv[1:]
    target = config.TARGET_ODDS
    if "--bomb" in args:
        i = args.index("--bomb")
        target = float(args[i + 1])
        del args[i:i + 2]
    day = datetime.strptime(args[0], "%Y-%m-%d").date() if args else date.today()

    provider = get_provider()
    matches = await provider.fixtures(day)
    sels = build_selections(matches)
    safe, bomb = build_safe(sels), build_bomb(sels, target)

    import formatting as fmt
    print(strip_html(fmt.header(day, len(matches), provider.name)))
    print()
    print(strip_html(fmt.render_ticket(safe, "SAFE TICKET", "[SAFE]")))
    print("\n" + "=" * 46 + "\n")
    print(strip_html(fmt.render_ticket(bomb, f"BOMB TICKET ({target:.0f}x+)", "[BOMB]")))
    print("\n" + "=" * 46 + "\n")
    print(strip_html(fmt.render_value_board(sels, limit=8)))
    await provider.close()


if __name__ == "__main__":
    asyncio.run(main())
