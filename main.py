"""
Main Orchestration Pipeline for nba-quant-engine.
Runs end-to-end data ingestion, rating models, Benter ensemble, institutional risk filters,
recreational parlay generation, historical CSV persistence, and notification dispatch.
"""

from __future__ import annotations

import argparse
import csv
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

import config
from src.data.nba_collector import NBADataCollector
from src.data.odds_collector import OddsCollector
from src.data.season_stats import normalize_name
from src.models.rating_engine import RatingEngine
from src.models.benter_engine import BenterEngine, BetEvaluation
from src.models.risk_manager import deduplicate_picks_by_game, rescale_daily_exposure
from src.models.player_props import PlayerPropsModel
from src.models.parlay_builder import ParlayBuilder, ParlayLeg, ParlayTicket
from src.utils.notifier import TelegramNotifier

logger = logging.getLogger("nba_quant_engine")
if not logger.handlers:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


def get_current_date_str() -> str:
    """Returns today's date formatted as YYYY-MM-DD in the official timezone."""
    now = datetime.now(config.TZ_INFO)
    return now.strftime("%Y-%m-%d")


def evaluate_player_props(
    players: pd.DataFrame,
    props: Dict[str, Any],
    game_id: str,
    matchup: str,
    team_paces: Dict[str, float],
    expected_pace: float,
    props_model: PlayerPropsModel,
    benter_engine: BenterEngine,
) -> List[BetEvaluation]:
    """Evaluates both sides of each real player line against the player's distribution."""
    bets: List[BetEvaluation] = []
    for category, lines in props.items():
        for pl in players.to_dict(orient="records"):
            entry = lines.get(normalize_name(pl["player_name"]))
            if not entry or pl["gp"] < config.MIN_PROP_GAMES or pl["min"] < config.MIN_PROP_MINUTES:
                continue

            team_pace = team_paces.get(pl["team_abbreviation"], expected_pace)
            pace_adjusted = pl[category] * props_model.get_pace_factor(team_pace, expected_pace)
            market_mean = props_model.market_implied_mean(category, entry["point"], entry["Over"]["market_fair_prob"])
            baseline = props_model.shrink_to_market(pace_adjusted, market_mean, pl["gp"], config.PROP_LINE_PRIOR_GAMES)
            dist = props_model.fit_distribution(
                player_name=entry["name"],
                team_abbreviation=pl["team_abbreviation"],
                category=category,
                baseline_stat=baseline,
                team_pace=expected_pace,
                expected_game_pace=expected_pace,
            )
            p_over = props_model.calculate_over_prob(dist, entry["point"])
            for direction, p_model in (("Over", p_over), ("Under", 1.0 - p_over)):
                bets.append(benter_engine.evaluate_bet(
                    game_id=game_id,
                    market="player_prop",
                    selection=f"{entry['name']} {direction} {entry['point']:g} {category.upper()}",
                    decimal_odds=entry[direction]["odds"],
                    p_model=p_model,
                    p_market_fair=entry[direction]["market_fair_prob"],
                    matchup=matchup,
                ))
    return bets


def _is_on_date(game: Dict[str, Any], target_date: str) -> bool:
    """True if the game tips off on target_date in the official timezone."""
    commence_time = game.get("commence_time")
    if not commence_time:
        return True
    try:
        c_dt = datetime.fromisoformat(commence_time.replace("Z", "+00:00"))
        return c_dt.astimezone(config.TZ_INFO).strftime("%Y-%m-%d") == target_date
    except Exception as e:
        logger.debug("Error parsing commence_time %s: %s", commence_time, e)
        return True


def calculate_weekly_spent(history_dir: Path, target_date_str: str) -> float:
    """Computes total recreational parlay spend in the current ISO week."""
    current_dt = datetime.strptime(target_date_str, "%Y-%m-%d")
    current_year, current_week, _ = current_dt.isocalendar()

    spent = 0.0
    for csv_file in history_dir.glob("picks_*.csv"):
        try:
            file_date_str = csv_file.stem.replace("picks_", "")
            file_dt = datetime.strptime(file_date_str, "%Y-%m-%d")
            f_year, f_week, _ = file_dt.isocalendar()
            if f_year == current_year and f_week == current_week:
                df = pd.read_csv(csv_file)
                if not df.empty and "bet_type" in df.columns:
                    parlays = df[df["bet_type"] == "SATELLITE_PARLAY"]
                    spent += float(parlays["stake_amount"].sum())
        except Exception as e:
            logger.debug("Error parsing weekly spend from %s: %s", csv_file.name, e)

    return round(spent, 2)


def persist_picks_to_csv(
    core_picks: List[BetEvaluation],
    parlay: Optional[ParlayTicket],
    date_str: str,
    is_preseason: bool = False,
    output_dir: Optional[Path] = None,
) -> Optional[Path]:
    """
    Saves daily approved trades.
    If is_preseason is True: operates in SANDBOX mode and does NOT write to
    production 'todas_las_apuestas.csv'.
    """
    if output_dir is None:
        output_dir = config.HISTORY_DIR
    output_dir.mkdir(parents=True, exist_ok=True)
    # Active Paper Trading: always write to picks_{date_str}.csv
    out_file = output_dir / f"picks_{date_str}.csv"

    fieldnames = [
        "date",
        "game_id",
        "matchup",
        "bet_type",  # 'CORE_STRAIGHT' or 'SATELLITE_PARLAY'
        "market",
        "selection",
        "odds",
        "min_odds",
        "model_prob",
        "market_fair_prob",
        "final_prob",
        "edge",
        "ev_percent",
        "stake_fraction",
        "stake_amount",
        "status",  # 'PENDING', 'SETTLED'
        "result",  # 'PENDING', 'WON', 'LOST', 'PUSH'
        "pnl",
        "details",
    ]

    rows: List[Dict[str, Any]] = []

    # 1. Institutional Core Straight Bets
    for p in core_picks:
        rows.append({
            "date": date_str,
            "game_id": p.game_id,
            "matchup": p.matchup if p.matchup else p.game_id,
            "bet_type": "CORE_STRAIGHT",
            "market": p.market,
            "selection": p.selection,
            "odds": p.odds,
            "min_odds": p.min_odds,
            "model_prob": p.model_prob,
            "market_fair_prob": p.market_fair_prob,
            "final_prob": p.final_prob,
            "edge": p.edge,
            "ev_percent": p.ev_percent,
            "stake_fraction": p.recommended_stake_fraction,
            "stake_amount": p.recommended_stake_amount,
            "status": "PENDING",
            "result": "PENDING",
            "pnl": 0.0,
            "details": f"Quarter-Kelly={p.quarter_kelly:.4f}; RawKelly={p.raw_kelly:.4f}",
        })

    # 2. Recreational Satellite Parlay
    if parlay:
        legs_summary = "; ".join(
            f"{l.player_name} ({l.team_abbreviation}) {l.direction} {l.line} {l.category.upper()} @ {l.odds}"
            for l in parlay.legs
        )
        rows.append({
            "date": date_str,
            "game_id": parlay.ticket_id,
            "matchup": "PARLAY_DRAFTEA",
            "bet_type": "SATELLITE_PARLAY",
            "market": "player_props_parlay",
            "selection": parlay.ticket_id,
            "odds": parlay.combined_odds,
            "min_odds": "",
            "model_prob": 0.0,
            "market_fair_prob": 0.0,
            "final_prob": 0.0,
            "edge": 0.0,
            "ev_percent": 0.0,
            "stake_fraction": 0.0,
            "stake_amount": parlay.stake_mxn,
            "status": "PENDING",
            "result": "PENDING",
            "pnl": 0.0,
            "details": f"{parlay.correlation_rationale} | Legs: [{legs_summary}]",
        })

    with open(out_file, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in rows:
            writer.writerow(r)

    # Master file (strictly separated to prevent preseason data contamination)
    master_name = "preseason_apuestas.csv" if is_preseason else "todas_las_apuestas.csv"
    prod_master = output_dir / master_name
    if prod_master.exists():
        try:
            mdf = pd.read_csv(prod_master)
            mdf = mdf[mdf["date"] != date_str]
            updated_mdf = pd.concat([mdf, pd.DataFrame(rows)], ignore_index=True)
            updated_mdf.to_csv(prod_master, index=False)
            logger.info("Updated master record (%s): %s", "PRESEASON" if is_preseason else "REGULAR SEASON", prod_master.name)
        except Exception as e:
            logger.debug("Failed updating master record %s: %s", prod_master.name, e)
            pd.DataFrame(rows).to_csv(prod_master, index=False)
    else:
        pd.DataFrame(rows).to_csv(prod_master, index=False)
        logger.info("Created master record (%s): %s", "PRESEASON" if is_preseason else "REGULAR SEASON", prod_master.name)

    logger.info("Persisted %d approved picks to %s", len(rows), out_file.name)
    return out_file


def run_pipeline(
    date_str: Optional[str] = None,
    force_refresh: bool = False,
    force: bool = False,
    bankroll: Optional[float] = None,
) -> None:
    """Executes full quantitative trading routine."""
    target_date = date_str or get_current_date_str()
    logger.info("Initiating NBA Quant Engine pipeline for target date: %s", target_date)

    sentinel = config.HISTORY_DIR / f"notification_{target_date}.done"
    if sentinel.exists() and not force:
        logger.info(
            "Notification for %s already dispatched (%s). Skipping duplicate Telegram notification. (Pass --force to override)",
            target_date, sentinel.name
        )
        return

    active_bankroll = bankroll if bankroll is not None else config.get_current_bankroll()
    logger.info("Operating with active bankroll: $%.2f MXN", active_bankroll)

    # 1. Ingestion
    nba_collector = NBADataCollector()
    odds_collector = OddsCollector()

    team_df = nba_collector.get_team_stats(force_refresh=force_refresh)
    player_df = nba_collector.get_player_stats(force_refresh=force_refresh)
    league_averages = nba_collector.get_league_averages(team_df)
    games_odds = odds_collector.get_game_odds(force_refresh=force_refresh)

    # Sin mercado real o sin datos de la temporada no se apuesta, y sin centinela para poder reintentar
    if not games_odds:
        logger.error("Sin cuotas para %s: no se generan picks.", target_date)
        return
    if "season_gp" in team_df.columns and team_df["season_gp"].sum() == 0:
        logger.error("Sin partidos de la temporada en curso: no se generan picks.")
        return

    # Pre-index teams by name for fast lookup
    teams_by_name = {t["team_name"]: t for t in team_df.to_dict(orient="records")}
    teams_by_abbrev = {t["team_abbreviation"]: t for t in team_df.to_dict(orient="records")}

    # Sin jugadores con muestra suficiente no se gastan créditos en líneas de jugadores
    eligible_players = (
        not player_df.empty
        and ((player_df["gp"] >= config.MIN_PROP_GAMES) & (player_df["min"] >= config.MIN_PROP_MINUTES)).any()
    )
    today_ids = [g["game_id"] for g in games_odds if _is_on_date(g, target_date)]
    props_by_game = odds_collector.get_player_props(today_ids) if eligible_players else {}
    back_to_back = nba_collector.get_back_to_back_teams(target_date)

    # 2. Rating & Benter Engines
    rating_engine = RatingEngine()
    benter_engine = BenterEngine(bankroll=active_bankroll)
    props_model = PlayerPropsModel(league_average_pace=league_averages["pace"])
    parlay_builder = ParlayBuilder()

    candidate_bets: List[BetEvaluation] = []
    candidate_parlay_legs: List[ParlayLeg] = []

    # 3. Game by Game Analysis
    for game in games_odds:
        game_id = game["game_id"]
        home_name = game["home_team"]
        away_name = game["away_team"]

        # Strict Slate Filter: Only evaluate games tipping off on target_date in local timezone (America/Mexico_City)
        if not _is_on_date(game, target_date):
            logger.info("Ignoring game %s @ %s (not on target date %s)", away_name, home_name, target_date)
            continue

        markets = game.get("markets", {})

        if home_name not in teams_by_name or away_name not in teams_by_name:
            logger.error("Equipo sin ratings en %s @ %s: partido omitido.", away_name, home_name)
            continue
        home_stats = dict(teams_by_name[home_name])
        away_stats = dict(teams_by_name[away_name])

        # Primary lines
        # Fatiga: el equipo que jugó ayer anota menos por posesión
        for stats in (home_stats, away_stats):
            if stats.get("team_abbreviation") in back_to_back:
                stats["ortg"] = stats["ortg"] * (1.0 - config.BACK_TO_BACK_ORTG_PENALTY)

        spread_m = markets.get("spreads", {})
        total_m = markets.get("totals", {})
        h2h_m = markets.get("h2h", {})

        matchup_str = f"{away_name} @ {home_name}"

        home_spread_line = None
        if home_name in spread_m:
            home_spread_line = spread_m[home_name].get("point")

        over_total_line = None
        if "Over" in total_m:
            over_total_line = total_m["Over"].get("point")

        # Run Rating Engine & Monte Carlo Simulation
        sim_res = rating_engine.analyze_game(
            home_stats=home_stats,
            away_stats=away_stats,
            league_averages=league_averages,
            spread_line=home_spread_line,
            total_line=over_total_line,
        )

        # Evaluate Moneyline
        if home_name in h2h_m and away_name in h2h_m:
            home_market = h2h_m[home_name]
            away_market = h2h_m[away_name]

            # Home ML
            eval_home_ml = benter_engine.evaluate_bet(
                game_id=game_id,
                market="moneyline",
                selection=f"{home_name} ML",
                decimal_odds=home_market["odds"],
                p_model=sim_res.home_win_prob,
                p_market_fair=home_market["market_fair_prob"],
                matchup=matchup_str,
            )
            candidate_bets.append(eval_home_ml)

            # Away ML
            eval_away_ml = benter_engine.evaluate_bet(
                game_id=game_id,
                market="moneyline",
                selection=f"{away_name} ML",
                decimal_odds=away_market["odds"],
                p_model=sim_res.away_win_prob,
                p_market_fair=away_market["market_fair_prob"],
                matchup=matchup_str,
            )
            candidate_bets.append(eval_away_ml)

        # Evaluate Spread
        if home_spread_line is not None and home_name in spread_m:
            home_sp_m = spread_m[home_name]
            p_model_cover = sim_res.spread_cover_probs.get(home_spread_line, 0.50)
            eval_spread = benter_engine.evaluate_bet(
                game_id=game_id,
                market="spread",
                selection=f"{home_name} {home_spread_line:+g}",
                decimal_odds=home_sp_m["odds"],
                p_model=p_model_cover,
                p_market_fair=home_sp_m["market_fair_prob"],
                matchup=matchup_str,
            )
            candidate_bets.append(eval_spread)

            if away_name in spread_m:
                away_sp_m = spread_m[away_name]
                candidate_bets.append(benter_engine.evaluate_bet(
                    game_id=game_id,
                    market="spread",
                    selection=f"{away_name} {away_sp_m['point']:+g}",
                    decimal_odds=away_sp_m["odds"],
                    p_model=1.0 - p_model_cover,
                    p_market_fair=away_sp_m["market_fair_prob"],
                    matchup=matchup_str,
                ))

        # Evaluate Totals
        if over_total_line is not None and "Over" in total_m and "Under" in total_m:
            p_model_over = sim_res.total_over_probs.get(over_total_line, 0.50)
            p_model_under = 1.0 - p_model_over

            eval_over = benter_engine.evaluate_bet(
                game_id=game_id,
                market="total",
                selection=f"Over {over_total_line}",
                decimal_odds=total_m["Over"]["odds"],
                p_model=p_model_over,
                p_market_fair=total_m["Over"]["market_fair_prob"],
                matchup=matchup_str,
            )
            candidate_bets.append(eval_over)

            eval_under = benter_engine.evaluate_bet(
                game_id=game_id,
                market="total",
                selection=f"Under {over_total_line}",
                decimal_odds=total_m["Under"]["odds"],
                p_model=p_model_under,
                p_market_fair=total_m["Under"]["market_fair_prob"],
                matchup=matchup_str,
            )
            candidate_bets.append(eval_under)

        # Evaluate real player lines (Core)
        if game_id in props_by_game:
            game_players = player_df[player_df["team_abbreviation"].isin(
                [home_stats.get("team_abbreviation"), away_stats.get("team_abbreviation")]
            )]
            candidate_bets.extend(evaluate_player_props(
                game_players,
                props_by_game[game_id],
                game_id,
                matchup_str,
                {
                    home_stats.get("team_abbreviation"): float(home_stats.get("pace", 100.0)),
                    away_stats.get("team_abbreviation"): float(away_stats.get("pace", 100.0)),
                },
                sim_res.expected_pace,
                props_model,
                benter_engine,
            ))

    # 4. Extract Candidate Player Props for Parlays (Líneas de Piso Alternativas Draftea)
        # PROHIBIDO usar las selecciones directas del Core en los parlays.
        home_abbr = home_stats.get("team_abbreviation")
        away_abbr = away_stats.get("team_abbreviation")
        team_abbrs = [home_abbr, away_abbr]

        if player_df.empty or "team_abbreviation" not in player_df.columns:
            relevant_players = pd.DataFrame()
        else:
            relevant_players = player_df[player_df["team_abbreviation"].isin(team_abbrs)]
        for _, pl in relevant_players.iterrows():
            pl_team_abbr = pl["team_abbreviation"]
            is_home = (pl_team_abbr == home_abbr)
            opp_abbr = away_abbr if is_home else home_abbr
            tm_stats = home_stats if is_home else away_stats

            # Points: Línea de Piso Alternativa Draftea (P >= 75%)
            if pl["pts"] >= 15.0:
                dist_pts = props_model.fit_distribution(
                    player_name=pl["player_name"],
                    team_abbreviation=pl_team_abbr,
                    category="pts",
                    baseline_stat=pl["pts"],
                    team_pace=tm_stats.get("pace", 100.0),
                    expected_game_pace=sim_res.expected_pace,
                )
                floor_pts, prob_pts, mult_pts = props_model.find_alt_floor_line(
                    dist_pts, min_prob=config.PARLAY_MIN_LEG_PROB
                )
                if prob_pts >= config.PARLAY_MIN_LEG_PROB:
                    candidate_parlay_legs.append(
                        ParlayLeg(
                            player_name=pl["player_name"],
                            team_abbreviation=pl_team_abbr,
                            opponent=opp_abbr,
                            category="pts",
                            line=floor_pts,
                            direction="Over",
                            odds=mult_pts,
                            prob=prob_pts,
                            correlation_tag="STACK_PASS_SCORER" if is_home else "PACE_BOOST",
                        )
                    )

            # Assists: Línea de Piso Alternativa Draftea (P >= 75%)
            if pl["ast"] >= 4.0:
                dist_ast = props_model.fit_distribution(
                    player_name=pl["player_name"],
                    team_abbreviation=pl_team_abbr,
                    category="ast",
                    baseline_stat=pl["ast"],
                    team_pace=tm_stats.get("pace", 100.0),
                    expected_game_pace=sim_res.expected_pace,
                )
                floor_ast, prob_ast, mult_ast = props_model.find_alt_floor_line(
                    dist_ast, min_prob=config.PARLAY_MIN_LEG_PROB
                )
                if prob_ast >= config.PARLAY_MIN_LEG_PROB:
                    candidate_parlay_legs.append(
                        ParlayLeg(
                            player_name=pl["player_name"],
                            team_abbreviation=pl_team_abbr,
                            opponent=opp_abbr,
                            category="ast",
                            line=floor_ast,
                            direction="Over",
                            odds=mult_ast,
                            prob=prob_ast,
                            correlation_tag="STACK_PASS_SCORER",
                        )
                    )

            # Rebounds: Línea de Piso Alternativa Draftea (P >= 75%)
            if pl.get("reb", 0) >= 5.0:
                dist_reb = props_model.fit_distribution(
                    player_name=pl["player_name"],
                    team_abbreviation=pl_team_abbr,
                    category="reb",
                    baseline_stat=pl["reb"],
                    team_pace=tm_stats.get("pace", 100.0),
                    expected_game_pace=sim_res.expected_pace,
                )
                floor_reb, prob_reb, mult_reb = props_model.find_alt_floor_line(
                    dist_reb, min_prob=config.PARLAY_MIN_LEG_PROB
                )
                if prob_reb >= config.PARLAY_MIN_LEG_PROB:
                    candidate_parlay_legs.append(
                        ParlayLeg(
                            player_name=pl["player_name"],
                            team_abbreviation=pl_team_abbr,
                            opponent=opp_abbr,
                            category="reb",
                            line=floor_reb,
                            direction="Over",
                            odds=mult_reb,
                            prob=prob_reb,
                            correlation_tag="PACE_BOOST",
                        )
                    )

    # ==========================================
    # MANDATORY GLOBAL RISK FILTERS
    # ==========================================
    logger.info("Total candidate positions evaluated: %d", len(candidate_bets))
    approved_bets = [b for b in candidate_bets if b.is_approved]
    logger.info("Bets passing initial Benter & Kelly floor/ceiling filters: %d", len(approved_bets))

    # 1. Strict Game Deduplication: exactly 1 bet per game_id (highest EV%)
    deduped_bets = deduplicate_picks_by_game(approved_bets)
    logger.info("Bets after strict single-game deduplication: %d", len(deduped_bets))

    # 2. Daily Exposure Normalization: capped at MAX_DAILY_EXPOSURE (10.0%)
    final_core_picks = rescale_daily_exposure(deduped_bets, bankroll=active_bankroll)
    logger.info("Final approved Core institutional picks: %d", len(final_core_picks))

    # ==========================================
    # SATELLITE PARLAY GENERATION (SOÑADORA DRAFTEA)
    # ==========================================
    spent_week = calculate_weekly_spent(config.HISTORY_DIR, target_date)
    best_parlay = parlay_builder.select_best_daily_ticket(
        candidate_legs=candidate_parlay_legs,
        spent_this_week_mxn=spent_week,
    )

    # ==========================================
    # GATEKEEPER PRETEMPORADA, PERSISTENCIA & NOTIFICACIÓN
    # ==========================================
    target_dt_obj = datetime.strptime(target_date, "%Y-%m-%d").date()
    is_preseason = target_dt_obj < config.REGULAR_SEASON_START_DATE

    if is_preseason:
        logger.warning(
            "⚠️ MODO PRETEMPORADA DETECTADO (Fecha %s < %s). Operando en SANDBOX.",
            target_date, config.REGULAR_SEASON_START_DATE
        )

    persist_picks_to_csv(final_core_picks, best_parlay, target_date, is_preseason=is_preseason)

    notifier = TelegramNotifier()
    notifier.send_notification(
        final_core_picks, best_parlay, spent_this_week_mxn=spent_week, is_preseason=is_preseason
    )

    # Sentinel to avoid duplicate Telegram notifications on the same date
    try:
        with open(sentinel, "w", encoding="utf-8") as f:
            f.write(
                f"Notified at: {datetime.now(config.TZ_INFO).isoformat()}\n"
                f"Core picks: {len(final_core_picks)}\n"
                f"Parlay: {best_parlay.ticket_id if best_parlay else 'None'}\n"
            )
        logger.info("Generated notification sentinel: %s", sentinel.name)
    except Exception as e:
        logger.debug("Failed creating sentinel %s: %s", sentinel.name, e)


def main() -> None:
    parser = argparse.ArgumentParser(description="NBA Quantitative Engine Daily Orchestrator")
    parser.add_argument("--date", type=typestr_date, default=None, help="Target date in YYYY-MM-DD format")
    parser.add_argument("--refresh", action="store_true", help="Force fresh data from APIs, ignoring cache")
    parser.add_argument("--force", action="store_true", help="Force execution and notification even if already sent today")
    parser.add_argument("--set-bankroll", type=float, default=None, help="Set current bankroll in MXN and update state file")
    args = parser.parse_args()

    if args.set_bankroll is not None:
        if args.set_bankroll <= 0:
            print("Error: El monto de bankroll debe ser un número positivo (> 0).", file=sys.stderr)
            sys.exit(1)

        new_bankroll = config.set_current_bankroll(args.set_bankroll)
        ceiling_amount = round(config.MAX_KELLY_STAKE * new_bankroll, 2)
        floor_amount = round(config.MIN_KELLY_STAKE * new_bankroll, 2)

        print(f"[BANKROLL ACTUALIZADO] Saldo base configurado en: ${new_bankroll:.2f} MXN")
        print(f"-> Nuevo techo de Kelly ({config.MAX_KELLY_STAKE * 100:.2f}%): ${ceiling_amount:.2f} MXN")
        print(f"-> Nuevo piso operativo ({config.MIN_KELLY_STAKE * 100:.2f}%): ${floor_amount:.2f} MXN")

        if not args.force and args.date is None:
            return

    run_pipeline(date_str=args.date, force_refresh=args.refresh, force=args.force)


def typestr_date(val: str) -> str:
    try:
        datetime.strptime(val, "%Y-%m-%d")
        return val
    except ValueError:
        raise argparse.ArgumentTypeError(f"Invalid date format: '{val}'. Expected YYYY-MM-DD.")


if __name__ == "__main__":
    main()
