"""
Institutional Risk Manager: Deduplication and Daily Exposure Rescaling.
Enforces hard single-game constraints and bankroll safety ceilings.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from typing import Dict, List

import config
from src.models.benter_engine import BetEvaluation

logger = logging.getLogger(__name__)


def deduplicate_picks_by_game(picks: List[BetEvaluation]) -> List[BetEvaluation]:
    """
    Strict deduplication: exactly 1 bet per game_id.
    Retains the candidate bet with the highest Expected Value (EV%).
    """
    approved_picks = [p for p in picks if p.is_approved]
    grouped: Dict[str, List[BetEvaluation]] = {}

    for pick in approved_picks:
        grouped.setdefault(pick.game_id, []).append(pick)

    deduped: List[BetEvaluation] = []
    for game_id, game_picks in grouped.items():
        # Sort descending by ev_percent
        best_pick = max(game_picks, key=lambda p: p.ev_percent)
        deduped.append(best_pick)

    # Sort remaining picks by EV% descending
    deduped.sort(key=lambda p: p.ev_percent, reverse=True)
    return deduped


def rescale_daily_exposure(
    picks: List[BetEvaluation],
    max_daily_exposure: float = config.MAX_DAILY_EXPOSURE,
    bankroll: float = config.BANKROLL_CORE,
) -> List[BetEvaluation]:
    """
    Daily Exposure Normalization:
    Sums stakes of all approved picks. If the sum exceeds max_daily_exposure (10.0%),
    applies a proportional scaling factor S = max_daily_exposure / sum(stakes)
    so cumulative exposure is strictly <= max_daily_exposure.
    """
    if not picks:
        return []

    total_stake = sum(p.recommended_stake_fraction for p in picks)
    if total_stake <= max_daily_exposure:
        return picks

    scaling_factor = max_daily_exposure / total_stake
    logger.info(
        "Total daily stake exposure (%.4f) exceeds ceiling (%.4f). Applying scaling factor S=%.4f",
        total_stake, max_daily_exposure, scaling_factor
    )

    rescaled_picks: List[BetEvaluation] = []
    for p in picks:
        new_fraction = round(p.recommended_stake_fraction * scaling_factor, 6)
        new_amount = round(new_fraction * bankroll, 2)
        rescaled = replace(
            p,
            recommended_stake_fraction=new_fraction,
            recommended_stake_amount=new_amount,
        )
        rescaled_picks.append(rescaled)

    return rescaled_picks
