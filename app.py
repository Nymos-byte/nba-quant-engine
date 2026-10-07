"""
Lightweight Webhook Server for nba-quant-engine.
Enables execution via external HTTP cron services like cron-job.org, Render, or Railway.
Uses standard library http.server (zero additional dependencies required).
"""

from __future__ import annotations

import json
import logging
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

import config
from main import run_pipeline
from settle_nba import SettlementEngine

logger = logging.getLogger("webhook_server")
if not logger.handlers:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

PORT = int(os.getenv("PORT", "8080"))
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")  # Optional auth key for cron-job.org


class WebhookHandler(BaseHTTPRequestHandler):
    """Handles HTTP requests from cron-job.org and webhooks."""

    def _send_json(self, status_code: int, data: dict) -> None:
        response_bytes = json.dumps(data, indent=2).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(response_bytes)))
        self.end_headers()
        self.wfile.write(response_bytes)

    def _verify_auth(self, parsed_url) -> bool:
        if not WEBHOOK_SECRET:
            return True
        query_params = parse_qs(parsed_url.query)
        auth_param = query_params.get("key", [""])[0]
        header_param = self.headers.get("X-API-Key", "")
        return (auth_param == WEBHOOK_SECRET) or (header_param == WEBHOOK_SECRET)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path

        if path in ("/", "/health"):
            self._send_json(200, {
                "status": "online",
                "service": "nba-quant-engine-webhook",
                "version": "1.0.0",
                "endpoints": [
                    "/run-pipeline (triggers daily predictions & Telegram alert)",
                    "/settle (triggers daily bets settlement)",
                ]
            })
            return

        if not self._verify_auth(parsed):
            self._send_json(401, {"error": "Unauthorized. Provide valid 'key' query parameter or X-API-Key header."})
            return

        query_params = parse_qs(parsed.query)
        date_str = query_params.get("date", [None])[0]

        if path == "/run-pipeline":
            logger.info("Triggered /run-pipeline via HTTP webhook (date=%s)", date_str)
            # Run in worker thread or synchronously
            try:
                run_pipeline(date_str=date_str)
                self._send_json(200, {
                    "status": "SUCCESS",
                    "action": "pipeline",
                    "message": "Daily predictions generated and notified via Telegram."
                })
            except Exception as e:
                logger.error("Error executing pipeline: %s", e)
                self._send_json(500, {"status": "ERROR", "message": str(e)})

        elif path == "/settle":
            logger.info("Triggered /settle via HTTP webhook (date=%s)", date_str)
            try:
                engine = SettlementEngine()
                res = engine.settle_date(date_str=date_str or "", force=False)
                self._send_json(200, {
                    "status": "SUCCESS",
                    "action": "settle",
                    "result": res
                })
            except Exception as e:
                logger.error("Error executing settlement: %s", e)
                self._send_json(500, {"status": "ERROR", "message": str(e)})

        else:
            self._send_json(404, {"error": f"Endpoint '{path}' not found."})

    def do_POST(self) -> None:
        self.do_GET()


def run_server() -> None:
    server = HTTPServer(("0.0.0.0", PORT), WebhookHandler)
    logger.info("NBA Quant Webhook Server listening on port %d...", PORT)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Server shutting down.")
        server.server_close()


if __name__ == "__main__":
    run_server()
