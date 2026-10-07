"""
Centralized Configuration and Institutional Risk Parameters for nba-quant-engine.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

# Base Directory Resolution
BASE_DIR: Path = Path(__file__).resolve().parent
DATA_DIR: Path = BASE_DIR / "data"
CACHE_DIR: Path = DATA_DIR / "cache"
HISTORY_DIR: Path = DATA_DIR / "history"

# Ensure runtime directories exist
CACHE_DIR.mkdir(parents=True, exist_ok=True)
HISTORY_DIR.mkdir(parents=True, exist_ok=True)

# Load environment variables from .env file if present
load_dotenv(BASE_DIR / ".env")

# Official Timezone
TIMEZONE: str = "America/Mexico_City"
TZ_INFO: ZoneInfo = ZoneInfo(TIMEZONE)

# ==========================================
# CORE QUANTITATIVE PARAMETERS (INSTITUTIONAL)
# ==========================================
BANKROLL_CORE: float = float(os.getenv("BANKROLL_CORE", "1000.0"))
KELLY_FRACTION: float = 0.25  # Quarter-Kelly
MAX_KELLY_STAKE: float = 0.0175  # 1.75% hard ceiling per bet
MIN_KELLY_STAKE: float = 0.0100  # 1.00% hard floor per bet (below this, stake is 0.0)
MAX_DAILY_EXPOSURE: float = 0.100  # 10.0% max total daily exposure
MIN_EDGE: float = 0.040  # 4.0% minimum edge over fair market
MIN_EV_PERCENT: float = 5.0  # 5.0% minimum expected value
HOME_COURT_ADVANTAGE: float = 2.8  # Net points advantage for home team
BENTER_WEIGHT_MODEL: float = 0.35  # Weight assigned to fundamental model
BENTER_WEIGHT_MARKET: float = 0.65  # Weight assigned to sharp market devigged probability

# Monte Carlo Simulation parameters
MC_SIMULATIONS: int = 10_000
MC_POINT_DISPERSION: float = 10.5  # Standard deviation for team scores

# ==========================================
# RECREATIONAL PARLAY PARAMETERS (SATELLITE / DRAFTEA)
# ==========================================
WEEKLY_FUN_BUDGET: float = float(os.getenv("WEEKLY_FUN_BUDGET", "150.0"))  # MXN
PARLAY_FIXED_STAKE: float = 20.0  # MXN per ticket
TARGET_PARLAY_MIN_ODDS: float = 8.0  # Decimal odds (+700)
TARGET_PARLAY_MAX_ODDS: float = 25.0  # Decimal odds (+2400)
MIN_PARLAY_LEGS: int = 3
MAX_PARLAY_LEGS: int = 4

# ==========================================
# API & NOTIFICATION SETTINGS
# ==========================================
ODDS_API_KEY: str = os.getenv("ODDS_API_KEY", "")
ODDS_API_REGION: str = os.getenv("ODDS_API_REGION", "us")
TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID", "")

# Cache Settings
CACHE_TTL_HOURS: int = int(os.getenv("CACHE_TTL_HOURS", "12"))
NBA_SEASON_CURRENT: str = os.getenv("NBA_SEASON_CURRENT", "2025-26")


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


@dataclass(frozen=True)
class SatelliteConstraints:
    """Type-safe container for recreational parlay parameters."""
    weekly_budget: float = WEEKLY_FUN_BUDGET
    fixed_stake: float = PARLAY_FIXED_STAKE
    target_min_odds: float = TARGET_PARLAY_MIN_ODDS
    target_max_odds: float = TARGET_PARLAY_MAX_ODDS
    min_legs: int = MIN_PARLAY_LEGS
    max_legs: int = MAX_PARLAY_LEGS
