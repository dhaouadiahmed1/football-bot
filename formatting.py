"""Telegram HTML rendering."""
from __future__ import annotations

from datetime import date
from html import escape
from typing import List, Optional

from engine.markets import Selection
from engine.tickets import Ticket

DISCLAIMER = (
    "<i>Model output, not a prediction. Betting is risky — stake only what you "
    "can afford to lose. 18+.</i>"
)


def _pct(x: float) -> str:
    return f"{x * 100:.1f}%"


def header(day: date, n_matches: int, provider: str) -> str:
    return (
        f"<b>⚽ Daily Football Analysis — {day.strftime('%a %d %b %Y')}</b>\n"
        f"<i>{n_matches} fixtures scanned · source: {escape(provider)}</i>"
    )


def render_ticket(t: Optional[Ticket], title: str, emoji: str, note: str = "") -> str:
    if t is None or not t.legs:
        return f"{emoji} <b>{title}</b>\nNo qualifying ticket today — the card is too thin."

    lines = [f"{emoji} <b>{title}</b>"]
    if note:
        lines.append(f"<i>{note}</i>")
    for i, s in enumerate(t.legs, 1):
        lines.append(
            f"\n<b>{i}. {escape(s.label)}</b>  <code>{escape((s.day + ' ' + s.kickoff).strip())}</code>"
            f"\n   <i>{escape(s.league)}</i>"
            f"\n   ✅ <b>{escape(s.market_name)}</b> @ <b>{s.odds:.2f}</b>"
            f"\n   model {_pct(s.prob)} · edge {s.edge * 100:+.1f}pts · "
            f"xG {s.xg_home:.2f}-{s.xg_away:.2f} · {s.stars}"
        )
    import config as _cfg
    short = t.kind == "BOMB" and t.total_odds < _cfg.TARGET_ODDS * 0.98
    if short:
        lines.append(
            f"\n⚠️ <i>Today's card can't reach {_cfg.TARGET_ODDS:.0f}x without legs "
            f"I don't trust. This is the best honest ticket available.</i>")
    lines.append(
        f"\n━━━━━━━━━━━━━━━\n"
        f"<b>Total odds: {t.total_odds:.2f}</b>  ({len(t.legs)} legs)\n"
        f"Win probability: <b>{_pct(t.combined_prob)}</b>  (fair price {t.fair_odds:.2f})\n"
        f"Expected value: <b>{t.ev * 100:+.1f}%</b> per unit staked\n"
        f"<i>Prices are best-available — shop around, it is worth ~4% a leg.</i>"
    )
    return "\n".join(lines)


def render_daily(day: date, n_matches: int, provider: str,
                 safe: Optional[Ticket], bomb: Optional[Ticket]) -> List[str]:
    """Returns a list of messages (Telegram caps at 4096 chars)."""
    msgs = [
        header(day, n_matches, provider) + "\n\n"
        + render_ticket(safe, "SAFE TICKET", "🛡",
                        "High-probability legs only — built to land, not to pay big."),
        render_ticket(bomb, "BOMB TICKET (30x+)", "💣",
                      "Cheapest route to 30.00 — maximum odds for minimum probability lost.")
        + "\n\n" + DISCLAIMER,
    ]
    return msgs


def render_value_board(sels: List[Selection], limit: int = 12) -> str:
    top = sorted(sels, key=lambda s: -s.edge)[:limit]
    if not top:
        return "No value selections found today."
    out = ["📊 <b>Top value selections</b> (model vs bookmaker)\n"]
    for s in top:
        out.append(
            f"• <b>{escape(s.label)}</b> — {escape(s.market_name)} @ {s.odds:.2f}\n"
            f"  model {_pct(s.prob)} vs book {_pct(s.implied)} → "
            f"<b>{s.edge * 100:+.1f}pts</b>, EV {s.ev * 100:+.1f}%"
        )
    return "\n".join(out)


def render_status(s: dict) -> str:
    """Operational status: data source, quota, config, track record."""
    L = [f"⚙️ <b>Bot status</b>  <i>{escape(s['now'])}</i>", ""]

    L.append("<b>Data</b>")
    L.append(f"  mode: <code>{escape(s['provider_mode'])}</code>")
    L.append(f"  using: {escape(s['source'])}")
    if s["fixtures"] is None:
        L.append("  ❌ could not load today's card")
    else:
        icon = "✅" if s["fixtures"] >= s["min_card"] else "⚠️"
        L.append(f"  {icon} {s['fixtures']} fixtures · {s['selections']} selections")
        if s["fixtures"] == 0:
            L.append("  <i>no matches in the tracked leagues today — "
                     "international break or an off-day, not a fault</i>")
        elif s["fixtures"] < s["min_card"]:
            L.append(f"  <i>thin card (want {s['min_card']}+) — "
                     f"{'API will top it up' if s['api_key'] else 'add an API key to fill gaps'}</i>")

    L.append("")
    L.append("<b>API-Football</b>")
    if not s["api_key"]:
        L.append("  not configured <i>(optional — free source covers most days)</i>")
    elif s.get("api_error"):
        L.append(f"  ❌ {escape(str(s['api_error']))}")
    else:
        used, limit = s.get("api_used", "?"), s.get("api_limit", "?")
        bar = ""
        if isinstance(used, int) and isinstance(limit, int) and limit:
            n = max(0, min(10, round(used / limit * 10)))
            bar = f"  [{'█' * n}{'░' * (10 - n)}]"
            left = limit - used
            L.append(f"  ✅ plan <b>{escape(str(s.get('api_plan','?')))}</b> · "
                     f"<b>{used}/{limit}</b> today{bar}")
            L.append(f"  {left} requests left · resets midnight UTC")
        else:
            L.append(f"  ✅ plan {escape(str(s.get('api_plan','?')))} · {used}/{limit} today")
        L.append(f"  local guard: {s['local_used']}/{s['local_budget']}")

    L.append("")
    L.append("<b>Settings</b>")
    L.append(f"  target odds: <b>{s['target']:.0f}x</b> · "
             f"prices: <code>{escape(s['price_source'])}</code>")
    L.append(f"  model weight: {s['model_weight']:.2f} · "
             f"de-vig: <code>{escape(s['devig'])}</code>")
    L.append(f"  daily push: <b>{s['daily_time']}</b> {escape(s['tz'])} "
             f"<i>({escape(s.get('pushed_by', 'this instance'))})</i>")
    L.append(f"  {s['subscribers']} subscriber(s)")

    L.append("")
    L.append("<b>Track record</b>")
    if not s["history_days"]:
        L.append("  nothing recorded yet — /today starts the log")
    else:
        L.append(f"  {s['history_days']} day(s) logged")
        for kind, emoji in (("safe", "🛡"), ("bomb", "💣")):
            r = s["record"].get(kind) or {}
            if r.get("n"):
                L.append(f"  {emoji} {kind.upper()}: {r['won']}/{r['n']} · "
                         f"<b>{r['profit']:+.2f}u</b> · ROI {r['roi']*100:+.1f}%")
                if r["n"] < 30:
                    L.append(f"     <i>only {r['n']} settled — noise, not a record</i>")
            else:
                L.append(f"  {emoji} {kind.upper()}: no settled tickets yet")
        L.append("  <i>/roi for the full breakdown</i>")
    return "\n".join(L)


def render_matches(matches, limit: int = 25) -> str:
    out = ["🗓 <b>Today's card</b>\n"]
    for m in matches[:limit]:
        out.append(
            f"<code>{escape(m.when)}</code> {escape(m.label)} "
            f"<i>({escape(m.country)})</i>"
        )
    if len(matches) > limit:
        out.append(f"\n<i>… and {len(matches) - limit} more</i>")
    return "\n".join(out)
