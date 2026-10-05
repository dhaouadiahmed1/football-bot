"""Offline tests for the API-Football provider:  python test_api.py

Uses httpx.MockTransport with realistic API-Football v3 payloads, so the
parsing, odds mapping, quota guard and settlement are all verified WITHOUT
spending a single request from your 100/day free quota.
"""
from __future__ import annotations

import asyncio
import json
from datetime import date

import httpx

import config

config.APIFOOTBALL_KEY = "test-key-not-real"
config.DATA_DIR.mkdir(exist_ok=True)

from providers.apifootball import (  # noqa: E402
    APIFootballProvider, QuotaExceeded, season_for,
)

DAY = date(2026, 10, 10)

FIXTURES = {"response": [
    {"fixture": {"id": 1001, "date": "2026-10-10T18:30:00+01:00",
                 "status": {"short": "NS"}},
     "league": {"id": 39, "name": "Premier League", "country": "England",
                "season": 2026},
     "teams": {"home": {"id": 42, "name": "Arsenal"},
               "away": {"id": 45, "name": "Everton"}}},
    {"fixture": {"id": 1002, "date": "2026-10-10T21:00:00+01:00",
                 "status": {"short": "NS"}},
     "league": {"id": 39, "name": "Premier League", "country": "England",
                "season": 2026},
     "teams": {"home": {"id": 50, "name": "Man City"},
               "away": {"id": 52, "name": "Crystal Palace"}}},
    # finished match: must be ignored
    {"fixture": {"id": 1003, "date": "2026-10-10T13:00:00+01:00",
                 "status": {"short": "FT"}},
     "league": {"id": 39, "name": "Premier League", "country": "England",
                "season": 2026},
     "teams": {"home": {"id": 42, "name": "Arsenal"},
               "away": {"id": 50, "name": "Man City"}}},
    # league we did not ask for: must be ignored
    {"fixture": {"id": 1004, "date": "2026-10-10T20:00:00+01:00",
                 "status": {"short": "NS"}},
     "league": {"id": 999, "name": "Some Cup", "country": "Nowhere",
                "season": 2026},
     "teams": {"home": {"id": 1, "name": "A"}, "away": {"id": 2, "name": "B"}}},
], "errors": {}, "paging": {"current": 1, "total": 1}}


def _row(tid, name, played, gf, ga):
    return {"team": {"id": tid, "name": name},
            "all": {"played": played, "goals": {"for": gf, "against": ga}}}


STANDINGS = {"response": [{"league": {"id": 39, "standings": [[
    _row(42, "Arsenal", 8, 18, 6),
    _row(45, "Everton", 8, 7, 12),
    _row(50, "Man City", 8, 20, 7),
    _row(52, "Crystal Palace", 8, 9, 11),
    _row(60, "New Team", 1, 1, 1),          # too few games -> unusable
]]}}], "errors": {}, "paging": {"current": 1, "total": 1}}


def _odds_item(fid):
    return {"fixture": {"id": fid}, "bookmakers": [{"id": 8, "name": "Bet365", "bets": [
        {"id": 1, "name": "Match Winner", "values": [
            {"value": "Home", "odd": "1.55"}, {"value": "Draw", "odd": "4.20"},
            {"value": "Away", "odd": "5.50"}]},
        {"id": 5, "name": "Goals Over/Under", "values": [
            {"value": "Over 1.5", "odd": "1.25"}, {"value": "Under 1.5", "odd": "3.90"},
            {"value": "Over 2.5", "odd": "1.80"}, {"value": "Under 2.5", "odd": "2.00"},
            {"value": "Over 3.5", "odd": "3.10"}, {"value": "Under 3.5", "odd": "1.36"}]},
        {"id": 8, "name": "Both Teams Score", "values": [
            {"value": "Yes", "odd": "1.85"}, {"value": "No", "odd": "1.90"}]},
        {"id": 12, "name": "Double Chance", "values": [
            {"value": "Home/Draw", "odd": "1.14"}, {"value": "Home/Away", "odd": "1.22"},
            {"value": "Draw/Away", "odd": "2.40"}]},
        {"id": 99, "name": "Unknown market", "values": [
            {"value": "Whatever", "odd": "2.00"}]},     # must be skipped
    ]}]}


ODDS = {"response": [_odds_item(1001), _odds_item(1002)],
        "errors": {}, "paging": {"current": 1, "total": 1}}

RESULTS = {"response": [
    {"fixture": {"id": 1001, "status": {"short": "FT"}},
     "goals": {"home": 2, "away": 0}},
    {"fixture": {"id": 1002, "status": {"short": "NS"}},
     "goals": {"home": None, "away": None}},      # not played -> pending
], "errors": {}, "paging": {"current": 1, "total": 1}}

CALLS: list[str] = []


EMPTY = {"response": [], "errors": {}, "paging": {"current": 1, "total": 1}}

# Which dates actually have matches in the fake world. Everything else is an
# international break, so the provider must roll forward to find this one.
MATCH_DAYS = {"2026-10-10"}


def handler(request: httpx.Request) -> httpx.Response:
    CALLS.append(str(request.url))
    p = request.url.path
    q = request.url.params
    hdrs = {"x-ratelimit-requests-remaining": "93"}
    if p.endswith("/fixtures") and "ids" in q:
        return httpx.Response(200, json=RESULTS, headers=hdrs)
    if p.endswith("/fixtures"):
        if q.get("date") not in MATCH_DAYS:
            return httpx.Response(200, json=EMPTY, headers=hdrs)
        return httpx.Response(200, json=FIXTURES, headers=hdrs)
    if p.endswith("/standings"):
        return httpx.Response(200, json=STANDINGS, headers=hdrs)
    if p.endswith("/odds"):
        if q.get("date") not in MATCH_DAYS:
            return httpx.Response(200, json=EMPTY, headers=hdrs)
        return httpx.Response(200, json=ODDS, headers=hdrs)
    return httpx.Response(404, json={"response": [], "errors": {}})


def fresh_provider() -> APIFootballProvider:
    prov = APIFootballProvider()
    # wipe caches + usage so each test starts clean
    for f in prov.cache_dir.glob("*.json"):
        f.unlink()
    prov.usage_file.unlink(missing_ok=True)
    prov._c = httpx.AsyncClient(
        base_url="https://v3.football.api-sports.io",
        transport=httpx.MockTransport(handler), timeout=5,
    )
    return prov


def test_season():
    assert season_for(date(2026, 10, 5)) == 2026, "October belongs to the 2026 season"
    assert season_for(date(2026, 3, 5)) == 2025, "March belongs to the 2025 season"
    print("  season detection                             OK")


def test_fixtures_and_odds():
    config.LEAGUE_IDS = [39, 140]
    prov = fresh_provider()
    matches = asyncio.run(prov.fixtures(DAY))

    assert len(matches) == 2, f"expected 2 usable fixtures, got {len(matches)}"
    labels = {m.label for m in matches}
    assert "Arsenal vs Everton" in labels
    assert not any("Some Cup" in m.league for m in matches), "wrong league leaked in"
    assert not any(m.match_id.endswith("1003") for m in matches), "finished match leaked in"

    m = next(m for m in matches if m.label == "Arsenal vs Everton")
    assert m.match_id.startswith("AF|1001|"), m.match_id
    assert m.kickoff == "18:30", m.kickoff
    assert m.country == "England"
    assert abs(m.home.goals_for - 18 / 8) < 1e-6, "form must come from standings"
    assert abs(m.away.goals_against - 12 / 8) < 1e-6

    # odds mapping, including the markets the free CSVs don't have
    for k in ("1", "X", "2", "1X", "X2", "12", "O1.5", "U1.5",
              "O2.5", "U2.5", "O3.5", "U3.5", "BTTS", "NOBTTS"):
        assert k in m.odds, f"missing market {k}"
    assert m.odds["1"] == 1.55 and m.odds["1X"] == 1.14 and m.odds["BTTS"] == 1.85
    assert "Whatever" not in m.odds, "unknown market should be dropped"
    print(f"  fixtures + odds parsing ({len(m.odds)} markets)         OK")
    asyncio.run(prov.close())


def test_lookahead_over_a_break():
    """Nothing today -> must find the next real match day, not give up."""
    config.LEAGUE_IDS = [39, 140]
    prov = fresh_provider()
    # start 3 days early: 07, 08, 09 are empty, 10 has the games
    matches = asyncio.run(prov.fixtures(date(2026, 10, 7), lookahead=6))
    assert len(matches) == 2, f"lookahead failed, got {len(matches)}"
    assert all(m.day == "Sat 10 Oct" for m in matches), [m.day for m in matches]
    assert all("|2026-10-10|" in m.match_id for m in matches)
    # 4 fixture scans (07,08,09,10) + 1 standings + 1 odds = 6
    assert prov.calls_this_run <= 8, prov.calls_this_run
    print(f"  rolls forward over a break ({prov.calls_this_run} requests)   OK")
    asyncio.run(prov.close())


def test_no_fixtures_at_all():
    config.LEAGUE_IDS = [39]
    prov = fresh_provider()
    matches = asyncio.run(prov.fixtures(date(2026, 11, 20), lookahead=6))
    assert matches == [], "should return nothing, not crash"
    print("  empty horizon handled cleanly                OK")
    asyncio.run(prov.close())


PLAN_ERROR = {"response": [], "paging": {"current": 1, "total": 1},
              "errors": {"plan": "Free plans do not have access to this date, "
                                 "try from 2026-10-04 to 2026-10-06."}}


def test_free_plan_date_window():
    """The free plan only serves today +/- 1 day.

    That is a plan limit, not a bad key: the provider must stop scanning and
    return what it has, never blow up the whole daily run.
    """
    def limited(request: httpx.Request) -> httpx.Response:
        q = request.url.params
        if request.url.path.endswith("/fixtures") and "ids" not in q:
            if q.get("date") not in ("2026-10-04", "2026-10-05", "2026-10-06"):
                return httpx.Response(200, json=PLAN_ERROR)
            return httpx.Response(200, json=EMPTY)
        return handler(request)

    config.LEAGUE_IDS = [39]
    prov = fresh_provider()
    prov._c = httpx.AsyncClient(
        base_url="https://v3.football.api-sports.io",
        transport=httpx.MockTransport(limited), timeout=5)
    matches = asyncio.run(prov.fixtures(date(2026, 10, 5), lookahead=6))
    assert matches == [], "should return empty, not raise"
    assert prov.calls_this_run <= 4, (
        f"kept hammering past the plan window ({prov.calls_this_run} requests)")
    print(f"  free-plan date window handled ({prov.calls_this_run} requests)   OK")
    asyncio.run(prov.close())


def test_bad_key_still_raises():
    """A genuinely invalid key must NOT be silently swallowed."""
    def bad(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "response": [], "errors": {"token": "Invalid API key."}})

    prov = fresh_provider()
    prov._c = httpx.AsyncClient(
        base_url="https://v3.football.api-sports.io",
        transport=httpx.MockTransport(bad), timeout=5)
    try:
        asyncio.run(prov.fixtures(date(2026, 10, 10)))
    except RuntimeError as exc:
        assert "rejected the key" in str(exc)
        print("  invalid key still reported loudly         OK")
    else:
        raise AssertionError("a bad key was silently ignored")
    finally:
        asyncio.run(prov.close())


def test_request_budget():
    """The whole run must fit comfortably inside the free 100/day plan."""
    config.LEAGUE_IDS = [39, 140]
    CALLS.clear()
    prov = fresh_provider()
    asyncio.run(prov.fixtures(DAY))
    first = prov.calls_this_run
    assert first <= 5, f"used {first} requests for one league — too many"

    # second run on the same day must be served from cache: zero requests
    prov2 = APIFootballProvider()
    prov2._c = httpx.AsyncClient(
        base_url="https://v3.football.api-sports.io",
        transport=httpx.MockTransport(handler), timeout=5)
    asyncio.run(prov2.fixtures(DAY))
    assert prov2.calls_this_run == 0, "cache did not prevent repeat requests"
    print(f"  request budget ({first} live, 0 cached)             OK")
    asyncio.run(prov.close())
    asyncio.run(prov2.close())


def test_quota_guard():
    prov = fresh_provider()
    prov.usage_file.write_text(json.dumps(
        {"date": date.today().isoformat(), "count": 999}))
    try:
        asyncio.run(prov._get("/fixtures", date="2026-10-10"))
    except QuotaExceeded:
        print("  quota guard stops runaway usage              OK")
    else:
        raise AssertionError("quota guard did not fire")
    finally:
        prov.usage_file.unlink(missing_ok=True)
        asyncio.run(prov.close())


def test_settlement_results():
    prov = fresh_provider()
    scores = asyncio.run(prov.results(["1001", "1002"]))
    assert scores == {"1001": (2, 0)}, scores   # 1002 not played yet
    print("  result fetch for settlement                  OK")
    asyncio.run(prov.close())


def test_end_to_end_tickets():
    """Live-API matches must flow through the same engine as free ones."""
    config.LEAGUE_IDS = [39]
    prov = fresh_provider()
    matches = asyncio.run(prov.fixtures(DAY))
    from engine import build_selections, build_bomb
    sels = build_selections(matches)
    assert sels, "no selections built from API data"
    assert all(s.match_id.startswith("AF|") for s in sels)
    t = build_bomb(sels, target=3.0)
    assert t and t.total_odds >= 3.0
    print("  API data -> engine -> ticket                 OK")
    asyncio.run(prov.close())


if __name__ == "__main__":
    print("Running API-Football tests (no network, no quota used)…")
    test_season()
    test_fixtures_and_odds()
    test_lookahead_over_a_break()
    test_no_fixtures_at_all()
    test_free_plan_date_window()
    test_bad_key_still_raises()
    test_request_budget()
    test_quota_guard()
    test_settlement_results()
    test_end_to_end_tickets()
    # tidy up
    prov_dir = config.DATA_DIR / "cache" / "apifootball"
    for f in prov_dir.glob("*.json"):
        f.unlink()
    (config.DATA_DIR / "api_usage.json").unlink(missing_ok=True)
    print("All API tests passed ✅")
