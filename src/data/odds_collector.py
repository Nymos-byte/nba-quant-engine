"""
Odds Collector and De-Vigging Engine for The Odds API and Sharp Bookmakers.
Fetches NBA markets: h2h (moneyline), spreads, totals, player props and strips bookmaker margins.
"""

from __future__ import annotations

import calendar
import json
import logging
import time
from datetime import date, datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

import config
from src.data.season_stats import normalize_name

logger = logging.getLogger(__name__)
if not logger.handlers:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")


def prop_events_budget(remaining: int, today: date) -> int:
    """
    Player-prop events affordable today, pacing the monthly quota (which resets on the 1st)
    after setting aside the credits score markets need until then.
    """
    days_left = calendar.monthrange(today.year, today.month)[1] - today.day + 1
    spendable = remaining - config.ODDS_API_CREDIT_RESERVE - days_left * config.ODDS_API_DAILY_BASE_CREDITS
    if spendable <= 0:
        return 0
    events = int(spendable / days_left / len(config.PROP_MARKETS))
    return min(events, config.MAX_PROP_EVENTS_PER_DAY)


def _two_way_fair(price_a: float, price_b: float) -> float:
    """De-vigged probability of the first side of a two-way market."""
    return devig_market([price_a, price_b])[0]


def devig_market(odds: List[float]) -> List[float]:
    """
    Strips the bookmaker margin (vig / overround) from a list of decimal odds.
    Formula:
        raw_prob_i = 1.0 / odd_i
        sum_raw = sum(raw_prob_j)
        fair_prob_i = raw_prob_i / sum_raw

    Returns:
        List of fair probabilities that strictly sum to 1.0.
    """
    if not odds:
        return []
    for odd in odds:
        if odd <= 1.0:
            raise ValueError(f"Decimal odds must be strictly greater than 1.0, got: {odd}")

    raw_probs = [1.0 / odd for odd in odds]
    total_implied = sum(raw_probs)
    if total_implied <= 0:
        raise ValueError("Invalid total implied probability.")

    return [p / total_implied for p in raw_probs]


class OddsCollector:
    """Connects to The Odds API, parses markets, and applies sharp de-vigging."""

    @property
    def base_url(self) -> str:
        sport = "basketball_nba_preseason" if config.IS_PRESEASON_MODE else "basketball_nba"
        return f"https://api.the-odds-api.com/v4/sports/{sport}"

    def __init__(
        self,
        api_key: Optional[str] = None,
        region: str = config.ODDS_API_REGION,
        cache_dir: Optional[Path] = None,
        ttl_hours: int = config.CACHE_TTL_HOURS,
    ) -> None:
        self.api_key: str = api_key or config.ODDS_API_KEY
        self.region: str = region
        self.cache_dir: Path = cache_dir or config.CACHE_DIR
        self.ttl_hours: int = ttl_hours
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _get_cache_path(self, key: str) -> Path:
        return self.cache_dir / f"odds_{key}.json"

    def _is_cache_valid(self, cache_file: Path) -> bool:
        if not cache_file.exists():
            return False
        try:
            mtime = cache_file.stat().st_mtime
            age_hours = (time.time() - mtime) / 3600.0
            return age_hours < self.ttl_hours
        except Exception:
            return False

    def get_game_odds(
        self,
        markets: str = "h2h,spreads,totals",
        force_refresh: bool = False,
    ) -> List[Dict[str, Any]]:
        """
        Retrieves current game lines (Moneyline, Spreads, Totals) and calculates
        de-vigged fair probabilities.
        """
        cache_key = f"games_{markets.replace(',', '_')}"
        cache_path = self._get_cache_path(cache_key)

        if not force_refresh and self._is_cache_valid(cache_path):
            try:
                with open(cache_path, "r", encoding="utf-8") as f:
                    logger.info("Loaded game odds from local cache (%s)", cache_path.name)
                    return json.load(f)
            except Exception as e:
                logger.warning("Error reading odds cache: %s", e)

        # Without a real market there are no bets: invented games must never reach the history
        if not self.api_key or self.api_key.strip() in ("", "YOUR_ODDS_API_KEY"):
            logger.error("No ODDS_API_KEY supplied. No games to evaluate.")
            return []

        try:
            url = f"{self.base_url}/odds"
            params = {
                "apiKey": self.api_key,
                "regions": self.region,
                "markets": markets,
                "oddsFormat": "decimal",
            }
            res = requests.get(url, params=params, timeout=10)
            res.raise_for_status()
            data = res.json()
            processed_data = self._parse_and_devig_odds_api(data)
            self._save_cache(cache_key, processed_data)
            return processed_data
        except Exception as e:
            logger.error("Failed to fetch odds from live API: %s. No games to evaluate.", type(e).__name__)
            return []

    def _remaining_credits(self) -> Optional[int]:
        """Reads the remaining quota from the events endpoint, which does not consume credits."""
        try:
            res = requests.get(f"{self.base_url}/events", params={"apiKey": self.api_key}, timeout=10)
            res.raise_for_status()
            return int(res.headers.get("x-requests-remaining", ""))
        except Exception as e:
            logger.warning("Could not read Odds API quota: %s", type(e).__name__)
            return None

    def get_commence_time(self, event_id: str) -> Optional[str]:
        """Tip-off time from the events endpoint, which does not consume credits."""
        try:
            res = requests.get(f"{self.base_url}/events", params={"apiKey": self.api_key}, timeout=10)
            res.raise_for_status()
            for event in res.json():
                if event.get("id") == event_id:
                    return event.get("commence_time")
        except Exception as e:
            logger.warning("Could not read event schedule: %s", type(e).__name__)
        return None

    def get_player_props(
        self,
        event_ids: List[str],
        categories: Optional[List[str]] = None,
        paced: bool = True,
    ) -> Dict[str, Dict[str, Any]]:
        """
        Real two-sided player lines per event: {event_id: {category: {normalized name: entry}}}.
        Costs one credit per market and event, and stops before touching the credit reserve.
        With paced=True, also limits events to today's share of the monthly quota.
        """
        if not config.ENABLE_PLAYER_PROPS or not self.api_key or not event_ids:
            return {}

        remaining = self._remaining_credits()
        markets = [m for m, c in config.PROP_MARKETS.items() if categories is None or c in categories]
        cost = len(markets)
        if remaining is None:
            return {}

        max_events = (
            prop_events_budget(remaining, datetime.now(config.TZ_INFO).date()) if paced else len(event_ids)
        )

        props: Dict[str, Dict[str, Any]] = {}
        for event_id in event_ids[:max_events]:
            if remaining - cost < config.ODDS_API_CREDIT_RESERVE:
                logger.warning("Player props skipped: %d credits left, reserve is %d.", remaining, config.ODDS_API_CREDIT_RESERVE)
                break
            try:
                res = requests.get(
                    f"{self.base_url}/events/{event_id}/odds",
                    params={
                        "apiKey": self.api_key,
                        "regions": self.region,
                        "markets": ",".join(markets),
                        "oddsFormat": "decimal",
                    },
                    timeout=10,
                )
                res.raise_for_status()
                remaining = int(res.headers.get("x-requests-remaining", remaining - cost))
                props[event_id] = self._parse_player_props(res.json())
            except Exception as e:
                logger.warning("Failed to fetch player props for event %s: %s", event_id, type(e).__name__)
        return props

    def _parse_player_props(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Price comes from the preferred book; fair probability is the consensus of books at that same line."""
        quotes: Dict[str, Dict[str, List[Dict[str, Any]]]] = {}
        for book in payload.get("bookmakers", []):
            for market in book.get("markets", []):
                category = config.PROP_MARKETS.get(market.get("key"))
                if not category:
                    continue
                sides: Dict[Tuple[str, float], Dict[str, float]] = {}
                for o in market.get("outcomes", []):
                    if o.get("point") is None or o.get("name") not in ("Over", "Under"):
                        continue
                    sides.setdefault((str(o.get("description", "")), float(o["point"])), {})[o["name"]] = float(o["price"])
                for (player, point), prices in sides.items():
                    if len(prices) == 2:
                        quotes.setdefault(category, {}).setdefault(normalize_name(player), []).append(
                            {"book": book.get("key"), "name": player, "point": point, **prices}
                        )

        def book_rank(quote: Dict[str, Any]) -> int:
            key = quote["book"]
            return config.PRICE_BOOKMAKERS.index(key) if key in config.PRICE_BOOKMAKERS else len(config.PRICE_BOOKMAKERS)

        parsed: Dict[str, Any] = {}
        for category, players in quotes.items():
            for player, player_quotes in players.items():
                chosen = min(player_quotes, key=book_rank)
                same_line = [q for q in player_quotes if q["point"] == chosen["point"]]
                fair_over = sum(_two_way_fair(q["Over"], q["Under"]) for q in same_line) / len(same_line)
                parsed.setdefault(category, {})[player] = {
                    "name": chosen["name"],
                    "point": chosen["point"],
                    "Over": {"odds": chosen["Over"], "market_fair_prob": round(fair_over, 4)},
                    "Under": {"odds": chosen["Under"], "market_fair_prob": round(1.0 - fair_over, 4)},
                }
        return parsed

    def _save_cache(self, key: str, data: Any) -> None:
        cache_path = self._get_cache_path(key)
        try:
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning("Could not write odds cache: %s", e)

    @staticmethod
    def _consensus_fair(
        bookmakers: List[Dict[str, Any]],
        market_key: str,
        outcome: Dict[str, Any],
    ) -> Tuple[float, float]:
        """Mean de-vigged probability of an outcome across every book quoting the same line."""
        fairs: List[float] = []
        for book in bookmakers:
            for m in book.get("markets", []):
                outcomes = m.get("outcomes", [])
                if m.get("key") != market_key or len(outcomes) != 2:
                    continue
                for i, o in enumerate(outcomes):
                    if o.get("name") == outcome.get("name") and o.get("point") == outcome.get("point"):
                        fairs.append(_two_way_fair(float(o["price"]), float(outcomes[1 - i]["price"])))
        fair = sum(fairs) / len(fairs)
        return fair, 1.0 - fair

    def _parse_and_devig_odds_api(self, raw_games: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Extracts markets from live Odds API JSON and computes de-vigged fair probabilities."""
        parsed_games = []
        now_utc = datetime.now(timezone.utc)
        max_future_utc = now_utc + timedelta(hours=36)
        min_past_utc = now_utc - timedelta(hours=4)

        for g in raw_games:
            game_id = g.get("id")
            home_team = g.get("home_team")
            away_team = g.get("away_team")
            commence_time = g.get("commence_time")

            # Slate Filter: strictly keep games occurring within the current 36-hour window
            if commence_time:
                try:
                    c_dt = datetime.fromisoformat(commence_time.replace("Z", "+00:00"))
                    if c_dt < min_past_utc or c_dt > max_future_utc:
                        continue
                except Exception:
                    pass

            bookmakers = g.get("bookmakers", [])
            if not bookmakers:
                continue

            # Price from the preferred book; fair probability from the consensus of all books
            selected_book = bookmakers[0]
            for preferred in reversed(config.PRICE_BOOKMAKERS):
                for b in bookmakers:
                    if b.get("key") == preferred:
                        selected_book = b

            markets_map: Dict[str, Any] = {}
            for m in selected_book.get("markets", []):
                key = m.get("key")
                outcomes = m.get("outcomes", [])
                if key == "h2h" and len(outcomes) == 2:
                    o1, o2 = outcomes[0], outcomes[1]
                    price1, price2 = float(o1["price"]), float(o2["price"])
                    fair1, fair2 = self._consensus_fair(bookmakers, key, o1)
                    markets_map["h2h"] = {
                        o1["name"]: {"odds": price1, "market_fair_prob": fair1},
                        o2["name"]: {"odds": price2, "market_fair_prob": fair2},
                    }
                elif key == "spreads" and len(outcomes) == 2:
                    o1, o2 = outcomes[0], outcomes[1]
                    price1, price2 = float(o1["price"]), float(o2["price"])
                    fair1, fair2 = self._consensus_fair(bookmakers, key, o1)
                    markets_map["spreads"] = {
                        o1["name"]: {"point": float(o1.get("point", 0.0)), "odds": price1, "market_fair_prob": fair1},
                        o2["name"]: {"point": float(o2.get("point", 0.0)), "odds": price2, "market_fair_prob": fair2},
                    }
                elif key == "totals" and len(outcomes) == 2:
                    o1, o2 = outcomes[0], outcomes[1]
                    price1, price2 = float(o1["price"]), float(o2["price"])
                    fair1, fair2 = self._consensus_fair(bookmakers, key, o1)
                    markets_map["totals"] = {
                        o1["name"]: {"point": float(o1.get("point", 220.0)), "odds": price1, "market_fair_prob": fair1},
                        o2["name"]: {"point": float(o2.get("point", 220.0)), "odds": price2, "market_fair_prob": fair2},
                    }

            parsed_games.append({
                "game_id": game_id,
                "home_team": home_team,
                "away_team": away_team,
                "commence_time": commence_time,
                "markets": markets_map,
            })
        return parsed_games
