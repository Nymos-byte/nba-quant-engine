"""
Tests for current-season ratings, injuries, closing odds and sample archiving.
"""

from datetime import date, datetime, timezone

import pandas as pd
import pytest

import config
from capture_closing import update_closing_columns
from scripts.archive_sample import archive_sample
from src.data.nba_collector import NBADataCollector
from src.data.odds_collector import OddsCollector
from src.data.season_stats import (
    SeasonStatsStore,
    build_player_stats,
    build_team_ratings,
    injury_scoring_loss,
)

# 100 possessions, 110 points
TEAM_A = {"fgm": 40.0, "fga": 85.0, "fg3m": 10.0, "ftm": 20.0, "fta": 25.0, "oreb": 10.0, "to": 14.0}
# 100 possessions, 100 points
TEAM_B = {"fgm": 37.0, "fga": 85.0, "fg3m": 6.0, "ftm": 20.0, "fta": 25.0, "oreb": 10.0, "to": 14.0}
PRIOR = pd.DataFrame([
    {"team_id": 1, "team_abbreviation": "AAA", "team_name": "A", "pace": 90.0, "ortg": 100.0, "drtg": 120.0},
    {"team_id": 2, "team_abbreviation": "BBB", "team_name": "B", "pace": 100.0, "ortg": 100.0, "drtg": 100.0},
    {"team_id": 3, "team_abbreviation": "CCC", "team_name": "C", "pace": 99.0, "ortg": 111.0, "drtg": 112.0},
])


def _game(pts: float = 30.0) -> dict:
    return {
        "teams": {"AAA": TEAM_A, "BBB": TEAM_B},
        "players": {
            "star one": {"team": "AAA", "min": 36.0, "pts": pts, "reb": 5.0, "ast": 5.0, "fg3m": 3.0},
        },
    }


def test_ratings_blend_season_and_prior_by_games_played():
    ratings = build_team_ratings([_game()] * 10, PRIOR, prior_games=10.0)
    a = ratings[ratings["team_abbreviation"] == "AAA"].iloc[0]
    assert a["season_gp"] == 10
    assert a["pace"] == pytest.approx((100.1 + 90.0) / 2, abs=0.1)
    assert a["ortg"] == pytest.approx((109.9 + 100.0) / 2, abs=0.1)
    assert a["drtg"] == pytest.approx((99.9 + 120.0) / 2, abs=0.1)


def test_team_without_games_keeps_prior():
    ratings = build_team_ratings([_game()], PRIOR, prior_games=10.0)
    c = ratings[ratings["team_abbreviation"] == "CCC"].iloc[0]
    assert (c["season_gp"], c["ortg"]) == (0, 111.0)


def test_player_uses_team_of_latest_game_and_needs_min_games():
    traded = {"teams": {"BBB": TEAM_B, "AAA": TEAM_A}, "players": {
        "star one": {"team": "BBB", "min": 30.0, "pts": 20.0, "reb": 1.0, "ast": 1.0, "fg3m": 1.0},
    }}
    players = build_player_stats([_game(), traded], min_games=2)
    assert players.iloc[0]["team_abbreviation"] == "BBB"
    assert players.iloc[0]["pts"] == 25.0
    assert build_player_stats([_game()], min_games=2).empty


def test_injury_loss_counts_out_players_only_and_is_capped():
    players = pd.DataFrame([
        {"player_name": "Star One", "team_abbreviation": "AAA", "team_pts_share": 0.30},
        {"player_name": "Two", "team_abbreviation": "BBB", "team_pts_share": 0.20},
        {"player_name": "Absent", "team_abbreviation": "CCC", "team_pts_share": 0.90},
    ])
    injuries = {"star one": "Out", "two": "Day-To-Day", "absent": "Out"}
    loss = injury_scoring_loss(players, injuries, replacement_rate=0.5, max_penalty=0.08)
    assert loss == {"AAA": 0.08, "CCC": 0.08}
    assert injury_scoring_loss(players, {"star one": "Out"}, replacement_rate=0.9, max_penalty=0.08)["AAA"] == pytest.approx(0.03)


class _FakeEspn:
    def __init__(self, scoreboards):
        self.scoreboards = scoreboards
        self.calls = []

    def get_daily_scoreboard(self, day):
        self.calls.append(day)
        return self.scoreboards.get(day, [])

    def get_game_boxscore(self, event_id):
        return {
            "teams": {"GS": TEAM_A, "NY": TEAM_B},
            "players": {"star one": {"team": "GS", "min": 30.0, "pts": 20.0, "reb": 1.0, "ast": 1.0, "fg3m": 1.0}},
        }


def _espn_game(completed=True):
    return {"event_id": "1", "is_completed": completed, "home_team": {"abbrev": "GS"}, "away_team": {"abbrev": "NY"}}


def test_store_downloads_finished_dates_once_and_retries_the_rest(tmp_path):
    espn = _FakeEspn({"2026-10-20": [_espn_game()], "2026-10-21": [_espn_game(completed=False)]})
    store = SeasonStatsStore(espn=espn, store_file=tmp_path / "s.json", season_start=date(2026, 10, 20))

    games = store.update(date(2026, 10, 22))
    assert list(games[0]["teams"]) == ["GSW", "NYK"]
    assert espn.calls == ["2026-10-20", "2026-10-21"]

    espn.calls.clear()
    assert len(store.update(date(2026, 10, 22))) == 1
    assert espn.calls == ["2026-10-21"]


def test_collector_applies_injury_penalty_and_removes_injured_players(tmp_path, monkeypatch):
    games = [_game()] * 5
    monkeypatch.setattr(SeasonStatsStore, "update", lambda self, today: games)
    monkeypatch.setattr(NBADataCollector, "_get_injuries", lambda self: {"star one": "Out"})
    collector = NBADataCollector(cache_dir=tmp_path)
    monkeypatch.setattr("src.data.nba_collector.NBA_TEAMS_REFERENCE", PRIOR.to_dict(orient="records"))

    healthy = build_team_ratings(games, PRIOR)
    ratings = collector.get_team_stats()
    a_healthy = healthy[healthy["team_abbreviation"] == "AAA"].iloc[0]["ortg"]
    a_adjusted = ratings[ratings["team_abbreviation"] == "AAA"].iloc[0]["ortg"]
    assert a_adjusted < a_healthy
    assert collector.get_player_stats().empty


def test_clippers_name_matches_odds_api():
    from src.data.nba_collector import NBA_TEAMS_REFERENCE
    assert "Los Angeles Clippers" in {t["team_name"] for t in NBA_TEAMS_REFERENCE}


def test_failed_odds_api_never_produces_synthetic_games(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("down")
    monkeypatch.setattr("src.data.odds_collector.requests.get", boom)
    assert OddsCollector(api_key="k", cache_dir=tmp_path).get_game_odds(force_refresh=True) == []
    assert OddsCollector(api_key="", cache_dir=tmp_path).get_game_odds(force_refresh=True) == []


def _picks() -> pd.DataFrame:
    base = {"bet_type": "CORE_STRAIGHT", "status": "PENDING", "odds": 2.0}
    return pd.DataFrame([
        {**base, "game_id": "g1", "market": "moneyline", "selection": "Boston Celtics ML"},
        {**base, "game_id": "g2", "market": "spread", "selection": "Miami Heat -2.5"},
        {**base, "game_id": "g3", "market": "total", "selection": "Over 220.5"},
        {**base, "game_id": "g4", "market": "moneyline", "selection": "Utah Jazz ML"},
    ])


def _odds_games() -> list:
    future, past = "2026-11-05T23:00:00Z", "2026-11-05T10:00:00Z"
    return [
        {"game_id": "g1", "commence_time": future, "markets": {"h2h": {"Boston Celtics": {"odds": 1.9, "market_fair_prob": 0.52}}}},
        {"game_id": "g2", "commence_time": future, "markets": {"spreads": {"Miami Heat": {"point": -3.5, "odds": 1.9, "market_fair_prob": 0.5}}}},
        {"game_id": "g3", "commence_time": future, "markets": {"totals": {"Over": {"point": 220.5, "odds": 1.9, "market_fair_prob": 0.48}}}},
        {"game_id": "g4", "commence_time": past, "markets": {"h2h": {"Utah Jazz": {"odds": 1.5, "market_fair_prob": 0.6}}}},
    ]


def test_closing_capture_computes_clv_only_before_tipoff_and_on_same_line():
    now = datetime(2026, 11, 5, 20, 0, tzinfo=timezone.utc)
    df, updated = update_closing_columns(_picks(), _odds_games(), now)
    assert updated == 3
    assert df.loc[0, "clv_percent"] == 4.0
    assert pd.isna(df.loc[1, "clv_percent"]) and df.loc[1, "closing_point"] == -3.5
    assert df.loc[2, "clv_percent"] == -4.0
    assert pd.isna(df.loc[3, "closing_odds"])


def test_archive_moves_sample_and_resets_bankroll(tmp_path):
    history = tmp_path / "history"
    history.mkdir()
    for name in ("picks_2026-10-22.csv", "settlement_2026-10-22.done", "todas_las_apuestas.csv", "preseason_apuestas.csv"):
        (history / name).write_text("x", encoding="utf-8")
    state = tmp_path / "bankroll.json"
    config.set_current_bankroll(1171.4, state_file=state)

    assert archive_sample("v1", bankroll=1000.0, history_dir=history, state_file=state) == 3
    assert (history / "archive" / "v1" / "picks_2026-10-22.csv").exists()
    assert not (history / "todas_las_apuestas.csv").exists()
    assert (history / "preseason_apuestas.csv").exists()
    assert config.get_current_bankroll(state_file=state) == 1000.0
    assert '1171.4' in (history / "archive" / "v1" / "bankroll.json").read_text(encoding="utf-8")

    with pytest.raises(FileExistsError):
        archive_sample("v1", history_dir=history, state_file=state)
