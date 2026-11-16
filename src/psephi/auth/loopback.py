"""Loopback redirect for the authorization code flow (RFC 8252, 7.3): a one-shot HTTP server
on 127.0.0.1 and a free port that receives `?code=…&state=…&iss=…` from the browser."""

from __future__ import annotations

import hmac
import html
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlsplit

from psephi.errors import CliError

CALLBACK_PATH = "/callback"
HOST = "127.0.0.1"  # not "localhost": hosts files and DNS can point it elsewhere

_PAGE = """<!doctype html><html lang="en"><meta charset="utf-8"><title>Psephi</title>
<body style="font-family:system-ui,sans-serif;max-width:32rem;margin:4rem auto;padding:0 1rem">
<h1 style="font-size:1.4rem">{title}</h1><p>{text}</p></body></html>"""


@dataclass(frozen=True)
class Callback:
    code: str | None = None
    error: str | None = None


class _Server(HTTPServer):
    expected_state: str
    issuer: str
    require_iss: bool
    result: Callback | None = None


def check_callback(query: str, state: str, issuer: str, require_iss: bool) -> Callback:
    """Validate the redirect: state (CSRF), iss (RFC 9207 mix-up defence), then code or error."""
    params = {k: v[0] for k, v in parse_qs(query, keep_blank_values=True).items()}
    got_state = params.get("state", "")
    if not hmac.compare_digest(got_state.encode(), state.encode()):
        return Callback(error="the answer does not belong to this sign-in (state mismatch)")
    iss = params.get("iss")
    if (require_iss or iss is not None) and (iss or "").rstrip("/") != issuer:
        return Callback(error=f"the answer came from another server (iss={iss!r})")
    if "error" in params:
        if params["error"] == "access_denied":
            return Callback(error="sign-in was declined in the browser")
        description = params.get("error_description")
        text = params["error"] + (f": {description}" if description else "")
        return Callback(error=f"the authorization server refused: {text}")
    if not params.get("code"):
        return Callback(error="the answer has no authorization code")
    return Callback(code=params["code"])


class _Handler(BaseHTTPRequestHandler):
    server: _Server

    def do_GET(self) -> None:
        parts = urlsplit(self.path)
        if parts.path != CALLBACK_PATH:
            self._reply(404, "Not found", "This address only receives the Psephi sign-in.")
            return
        if self.server.result is not None:
            self._reply(409, "Already done", "This sign-in has already finished.")
            return
        result = check_callback(
            parts.query, self.server.expected_state, self.server.issuer, self.server.require_iss
        )
        self.server.result = result
        if result.code:
            self._reply(200, "Signed in", "You can close this tab and go back to the terminal.")
        else:
            self._reply(400, "Sign-in failed", f"{result.error}. Go back to the terminal.")

    def _reply(self, status: int, title: str, text: str) -> None:
        body = _PAGE.format(title=html.escape(title), text=html.escape(text)).encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        """Silent: the query string carries the code."""


class LoopbackReceiver:
    def __init__(self, state: str, issuer: str, require_iss: bool):
        self._server = _Server((HOST, 0), _Handler)
        self._server.expected_state = state
        self._server.issuer = issuer
        self._server.require_iss = require_iss
        self._server.timeout = 0.5

    @property
    def redirect_uri(self) -> str:
        return f"http://{HOST}:{self._server.server_port}{CALLBACK_PATH}"

    def wait(self, timeout: float) -> str:
        """The authorization code, or CliError on refusal, mismatch or timeout."""
        deadline = time.monotonic() + timeout
        while self._server.result is None:
            if time.monotonic() >= deadline:
                raise CliError(
                    "no answer from the browser in time. Run `psephi login` again, "
                    "or `psephi login --device` if the browser is on another machine"
                )
            self._server.handle_request()
        result = self._server.result
        if result.error or not result.code:
            raise CliError(result.error or "the answer has no authorization code")
        return result.code

    def close(self) -> None:
        self._server.server_close()

    def __enter__(self) -> LoopbackReceiver:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
