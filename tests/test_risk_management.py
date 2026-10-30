"""
Unit tests for Institutional Risk Management.
Verifies Kelly truncation, minimum floor, proportional daily rescaling, and strict deduplication.
"""

import pytest
from src.models.benter_engine import BenterEngine, BetEvaluation
from src.models.risk_manager import deduplicate_picks_by_game, rescale_daily_exposure


def test_kelly_truncation_ceiling():
    """Verify Quarter-Kelly stake is strictly capped at 1.75% (0.0175) for high-edge bets."""
    engine = BenterEngine(
        kelly_fraction=0.25,
        min_kelly_stake=0.0100,
        max_kelly_stake=0.0175,
        bankroll=1000.0,
    )

    # Very large edge: model=85%, market fair=80%, odds=2.0
    res = engine.evaluate_bet(
        game_id="game_1",
        market="moneyline",
        selection="Heavy Favorite",
        decimal_odds=2.0,
        p_model=0.85,
        p_market_fair=0.80,
    )

    assert res.is_approved is True
    # Raw quarter-kelly would be ~0.154 (15.4%), but must be capped at exactly 1.75%
    assert res.quarter_kelly > 0.0175
    assert res.recommended_stake_fraction == 0.0175
    assert res.recommended_stake_amount == 17.50


def test_kelly_floor_discard():
    """Verify Quarter-Kelly stake is discarded (0.0) if under 1.00% (0.0100)."""
    engine = BenterEngine(
        kelly_fraction=0.25,
        min_kelly_stake=0.0100,
        max_kelly_stake=0.0175,
        min_edge=0.01,
        min_ev_percent=1.0,
    )

    # Edge exists but tiny: odds=2.0, p_final ~ 0.51 -> full_k ~ 0.02 -> q_k ~ 0.005 (0.50%)
    res = engine.evaluate_bet(
        game_id="game_2",
        market="total",
        selection="Under 220",
        decimal_odds=2.0,
        p_model=0.51,
        p_market_fair=0.51,
    )

    assert res.is_approved is False
    assert res.recommended_stake_fraction == 0.0
    assert "below floor" in res.rejection_reason.lower()


def test_strict_game_deduplication():
    """Verify exactly 1 bet per game_id is retained, selecting the highest EV%."""
    # Create two bets on game_A and one on game_B
    bet_a1 = BetEvaluation(
        game_id="game_A",
        market="moneyline",
        selection="Home ML",
        odds=1.90,
        model_prob=0.58,
        market_fair_prob=0.52,
        final_prob=0.54,
        market_implied_prob=0.526,
        edge=0.045,
        ev_percent=7.5,
        raw_kelly=0.06,
        quarter_kelly=0.015,
        recommended_stake_fraction=0.015,
        recommended_stake_amount=15.0,
        is_approved=True,
    )

    bet_a2 = BetEvaluation(
        game_id="game_A",
        market="spread",
        selection="Home -3.5",
        odds=1.95,
        model_prob=0.62,
        market_fair_prob=0.53,
        final_prob=0.56,
        market_implied_prob=0.512,
        edge=0.062,
        ev_percent=11.2,  # Higher EV%
        raw_kelly=0.08,
        quarter_kelly=0.0175,
        recommended_stake_fraction=0.0175,
        recommended_stake_amount=17.5,
        is_approved=True,
    )

    bet_b1 = BetEvaluation(
        game_id="game_B",
        market="total",
        selection="Over 224.5",
        odds=1.91,
        model_prob=0.58,
        market_fair_prob=0.52,
        final_prob=0.54,
        market_implied_prob=0.523,
        edge=0.042,
        ev_percent=6.8,
        raw_kelly=0.05,
        quarter_kelly=0.0125,
        recommended_stake_fraction=0.0125,
        recommended_stake_amount=12.5,
        is_approved=True,
    )

    picks = [bet_a1, bet_a2, bet_b1]
    deduped = deduplicate_picks_by_game(picks)

    # Exactly 2 picks remaining (one for game_A and one for game_B)
    assert len(deduped) == 2
    game_ids = [p.game_id for p in deduped]
    assert game_ids.count("game_A") == 1
    assert game_ids.count("game_B") == 1

    # For game_A, bet_a2 must have been selected
    game_a_pick = next(p for p in deduped if p.game_id == "game_A")
    assert game_a_pick.selection == "Home -3.5"
    assert game_a_pick.ev_percent == 11.2


def test_proportional_daily_exposure_rescaling():
    """Verify proportional rescaling when total stakes exceed 10.0% (0.100)."""
    # Create 8 approved picks each with 1.75% stake = 8 * 1.75% = 14.0% > 10.0%
    picks = []
    for i in range(8):
        picks.append(
            BetEvaluation(
                game_id=f"game_{i}",
                market="moneyline",
                selection=f"Team {i}",
                odds=1.90,
                model_prob=0.60,
                market_fair_prob=0.52,
                final_prob=0.55,
                market_implied_prob=0.526,
                edge=0.05,
                ev_percent=8.0,
                raw_kelly=0.07,
                quarter_kelly=0.0175,
                recommended_stake_fraction=0.0175,
                recommended_stake_amount=17.50,
                is_approved=True,
            )
        )

    initial_total = sum(p.recommended_stake_fraction for p in picks)
    assert pytest.approx(initial_total, abs=1e-4) == 0.14  # 14.0%

    rescaled = rescale_daily_exposure(picks, max_daily_exposure=0.100, bankroll=1000.0)

    rescaled_total = sum(p.recommended_stake_fraction for p in rescaled)
    # Total must now be <= 10.0% (within roundoff precision)
    assert rescaled_total <= 0.100001
    assert pytest.approx(rescaled_total, abs=1e-4) == 0.100

    # Each pick should have scaled by 0.10 / 0.14 = 0.714285
    expected_fraction = 0.0175 * (0.100 / 0.14)
    for p in rescaled:
        assert pytest.approx(p.recommended_stake_fraction, abs=1e-4) == expected_fraction
        assert pytest.approx(p.recommended_stake_amount, abs=0.1) == round(expected_fraction * 1000.0, 2)


def test_set_bankroll_cli(tmp_path, monkeypatch, capsys):
    """Validate setting custom bankroll via CLI updates bankroll_state.json and prints confirmation."""
    import json
    import sys
    import config
    import main

    test_state_file = tmp_path / "bankroll_state.json"
    monkeypatch.setattr(config, "BANKROLL_STATE_FILE", test_state_file)

    # 1. Test CLI with --set-bankroll 1250.0
    monkeypatch.setattr(sys, "argv", ["main.py", "--set-bankroll", "1250.0"])
    main.main()

    captured = capsys.readouterr()
    assert "[BANKROLL ACTUALIZADO] Saldo base configurado en: $1250.00 MXN" in captured.out
    assert "Nuevo techo de Kelly (1.75%): $21.88 MXN" in captured.out
    assert "Nuevo piso operativo (0.50%): $6.25 MXN" in captured.out

    # Verify JSON file structure
    assert test_state_file.exists()
    with open(test_state_file, "r", encoding="utf-8") as f:
        data = json.load(f)
    assert data["current_bankroll"] == 1250.0
    assert "last_updated" in data

    # Verify config.get_current_bankroll() retrieves this exact value
    assert config.get_current_bankroll() == 1250.0

    # 2. Test negative value validation
    monkeypatch.setattr(sys, "argv", ["main.py", "--set-bankroll", "-100.0"])
    with pytest.raises(SystemExit):
        main.main()


def test_stake_sizing_with_custom_bankroll(tmp_path, monkeypatch):
    """Verify that Kelly stake amounts in pesos (1.75% and 1.00%) recalculate exactly with new bankroll."""
    import config
    from src.models.benter_engine import BenterEngine
    from src.models.risk_manager import rescale_daily_exposure

    test_state_file = tmp_path / "bankroll_state.json"
    monkeypatch.setattr(config, "BANKROLL_STATE_FILE", test_state_file)

    # Set new bankroll to $2000.0 MXN
    config.set_current_bankroll(2000.0)

    # BenterEngine without explicit bankroll argument should consult state file ($2000.0)
    engine = BenterEngine(
        kelly_fraction=0.25,
        min_kelly_stake=0.0100,
        max_kelly_stake=0.0175,
    )
    assert engine.bankroll == 2000.0

    # Test Ceiling: Large edge capped at 1.75%
    # 1.75% of $2000.0 = $35.00 MXN
    res_ceiling = engine.evaluate_bet(
        game_id="game_ceil",
        market="moneyline",
        selection="Heavy Favorite",
        decimal_odds=2.0,
        p_model=0.85,
        p_market_fair=0.80,
    )
    assert res_ceiling.is_approved is True
    assert res_ceiling.recommended_stake_fraction == 0.0175
    assert res_ceiling.recommended_stake_amount == 35.00

    # Test Floor sizing: 1.00% (0.0100)
    # 1.00% of $2000.0 = $20.00 MXN
    pick_floor = BetEvaluation(
        game_id="game_floor",
        market="moneyline",
        selection="Pick Floor",
        odds=2.0,
        model_prob=0.55,
        market_fair_prob=0.50,
        final_prob=0.52,
        market_implied_prob=0.50,
        edge=0.04,
        ev_percent=5.5,
        raw_kelly=0.04,
        quarter_kelly=0.0100,
        recommended_stake_fraction=0.0100,
        recommended_stake_amount=20.00,
        is_approved=True,
    )

    rescaled = rescale_daily_exposure([pick_floor])
    assert rescaled[0].recommended_stake_amount == 20.00
    assert rescaled[0].recommended_stake_fraction == 0.0100


def test_settle_syncs_bankroll(tmp_path, monkeypatch):
    """Verify settle_nba updates the bankroll state file by adding net PnL."""
    import config
    import pandas as pd
    from settle_nba import SettlementEngine

    test_state_file = tmp_path / "bankroll_state.json"
    history_dir = tmp_path / "history"
    history_dir.mkdir(parents=True, exist_ok=True)

    # Initialize bankroll at 1000.0
    config.set_current_bankroll(1000.0, state_file=test_state_file)

    # Mock daily picks file
    date_str = "2026-10-21"
    picks_file = history_dir / f"picks_{date_str}.csv"
    picks_df = pd.DataFrame([
        {
            "date": date_str,
            "game_id": "game_1",
            "matchup": "Boston Celtics @ Dallas Mavericks",
            "bet_type": "CORE_STRAIGHT",
            "market": "spread",
            "selection": "Boston Celtics -5.5",
            "odds": 1.91,
            "stake_amount": 17.50,
            "status": "PENDING",
            "result": "PENDING",
            "pnl": 0.0,
        }
    ])
    picks_df.to_csv(picks_file, index=False)

    # Master file
    master_file = history_dir / "todas_las_apuestas.csv"
    picks_df.to_csv(master_file, index=False)

    engine = SettlementEngine(history_dir=history_dir, bankroll_state_file=test_state_file)

    # Mock ESPN scoreboard returning Boston win covering spread
    class FakeESPN:
        def get_daily_scoreboard(self, *args, **kwargs):
            return [
                {
                    "event_id": "event_1",
                    "is_completed": True,
                    "home_team": {"name": "Dallas Mavericks", "abbrev": "DAL", "score": 100.0},
                    "away_team": {"name": "Boston Celtics", "abbrev": "BOS", "score": 115.0},
                }
            ]

        def get_game_boxscore(self, *args, **kwargs):
            return {}

    engine.espn = FakeESPN()
    # Mock TelegramNotifier
    monkeypatch.setattr("settle_nba.TelegramNotifier.send_settlement_report", lambda *args, **kwargs: True)

    summary = engine.settle_date(date_str)
    assert summary["status"] == "SUCCESS"
    assert summary["won"] == 1
    # PnL for winning bet of $17.50 @ 1.91 = 17.50 * 0.91 = 15.92 or 15.93
    expected_pnl = summary["total_pnl"]
    assert expected_pnl > 0

    # Verify bankroll was updated to 1000.0 + expected_pnl
    updated_b = config.get_current_bankroll(state_file=test_state_file)
    assert pytest.approx(updated_b, abs=0.01) == 1000.0 + expected_pnl

