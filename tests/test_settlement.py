import pytest
from settle_nba import SettlementEngine

def test_settle_unplayed_game_returns_pending():
    engine = SettlementEngine()
    row = {
        "market": "moneyline",
        "selection": "Golden State Warriors ML",
        "odds": 2.10,
        "stake_amount": 20.0,
        "matchup": "Golden State Warriors @ Los Angeles Lakers",
    }
    # Empty scores (games haven't completed)
    res, pnl = engine.settle_single_bet(row, {})
    assert res == "PENDING"
    assert pnl == 0.0

def test_settle_missing_team_returns_pending():
    engine = SettlementEngine()
    row = {
        "market": "spread",
        "selection": "Oklahoma City Thunder -2.5",
        "odds": 1.91,
        "stake_amount": 15.0,
        "matchup": "Denver Nuggets @ Oklahoma City Thunder",
    }
    # Scores only have Boston and Dallas, but not OKC
    scores = {
        "BOS": {"pts": 110.0, "opponent_pts": 105.0, "team_name": "Boston Celtics"},
        "Boston Celtics": {"pts": 110.0, "opponent_pts": 105.0, "team_name": "Boston Celtics"},
    }
    res, pnl = engine.settle_single_bet(row, scores)
    assert res == "PENDING"
    assert pnl == 0.0

def test_settle_completed_spread_win_and_loss():
    engine = SettlementEngine()
    scores = {
        "OKC": {"pts": 115.0, "opponent_pts": 108.0, "team_name": "Oklahoma City Thunder"},
        "Oklahoma City Thunder": {"pts": 115.0, "opponent_pts": 108.0, "team_name": "Oklahoma City Thunder"},
    }
    # OKC wins by 7, spread -2.5 -> cover_diff = 7 - 2.5 = 4.5 > 0 -> WON
    win_row = {
        "market": "spread",
        "selection": "Oklahoma City Thunder -2.5",
        "odds": 1.90,
        "stake_amount": 10.0,
    }
    res, pnl = engine.settle_single_bet(win_row, scores)
    assert res == "WON"
    assert pnl == 9.0

    # OKC -8.5 -> cover_diff = 7 - 8.5 = -1.5 < 0 -> LOST
    loss_row = {
        "market": "spread",
        "selection": "Oklahoma City Thunder -8.5",
        "odds": 1.90,
        "stake_amount": 10.0,
    }
    res, pnl = engine.settle_single_bet(loss_row, scores)
    assert res == "LOST"
    assert pnl == -10.0

def test_settle_parlay_pending_when_games_unfinished():
    engine = SettlementEngine()
    parlay_row = {
        "market": "player_props_parlay",
        "selection": "PARLAY_4LEG",
        "odds": 10.50,
        "stake_amount": 20.0,
        "details": "Legs: [Luka Doncic (DAL) Over 7.5 AST; Nikola Jokic (DEN) Over 23.0 PTS]",
    }
    # Only DAL is finished, DEN is still playing
    scores = {
        "DAL": {"pts": 112.0, "opponent_pts": 104.0, "team_name": "Dallas Mavericks"},
    }
    res, pnl = engine.settle_single_bet(parlay_row, scores)
    assert res == "PENDING"
    assert pnl == 0.0
