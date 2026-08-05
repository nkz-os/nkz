"""
Lightweight HTTP server exposing /healthz and /readyz for weather-worker.

Deliberately separate from the Prometheus metrics server
(prometheus_client.start_http_server, port METRICS_PORT / path /metrics) so
that existing metrics scraping and the current K8s livenessProbe (GET
/metrics :METRICS_PORT) are completely untouched by this change.

- /healthz -> process liveness: 200 as long as this HTTP server is answering.
- /readyz  -> thread liveness: 200 when every daemon thread that was started
  (parcel engine, meteoalarm engine, main loop) has called heartbeat()
  recently enough; 503 naming the stale thread(s) otherwise. Wire a K8s
  readinessProbe (or repoint the livenessProbe) at this path to get pods
  actually restarted when a worker thread dies or hangs silently.
"""

from __future__ import annotations

import json
import logging
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from typing import Dict

from weather_worker.heartbeat import check_threads_healthy

logger = logging.getLogger(__name__)


def _make_handler(max_staleness_per_thread: Dict[str, float], startup_grace_seconds: float):
    class HealthHandler(BaseHTTPRequestHandler):
        def _write_json(self, status_code: int, payload: dict) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status_code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):  # noqa: N802 - required stdlib handler method name
            if self.path == "/healthz":
                self._write_json(200, {"status": "ok"})
                return

            if self.path == "/readyz":
                healthy, details = check_threads_healthy(
                    max_staleness_per_thread,
                    startup_grace_seconds=startup_grace_seconds,
                )
                self._write_json(
                    200 if healthy else 503,
                    {"status": "ready" if healthy else "not_ready", "threads": details},
                )
                return

            self._write_json(404, {"error": "not found"})

        def log_message(self, format, *args):  # noqa: A002 - stdlib signature
            logger.debug("health-server: " + format, *args)

    return HealthHandler


def start_health_server(
    host: str,
    port: int,
    max_staleness_per_thread: Dict[str, float],
    startup_grace_seconds: float = 180.0,
) -> ThreadingHTTPServer:
    """Start the /healthz + /readyz server in a background daemon thread.

    Returns the running server (mainly useful for tests to call
    server_close()).
    """
    handler_cls = _make_handler(max_staleness_per_thread, startup_grace_seconds)
    httpd = ThreadingHTTPServer((host, port), handler_cls)
    thread = Thread(target=httpd.serve_forever, daemon=True, name="health-server")
    thread.start()
    logger.info(f"Health server started on {host}:{port} (/healthz, /readyz)")
    return httpd
