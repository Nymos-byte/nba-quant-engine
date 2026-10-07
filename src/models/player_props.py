"""
Player Props Statistical Modeling Engine.
Models player distributions (Normal for points, Poisson/NegBinomial for AST, REB, 3PM)
conditioned on projected game pace.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Dict, Literal, Optional

import numpy as np
from scipy import stats

logger = logging.getLogger(__name__)

PropCategory = Literal["pts", "ast", "reb", "fg3m"]


@dataclass(frozen=True)
class PropDistribution:
    """Statistical distribution attributes of a player prop."""
    player_name: str
    team_abbreviation: str
    category: PropCategory
    baseline_stat: float
    pace_factor: float
    adjusted_mean: float
    std_dev: float
    distribution_type: str


class PlayerPropsModel:
    """Calculates conditional probability distributions for individual player props."""

    def __init__(self, league_average_pace: float = 99.5) -> None:
        self.league_average_pace = league_average_pace

    def get_pace_factor(self, team_pace: float, expected_game_pace: float) -> float:
        """Computes ratio of projected game pace to team baseline pace."""
        if team_pace <= 0:
            return 1.0
        return float(expected_game_pace / team_pace)

    def fit_distribution(
        self,
        player_name: str,
        team_abbreviation: str,
        category: PropCategory,
        baseline_stat: float,
        team_pace: float,
        expected_game_pace: float,
        usage_multiplier: float = 1.0,
    ) -> PropDistribution:
        """
        Adjusts baseline player stat by game pace and usage rate,
        and determines appropriate distribution.
        """
        pace_factor = self.get_pace_factor(team_pace, expected_game_pace)
        adj_mean = baseline_stat * pace_factor * usage_multiplier

        if category == "pts":
            dist_type = "normal"
            # Empirical NBA variance for points: ~28-32% CV (coefficient of variation)
            std_dev = max(3.5, adj_mean * 0.28)
        elif category in ("ast", "reb"):
            dist_type = "poisson"
            std_dev = float(np.sqrt(max(0.1, adj_mean)))
        elif category == "fg3m":
            dist_type = "poisson"
            std_dev = float(np.sqrt(max(0.1, adj_mean)))
        else:
            dist_type = "normal"
            std_dev = max(1.0, adj_mean * 0.3)

        return PropDistribution(
            player_name=player_name,
            team_abbreviation=team_abbreviation,
            category=category,
            baseline_stat=round(baseline_stat, 2),
            pace_factor=round(pace_factor, 4),
            adjusted_mean=round(adj_mean, 2),
            std_dev=round(std_dev, 2),
            distribution_type=dist_type,
        )

    def calculate_over_prob(self, dist: PropDistribution, line: float) -> float:
        """
        Calculates P(stat > line) using continuous Normal or discrete Poisson CDF.
        """
        if dist.distribution_type == "normal":
            # Survival function: P(X > line)
            prob = stats.norm.sf(line, loc=dist.adjusted_mean, scale=dist.std_dev)
            return float(np.clip(prob, 0.0001, 0.9999))
        else:
            # Discrete Poisson: P(X > line) = 1 - CDF(floor(line))
            k = int(np.floor(line))
            prob = stats.poisson.sf(k, mu=dist.adjusted_mean)
            return float(np.clip(prob, 0.0001, 0.9999))

    def calculate_under_prob(self, dist: PropDistribution, line: float) -> float:
        """Calculates P(stat < line)."""
        return float(1.0 - self.calculate_over_prob(dist, line))
