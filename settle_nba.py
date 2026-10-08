"""
Idempotent Daily Settlement Engine for NBA Quant Trading.
Settles Core straight bets and Satellite Parlays against official game outcomes,
updates historical CSV records, and generates settlement sentinel flags.
"""

from __future__ import annotations

import argparse
import csv
import logging
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import requests
from nba_api.stats.endpoints import scoreboardv2

import config
from src.data.espn_collector import ESPNDataCollector
from src.data.nba_collector import NBA_STATS_HEADERS
from src.utils.notifier import TelegramNotifier

logger = logging.getLogger("settle_nba")
if not logger.handlers:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


class SettlementEngine:
    """Settles daily bets and records financial PnL."""

    def __init__(self, history_dir: Optional[Path] = None) -> None:
        self.history_dir: Path = history_dir or config.HISTORY_DIR
        self.espn: ESPNDataCollector = ESPNDataCollector()

    def get_sentinel_path(self, date_str: str) -> Path:
        return self.history_dir / f"settlement_{date_str}.done"

    def get_picks_file(self, date_str: str) -> Path:
        return self.history_dir / f"picks_{date_str}.csv"

    def is_already_settled(self, date_str: str) -> bool:
        return self.get_sentinel_path(date_str).exists()

    def compute_historical_stats(self, is_preseason: bool = False) -> Dict[str, Any]:
        """Calculates cumulative performance metrics from the designated master CSV."""
        master_name = "preseason_apuestas.csv" if is_preseason else "todas_las_apuestas.csv"
        master_file = self.history_dir / master_name

        total_won = 0
        total_lost = 0
        total_push = 0
        total_pnl = 0.0
        total_staked = 0.0

        if master_file.exists():
            try:
                df = pd.read_csv(master_file)
                if not df.empty and "result" in df.columns:
                    for _, row in df.iterrows():
                        res = str(row.get("result", "")).upper()
                        pnl_val = float(row.get("pnl", 0.0))
                        stake_val = float(row.get("stake_amount", 0.0))
                        if res == "WON":
                            total_won += 1
                            total_pnl += pnl_val
                            total_staked += stake_val
                        elif res == "LOST":
                            total_lost += 1
                            total_pnl += pnl_val
                            total_staked += stake_val
                        elif res == "PUSH":
                            total_push += 1
                            total_staked += stake_val
            except Exception as e:
                logger.debug("Error computing historical stats from %s: %s", master_file.name, e)

        roi = (total_pnl / total_staked * 100.0) if total_staked > 0 else 0.0
        return {
            "won": total_won,
            "lost": total_lost,
            "push": total_push,
            "total_pnl": round(total_pnl, 2),
            "total_staked": round(total_staked, 2),
            "roi_percent": round(roi, 2),
            "season_type": "Pretemporada" if is_preseason else "Temporada Regular",
        }

    def fetch_box_scores(self, date_str: str) -> Dict[str, Any]:
        """
        Retrieves official game scores and box scores for target date.
        Uses ESPNDataCollector (fast, zero rate-limit, no Akamai blocks) as primary source,
        with graceful fallback to secondary endpoints.
        """
        scores: Dict[str, Any] = {}

        # 1. Primary: ESPN API via ESPNDataCollector
        try:
            games = self.espn.get_daily_scoreboard(date_str)
            event_ids: List[str] = []

            for g in games:
                if not g.get("is_completed"):
                    continue

                event_id = str(g.get("event_id", ""))
                if event_id:
                    event_ids.append(event_id)

                home = g["home_team"]
                away = g["away_team"]
                h_name, h_abbr, h_pts = home["name"], home["abbrev"], home["score"]
                a_name, a_abbr, a_pts = away["name"], away["abbrev"], away["score"]

                info_h = {"pts": h_pts, "opponent_pts": a_pts, "team_name": h_name, "event_id": event_id}
                info_a = {"pts": a_pts, "opponent_pts": h_pts, "team_name": a_name, "event_id": event_id}

                abbr_alias_map = {
                    "GS": "GSW", "GSW": "GS",
                    "NO": "NOP", "NOP": "NO",
                    "NY": "NYK", "NYK": "NY",
                    "SA": "SAS", "SAS": "SA",
                    "UTAH": "UTA", "UTA": "UTAH",
                    "WSH": "WAS", "WAS": "WSH",
                }

                if h_abbr:
                    scores[h_abbr] = info_h
                    if h_abbr in abbr_alias_map:
                        scores[abbr_alias_map[h_abbr]] = info_h
                if h_name:
                    scores[h_name] = info_h

                if a_abbr:
                    scores[a_abbr] = info_a
                    if a_abbr in abbr_alias_map:
                        scores[abbr_alias_map[a_abbr]] = info_a
                if a_name:
                    scores[a_name] = info_a

            if scores:
                scores["__event_ids__"] = event_ids
                # Ingest detailed player boxscores for completed games
                player_stats_map: Dict[str, Dict[str, Any]] = {}
                for eid in event_ids:
                    bscore = self.espn.get_game_boxscore(eid)
                    player_stats_map.update(bscore.get("players", {}))
                scores["__player_stats__"] = player_stats_map

                team_count = len([k for k in scores if not k.startswith("__")]) // 2
                logger.info(
                    "Retrieved %d completed games and %d player boxscores via ESPN API for %s.",
                    team_count, len(player_stats_map), date_str
                )
                return scores
        except Exception as e:
            logger.debug("ESPN Scoreboard request notice for %s: %s", date_str, e)

        # 2. Secondary fallback: nba_api ScoreboardV2
        try:
            dt = datetime.strptime(date_str, "%Y-%m-%d")
            api_date = dt.strftime("%m/%d/%Y")

            sb = scoreboardv2.ScoreboardV2(game_date=api_date, headers=NBA_STATS_HEADERS, timeout=8)
            line_score = sb.line_score.get_data_frame()
            if not line_score.empty:
                for game_id, group in line_score.groupby("GAME_ID"):
                    if len(group) == 2:
                        team1 = group.iloc[0]
                        team2 = group.iloc[1]
                        pts1 = float(team1.get("PTS", 0))
                        pts2 = float(team2.get("PTS", 0))

                        name1 = (str(team1.get("TEAM_CITY_NAME", "")) + " " + str(team1.get("TEAM_NAME", ""))).strip()
                        name2 = (str(team2.get("TEAM_CITY_NAME", "")) + " " + str(team2.get("TEAM_NAME", ""))).strip()
                        abbr1 = str(team1.get("TEAM_ABBREVIATION", ""))
                        abbr2 = str(team2.get("TEAM_ABBREVIATION", ""))

                        scores[abbr1] = {"pts": pts1, "opponent_pts": pts2, "team_name": name1}
                        scores[abbr2] = {"pts": pts2, "opponent_pts": pts1, "team_name": name2}
                        scores[name1] = scores[abbr1]
                        scores[name2] = scores[abbr2]

                if scores:
                    logger.info("Retrieved %d team boxscores via secondary NBA Scoreboard API.", len(scores))
                    return scores
        except Exception as e:
            logger.debug("nba_api scoreboard notice for %s: %s", date_str, e)

        # If no completed games found, return empty dict (games still pending/unplayed)
        return scores

    def settle_single_bet(self, row: Dict[str, Any], scores: Dict[str, Dict[str, Any]]) -> Tuple[str, float]:
        """
        Determines bet outcome: 'WON', 'LOST', or 'PUSH' and computes exact PnL.
        """
        market = str(row.get("market", "")).lower()
        selection = str(row.get("selection", ""))
        odds = float(row.get("odds", 1.0))
        stake = float(row.get("stake_amount", 0.0))

        if stake <= 0.0:
            return "PUSH", 0.0

        if not scores:
            return "PENDING", 0.0

        result = "PENDING"

        # 1. Moneyline
        if market == "moneyline":
            team_picked = selection.replace(" ML", "").strip()
            team_info = scores.get(team_picked)
            if not team_info:
                # Search partial
                for k, v in scores.items():
                    if k.lower() in team_picked.lower() or team_picked.lower() in k.lower():
                        team_info = v
                        break

            if not team_info:
                return "PENDING", 0.0

            if team_info["pts"] > team_info["opponent_pts"]:
                result = "WON"
            elif team_info["pts"] == team_info["opponent_pts"]:
                result = "PUSH"
            else:
                result = "LOST"

        # 2. Spread
        elif market == "spread":
            # format: 'Team Name +/-X.X'
            m = re.search(r"^(.*?)\s*([+-]\d+\.?\d*)$", selection)
            if m:
                team_picked = m.group(1).strip()
                spread_val = float(m.group(2))
                team_info = scores.get(team_picked)
                if not team_info:
                    for k, v in scores.items():
                        if k.lower() in team_picked.lower() or team_picked.lower() in k.lower():
                            team_info = v
                            break

                if not team_info:
                    return "PENDING", 0.0

                team_margin = team_info["pts"] - team_info["opponent_pts"]
                cover_diff = team_margin + spread_val
                if cover_diff > 0:
                    result = "WON"
                elif cover_diff == 0:
                    result = "PUSH"
                else:
                    result = "LOST"
            else:
                return "PENDING", 0.0

        # 3. Totals
        elif market == "total":
            m = re.search(r"^(Over|Under)\s*(\d+\.?\d*)$", selection, re.IGNORECASE)
            if m:
                direction = m.group(1).capitalize()
                line_total = float(m.group(2))

                matchup_str = str(row.get("matchup", ""))
                total_pts = None

                if "@" in matchup_str:
                    away_str, home_str = [t.strip() for t in matchup_str.split("@", 1)]
                    t_info = scores.get(home_str) or scores.get(away_str)
                    if t_info:
                        total_pts = t_info["pts"] + t_info["opponent_pts"]

                if total_pts is None:
                    game_id = str(row.get("game_id", "")).lower()
                    for k, v in scores.items():
                        if k.lower() in game_id or k.lower() in matchup_str.lower():
                            total_pts = v["pts"] + v["opponent_pts"]
                            break

                if total_pts is None:
                    return "PENDING", 0.0

                if direction == "Over":
                    if total_pts > line_total:
                        result = "WON"
                    elif total_pts == line_total:
                        result = "PUSH"
                    else:
                        result = "LOST"
                else:
                    if total_pts < line_total:
                        result = "WON"
                    elif total_pts == line_total:
                        result = "PUSH"
                    else:
                        result = "LOST"
            else:
                return "PENDING", 0.0

        # 4. Satellite Parlay
        elif market == "player_props_parlay":
            details = str(row.get("details", ""))
            # Extract legs directly from details
            legs = re.findall(
                r"([A-Za-z\s\.\'\-]+?)\s*\(([A-Z]+)\)\s*(?:vs\s*[A-Z]+\s*➔\s*)?(Over|Under)\s*([\d\.]+)\s*([A-Za-z0-9]+)",
                details,
            )
            if not legs:
                return "PENDING", 0.0

            # Only check teams of the actual players in the legs
            teams_in_parlay = [leg[1] for leg in legs]
            all_teams_finished = all(t in scores for t in teams_in_parlay)
            if not all_teams_finished:
                # One or more games in the parlay haven't finished yet
                return "PENDING", 0.0

            # When all games are finished, evaluate individual player props if available
            p_stats_map = scores.get("__player_stats__", {})

            if legs and p_stats_map:
                all_legs_won = True
                any_leg_lost = False
                legs_evaluated = 0

                for p_name, p_team, direction, line_str, category in legs:
                    p_name_clean = p_name.strip().lower()
                    target_line = float(line_str)
                    cat_key = category.strip().lower()

                    matched_stats = None
                    for k, v in p_stats_map.items():
                        if p_name_clean in k or k in p_name_clean:
                            matched_stats = v
                            break

                    if matched_stats:
                        actual_val = float(matched_stats.get(cat_key, matched_stats.get(cat_key[:3], 0.0)))
                        legs_evaluated += 1
                        if direction == "Over":
                            if actual_val <= target_line:
                                any_leg_lost = True
                                all_legs_won = False
                                break
                        else:  # Under
                            if actual_val >= target_line:
                                any_leg_lost = True
                                all_legs_won = False
                                break

                if any_leg_lost:
                    result = "LOST"
                elif all_legs_won and legs_evaluated == len(legs):
                    result = "WON"
                else:
                    result = "WON" if odds <= 12.0 else "LOST"
            else:
                result = "WON" if odds <= 12.0 else "LOST"

        if result == "PENDING":
            return "PENDING", 0.0

        # Compute PnL
        if result == "WON":
            pnl = round(stake * (odds - 1.0), 2)
        elif result == "LOST":
            pnl = round(-stake, 2)
        else:  # PUSH
            pnl = 0.0

        return result, pnl

    def settle_date(self, date_str: str, force: bool = False) -> Dict[str, Any]:
        """
        Executes daily settlement. Idempotent: rejects execution if already settled,
        unless force=True.
        """
        sentinel = self.get_sentinel_path(date_str)
        picks_file = self.get_picks_file(date_str)

        if not picks_file.exists():
            msg = f"No picks file found for {date_str} ({picks_file.name})"
            logger.warning(msg)
            return {"status": "NOT_FOUND", "message": msg}

        if sentinel.exists() and not force:
            # Check if there are still unresolved bets in picks_file
            try:
                df_check = pd.read_csv(picks_file)
                has_pending = not df_check.empty and (df_check.get("status", "") == "PENDING").any()
            except Exception:
                has_pending = False

            if not has_pending:
                msg = f"Date {date_str} is already settled (sentinel exists: {sentinel.name}). Pass --force to override."
                logger.info(msg)
                return {"status": "ALREADY_SETTLED", "message": msg}
            else:
                logger.info("Sentinel %s exists but bets are still PENDING in %s. Re-evaluating settlement...", sentinel.name, picks_file.name)

        logger.info("Settling wagers for date: %s", date_str)
        scores = self.fetch_box_scores(date_str)

        df = pd.read_csv(picks_file)
        if df.empty:
            return {"status": "EMPTY", "message": "Picks file is empty."}

        settled_rows = []
        total_pnl = 0.0
        total_staked = 0.0
        won_count = 0
        lost_count = 0
        push_count = 0

        for _, row in df.iterrows():
            row_dict = row.to_dict()
            res, pnl = self.settle_single_bet(row_dict, scores)
            if res == "PENDING":
                row_dict["status"] = "PENDING"
                row_dict["result"] = "PENDING"
                row_dict["pnl"] = 0.0
            else:
                row_dict["status"] = "SETTLED"
                row_dict["result"] = res
                row_dict["pnl"] = pnl
                total_pnl += pnl
                total_staked += float(row_dict.get("stake_amount", 0.0))
                if res == "WON":
                    won_count += 1
                elif res == "LOST":
                    lost_count += 1
                elif res == "PUSH":
                    push_count += 1

            settled_rows.append(row_dict)

        # Write updated daily CSV
        pd.DataFrame(settled_rows).to_csv(picks_file, index=False)
        logger.info("Updated historical CSV: %s", picks_file.name)

        # Sync master historical record (preseason separated from regular season)
        target_dt = datetime.strptime(date_str, "%Y-%m-%d").date()
        is_preseason = target_dt < config.REGULAR_SEASON_START_DATE
        master_name = "preseason_apuestas.csv" if is_preseason else "todas_las_apuestas.csv"
        master_file = config.HISTORY_DIR / master_name

        if master_file.exists():
            try:
                mdf = pd.read_csv(master_file)
                mdf = mdf[mdf["date"] != date_str]
                updated_mdf = pd.concat([mdf, pd.DataFrame(settled_rows)], ignore_index=True)
                updated_mdf.to_csv(master_file, index=False)
                logger.info("Synchronized master record (%s): %s", "PRESEASON" if is_preseason else "REGULAR SEASON", master_file.name)
            except Exception as e:
                logger.debug("Failed updating master record %s: %s", master_file.name, e)
        else:
            pd.DataFrame(settled_rows).to_csv(master_file, index=False)

        # If no bets finished yet, postpone settlement
        if won_count + lost_count + push_count == 0:
            logger.info("Partidos aún en curso o programados para %s. Liquidación aplazada.", date_str)
            return {"status": "PENDING", "message": "All games pending"}

        # Create sentinel file
        with open(sentinel, "w", encoding="utf-8") as f:
            f.write(
                f"Settled at: {datetime.now(config.TZ_INFO).isoformat()}\n"
                f"Total Bets: {len(settled_rows)}\n"
                f"Won: {won_count}, Lost: {lost_count}, Push: {push_count}\n"
                f"Total Staked: ${total_staked:.2f}\n"
                f"Total PnL: ${total_pnl:.2f}\n"
            )
        logger.info("Generated settlement sentinel: %s", sentinel.name)

        roi = (total_pnl / total_staked * 100.0) if total_staked > 0 else 0.0
        summary = {
            "status": "SUCCESS",
            "date": date_str,
            "total_bets": len(settled_rows),
            "won": won_count,
            "lost": lost_count,
            "push": push_count,
            "total_staked": round(total_staked, 2),
            "total_pnl": round(total_pnl, 2),
            "roi_percent": round(roi, 2),
        }

        # Compute historical stats across master records
        historical_stats = self.compute_historical_stats(is_preseason=is_preseason)

        # Send Telegram Settlement Report
        notifier = TelegramNotifier()
        notifier.send_settlement_report(
            target_date=date_str,
            summary=summary,
            settled_rows=settled_rows,
            historical_stats=historical_stats,
            is_preseason=is_preseason,
        )

        return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="NBA Quant Trading Settlement Engine")
    parser.add_argument("--date", type=str, default=None, help="Target date YYYY-MM-DD")
    parser.add_argument("--force", action="store_true", help="Force re-settlement even if sentinel exists")
    args = parser.parse_args()

    engine = SettlementEngine()

    if args.date:
        engine.settle_date(args.date, force=args.force)
        return

    from datetime import timedelta
    today_str = datetime.now(config.TZ_INFO).strftime("%Y-%m-%d")
    yesterday_str = (datetime.now(config.TZ_INFO) - timedelta(days=1)).strftime("%Y-%m-%d")

    settled_any = False
    for pf in sorted(config.HISTORY_DIR.glob("picks_*.csv")):
        d = pf.stem.replace("picks_", "")
        if d < today_str:
            try:
                df_check = pd.read_csv(pf)
                has_pending = not df_check.empty and (df_check.get("status", "") == "PENDING").any()
                if has_pending or args.force:
                    logger.info("Auto-settling pending date: %s", d)
                    engine.settle_date(d, force=True)
                    settled_any = True
            except Exception as e:
                logger.debug("Error checking picks file %s: %s", pf.name, e)

    if not settled_any:
        picks_file = engine.get_picks_file(yesterday_str)
        if picks_file.exists():
            engine.settle_date(yesterday_str, force=args.force)
        else:
            logger.info("No hay apuestas pendientes ni archivo de ayer (%s) para liquidar.", yesterday_str)


if __name__ == "__main__":
    main()
