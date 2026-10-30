"""
Tests for real-line player prop bets, market consensus and simulator calibration.
"""

from dataclasses import replace
from datetime import date, datetime, timezone

import numpy as np
import pandas as pd
import pytest

import config
from capture_closing import update_closing_columns
from main import evaluate_player_props
from settle_nba import SettlementEngine
from src.data.odds_collector import OddsCollector, prop_events_budget
from src.models.benter_engine import BenterEngine
from src.models.player_props import PlayerPropsModel
from src.models.rating_engine import RatingEngine
from src.models.risk_manager import deduplicate_picks_by_game

HOME = {"pts": 110.0, "opponent_pts": 100.0, "team_name": "Los Angeles Lakers"}
AWAY = {"pts": 100.0, "opponent_pts": 110.0, "team_name": "Denver Nuggets"}
SCORES = {"LAL": HOME, "Los Angeles Lakers": HOME, "DEN": AWAY, "Denver Nuggets": AWAY}


def _prop_row(selection: str = "Luka Doncic Over 28.5 PTS") -> dict:
    return {
        "market": "player_prop",
        "bet_type": "CORE_STRAIGHT",
        "selection": selection,
        "matchup": "Denver Nuggets @ Los Angeles Lakers",
        "odds": 1.9,
        "stake_amount": 10.0,
    }


def _box(luka_pts: float = 30.0) -> dict:
    return {
        "luka doncic": {"team": "LAL", "pts": luka_pts, "reb": 8.0, "ast": 9.0, "fg3m": 3.0},
        "nikola jokic": {"team": "DEN", "pts": 25.0, "reb": 12.0, "ast": 10.0, "fg3m": 1.0},
    }


@pytest.mark.parametrize("selection,box,expected", [
    ("Luka Doncic Over 28.5 PTS", _box(30.0), ("WON", 9.0)),
    ("Luka Doncic Over 28.5 PTS", _box(20.0), ("LOST", -10.0)),
    ("Luka Doncic Under 28.5 PTS", _box(20.0), ("WON", 9.0)),
    ("Luka Doncic Over 30 PTS", _box(30.0), ("PUSH", 0.0)),
    ("Austin Reaves Over 15.5 PTS", _box(), ("PUSH", 0.0)),
])
def test_settle_player_prop(selection, box, expected):
    scores = {**SCORES, "__player_stats__": box}
    assert SettlementEngine().settle_single_bet(_prop_row(selection), scores) == expected


def test_prop_stays_pending_if_game_or_box_score_is_missing():
    engine = SettlementEngine()
    assert engine.settle_single_bet(_prop_row(), {"LAL": HOME, "Los Angeles Lakers": HOME, "__player_stats__": _box()})[0] == "PENDING"
    assert engine.settle_single_bet(_prop_row(), {**SCORES, "__player_stats__": {}})[0] == "PENDING"
    only_den = {"nikola jokic": _box()["nikola jokic"]}
    assert engine.settle_single_bet(_prop_row(), {**SCORES, "__player_stats__": only_den})[0] == "PENDING"


def test_prop_counts_in_core_not_satellite():
    assert not SettlementEngine.is_satellite_bet(_prop_row())


def _book(key: str, price_a: float, price_b: float, point: float = -2.5) -> dict:
    return {"key": key, "markets": [{"key": "spreads", "outcomes": [
        {"name": "Boston Celtics", "price": price_a, "point": point},
        {"name": "Miami Heat", "price": price_b, "point": -point},
    ]}]}


def test_market_fair_prob_is_consensus_of_books_at_same_line():
    raw = [{
        "id": "g", "home_team": "Boston Celtics", "away_team": "Miami Heat",
        "commence_time": datetime.now(timezone.utc).isoformat(),
        "bookmakers": [_book("draftkings", 2.05, 1.80), _book("fanduel", 1.91, 1.91), _book("other", 1.5, 2.5, point=-7.5)],
    }]
    spread = OddsCollector(api_key="k")._parse_and_devig_odds_api(raw)[0]["markets"]["spreads"]
    a = spread["Boston Celtics"]
    assert a["odds"] == 2.05
    own_book = (1 / 2.05) / (1 / 2.05 + 1 / 1.80)
    assert a["market_fair_prob"] == pytest.approx((own_book + 0.5) / 2, abs=1e-3)
    assert spread["Miami Heat"]["market_fair_prob"] == pytest.approx(1 - a["market_fair_prob"], abs=1e-3)


def _prop_book(key: str, point: float, over: float, under: float) -> dict:
    return {"key": key, "markets": [{"key": "player_points", "outcomes": [
        {"name": "Over", "description": "Luka Dončić", "price": over, "point": point},
        {"name": "Under", "description": "Luka Dončić", "price": under, "point": point},
        {"name": "Over", "description": "One Sided", "price": 1.9, "point": 10.5},
    ]}]}


def test_parse_player_props_requires_both_sides_and_uses_consensus():
    payload = {"bookmakers": [_prop_book("fanduel", 28.5, 2.0, 1.8), _prop_book("draftkings", 28.5, 1.91, 1.91), _prop_book("x", 30.5, 3.0, 1.3)]}
    parsed = OddsCollector(api_key="k")._parse_player_props(payload)["pts"]
    assert "one sided" not in parsed
    entry = parsed["luka doncic"]
    assert entry["point"] == 28.5
    assert entry["Over"]["odds"] == 1.91  # draftkings has price priority
    fanduel = (1 / 2.0) / (1 / 2.0 + 1 / 1.8)
    assert entry["Over"]["market_fair_prob"] == pytest.approx((fanduel + 0.5) / 2, abs=1e-3)


def test_props_are_skipped_when_credits_are_near_the_reserve(tmp_path, monkeypatch):
    collector = OddsCollector(api_key="k", cache_dir=tmp_path)
    monkeypatch.setattr(collector, "_remaining_credits", lambda: config.ODDS_API_CREDIT_RESERVE)
    called = []
    monkeypatch.setattr("src.data.odds_collector.requests.get", lambda *a, **k: called.append(1))
    assert collector.get_player_props(["g"]) == {}
    assert not called


def test_prop_budget_fits_the_monthly_quota():
    first_of_month = date(2026, 11, 1)
    last_of_month = date(2026, 11, 30)
    assert prop_events_budget(500, first_of_month) == 2
    assert prop_events_budget(300, first_of_month) == 0
    assert prop_events_budget(100, last_of_month) == 3
    assert prop_events_budget(35, last_of_month) == 0


def test_score_markets_do_not_spend_credits_on_props_in_closing_capture():
    class Spy:
        def __init__(self):
            self.markets = None

        def get_game_odds(self, markets="", force_refresh=False):
            self.markets = markets
            return []

    import tempfile
    from pathlib import Path
    from capture_closing import run
    with tempfile.TemporaryDirectory() as tmp:
        day = datetime.now(config.TZ_INFO).strftime("%Y-%m-%d")
        pd.DataFrame([
            {"date": day, "game_id": "g", "bet_type": "CORE_STRAIGHT", "status": "PENDING", "market": "moneyline", "selection": "A ML", "odds": 2.0},
            {"date": day, "game_id": "p", "bet_type": "SATELLITE_PARLAY", "status": "PENDING", "market": "player_props_parlay", "selection": "PARLAY", "odds": 9.0},
        ]).to_csv(Path(tmp) / f"picks_{day}.csv", index=False)
        spy = Spy()
        run(history_dir=Path(tmp), collector=spy)
    assert spy.markets == "h2h"


def test_min_odds_is_the_price_where_ev_hits_the_threshold():
    bet = BenterEngine(bankroll=1000.0).evaluate_bet("g", "spread", "A -2", 1.91, 0.60, 0.50)
    assert bet.min_odds == pytest.approx((1 + config.MIN_EV_PERCENT / 100) / bet.final_prob, abs=0.01)


def test_shrink_pulls_small_samples_toward_the_market():
    model = PlayerPropsModel()
    assert model.shrink_to_market(36.0, 28.0, games=5, prior_games=10.0) == pytest.approx(30.667, abs=1e-2)
    assert model.shrink_to_market(36.0, 28.0, games=90, prior_games=10.0) == pytest.approx(35.2, abs=1e-2)


@pytest.mark.parametrize("category,line,fair_over", [("ast", 1.5, 0.60), ("reb", 9.5, 0.45), ("pts", 27.5, 0.52)])
def test_market_implied_mean_reproduces_market_probability(category, line, fair_over):
    model = PlayerPropsModel()
    mean = model.market_implied_mean(category, line, fair_over)
    dist = replace(model.fit_distribution("", "", category, mean, 100.0, 100.0), adjusted_mean=mean)
    assert model.calculate_over_prob(dist, line) == pytest.approx(fair_over, abs=0.01)


def test_player_matching_the_market_shows_no_edge_on_a_low_count_line():
    """Regression: shrinking toward the line itself invented value on Under 1.5 with a favored Over."""
    model = PlayerPropsModel()
    implied = model.market_implied_mean("ast", 1.5, 0.60)
    props = {"ast": {"luka doncic": {"name": "Luka Doncic", "point": 1.5,
                                     "Over": {"odds": 1.6, "market_fair_prob": 0.60},
                                     "Under": {"odds": 2.4, "market_fair_prob": 0.40}}}}
    players = _player().assign(ast=implied)
    under = next(b for b in _evaluate(players, props) if "Under" in b.selection)
    assert under.model_prob == pytest.approx(0.40, abs=0.02)


def _player(gp: int = 30, pts: float = 34.0, minutes: float = 35.0) -> pd.DataFrame:
    return pd.DataFrame([{"player_name": "Luka Doncic", "team_abbreviation": "LAL", "gp": gp, "min": minutes,
                          "pts": pts, "reb": 8.0, "ast": 9.0, "fg3m": 3.0}])


def _props(over: float = 1.91, under: float = 1.91) -> dict:
    return {"pts": {"luka doncic": {"name": "Luka Doncic", "point": 30.5,
                                    "Over": {"odds": over, "market_fair_prob": 0.5},
                                    "Under": {"odds": under, "market_fair_prob": 0.5}}}}


def _evaluate(players: pd.DataFrame, props: dict) -> list:
    return evaluate_player_props(
        players, props, "g", "Denver Nuggets @ Los Angeles Lakers", {"LAL": 100.0}, 100.0,
        PlayerPropsModel(), BenterEngine(bankroll=1000.0),
    )


def test_evaluate_props_builds_both_sides_with_model_shrunk_to_line():
    bets = _evaluate(_player(), _props())
    assert {b.selection for b in bets} == {"Luka Doncic Over 30.5 PTS", "Luka Doncic Under 30.5 PTS"}
    over = next(b for b in bets if "Over" in b.selection)
    assert over.market == "player_prop"
    assert 0.5 < over.model_prob < 0.65


@pytest.mark.parametrize("players", [_player(gp=config.MIN_PROP_GAMES - 1), _player(minutes=config.MIN_PROP_MINUTES - 1)])
def test_evaluate_props_ignores_small_samples_and_low_minutes(players):
    assert _evaluate(players, _props()) == []


def _bet(game: str, selection: str, market: str, ev: float):
    engine = BenterEngine(bankroll=1000.0, min_edge=0.0, min_ev_percent=0.0, min_kelly_stake=0.0)
    return replace(engine.evaluate_bet(game, market, selection, 2.0, 0.55, 0.55), ev_percent=ev)


def test_dedupe_keeps_one_game_bet_and_one_prop_per_game_with_prop_cap():
    picks = [
        _bet("g1", "A ML", "moneyline", 5.0),
        _bet("g1", "B +2", "spread", 4.0),
        _bet("g1", "P1 Over 1.5 AST", "player_prop", 6.0),
        _bet("g1", "P2 Over 1.5 AST", "player_prop", 7.0),
    ] + [_bet(f"p{i}", f"P{i} Over 1.5 AST", "player_prop", 3.0 + i) for i in range(5)]
    result = deduplicate_picks_by_game(picks, max_picks=20, max_props=3)
    g1 = {p.selection for p in result if p.game_id == "g1"}
    assert "A ML" in g1 and "B +2" not in g1 and "P1 Over 1.5 AST" not in g1
    assert sum(p.market == "player_prop" for p in result) == 3


def test_simulator_matches_observed_margin_and_total_dispersion():
    engine = RatingEngine(random_seed=1)
    rng_home, rng_away = 115.0, 112.0
    cov_total = 2 * engine.dispersion ** 2 * (1 + engine.correlation)
    cov_margin = 2 * engine.dispersion ** 2 * (1 - engine.correlation)
    assert np.sqrt(cov_total) == pytest.approx(19.0, abs=0.5)
    assert np.sqrt(cov_margin) == pytest.approx(13.5, abs=0.5)
    assert engine.run_monte_carlo(rng_home, rng_away, expected_pace=101.0).expected_pace == 101.0


def test_analyze_game_has_no_total_bias_from_unbalanced_league_ratings():
    avg = {"pace": 100.0, "ortg": 110.0, "drtg": 114.0}
    team = {"pace": 100.0, "ortg": 110.0, "drtg": 114.0}
    res = RatingEngine().analyze_game(team, team, avg)
    assert res.expected_total == pytest.approx(224.0, abs=0.1)


def test_closing_capture_handles_player_props():
    df = pd.DataFrame([{"game_id": "g", "bet_type": "CORE_STRAIGHT", "status": "PENDING", "odds": 2.0,
                        "market": "player_prop", "selection": "Luka Doncic Over 30.5 PTS"}])
    games = [{"game_id": "g", "commence_time": "2026-11-05T23:00:00Z", "markets": {"player_props": _props()}}]
    out, updated = update_closing_columns(df, games, datetime(2026, 11, 5, 20, tzinfo=timezone.utc))
    assert updated == 1
    assert out.loc[0, "clv_percent"] == 0.0
    assert out.loc[0, "closing_point"] == 30.5
