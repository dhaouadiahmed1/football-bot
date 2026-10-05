"""Offline provider — no API key required.

It builds a realistic daily card: real league/team names, plausible form
numbers, and bookmaker odds generated from a *slightly different* model than
the engine's (plus a normal bookmaker margin). That mismatch is what creates
the value edges, so the whole pipeline behaves exactly like it will on live
data. Seeded by date => the same day always produces the same card.

Swap to real data by setting PROVIDER=apifootball in .env.
"""
from __future__ import annotations

import random
from datetime import date
from typing import List, Optional

from engine.poisson import lambdas, market_probabilities, score_matrix
from providers.base import BaseProvider, Match, TeamForm

LEAGUES = {
    ("England", "Premier League", 2.85): [
        "Arsenal", "Manchester City", "Liverpool", "Chelsea", "Tottenham",
        "Aston Villa", "Newcastle", "Manchester United", "Brighton", "West Ham",
        "Crystal Palace", "Fulham", "Brentford", "Everton", "Nottingham Forest",
        "Bournemouth", "Wolves", "Leicester", "Ipswich", "Southampton",
    ],
    ("Spain", "La Liga", 2.60): [
        "Real Madrid", "Barcelona", "Atletico Madrid", "Athletic Bilbao",
        "Real Sociedad", "Villarreal", "Real Betis", "Sevilla", "Valencia",
        "Girona", "Osasuna", "Celta Vigo", "Mallorca", "Rayo Vallecano",
        "Getafe", "Las Palmas", "Alaves", "Espanyol", "Leganes", "Valladolid",
    ],
    ("Italy", "Serie A", 2.70): [
        "Inter", "Juventus", "AC Milan", "Napoli", "Atalanta", "Roma", "Lazio",
        "Fiorentina", "Bologna", "Torino", "Udinese", "Genoa", "Empoli",
        "Verona", "Cagliari", "Parma", "Lecce", "Como", "Monza", "Venezia",
    ],
    ("Germany", "Bundesliga", 3.10): [
        "Bayern Munich", "Bayer Leverkusen", "Borussia Dortmund", "RB Leipzig",
        "Stuttgart", "Eintracht Frankfurt", "Freiburg", "Hoffenheim",
        "Werder Bremen", "Wolfsburg", "Mainz", "Augsburg", "Union Berlin",
        "Borussia M.Gladbach", "Heidenheim", "St. Pauli", "Bochum", "Holstein Kiel",
    ],
    ("France", "Ligue 1", 2.75): [
        "Paris Saint-Germain", "Monaco", "Marseille", "Lille", "Lyon", "Nice",
        "Lens", "Rennes", "Toulouse", "Reims", "Strasbourg", "Brest",
        "Nantes", "Auxerre", "Angers", "Le Havre", "Saint-Etienne", "Montpellier",
    ],
    ("Portugal", "Primeira Liga", 2.65): [
        "Benfica", "Porto", "Sporting CP", "Braga", "Vitoria Guimaraes",
        "Famalicao", "Moreirense", "Santa Clara", "Gil Vicente", "Estoril",
        "Arouca", "Casa Pia", "Boavista", "Farense",
    ],
    ("Netherlands", "Eredivisie", 3.20): [
        "PSV", "Feyenoord", "Ajax", "AZ Alkmaar", "Twente", "Utrecht",
        "Go Ahead Eagles", "Sparta Rotterdam", "NEC", "Heerenveen",
        "Fortuna Sittard", "Groningen", "PEC Zwolle", "Almere City",
    ],
    ("Turkey", "Super Lig", 2.95): [
        "Galatasaray", "Fenerbahce", "Besiktas", "Trabzonspor", "Basaksehir",
        "Samsunspor", "Rizespor", "Antalyaspor", "Konyaspor", "Kasimpasa",
        "Alanyaspor", "Sivasspor", "Gaziantep", "Kayserispor",
    ],
}

# Rough tier strength: index in the list -> quality multiplier
def _tier(idx: int, size: int) -> float:
    return 1.38 - 0.76 * (idx / max(size - 1, 1))      # 1.38 (best) .. 0.62 (worst)


class MockProvider(BaseProvider):
    name = "mock (demo data)"

    def __init__(self, matches_per_day: int = 24):
        self.matches_per_day = matches_per_day

    async def fixtures(self, day: Optional[date] = None) -> List[Match]:
        day = day or date.today()
        rng = random.Random(day.toordinal() * 7919)
        out: List[Match] = []

        league_keys = list(LEAGUES.keys())
        rng.shuffle(league_keys)
        per_league = max(2, self.matches_per_day // len(league_keys) + 1)

        for (country, league, avg) in league_keys:
            teams = LEAGUES[(country, league, avg)][:]
            order = list(range(len(teams)))
            rng.shuffle(order)
            for k in range(per_league):
                if len(order) < 2 or len(out) >= self.matches_per_day:
                    break
                hi, ai = order.pop(), order.pop()
                home = self._form(teams[hi], _tier(hi, len(teams)), avg, rng, True)
                away = self._form(teams[ai], _tier(ai, len(teams)), avg, rng, False)
                kickoff = rng.choice(
                    ["13:00", "15:00", "16:00", "17:30", "18:00", "19:45",
                     "20:00", "20:45", "21:00", "22:00"]
                )
                m = Match(
                    match_id=f"{day.isoformat()}-{country[:3].upper()}-{hi}-{ai}",
                    league=league, country=country, kickoff=kickoff,
                    home=home, away=away, league_avg_goals=avg,
                )
                m.odds = self._price(m, rng)
                out.append(m)

        out.sort(key=lambda m: m.kickoff)
        return out

    # -- helpers -------------------------------------------------------------
    @staticmethod
    def _form(name: str, tier: float, avg: float, rng: random.Random, home: bool) -> TeamForm:
        base = avg / 2
        gf = base * tier * rng.uniform(0.82, 1.20)
        ga = base / tier * rng.uniform(0.82, 1.20)
        return TeamForm(
            name=name, goals_for=round(gf, 2), goals_against=round(ga, 2),
            matches=rng.randint(8, 12),
            home_advantage=round(rng.uniform(1.06, 1.20), 3) if home else 1.0,
        )

    @staticmethod
    def _price(m: Match, rng: random.Random) -> dict:
        """Bookmaker odds = slightly different model + margin."""
        jitter = lambda v: v * rng.uniform(0.90, 1.10)          # noqa: E731
        lh, la = lambdas(
            jitter(m.home.goals_for), jitter(m.home.goals_against),
            jitter(m.away.goals_for), jitter(m.away.goals_against),
            league_avg=m.league_avg_goals, home_adv=m.home.home_advantage,
        )
        p = market_probabilities(score_matrix(lh, la))

        # (markets, how much the group's true probabilities sum to, margin)
        # double chance sums to 2.0 because every outcome appears twice.
        groups = [
            (["1", "X", "2"], 1.0, rng.uniform(1.045, 1.075)),
            (["1X", "X2", "12"], 2.0, rng.uniform(1.030, 1.055)),
            (["O1.5", "U1.5"], 1.0, rng.uniform(1.035, 1.060)),
            (["O2.5", "U2.5"], 1.0, rng.uniform(1.035, 1.060)),
            (["O3.5", "U3.5"], 1.0, rng.uniform(1.040, 1.065)),
            (["BTTS", "NOBTTS"], 1.0, rng.uniform(1.040, 1.070)),
        ]
        odds = {}
        for keys, norm, margin in groups:
            s = (sum(p[k] for k in keys) or norm) / norm
            for k in keys:
                q = min((p[k] / s) * margin, 0.995)
                odds[k] = max(1.01, round(1 / q, 2)) if q > 0 else 50.0
        return odds
