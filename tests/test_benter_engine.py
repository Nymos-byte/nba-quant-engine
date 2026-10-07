"""
Unit tests for BenterEngine and Market De-vigging.
Verifies margin stripping, ensemble weighting, EV%, and fractional Kelly sizing.
"""

import pytest
from src.data.odds_collector import devig_market
from src.models.benter_engine import BenterEngine, BetEvaluation


def test_devig_market_symmetry_and_normalization():
    """Verify margin removal produces normalized fair probabilities summing strictly to 1.0."""
    # Standard -110 / -110 odds in decimal (1.909 / 1.909)
    odds = [1.9091, 1.9091]
    fair_probs = devig_market(odds)

    assert len(fair_probs) == 2
    assert pytest.approx(fair_probs[0], abs=1e-4) == 0.50
    assert pytest.approx(fair_probs[1], abs=1e-4) == 0.50
    assert pytest.approx(sum(fair_probs), abs=1e-6) == 1.0

    # Asymmetric market
    odds_asym = [1.40, 3.10]
    fair_asym = devig_market(odds_asym)
    assert pytest.approx(sum(fair_asym), abs=1e-6) == 1.0
    # 1/1.4 = 0.7143, 1/3.1 = 0.3226 => Sum = 1.0369
    # Fair 1 = 0.7143 / 1.0369 = 0.6888
    assert pytest.approx(fair_asym[0], abs=1e-3) == 0.6888


def test_benter_linear_ensemble_weighting():
    """Verify Benter ensemble: P_final = 0.35 * P_model + 0.65 * P_market_fair."""
    engine = BenterEngine(weight_model=0.35, weight_market=0.65)
    p_model = 0.60
    p_market_fair = 0.50

    p_final = engine.combine_probabilities(p_model, p_market_fair)
    expected = (0.35 * 0.60) + (0.65 * 0.50)  # 0.21 + 0.325 = 0.535
    assert pytest.approx(p_final, abs=1e-5) == expected


def test_quarter_kelly_calculation():
    """
    Verify Quarter-Kelly formula:
        b = odds - 1
        q = 1 - p
        full_k = (b * p - q) / b
        q_k = 0.25 * full_k
    """
    engine = BenterEngine(kelly_fraction=0.25)
    odds = 2.0  # even money: b = 1.0
    p_final = 0.55  # 55% win rate, q = 0.45
    # full_k = (1.0 * 0.55 - 0.45) / 1.0 = 0.10 (10%)
    # q_k = 0.25 * 0.10 = 0.025 (2.5%)

    full_k, q_k = engine.calculate_kelly(p_final, odds)
    assert pytest.approx(full_k, abs=1e-4) == 0.10
    assert pytest.approx(q_k, abs=1e-4) == 0.025


def test_edge_and_ev_calculation():
    """Verify Edge and EV% calculations in BetEvaluation."""
    engine = BenterEngine()
    eval_result = engine.evaluate_bet(
        game_id="game_test",
        market="moneyline",
        selection="Team A",
        decimal_odds=2.0,
        p_model=0.60,
        p_market_fair=0.52,
    )

    # p_final = 0.35 * 0.60 + 0.65 * 0.52 = 0.21 + 0.338 = 0.548
    assert pytest.approx(eval_result.final_prob, abs=1e-3) == 0.548
    # market_implied = 1 / 2.0 = 0.50
    # edge = 0.548 - 0.50 = 0.048 (4.8%)
    assert pytest.approx(eval_result.edge, abs=1e-3) == 0.048
    # EV% = (0.548 * 2.0 - 1) * 100 = 9.6%
    assert pytest.approx(eval_result.ev_percent, abs=1e-1) == 9.6
