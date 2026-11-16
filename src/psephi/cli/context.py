"""What a command gets: the API client, output in the chosen format, confirmations.

Data goes to stdout (table, JSON or CSV); messages for the person go to stderr, so
`psephi … --format=json | jq` stays clean.
"""

from __future__ import annotations

import csv
import json
import os
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import IO, Any

import httpx

from psephi.auth.oauth import OAuth
from psephi.auth.session import EnvToken, KeyringSession, token_source
from psephi.client import Api, new_http
from psephi.endpoints import Endpoint
from psephi.errors import CliError, Refused

FORMATS = ("table", "json", "csv")


def is_money(value: Any) -> bool:
    return isinstance(value, Mapping) and set(value) == {"amount", "currency"}


def cell(value: Any) -> str:
    """One value as text for a table or CSV cell. Money stays a decimal string."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "yes" if value else "no"
    if is_money(value):
        return f"{value['amount']} {value['currency']}"
    if isinstance(value, list):
        return ",".join(cell(v) for v in value)
    if isinstance(value, Mapping):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


@dataclass
class Context:
    api_url: str
    stdin: IO
    stdout: IO
    stderr: IO
    format: str = "table"
    yes: bool = False
    dry_run: bool = False
    quiet: bool = False
    interactive: bool = False
    environ: Mapping[str, str] = field(default_factory=lambda: dict(os.environ))
    http_factory: Callable[[], httpx.Client] = new_http
    sleep: Callable[[float], None] | None = None
    open_browser: Callable[[str], bool] | None = None
    _http: httpx.Client | None = None
    _tokens: EnvToken | KeyringSession | None = None
    _api: Api | None = None

    # --- API ---
    @property
    def http(self) -> httpx.Client:
        if self._http is None:
            self._http = self.http_factory()
        return self._http

    def _timing(self) -> dict:
        return {"sleep": self.sleep} if self.sleep else {}

    @property
    def oauth(self) -> OAuth:
        return OAuth(self.api_url, self.http, notice=self.info, **self._timing())

    @property
    def tokens(self) -> EnvToken | KeyringSession:
        if self._tokens is None:
            self._tokens = token_source(self.api_url, self.oauth, self.environ)
        return self._tokens

    @property
    def api(self) -> Api:
        if self._api is None:
            self._api = Api(
                self.api_url, self.http, self.tokens, notice=self.info, **self._timing()
            )
        return self._api

    @property
    def anonymous(self) -> Api:
        return Api(self.api_url, self.http, None, notice=self.info, **self._timing())

    def close(self) -> None:
        if self._http is not None:
            self._http.close()

    # --- output ---
    def info(self, message: str) -> None:
        if not self.quiet:
            self.stderr.write(message + "\n")
            self.stderr.flush()

    def table(self, rows: Iterable[Mapping[str, Any]], columns: list[str]) -> None:
        """A list: aligned columns, a JSON array of the API objects as they are, or CSV."""
        rows = list(rows)
        if self.format == "json":
            self.stdout.write(json.dumps(rows, ensure_ascii=False, indent=2) + "\n")
            return
        if self.format == "csv":
            writer = csv.writer(self.stdout, lineterminator="\n")
            writer.writerow(columns)
            writer.writerows([cell(r.get(c)) for c in columns] for r in rows)
            return
        cells = [[cell(r.get(c)) for c in columns] for r in rows]
        widths = [max([len(c), *(len(row[i]) for row in cells)]) for i, c in enumerate(columns)]
        for line in [columns, *cells]:
            text = "  ".join(v.ljust(w) for v, w in zip(line, widths, strict=True))
            self.stdout.write(text.rstrip() + "\n")

    def record(self, data: Mapping[str, Any]) -> None:
        """One object: «key  value» lines, the JSON object, or CSV with one row."""
        if self.format == "json":
            self.stdout.write(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
        elif self.format == "csv":
            self.table([data], list(data))
        else:
            width = max((len(k) for k in data), default=0)
            for key, value in data.items():
                self.stdout.write(f"{key.ljust(width)}  {cell(value)}".rstrip() + "\n")

    def error(self, err: CliError) -> None:
        if self.format == "json":
            payload: dict[str, Any] = {"error": str(err), "exit_code": err.code}
            problem = getattr(err, "problem", None)
            if problem:
                payload["problem"] = problem
            self.stderr.write(json.dumps(payload, ensure_ascii=False) + "\n")
        else:
            self.stderr.write(f"Error: {err}\n")

    # --- changes ---
    def confirm(self, question: str) -> None:
        """--yes — no question; no terminal — refusal (exit code 3)."""
        if self.yes:
            return
        if not self.interactive:
            raise Refused(f"{question} — needs confirmation: add --yes")
        self.stderr.write(f"{question} [y/N] ")
        self.stderr.flush()
        if self.stdin.readline().strip().lower() not in ("y", "yes"):
            raise Refused("cancelled")

    def change(
        self,
        endpoint: Endpoint,
        question: str,
        *,
        body: Mapping[str, Any] | None = None,
        **path: object,
    ) -> Any:
        """Send a change. --dry-run shows the request and sends nothing (None is returned)."""
        url = self.api.full_url(endpoint, **path)
        if self.dry_run:
            self.show_request(endpoint.method, url, body)
            return None
        self.confirm(question)
        return self.api.call(endpoint, body=body, **path)

    def show_request(self, method: str, url: str, body: Mapping[str, Any] | None) -> None:
        if self.format == "json":
            self.stdout.write(
                json.dumps({"dry_run": True, "method": method, "url": url, "body": body}) + "\n"
            )
            return
        self.stdout.write(f"{method} {url}\n")
        if body is not None:
            self.stdout.write(json.dumps(body, ensure_ascii=False, indent=2) + "\n")
        self.info("Dry run: nothing was sent.")
