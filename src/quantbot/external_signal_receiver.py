from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import hmac
import json
import os
import socket
import threading

from .external_execution import append_external_signal, parse_external_signal

_SERVER: ThreadingHTTPServer | None = None
_DATABASES: set[Path] = set()
_DATABASES_LOCK = threading.Lock()

def start_external_signal_receiver(database: str | Path, *, port: int = 8765) -> ThreadingHTTPServer:
    """Start the loopback receiver shared by PineTS, Edge and authenticated Webhook."""
    global _SERVER
    database_path = Path(database)
    with _DATABASES_LOCK:
        _DATABASES.add(database_path)
    if _SERVER is not None:
        return _SERVER
    secret = os.environ.get("QUANTBOT_EXECUTION_WEBHOOK_SECRET", "")

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_args) -> None:
            return

        def reply(self, status: int, payload: dict) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            reason = "Accepted" if status == 202 else "Bad Request" if status == 400 else "Internal Server Error"
            head = (f"HTTP/1.1 {status} {reason}\r\nContent-Type: application/json; charset=utf-8\r\n"
                    f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n").encode("ascii")
            self.connection.settimeout(1.0)
            self.connection.sendall(head + body)
            try:
                self.connection.shutdown(socket.SHUT_WR)
            except OSError:
                pass
            self.close_connection = True

        def do_POST(self) -> None:
            if self.path != "/v1/execution-signal":
                self.send_error(404); return
            if secret and not hmac.compare_digest(
                    self.headers.get("X-QuantBot-Secret", ""), secret):
                self.send_error(401); return
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if size <= 0 or size > 65536: raise ValueError("invalid body size")
                payload = json.loads(self.rfile.read(size).decode("utf-8"))
                signal = parse_external_signal(payload)
                self.reply(202, {"accepted": True, "event_id": signal.event_id})
                with _DATABASES_LOCK:
                    databases = tuple(_DATABASES)
                for target_database in databases:
                    append_external_signal(target_database, payload)
            except (ValueError, TypeError, json.JSONDecodeError) as exc:
                self.reply(400, {"accepted": False, "error": str(exc)})
            except Exception as exc:
                self.reply(500, {"accepted": False, "error_type": type(exc).__name__})

    _SERVER = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=_SERVER.serve_forever, name="external-signal-receiver", daemon=True).start()
    return _SERVER
