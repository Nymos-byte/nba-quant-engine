"""
Walk-forward check of the game model on the season store: each game is predicted using only earlier dates.
Usage: python scripts/evaluate_model.py [store_file]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

import config
from src.data.nba_collector import NBA_TEAMS_REFERENCE
from src.data.season_stats import build_team_ratings, team_points
from src.models.rating_engine import RatingEngine


def walk_forward(store: Dict[str, List[Dict[str, Any]]]) -> pd.DataFrame:
    prior = pd.DataFrame(NBA_TEAMS_REFERENCE)
    engine = RatingEngine()
    past: List[Dict[str, Any]] = []
    rows = []
    for day in sorted(store):
        ratings = build_team_ratings(past, prior)
        by_abbr = {t["team_abbreviation"]: t for t in ratings.to_dict(orient="records")}
        league = {k: float(ratings[k].mean()) for k in ("pace", "ortg", "drtg")}
        for game in store[day]:
            (home, home_box), (away, away_box) = game["teams"].items()
            sim = engine.analyze_game(by_abbr[home], by_abbr[away], league)
            margin = team_points(home_box) - team_points(away_box)
            total = team_points(home_box) + team_points(away_box)
            rows.append({
                "date": day,
                "p_home": sim.home_win_prob,
                "home_won": float(margin > 0),
                "margin_error": margin - sim.expected_spread,
                "total_error": total - sim.expected_total,
            })
        past.extend(store[day])
    return pd.DataFrame(rows)


if __name__ == "__main__":
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else config.CACHE_DIR / config.SEASON_STORE_FILE
    with open(path, "r", encoding="utf-8") as f:
        results = walk_forward(json.load(f))
    brier = ((results["p_home"] - results["home_won"]) ** 2).mean()
    print(f"Partidos: {len(results)}")
    print(f"Brier del modelo: {brier:.4f} (moneda al aire: 0.2500)")
    print(f"Margen: sesgo {results['margin_error'].mean():+.2f}, error absoluto {results['margin_error'].abs().mean():.2f}")
    print(f"Total: sesgo {results['total_error'].mean():+.2f}, error absoluto {results['total_error'].abs().mean():.2f}")
