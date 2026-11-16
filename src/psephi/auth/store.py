"""Tokens from `psephi login`, kept in the system keyring (macOS Keychain, Windows Credential
Manager, Secret Service on Linux) — one entry per API host, never in a plain file."""

from __future__ import annotations

import contextlib
import json
import os
import sys
import time
from collections.abc import Iterator
from dataclasses import asdict, dataclass, replace
from pathlib import Path

import keyring
from keyring.errors import KeyringError, PasswordDeleteError

from psephi.errors import CliError

SERVICE = "psephi"
EXPIRY_MARGIN = 60  # seconds: refresh a bit before the access token runs out


@dataclass(frozen=True)
class Credentials:
    access_token: str
    refresh_token: str
    expires_at: float  # Unix time when the access token ends
    scope: str
    issuer: str
    token_endpoint: str
    revocation_endpoint: str | None = None

    @property
    def scopes(self) -> list[str]:
        return self.scope.split()

    def expired(self, now: float | None = None) -> bool:
        return (now if now is not None else time.time()) >= self.expires_at - EXPIRY_MARGIN

    def rotated(self, access: str, refresh: str, expires_in: int, scope: str | None) -> Credentials:
        return replace(
            self,
            access_token=access,
            refresh_token=refresh,
            expires_at=time.time() + expires_in,
            scope=scope or self.scope,
        )


def _keyring_error(err: Exception) -> CliError:
    return CliError(
        f"system keyring is not available ({err}). Use a personal token in PSEPHI_TOKEN "
        "instead, or install a keyring backend (see the README)"
    )


def load(api_url: str) -> Credentials | None:
    try:
        raw = keyring.get_password(SERVICE, api_url)
    except KeyringError as err:
        raise _keyring_error(err) from err
    if not raw:
        return None
    try:
        return Credentials(**json.loads(raw))
    except (TypeError, ValueError):
        return None  # written by another version or damaged: treat as signed out


def save(api_url: str, creds: Credentials) -> None:
    try:
        keyring.set_password(SERVICE, api_url, json.dumps(asdict(creds)))
    except KeyringError as err:
        raise _keyring_error(err) from err


def delete(api_url: str) -> bool:
    """Remove the entry; False — there was none."""
    try:
        keyring.delete_password(SERVICE, api_url)
    except PasswordDeleteError:
        return False
    except KeyringError as err:
        raise _keyring_error(err) from err
    return True


def _lock_dir() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    return base / "psephi"


@contextlib.contextmanager
def refresh_lock(api_url: str) -> Iterator[None]:
    """One refresh at a time across processes. Refresh tokens are single-use and the server
    treats a second use as theft (it revokes the whole connection), so two parallel
    `psephi` runs must not refresh with the same token."""
    folder = _lock_dir()
    folder.mkdir(parents=True, exist_ok=True)
    name = "".join(c if c.isalnum() else "_" for c in api_url)
    with open(folder / f"{name}.lock", "a+") as handle:
        if sys.platform == "win32":  # pragma: no cover - exercised on Windows only
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
