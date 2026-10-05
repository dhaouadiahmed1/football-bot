"""Central configuration. Everything is overridable with environment variables."""
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

# ---------------------------------------------------------------- Telegram --
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
# Comma separated chat ids that receive the daily post automatically.
DEFAULT_CHAT_IDS = [c.strip() for c in os.getenv("CHAT_IDS", "").split(",") if c.strip()]
# Hour (0-23) in TIMEZONE at which the daily tickets are pushed.
DAILY_HOUR = int(os.getenv("DAILY_HOUR", "9"))
DAILY_MINUTE = int(os.getenv("DAILY_MINUTE", "0"))
TIMEZONE = os.getenv("TIMEZONE", "Africa/Tunis")

# ---------------------------------------------------------------- Provider --
# "hybrid"      -> free CSVs, live API only when the card is thin (default)
# "free"        -> football-data.co.uk, zero API keys, zero limits
# "mock"        -> offline generator, for tests
# "apifootball" -> API-Football via RapidAPI  (needs APIFOOTBALL_KEY)
PROVIDER = os.getenv("PROVIDER", "hybrid").lower()
APIFOOTBALL_KEY = os.getenv("APIFOOTBALL_KEY", "")
APIFOOTBALL_HOST = os.getenv("APIFOOTBALL_HOST", "v3.football.api-sports.io")
# Leagues to scan (API-Football ids). Default: big 5 + Portugal, Netherlands, Turkey.
# Domestic leagues with reliable standings + odds, plus the European cups.
# 39 EPL · 140 LaLiga · 135 SerieA · 78 Bundesliga · 61 Ligue1 · 94 Primeira
# 88 Eredivisie · 203 SuperLig · 144 Belgium · 179 Scotland · 197 Greece
# 207 Switzerland · 218 Austria · 119 Denmark · 106 Poland · 345 Czechia
# 40 Championship · 141 Segunda · 136 SerieB · 79 Bundesliga2 · 62 Ligue2
# 2 Champions League · 3 Europa League · 848 Conference League
# 202 Tunisia Ligue 1
LEAGUE_IDS = [int(x) for x in os.getenv(
    "LEAGUE_IDS",
    "39,140,135,78,61,94,88,203,144,179,197,207,218,119,106,345,"
    "40,141,136,79,62,2,3,848,202"
).split(",") if x.strip()]

# Cap how many leagues we pull standings+odds for in one run (2 requests each),
# so a busy day can never run away with the 100/day free quota.
APIFOOTBALL_MAX_LEAGUES = int(os.getenv("APIFOOTBALL_MAX_LEAGUES", "10"))
BOOKMAKER_ID = int(os.getenv("BOOKMAKER_ID", "8"))  # 8 = Bet365 on API-Football

# Free plan is 100 requests/day. We stop well short of it on purpose.
APIFOOTBALL_DAILY_BUDGET = int(os.getenv("APIFOOTBALL_DAILY_BUDGET", "80"))
# How many days ahead to scan when today has no fixtures (1 request per day).
# Free plan only serves today +/- 1 day, so 1 is the useful maximum there.
# Paid plans can raise this to scan further ahead.
APIFOOTBALL_LOOKAHEAD = int(os.getenv("APIFOOTBALL_LOOKAHEAD", "1"))
APIFOOTBALL_FIXTURES_TTL = int(os.getenv("APIFOOTBALL_FIXTURES_TTL", str(3 * 3600)))
APIFOOTBALL_ODDS_TTL = int(os.getenv("APIFOOTBALL_ODDS_TTL", str(2 * 3600)))
APIFOOTBALL_STANDINGS_TTL = int(os.getenv("APIFOOTBALL_STANDINGS_TTL", str(12 * 3600)))

# ------------------------------------------------------------------ Engine --
MAX_GOALS = 8                 # Poisson score-matrix truncation
MIN_CARD_SIZE = int(os.getenv("MIN_CARD_SIZE", "14"))  # grow the card across days until this many fixtures
MIN_LEG_ODDS = float(os.getenv("MIN_LEG_ODDS", "1.03"))   # the safest legs are priced ~1.05
MAX_LEG_ODDS = float(os.getenv("MAX_LEG_ODDS", "2.30"))   # mid-range: see README backtest

# Safe ticket
SAFE_MIN_PROB = float(os.getenv("SAFE_MIN_PROB", "0.68"))
SAFE_MIN_LEGS = int(os.getenv("SAFE_MIN_LEGS", "3"))
SAFE_MAX_LEGS = int(os.getenv("SAFE_MAX_LEGS", "5"))
SAFE_MIN_TOTAL_ODDS = float(os.getenv("SAFE_MIN_TOTAL_ODDS", "1.45"))
# Markets allowed in the safe ticket. Double chance feels safe but carries the
# vig of two outcomes, which the backtest shows costs ~13% ROI. Empty = all.
SAFE_MARKETS = [m for m in os.getenv("SAFE_MARKETS", "1,2,O2.5,U2.5").split(",") if m]

# Bomb / combo ticket
TARGET_ODDS = float(os.getenv("TARGET_ODDS", "30.0"))
BOMB_MIN_PROB = float(os.getenv("BOMB_MIN_PROB", "0.22"))
BOMB_MAX_LEGS = int(os.getenv("BOMB_MAX_LEGS", "12"))

# Only keep selections where our model disagrees with the book in our favour.
MIN_EDGE = float(os.getenv("MIN_EDGE", "-1.0"))   # -1 = no filter (de-vigged edges are mostly negative)

# How much to trust our own model vs the de-vigged bookmaker price.
# 0.0 = pure market, 1.0 = pure model. The book is sharp; stay humble.
MODEL_WEIGHT = float(os.getenv("MODEL_WEIGHT", "0.10"))

# "power" corrects the favourite-long-shot bias, "proportional" is the naive way
DEVIG_METHOD = os.getenv("DEVIG_METHOD", "power")

# Whose price do we bet at?  b365 | max (best available = line shopping) | avg
PRICE_SOURCE = os.getenv("PRICE_SOURCE", "max")

# Double chance is built from a single book (you cannot dutch inside an acca).
# This haircut keeps that synthetic price realistic.
DC_HAIRCUT = float(os.getenv("DC_HAIRCUT", "0.015"))

# ------------------------------------------------------------------- Files --
SUBS_FILE = DATA_DIR / "subscribers.json"
HISTORY_FILE = DATA_DIR / "history.jsonl"

# ------------------------------------------------------------------- Misc ----
PORT = int(os.getenv("PORT", "8080"))  # tiny health server for free web hosts
