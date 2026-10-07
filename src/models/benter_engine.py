"""
Bill Benter Hybrid Ensemble and Fractional Kelly Sizing Engine.
Blends statistical model projections with sharp de-vigged market lines
and enforces institutional risk invariants.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import config

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BetEvaluation:
    """Outcome of Benter ensemble evaluation and Kelly risk gating."""
    game_id: str
    market: str  # 'moneyline', 'spread', 'total'
    selection: str  # e.g., 'Boston Celtics', 'Over 226.5'
    odds: float
    model_prob: float
    market_fair_prob: float
    final_prob: float
    market_implied_prob: float
    edge: float
    ev_percent: float
    raw_kelly: float
    quarter_kelly: float
    recommended_stake_fraction: float  # bounded between 0.0100 and 0.0175, or 0.0
    recommended_stake_amount: float
    is_approved: bool
    rejection_reason: Optional[str] = None


class BenterEngine:
    """Ensemble weighting and Kelly criterion position sizer."""

    def __init__(
        self,
        weight_model: float = config.BENTER_WEIGHT_MODEL,
        weight_market: float = config.BENTER_WEIGHT_MARKET,
        kelly_fraction: float = config.KELLY_FRACTION,
        min_kelly_stake: float = config.MIN_KELLY_STAKE,
        max_kelly_stake: float = config.MAX_KELLY_STAKE,
        min_edge: float = config.MIN_EDGE,
        min_ev_percent: float = config.MIN_EV_PERCENT,
        bankroll: float = config.BANKROLL_CORE,
    ) -> None:
        if not abs((weight_model + weight_market) - 1.0) < 1e-5:
            raise ValueError(f"Ensemble weights must sum to 1.0, got: {weight_model + weight_market}")
        self.weight_model = weight_model
        self.weight_market = weight_market
        self.kelly_fraction = kelly_fraction
        self.min_kelly_stake = min_kelly_stake
        self.max_kelly_stake = max_kelly_stake
        self.min_edge = min_edge
        self.min_ev_percent = min_ev_percent
        self.bankroll = bankroll

    def combine_probabilities(self, p_model: float, p_market_fair: float) -> float:
        """
        Bill Benter linear combination:
            P_final = (w_model * P_model) + (w_market * P_market_fair)
        """
        p_final = (self.weight_model * p_model) + (self.weight_market * p_market_fair)
        return float(np_clip_prob(p_final))

    def calculate_kelly(self, p_final: float, decimal_odds: float) -> TupleFloat2:
        """
        Full Kelly and Quarter-Kelly calculation:
            b = decimal_odds - 1.0
            q = 1.0 - p_final
            full_kelly = (b * p_final - q) / b
            quarter_kelly = full_kelly * kelly_fraction
        """
        if decimal_odds <= 1.0:
            return 0.0, 0.0
        b = decimal_odds - 1.0
        q = 1.0 - p_final
        full_kelly = (b * p_final - q) / b
        if full_kelly <= 0:
            return 0.0, 0.0
        quarter_kelly = full_kelly * self.kelly_fraction
        return float(full_kelly), float(quarter_kelly)

    def evaluate_bet(
        self,
        game_id: str,
        market: str,
        selection: str,
        decimal_odds: float,
        p_model: float,
        p_market_fair: float,
    ) -> BetEvaluation:
        """
        Evaluates bet opportunity through Benter ensemble, computes edge, EV%,
        and applies strict Quarter-Kelly institutional gating.
        """
        if decimal_odds <= 1.0:
            raise ValueError(f"Decimal odds must be > 1.0, got {decimal_odds}")

        p_final = self.combine_probabilities(p_model, p_market_fair)
        market_implied = 1.0 / decimal_odds
        edge = p_final - market_implied
        ev_pct = ((p_final * decimal_odds) - 1.0) * 100.0

        full_k, q_k = self.calculate_kelly(p_final, decimal_odds)

        # Institutional Risk Gates
        is_approved = True
        rejection_reason = None
        final_fraction = 0.0

        if edge < self.min_edge:
            is_approved = False
            rejection_reason = f"Edge {edge:.4f} below minimum threshold {self.min_edge:.4f}"
        elif ev_pct < self.min_ev_percent:
            is_approved = False
            rejection_reason = f"EV {ev_pct:.2f}% below minimum threshold {self.min_ev_percent:.2f}%"
        elif q_k < self.min_kelly_stake:
            # Floor rule: if quarter-kelly stake is under 1.0%, discard (0.0)
            is_approved = False
            rejection_reason = f"Quarter-Kelly stake {q_k:.4f} below floor {self.min_kelly_stake:.4f} (1.00%)"
        else:
            # Ceiling rule: truncate to 1.75% if exceeding max
            if q_k > self.max_kelly_stake:
                final_fraction = self.max_kelly_stake
            else:
                final_fraction = q_k

        stake_amount = round(final_fraction * self.bankroll, 2)

        return BetEvaluation(
            game_id=game_id,
            market=market,
            selection=selection,
            odds=decimal_odds,
            model_prob=round(p_model, 4),
            market_fair_prob=round(p_market_fair, 4),
            final_prob=round(p_final, 4),
            market_implied_prob=round(market_implied, 4),
            edge=round(edge, 4),
            ev_percent=round(ev_pct, 2),
            raw_kelly=round(full_k, 4),
            quarter_kelly=round(q_k, 4),
            recommended_stake_fraction=round(final_fraction, 4),
            recommended_stake_amount=stake_amount,
            is_approved=is_approved,
            rejection_reason=rejection_reason,
        )


def np_clip_prob(p: float) -> float:
    return max(0.0, min(1.0, float(p)))


TupleFloat2 = tuple[float, float]
