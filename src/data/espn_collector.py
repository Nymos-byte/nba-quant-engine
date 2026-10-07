"""
ESPN NBA Data Collector for Scoreboards, Advanced Box Scores, and Player Statistics.
Consumes public unauthenticated endpoints from site.api.espn.com without rate-limits
or Akamai CDN blocking.
"""

from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

import config

logger = logging.getLogger(__name__)


class ESPNDataCollector:
    """Connects to ESPN public NBA API, extracts daily slates, team boxscores, and player stats."""

    BASE_URL: str = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba"
    DEFAULT_HEADERS: Dict[str, str] = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json",
    }

    def __init__(
        self,
        cache_dir: Optional[Path] = None,
        timeout: int = 8,
    ) -> None:
        self.cache_dir: Path = cache_dir or config.CACHE_DIR
        self.timeout: int = timeout
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def get_daily_scoreboard(self, date_str: str) -> List[Dict[str, Any]]:
        """
        Retrieves the slate of games for a given date in YYYY-MM-DD or YYYYMMDD format.
        Returns a list of structured game objects with status, scores, and event IDs.
        """
        date_compact = date_str.replace("-", "")
        url = f"{self.BASE_URL}/scoreboard?dates={date_compact}"
        logger.info("Fetching ESPN NBA scoreboard for %s...", date_str)

        try:
            resp = requests.get(url, headers=self.DEFAULT_HEADERS, timeout=self.timeout)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            logger.warning("Failed to fetch ESPN scoreboard for %s: %s", date_str, e)
            return []

        games: List[Dict[str, Any]] = []
        for event in data.get("events", []):
            event_id = str(event.get("id", ""))
            name = str(event.get("name", ""))
            short_name = str(event.get("shortName", ""))
            event_date = str(event.get("date", ""))

            status_obj = event.get("status", {})
            type_obj = status_obj.get("type", {})
            is_completed = bool(type_obj.get("completed", False))
            status_name = str(type_obj.get("name", ""))
            status_detail = str(type_obj.get("detail", ""))

            competitions = event.get("competitions", [])
            if not competitions:
                continue

            comp = competitions[0]
            competitors = comp.get("competitors", [])
            if len(competitors) < 2:
                continue

            home_team_info: Dict[str, Any] = {}
            away_team_info: Dict[str, Any] = {}

            for c in competitors:
                team_data = c.get("team", {})
                score_val = float(c.get("score", 0.0))
                team_dict = {
                    "id": str(team_data.get("id", "")),
                    "name": str(team_data.get("displayName", "")),
                    "abbrev": str(team_data.get("abbreviation", "")),
                    "score": score_val,
                }
                if c.get("homeAway") == "home":
                    home_team_info = team_dict
                else:
                    away_team_info = team_dict

            games.append({
                "event_id": event_id,
                "name": name,
                "short_name": short_name,
                "date": event_date,
                "is_completed": is_completed,
                "status_name": status_name,
                "status_detail": status_detail,
                "home_team": home_team_info,
                "away_team": away_team_info,
            })

        logger.info("Retrieved %d games from ESPN scoreboard for %s", len(games), date_str)
        return games

    def get_game_boxscore(
        self,
        event_id: str,
        force_refresh: bool = False,
    ) -> Dict[str, Any]:
        """
        Retrieves detailed team raw box scores and player stats from ESPN event summary.
        Caches summary locally to prevent redundant network calls.
        """
        cache_file = self.cache_dir / f"espn_summary_{event_id}.json"
        if not force_refresh and cache_file.exists():
            try:
                with open(cache_file, "r", encoding="utf-8") as f:
                    cached_data = json.load(f)
                    return self._parse_boxscore_summary(cached_data)
            except Exception as e:
                logger.debug("Error reading cache for %s: %s", cache_file.name, e)

        url = f"{self.BASE_URL}/summary?event={event_id}"
        try:
            resp = requests.get(url, headers=self.DEFAULT_HEADERS, timeout=self.timeout)
            resp.raise_for_status()
            data = resp.json()
            # Cache the raw JSON
            with open(cache_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
            return self._parse_boxscore_summary(data)
        except Exception as e:
            logger.warning("Failed to fetch ESPN summary for event %s: %s", event_id, e)
            return {"teams": {}, "players": {}}

    def _parse_boxscore_summary(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Extracts normalized team stats and athlete stats from an ESPN summary payload."""
        box = data.get("boxscore", {})
        team_stats_out: Dict[str, Dict[str, float]] = {}
        player_stats_out: Dict[str, Dict[str, Any]] = {}

        # 1. Team level statistics
        for team_entry in box.get("teams", []):
            team_meta = team_entry.get("team", {})
            abbr = str(team_meta.get("abbreviation", "")).upper()
            display_name = str(team_meta.get("displayName", ""))

            raw_stats: Dict[str, float] = {
                "pts": 0.0,
                "fgm": 0.0,
                "fga": 0.0,
                "fg3m": 0.0,
                "fg3a": 0.0,
                "ftm": 0.0,
                "fta": 0.0,
                "oreb": 0.0,
                "dreb": 0.0,
                "reb": 0.0,
                "ast": 0.0,
                "stl": 0.0,
                "blk": 0.0,
                "to": 0.0,
                "minutes": 240.0,
            }

            for stat in team_entry.get("statistics", []):
                name = stat.get("name", "").lower()
                val_str = str(stat.get("displayValue", "0"))

                # Handle combined stats like "42-88" (FGM-FGA)
                if "-" in val_str and ("fieldgoal" in name or "threepoint" in name or "freethrow" in name):
                    parts = val_str.split("-")
                    if len(parts) == 2:
                        made = float(parts[0])
                        att = float(parts[1])
                        if "fieldgoal" in name and "three" not in name:
                            raw_stats["fgm"] = made
                            raw_stats["fga"] = att
                        elif "three" in name:
                            raw_stats["fg3m"] = made
                            raw_stats["fg3a"] = att
                        elif "freethrow" in name:
                            raw_stats["ftm"] = made
                            raw_stats["fta"] = att
                    continue

                try:
                    num_val = float(val_str)
                except ValueError:
                    continue

                if name in ("points", "pts"):
                    raw_stats["pts"] = num_val
                elif name in ("fieldgoalsmade", "fgm"):
                    raw_stats["fgm"] = num_val
                elif name in ("fieldgoalsattempted", "fga"):
                    raw_stats["fga"] = num_val
                elif name in ("threepointfieldgoalsmade", "fg3m", "3pm"):
                    raw_stats["fg3m"] = num_val
                elif name in ("threepointfieldgoalsattempted", "fg3a", "3pa"):
                    raw_stats["fg3a"] = num_val
                elif name in ("freethrowsmade", "ftm"):
                    raw_stats["ftm"] = num_val
                elif name in ("freethrowsattempted", "fta"):
                    raw_stats["fta"] = num_val
                elif name in ("offensiverebounds", "oreb"):
                    raw_stats["oreb"] = num_val
                elif name in ("defensiverebounds", "dreb"):
                    raw_stats["dreb"] = num_val
                elif name in ("totalrebounds", "reb"):
                    raw_stats["reb"] = num_val
                elif name in ("assists", "ast"):
                    raw_stats["ast"] = num_val
                elif name in ("steals", "stl"):
                    raw_stats["stl"] = num_val
                elif name in ("blocks", "blk"):
                    raw_stats["blk"] = num_val
                elif name in ("turnovers", "totalturnovers", "to"):
                    raw_stats["to"] = num_val

            if abbr:
                team_stats_out[abbr] = raw_stats
            if display_name:
                team_stats_out[display_name] = raw_stats

        # 2. Player level statistics
        for player_entry in box.get("players", []):
            team_meta = player_entry.get("team", {})
            team_abbr = str(team_meta.get("abbreviation", "")).upper()

            for stat_group in player_entry.get("statistics", []):
                stat_names = [s.upper() for s in stat_group.get("names", [])]
                for athlete_entry in stat_group.get("athletes", []):
                    athlete_data = athlete_entry.get("athlete", {})
                    player_name = str(athlete_data.get("displayName", "")).strip()
                    stats_vals = athlete_entry.get("stats", [])

                    if not player_name or len(stats_vals) != len(stat_names):
                        continue

                    p_stats: Dict[str, float] = {
                        "team": team_abbr,
                        "pts": 0.0,
                        "ast": 0.0,
                        "reb": 0.0,
                        "oreb": 0.0,
                        "dreb": 0.0,
                        "fg3m": 0.0,
                        "stl": 0.0,
                        "blk": 0.0,
                        "to": 0.0,
                        "min": 0.0,
                    }

                    for s_name, s_val_str in zip(stat_names, stats_vals):
                        s_val_str = str(s_val_str).strip()
                        if "-" in s_val_str:
                            # Split "11-22" format
                            parts = s_val_str.split("-")
                            if s_name in ("3PT", "3PM-A", "3P"):
                                try:
                                    p_stats["fg3m"] = float(parts[0])
                                except ValueError:
                                    pass
                            continue

                        try:
                            num = float(s_val_str)
                        except ValueError:
                            continue

                        if s_name in ("PTS", "POINTS"):
                            p_stats["pts"] = num
                        elif s_name in ("AST", "ASSISTS"):
                            p_stats["ast"] = num
                        elif s_name in ("REB", "REBOUNDS"):
                            p_stats["reb"] = num
                        elif s_name in ("OREB", "OFFREB"):
                            p_stats["oreb"] = num
                        elif s_name in ("DREB", "DEFREB"):
                            p_stats["dreb"] = num
                        elif s_name in ("3PM", "FG3M"):
                            p_stats["fg3m"] = num
                        elif s_name in ("STL", "STEALS"):
                            p_stats["stl"] = num
                        elif s_name in ("BLK", "BLOCKS"):
                            p_stats["blk"] = num
                        elif s_name in ("TO", "TURNOVERS"):
                            p_stats["to"] = num
                        elif s_name in ("MIN", "MINUTES"):
                            p_stats["min"] = num

                    player_stats_out[player_name.lower()] = p_stats

        return {
            "teams": team_stats_out,
            "players": player_stats_out,
        }
