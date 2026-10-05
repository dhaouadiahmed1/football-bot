"""Grade stored tickets against real results, and report true ROI.

Reads data/history.jsonl, finds every ticket that is still pending, downloads
the (free) results CSVs it needs, settles each leg, and writes the verdict
back. This is what turns the bot from "a model with opinions" into something
you can actually hold accountable.
"""
from __future__ import annotations

import json
import logging
from collections import defaultdict
from typing import Dict, List, Tuple

import config
from engine.grading import settle_ticket, summarise
from providers.footballdata_uk import FootballDataUKProvider, parse_date, season_code

log = logging.getLogger(__name__)


def _load(path=None) -> List[dict]:
    path = path or config.HISTORY_FILE
    if not path.exists():
        return []
    rows = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if line:
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def _save(rows: List[dict], path=None) -> None:
    path = path or config.HISTORY_FILE
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n")


def _match_ids(rows: List[dict]) -> List[str]:
    out = []
    for r in rows:
        for kind in ("safe", "bomb"):
            t = r.get(kind)
            if t and r.get(f"{kind}_result", {}).get("status") in (None, "pending"):
                out += [l.get("match_id", "") for l in t.get("legs", [])]
    return [m for m in out if m.count("|") == 3]


async def results_for(match_ids: List[str]) -> Dict[str, Tuple[int, int]]:
    """Fetch final scores, asking whichever source produced each ticket."""
    scores: Dict[str, Tuple[int, int]] = {}

    csv_ids = [m for m in match_ids if not m.startswith("AF|")]
    api_ids = [m for m in match_ids if m.startswith("AF|")]

    # ---- free CSV tickets --------------------------------------------------
    if csv_ids:
        need = defaultdict(set)
        for mid in csv_ids:
            div, dstr, _, _ = mid.split("|")
            d = parse_date(dstr)
            if d:
                need[season_code(d)].add(div)
        prov = FootballDataUKProvider()
        try:
            for season, divs in need.items():
                for div in divs:
                    for r in await prov.results(div, season):
                        key = (f"{div}|{r['Date'].strip()}|"
                               f"{r['HomeTeam'].strip()}|{r['AwayTeam'].strip()}")
                        scores[key] = (r["_hg"], r["_ag"])
        finally:
            await prov.close()

    # ---- API-Football tickets ---------------------------------------------
    if api_ids and config.APIFOOTBALL_KEY:
        try:
            from providers.apifootball import APIFootballProvider
            api = APIFootballProvider()
            try:
                fids = [m.split("|")[1] for m in api_ids]
                by_fid = await api.results(fids)
            finally:
                await api.close()
            for mid in api_ids:
                fid = mid.split("|")[1]
                if fid in by_fid:
                    scores[mid] = by_fid[fid]
        except Exception as exc:  # noqa: BLE001
            log.warning("could not settle API-Football tickets: %s", exc)

    return scores


async def grade_all(path=None) -> dict:
    """Settle everything settleable. Returns a report dict."""
    rows = _load(path)
    if not rows:
        return {"newly_settled": [], "safe": summarise([]), "bomb": summarise([])}

    ids = _match_ids(rows)
    scores = await results_for(ids) if ids else {}

    newly = []
    for r in rows:
        for kind in ("safe", "bomb"):
            t = r.get(kind)
            if not t:
                continue
            prev = r.get(f"{kind}_result", {}).get("status")
            if prev in ("won", "lost"):
                continue
            g = settle_ticket(t, scores)
            r[f"{kind}_result"] = g
            if g["status"] in ("won", "lost") and prev != g["status"]:
                newly.append({"date": r.get("date"), "kind": kind,
                              "odds": t["total_odds"], **g})
    _save(rows, path)

    graded = {k: [r[f"{k}_result"] for r in rows if r.get(f"{k}_result")]
              for k in ("safe", "bomb")}
    return {"newly_settled": newly,
            "safe": summarise(graded["safe"]),
            "bomb": summarise(graded["bomb"]),
            "rows": len(rows)}


def render_report(rep: dict) -> str:
    """HTML summary for Telegram."""
    if not rep.get("rows"):
        return "No tickets recorded yet — run /today or wait for the daily post."

    lines = []
    for n in rep["newly_settled"]:
        icon = "✅ WON" if n["status"] == "won" else "❌ lost"
        lines.append(f"{icon} — {n['kind'].upper()} {n['date']} @ {n['odds']:.2f} "
                     f"({n['legs_won']}/{n['legs_total']} legs) "
                     f"<b>{n['profit']:+.2f}u</b>")
    if lines:
        lines.insert(0, "<b>🧾 Settled since last check</b>")
        lines.append("")

    lines.append("<b>📊 Lifetime record</b> <i>(1 unit flat stake)</i>")
    for kind, emoji in (("safe", "🛡"), ("bomb", "💣")):
        s = rep[kind]
        if not s["n"]:
            lines.append(f"{emoji} {kind.upper()}: no settled tickets yet")
            continue
        lines.append(
            f"{emoji} <b>{kind.upper()}</b>: {s['won']}/{s['n']} "
            f"({s['hit_rate']*100:.1f}%) · P/L <b>{s['profit']:+.2f}u</b> · "
            f"ROI <b>{s['roi']*100:+.1f}%</b> · worst run {s['max_drawdown']:.1f}u"
        )
    return "\n".join(lines)
