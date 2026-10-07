"""
Odds Collector and De-Vigging Engine for The Odds API and Sharp Bookmakers.
Fetches NBA markets: h2h (moneyline), spreads, totals, player props and strips bookmaker margins.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

import config

logger = logging.getLogger(__name__)
if not logger.handlers:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")


def devig_market(odds: List[float]) -> List[float]:
    """
    Strips the bookmaker margin (vig / overround) from a list of decimal odds.
    Formula:
        raw_prob_i = 1.0 / odd_i
        sum_raw = sum(raw_prob_j)
        fair_prob_i = raw_prob_i / sum_raw

    Returns:
        List of fair probabilities that strictly sum to 1.0.
    """
    if not odds:
        return []
    for odd in odds:
        if odd <= 1.0:
            raise ValueError(f"Decimal odds must be strictly greater than 1.0, got: {odd}")

    raw_probs = [1.0 / odd for odd in odds]
    total_implied = sum(raw_probs)
    if total_implied <= 0:
        raise ValueError("Invalid total implied probability.")

    return [p / total_implied for p in raw_probs]


class OddsCollector:
    """Connects to The Odds API, parses markets, and applies sharp de-vigging."""

    @property
    def base_url(self) -> str:
        sport = "basketball_nba_preseason" if config.IS_PRESEASON_MODE else "basketball_nba"
        return f"https://api.the-odds-api.com/v4/sports/{sport}"

    def __init__(
        self,
        api_key: Optional[str] = None,
        region: str = config.ODDS_API_REGION,
        cache_dir: Optional[Path] = None,
        ttl_hours: int = config.CACHE_TTL_HOURS,
    ) -> None:
        self.api_key: str = api_key or config.ODDS_API_KEY
        self.region: str = region
        self.cache_dir: Path = cache_dir or config.CACHE_DIR
        self.ttl_hours: int = ttl_hours
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _get_cache_path(self, key: str) -> Path:
        return self.cache_dir / f"odds_{key}.json"

    def _is_cache_valid(self, cache_file: Path) -> bool:
        if not cache_file.exists():
            return False
        try:
            mtime = cache_file.stat().st_mtime
            age_hours = (time.time() - mtime) / 3600.0
            return age_hours < self.ttl_hours
        except Exception:
            return False

    def get_game_odds(
        self,
        markets: str = "h2h,spreads,totals",
        force_refresh: bool = False,
    ) -> List[Dict[str, Any]]:
        """
        Retrieves current game lines (Moneyline, Spreads, Totals) and calculates
        de-vigged fair probabilities.
        """
        cache_key = f"games_{markets.replace(',', '_')}"
        cache_path = self._get_cache_path(cache_key)

        if not force_refresh and self._is_cache_valid(cache_path):
            try:
                with open(cache_path, "r", encoding="utf-8") as f:
                    logger.info("Loaded game odds from local cache (%s)", cache_path.name)
                    return json.load(f)
            except Exception as e:
                logger.warning("Error reading odds cache: %s", e)

        # If API key is missing or dummy, generate synthetic realistic board
        if not self.api_key or self.api_key.strip() in ("", "YOUR_ODDS_API_KEY"):
            logger.info("No ODDS_API_KEY supplied. Utilizing realistic market board fixture.")
            odds_data = self._generate_synthetic_game_odds()
            self._save_cache(cache_key, odds_data)
            return odds_data

        try:
            url = f"{self.base_url}/odds"
            params = {
                "apiKey": self.api_key,
                "regions": self.region,
                "markets": markets,
                "oddsFormat": "decimal",
            }
            res = requests.get(url, params=params, timeout=10)
            res.raise_for_status()
            data = res.json()
            processed_data = self._parse_and_devig_odds_api(data)
            self._save_cache(cache_key, processed_data)
            return processed_data
        except Exception as e:
            logger.warning("Failed to fetch odds from live API: %s. Using synthetic market.", e)
            odds_data = self._generate_synthetic_game_odds()
            self._save_cache(cache_key, odds_data)
            return odds_data

    def _save_cache(self, key: str, data: Any) -> None:
        cache_path = self._get_cache_path(key)
        try:
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning("Could not write odds cache: %s", e)

    def _parse_and_devig_odds_api(self, raw_games: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Extracts markets from live Odds API JSON and computes de-vigged fair probabilities."""
        parsed_games = []
        now_utc = datetime.now(timezone.utc)
        max_future_utc = now_utc + timedelta(hours=36)
        min_past_utc = now_utc - timedelta(hours=4)

        for g in raw_games:
            game_id = g.get("id")
            home_team = g.get("home_team")
            away_team = g.get("away_team")
            commence_time = g.get("commence_time")

            # Slate Filter: strictly keep games occurring within the current 36-hour window
            if commence_time:
                try:
                    c_dt = datetime.fromisoformat(commence_time.replace("Z", "+00:00"))
                    if c_dt < min_past_utc or c_dt > max_future_utc:
                        continue
                except Exception:
                    pass

            bookmakers = g.get("bookmakers", [])
            if not bookmakers:
                continue

            # Prefer sharp books like Pinnacle or consensus average
            selected_book = bookmakers[0]
            for b in bookmakers:
                if b.get("key") in ("pinnacle", "betonlineag", "draftkings"):
                    selected_book = b
                    break

            markets_map: Dict[str, Any] = {}
            for m in selected_book.get("markets", []):
                key = m.get("key")
                outcomes = m.get("outcomes", [])
                if key == "h2h" and len(outcomes) == 2:
                    o1, o2 = outcomes[0], outcomes[1]
                    price1, price2 = float(o1["price"]), float(o2["price"])
                    fair1, fair2 = devig_market([price1, price2])
                    markets_map["h2h"] = {
                        o1["name"]: {"odds": price1, "market_fair_prob": fair1},
                        o2["name"]: {"odds": price2, "market_fair_prob": fair2},
                    }
                elif key == "spreads" and len(outcomes) == 2:
                    o1, o2 = outcomes[0], outcomes[1]
                    price1, price2 = float(o1["price"]), float(o2["price"])
                    fair1, fair2 = devig_market([price1, price2])
                    markets_map["spreads"] = {
                        o1["name"]: {"point": float(o1.get("point", 0.0)), "odds": price1, "market_fair_prob": fair1},
                        o2["name"]: {"point": float(o2.get("point", 0.0)), "odds": price2, "market_fair_prob": fair2},
                    }
                elif key == "totals" and len(outcomes) == 2:
                    o1, o2 = outcomes[0], outcomes[1]
                    price1, price2 = float(o1["price"]), float(o2["price"])
                    fair1, fair2 = devig_market([price1, price2])
                    markets_map["totals"] = {
                        o1["name"]: {"point": float(o1.get("point", 220.0)), "odds": price1, "market_fair_prob": fair1},
                        o2["name"]: {"point": float(o2.get("point", 220.0)), "odds": price2, "market_fair_prob": fair2},
                    }

            parsed_games.append({
                "game_id": game_id,
                "home_team": home_team,
                "away_team": away_team,
                "commence_time": commence_time,
                "markets": markets_map,
            })
        return parsed_games

    def _generate_synthetic_game_odds(self) -> List[Dict[str, Any]]:
        """Deterministic baseline slate for offline runs and testing."""
        games = [
            {
                "game_id": "nba_2026_dal_bos",
                "home_team": "Boston Celtics",
                "away_team": "Dallas Mavericks",
                "commence_time": "2026-10-07T23:30:00Z",
                "markets": {
                    "h2h": {
                        "Boston Celtics": {"odds": 1.55, "market_fair_prob": 0.621},
                        "Dallas Mavericks": {"odds": 2.55, "market_fair_prob": 0.379},
                    },
                    "spreads": {
                        "Boston Celtics": {"point": -5.5, "odds": 1.91, "market_fair_prob": 0.50},
                        "Dallas Mavericks": {"point": 5.5, "odds": 1.91, "market_fair_prob": 0.50},
                    },
                    "totals": {
                        "Over": {"point": 226.5, "odds": 1.90, "market_fair_prob": 0.502},
                        "Under": {"point": 226.5, "odds": 1.92, "market_fair_prob": 0.498},
                    },
                },
            },
            {
                "game_id": "nba_2026_den_okc",
                "home_team": "Oklahoma City Thunder",
                "away_team": "Denver Nuggets",
                "commence_time": "2026-10-08T00:00:00Z",
                "markets": {
                    "h2h": {
                        "Oklahoma City Thunder": {"odds": 1.70, "market_fair_prob": 0.565},
                        "Denver Nuggets": {"odds": 2.20, "market_fair_prob": 0.435},
                    },
                    "spreads": {
                        "Oklahoma City Thunder": {"point": -2.5, "odds": 1.92, "market_fair_prob": 0.498},
                        "Denver Nuggets": {"point": 2.5, "odds": 1.90, "market_fair_prob": 0.502},
                    },
                    "totals": {
                        "Over": {"point": 222.0, "odds": 1.88, "market_fair_prob": 0.508},
                        "Under": {"point": 222.0, "odds": 1.94, "market_fair_prob": 0.492},
                    },
                },
            },
            {
                "game_id": "nba_2026_gsw_lal",
                "home_team": "Los Angeles Lakers",
                "away_team": "Golden State Warriors",
                "commence_time": "2026-10-08T02:30:00Z",
                "markets": {
                    "h2h": {
                        "Los Angeles Lakers": {"odds": 1.85, "market_fair_prob": 0.521},
                        "Golden State Warriors": {"odds": 1.98, "market_fair_prob": 0.479},
                    },
                    "spreads": {
                        "Los Angeles Lakers": {"point": -1.5, "odds": 1.91, "market_fair_prob": 0.50},
                        "Golden State Warriors": {"point": 1.5, "odds": 1.91, "market_fair_prob": 0.50},
                    },
                    "totals": {
                        "Over": {"point": 229.5, "odds": 1.91, "market_fair_prob": 0.50},
                        "Under": {"point": 229.5, "odds": 1.91, "market_fair_prob": 0.50},
                    },
                },
            },
        ]
        # Re-verify and standardize de-vigging
        for g in games:
            for m_key, m_val in g["markets"].items():
                items = list(m_val.items())
                odds_list = [items[0][1]["odds"], items[1][1]["odds"]]
                fair_probs = devig_market(odds_list)
                items[0][1]["market_fair_prob"] = round(fair_probs[0], 4)
                items[1][1]["market_fair_prob"] = round(fair_probs[1], 4)
        return games
