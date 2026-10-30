"""
Current-season team ratings and player averages built from ESPN box scores.
Stores one compact record per game keyed by date, so each game is downloaded only once.
"""

from __future__ import annotations

import json
import logging
import unicodedata
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

import config
from src.data.espn_collector import ESPNDataCollector

logger = logging.getLogger(__name__)

ESPN_ABBR_TO_NBA: Dict[str, str] = {
    "GS": "GSW",
    "NO": "NOP",
    "NY": "NYK",
    "SA": "SAS",
    "UTAH": "UTA",
    "WSH": "WAS",
}

PLAYER_COLUMNS: List[str] = [
    "player_name", "team_abbreviation", "gp", "min", "pts", "reb", "ast", "fg3m", "team_pts_share",
]


def normalize_name(name: str) -> str:
    """Lowercases a player name and strips accents so names from different sources compare equal."""
    decomposed = unicodedata.normalize("NFKD", name)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).strip().lower()


def nba_abbr(espn_abbr: str) -> str:
    return ESPN_ABBR_TO_NBA.get(espn_abbr, espn_abbr)


def possessions(team: Dict[str, float]) -> float:
    return team["fga"] - team["oreb"] + team["to"] + 0.44 * team["fta"]


def team_points(team: Dict[str, float]) -> float:
    return 2.0 * (team["fgm"] - team["fg3m"]) + 3.0 * team["fg3m"] + team["ftm"]


def _compact_game(espn_game: Dict[str, Any], box: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Reduces an ESPN summary to the fields ratings need, or None if the box score is incomplete."""
    teams: Dict[str, Dict[str, float]] = {}
    for side in ("home_team", "away_team"):
        abbr = str(espn_game[side]["abbrev"])
        raw = box.get("teams", {}).get(abbr)
        if not raw or raw.get("fga", 0.0) <= 0:
            return None
        teams[nba_abbr(abbr)] = {k: raw[k] for k in ("fgm", "fga", "fg3m", "ftm", "fta", "oreb", "to")}

    players = {
        name: {
            "team": nba_abbr(str(p["team"])),
            **{k: p[k] for k in ("min", "pts", "reb", "ast", "fg3m")},
        }
        for name, p in box.get("players", {}).items()
    }
    return {"teams": teams, "players": players}


class SeasonStatsStore:
    """Downloads and accumulates finished games of the season being played."""

    def __init__(
        self,
        espn: Optional[ESPNDataCollector] = None,
        store_file: Optional[Path] = None,
        season_start: date = config.REGULAR_SEASON_START_DATE,
    ) -> None:
        self.espn = espn or ESPNDataCollector()
        self.store_file = store_file or config.CACHE_DIR / config.SEASON_STORE_FILE
        self.season_start = season_start

    def _load(self) -> Dict[str, List[Dict[str, Any]]]:
        if self.store_file.exists():
            try:
                with open(self.store_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.warning("Could not read %s: %s", self.store_file.name, e)
        return {}

    def teams_played_on(self, day: date) -> set[str]:
        """Teams with a stored game on the given date."""
        return {abbr for game in self._load().get(day.isoformat(), []) for abbr in game["teams"]}

    def update(self, today: date) -> List[Dict[str, Any]]:
        """Fetches every past date not stored yet and returns all stored games."""
        store = self._load()
        changed = False
        current = self.season_start
        while current < today:
            key = current.isoformat()
            current += timedelta(days=1)
            if key in store:
                continue
            games = self.espn.get_daily_scoreboard(key)
            # An empty list can be a day off or a failed request: retry it on the next run.
            if not games or not all(g.get("is_completed") for g in games):
                continue
            compact = [
                _compact_game(g, self.espn.get_game_boxscore(str(g["event_id"])))
                for g in games
            ]
            if all(compact):
                store[key] = compact
                changed = True

        if changed:
            self.store_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.store_file, "w", encoding="utf-8") as f:
                json.dump(store, f, ensure_ascii=False, separators=(",", ":"))
        return [g for day in store.values() for g in day]


def build_team_ratings(
    games: List[Dict[str, Any]],
    prior: pd.DataFrame,
    prior_games: float = config.RATING_PRIOR_GAMES,
) -> pd.DataFrame:
    """
    Blends the season's ratings with last season's baseline:
        rating = (gp * season + prior_games * prior) / (gp + prior_games)
    The weight of the baseline fades as the current season accumulates games.
    """
    totals: Dict[str, Dict[str, float]] = {}
    for game in games:
        (abbr_a, a), (abbr_b, b) = game["teams"].items()
        poss = (possessions(a) + possessions(b)) / 2.0
        pts_a, pts_b = team_points(a), team_points(b)
        for abbr, pts, opp in ((abbr_a, pts_a, pts_b), (abbr_b, pts_b, pts_a)):
            t = totals.setdefault(abbr, {"gp": 0.0, "poss": 0.0, "pts": 0.0, "opp": 0.0})
            t["gp"] += 1
            t["poss"] += poss
            t["pts"] += pts
            t["opp"] += opp

    rows = []
    for base in prior.to_dict(orient="records"):
        t = totals.get(base["team_abbreviation"])
        gp = t["gp"] if t else 0.0
        season = {
            "pace": t["poss"] / gp,
            "ortg": 100.0 * t["pts"] / t["poss"],
            "drtg": 100.0 * t["opp"] / t["poss"],
        } if t else {}
        blended = {
            k: (gp * season[k] + prior_games * base[k]) / (gp + prior_games) if t else base[k]
            for k in ("pace", "ortg", "drtg")
        }
        rows.append({
            **base,
            **{k: round(v, 2) for k, v in blended.items()},
            "pts": round(blended["ortg"] * blended["pace"] / 100.0, 2),
            "gp": int(gp),
            "season_gp": int(gp),
        })
    return pd.DataFrame(rows)


def build_player_stats(games: List[Dict[str, Any]], min_games: int = config.MIN_PLAYER_GAMES) -> pd.DataFrame:
    """Per-game averages of the current season; team is the one of the player's latest game."""
    totals: Dict[str, Dict[str, Any]] = {}
    team_pts: Dict[str, float] = {}
    for game in games:
        for abbr, team in game["teams"].items():
            team_pts[abbr] = team_pts.get(abbr, 0.0) + team_points(team)
        for name, p in game["players"].items():
            t = totals.setdefault(name, {"gp": 0, "min": 0.0, "pts": 0.0, "reb": 0.0, "ast": 0.0, "fg3m": 0.0})
            t["gp"] += 1
            t["team"] = p["team"]
            for k in ("min", "pts", "reb", "ast", "fg3m"):
                t[k] += p[k]

    rows = []
    for name, t in totals.items():
        if t["gp"] < min_games:
            continue
        rows.append({
            "player_name": name.title(),
            "team_abbreviation": t["team"],
            "gp": t["gp"],
            **{k: round(t[k] / t["gp"], 2) for k in ("min", "pts", "reb", "ast", "fg3m")},
            "team_pts_share": round(t["pts"] / team_pts[t["team"]], 4) if team_pts.get(t["team"]) else 0.0,
        })
    return pd.DataFrame(rows, columns=PLAYER_COLUMNS)


def injury_scoring_loss(
    players: pd.DataFrame,
    injuries: Dict[str, str],
    replacement_rate: float = config.INJURY_REPLACEMENT_RATE,
    max_penalty: float = config.MAX_INJURY_ORTG_PENALTY,
) -> Dict[str, float]:
    """
    Fraction of each team's offense lost to players ruled Out.
    A player's share counts only the points already in the team's season ratings, so someone
    injured all season has no effect: the ratings already reflect the absence.
    """
    losses: Dict[str, float] = {}
    if players.empty:
        return losses
    for p in players.to_dict(orient="records"):
        if injuries.get(p["player_name"].lower()) == "Out":
            lost = p["team_pts_share"] * (1.0 - replacement_rate)
            losses[p["team_abbreviation"]] = losses.get(p["team_abbreviation"], 0.0) + lost
    return {team: min(loss, max_penalty) for team, loss in losses.items()}
