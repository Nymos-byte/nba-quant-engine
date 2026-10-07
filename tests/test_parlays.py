"""
Unit tests for ParlayBuilder and LineEvaluator.
Verifies correlated combinations, target odds [8.0, 25.0], and Draftea alternative line trap detection.
"""

import pytest
from src.models.player_props import PlayerPropsModel
from src.models.line_evaluator import LineEvaluator
from src.models.parlay_builder import ParlayBuilder, ParlayLeg


def test_line_evaluator_trap_detection():
    """
    Verify that moving an alternative line with disproportionate multiplier reduction
    is identified as a 'TRAMPA MATEMÁTICA / TRAP'.
    """
    props_model = PlayerPropsModel()
    evaluator = LineEvaluator(props_model)

    dist = props_model.fit_distribution(
        player_name="Luka Doncic",
        team_abbreviation="DAL",
        category="pts",
        baseline_stat=32.0,
        team_pace=100.0,
        expected_game_pace=100.0,
    )

    # Standard line: 31.5 @ 1.85
    # Alternative line lower: 25.5, but offered multiplier slashed to 1.15
    res = evaluator.evaluate_alternative_line(
        distribution=dist,
        standard_line=31.5,
        standard_multiplier=1.85,
        alt_line=25.5,
        alt_multiplier=1.15,
    )

    assert "TRAP" in res.verdict.upper() or "TRAMPA" in res.verdict.upper()
    assert res.alt_ev_percent < 0.0 or res.alt_ev_percent < res.standard_ev_percent


def test_line_evaluator_positive_value():
    """
    Verify that when an alternative line offers positive EV and fair multiplier,
    it is labeled as '+EV VALUE'.
    """
    props_model = PlayerPropsModel()
    evaluator = LineEvaluator(props_model)

    dist = props_model.fit_distribution(
        player_name="Nikola Jokic",
        team_abbreviation="DEN",
        category="ast",
        baseline_stat=9.8,
        team_pace=99.0,
        expected_game_pace=103.0,  # Fast game
    )

    # Line is 8.5, model expects ~10.2 ast. Multiplier 2.10 is very generous (+EV)
    res = evaluator.evaluate_alternative_line(
        distribution=dist,
        standard_line=9.5,
        standard_multiplier=1.85,
        alt_line=8.5,
        alt_multiplier=1.80,
    )

    assert "+EV VALUE" in res.verdict
    assert res.alt_ev_percent > 0.0


def test_parlay_builder_odds_boundaries():
    """Verify generated parlays fall strictly within [8.0, 25.0] combined odds."""
    builder = ParlayBuilder(min_odds=8.0, max_odds=25.0, fixed_stake_mxn=20.0)

    candidate_legs = [
        ParlayLeg("Luka Doncic", "DAL", "BOS", "ast", 8.5, "Over", 1.85, 0.58, "STACK_PASS_SCORER"),
        ParlayLeg("Kyrie Irving", "DAL", "BOS", "pts", 24.5, "Over", 1.88, 0.56, "STACK_PASS_SCORER"),
        ParlayLeg("Jayson Tatum", "BOS", "DAL", "pts", 26.5, "Over", 1.85, 0.55, "PACE_BOOST"),
        ParlayLeg("Nikola Jokic", "DEN", "OKC", "ast", 9.5, "Over", 1.80, 0.60, "PACE_BOOST"),
        ParlayLeg("Shai Gilgeous-Alexander", "OKC", "DEN", "pts", 30.5, "Over", 1.85, 0.55, "PACE_BOOST"),
    ]

    tickets = builder.build_correlated_parlays(candidate_legs, spent_this_week_mxn=0.0)

    assert len(tickets) > 0
    for t in tickets:
        assert 8.0 <= t.combined_odds <= 25.0
        assert 3 <= len(t.legs) <= 4
        assert t.stake_mxn == 20.0
        assert t.is_valid is True


def test_parlay_builder_correlation_preference():
    """Verify game script stacking (passer AST + teammate PTS) boosts correlation score."""
    builder = ParlayBuilder()

    correlated_legs = [
        ParlayLeg("Luka Doncic", "DAL", "BOS", "ast", 8.5, "Over", 1.85, 0.58, "STACK_PASS_SCORER"),
        ParlayLeg("Kyrie Irving", "DAL", "BOS", "pts", 24.5, "Over", 1.88, 0.56, "STACK_PASS_SCORER"),
        ParlayLeg("Jayson Tatum", "BOS", "DAL", "pts", 26.5, "Over", 1.85, 0.55, "PACE_BOOST"),
    ]

    score, rationale = builder.assess_correlation_score(correlated_legs)
    assert score > 0
    assert "Game Script Stacking DAL" in rationale
