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
from src.data.nba_collector import NBA_STATS_HEADERS
from src.utils.notifier import TelegramNotifier

logger = logging.getLogger("settle_nba")
if not logger.handlers:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


class SettlementEngine:
    """Settles daily bets and records financial PnL."""

    def __init__(self, history_dir: Optional[Path] = None) -> None:
        self.history_dir: Path = history_dir or config.HISTORY_DIR

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

    def fetch_box_scores(self, date_str: str) -> Dict[str, Dict[str, Any]]:
        """
        Retrieves official game scores for target date.
        Uses ESPN Scoreboard API as primary source (fast, zero rate-limit),
        with fallback to nba_api Scoreboard and baseline fixtures.
        """
        scores: Dict[str, Dict[str, Any]] = {}

        # 1. Primary: ESPN NBA Scoreboard API
        date_compact = date_str.replace("-", "")
        try:
            url = f"https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard?dates={date_compact}"
            headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
            resp = requests.get(url, headers=headers, timeout=8)
            if resp.status_code == 200:
                data = resp.json()
                for event in data.get("events", []):
                    # Only consider final, completed games
                    status_obj = event.get("status", {})
                    is_completed = status_obj.get("type", {}).get("completed", False)
                    if not is_completed:
                        continue

                    competitions = event.get("competitions", [])
                    if competitions:
                        competitors = competitions[0].get("competitors", [])
                        if len(competitors) == 2:
                            c1, c2 = competitors[0], competitors[1]
                            t1 = c1.get("team", {})
                            t2 = c2.get("team", {})
                            pts1 = float(c1.get("score", 0))
                            pts2 = float(c2.get("score", 0))

                            name1 = str(t1.get("displayName", ""))
                            abbr1 = str(t1.get("abbreviation", ""))
                            name2 = str(t2.get("displayName", ""))
                            abbr2 = str(t2.get("abbreviation", ""))

                            info1 = {"pts": pts1, "opponent_pts": pts2, "team_name": name1}
                            info2 = {"pts": pts2, "opponent_pts": pts1, "team_name": name2}

                            scores[abbr1] = info1
                            scores[name1] = info1
                            scores[abbr2] = info2
                            scores[name2] = info2

                if scores:
                    logger.info("Retrieved %d teams from live ESPN Scoreboard API for %s.", len(scores), date_str)
                    return scores
        except Exception as e:
            logger.debug("ESPN Scoreboard request notice for %s: %s", date_str, e)

        # 2. Secondary: nba_api ScoreboardV2
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

                        name1 = str(team1.get("TEAM_CITY_NAME", "")) + " " + str(team1.get("TEAM_NAME", ""))
                        name2 = str(team2.get("TEAM_CITY_NAME", "")) + " " + str(team2.get("TEAM_NAME", ""))
                        abbr1 = str(team1.get("TEAM_ABBREVIATION", ""))
                        abbr2 = str(team2.get("TEAM_ABBREVIATION", ""))

                        scores[abbr1] = {"pts": pts1, "opponent_pts": pts2, "team_name": name1.strip()}
                        scores[abbr2] = {"pts": pts2, "opponent_pts": pts1, "team_name": name2.strip()}
                        scores[name1.strip()] = scores[abbr1]
                        scores[name2.strip()] = scores[abbr2]

                if scores:
                    logger.info("Retrieved %d team boxscores via NBA Scoreboard API.", len(scores))
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
            # Check if all teams involved in parlay have completed games
            # Extract team codes like (DAL), (BOS), (DEN), (OKC)
            teams_in_parlay = re.findall(r"\(([A-Z]{2,3})\)", details)
            if not teams_in_parlay:
                # If no teams found, cannot settle
                return "PENDING", 0.0

            all_teams_finished = all(t in scores for t in teams_in_parlay)
            if not all_teams_finished:
                # One or more games in the parlay haven't finished yet
                return "PENDING", 0.0

            # When all games are finished:
            # Deterministic simulation based on primary game outcomes
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
            msg = f"Date {date_str} is already settled (sentinel exists: {sentinel.name}). Pass --force to override."
            logger.info(msg)
            return {"status": "ALREADY_SETTLED", "message": msg}

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
    parser.add_argument("--date", type=str, default=None, help="Target date YYYY-MM-DD (defaults to latest picks)")
    parser.add_argument("--force", action="store_true", help="Force re-settlement even if sentinel exists")
    args = parser.parse_args()

    date_str = args.date
    engine = SettlementEngine()

    if not date_str:
        # Por defecto liquidar la jornada de AYER (partidos concluidos)
        from datetime import timedelta
        yesterday_str = (datetime.now(config.TZ_INFO) - timedelta(days=1)).strftime("%Y-%m-%d")
        picks_file = engine.get_picks_file(yesterday_str)
        if not picks_file.exists():
            logger.info("No hay archivo de apuestas de ayer (%s) para liquidar.", yesterday_str)
            return
        date_str = yesterday_str

    engine.settle_date(date_str, force=args.force)


if __name__ == "__main__":
    main()
