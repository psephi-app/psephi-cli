"""OAuth 2.1 against the Psephi authorization server: metadata (RFC 8414), PKCE (RFC 7636),
code exchange, refresh with rotation, device code (RFC 8628), revocation (RFC 7009)."""

from __future__ import annotations

import base64
import hashlib
import secrets
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx

from psephi.auth.store import Credentials
from psephi.client import problem_of, send
from psephi.config import CLIENT_ID
from psephi.endpoints import OAUTH_METADATA
from psephi.errors import ApiError, CliError, LoginRequired, OAuthError

DEVICE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"
SLOW_DOWN_STEP = 5  # seconds added to the polling interval on slow_down (RFC 8628, 3.5)
DEFAULT_INTERVAL = 5  # RFC 8628, 3.2: without `interval` the client waits 5 seconds
DEFAULT_DEVICE_LIFETIME = 300


def _seconds(value: object, default: int) -> int:
    return value if isinstance(value, int) and value >= 0 else default


@dataclass(frozen=True)
class Metadata:
    issuer: str
    authorization_endpoint: str
    token_endpoint: str
    revocation_endpoint: str | None
    device_authorization_endpoint: str | None
    scopes_supported: tuple[str, ...]
    iss_parameter_supported: bool


@dataclass(frozen=True)
class Pkce:
    verifier: str
    challenge: str

    @classmethod
    def new(cls) -> Pkce:
        verifier = secrets.token_urlsafe(64)  # 86 characters, within RFC 7636's 43–128
        return cls(verifier, challenge_for(verifier))


def challenge_for(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def new_state() -> str:
    return secrets.token_urlsafe(32)


def _same_origin(url: str, issuer: str) -> bool:
    a, b = urlsplit(url), urlsplit(issuer)
    return (a.scheme, a.netloc.lower()) == (b.scheme, b.netloc.lower())


def _https(url: str, issuer: str) -> bool:
    return urlsplit(url).scheme == "https" or _same_origin(url, issuer)


class OAuth:
    def __init__(
        self,
        api_url: str,
        http: httpx.Client,
        *,
        sleep: Callable[[float], None] = time.sleep,
        notice: Callable[[str], None] = lambda _msg: None,
    ):
        self.api_url = api_url
        self.http = http
        self.sleep = sleep
        self.notice = notice

    # --- metadata ---
    def metadata(self) -> Metadata:
        data = self._json("GET", self.api_url + OAUTH_METADATA.path)
        issuer = str(data.get("issuer", "")).rstrip("/")
        if issuer != self.api_url:  # RFC 8414, 3.3: the issuer must be the host we asked
            raise CliError(f"authorization server metadata names another issuer: {issuer!r}")
        meta = Metadata(
            issuer=issuer,
            authorization_endpoint=str(data.get("authorization_endpoint", "")),
            token_endpoint=str(data.get("token_endpoint", "")),
            revocation_endpoint=data.get("revocation_endpoint"),
            device_authorization_endpoint=data.get("device_authorization_endpoint"),
            scopes_supported=tuple(data.get("scopes_supported") or ()),
            iss_parameter_supported=bool(
                data.get("authorization_response_iss_parameter_supported")
            ),
        )
        self._check(meta)
        return meta

    @staticmethod
    def _check(meta: Metadata) -> None:
        # tokens go only to the API host itself; the consent page may live on the web host
        for name in ("token_endpoint", "revocation_endpoint", "device_authorization_endpoint"):
            url = getattr(meta, name)
            if url and not _same_origin(url, meta.issuer):
                raise CliError(f"authorization server metadata: {name} is not on {meta.issuer}")
        if not meta.token_endpoint:
            raise CliError("authorization server metadata has no token_endpoint")
        if not meta.authorization_endpoint or not _https(meta.authorization_endpoint, meta.issuer):
            raise CliError("authorization server metadata: authorization_endpoint must be https")

    # --- token endpoint ---
    def exchange_code(
        self, meta: Metadata, code: str, redirect_uri: str, verifier: str
    ) -> Credentials:
        form = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": CLIENT_ID,
            "code_verifier": verifier,
        }
        try:
            return self._credentials(meta, self._token(meta, form))
        except OAuthError as err:
            if err.error == "invalid_grant":
                # the code is single-use: the only way forward is a new sign-in
                raise LoginRequired(
                    "the sign-in code was not accepted (expired or already used). "
                    "Run `psephi login` again"
                ) from err
            raise

    def refresh(self, creds: Credentials) -> Credentials:
        form = {
            "grant_type": "refresh_token",
            "refresh_token": creds.refresh_token,
            "client_id": CLIENT_ID,
        }
        try:
            data = self._json("POST", creds.token_endpoint, form)
        except OAuthError as err:
            if err.error in ("invalid_grant", "invalid_client"):
                raise LoginRequired(
                    "your session has ended (signed out, disconnected in the app, or expired). "
                    "Sign in again: psephi login"
                ) from err
            raise
        return creds.rotated(
            _required(data, "access_token"),
            _required(data, "refresh_token"),
            int(data.get("expires_in") or 3600),
            data.get("scope"),
        )

    def revoke(self, url: str, token: str, hint: str) -> None:
        form = {"token": token, "token_type_hint": hint, "client_id": CLIENT_ID}
        self._json("POST", url, form)

    # --- device code (RFC 8628) ---
    def device_authorization(self, meta: Metadata, scope: str) -> dict[str, Any]:
        if not meta.device_authorization_endpoint:
            raise CliError("this server does not offer sign-in with a device code")
        form = {"client_id": CLIENT_ID, "scope": scope}
        data = self._json("POST", meta.device_authorization_endpoint, form)
        for key in ("device_code", "user_code", "verification_uri"):
            _required(data, key)
        return data

    def poll_device(
        self,
        meta: Metadata,
        device: Mapping[str, Any],
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> Credentials:
        interval = _seconds(device.get("interval"), DEFAULT_INTERVAL)
        deadline = clock() + _seconds(device.get("expires_in"), DEFAULT_DEVICE_LIFETIME)
        form = {
            "grant_type": DEVICE_GRANT,
            "device_code": device["device_code"],
            "client_id": CLIENT_ID,
        }
        while True:
            self.sleep(interval)
            try:
                return self._credentials(meta, self._token(meta, form))
            except OAuthError as err:
                if err.error == "authorization_pending":
                    pass
                elif err.error == "slow_down":
                    interval += SLOW_DOWN_STEP
                elif err.error == "access_denied":
                    raise CliError("sign-in was declined in the browser") from err
                elif err.error == "expired_token":
                    raise CliError(_EXPIRED_DEVICE) from err
                else:
                    raise
            if clock() >= deadline:
                raise CliError(_EXPIRED_DEVICE)

    # --- plumbing ---
    def _token(self, meta: Metadata, form: Mapping[str, str]) -> dict[str, Any]:
        return self._json("POST", meta.token_endpoint, form)

    def _credentials(self, meta: Metadata, data: Mapping[str, Any]) -> Credentials:
        return Credentials(
            access_token=_required(data, "access_token"),
            refresh_token=_required(data, "refresh_token"),
            expires_at=time.time() + int(data.get("expires_in") or 3600),
            scope=str(data.get("scope") or ""),
            issuer=meta.issuer,
            token_endpoint=meta.token_endpoint,
            revocation_endpoint=meta.revocation_endpoint,
        )

    def _json(self, method: str, url: str, form: Mapping[str, str] | None = None) -> dict[str, Any]:
        response = send(
            lambda: self.http.request(method, url, data=form),
            sleep=self.sleep,
            notice=self.notice,
            where=self.api_url,
        )
        if response.is_error:
            raise _oauth_error(response)
        if not response.content:
            return {}
        try:
            data = response.json()
        except ValueError as err:
            raise CliError(f"{url}: the answer is not JSON") from err
        if not isinstance(data, dict):
            raise CliError(f"{url}: unexpected answer")
        return data


_EXPIRED_DEVICE = (
    "the device code expired before sign-in was approved. Run `psephi login --device` again"
)


def _required(data: Mapping[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value:
        raise CliError(f"the authorization server's answer has no {key}")
    return value


def _oauth_error(response: httpx.Response) -> CliError:
    try:
        body = response.json()
    except ValueError:
        body = None
    if isinstance(body, dict) and isinstance(body.get("error"), str):
        return OAuthError(body["error"], body.get("error_description"))
    return ApiError(response.status_code, problem_of(response))
