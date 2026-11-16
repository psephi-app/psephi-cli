"""Shared fixtures: a fake API host (respx), an in-memory keyring, and a runner for `psephi`."""

from __future__ import annotations

import io
import json
import time
from dataclasses import dataclass, field

import httpx
import keyring
import pytest
import respx
from keyring.backend import KeyringBackend
from keyring.errors import PasswordDeleteError

from psephi.auth import store
from psephi.auth.store import Credentials
from psephi.cli.main import main

API = "https://api.test"
WEB = "https://app.test"

METADATA = {
    "issuer": API,
    "authorization_endpoint": f"{WEB}/oauth/authorize",
    "token_endpoint": f"{API}/oauth/token",
    "revocation_endpoint": f"{API}/oauth/revoke",
    "device_authorization_endpoint": f"{API}/oauth/device_authorization",
    "code_challenge_methods_supported": ["S256"],
    "scopes_supported": ["account:read", "account:write", "transactions:read"],
    "authorization_response_iss_parameter_supported": True,
}


class MemoryKeyring(KeyringBackend):
    priority = 1

    def __init__(self):
        super().__init__()
        self.items: dict[tuple[str, str], str] = {}

    def get_password(self, service, username):
        return self.items.get((service, username))

    def set_password(self, service, username, password):
        self.items[(service, username)] = password

    def delete_password(self, service, username):
        if (service, username) not in self.items:
            raise PasswordDeleteError(username)
        del self.items[(service, username)]


@pytest.fixture(autouse=True)
def memory_keyring(tmp_path, monkeypatch):
    backend = MemoryKeyring()
    previous = keyring.get_keyring()
    keyring.set_keyring(backend)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setattr("psephi.auth.flows.BROWSER_TIMEOUT", 5)
    yield backend
    keyring.set_keyring(previous)


@pytest.fixture
def api():
    with respx.mock(base_url=API, assert_all_called=False) as router:
        yield router


def credentials(**changes) -> Credentials:
    base = {
        "access_token": "psephi_oat_old",
        "refresh_token": "psephi_ort_old",
        "expires_at": time.time() + 3600,
        "scope": "account:read transactions:read",
        "issuer": API,
        "token_endpoint": f"{API}/oauth/token",
        "revocation_endpoint": f"{API}/oauth/revoke",
    }
    return Credentials(**{**base, **changes})


@pytest.fixture
def signed_in():
    creds = credentials()
    store.save(API, creds)
    return creds


@dataclass
class Result:
    code: int
    out: str
    err: str
    sleeps: list[float] = field(default_factory=list)

    def json(self):
        return json.loads(self.out)


class TtyInput(io.StringIO):
    def isatty(self) -> bool:
        return True


def run(*argv: str, env: dict | None = None, stdin: str | None = None, tty=False, **kw) -> Result:
    sleeps: list[float] = []
    out, err = io.StringIO(), io.StringIO()
    source = (TtyInput if tty else io.StringIO)(stdin or "")
    code = main(
        ["--api", API, *argv],
        stdin=source,
        stdout=out,
        stderr=err,
        environ={"DISPLAY": ":0"} if env is None else env,
        http_factory=lambda: httpx.Client(follow_redirects=False),
        sleep=sleeps.append,
        **kw,
    )
    return Result(code, out.getvalue(), err.getvalue(), sleeps)


def page(results, next_url=None):
    return {"next": next_url, "previous": None, "results": results}


def problem(status, code, title, detail=None, **extra):
    body = {"type": f"{API}/v1/problems/#{code}", "title": title, "status": status, "code": code}
    if detail:
        body["detail"] = detail
    body.update(extra)
    return httpx.Response(status, json=body, headers={"Content-Type": "application/problem+json"})
