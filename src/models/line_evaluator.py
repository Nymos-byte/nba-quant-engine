"""
Alternative Line Evaluator for Draftea / DFS Player Props.
Computes P(stat >= alt_line) and determines whether moving a line offers
positive expected value (+EV) or represents a mathematical trap.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from src.models.player_props import PlayerPropsModel, PropDistribution

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LineEvaluationResult:
    """Evaluation output for alternative line vs standard line."""
    player_name: str
    category: str
    standard_line: float
    standard_multiplier: float
    standard_prob: float
    standard_ev_percent: float

    alt_line: float
    alt_multiplier: float
    alt_prob: float
    alt_ev_percent: float

    fair_alt_multiplier: float
    verdict: str  # "+EV VALUE" or "TRAMPA MATEMÁTICA / TRAP"
    explanation: str


class LineEvaluator:
    """Evaluates whether alternative prop lines on Draftea offer edge or destroy equity."""

    def __init__(self, props_model: Optional[PlayerPropsModel] = None) -> None:
        self.props_model = props_model or PlayerPropsModel()

    def evaluate_alternative_line(
        self,
        distribution: PropDistribution,
        standard_line: float,
        standard_multiplier: float,
        alt_line: float,
        alt_multiplier: float,
    ) -> LineEvaluationResult:
        """
        Calculates exact cumulative probability P(stat >= alt_line),
        compares multiplier drop against probability gain, and delivers verdict.
        """
        # Exact probabilities
        std_prob = self.props_model.calculate_over_prob(distribution, standard_line)
        alt_prob = self.props_model.calculate_over_prob(distribution, alt_line)

        std_ev = ((std_prob * standard_multiplier) - 1.0) * 100.0
        alt_ev = ((alt_prob * alt_multiplier) - 1.0) * 100.0

        fair_alt_multiplier = round(1.0 / alt_prob, 3) if alt_prob > 0 else 999.0

        # Mathematical Trap Logic:
        # A line move is a trap if:
        # 1. alt_ev < 0.0 (negative expected return)
        # OR
        # 2. alt_ev is substantially worse than std_ev (the house penalty outstrips the win rate gain)
        if alt_ev < 0.0 or (alt_ev < std_ev - 3.0):
            verdict = "TRAMPA MATEMÁTICA / TRAP"
            penalty = round(std_ev - alt_ev, 1)
            explanation = (
                f"Mover la línea de {standard_line} a {alt_line} sube la probabilidad "
                f"de {std_prob*100:.1f}% a {alt_prob*100:.1f}%, pero el multiplicador baja de "
                f"{standard_multiplier:.2f}x a {alt_multiplier:.2f}x. "
                f"El EV cae a {alt_ev:.2f}% (castigo matemático de {penalty}% EV). "
                f"Multiplicador justo requerido: {fair_alt_multiplier:.2f}x."
            )
        else:
            verdict = "+EV VALUE"
            explanation = (
                f"Línea alternativa atractiva: {alt_line} con prob {alt_prob*100:.1f}% "
                f"y multiplicador {alt_multiplier:.2f}x rinde un EV de +{alt_ev:.2f}%. "
                f"Multiplicador justo: {fair_alt_multiplier:.2f}x."
            )

        return LineEvaluationResult(
            player_name=distribution.player_name,
            category=distribution.category,
            standard_line=standard_line,
            standard_multiplier=standard_multiplier,
            standard_prob=round(std_prob, 4),
            standard_ev_percent=round(std_ev, 2),
            alt_line=alt_line,
            alt_multiplier=alt_multiplier,
            alt_prob=round(alt_prob, 4),
            alt_ev_percent=round(alt_ev, 2),
            fair_alt_multiplier=fair_alt_multiplier,
            verdict=verdict,
            explanation=explanation,
        )
