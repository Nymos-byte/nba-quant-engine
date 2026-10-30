"""
NBA Data Collector.
Serves team and player statistics of the current season from ESPN box scores,
using the last season's reference table as a fading baseline.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

import config
from src.data.espn_collector import ESPNDataCollector
from src.data.season_stats import SeasonStatsStore, build_player_stats, build_team_ratings, injury_scoring_loss

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

# 30 NBA Teams reference: prior for the ratings, named as The Odds API names them
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
    {"team_id": 1610612746, "team_abbreviation": "LAC", "team_name": "Los Angeles Clippers", "pace": 97.9, "ortg": 114.2, "drtg": 112.5},
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
    """Serves current-season team ratings and player averages, adjusted for injuries."""

    def __init__(
        self,
        cache_dir: Optional[Path] = None,
        espn: Optional[ESPNDataCollector] = None,
    ) -> None:
        self.cache_dir: Path = cache_dir or config.CACHE_DIR
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.espn: ESPNDataCollector = espn or ESPNDataCollector(cache_dir=self.cache_dir)
        self.store = SeasonStatsStore(espn=self.espn, store_file=self.cache_dir / config.SEASON_STORE_FILE)
        self._games: Optional[List[Dict[str, Any]]] = None
        self._injuries: Optional[Dict[str, str]] = None

    def _season_games(self) -> List[Dict[str, Any]]:
        if self._games is None:
            self._games = self.store.update(datetime.now(config.TZ_INFO).date())
            logger.info("Current season sample: %d finished games.", len(self._games))
        return self._games

    def _get_injuries(self) -> Dict[str, str]:
        if self._injuries is None:
            self._injuries = self.espn.get_injuries()
        return self._injuries

    def get_team_stats(self, force_refresh: bool = False) -> pd.DataFrame:
        """
        Team pace, ORtg and DRtg of the current season blended with last season's baseline,
        with offense reduced for players ruled Out.
        Columns guaranteed:
        ['team_id', 'team_name', 'team_abbreviation', 'pace', 'ortg', 'drtg', 'pts', 'gp', 'season_gp', 'injury_loss']
        """
        games = self._season_games()
        ratings = build_team_ratings(games, pd.DataFrame(NBA_TEAMS_REFERENCE))
        losses = injury_scoring_loss(build_player_stats(games), self._get_injuries())
        ratings["injury_loss"] = ratings["team_abbreviation"].map(losses).fillna(0.0)
        ratings["ortg"] = (ratings["ortg"] * (1.0 - ratings["injury_loss"])).round(2)
        for team, loss in losses.items():
            logger.info("Injury adjustment for %s: ORtg -%.1f%%", team, loss * 100.0)
        return ratings

    def get_player_stats(self, force_refresh: bool = False) -> pd.DataFrame:
        """
        Current-season per-game averages, leaving out anyone on the injury report.
        Empty when the season has too few games: never falls back to old rosters.
        """
        players = build_player_stats(self._season_games())
        if players.empty:
            return players
        injured = self._get_injuries()
        return players[~players["player_name"].str.lower().isin(injured)].reset_index(drop=True)

    def get_back_to_back_teams(self, target_date: str) -> set[str]:
        """Teams that played the day before target_date."""
        self._season_games()
        day = datetime.strptime(target_date, "%Y-%m-%d").date()
        return self.store.teams_played_on(day - timedelta(days=1))

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
