"""
Unit tests for RatingEngine: analytical pace calculations, projections, and Monte Carlo.
"""

import pytest
import numpy as np
from src.models.rating_engine import RatingEngine, SimulationResult


def test_analytical_pace_calculation():
    """Verify Pace_exp = (Pace_Home * Pace_Away) / Pace_League."""
    engine = RatingEngine()
    home_pace = 102.0
    away_pace = 98.0
    league_pace = 100.0

    exp_pace = engine.calculate_expected_pace(home_pace, away_pace, league_pace)
    expected = (102.0 * 98.0) / 100.0  # 99.96
    assert pytest.approx(exp_pace, rel=1e-4) == expected


def test_project_team_points_with_home_court_advantage():
    """Verify point projections adjust for ratings and incorporate exact 2.8 HCA."""
    hca = 2.8
    engine = RatingEngine(home_court_advantage=hca)

    # Identical ratings to isolate HCA impact
    home_ortg = 114.0
    home_drtg = 114.0
    away_ortg = 114.0
    away_drtg = 114.0
    league_ortg = 114.0
    expected_pace = 100.0

    home_pts, away_pts = engine.project_team_points(
        home_ortg, home_drtg, away_ortg, away_drtg, league_ortg, expected_pace
    )

    # Base points should be 114.0 for both.
    # Home gets +1.4, away gets -1.4. Net difference = 2.8 points.
    assert pytest.approx(home_pts, abs=1e-3) == 115.4
    assert pytest.approx(away_pts, abs=1e-3) == 112.6
    assert pytest.approx(home_pts - away_pts, abs=1e-3) == hca


def test_monte_carlo_simulation_probabilities():
    """Verify Monte Carlo outputs valid probabilities that sum to 1.0."""
    engine = RatingEngine(mc_simulations=10_000, random_seed=42)
    home_pts = 115.0
    away_pts = 110.0

    res = engine.run_monte_carlo(
        home_pts=home_pts,
        away_pts=away_pts,
        spread_lines=[-5.0, 5.0],
        total_lines=[225.0],
    )

    assert isinstance(res, SimulationResult)
    assert 0.0 <= res.home_win_prob <= 1.0
    assert 0.0 <= res.away_win_prob <= 1.0
    assert pytest.approx(res.home_win_prob + res.away_win_prob, abs=1e-3) == 1.0

    # Home expected points > Away expected points -> home_win_prob must be > 0.50
    assert res.home_win_prob > 0.55

    # Check spreads and totals
    assert -5.0 in res.spread_cover_probs
    assert 225.0 in res.total_over_probs
    assert 0.0 <= res.spread_cover_probs[-5.0] <= 1.0
    assert 0.0 <= res.total_over_probs[225.0] <= 1.0
