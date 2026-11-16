"""Where the bearer token comes from: PSEPHI_TOKEN (personal token, for CI) or the keyring
entry written by `psephi login`, refreshed transparently when it runs out."""

from __future__ import annotations

from psephi.auth import store
from psephi.auth.oauth import OAuth
from psephi.auth.store import Credentials
from psephi.config import ENV_TOKEN
from psephi.errors import LoginRequired


class EnvToken:
    """A personal token from the environment: used as is, never refreshed or stored."""

    source = "env"

    def __init__(self, value: str):
        self.value = value.strip()

    def token(self) -> str:
        return self.value

    def after_unauthorized(self) -> bool:
        return False


class KeyringSession:
    source = "keyring"

    def __init__(self, api_url: str, oauth: OAuth):
        self.api_url = api_url
        self.oauth = oauth
        self._creds: Credentials | None = None
        self._retried = False

    def credentials(self) -> Credentials:
        if self._creds is None:
            self._creds = store.load(self.api_url)
        if self._creds is None:
            raise LoginRequired(
                f"not signed in to {self.api_url}. Run `psephi login` "
                f"(or set {ENV_TOKEN} to a personal token)"
            )
        return self._creds

    def token(self) -> str:
        creds = self.credentials()
        if creds.expired():
            creds = self._refresh(creds)
        return creds.access_token

    def after_unauthorized(self) -> bool:
        """The access token was refused before its time (revoked, server clock): refresh once."""
        if self._retried or self._creds is None:
            return False
        self._retried = True
        self._refresh(self._creds)
        return True

    def _refresh(self, seen: Credentials) -> Credentials:
        with store.refresh_lock(self.api_url):
            # another psephi process may have refreshed while we waited for the lock
            current = store.load(self.api_url)
            if current is None:
                raise LoginRequired("signed out meanwhile. Sign in again: psephi login")
            if current.refresh_token != seen.refresh_token and not current.expired():
                self._creds = current
                return current
            try:
                fresh = self.oauth.refresh(current)
            except LoginRequired:
                store.delete(self.api_url)
                self._creds = None
                raise
            store.save(self.api_url, fresh)
            self._creds = fresh
            return fresh


def token_source(api_url: str, oauth: OAuth, environ) -> EnvToken | KeyringSession:
    value = environ.get(ENV_TOKEN, "").strip()
    return EnvToken(value) if value else KeyringSession(api_url, oauth)
