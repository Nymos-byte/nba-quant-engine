"""
Correlated Parlay Generator for Recreational High-Odds Tickets (Draftea / DFS).
Enforces positive correlation stacking (Game Script, Pace, Usage)
and validates combined odds >= 8.0 without upper ceiling (unlimited dream odds).
"""

from __future__ import annotations

import itertools
import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import config

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ParlayLeg:
    """Individual proposition in a parlay."""
    player_name: str
    team_abbreviation: str
    opponent: str
    category: str
    line: float
    direction: str  # 'Over' or 'Under'
    odds: float  # Decimal odds (e.g. 1.85)
    prob: float
    correlation_tag: str  # e.g. 'STACK_PASS_SCORER', 'PACE_BOOST'


@dataclass(frozen=True)
class ParlayTicket:
    """Combined ticket with correlated legs and payout details."""
    ticket_id: str
    legs: List[ParlayLeg]
    combined_odds: float
    stake_mxn: float
    potential_payout_mxn: float
    correlation_rationale: str
    is_valid: bool
    rejection_reason: Optional[str] = None


class ParlayBuilder:
    """Builds and validates correlated 3 to 4 leg parlay combinations."""

    def __init__(
        self,
        min_odds: float = config.TARGET_PARLAY_MIN_ODDS,
        max_odds: float = config.TARGET_PARLAY_MAX_ODDS,
        fixed_stake_mxn: float = config.PARLAY_FIXED_STAKE,
        weekly_budget_mxn: float = config.WEEKLY_FUN_BUDGET,
        min_leg_prob: float = config.PARLAY_MIN_LEG_PROB,
    ) -> None:
        self.min_odds = min_odds
        self.max_odds = max_odds
        self.fixed_stake_mxn = fixed_stake_mxn
        self.weekly_budget_mxn = weekly_budget_mxn
        self.min_leg_prob = min_leg_prob

    def calculate_combined_odds(self, legs: List[ParlayLeg]) -> float:
        """Multiplies decimal odds of independent/correlated legs."""
        total = 1.0
        for leg in legs:
            total *= leg.odds
        return round(total, 2)

    def assess_correlation_score(self, legs: List[ParlayLeg]) -> Tuple[float, str]:
        """
        Assesses synergy across legs:
        - Stacking Assist Over + Teammate Points Over gives strong positive correlation.
        - Multiple Overs in high pace games gives positive correlation.
        - Negative or conflicting legs (e.g., opposing team unders/overs or 2 players from same team fighting for same rebounds) are penalized.
        """
        score = 0.0
        reasons = []

        # Check for teammate assist + points stacking
        teams = {}
        for leg in legs:
            teams.setdefault(leg.team_abbreviation, []).append(leg)

        for tm, tm_legs in teams.items():
            has_ast_over = any(l.category == "ast" and l.direction == "Over" for l in tm_legs)
            has_pts_over = any(l.category in ("pts", "fg3m") and l.direction == "Over" for l in tm_legs)
            if has_ast_over and has_pts_over:
                score += 3.0
                reasons.append(f"Game Script Stacking {tm}: Generador de juego (AST) + Finalizador (PTS)")

            if len(tm_legs) > 2:
                # Too many legs on one team dilutes equity
                score -= 1.0

        # High-pace correlation
        pace_legs = [l for l in legs if "PACE" in l.correlation_tag]
        if len(pace_legs) >= 2:
            score += 2.0
            reasons.append("Pace Stacking: Múltiples selecciones impulsadas por ritmo acelerado")

        rationale = " | ".join(reasons) if reasons else "Props complementarios de valor esperado positivo"
        return score, rationale

    def build_correlated_parlays(
        self,
        candidate_legs: List[ParlayLeg],
        spent_this_week_mxn: float = 0.0,
    ) -> List[ParlayTicket]:
        """
        Generates candidate parlays of 3 to 4 legs and retains those
        satisfying the minimum odds threshold (>= 8.0, uncapped).
        """
        if (spent_this_week_mxn + self.fixed_stake_mxn) > self.weekly_budget_mxn:
            logger.warning(
                "Weekly fun budget limit reached ($%.2f / $%.2f MXN). No new parlay recommended.",
                spent_this_week_mxn, self.weekly_budget_mxn
            )
            return []

        # Filter strictly: only legs with individual projected probability >= min_leg_prob (75%)
        eligible_legs = [l for l in candidate_legs if l.prob >= self.min_leg_prob]
        if len(eligible_legs) < 3:
            logger.info("Not enough candidate legs with P >= %.2f to form a parlay (%d available).", self.min_leg_prob, len(eligible_legs))
            return []

        valid_tickets: List[ParlayTicket] = []
        ticket_counter = 1

        # Search combinations of 3 and 4 legs
        for leg_count in (3, 4):
            for combo in itertools.combinations(eligible_legs, leg_count):
                legs = list(combo)

                # Ensure player deduplication: at most 1 prop per player
                player_names = [l.player_name for l in legs]
                if len(player_names) != len(set(player_names)):
                    continue

                combined_odds = self.calculate_combined_odds(legs)

                # Odds boundary check: strictly >= min_odds (without upper ceiling)
                if self.min_odds <= combined_odds <= self.max_odds:
                    score, rationale = self.assess_correlation_score(legs)
                    # We prefer combinations with positive synergy (score > 0)
                    ticket = ParlayTicket(
                        ticket_id=f"PARLAY_{leg_count}LEG_{ticket_counter:03d}",
                        legs=legs,
                        combined_odds=combined_odds,
                        stake_mxn=self.fixed_stake_mxn,
                        potential_payout_mxn=round(self.fixed_stake_mxn * combined_odds, 2),
                        correlation_rationale=rationale,
                        is_valid=True,
                    )
                    valid_tickets.append((score, ticket))
                    ticket_counter += 1

        # Sort by correlation score descending, then by highest combined odds descending
        valid_tickets.sort(key=lambda item: (item[0], item[1].combined_odds), reverse=True)
        return [item[1] for item in valid_tickets]

    def select_best_daily_ticket(
        self,
        candidate_legs: List[ParlayLeg],
        spent_this_week_mxn: float = 0.0,
    ) -> Optional[ParlayTicket]:
        """Selects the single top-ranked correlated ticket for the daily action."""
        tickets = self.build_correlated_parlays(candidate_legs, spent_this_week_mxn)
        return tickets[0] if tickets else None


Tuple = tuple
