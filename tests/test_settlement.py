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

def test_compute_historical_stats_separates_core_and_satellite(tmp_path, monkeypatch):
    import pandas as pd
    from settle_nba import SettlementEngine
    import config

    engine = SettlementEngine()
    engine.history_dir = tmp_path

    # Create dummy master CSV
    df = pd.DataFrame([
        {
            "date": "2026-10-07",
            "bet_type": "CORE_STRAIGHT",
            "market": "moneyline",
            "selection": "Golden State Warriors ML",
            "stake_amount": 20.0,
            "result": "LOST",
            "pnl": -20.0,
        },
        {
            "date": "2026-10-07",
            "bet_type": "CORE_STRAIGHT",
            "market": "total",
            "selection": "Over 220.5",
            "stake_amount": 20.0,
            "result": "WON",
            "pnl": 18.0,
        },
        {
            "date": "2026-10-07",
            "bet_type": "SATELLITE_PARLAY",
            "market": "player_props_parlay",
            "selection": "PARLAY_4LEG",
            "stake_amount": 10.0,
            "result": "LOST",
            "pnl": -10.0,
        },
    ])
    df.to_csv(tmp_path / "preseason_apuestas.csv", index=False)

    stats = engine.compute_historical_stats(is_preseason=True)

    # Core stats
    assert stats["core"]["won"] == 1
    assert stats["core"]["lost"] == 1
    assert stats["core"]["total_staked"] == 40.0
    assert stats["core"]["total_pnl"] == -2.0
    assert stats["core"]["winrate"] == 50.0

    # Satellite stats
    assert stats["satellite"]["won"] == 0
    assert stats["satellite"]["lost"] == 1
    assert stats["satellite"]["total_staked"] == 10.0
    assert stats["satellite"]["total_pnl"] == -10.0
    assert stats["satellite"]["winrate"] == 0.0

    # Global stats
    assert stats["won"] == 1
    assert stats["lost"] == 2
    assert stats["total_staked"] == 50.0
    assert stats["total_pnl"] == -12.0

def test_send_settlement_report_output(monkeypatch):
    from src.utils.notifier import TelegramNotifier

    notifier = TelegramNotifier()
    # Mock _send_text to verify output content
    sent_messages = []
    monkeypatch.setattr(notifier, "_send_text", lambda text: sent_messages.append(text) or True)

    summary = {
        "won": 1,
        "lost": 2,
        "push": 0,
        "total_pnl": -12.0,
        "roi_percent": -24.0,
        "core": {
            "won": 1,
            "lost": 1,
            "push": 0,
            "total_pnl": -2.0,
            "roi_percent": -5.0,
            "winrate": 50.0,
        },
        "satellite": {
            "won": 0,
            "lost": 1,
            "push": 0,
            "total_pnl": -10.0,
            "roi_percent": -100.0,
            "winrate": 0.0,
        },
    }
    settled_rows = [
        {"selection": "Warriors ML", "result": "LOST", "pnl": -20.0, "bet_type": "CORE_STRAIGHT"},
        {"selection": "Over 220.5", "result": "WON", "pnl": 18.0, "bet_type": "CORE_STRAIGHT"},
        {"selection": "PARLAY_4LEG", "result": "LOST", "pnl": -10.0, "bet_type": "SATELLITE_PARLAY"},
    ]
    historical_stats = {
        "won": 1,
        "lost": 2,
        "push": 0,
        "total_pnl": -12.0,
        "roi_percent": -24.0,
        "core": {
            "won": 1,
            "lost": 1,
            "push": 0,
            "total_pnl": -2.0,
            "roi_percent": -5.0,
            "winrate": 50.0,
        },
        "satellite": {
            "won": 0,
            "lost": 1,
            "push": 0,
            "total_pnl": -10.0,
            "roi_percent": -100.0,
            "winrate": 0.0,
        },
        "season_type": "Pretemporada",
    }

    notifier.send_settlement_report(
        target_date="2026-10-07",
        summary=summary,
        settled_rows=settled_rows,
        historical_stats=historical_stats,
        is_preseason=True,
    )

    assert len(sent_messages) == 1
    msg = sent_messages[0]
    assert "CORE QUANT" in msg
    assert "SOÑADORA" in msg
    assert "BALANCE TOTAL DEL DÍA" in msg
    assert "Core Quant Acumulado" in msg
    assert "Soñadora Acumulado" in msg


def test_run_pipeline_sentinel_skips_duplicate_run(tmp_path, monkeypatch):
    import config
    import main

    target_date = "2026-10-12"
    sentinel = tmp_path / f"notification_{target_date}.done"
    sentinel.write_text("Notified at: test\n", encoding="utf-8")

    monkeypatch.setattr(config, "HISTORY_DIR", tmp_path)

    called_ingestion = []
    monkeypatch.setattr(main, "NBADataCollector", lambda: called_ingestion.append(True))

    # Without force, it should skip execution and not call NBADataCollector
    main.run_pipeline(date_str=target_date, force=False)
    assert len(called_ingestion) == 0


def test_run_pipeline_strict_date_filter(tmp_path, monkeypatch):
    import config
    import main
    import pandas as pd

    monkeypatch.setattr(config, "HISTORY_DIR", tmp_path)
    monkeypatch.setattr(config, "CACHE_DIR", tmp_path)

    # Mock NBADataCollector so it doesn't call stats.nba.com
    class FakeNBACollector:
        def get_team_stats(self, **kwargs):
            return pd.DataFrame([
                {"team_id": 1, "team_name": "Boston Celtics", "team_abbreviation": "BOS", "pace": 100.0, "ortg": 125.0, "drtg": 102.0, "pts": 120.0, "gp": 82},
                {"team_id": 2, "team_name": "Dallas Mavericks", "team_abbreviation": "DAL", "pace": 100.0, "ortg": 105.0, "drtg": 118.0, "pts": 102.0, "gp": 82},
                {"team_id": 3, "team_name": "Los Angeles Lakers", "team_abbreviation": "LAL", "pace": 100.0, "ortg": 125.0, "drtg": 102.0, "pts": 120.0, "gp": 82},
                {"team_id": 4, "team_name": "Golden State Warriors", "team_abbreviation": "GSW", "pace": 100.0, "ortg": 105.0, "drtg": 118.0, "pts": 102.0, "gp": 82},
            ])

        def get_player_stats(self, **kwargs):
            return pd.DataFrame()

        def get_league_averages(self, df):
            return {"pace": 100.0, "ortg": 114.0, "drtg": 114.0, "pts": 112.0}

    monkeypatch.setattr(main, "NBADataCollector", FakeNBACollector)

    # Mock OddsCollector with huge +EV on Boston (odds 3.50 when model expects Boston to win easily)
    fake_games = [
        {
            "game_id": "today_game",
            "home_team": "Boston Celtics",
            "away_team": "Dallas Mavericks",
            "commence_time": "2026-10-21T23:00:00Z",
            "markets": {
                "h2h": {"Boston Celtics": {"odds": 3.50, "market_fair_prob": 0.28}, "Dallas Mavericks": {"odds": 1.35, "market_fair_prob": 0.72}},
            },
        },
        {
            "game_id": "tomorrow_game",
            "home_team": "Los Angeles Lakers",
            "away_team": "Golden State Warriors",
            "commence_time": "2026-10-22T23:00:00Z",
            "markets": {
                "h2h": {"Los Angeles Lakers": {"odds": 3.50, "market_fair_prob": 0.28}, "Golden State Warriors": {"odds": 1.35, "market_fair_prob": 0.72}},
            },
        },
    ]

    class FakeOddsCollector:
        def get_game_odds(self, **kwargs):
            return fake_games

    monkeypatch.setattr(main, "OddsCollector", FakeOddsCollector)

    # Mock notifier so no Telegram message is sent
    monkeypatch.setattr(main.TelegramNotifier, "send_notification", lambda self, *args, **kwargs: True)

    main.run_pipeline(date_str="2026-10-21", force=True)

    # Check generated picks CSV
    picks_file = tmp_path / "picks_2026-10-21.csv"
    assert picks_file.exists()
    df = pd.read_csv(picks_file)
    # Ensure tomorrow's game was NOT evaluated
    game_ids = df["game_id"].unique()
    assert "today_game" in game_ids
    assert "tomorrow_game" not in game_ids



