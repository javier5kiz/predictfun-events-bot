"""
Minimal HTTP keep-alive server for Render free tier.

Render spins down free web services after 15 minutes of no inbound HTTP
requests. This tiny server listens on $PORT and responds 200 OK to any
request, keeping the service alive when pinged by an external monitor
(UptimeRobot, cron-job.org, etc.) every 5 minutes.

Runs in a background thread so it doesn't block the async trading loop.
"""

import os
import threading
import logging
from http.server import BaseHTTPRequestHandler, HTTPServer

from logger import get_logger

log = get_logger("keepalive")


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"predict-fun-bot: alive\n")

    def log_message(self, fmt, *args):
        # Suppress default access logs (too noisy for a health endpoint)
        pass


class KeepAliveServer:
    """Starts a background HTTP server on $PORT (Render injects this)."""

    def __init__(self):
        self.port = int(os.environ.get("PORT", 10000))
        self._server: HTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self):
        try:
            self._server = HTTPServer(("0.0.0.0", self.port), _Handler)
            self._thread = threading.Thread(
                target=self._server.serve_forever,
                daemon=True,
                name="keepalive-http",
            )
            self._thread.start()
            log.info(f"Keep-alive HTTP server listening on :{self.port}")
        except Exception as e:
            log.warning(f"Keep-alive server failed to start: {e}")

    def stop(self):
        if self._server:
            self._server.shutdown()
            self._server.server_close()
            log.info("Keep-alive server stopped")
