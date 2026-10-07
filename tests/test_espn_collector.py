import json
from unittest.mock import MagicMock, patch
import pytest

from src.data.espn_collector import ESPNDataCollector
from src.models.rating_engine import calculate_game_metrics


@pytest.fixture
def mock_espn_scoreboard():
    return {
        "events": [
            {
                "id": "401704901",
                "name": "Denver Nuggets at Oklahoma City Thunder",
                "shortName": "DEN @ OKC",
                "date": "2026-10-07T23:00Z",
                "status": {
                    "type": {
                        "name": "STATUS_FINAL",
                        "completed": True,
                        "detail": "Final",
                    }
                },
                "competitions": [
                    {
                        "competitors": [
                            {
                                "homeAway": "home",
                                "score": "116",
                                "team": {
                                    "id": "25",
                                    "displayName": "Oklahoma City Thunder",
                                    "abbreviation": "OKC",
                                },
                            },
                            {
                                "homeAway": "away",
                                "score": "112",
                                "team": {
                                    "id": "7",
                                    "displayName": "Denver Nuggets",
                                    "abbreviation": "DEN",
                                },
                            },
                        ]
                    }
                ],
            },
            {
                "id": "401704902",
                "name": "Minnesota Timberwolves at Indiana Pacers",
                "shortName": "MIN @ IND",
                "date": "2026-10-08T00:00Z",
                "status": {
                    "type": {
                        "name": "STATUS_SCHEDULED",
                        "completed": False,
                        "detail": "7:00 PM EST",
                    }
                },
                "competitions": [
                    {
                        "competitors": [
                            {
                                "homeAway": "home",
                                "score": "0",
                                "team": {
                                    "id": "11",
                                    "displayName": "Indiana Pacers",
                                    "abbreviation": "IND",
                                },
                            },
                            {
                                "homeAway": "away",
                                "score": "0",
                                "team": {
                                    "id": "16",
                                    "displayName": "Minnesota Timberwolves",
                                    "abbreviation": "MIN",
                                },
                            },
                        ]
                    }
                ],
            },
        ]
    }


@pytest.fixture
def mock_espn_summary():
    return {
        "boxscore": {
            "teams": [
                {
                    "team": {"id": "25", "displayName": "Oklahoma City Thunder", "abbreviation": "OKC"},
                    "statistics": [
                        {"name": "points", "displayValue": "116"},
                        {"name": "fieldGoalsMade-fieldGoalsAttempted", "displayValue": "42-88"},
                        {"name": "threePointFieldGoalsMade-threePointFieldGoalsAttempted", "displayValue": "14-36"},
                        {"name": "freeThrowsMade-freeThrowsAttempted", "displayValue": "18-22"},
                        {"name": "offensiveRebounds", "displayValue": "11"},
                        {"name": "defensiveRebounds", "displayValue": "33"},
                        {"name": "assists", "displayValue": "27"},
                        {"name": "turnovers", "displayValue": "12"},
                    ],
                },
                {
                    "team": {"id": "7", "displayName": "Denver Nuggets", "abbreviation": "DEN"},
                    "statistics": [
                        {"name": "points", "displayValue": "112"},
                        {"name": "fieldGoalsMade-fieldGoalsAttempted", "displayValue": "40-85"},
                        {"name": "threePointFieldGoalsMade-threePointFieldGoalsAttempted", "displayValue": "12-32"},
                        {"name": "freeThrowsMade-freeThrowsAttempted", "displayValue": "20-25"},
                        {"name": "offensiveRebounds", "displayValue": "9"},
                        {"name": "defensiveRebounds", "displayValue": "31"},
                        {"name": "assists", "displayValue": "26"},
                        {"name": "turnovers", "displayValue": "14"},
                    ],
                },
            ],
            "players": [
                {
                    "team": {"abbreviation": "OKC"},
                    "statistics": [
                        {
                            "names": ["MIN", "FGM-A", "3PM-A", "FTM-A", "OREB", "DREB", "REB", "AST", "STL", "BLK", "TO", "PF", "+/-", "PTS"],
                            "athletes": [
                                {
                                    "athlete": {"displayName": "Shai Gilgeous-Alexander"},
                                    "stats": ["34", "11-20", "2-5", "8-9", "1", "5", "6", "8", "2", "1", "3", "2", "+8", "32"],
                                },
                                {
                                    "athlete": {"displayName": "Chet Holmgren"},
                                    "stats": ["31", "7-13", "3-6", "2-2", "2", "8", "10", "3", "1", "3", "1", "3", "+5", "19"],
                                },
                            ],
                        }
                    ],
                },
                {
                    "team": {"abbreviation": "DEN"},
                    "statistics": [
                        {
                            "names": ["MIN", "FGM-A", "3PM-A", "FTM-A", "OREB", "DREB", "REB", "AST", "STL", "BLK", "TO", "PF", "+/-", "PTS"],
                            "athletes": [
                                {
                                    "athlete": {"displayName": "Nikola Jokic"},
                                    "stats": ["36", "10-18", "1-3", "6-7", "3", "11", "14", "10", "1", "1", "4", "3", "-2", "27"],
                                },
                                {
                                    "athlete": {"displayName": "Jamal Murray"},
                                    "stats": ["35", "8-19", "3-8", "2-2", "0", "4", "4", "6", "1", "0", "2", "2", "-6", "21"],
                                },
                            ],
                        }
                    ],
                },
            ],
        }
    }


def test_espn_get_daily_scoreboard(mock_espn_scoreboard, tmp_path):
    collector = ESPNDataCollector(cache_dir=tmp_path)
    with patch("requests.get") as mock_get:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = mock_espn_scoreboard
        mock_get.return_value = mock_resp

        games = collector.get_daily_scoreboard("2026-10-07")
        assert len(games) == 2

        # Final game
        g1 = games[0]
        assert g1["event_id"] == "401704901"
        assert g1["is_completed"] is True
        assert g1["home_team"]["abbrev"] == "OKC"
        assert g1["home_team"]["score"] == 116.0
        assert g1["away_team"]["abbrev"] == "DEN"
        assert g1["away_team"]["score"] == 112.0

        # Scheduled game
        g2 = games[1]
        assert g2["event_id"] == "401704902"
        assert g2["is_completed"] is False


def test_espn_get_game_boxscore(mock_espn_summary, tmp_path):
    collector = ESPNDataCollector(cache_dir=tmp_path)
    with patch("requests.get") as mock_get:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = mock_espn_summary
        mock_get.return_value = mock_resp

        data = collector.get_game_boxscore("401704901")
        teams = data["teams"]
        players = data["players"]

        assert "OKC" in teams
        assert teams["OKC"]["pts"] == 116.0
        assert teams["OKC"]["fga"] == 88.0
        assert teams["OKC"]["fgm"] == 42.0
        assert teams["OKC"]["fg3m"] == 14.0
        assert teams["OKC"]["fta"] == 22.0
        assert teams["OKC"]["oreb"] == 11.0
        assert teams["OKC"]["to"] == 12.0

        assert "DEN" in teams
        assert teams["DEN"]["pts"] == 112.0
        assert teams["DEN"]["fga"] == 85.0

        # Players
        assert "nikola jokic" in players
        jokic = players["nikola jokic"]
        assert jokic["pts"] == 27.0
        assert jokic["reb"] == 14.0
        assert jokic["ast"] == 10.0
        assert jokic["team"] == "DEN"

        assert "shai gilgeous-alexander" in players
        sga = players["shai gilgeous-alexander"]
        assert sga["pts"] == 32.0
        assert sga["ast"] == 8.0


def test_dean_oliver_four_factors_calculation():
    # Test formula: Possessions = FGA + 0.44 * FTA - OREB + TO
    team_stats = {
        "fga": 88.0,
        "fta": 22.0,
        "oreb": 11.0,
        "to": 12.0,
        "pts": 116.0,
        "fgm": 42.0,
        "fg3m": 14.0,
    }
    opponent_stats = {
        "fga": 85.0,
        "fta": 25.0,
        "oreb": 9.0,
        "dreb": 31.0,
        "to": 14.0,
        "pts": 112.0,
    }

    # Expected possessions: 88 + 0.44 * 22 - 11 + 12 = 88 + 9.68 - 11 + 12 = 98.68
    metrics = calculate_game_metrics(team_stats, opponent_stats, game_minutes=48.0)

    assert metrics["possessions"] == 98.68
    assert metrics["pace"] == 98.68  # 48 min game -> Pace == Possessions
    # ORtg = 100 * (116 / 98.68) = 117.55
    assert metrics["ortg"] == pytest.approx(117.55, abs=0.1)
    # Opp possessions: 85 + 0.44 * 25 - 9 + 14 = 85 + 11 - 9 + 14 = 101.0
    assert metrics["opponent_possessions"] == 101.0
    # DRtg = 100 * (112 / 101.0) = 110.89
    assert metrics["drtg"] == pytest.approx(110.89, abs=0.1)
    # eFG% = (42 + 0.5 * 14) / 88 = 49 / 88 = 0.5568
    assert metrics["efg_pct"] == pytest.approx(0.5568, abs=0.001)
