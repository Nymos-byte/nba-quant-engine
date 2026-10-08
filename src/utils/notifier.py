"""
Notifier Module for NBA Quant Engine.
Formats and transmits structured alerts to Telegram with differentiated visual formats
for Core Quant (+EV) and Satellite Parlay (Draftea/Fun).
Includes automatic message chunking (<= 4096 chars) and Markdown-to-plain-text fallback.
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


def split_telegram_message(text: str, max_chars: int = 3800) -> List[str]:
    """
    Splits a long message into chunks respecting line breaks
    to satisfy Telegram's strict 4096-character limit per message.
    """
    if len(text) <= max_chars:
        return [text]

    chunks: List[str] = []
    lines = text.split("\n")
    current_chunk: List[str] = []
    current_len = 0

    for line in lines:
        line_len = len(line) + 1  # includes newline
        if current_len + line_len > max_chars and current_chunk:
            chunks.append("\n".join(current_chunk))
            current_chunk = []
            current_len = 0
        current_chunk.append(line)
        current_len += line_len

    if current_chunk:
        chunks.append("\n".join(current_chunk))

    return chunks


class TelegramNotifier:
    """Formats and sends trading signals via Telegram Bot API or CLI console."""

    def __init__(
        self,
        bot_token: Optional[str] = None,
        chat_id: Optional[str] = None,
    ) -> None:
        self.bot_token = (bot_token or config.TELEGRAM_BOT_TOKEN or "").strip()
        self.chat_id = (chat_id or config.TELEGRAM_CHAT_ID or "").strip()

    def _send_text(self, text: str) -> bool:
        """
        Sends text via Telegram Bot API with automatic chunking and Markdown-to-plain-text fallback.
        """
        if not self.bot_token or not self.chat_id:
            logger.info("Telegram credentials not configured. Notification not sent.")
            return False

        chunks = split_telegram_message(text, max_chars=3800)
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        all_success = True

        for i, chunk in enumerate(chunks, start=1):
            payload = {
                "chat_id": self.chat_id,
                "text": chunk,
                "parse_mode": "Markdown",
            }
            try:
                res = requests.post(url, json=payload, timeout=12)
                if res.status_code == 200:
                    logger.info("Telegram message chunk %d/%d sent successfully.", i, len(chunks))
                    continue

                # If Markdown parsing failed or 400 Bad Request occurred, fallback to plain text
                logger.warning(
                    "Telegram error on chunk %d/%d (status %d): %s. Retrying in plain text...",
                    i, len(chunks), res.status_code, res.text
                )
                clean_chunk = chunk.replace("*", "").replace("`", "").replace("_", "")
                payload_plain = {
                    "chat_id": self.chat_id,
                    "text": clean_chunk,
                }
                res_plain = requests.post(url, json=payload_plain, timeout=12)
                if res_plain.status_code == 200:
                    logger.info("Telegram message chunk %d/%d delivered successfully as plain text.", i, len(chunks))
                else:
                    logger.error("Failed to send plain text chunk %d/%d (status %d): %s", i, len(chunks), res_plain.status_code, res_plain.text)
                    all_success = False
            except Exception as e:
                logger.error("Network exception while sending Telegram chunk %d/%d: %s", i, len(chunks), e)
                all_success = False

        return all_success

    def build_core_report(self, core_picks: List[BetEvaluation]) -> str:
        """Formats institutional core bets."""
        if not core_picks:
            return "🟢 *CORE QUANT - ALTA CONVICCIÓN*\n_Sin posiciones +EV que cumplan los filtros institucionales hoy._\n"

        lines = [
            "🟢 *CORE QUANT - ALTA CONVICCIÓN*",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        ]
        total_stake_fraction = sum(p.recommended_stake_fraction for p in core_picks)
        total_stake_amount = sum(p.recommended_stake_amount for p in core_picks)

        for p in core_picks:
            partido_str = p.matchup if p.matchup else p.game_id
            lines.extend([
                f"🏀 *Partido:* *{partido_str}*",
                f"🎯 *Mercado / Selección:* {p.market.upper()} ➔ *{p.selection}*",
                f"📊 *Cuota:* `{p.odds:.2f}` | *P. Justa:* `{p.final_prob*100:.1f}%`",
                f"📈 *Edge:* `+{p.edge*100:.2f}%` | *EV:* `+{p.ev_percent:.1f}%`",
                f"💰 *Stake Quarter-Kelly:* `{p.recommended_stake_fraction*100:.2f}%` (${p.recommended_stake_amount:.2f} MXN)",
                "─────────────────────────────",
            ])

        lines.append(
            f"⚖️ *Exposición Total Core:* `{total_stake_fraction*100:.2f}%` (${total_stake_amount:.2f} MXN)\n"
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
                "🎰 *ACTION PARLAY - DRAFTEA / FUN*\n"
                "_Sin combinada que satisfaga correlación positiva o cuota >= 8.0 hoy._\n"
            )

        current_spend = spent_this_week_mxn + parlay.stake_mxn
        lines = [
            "🎰 *ACTION PARLAY - DRAFTEA / FUN*",
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
        is_preseason: bool = config.IS_PRESEASON_MODE,
    ) -> str:
        """Assembles unified Telegram notification text."""
        banner = ""
        if is_preseason:
            banner = "⚠️ *SANDBOX / CALIBRACIÓN PRETEMPORADA - ROTACIONES NO OFICIALES - NO APOSTAR* ⚠️\n\n"

        header = (
            "🏀 *NBA QUANT TRADING ENGINE - REPORTE DIARIO*\n"
            "Arquitectura Core & Satellite (Benter + Kelly + Draftea)\n\n"
        )
        core_sec = self.build_core_report(core_picks)
        sat_sec = self.build_satellite_report(parlay, spent_this_week_mxn)
        return banner + header + core_sec + "\n" + sat_sec

    def send_notification(
        self,
        core_picks: List[BetEvaluation],
        parlay: Optional[ParlayTicket],
        spent_this_week_mxn: float = 0.0,
        is_preseason: bool = config.IS_PRESEASON_MODE,
    ) -> bool:
        """Sends message via Telegram if tokens configured, otherwise outputs to console."""
        message_text = self.generate_full_report(core_picks, parlay, spent_this_week_mxn, is_preseason=is_preseason)

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

        return self._send_text(message_text)

    def send_settlement_report(
        self,
        target_date: str,
        summary: dict,
        settled_rows: List[dict],
        historical_stats: Optional[dict] = None,
        is_preseason: bool = False,
    ) -> bool:
        """Formats and transmits the daily settlement report via Telegram, separating Core from Satellite bets."""
        won = summary.get("won", 0)
        lost = summary.get("lost", 0)
        push = summary.get("push", 0)
        total_pnl = summary.get("total_pnl", 0.0)
        roi = summary.get("roi_percent", 0.0)
        winrate = (won / (won + lost) * 100.0) if (won + lost) > 0 else 0.0

        pnl_symbol = "🟢 +" if total_pnl >= 0 else "🔴 -"

        lines = []
        if is_preseason:
            lines.append("⚠️ *[CALIBRACIÓN PRETEMPORADA - RESULTADOS DE PRUEBA]* ⚠️\n")

        lines.extend([
            "📊 *NBA QUANT ENGINE - LIQUIDACIÓN DE RESULTADOS*",
            f"📅 *Fecha:* `{target_date}`",
            "─────────────────────────────",
        ])

        # Separate Core Quant vs Soñadora Draftea in Daily Summary
        core = summary.get("core")
        sat = summary.get("satellite")

        if core and (core.get("won", 0) + core.get("lost", 0) + core.get("push", 0) > 0):
            c_won = core.get("won", 0)
            c_lost = core.get("lost", 0)
            c_push = core.get("push", 0)
            c_pnl = core.get("total_pnl", 0.0)
            c_roi = core.get("roi_percent", 0.0)
            c_wr = core.get("winrate", 0.0)
            c_sym = "🟢 +" if c_pnl >= 0 else "🔴 -"

            lines.extend([
                "🟢 *CORE QUANT (Benter + Kelly):*",
                f"• Récord: `{c_won}W - {c_lost}L - {c_push}P` ({c_wr:.1f}%)",
                f"• PnL: *{c_sym}${abs(c_pnl):.2f} MXN*",
                f"• ROI: *{c_roi:+.2f}%*",
                "─────────────────────────────",
            ])

        if sat and (sat.get("won", 0) + sat.get("lost", 0) + sat.get("push", 0) > 0):
            s_won = sat.get("won", 0)
            s_lost = sat.get("lost", 0)
            s_push = sat.get("push", 0)
            s_pnl = sat.get("total_pnl", 0.0)
            s_roi = sat.get("roi_percent", 0.0)
            s_wr = sat.get("winrate", 0.0)
            s_sym = "🟢 +" if s_pnl >= 0 else "🔴 -"

            lines.extend([
                "🎰 *SOÑADORA (Draftea / Parlay):*",
                f"• Récord: `{s_won}W - {s_lost}L - {s_push}P` ({s_wr:.1f}%)",
                f"• PnL: *{s_sym}${abs(s_pnl):.2f} MXN*",
                f"• ROI: *{s_roi:+.2f}%*",
                "─────────────────────────────",
            ])

        lines.extend([
            "⚖️ *BALANCE TOTAL DEL DÍA:*",
            f"• Récord Global: `{won}W - {lost}L - {push}P` ({winrate:.1f}%)",
            f"• PnL Neto: *{pnl_symbol}${abs(total_pnl):.2f} MXN*",
            f"• ROI del Día: *{roi:+.2f}%*",
            "─────────────────────────────",
            "*Detalle de Posiciones:*",
        ])

        # Separate positions into Core and Soñadora in detail list
        core_rows = [
            r for r in settled_rows
            if not (
                str(r.get("bet_type", "")).upper() == "SATELLITE_PARLAY"
                or str(r.get("market", "")).lower() == "player_props_parlay"
                or "PARLAY" in str(r.get("selection", "")).upper()
            )
        ]
        sat_rows = [
            r for r in settled_rows
            if (
                str(r.get("bet_type", "")).upper() == "SATELLITE_PARLAY"
                or str(r.get("market", "")).lower() == "player_props_parlay"
                or "PARLAY" in str(r.get("selection", "")).upper()
            )
        ]

        def _format_pos(row: dict) -> str:
            res = row.get("result", "PENDING")
            sel = row.get("selection", "")
            pnl_val = float(row.get("pnl", 0.0))
            curr = "MXN"
            icon = "✅" if res == "WON" else ("❌" if res == "LOST" else "⚪")
            pnl_str = f"+${pnl_val:.2f} {curr}" if pnl_val > 0 else (f"-${abs(pnl_val):.2f} {curr}" if pnl_val < 0 else f"$0.00 {curr}")
            return f"  {icon} *{sel}*: `{pnl_str}` ({res})"

        if core_rows:
            lines.append("🟢 _Core Quant:_")
            for r in core_rows:
                lines.append(_format_pos(r))

        if sat_rows:
            lines.append("🎰 _Soñadora Draftea:_")
            for r in sat_rows:
                lines.append(_format_pos(r))

        if not core_rows and not sat_rows:
            for r in settled_rows:
                lines.append(_format_pos(r))

        if historical_stats:
            season_lbl = historical_stats.get("season_type", "Pretemporada" if is_preseason else "Temporada Regular")
            lines.extend([
                "─────────────────────────────",
                f"🏦 *Métricas Históricas ({season_lbl}):*",
            ])

            h_core = historical_stats.get("core")
            h_sat = historical_stats.get("satellite")

            if h_core and (h_core.get("won", 0) + h_core.get("lost", 0) + h_core.get("push", 0) > 0):
                hc_won = h_core.get("won", 0)
                hc_lost = h_core.get("lost", 0)
                hc_push = h_core.get("push", 0)
                hc_pnl = h_core.get("total_pnl", 0.0)
                hc_roi = h_core.get("roi_percent", 0.0)
                hc_wr = h_core.get("winrate", 0.0)
                hc_sym = "🟢 +" if hc_pnl >= 0 else "🔴 -"

                lines.extend([
                    "🟢 *Core Quant Acumulado:*",
                    f"• Récord: `{hc_won}W - {hc_lost}L - {hc_push}P` ({hc_wr:.1f}%)",
                    f"• PnL: *{hc_sym}${abs(hc_pnl):.2f} MXN* | ROI: *{hc_roi:+.2f}%*",
                ])

            if h_sat and (h_sat.get("won", 0) + h_sat.get("lost", 0) + h_sat.get("push", 0) > 0):
                hs_won = h_sat.get("won", 0)
                hs_lost = h_sat.get("lost", 0)
                hs_push = h_sat.get("push", 0)
                hs_pnl = h_sat.get("total_pnl", 0.0)
                hs_roi = h_sat.get("roi_percent", 0.0)
                hs_wr = h_sat.get("winrate", 0.0)
                hs_sym = "🟢 +" if hs_pnl >= 0 else "🔴 -"

                lines.extend([
                    "🎰 *Soñadora Acumulado:*",
                    f"• Récord: `{hs_won}W - {hs_lost}L - {hs_push}P` ({hs_wr:.1f}%)",
                    f"• PnL: *{hs_sym}${abs(hs_pnl):.2f} MXN* | ROI: *{hs_roi:+.2f}%*",
                ])

            h_won = historical_stats.get("won", 0)
            h_lost = historical_stats.get("lost", 0)
            h_push = historical_stats.get("push", 0)
            h_pnl = historical_stats.get("total_pnl", 0.0)
            h_roi = historical_stats.get("roi_percent", 0.0)
            h_wr = (h_won / (h_won + h_lost) * 100.0) if (h_won + h_lost) > 0 else 0.0
            h_symbol = "🟢 +" if h_pnl >= 0 else "🔴 -"

            lines.extend([
                "⚖️ *Balance Global Acumulado:*",
                f"• Récord Total: `{h_won}W - {h_lost}L - {h_push}P` ({h_wr:.1f}%)",
                f"• PnL Neto: *{h_symbol}${abs(h_pnl):.2f} MXN* | ROI: *{h_roi:+.2f}%*",
            ])

        lines.append("─────────────────────────────")
        lines.append("🤖 _Liquidación automatizada vía settle_nba.py_\n")

        message_text = "\n".join(lines)

        # Print to console
        clean_text = message_text.replace("*", "").replace("`", "").replace("_", "")
        try:
            if hasattr(sys.stdout, "reconfigure"):
                sys.stdout.reconfigure(encoding="utf-8")
            print(clean_text)
        except Exception:
            print(clean_text.encode("ascii", errors="replace").decode("ascii"))

        return self._send_text(message_text)
