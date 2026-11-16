"""Errors and exit codes.

Exit codes: 0 — success, 1 — error, 2 — wrong arguments,
3 — refused (no confirmation).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


class CliError(Exception):
    """Something went wrong; the message is shown to the person as is."""

    code = 1


class UsageError(CliError):
    code = 2


class Refused(CliError):
    code = 3


class LoginRequired(CliError):
    """No usable credentials: never signed in, signed out, or the session ended."""


def _field_lines(fields: Mapping[str, Any]) -> list[str]:
    lines = []
    for name, errors in fields.items():
        items = errors if isinstance(errors, list) else [errors]
        for item in items:
            message = item.get("message") if isinstance(item, Mapping) else item
            lines.append(f"  {name}: {message}")
    return lines


class ApiError(CliError):
    """An error answer from the API (problem+json, RFC 9457), in human words."""

    def __init__(self, status: int, problem: Mapping[str, Any]):
        self.status = status
        self.problem = dict(problem)
        super().__init__(self._text())

    @property
    def problem_code(self) -> str | None:
        return self.problem.get("code")

    def _text(self) -> str:
        title = self.problem.get("title") or f"HTTP {self.status}"
        detail = self.problem.get("detail")
        head = f"{title}: {detail}" if detail and detail != title else title
        lines = [head]
        lines += _field_lines(self.problem.get("fields") or {})
        scopes = self.problem.get("required_scopes")
        if scopes:
            lines.append(f"  required scopes: {', '.join(scopes)}")
        return "\n".join(lines)


class OAuthError(CliError):
    """An error answer from the OAuth endpoints (RFC 6749, 5.2: error, error_description)."""

    def __init__(self, error: str, description: str | None = None):
        self.error = error
        self.description = description
        text = f"{error}: {description}" if description else error
        super().__init__(text)
