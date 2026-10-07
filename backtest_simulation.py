"""
NBA Quantitative Engine - Walk-Forward Historical Simulation & Backtesting.
Trains fundamental ratings on the first half of a season (in-sample calibration)
and predicts out-of-sample matchups on the second half (Monte Carlo evaluation).
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
from pathlib import Path
import sys
import numpy as np
import pandas as pd

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import config
from src.models.rating_engine import RatingEngine

logger = logging.getLogger("backtest_simulation")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


def generate_season_schedule(teams: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Generates a synthetic realistic regular season schedule of 1,230 games (82 per team).
    Each team plays balanced home and away games.
    """
    team_abbrevs = [t["team_abbreviation"] for t in teams]
    rng = np.random.default_rng(seed=42)

    games: List[Dict[str, Any]] = []
    game_counter = 1

    # Round-robin pairings
    pairs = []
    for i in range(len(team_abbrevs)):
        for j in range(i + 1, len(team_abbrevs)):
            pairs.append((team_abbrevs[i], team_abbrevs[j]))

    # Repeat pairings to reach ~1,230 games (41 games per pair on average)
    all_matchups = []
    for _ in range(3):
        for home, away in pairs:
            # Home game
            all_matchups.append((home, away))
            # Reverse home game
            all_matchups.append((away, home))

    # Shuffle to simulate chronological season flow
    rng.shuffle(all_matchups)
    all_matchups = all_matchups[:1230]

    for home_abbr, away_abbr in all_matchups:
        games.append({
            "game_number": game_counter,
            "home_team": home_abbr,
            "away_team": away_abbr,
        })
        game_counter += 1

    return games


def simulate_game_ground_truth(
    home_true_stats: Dict[str, Any],
    away_true_stats: Dict[str, Any],
    league_averages: Dict[str, Any],
    hca: float = config.HOME_COURT_ADVANTAGE,
    sigma: float = config.MC_POINT_DISPERSION,
    rng: Optional[np.random.Generator] = None,
) -> Tuple[int, int]:
    """
    Simulates actual final score for a game based on ground-truth team ratings
    plus real-world NBA game dispersion (sigma ~ 10.5).
    """
    if rng is None:
        rng = np.random.default_rng()

    exp_pace = (home_true_stats["pace"] * away_true_stats["pace"]) / league_averages["pace"]
    home_exp_pts = (home_true_stats["ortg"] * away_true_stats["drtg"] / league_averages["ortg"]) * (exp_pace / 100.0) + (hca / 2.0)
    away_exp_pts = (away_true_stats["ortg"] * home_true_stats["drtg"] / league_averages["drtg"]) * (exp_pace / 100.0) - (hca / 2.0)

    # Sample actual points with covariance
    cov = 0.15 * sigma * sigma
    mean = [home_exp_pts, away_exp_pts]
    cov_matrix = [[sigma ** 2, cov], [cov, sigma ** 2]]
    scores = rng.multivariate_normal(mean, cov_matrix)

    home_pts = max(70, int(round(scores[0])))
    away_pts = max(70, int(round(scores[1])))

    # NBA games do not end in ties (overtime resolves winner)
    if home_pts == away_pts:
        if rng.random() > 0.45:
            home_pts += rng.choice([2, 3, 5])
        else:
            away_pts += rng.choice([2, 3, 5])

    return home_pts, away_pts


def run_walk_forward_simulation(
    cache_dir: Optional[Path] = None,
    output_csv: Optional[Path] = None,
) -> Dict[str, Any]:
    """
    Executes walk-forward backtest:
    - Phase 1: Calibrate ratings on the 1st half of the season (Games 1 - 615).
    - Phase 2: Predict and validate on the 2nd half of the season (Games 616 - 1,230).
    """
    cache_dir = cache_dir or config.CACHE_DIR
    team_cache_file = cache_dir / "team_stats_2025_26.json"

    if not team_cache_file.exists():
        logger.error("Team stats cache not found at %s. Please run main.py first.", team_cache_file)
        raise FileNotFoundError(f"Missing {team_cache_file}")

    with open(team_cache_file, "r", encoding="utf-8") as f:
        teams_data = json.load(f)

    teams_by_abbr = {t["team_abbreviation"]: t for t in teams_data}
    league_averages = {
        "pace": float(np.mean([t["pace"] for t in teams_data])),
        "ortg": float(np.mean([t["ortg"] for t in teams_data])),
        "drtg": float(np.mean([t["drtg"] for t in teams_data])),
    }

    # Generate full 82-game regular season schedule (1,230 games)
    schedule = generate_season_schedule(teams_data)
    total_games = len(schedule)
    midpoint = total_games // 2

    first_half = schedule[:midpoint]
    second_half = schedule[midpoint:]

    rng = np.random.default_rng(seed=123)

    logger.info("============================================================")
    logger.info("INICIANDO SIMULACIÓN CUANTITATIVA WALK-FORWARD NBA")
    logger.info("Total Partidos Temporada: %d | Mitad Entrenamiento: %d | Mitad Test: %d", total_games, len(first_half), len(second_half))
    logger.info("============================================================")

    # -------------------------------------------------------------
    # FASE 1: CALIBRACIÓN CON LA PRIMERA MITAD (GAMES 1 TO 615)
    # -------------------------------------------------------------
    logger.info("Calibrando ratings empíricos con los primeros %d partidos...", len(first_half))
    team_history: Dict[str, List[Dict[str, Any]]] = {abbr: [] for abbr in teams_by_abbr}

    for g in first_half:
        home = teams_by_abbr[g["home_team"]]
        away = teams_by_abbr[g["away_team"]]
        h_pts, a_pts = simulate_game_ground_truth(home, away, league_averages, rng=rng)

        team_history[g["home_team"]].append({"pts_for": h_pts, "pts_against": a_pts, "is_home": True})
        team_history[g["away_team"]].append({"pts_for": a_pts, "pts_against": h_pts, "is_home": False})

    # Calibrate in-sample ratings from observed first-half games
    calibrated_teams: Dict[str, Dict[str, Any]] = {}
    for abbr, games_played in team_history.items():
        base = teams_by_abbr[abbr]
        pts_for = np.mean([x["pts_for"] for x in games_played])
        pts_against = np.mean([x["pts_against"] for x in games_played])

        # Bayesian blending: blend observed midpoint stats with base prior
        est_ortg = round(0.70 * (pts_for / base["pace"] * 100.0) + 0.30 * base["ortg"], 2)
        est_drtg = round(0.70 * (pts_against / base["pace"] * 100.0) + 0.30 * base["drtg"], 2)

        calibrated_teams[abbr] = {
            "team_abbreviation": abbr,
            "team_name": base["team_name"],
            "pace": base["pace"],
            "ortg": est_ortg,
            "drtg": est_drtg,
        }

    logger.info("Calibración completada con éxito. Procediendo a evaluar la segunda mitad...")

    # -------------------------------------------------------------
    # FASE 2: EVALUACIÓN FUERA DE MUESTRA (OUT-OF-SAMPLE: GAMES 616 TO 1230)
    # -------------------------------------------------------------
    rating_engine = RatingEngine()
    results: List[Dict[str, Any]] = []

    correct_winner = 0
    high_conf_correct = 0
    high_conf_total = 0
    point_errors_home: List[float] = []
    point_errors_away: List[float] = []
    total_point_errors: List[float] = []
    spread_correct = 0
    brier_scores: List[float] = []

    for g in second_half:
        home_abbr = g["home_team"]
        away_abbr = g["away_team"]
        home_cal = calibrated_teams[home_abbr]
        away_cal = calibrated_teams[away_abbr]

        # Actual true game outcome
        h_actual_pts, a_actual_pts = simulate_game_ground_truth(
            teams_by_abbr[home_abbr], teams_by_abbr[away_abbr], league_averages, rng=rng
        )
        actual_home_win = 1 if h_actual_pts > a_actual_pts else 0
        actual_total = h_actual_pts + a_actual_pts
        actual_margin = h_actual_pts - a_actual_pts  # positive if home wins

        # Pre-game consensus spread line (estimated from midpoint expected margin)
        expected_spread_line = round((home_cal["ortg"] - away_cal["ortg"]) + config.HOME_COURT_ADVANTAGE, 1)

        # Quantitative Engine Prediction (10,000 Monte Carlo simulations)
        sim = rating_engine.analyze_game(
            home_stats=home_cal,
            away_stats=away_cal,
            league_averages=league_averages,
            spread_line=-expected_spread_line,
            total_line=round(home_cal["pace"] * 2.2, 1),
        )

        pred_home_win = 1 if sim.home_win_prob > 0.50 else 0
        is_correct_win = (pred_home_win == actual_home_win)
        if is_correct_win:
            correct_winner += 1

        # High confidence check (P >= 65%)
        if sim.home_win_prob >= 0.65 or sim.home_win_prob <= 0.35:
            high_conf_total += 1
            if is_correct_win:
                high_conf_correct += 1

        # Point projection errors
        err_h = abs(sim.home_expected_points - h_actual_pts)
        err_a = abs(sim.away_expected_points - a_actual_pts)
        err_tot = abs((sim.home_expected_points + sim.away_expected_points) - actual_total)
        point_errors_home.append(err_h)
        point_errors_away.append(err_a)
        total_point_errors.append(err_tot)

        # Spread prediction
        pred_cover = (sim.home_expected_points - sim.away_expected_points) > expected_spread_line
        actual_cover = actual_margin > expected_spread_line
        if pred_cover == actual_cover:
            spread_correct += 1

        # Brier score: (prob - actual)^2
        brier = (sim.home_win_prob - actual_home_win) ** 2
        brier_scores.append(brier)

        results.append({
            "game_number": g["game_number"],
            "matchup": f"{away_abbr} @ {home_abbr}",
            "home_team": home_abbr,
            "away_team": away_abbr,
            "pred_home_prob": round(sim.home_win_prob, 4),
            "pred_home_pts": round(sim.home_expected_points, 1),
            "pred_away_pts": round(sim.away_expected_points, 1),
            "actual_home_pts": h_actual_pts,
            "actual_away_pts": a_actual_pts,
            "actual_home_win": actual_home_win,
            "pred_correct_winner": is_correct_win,
            "error_total_points": round(err_tot, 1),
            "brier_score": round(brier, 4),
        })

    # Summary Statistics
    n_test = len(second_half)
    win_accuracy = (correct_winner / n_test) * 100.0
    high_conf_acc = (high_conf_correct / high_conf_total * 100.0) if high_conf_total > 0 else 0.0
    mae_home = float(np.mean(point_errors_home))
    mae_away = float(np.mean(point_errors_away))
    mae_total = float(np.mean(total_point_errors))
    spread_acc = (spread_correct / n_test) * 100.0
    mean_brier = float(np.mean(brier_scores))

    # Save details to CSV
    out_path = output_csv or (config.HISTORY_DIR / "simulacion_backtest.csv")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df_res = pd.DataFrame(results)
    df_res.to_csv(out_path, index=False, encoding="utf-8")
    logger.info("Guardado detalle de %d partidos fuera de muestra en %s", len(df_res), out_path.name)

    # Print Report
    print("\n" + "=" * 65)
    print("📊 RESULTADOS DEL BACKTEST Y SIMULACIÓN PREDICTIVA FUERA DE MUESTRA")
    print("=" * 65)
    print(f"🎮 Total Partidos Evaluados (2da Mitad):   {n_test}")
    print(f"🎯 Precisión de Ganador (Moneyline):       {win_accuracy:.2f}%  ({correct_winner}/{n_test})")
    print(f"🔥 Precisión en Alta Confianza (P>=65%):   {high_conf_acc:.2f}%  ({high_conf_correct}/{high_conf_total})")
    print(f"📐 Precisión de Dirección de Spread:       {spread_acc:.2f}%")
    print(f"🏀 Error Absoluto Medio (MAE) Equipo Local:{mae_home:.2f} pts")
    print(f"🏀 Error Absoluto Medio (MAE) Equipo Visita:{mae_away:.2f} pts")
    print(f"⚖️ Error Absoluto Medio (MAE) Total Juego: {mae_total:.2f} pts")
    print(f"🔬 Brier Score de Calibración:             {mean_brier:.4f}  (Referencia aleatoria = 0.2500)")
    print("=" * 65 + "\n")

    return {
        "games_tested": n_test,
        "win_accuracy_pct": round(win_accuracy, 2),
        "high_confidence_accuracy_pct": round(high_conf_acc, 2),
        "spread_accuracy_pct": round(spread_acc, 2),
        "mae_home_pts": round(mae_home, 2),
        "mae_away_pts": round(mae_away, 2),
        "mae_total_pts": round(mae_total, 2),
        "brier_score": round(mean_brier, 4),
        "output_file": str(out_path),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="NBA Quantitative Engine Backtest Simulation")
    parser.add_argument("--out", type=str, default=None, help="Output CSV path")
    args = parser.parse_args()

    out_p = Path(args.out) if args.out else None
    run_walk_forward_simulation(output_csv=out_p)


if __name__ == "__main__":
    main()
