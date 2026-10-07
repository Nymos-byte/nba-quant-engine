"""
Rating Engine for Fundamental Projections and Monte Carlo Simulations.
Implements pace adjustments, offensive/defensive ratings, home court advantage,
and 10,000-iteration bivariate score simulations.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

import numpy as np

import config

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SimulationResult:
    """Encapsulates Monte Carlo simulation outputs."""
    home_expected_points: float
    away_expected_points: float
    expected_pace: float
    expected_total: float
    expected_spread: float  # home - away
    home_win_prob: float
    away_win_prob: float
    spread_cover_probs: Dict[float, float]  # line -> prob home covers line
    total_over_probs: Dict[float, float]  # line -> prob total > line


class RatingEngine:
    """Calculates fundamental NBA ratings and runs Monte Carlo match simulations."""

    def __init__(
        self,
        home_court_advantage: float = config.HOME_COURT_ADVANTAGE,
        mc_simulations: int = config.MC_SIMULATIONS,
        dispersion: float = config.MC_POINT_DISPERSION,
        random_seed: Optional[int] = 42,
    ) -> None:
        self.home_court_advantage: float = home_court_advantage
        self.mc_simulations: int = mc_simulations
        self.dispersion: float = dispersion
        self.random_seed: Optional[int] = random_seed

    def calculate_expected_pace(
        self,
        home_pace: float,
        away_pace: float,
        league_pace: float,
    ) -> float:
        """
        Analytical Expected Pace formula:
            Pace_exp = (Pace_Home * Pace_Away) / Pace_League
        """
        if league_pace <= 0:
            raise ValueError(f"League pace must be positive, got: {league_pace}")
        return float((home_pace * away_pace) / league_pace)

    def project_team_points(
        self,
        home_ortg: float,
        home_drtg: float,
        away_ortg: float,
        away_drtg: float,
        league_ortg: float,
        expected_pace: float,
    ) -> Tuple[float, float]:
        """
        Calculates expected points per team based on opponent-adjusted efficiency
        and adds net HOME_COURT_ADVANTAGE to the host team.
        """
        if league_ortg <= 0:
            league_ortg = 114.5

        # Adjusted Offensive Ratings (points per 100 possessions)
        adj_home_ortg = (home_ortg * away_drtg) / league_ortg
        adj_away_ortg = (away_ortg * home_drtg) / league_ortg

        # Pace scaled points
        pace_factor = expected_pace / 100.0
        base_home_pts = adj_home_ortg * pace_factor
        base_away_pts = adj_away_ortg * pace_factor

        # Allocate Home Court Advantage: +HCA/2 to home, -HCA/2 to away
        half_hca = self.home_court_advantage / 2.0
        home_pts = base_home_pts + half_hca
        away_pts = base_away_pts - half_hca

        return float(home_pts), float(away_pts)

    def run_monte_carlo(
        self,
        home_pts: float,
        away_pts: float,
        spread_lines: Optional[list[float]] = None,
        total_lines: Optional[list[float]] = None,
    ) -> SimulationResult:
        """
        Runs Monte Carlo simulation (10,000 iterations) with bivariate normal
        dispersion (sigma ~ 10.5).
        """
        if self.random_seed is not None:
            rng = np.random.default_rng(self.random_seed)
        else:
            rng = np.random.default_rng()

        # Simulate independent score variations with weak correlation (rho ~ 0.12)
        rho = 0.12
        cov = np.array([
            [self.dispersion ** 2, rho * (self.dispersion ** 2)],
            [rho * (self.dispersion ** 2), self.dispersion ** 2],
        ])
        mean = [home_pts, away_pts]

        sim_scores = rng.multivariate_normal(mean, cov, size=self.mc_simulations)
        home_sim = sim_scores[:, 0]
        away_sim = sim_scores[:, 1]

        # Break ties with slight home advantage overtime probability
        diff = home_sim - away_sim
        home_wins = np.count_nonzero(diff > 0)
        ties = np.count_nonzero(diff == 0)
        home_win_prob = (home_wins + 0.5 * ties) / self.mc_simulations
        away_win_prob = 1.0 - home_win_prob

        # Spread cover probabilities
        # Spread is defined from home perspective: e.g. -5.5 means Home - 5.5 > Away
        spread_cover_probs: Dict[float, float] = {}
        if spread_lines:
            for line in spread_lines:
                # Home covers if (home_sim + line) > away_sim
                # E.g. if line = -5.5: home_sim - 5.5 > away_sim => diff > 5.5
                cover_count = np.count_nonzero((home_sim + line) > away_sim)
                spread_cover_probs[line] = float(cover_count / self.mc_simulations)

        # Totals over probabilities
        total_sim = home_sim + away_sim
        total_over_probs: Dict[float, float] = {}
        if total_lines:
            for t_line in total_lines:
                over_count = np.count_nonzero(total_sim > t_line)
                total_over_probs[t_line] = float(over_count / self.mc_simulations)

        return SimulationResult(
            home_expected_points=round(float(home_pts), 2),
            away_expected_points=round(float(away_pts), 2),
            expected_pace=round(float((home_pts + away_pts) / 2.2), 2),
            expected_total=round(float(home_pts + away_pts), 2),
            expected_spread=round(float(home_pts - away_pts), 2),
            home_win_prob=round(float(home_win_prob), 4),
            away_win_prob=round(float(away_win_prob), 4),
            spread_cover_probs={k: round(v, 4) for k, v in spread_cover_probs.items()},
            total_over_probs={k: round(v, 4) for k, v in total_over_probs.items()},
        )

    def analyze_game(
        self,
        home_stats: Dict[str, Any],
        away_stats: Dict[str, Any],
        league_averages: Dict[str, float],
        spread_line: Optional[float] = None,
        total_line: Optional[float] = None,
    ) -> SimulationResult:
        """End-to-end game analysis wrapper."""
        home_pace = float(home_stats.get("pace", 100.0))
        away_pace = float(away_stats.get("pace", 100.0))
        league_pace = float(league_averages.get("pace", 99.5))
        league_ortg = float(league_averages.get("ortg", 114.5))

        exp_pace = self.calculate_expected_pace(home_pace, away_pace, league_pace)
        home_pts, away_pts = self.project_team_points(
            home_ortg=float(home_stats.get("ortg", 114.0)),
            home_drtg=float(home_stats.get("drtg", 114.0)),
            away_ortg=float(away_stats.get("ortg", 114.0)),
            away_drtg=float(away_stats.get("drtg", 114.0)),
            league_ortg=league_ortg,
            expected_pace=exp_pace,
        )

        spread_lines = [spread_line] if spread_line is not None else []
        total_lines = [total_line] if total_line is not None else []

        res = self.run_monte_carlo(
            home_pts=home_pts,
            away_pts=away_pts,
            spread_lines=spread_lines,
            total_lines=total_lines,
        )
        return res


def calculate_game_metrics(
    team_stats: Dict[str, Any],
    opponent_stats: Dict[str, Any],
    game_minutes: float = 48.0,
) -> Dict[str, float]:
    """
    Computes Dean Oliver's Four Factors and fundamental team game metrics from raw stats:
    - Possessions: FGA + 0.44 * FTA - OREB + TO
    - Pace: (Possessions / Game Minutes) * 48.0
    - Offensive Rating (ORtg): 100 * (Points / Possessions)
    - Defensive Rating (DRtg): 100 * (Opponent Points / Opponent Possessions)
    - Effective Field Goal % (eFG%): (FGM + 0.5 * 3PM) / FGA
    - Turnover % (TOV%): TO / (FGA + 0.44 * FTA + TO)
    - Offensive Rebound % (ORB%): OREB / (OREB + Opponent DREB)
    - Free Throw Rate: FTA / FGA
    """
    fga = float(team_stats.get("fga", 0.0))
    fta = float(team_stats.get("fta", 0.0))
    oreb = float(team_stats.get("oreb", 0.0))
    to = float(team_stats.get("to", 0.0))
    pts = float(team_stats.get("pts", 0.0))
    fgm = float(team_stats.get("fgm", 0.0))
    fg3m = float(team_stats.get("fg3m", 0.0))

    opp_fga = float(opponent_stats.get("fga", 0.0))
    opp_fta = float(opponent_stats.get("fta", 0.0))
    opp_oreb = float(opponent_stats.get("oreb", 0.0))
    opp_dreb = float(opponent_stats.get("dreb", 0.0))
    opp_to = float(opponent_stats.get("to", 0.0))
    opp_pts = float(opponent_stats.get("pts", 0.0))

    # Dean Oliver Possessions formula
    poss = max(1.0, fga + 0.44 * fta - oreb + to)
    opp_poss = max(1.0, opp_fga + 0.44 * opp_fta - opp_oreb + opp_to)

    # Pace normalized to 48 regulation minutes
    minutes = max(1.0, game_minutes)
    pace = (poss / minutes) * 48.0

    # Ratings per 100 possessions
    ortg = 100.0 * (pts / poss)
    drtg = 100.0 * (opp_pts / opp_poss)

    # Additional Dean Oliver Four Factors
    efg_pct = (fgm + 0.5 * fg3m) / fga if fga > 0 else 0.0
    tov_denom = fga + 0.44 * fta + to
    tov_pct = to / tov_denom if tov_denom > 0 else 0.0
    reb_denom = oreb + opp_dreb
    orb_pct = oreb / reb_denom if reb_denom > 0 else 0.0
    ft_rate = fta / fga if fga > 0 else 0.0

    return {
        "possessions": round(poss, 2),
        "opponent_possessions": round(opp_poss, 2),
        "pace": round(pace, 2),
        "ortg": round(ortg, 2),
        "drtg": round(drtg, 2),
        "pts": pts,
        "opponent_pts": opp_pts,
        "efg_pct": round(efg_pct, 4),
        "tov_pct": round(tov_pct, 4),
        "orb_pct": round(orb_pct, 4),
        "ft_rate": round(ft_rate, 4),
    }

