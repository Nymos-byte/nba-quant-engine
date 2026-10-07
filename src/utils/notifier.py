"""
Notifier Module for NBA Quant Engine.
Formats and transmits structured alerts to Telegram with differentiated visual formats
for Core Quant (+EV) and Satellite Parlay (Draftea/Fun).
"""

from __future__ import annotations

import logging
import sys
from typing import List, Optional

import requests

import config
from src.models.benter_engine import BetEvaluation
from src.models.parlay_builder import ParlayTicket

logger = logging.getLogger(__name__)


class TelegramNotifier:
    """Formats and sends trading signals via Telegram Bot API or CLI console."""

    def __init__(
        self,
        bot_token: Optional[str] = None,
        chat_id: Optional[str] = None,
    ) -> None:
        self.bot_token = bot_token or config.TELEGRAM_BOT_TOKEN
        self.chat_id = chat_id or config.TELEGRAM_CHAT_ID

    def build_core_report(self, core_picks: List[BetEvaluation]) -> str:
        """Formats institutional core bets."""
        if not core_picks:
            return "🟢 *[CORE QUANT - ALTA CONVICCIÓN]*\n_Sin posiciones +EV que cumplan los filtros institucionales hoy._\n"

        lines = [
            "🟢 *[CORE QUANT - ALTA CONVICCIÓN]*",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        ]
        total_stake_fraction = sum(p.recommended_stake_fraction for p in core_picks)
        total_stake_amount = sum(p.recommended_stake_amount for p in core_picks)

        for p in core_picks:
            lines.extend([
                f"🏀 *Partido:* `{p.game_id}`",
                f"🎯 *Mercado / Selección:* {p.market.upper()} ➔ *{p.selection}*",
                f"📊 *Cuota:* `{p.odds:.2f}` | *P. Justa:* `{p.final_prob*100:.1f}%`",
                f"📈 *Edge:* `+{p.edge*100:.2f}%` | *EV:* `+{p.ev_percent:.1f}%`",
                f"💰 *Stake Quarter-Kelly:* `{p.recommended_stake_fraction*100:.2f}%` (${p.recommended_stake_amount:.2f} USD)",
                "─────────────────────────────",
            ])

        lines.append(
            f"⚖️ *Exposición Total Core:* `{total_stake_fraction*100:.2f}%` (${total_stake_amount:.2f} USD)\n"
        )
        return "\n".join(lines)

    def build_satellite_report(
        self,
        parlay: Optional[ParlayTicket],
        spent_this_week_mxn: float = 0.0,
    ) -> str:
        """Formats recreational Draftea parlay ticket."""
        if not parlay:
            return (
                "🎰 *[ACTION PARLAY - DRAFTEA / FUN]*\n"
                "_Sin combinada que satisfaga correlación positiva o rango [8.0 - 25.0] hoy._\n"
            )

        current_spend = spent_this_week_mxn + parlay.stake_mxn
        lines = [
            "🎰 *[ACTION PARLAY - DRAFTEA / FUN]*",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            f"🎟️ *Ticket ID:* `{parlay.ticket_id}`",
            f"🔥 *Cuota Total:* `{parlay.combined_odds:.2f}`",
            f"💵 *Stake Sugerido:* `${parlay.stake_mxn:.2f} MXN` (Fijo)",
            f"🏆 *Retorno Potencial:* `${parlay.potential_payout_mxn:.2f} MXN`",
            f"🧠 *Correlación / Rationale:* _{parlay.correlation_rationale}_",
            "─────────────────────────────",
            "*Patas del Boleto:*",
        ]

        for i, leg in enumerate(parlay.legs, start=1):
            lines.append(
                f"  {i}. *{leg.player_name}* ({leg.team_abbreviation}) vs {leg.opponent} ➔ "
                f"*{leg.direction} {leg.line} {leg.category.upper()}* @ `{leg.odds:.2f}` ({leg.correlation_tag})"
            )

        lines.extend([
            "─────────────────────────────",
            f"💳 *Presupuesto Semanal:* `${current_spend:.2f} / ${config.WEEKLY_FUN_BUDGET:.2f} MXN` gastado.",
            "⚠️ *Regla:* Módulo satélite recreativo con riesgo estrictamente acotado.\n",
        ])
        return "\n".join(lines)

    def generate_full_report(
        self,
        core_picks: List[BetEvaluation],
        parlay: Optional[ParlayTicket],
        spent_this_week_mxn: float = 0.0,
    ) -> str:
        """Assembles unified Telegram notification text."""
        header = (
            "🏀 *NBA QUANT TRADING ENGINE - REPORTE DIARIO*\n"
            "Arquitectura Core & Satellite (Benter + Kelly + Draftea)\n\n"
        )
        core_sec = self.build_core_report(core_picks)
        sat_sec = self.build_satellite_report(parlay, spent_this_week_mxn)
        return header + core_sec + "\n" + sat_sec

    def send_notification(
        self,
        core_picks: List[BetEvaluation],
        parlay: Optional[ParlayTicket],
        spent_this_week_mxn: float = 0.0,
    ) -> bool:
        """Sends message via Telegram if tokens configured, otherwise outputs to console."""
        message_text = self.generate_full_report(core_picks, parlay, spent_this_week_mxn)

        # Always print to console safely
        print("\n" + "=" * 60)
        print("TELEGRAM DISPATCH PREVIEW / CONSOLE LOG")
        print("=" * 60)
        clean_text = message_text.replace("*", "").replace("`", "").replace("_", "")
        try:
            if hasattr(sys.stdout, "reconfigure"):
                sys.stdout.reconfigure(encoding="utf-8")
            print(clean_text)
        except Exception:
            safe_text = clean_text.encode("ascii", errors="replace").decode("ascii")
            print(safe_text)
        print("=" * 60 + "\n")

        if not self.bot_token or not self.chat_id:
            logger.info("Telegram credentials not configured. Printed notification to console.")
            return False

        try:
            url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
            payload = {
                "chat_id": self.chat_id,
                "text": message_text,
                "parse_mode": "Markdown",
            }
            res = requests.post(url, json=payload, timeout=8)
            res.raise_for_status()
            logger.info("Telegram notification sent successfully.")
            return True
        except Exception as e:
            logger.warning("Failed to send Telegram message: %s", e)
            return False
