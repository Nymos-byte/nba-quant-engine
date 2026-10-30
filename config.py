"""
Centralized Configuration and Institutional Risk Parameters for nba-quant-engine.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from datetime import date, datetime
from typing import Optional
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

# Base Directory Resolution
BASE_DIR: Path = Path(__file__).resolve().parent
DATA_DIR: Path = BASE_DIR / "data"
CACHE_DIR: Path = DATA_DIR / "cache"
HISTORY_DIR: Path = DATA_DIR / "history"
BANKROLL_STATE_FILE: Path = DATA_DIR / "bankroll_state.json"

# Ensure runtime directories exist
CACHE_DIR.mkdir(parents=True, exist_ok=True)
HISTORY_DIR.mkdir(parents=True, exist_ok=True)

# Load environment variables from .env file if present
load_dotenv(BASE_DIR / ".env")

# Official Timezone
TIMEZONE: str = "America/Mexico_City"
TZ_INFO: ZoneInfo = ZoneInfo(TIMEZONE)

# ==========================================
# CONTROL DE TEMPORADA Y PRETEMPORADA
# ==========================================
REGULAR_SEASON_START_DATE: date = date(2026, 10, 20)
IS_PRESEASON_MODE: bool = False  # Temporada regular activa (pretemporada finalizada)

# Temporada base para extracción histórica obligatoria
BASELINE_SEASON: str = "2025-26"

# ==========================================
# CORE QUANTITATIVE PARAMETERS (INSTITUTIONAL)
# ==========================================
BANKROLL_CORE: float = float(os.getenv("BANKROLL_CORE", "1000.0"))  # Pesos Mexicanos (MXN)


def get_current_bankroll(state_file: Optional[Path] = None) -> float:
    """
    Retrieves current active bankroll from JSON state file.
    If the file does not exist, initializes it automatically with BANKROLL_CORE.
    """
    file_path = state_file or BANKROLL_STATE_FILE
    if file_path.exists():
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                val = float(data.get("current_bankroll", BANKROLL_CORE))
                if val > 0:
                    return val
        except Exception:
            pass

    # Initialize file if missing or invalid
    init_val = BANKROLL_CORE
    set_current_bankroll(init_val, state_file=file_path)
    return init_val


def set_current_bankroll(amount: float, state_file: Optional[Path] = None) -> float:
    """
    Updates the bankroll state file with a validated positive amount and timestamp in CDMX timezone.
    """
    if amount <= 0:
        raise ValueError(f"El bankroll debe ser un número positivo (> 0), recibido: {amount}")

    file_path = state_file or BANKROLL_STATE_FILE
    file_path.parent.mkdir(parents=True, exist_ok=True)
    now_str = datetime.now(TZ_INFO).strftime("%Y-%m-%d %H:%M:%S")
    state = {
        "current_bankroll": round(float(amount), 2),
        "last_updated": now_str,
    }
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)

    return state["current_bankroll"]

KELLY_FRACTION: float = 0.25  # Quarter-Kelly
MAX_KELLY_STAKE: float = 0.0175  # 1.75% hard ceiling per bet
MIN_KELLY_STAKE: float = 0.0050  # 0.50% hard floor per bet (below this, stake is 0.0)
MAX_DAILY_EXPOSURE: float = 0.100  # 10.0% max total daily exposure
MIN_BET_AMOUNT_MXN: float = 5.0  # Piso mínimo por apuesta en Draftea / casas MXN ($5.00 MXN)
MAX_DAILY_CORE_BETS: int = 8  # Máximo de posiciones Core de alta convicción por día (evita dilución)
# Con peso de modelo 35% y tope de 10 puntos, una cuota 1.91 llega como máximo a edge 1.1%, EV 2.2%
# y Quarter-Kelly 0.6%: umbrales mayores dejarían el filtro sin ninguna apuesta posible.
MIN_EDGE: float = 0.0075  # 0.75% minimum edge over implied odds
MIN_EV_PERCENT: float = 1.75  # 1.75% minimum expected value
MAX_MARKET_DISCREPANCY: float = 0.10  # Máxima distancia modelo-mercado: más allá, el modelo suele estar equivocado
HOME_COURT_ADVANTAGE: float = 2.8  # Net points advantage for home team
BACK_TO_BACK_ORTG_PENALTY: float = 0.013  # Equipo que jugó ayer: ~1.5 puntos menos (supuesto sin calibrar)
BENTER_WEIGHT_MODEL: float = 0.35  # Weight assigned to fundamental model
BENTER_WEIGHT_MARKET: float = 0.65  # Weight assigned to sharp market devigged probability

# Ratings de la temporada en curso (ESPN) mezclados con la tabla de referencia
SEASON_STORE_FILE: str = "season_boxscores_2026_27.json"
RATING_PRIOR_GAMES: float = 15.0  # Partidos de esta temporada que pesan igual que la referencia
MIN_PLAYER_GAMES: int = 3  # Mínimo de partidos para usar a un jugador en patas de parlay
INJURY_REPLACEMENT_RATE: float = 0.5  # Parte de la producción de un baja que recupera su equipo
MAX_INJURY_ORTG_PENALTY: float = 0.08  # Máxima reducción de ORtg por bajas (8%)

# Monte Carlo Simulation parameters
MC_SIMULATIONS: int = 10_000
# Dan desviación de 19 en el total (observada 2026-27) y 13.5 en el margen ya descontada la fuerza de los equipos
MC_POINT_DISPERSION: float = 11.65  # Standard deviation for team scores
MC_SCORE_CORRELATION: float = 0.33  # Correlación entre marcadores: el ritmo mueve a ambos equipos a la vez

# ==========================================
# PLAYER PROPS CON LÍNEAS REALES (CORE)
# ==========================================
ENABLE_PLAYER_PROPS: bool = os.getenv("ENABLE_PLAYER_PROPS", "true").lower() == "true"
PROP_MARKETS: dict[str, str] = {"player_points": "pts", "player_rebounds": "reb", "player_assists": "ast"}
# Plan de 500 créditos al mes. Marcador: 1 crédito por mercado para todos los partidos.
# Props: 1 crédito por mercado POR PARTIDO (la API no las da en bloque).
MAX_PROP_EVENTS_PER_DAY: int = int(os.getenv("MAX_PROP_EVENTS_PER_DAY", "3"))
ODDS_API_DAILY_BASE_CREDITS: int = 7  # Corrida diaria (3) y dos capturas de cierre (hasta 2 cada una)
ODDS_API_CREDIT_RESERVE: int = int(os.getenv("ODDS_API_CREDIT_RESERVE", "30"))  # Las props nunca consumen esta reserva
MIN_PROP_GAMES: int = 8  # Partidos mínimos del jugador en la temporada
MIN_PROP_MINUTES: float = 24.0  # Minutos mínimos por partido
PROP_LINE_PRIOR_GAMES: float = 10.0  # La línea del mercado pesa como este número de partidos
MAX_PROP_DISCREPANCY: float = 0.15  # Tope modelo-mercado en props (mercado menos eficiente, margen mayor)
MAX_DAILY_PROP_BETS: int = 3

# ==========================================
# RECREATIONAL PARLAY PARAMETERS (SATELLITE / DRAFTEA)
# ==========================================
WEEKLY_FUN_BUDGET: float = float(os.getenv("WEEKLY_FUN_BUDGET", "150.0"))  # MXN
PARLAY_FIXED_STAKE: float = 20.0  # MXN per ticket
TARGET_PARLAY_MIN_ODDS: float = 8.0  # Decimal odds (+700)
TARGET_PARLAY_MAX_ODDS: float = float("inf")  # Sin techo superior (apuesta soñadora sin límite)
MIN_PARLAY_LEGS: int = 3
MAX_PARLAY_LEGS: int = 4
PARLAY_MIN_LEG_PROB: float = 0.75  # 75% o superior para líneas alternativas de piso

# ==========================================
# API & NOTIFICATION SETTINGS
# ==========================================
ODDS_API_KEY: str = os.getenv("ODDS_API_KEY", "")
ODDS_API_REGION: str = os.getenv("ODDS_API_REGION", "us")
# Draftea no está en la API. Precio de referencia: casas recreativas, más parecidas a ella que las de margen bajo.
# El consenso de todas da la probabilidad justa; Telegram indica la cuota mínima que debe pagar Draftea.
PRICE_BOOKMAKERS: tuple[str, ...] = tuple(
    b.strip() for b in os.getenv("PRICE_BOOKMAKERS", "draftkings,fanduel,betmgm").split(",") if b.strip()
)
TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID", "")

# Cache Settings
CACHE_TTL_HOURS: int = int(os.getenv("CACHE_TTL_HOURS", "12"))
NBA_SEASON_CURRENT: str = BASELINE_SEASON


@dataclass(frozen=True)
class RiskConstraints:
    """Type-safe container for institutional risk invariants."""
    bankroll_core: float = BANKROLL_CORE
    kelly_fraction: float = KELLY_FRACTION
    max_kelly_stake: float = MAX_KELLY_STAKE
    min_kelly_stake: float = MIN_KELLY_STAKE
    max_daily_exposure: float = MAX_DAILY_EXPOSURE
    min_edge: float = MIN_EDGE
    min_ev_percent: float = MIN_EV_PERCENT
    home_court_advantage: float = HOME_COURT_ADVANTAGE
    benter_weight_model: float = BENTER_WEIGHT_MODEL
    benter_weight_market: float = BENTER_WEIGHT_MARKET
    min_bet_amount_mxn: float = MIN_BET_AMOUNT_MXN
    max_daily_core_bets: int = MAX_DAILY_CORE_BETS
    max_market_discrepancy: float = MAX_MARKET_DISCREPANCY
    max_prop_discrepancy: float = MAX_PROP_DISCREPANCY


@dataclass(frozen=True)
class SatelliteConstraints:
    """Type-safe container for recreational parlay parameters."""
    weekly_budget: float = WEEKLY_FUN_BUDGET
    fixed_stake: float = PARLAY_FIXED_STAKE
    target_min_odds: float = TARGET_PARLAY_MIN_ODDS
    target_max_odds: float = TARGET_PARLAY_MAX_ODDS
    min_legs: int = MIN_PARLAY_LEGS
    max_legs: int = MAX_PARLAY_LEGS
