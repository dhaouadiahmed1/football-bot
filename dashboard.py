"""A single-page status dashboard served on the bot's health port.

Deliberately dependency-free and synchronous: it is rendered from inside the
health server's thread, which has no event loop, so it only ever reads files
that are already on disk. It never calls Telegram or the odds APIs, so hitting
refresh can't burn your 100-request daily quota.
"""
from __future__ import annotations

import base64
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

import config

HERE = Path(__file__).resolve().parent
TZ = ZoneInfo(config.TIMEZONE)

BG, CARD, LINE = "#0d1522", "#141f33", "#243busy"
LINE = "#243350"
TEXT, MUTED = "#e8eef7", "#8699b4"
GREEN, RED, AMBER, BLUE = "#34d399", "#f87171", "#fbbf24", "#60a5fa"


# ── data gathering ────────────────────────────────────────────────────────────

def _history() -> list[dict]:
    p = config.HISTORY_FILE
    if not p.exists():
        return []
    out = []
    for line in p.read_text().splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return out


def _quota() -> tuple[int, int]:
    p = HERE / "data" / "api_usage.json"
    used = 0
    if p.exists():
        try:
            d = json.loads(p.read_text())
            if d.get("date") == datetime.now(TZ).date().isoformat():
                used = int(d.get("count", 0))
        except (json.JSONDecodeError, ValueError):
            pass
    return used, config.APIFOOTBALL_DAILY_BUDGET


def _cron() -> Optional[tuple[int, int]]:
    """Read the schedule straight from the workflow, so this can't go stale."""
    p = HERE / ".github" / "workflows" / "daily.yml"
    if not p.exists():
        return None
    m = re.search(r'cron:\s*"(\d+)\s+(\d+)\s+\*\s+\*\s+\*"', p.read_text())
    return (int(m.group(2)), int(m.group(1))) if m else None


def _next_run() -> Optional[datetime]:
    c = _cron()
    if not c:
        return None
    hh, mm = c
    now = datetime.now(timezone.utc)
    nxt = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if nxt <= now:
        nxt += timedelta(days=1)
    return nxt


def _record(rows: list[dict]) -> dict:
    out = {}
    for kind in ("safe", "bomb"):
        won = lost = pending = 0
        ret = 0.0
        for r in rows:
            t, g = r.get(kind), r.get(f"{kind}_result") or {}
            if not t:
                continue
            st = g.get("status")
            if st == "won":
                won += 1
                ret += float(t.get("total_odds", 0))
            elif st == "lost":
                lost += 1
            else:
                pending += 1
        n = won + lost
        out[kind] = {
            "won": won, "lost": lost, "pending": pending, "settled": n,
            "hit": won / n if n else None,
            "roi": (ret - n) / n if n else None,
            "staked": n, "returned": ret,
        }
    return out


def _avatar() -> str:
    for name in ("bot_avatar_96.png", "bot_avatar_512.png"):
        p = HERE / "assets" / name
        if p.exists():
            return ("data:image/png;base64,"
                    + base64.b64encode(p.read_bytes()).decode())
    return ""


# ── rendering ─────────────────────────────────────────────────────────────────

def _esc(s) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def _pct(x: Optional[float], plus=False) -> str:
    if x is None:
        return "—"
    return f"{'+' if plus and x >= 0 else ''}{x * 100:.1f}%"


def _ticket_card(t: Optional[dict], kind: str, result: dict) -> str:
    colour = GREEN if kind == "SAFE" else AMBER
    emoji = "⚽" if kind == "SAFE" else "💣"
    if not t:
        return f"""
        <div class="card">
          <div class="chead"><span class="dot" style="background:{colour}"></span>
            {emoji} {kind}</div>
          <p class="empty">No ticket — not enough qualifying matches today.</p>
        </div>"""

    st = (result or {}).get("status", "pending")
    badge = {"won": (GREEN, "WON"), "lost": (RED, "LOST")}.get(st, (BLUE, "PENDING"))
    legs = "".join(f"""
        <tr>
          <td class="lg">{_esc(l.get('match', ''))}
              <span class="sub">{_esc(l.get('league', ''))} · {_esc(l.get('kickoff', ''))}</span></td>
          <td class="mk">{_esc(l.get('market_name', l.get('market', '')))}</td>
          <td class="od">{float(l.get('odds', 0)):.2f}</td>
          <td class="pr">{float(l.get('prob', 0)) * 100:.0f}%</td>
        </tr>""" for l in t.get("legs", []))

    return f"""
    <div class="card">
      <div class="chead">
        <span class="dot" style="background:{colour}"></span>{emoji} {kind}
        <span class="badge" style="background:{badge[0]}22;color:{badge[0]};
              border-color:{badge[0]}55">{badge[1]}</span>
      </div>
      <div class="nums">
        <div><b>{float(t.get('total_odds', 0)):.2f}</b><span>total odds</span></div>
        <div><b>{float(t.get('combined_prob', 0)) * 100:.1f}%</b><span>model prob</span></div>
        <div><b>{len(t.get('legs', []))}</b><span>legs</span></div>
      </div>
      <table>{legs}</table>
    </div>"""


def render() -> str:
    rows = _history()
    latest = rows[-1] if rows else {}
    rec = _record(rows)
    used, budget = _quota()
    nxt = _next_run()
    now = datetime.now(TZ)
    avatar = _avatar()

    pushing = "this machine" if config.DAILY_PUSH else "GitHub Actions"
    quota_pct = min(100, round(used / budget * 100)) if budget else 0
    qcol = GREEN if quota_pct < 60 else AMBER if quota_pct < 90 else RED

    nxt_local = nxt.astimezone(TZ).strftime("%a %d %b, %H:%M") if nxt else "not scheduled"
    nxt_iso = nxt.isoformat() if nxt else ""

    def stat(label, value, sub="", colour=TEXT):
        return f"""<div class="stat"><span class="lab">{label}</span>
                   <b style="color:{colour}">{value}</b>
                   <span class="sub">{sub}</span></div>"""

    def recblock(kind):
        r = rec[kind]
        if not r["settled"]:
            return f"""<div class="rec"><h4>{kind.upper()}</h4>
              <p class="empty">{r['pending']} pending, nothing settled yet.</p></div>"""
        roi = r["roi"]
        col = GREEN if roi and roi > 0 else RED
        return f"""<div class="rec">
            <h4>{kind.upper()}</h4>
            <div class="rrow"><span>settled</span><b>{r['settled']}</b></div>
            <div class="rrow"><span>won</span><b>{r['won']}</b></div>
            <div class="rrow"><span>hit rate</span><b>{_pct(r['hit'])}</b></div>
            <div class="rrow"><span>ROI</span><b style="color:{col}">{_pct(roi, True)}</b></div>
            <div class="rrow"><span>pending</span><b>{r['pending']}</b></div>
          </div>"""

    return f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Football Tickets — status</title>
<style>
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{background:{BG};color:{TEXT};font:15px/1.5 -apple-system,BlinkMacSystemFont,
       "Segoe UI",Roboto,Helvetica,Arial,sans-serif;padding:28px 20px 60px}}
  .wrap{{max-width:940px;margin:0 auto}}
  header{{display:flex;align-items:center;gap:16px;margin-bottom:26px}}
  header img{{width:62px;height:62px;border-radius:50%;flex:none;
              box-shadow:0 0 0 2px {LINE}}}
  header h1{{font-size:21px;font-weight:650;letter-spacing:-.2px}}
  header .handle{{color:{MUTED};font-size:13.5px}}
  .live{{margin-left:auto;display:flex;align-items:center;gap:7px;font-size:13px;
         color:{GREEN};background:{GREEN}14;border:1px solid {GREEN}33;
         padding:6px 13px;border-radius:999px;white-space:nowrap}}
  .pulse{{width:7px;height:7px;border-radius:50%;background:{GREEN};
          animation:p 2s infinite}}
  @keyframes p{{0%,100%{{opacity:1}}50%{{opacity:.25}}}}
  .grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(168px,1fr));
         gap:12px;margin-bottom:22px}}
  .stat{{background:{CARD};border:1px solid {LINE};border-radius:12px;padding:14px 16px}}
  .stat .lab{{display:block;color:{MUTED};font-size:11.5px;text-transform:uppercase;
              letter-spacing:.6px;margin-bottom:5px}}
  .stat b{{display:block;font-size:19px;font-weight:620;letter-spacing:-.3px}}
  .stat .sub,.sub{{color:{MUTED};font-size:12px;font-weight:400}}
  .bar{{height:4px;background:{LINE};border-radius:3px;margin-top:9px;overflow:hidden}}
  .bar i{{display:block;height:100%;background:{qcol};width:{quota_pct}%}}
  .cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(360px,1fr));gap:14px}}
  .card{{background:{CARD};border:1px solid {LINE};border-radius:14px;padding:18px 20px}}
  .chead{{display:flex;align-items:center;gap:9px;font-weight:620;font-size:15px;
          margin-bottom:14px}}
  .dot{{width:9px;height:9px;border-radius:50%}}
  .badge{{margin-left:auto;font-size:10.5px;font-weight:700;letter-spacing:.7px;
          padding:3px 9px;border-radius:999px;border:1px solid}}
  .nums{{display:flex;gap:26px;padding-bottom:14px;margin-bottom:6px;
         border-bottom:1px solid {LINE}}}
  .nums b{{display:block;font-size:22px;font-weight:650;letter-spacing:-.5px}}
  .nums span{{color:{MUTED};font-size:11.5px}}
  table{{width:100%;border-collapse:collapse;font-size:13.5px}}
  td{{padding:9px 0;border-bottom:1px solid {LINE}}}
  tr:last-child td{{border-bottom:none}}
  td.lg{{font-weight:530}} td.lg .sub{{display:block;font-size:11.5px;margin-top:1px}}
  td.mk{{color:{MUTED};text-align:right;padding-right:14px;white-space:nowrap}}
  td.od{{text-align:right;font-weight:650;font-variant-numeric:tabular-nums;width:54px}}
  td.pr{{text-align:right;color:{MUTED};font-variant-numeric:tabular-nums;width:46px}}
  .empty{{color:{MUTED};font-size:13.5px;padding:6px 0}}
  h3{{font-size:12px;text-transform:uppercase;letter-spacing:.8px;color:{MUTED};
      margin:26px 0 12px}}
  .recs{{display:grid;grid-template-columns:1fr 1fr;gap:14px}}
  .rec{{background:{CARD};border:1px solid {LINE};border-radius:14px;padding:16px 20px}}
  .rec h4{{font-size:13px;letter-spacing:.5px;margin-bottom:10px}}
  .rrow{{display:flex;justify-content:space-between;padding:4px 0;font-size:13.5px}}
  .rrow span{{color:{MUTED}}}
  footer{{margin-top:26px;padding-top:16px;border-top:1px solid {LINE};
          color:{MUTED};font-size:12.5px;line-height:1.7}}
  @media(max-width:620px){{.recs{{grid-template-columns:1fr}}header .live{{display:none}}}}
</style></head><body><div class="wrap">

<header>
  {'<img src="' + avatar + '" alt="">' if avatar else ''}
  <div><h1>Football Tickets — Daily</h1>
       <div class="handle">@my_foot_ball_tips_bot</div></div>
  <div class="live"><span class="pulse"></span>bot online</div>
</header>

<div class="grid">
  {stat("Next post", nxt_local, f'<span id="cd">in …</span> · {config.TIMEZONE}')}
  {stat("Sent by", pushing, "daily push" if config.DAILY_PUSH else "cron workflow")}
  {stat("Data source", "hybrid", "free CSV + API-Football")}
  <div class="stat"><span class="lab">API quota today</span>
    <b style="color:{qcol}">{used} / {budget}</b>
    <span class="sub">hard stop before 100</span><div class="bar"><i></i></div></div>
</div>

<h3>Latest card{' · ' + _esc(latest.get('date', '')) if latest else ''}</h3>
<div class="cards">
  {_ticket_card(latest.get('safe'), 'SAFE', latest.get('safe_result'))}
  {_ticket_card(latest.get('bomb'), 'BOMB', latest.get('bomb_result'))}
</div>

<h3>Settled record · {len(rows)} day{'s' if len(rows) != 1 else ''} recorded</h3>
<div class="recs">{recblock('safe')}{recblock('bomb')}</div>

<footer>
  Rendered {now.strftime('%d %b %Y, %H:%M')} {config.TIMEZONE} · refreshes itself every 30s.<br>
  Backtested over 3 seasons the SAFE ticket returned <b>−3.9%</b> and the BOMB
  <b>+14.0%</b>, but at 30× odds that second figure rests on one extra winner and
  the interval spans roughly ±65 points. Treat both as entertainment, not income.
</footer>

</div><script>
  var t = "{nxt_iso}";
  if (t) {{
    var el = document.getElementById("cd");
    setInterval(function () {{
      var s = Math.max(0, (new Date(t) - new Date()) / 1000),
          h = Math.floor(s / 3600), m = Math.floor(s % 3600 / 60);
      if (el) el.textContent = "in " + h + "h " + String(m).padStart(2, "0") + "m";
    }}, 1000);
  }}
  setTimeout(function () {{ location.reload(); }}, 30000);
</script></body></html>"""
