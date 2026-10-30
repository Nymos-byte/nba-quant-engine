"""
Records the latest odds before tip-off for pending Core bets, so settled bets can report CLV.
Each run overwrites the previous capture until the game starts: the last one is the closing line.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

import config
from src.data.odds_collector import OddsCollector
from src.data.season_stats import normalize_name

logger = logging.getLogger("capture_closing")
if not logger.handlers:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


def _find_outcome(row: Dict[str, Any], markets: Dict[str, Any]) -> Optional[Tuple[Dict[str, Any], Optional[float]]]:
    """Returns the market outcome for the bet and the line it was placed at (None for moneyline)."""
    market = str(row.get("market", ""))
    selection = str(row.get("selection", ""))
    if market == "player_prop":
        player, direction, line, category = selection.rsplit(" ", 3)
        entry = markets.get("player_props", {}).get(category.lower(), {}).get(normalize_name(player))
        return ({"point": entry["point"], **entry[direction]}, float(line)) if entry else None

    if market == "moneyline":
        outcome = markets.get("h2h", {}).get(selection.replace(" ML", "").strip())
        return (outcome, None) if outcome else None

    name, _, line = selection.rpartition(" ")
    source = markets.get("spreads" if market == "spread" else "totals", {})
    outcome = source.get(name.strip())
    return (outcome, float(line)) if outcome else None


def clv_percent(bet_odds: float, closing_fair_prob: float) -> float:
    """Expected return of the bet at the closing fair probability."""
    return round((bet_odds * closing_fair_prob - 1.0) * 100.0, 2)


def update_closing_columns(
    df: pd.DataFrame,
    games: List[Dict[str, Any]],
    now: datetime,
) -> Tuple[pd.DataFrame, int]:
    """Updates closing columns of pending Core bets whose game has not started."""
    games_by_id = {g["game_id"]: g for g in games}
    for col in ("closing_odds", "closing_point", "closing_fair_prob", "clv_percent"):
        if col not in df.columns:
            df[col] = pd.NA
        df[col] = df[col].astype(object)

    updated = 0
    for idx, row in df.iterrows():
        game = games_by_id.get(row.get("game_id"))
        if row.get("status") != "PENDING" or row.get("bet_type") != "CORE_STRAIGHT" or not game:
            continue
        tipoff = datetime.fromisoformat(str(game["commence_time"]).replace("Z", "+00:00"))
        if tipoff <= now:
            continue
        found = _find_outcome(row.to_dict(), game.get("markets", {}))
        if not found:
            continue
        outcome, bet_line = found
        closing_point = outcome.get("point")
        df.at[idx, "closing_odds"] = outcome["odds"]
        df.at[idx, "closing_point"] = closing_point if closing_point is not None else pd.NA
        df.at[idx, "closing_fair_prob"] = outcome["market_fair_prob"]
        # With a different line the probabilities are not comparable: no CLV for this bet
        same_line = bet_line is None or closing_point == bet_line
        df.at[idx, "clv_percent"] = (
            clv_percent(float(row["odds"]), outcome["market_fair_prob"]) if same_line else pd.NA
        )
        updated += 1
    return df, updated


def run(history_dir: Optional[Path] = None, collector: Optional[OddsCollector] = None) -> int:
    history_dir = history_dir or config.HISTORY_DIR
    now_local = datetime.now(config.TZ_INFO)
    picks_file = history_dir / f"picks_{now_local.strftime('%Y-%m-%d')}.csv"
    if not picks_file.exists():
        logger.info("No hay picks de hoy (%s): no se consulta la API.", picks_file.name)
        return 0

    df = pd.read_csv(picks_file)
    has_pending = not df.empty and (df["status"] == "PENDING").any()
    if not has_pending:
        return 0

    # Solo se piden los mercados con apuestas pendientes: cada uno cuesta créditos
    pending = df[(df["status"] == "PENDING") & (df["bet_type"] == "CORE_STRAIGHT")]
    score_markets = [
        api for bet, api in (("moneyline", "h2h"), ("spread", "spreads"), ("total", "totals"))
        if (pending["market"] == bet).any()
    ]
    pending_props = pending[pending["market"] == "player_prop"]
    if not score_markets and pending_props.empty:
        return 0

    odds = collector or OddsCollector()
    games = odds.get_game_odds(markets=",".join(score_markets), force_refresh=True) if score_markets else []
    if score_markets and not games:
        logger.warning("Sin cuotas en vivo: no se actualiza el cierre.")
        return 0

    if not pending_props.empty:
        categories = [str(s).rsplit(" ", 1)[1].lower() for s in pending_props["selection"]]
        props = odds.get_player_props(list(pending_props["game_id"].unique()), categories=categories, paced=False)
        known = {g["game_id"]: g for g in games}
        for event_id, event_props in props.items():
            if event_id not in known:
                # Sin apuestas de marcador la hora de inicio no viene en la respuesta de props
                tipoff = odds.get_commence_time(event_id)
                if not tipoff:
                    continue
                known[event_id] = {"game_id": event_id, "commence_time": tipoff, "markets": {}}
                games.append(known[event_id])
            known[event_id]["markets"]["player_props"] = event_props

    df, updated = update_closing_columns(df, games, datetime.now(timezone.utc))
    df.to_csv(picks_file, index=False)

    master = history_dir / "todas_las_apuestas.csv"
    if master.exists():
        mdf = pd.read_csv(master)
        date_str = now_local.strftime("%Y-%m-%d")
        mdf = pd.concat([mdf[mdf["date"] != date_str], df], ignore_index=True)
        mdf.to_csv(master, index=False)

    logger.info("Cuota de cierre actualizada en %d apuestas.", updated)
    return updated


if __name__ == "__main__":
    run()
