"""
Regression tests for settlement and bankroll bugs found in the v1 audit.
"""

import pandas as pd
import pytest

import config
import settle_nba
from settle_nba import SettlementEngine
from src.models.benter_engine import BenterEngine

GAME = {"pts": 110.0, "opponent_pts": 100.0, "team_name": "x"}
PARLAY = {
    "market": "player_props_parlay",
    "bet_type": "SATELLITE_PARLAY",
    "selection": "PARLAY_2LEG_1",
    "odds": 10.0,
    "stake_amount": 20.0,
    "details": "x | Legs: [Luka Doncic (LAL) Over 20.5 PTS @ 1.8; Nikola Jokic (DEN) Over 5.5 AST @ 1.8]",
}


def _player(team: str, pts: float = 30.0, ast: float = 10.0) -> dict:
    return {"team": team, "pts": pts, "ast": ast, "reb": 5.0, "fg3m": 1.0}


def _parlay_scores(players: dict) -> dict:
    return {"LAL": GAME, "DEN": GAME, "__player_stats__": players}


def test_parlay_without_any_player_stats_stays_pending():
    res, pnl = SettlementEngine().settle_single_bet(PARLAY, _parlay_scores({}))
    assert (res, pnl) == ("PENDING", 0.0)


def test_parlay_with_player_who_did_not_play_is_void_not_won():
    players = {"luka doncic": _player("LAL"), "aaron gordon": _player("DEN")}
    res, pnl = SettlementEngine().settle_single_bet(PARLAY, _parlay_scores(players))
    assert (res, pnl) == ("PUSH", 0.0)


def test_parlay_with_missing_team_boxscore_stays_pending():
    players = {"luka doncic": _player("LAL")}
    res, pnl = SettlementEngine().settle_single_bet(PARLAY, _parlay_scores(players))
    assert (res, pnl) == ("PENDING", 0.0)


def test_parlay_loses_when_a_known_leg_lost_even_if_other_player_missing():
    players = {"luka doncic": _player("LAL", pts=10.0), "aaron gordon": _player("DEN")}
    res, pnl = SettlementEngine().settle_single_bet(PARLAY, _parlay_scores(players))
    assert (res, pnl) == ("LOST", -20.0)


def test_parlay_wins_only_when_every_leg_is_verified():
    players = {"luka doncic": _player("LAL"), "nikola jokic": _player("DEN")}
    res, pnl = SettlementEngine().settle_single_bet(PARLAY, _parlay_scores(players))
    assert (res, pnl) == ("WON", 180.0)


def test_unfinished_team_is_not_settled_with_an_abbreviation_substring():
    # 'SA' (San Antonio, finished) must not match 'Sacramento Kings' (still playing)
    scores = {"SA": GAME, "San Antonio Spurs": GAME}
    row = {"market": "moneyline", "selection": "Sacramento Kings ML", "odds": 2.0, "stake_amount": 10.0}
    assert SettlementEngine().settle_single_bet(row, scores) == ("PENDING", 0.0)


def _core_row(selection: str, stake: float = 10.0) -> dict:
    return {
        "date": "2026-11-05",
        "game_id": selection,
        "matchup": "x",
        "bet_type": "CORE_STRAIGHT",
        "market": "moneyline",
        "selection": f"{selection} ML",
        "odds": 2.0,
        "stake_amount": stake,
        "status": "PENDING",
        "result": "PENDING",
        "pnl": 0.0,
        "details": "",
    }


@pytest.fixture
def engine(tmp_path, monkeypatch):
    monkeypatch.setattr(settle_nba.TelegramNotifier, "send_settlement_report", lambda *a, **k: True)
    state = tmp_path / "bankroll.json"
    config.set_current_bankroll(1000.0, state_file=state)
    return SettlementEngine(history_dir=tmp_path, bankroll_state_file=state)


def _bankroll(engine: SettlementEngine) -> float:
    return config.get_current_bankroll(state_file=engine.bankroll_state_file)


def _write_picks(engine: SettlementEngine, rows: list) -> None:
    pd.DataFrame(rows).to_csv(engine.get_picks_file("2026-11-05"), index=False)


def _scores(*teams: str) -> dict:
    return {t: GAME for t in teams}


def test_partial_settlement_does_not_double_count_bankroll(engine, monkeypatch):
    _write_picks(engine, [_core_row("Boston Celtics"), _core_row("Miami Heat")])

    monkeypatch.setattr(engine, "fetch_box_scores", lambda d: _scores("Boston Celtics"))
    engine.settle_date("2026-11-05")
    assert _bankroll(engine) == 1010.0

    monkeypatch.setattr(engine, "fetch_box_scores", lambda d: _scores("Boston Celtics", "Miami Heat"))
    engine.settle_date("2026-11-05")
    assert _bankroll(engine) == 1020.0


def test_forced_resettlement_does_not_change_bankroll(engine, monkeypatch):
    _write_picks(engine, [_core_row("Boston Celtics")])
    monkeypatch.setattr(engine, "fetch_box_scores", lambda d: _scores("Boston Celtics"))
    engine.settle_date("2026-11-05")
    engine.settle_date("2026-11-05", force=True)
    assert _bankroll(engine) == 1010.0


def test_forced_resettlement_without_scores_keeps_settled_results(engine, monkeypatch):
    _write_picks(engine, [_core_row("Boston Celtics")])
    monkeypatch.setattr(engine, "fetch_box_scores", lambda d: _scores("Boston Celtics"))
    engine.settle_date("2026-11-05")

    monkeypatch.setattr(engine, "fetch_box_scores", lambda d: {})
    engine.settle_date("2026-11-05", force=True)
    df = pd.read_csv(engine.get_picks_file("2026-11-05"))
    assert df.iloc[0]["result"] == "WON"
    assert _bankroll(engine) == 1010.0


def test_satellite_pnl_does_not_touch_core_bankroll(engine, monkeypatch):
    parlay = {**_core_row("x"), **PARLAY}
    _write_picks(engine, [parlay])
    players = {"luka doncic": _player("LAL"), "nikola jokic": _player("DEN")}
    monkeypatch.setattr(engine, "fetch_box_scores", lambda d: _parlay_scores(players))
    engine.settle_date("2026-11-05")
    assert _bankroll(engine) == 1000.0


def test_historical_stats_report_brier_and_clv(engine):
    rows = [
        {**_core_row("A"), "status": "SETTLED", "result": "WON", "pnl": 10.0, "model_prob": 0.6,
         "market_fair_prob": 0.5, "clv_percent": 4.0},
        {**_core_row("B"), "status": "SETTLED", "result": "LOST", "pnl": -10.0, "model_prob": 0.6,
         "market_fair_prob": 0.5, "clv_percent": ""},
    ]
    pd.DataFrame(rows).to_csv(engine.history_dir / "todas_las_apuestas.csv", index=False)
    core = engine.compute_historical_stats()["core"]
    assert core["brier_model"] == pytest.approx(0.26)
    assert core["brier_market"] == pytest.approx(0.25)
    assert core["clv_avg"] == pytest.approx(4.0)
    assert core["clv_n"] == 1


def test_discrepancy_cap_rejects_model_far_from_market():
    engine = BenterEngine(bankroll=1000.0, max_market_discrepancy=0.10, min_edge=0.0, min_ev_percent=0.0, min_kelly_stake=0.0)
    far = engine.evaluate_bet("g", "moneyline", "A", 2.0, p_model=0.70, p_market_fair=0.50)
    assert not far.is_approved
    assert "discrepancy" in far.rejection_reason.lower()
    near = engine.evaluate_bet("g", "moneyline", "A", 2.0, p_model=0.58, p_market_fair=0.50)
    assert near.is_approved


def test_default_filter_can_approve_a_bet_inside_the_cap():
    """Thresholds must stay reachable: a -110 bet at the cap has to pass."""
    engine = BenterEngine(bankroll=1000.0)
    bet = engine.evaluate_bet(
        "g", "spread", "A -2", 1.91, p_model=0.50 + config.MAX_MARKET_DISCREPANCY, p_market_fair=0.50
    )
    assert bet.is_approved, bet.rejection_reason
