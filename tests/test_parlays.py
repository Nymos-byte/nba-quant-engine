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
    """Verify generated parlays fall with combined odds >= 8.0 (no upper ceiling) and require P >= 0.75."""
    builder = ParlayBuilder(min_odds=8.0, max_odds=float("inf"), fixed_stake_mxn=20.0, min_leg_prob=0.75)

    # Candidate legs with floor probability >= 75%
    candidate_legs = [
        ParlayLeg("Luka Doncic", "DAL", "BOS", "pts", 24.5, "Over", 1.85, 0.78, "STACK_PASS_SCORER"),
        ParlayLeg("Kyrie Irving", "DAL", "BOS", "ast", 3.5, "Over", 1.88, 0.76, "STACK_PASS_SCORER"),
        ParlayLeg("Jayson Tatum", "BOS", "DAL", "pts", 20.5, "Over", 1.85, 0.75, "PACE_BOOST"),
        ParlayLeg("Nikola Jokic", "DEN", "OKC", "ast", 7.5, "Over", 1.80, 0.82, "PACE_BOOST"),
        ParlayLeg("Jamal Murray", "DEN", "OKC", "pts", 16.5, "Over", 1.88, 0.77, "STACK_PASS_SCORER"),
    ]

    tickets = builder.build_correlated_parlays(candidate_legs, spent_this_week_mxn=0.0)

    assert len(tickets) > 0
    for t in tickets:
        assert t.combined_odds >= 8.0
        assert 3 <= len(t.legs) <= 4
        assert t.stake_mxn == 20.0
        assert t.is_valid is True
        for leg in t.legs:
            assert leg.prob >= 0.75


def test_parlay_builder_accepts_very_high_odds_uncapped():
    """Verify ParlayBuilder does not cap odds at 25.0 and accepts very high combined odds (e.g. > 30.0)."""
    builder = ParlayBuilder(min_odds=8.0, max_odds=float("inf"), fixed_stake_mxn=20.0, min_leg_prob=0.75)

    high_odds_legs = [
        ParlayLeg("Luka Doncic", "DAL", "BOS", "pts", 24.5, "Over", 2.50, 0.76, "STACK_PASS_SCORER"),
        ParlayLeg("Kyrie Irving", "DAL", "BOS", "ast", 3.5, "Over", 2.40, 0.75, "STACK_PASS_SCORER"),
        ParlayLeg("Jayson Tatum", "BOS", "DAL", "pts", 20.5, "Over", 2.50, 0.78, "PACE_BOOST"),
        ParlayLeg("Nikola Jokic", "DEN", "OKC", "ast", 7.5, "Over", 2.30, 0.80, "PACE_BOOST"),
    ]

    tickets = builder.build_correlated_parlays(high_odds_legs)
    assert len(tickets) > 0
    # Combined odds of 4 legs: 2.5 * 2.4 * 2.5 * 2.3 = 34.50
    has_ultra_high = any(t.combined_odds > 25.0 for t in tickets)
    assert has_ultra_high is True


def test_parlay_builder_rejects_sub_75_probability_legs():
    """Verify ParlayBuilder strictly excludes legs where P < 0.75."""
    builder = ParlayBuilder(min_leg_prob=0.75)

    low_prob_legs = [
        ParlayLeg("Luka Doncic", "DAL", "BOS", "pts", 35.5, "Over", 2.20, 0.40, "STACK_PASS_SCORER"),
        ParlayLeg("Kyrie Irving", "DAL", "BOS", "pts", 26.5, "Over", 1.90, 0.52, "STACK_PASS_SCORER"),
        ParlayLeg("Jayson Tatum", "BOS", "DAL", "pts", 28.5, "Over", 1.85, 0.55, "PACE_BOOST"),
    ]

    tickets = builder.build_correlated_parlays(low_prob_legs)
    assert len(tickets) == 0


def test_find_alt_floor_line_meets_75_percent_threshold():
    """Verify find_alt_floor_line returns an alternative line with P >= 75%."""
    props_model = PlayerPropsModel()
    dist = props_model.fit_distribution(
        player_name="Luka Doncic",
        team_abbreviation="DAL",
        category="pts",
        baseline_stat=32.4,
        team_pace=100.0,
        expected_game_pace=100.0,
    )

    floor_line, exact_prob, multiplier = props_model.find_alt_floor_line(dist, min_prob=0.75)
    assert exact_prob >= 0.75
    assert floor_line < dist.baseline_stat
    assert multiplier >= 1.70


def test_parlay_builder_correlation_preference():
    """Verify game script stacking (passer AST + teammate PTS) boosts correlation score."""
    builder = ParlayBuilder(min_leg_prob=0.75)

    correlated_legs = [
        ParlayLeg("Luka Doncic", "DAL", "BOS", "pts", 24.5, "Over", 1.85, 0.78, "STACK_PASS_SCORER"),
        ParlayLeg("Kyrie Irving", "DAL", "BOS", "ast", 3.5, "Over", 1.88, 0.76, "STACK_PASS_SCORER"),
        ParlayLeg("Jayson Tatum", "BOS", "DAL", "pts", 20.5, "Over", 1.85, 0.75, "PACE_BOOST"),
    ]

    score, rationale = builder.assess_correlation_score(correlated_legs)
    assert score > 0
    assert "Game Script Stacking DAL" in rationale
