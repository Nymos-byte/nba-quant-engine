"""
NBA Data Collector with Rate-Limiting, Resilient Retries, and Local Caching.
Ingests team and player statistics from stats.nba.com via nba_api.
"""

from __future__ import annotations

import json
import logging
import random
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd
from nba_api.stats.endpoints import leaguedashteamstats, leaguedashplayerstats, scoreboardv2

import config

logger = logging.getLogger(__name__)
if not logger.handlers:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

# Real browser headers to avoid HTTP 403 / 429 from stats.nba.com
NBA_STATS_HEADERS: Dict[str, str] = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Origin": "https://www.nba.com",
    "Referer": "https://www.nba.com/",
    "x-nba-stats-origin": "stats",
    "x-nba-stats-token": "true",
    "Connection": "keep-alive",
}

# 30 NBA Teams reference for baseline fallback
NBA_TEAMS_REFERENCE = [
    {"team_id": 1610612737, "team_abbreviation": "ATL", "team_name": "Atlanta Hawks", "pace": 101.5, "ortg": 114.2, "drtg": 116.1},
    {"team_id": 1610612738, "team_abbreviation": "BOS", "team_name": "Boston Celtics", "pace": 98.2, "ortg": 121.5, "drtg": 110.2},
    {"team_id": 1610612751, "team_abbreviation": "BKN", "team_name": "Brooklyn Nets", "pace": 97.4, "ortg": 110.8, "drtg": 116.8},
    {"team_id": 1610612766, "team_abbreviation": "CHA", "team_name": "Charlotte Hornets", "pace": 98.5, "ortg": 108.9, "drtg": 117.5},
    {"team_id": 1610612741, "team_abbreviation": "CHI", "team_name": "Chicago Bulls", "pace": 102.1, "ortg": 112.4, "drtg": 115.3},
    {"team_id": 1610612739, "team_abbreviation": "CLE", "team_name": "Cleveland Cavaliers", "pace": 99.8, "ortg": 119.8, "drtg": 111.4},
    {"team_id": 1610612742, "team_abbreviation": "DAL", "team_name": "Dallas Mavericks", "pace": 99.1, "ortg": 117.2, "drtg": 114.0},
    {"team_id": 1610612743, "team_abbreviation": "DEN", "team_name": "Denver Nuggets", "pace": 98.6, "ortg": 118.5, "drtg": 114.3},
    {"team_id": 1610612765, "team_abbreviation": "DET", "team_name": "Detroit Pistons", "pace": 99.7, "ortg": 110.5, "drtg": 115.8},
    {"team_id": 1610612744, "team_abbreviation": "GSW", "team_name": "Golden State Warriors", "pace": 100.8, "ortg": 116.0, "drtg": 112.1},
    {"team_id": 1610612745, "team_abbreviation": "HOU", "team_name": "Houston Rockets", "pace": 99.4, "ortg": 114.8, "drtg": 110.5},
    {"team_id": 1610612754, "team_abbreviation": "IND", "team_name": "Indiana Pacers", "pace": 102.8, "ortg": 117.9, "drtg": 117.0},
    {"team_id": 1610612746, "team_abbreviation": "LAC", "team_name": "LA Clippers", "pace": 97.9, "ortg": 114.2, "drtg": 112.5},
    {"team_id": 1610612747, "team_abbreviation": "LAL", "team_name": "Los Angeles Lakers", "pace": 100.5, "ortg": 115.6, "drtg": 114.9},
    {"team_id": 1610612763, "team_abbreviation": "MEM", "team_name": "Memphis Grizzlies", "pace": 102.5, "ortg": 116.8, "drtg": 111.9},
    {"team_id": 1610612748, "team_abbreviation": "MIA", "team_name": "Miami Heat", "pace": 97.2, "ortg": 112.7, "drtg": 112.3},
    {"team_id": 1610612749, "team_abbreviation": "MIL", "team_name": "Milwaukee Bucks", "pace": 99.3, "ortg": 116.4, "drtg": 113.8},
    {"team_id": 1610612750, "team_abbreviation": "MIN", "team_name": "Minnesota Timberwolves", "pace": 98.1, "ortg": 115.0, "drtg": 109.8},
    {"team_id": 1610612740, "team_abbreviation": "NOP", "team_name": "New Orleans Pelicans", "pace": 98.8, "ortg": 111.8, "drtg": 116.2},
    {"team_id": 1610612752, "team_abbreviation": "NYK", "team_name": "New York Knicks", "pace": 96.5, "ortg": 118.8, "drtg": 113.1},
    {"team_id": 1610612760, "team_abbreviation": "OKC", "team_name": "Oklahoma City Thunder", "pace": 101.2, "ortg": 118.6, "drtg": 107.5},
    {"team_id": 1610612753, "team_abbreviation": "ORL", "team_name": "Orlando Magic", "pace": 97.8, "ortg": 111.2, "drtg": 109.2},
    {"team_id": 1610612755, "team_abbreviation": "PHI", "team_name": "Philadelphia 76ers", "pace": 98.0, "ortg": 112.5, "drtg": 114.7},
    {"team_id": 1610612756, "team_abbreviation": "PHX", "team_name": "Phoenix Suns", "pace": 98.9, "ortg": 115.4, "drtg": 114.5},
    {"team_id": 1610612757, "team_abbreviation": "POR", "team_name": "Portland Trail Blazers", "pace": 99.2, "ortg": 108.5, "drtg": 116.9},
    {"team_id": 1610612758, "team_abbreviation": "SAC", "team_name": "Sacramento Kings", "pace": 99.6, "ortg": 115.8, "drtg": 114.2},
    {"team_id": 1610612759, "team_abbreviation": "SAS", "team_name": "San Antonio Spurs", "pace": 101.0, "ortg": 112.9, "drtg": 115.1},
    {"team_id": 1610612761, "team_abbreviation": "TOR", "team_name": "Toronto Raptors", "pace": 100.2, "ortg": 111.6, "drtg": 117.2},
    {"team_id": 1610612762, "team_abbreviation": "UTA", "team_name": "Utah Jazz", "pace": 100.7, "ortg": 110.1, "drtg": 118.4},
    {"team_id": 1610612764, "team_abbreviation": "WAS", "team_name": "Washington Wizards", "pace": 102.3, "ortg": 108.2, "drtg": 119.5},
]


class NBADataCollector:
    """Ingests, cleans, caches and serves team and player statistics."""

    def __init__(
        self,
        cache_dir: Optional[Path] = None,
        ttl_hours: int = config.CACHE_TTL_HOURS,
        request_delay: float = 1.0,
        max_retries: int = 3,
    ) -> None:
        self.cache_dir: Path = cache_dir or config.CACHE_DIR
        self.ttl_hours: int = ttl_hours
        self.request_delay: float = request_delay
        self.max_retries: int = max_retries
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _get_cache_path(self, key: str) -> Path:
        return self.cache_dir / f"{key}.json"

    def _is_cache_valid(self, cache_file: Path) -> bool:
        if not cache_file.exists():
            return False
        try:
            mtime = cache_file.stat().st_mtime
            age_hours = (time.time() - mtime) / 3600.0
            return age_hours < self.ttl_hours
        except Exception:
            return False

    def _read_cache(self, key: str) -> Optional[Any]:
        cache_path = self._get_cache_path(key)
        if self._is_cache_valid(cache_path):
            try:
                with open(cache_path, "r", encoding="utf-8") as f:
                    logger.info("Loaded '%s' from valid local cache (%s)", key, cache_path.name)
                    return json.load(f)
            except Exception as e:
                logger.warning("Error reading cache for '%s': %s", key, e)
        return None

    def _write_cache(self, key: str, data: Any) -> None:
        cache_path = self._get_cache_path(key)
        try:
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            logger.info("Cached '%s' to %s", key, cache_path.name)
        except Exception as e:
            logger.warning("Could not write cache for '%s': %s", key, e)

    def _retry_api_call(self, endpoint_fn: Any, **kwargs: Any) -> Any:
        """Executes API call with exponential backoff and jitter."""
        for attempt in range(1, self.max_retries + 1):
            try:
                time.sleep(self.request_delay + random.uniform(0.1, 0.4))
                res = endpoint_fn(headers=NBA_STATS_HEADERS, timeout=12, **kwargs)
                return res
            except Exception as e:
                wait_time = (2 ** attempt) + random.uniform(0.2, 0.8)
                logger.warning(
                    "API call failed (attempt %d/%d): %s. Backing off for %.2fs...",
                    attempt, self.max_retries, e, wait_time
                )
                if attempt == self.max_retries:
                    raise
                time.sleep(wait_time)

    def get_team_stats(
        self,
        season: str = config.BASELINE_SEASON,
        force_refresh: bool = False,
    ) -> pd.DataFrame:
        """
        Retrieves Team Pace, ORtg, DRtg and baseline league stats.
        Forces regular season baseline (preseason data prohibited for ratings).
        Columns guaranteed:
        ['team_id', 'team_name', 'team_abbreviation', 'pace', 'ortg', 'drtg', 'pts', 'gp']
        """
        target_season = config.BASELINE_SEASON  # Enforce historical baseline
        cache_key = f"team_stats_{target_season.replace('-', '_')}"
        if not force_refresh:
            cached = self._read_cache(cache_key)
            if cached:
                return pd.DataFrame(cached)

        logger.info(
            "Fetching Team Stats from stats.nba.com for baseline season %s (SeasonType='Regular Season')...",
            target_season,
        )
        try:
            res = self._retry_api_call(
                leaguedashteamstats.LeagueDashTeamStats,
                season=target_season,
                season_type_all_star="Regular Season",
                per_mode_detailed="PerGame",
                measure_type_detailed_defense="Advanced",
            )
            data_frames = res.get_data_frames()
            if not data_frames or data_frames[0].empty:
                raise ValueError("Empty response received from NBA API.")

            raw_df = data_frames[0]
            # Standardize column naming
            df = pd.DataFrame()
            df["team_id"] = raw_df["TEAM_ID"]
            df["team_name"] = raw_df["TEAM_NAME"]
            df["team_abbreviation"] = raw_df.get("TEAM_ABBREVIATION", "")
            df["pace"] = pd.to_numeric(raw_df.get("PACE", 100.0), errors="coerce").fillna(100.0)
            df["ortg"] = pd.to_numeric(raw_df.get("OFF_RATING", 114.0), errors="coerce").fillna(114.0)
            df["drtg"] = pd.to_numeric(raw_df.get("DEF_RATING", 114.0), errors="coerce").fillna(114.0)
            df["pts"] = pd.to_numeric(raw_df.get("PTS", 112.0), errors="coerce").fillna(112.0)
            df["gp"] = pd.to_numeric(raw_df.get("GP", 82), errors="coerce").fillna(82)

            self._write_cache(cache_key, df.to_dict(orient="records"))
            return df
        except Exception as e:
            logger.error("Failed to retrieve live team stats: %s. Falling back to reference baseline.", e)
            fallback_df = pd.DataFrame(NBA_TEAMS_REFERENCE)
            fallback_df["pts"] = fallback_df["ortg"] * (fallback_df["pace"] / 100.0)
            fallback_df["gp"] = 70
            self._write_cache(cache_key, fallback_df.to_dict(orient="records"))
            return fallback_df

    def get_player_stats(
        self,
        season: str = config.BASELINE_SEASON,
        force_refresh: bool = False,
    ) -> pd.DataFrame:
        """
        Retrieves Player stats: minutes, usage, points, rebounds, assists, 3-pointers.
        Forces regular season baseline (preseason stats prohibited).
        Columns guaranteed:
        ['player_id', 'player_name', 'team_id', 'team_abbreviation', 'min', 'usg_pct', 'pts', 'reb', 'ast', 'fg3m']
        """
        target_season = config.BASELINE_SEASON
        cache_key = f"player_stats_{target_season.replace('-', '_')}"
        if not force_refresh:
            cached = self._read_cache(cache_key)
            if cached:
                return pd.DataFrame(cached)

        logger.info(
            "Fetching Player Stats from stats.nba.com for baseline season %s (SeasonType='Regular Season')...",
            target_season,
        )
        try:
            # Usage and advanced stats
            res_adv = self._retry_api_call(
                leaguedashplayerstats.LeagueDashPlayerStats,
                season=target_season,
                season_type_all_star="Regular Season",
                per_mode_detailed="PerGame",
                measure_type_detailed_defense="Advanced",
            )
            df_adv = res_adv.get_data_frames()[0]

            # Traditional stats for points, rebounds, assists, triples
            res_trad = self._retry_api_call(
                leaguedashplayerstats.LeagueDashPlayerStats,
                season=target_season,
                season_type_all_star="Regular Season",
                per_mode_detailed="PerGame",
                measure_type_detailed_defense="Base",
            )
            df_trad = res_trad.get_data_frames()[0]

            merged = pd.merge(
                df_trad[["PLAYER_ID", "PLAYER_NAME", "TEAM_ID", "TEAM_ABBREVIATION", "MIN", "PTS", "REB", "AST", "FG3M"]],
                df_adv[["PLAYER_ID", "USG_PCT"]],
                on="PLAYER_ID",
                how="left",
            )

            df = pd.DataFrame()
            df["player_id"] = merged["PLAYER_ID"]
            df["player_name"] = merged["PLAYER_NAME"]
            df["team_id"] = merged["TEAM_ID"]
            df["team_abbreviation"] = merged["TEAM_ABBREVIATION"]
            df["min"] = pd.to_numeric(merged["MIN"], errors="coerce").fillna(0.0)
            df["usg_pct"] = pd.to_numeric(merged["USG_PCT"], errors="coerce").fillna(0.20)
            df["pts"] = pd.to_numeric(merged["PTS"], errors="coerce").fillna(0.0)
            df["reb"] = pd.to_numeric(merged["REB"], errors="coerce").fillna(0.0)
            df["ast"] = pd.to_numeric(merged["AST"], errors="coerce").fillna(0.0)
            df["fg3m"] = pd.to_numeric(merged["FG3M"], errors="coerce").fillna(0.0)

            self._write_cache(cache_key, df.to_dict(orient="records"))
            return df
        except Exception as e:
            logger.error("Failed to retrieve live player stats: %s. Generating baseline stars fallback.", e)
            fallback_players = self._generate_fallback_players()
            self._write_cache(cache_key, fallback_players.to_dict(orient="records"))
            return fallback_players

    def get_league_averages(self, team_df: pd.DataFrame) -> Dict[str, float]:
        """Calculates baseline league-wide pace, offensive rating and defensive rating."""
        if team_df.empty:
            return {"pace": 99.5, "ortg": 114.5, "drtg": 114.5, "pts": 113.8}
        return {
            "pace": float(team_df["pace"].mean()),
            "ortg": float(team_df["ortg"].mean()),
            "drtg": float(team_df["drtg"].mean()),
            "pts": float(team_df["pts"].mean()),
        }

    def _generate_fallback_players(self) -> pd.DataFrame:
        """Realistic sample player roster with key starters and props benchmarks."""
        sample = [
            {"player_id": 1629029, "player_name": "Luka Doncic", "team_id": 1610612742, "team_abbreviation": "DAL", "min": 36.8, "usg_pct": 0.355, "pts": 32.4, "reb": 8.6, "ast": 9.4, "fg3m": 3.8},
            {"player_id": 202681, "player_name": "Kyrie Irving", "team_id": 1610612742, "team_abbreviation": "DAL", "min": 34.5, "usg_pct": 0.278, "pts": 25.1, "reb": 4.9, "ast": 5.2, "fg3m": 2.9},
            {"player_id": 1628369, "player_name": "Jayson Tatum", "team_id": 1610612738, "team_abbreviation": "BOS", "min": 35.7, "usg_pct": 0.301, "pts": 27.2, "reb": 8.4, "ast": 5.1, "fg3m": 3.2},
            {"player_id": 1627759, "player_name": "Jaylen Brown", "team_id": 1610612738, "team_abbreviation": "BOS", "min": 33.6, "usg_pct": 0.285, "pts": 23.5, "reb": 5.8, "ast": 3.6, "fg3m": 2.4},
            {"player_id": 203999, "player_name": "Nikola Jokic", "team_id": 1610612743, "team_abbreviation": "DEN", "min": 35.1, "usg_pct": 0.292, "pts": 26.8, "reb": 12.3, "ast": 9.8, "fg3m": 1.2},
            {"player_id": 1627750, "player_name": "Jamal Murray", "team_id": 1610612743, "team_abbreviation": "DEN", "min": 32.8, "usg_pct": 0.254, "pts": 21.2, "reb": 4.1, "ast": 6.5, "fg3m": 2.5},
            {"player_id": 1628983, "player_name": "Shai Gilgeous-Alexander", "team_id": 1610612760, "team_abbreviation": "OKC", "min": 34.4, "usg_pct": 0.324, "pts": 30.5, "reb": 5.6, "ast": 6.4, "fg3m": 1.4},
            {"player_id": 1631097, "player_name": "Jalen Williams", "team_id": 1610612760, "team_abbreviation": "OKC", "min": 31.8, "usg_pct": 0.231, "pts": 19.4, "reb": 4.2, "ast": 4.6, "fg3m": 1.5},
            {"player_id": 1630162, "player_name": "Anthony Edwards", "team_id": 1610612750, "team_abbreviation": "MIN", "min": 35.3, "usg_pct": 0.315, "pts": 26.5, "reb": 5.4, "ast": 5.1, "fg3m": 2.8},
            {"player_id": 1629630, "player_name": "Ja Morant", "team_id": 1610612763, "team_abbreviation": "MEM", "min": 32.5, "usg_pct": 0.310, "pts": 24.8, "reb": 5.3, "ast": 8.1, "fg3m": 1.8},
            {"player_id": 1630559, "player_name": "Cade Cunningham", "team_id": 1610612765, "team_abbreviation": "DET", "min": 34.0, "usg_pct": 0.298, "pts": 23.1, "reb": 4.4, "ast": 7.6, "fg3m": 2.0},
            {"player_id": 1630163, "player_name": "LaMelo Ball", "team_id": 1610612766, "team_abbreviation": "CHA", "min": 33.2, "usg_pct": 0.320, "pts": 25.4, "reb": 5.2, "ast": 8.3, "fg3m": 3.5},
        ]
        return pd.DataFrame(sample)
