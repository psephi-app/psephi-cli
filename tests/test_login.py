"""psephi login / logout / auth status: browser with PKCE and loopback, device code."""

from __future__ import annotations

import threading
import urllib.error
import urllib.request
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
import pytest

from psephi.auth import store
from psephi.auth.oauth import challenge_for
from tests.conftest import API, METADATA, WEB, credentials, run

TOKENS = {
    "access_token": "psephi_oat_new",
    "refresh_token": "psephi_ort_new",
    "token_type": "Bearer",
    "expires_in": 3600,
    "scope": "account:read transactions:read",
}


def form(request: httpx.Request) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(request.content.decode()).items()}


def browser(*, state=None, iss=API, error=None, code="psephi_oac_1"):
    """A fake browser: checks the authorize URL, then follows the redirect to the loopback."""
    seen: dict = {}

    def open_browser(url: str) -> bool:
        parts = urlsplit(url)
        query = {k: v[0] for k, v in parse_qs(parts.query).items()}
        seen.update(query, base=f"{parts.scheme}://{parts.netloc}{parts.path}")
        answer = {"state": state if state is not None else query["state"]}
        if iss:
            answer["iss"] = iss
        answer.update({"error": error} if error else {"code": code})
        target = f"{query['redirect_uri']}?{urlencode(answer)}"

        def hit():
            try:
                urllib.request.urlopen(target, timeout=5).read()
            except urllib.error.HTTPError as err:
                seen["status"] = err.code
            else:
                seen["status"] = 200

        seen["thread"] = threading.Thread(target=hit, daemon=True)
        seen["thread"].start()
        return True

    return open_browser, seen


@pytest.fixture
def server(api):
    api.get("/.well-known/oauth-authorization-server").respond(json=METADATA)
    api.get("/v1/me/").respond(json={"email": "anna@example.com"})
    return api


def test_browser_login_uses_pkce_s256_and_stores_tokens(server, memory_keyring):
    token = server.post("/oauth/token").respond(json=TOKENS)
    open_browser, seen = browser()

    result = run("login", open_browser=open_browser, env={"DISPLAY": ":0"})

    assert result.code == 0, result.err
    assert seen["base"] == f"{WEB}/oauth/authorize"
    assert seen["response_type"] == "code"
    assert seen["client_id"] == "psephi-cli"
    assert seen["code_challenge_method"] == "S256"
    assert seen["scope"] == " ".join(METADATA["scopes_supported"])
    assert urlsplit(seen["redirect_uri"]).hostname == "127.0.0.1"
    assert urlsplit(seen["redirect_uri"]).path == "/callback"
    sent = form(token.calls.last.request)
    assert sent["grant_type"] == "authorization_code"
    assert sent["code"] == "psephi_oac_1"
    assert sent["redirect_uri"] == seen["redirect_uri"]
    assert challenge_for(sent["code_verifier"]) == seen["code_challenge"]
    assert 43 <= len(sent["code_verifier"]) <= 128
    saved = store.load(API)
    assert saved.access_token == "psephi_oat_new"
    assert saved.refresh_token == "psephi_ort_new"
    assert "as anna@example.com" in result.err
    seen["thread"].join(5)
    assert seen["status"] == 200


def test_login_with_narrower_scopes(server):
    server.post("/oauth/token").respond(json=TOKENS)
    open_browser, seen = browser()

    result = run("login", "--scope", "transactions:read", open_browser=open_browser)

    assert result.code == 0, result.err
    assert seen["scope"] == "transactions:read"


def test_login_rejects_unknown_scope(server):
    assert run("login", "--scope", "everything:write").code == 2


def test_state_mismatch_is_refused_without_exchange(server):
    token = server.post("/oauth/token").respond(json=TOKENS)
    open_browser, seen = browser(state="forged")

    result = run("login", open_browser=open_browser)

    assert result.code == 1
    assert "state mismatch" in result.err
    assert not token.called
    assert store.load(API) is None
    seen["thread"].join(5)
    assert seen["status"] == 400


def test_issuer_mismatch_is_refused(server):
    token = server.post("/oauth/token").respond(json=TOKENS)
    open_browser, _ = browser(iss="https://evil.test")

    result = run("login", open_browser=open_browser)

    assert result.code == 1
    assert "another server" in result.err
    assert not token.called


def test_missing_iss_is_refused_when_the_server_promises_it(server):
    open_browser, _ = browser(iss=None)

    assert run("login", open_browser=open_browser).code == 1


def test_declined_consent(server):
    open_browser, _ = browser(error="access_denied")

    result = run("login", open_browser=open_browser)

    assert result.code == 1
    assert "declined" in result.err


def test_used_code_asks_to_sign_in_again(server):
    server.post("/oauth/token").respond(400, json={"error": "invalid_grant"})
    open_browser, _ = browser()

    result = run("login", open_browser=open_browser)

    assert result.code == 1
    assert "Run `psephi login` again" in result.err


def test_metadata_with_foreign_issuer_is_refused(api):
    api.get("/.well-known/oauth-authorization-server").respond(
        json={**METADATA, "issuer": "https://evil.test"}
    )

    result = run("login", open_browser=lambda _url: True)

    assert result.code == 1
    assert "another issuer" in result.err


def test_metadata_with_token_endpoint_elsewhere_is_refused(api):
    api.get("/.well-known/oauth-authorization-server").respond(
        json={**METADATA, "token_endpoint": "https://evil.test/token"}
    )

    assert run("login", open_browser=lambda _url: True).code == 1


def test_new_login_revokes_the_previous_one(server, signed_in):
    server.post("/oauth/token").respond(json=TOKENS)
    revoke = server.post("/oauth/revoke").respond(200)
    open_browser, _ = browser()

    assert run("login", open_browser=open_browser).code == 0

    assert form(revoke.calls.last.request)["token"] == signed_in.refresh_token


# --- device code ---


def device_answer(**extra):
    return {
        "device_code": "psephi_odc_1",
        "user_code": "BCDF-GHJK",
        "verification_uri": f"{WEB}/device",
        "verification_uri_complete": f"{WEB}/device?code=BCDF-GHJK",
        "expires_in": 300,
        "interval": 5,
        **extra,
    }


def test_device_flow_waits_slows_down_and_succeeds(server):
    device = server.post("/oauth/device_authorization").respond(json=device_answer())
    token = server.post("/oauth/token").mock(
        side_effect=[
            httpx.Response(400, json={"error": "authorization_pending"}),
            httpx.Response(400, json={"error": "slow_down"}),
            httpx.Response(400, json={"error": "authorization_pending"}),
            httpx.Response(200, json=TOKENS),
        ]
    )

    result = run("login", "--device")

    assert result.code == 0, result.err
    assert "BCDF-GHJK" in result.err
    assert f"{WEB}/device" in result.err
    assert result.sleeps == [5, 5, 10, 10]
    assert form(device.calls.last.request)["client_id"] == "psephi-cli"
    sent = form(token.calls.last.request)
    assert sent["grant_type"] == "urn:ietf:params:oauth:grant-type:device_code"
    assert sent["device_code"] == "psephi_odc_1"
    assert store.load(API).access_token == "psephi_oat_new"


def test_device_code_expired(server):
    server.post("/oauth/device_authorization").respond(json=device_answer())
    server.post("/oauth/token").respond(400, json={"error": "expired_token"})

    result = run("login", "--device")

    assert result.code == 1
    assert "expired" in result.err
    assert "login --device" in result.err
    assert store.load(API) is None


def test_device_code_declined(server):
    server.post("/oauth/device_authorization").respond(json=device_answer())
    server.post("/oauth/token").respond(400, json={"error": "access_denied"})

    result = run("login", "--device")

    assert result.code == 1
    assert "declined" in result.err


def test_device_polling_stops_at_the_deadline(server):
    server.post("/oauth/device_authorization").respond(json=device_answer(expires_in=0, interval=1))
    server.post("/oauth/token").respond(400, json={"error": "authorization_pending"})

    result = run("login", "--device")

    assert result.code == 1
    assert "expired" in result.err


def test_without_display_over_ssh_device_code_is_chosen(server):
    server.post("/oauth/device_authorization").respond(json=device_answer())
    server.post("/oauth/token").respond(json=TOKENS)

    result = run("login", env={"SSH_CONNECTION": "10.0.0.1 22 10.0.0.2 22"})

    assert result.code == 0, result.err
    assert "device code" in result.err


def test_without_display_the_person_may_decline_device_code(server):
    server.post("/oauth/token").respond(json=TOKENS)
    open_browser, _ = browser()

    result = run(
        "login", env={"SSH_TTY": "/dev/pts/0"}, stdin="n\n", tty=True, open_browser=open_browser
    )

    assert result.code == 0, result.err
    assert "Use a device code instead?" in result.err


# --- logout ---


def test_logout_revokes_and_forgets(api, signed_in):
    revoke = api.post("/oauth/revoke").respond(200)

    result = run("logout")

    assert result.code == 0
    sent = form(revoke.calls.last.request)
    assert sent == {
        "token": signed_in.refresh_token,
        "token_type_hint": "refresh_token",
        "client_id": "psephi-cli",
    }
    assert store.load(API) is None


def test_logout_forgets_even_when_the_server_is_unreachable(api, signed_in):
    api.post("/oauth/revoke").mock(side_effect=httpx.ConnectError("offline"))

    result = run("logout")

    assert result.code == 0
    assert "Connected apps" in result.err
    assert store.load(API) is None


def test_logout_when_not_signed_in(api):
    result = run("logout")

    assert result.code == 0
    assert "Not signed in" in result.err


# --- auth status ---

CURRENT = {
    "id": 7,
    "name": "psephi",
    "prefix": "psephi_oat_abc",
    "scopes": ["account:read", "transactions:read"],
    "expiry": "sliding",
    "valid_until": "2026-12-15T10:00:00Z",
    "kind": "oauth",
    "client": {"client_id": "psephi-cli", "name": "psephi"},
    "access_expires_at": "2026-11-15T11:00:00Z",
}


def test_status_shows_who_scopes_and_expiry(api, signed_in):
    current = api.get("/v1/token/").respond(json=CURRENT)
    api.get("/v1/me/").respond(json={"email": "anna@example.com"})

    result = run("auth", "status")

    assert result.code == 0, result.err
    assert "anna@example.com" in result.out
    assert "account:read,transactions:read" in result.out
    assert "2026-12-15T10:00:00Z" in result.out
    assert current.calls.last.request.headers["Authorization"] == "Bearer psephi_oat_old"


def test_status_without_sign_in_is_a_clear_error(api):
    result = run("auth", "status")

    assert result.code == 1
    assert "not signed in" in result.err
    assert "psephi login" in result.err


def test_personal_token_from_env_wins_over_keyring(api, signed_in):
    current = api.get("/v1/token/").respond(
        json={**CURRENT, "kind": "personal", "client": None, "scopes": ["transactions:read"]}
    )

    result = run("auth", "status", "--format", "json", env={"PSEPHI_TOKEN": "psephi_pat_ci"})

    assert result.code == 0, result.err
    assert current.calls.last.request.headers["Authorization"] == "Bearer psephi_pat_ci"
    assert result.json()["source"] == "PSEPHI_TOKEN"
    assert result.json()["email"] is None  # no account scope: /v1/me/ is not asked


def test_login_warns_that_env_token_wins(server):
    server.post("/oauth/token").respond(json=TOKENS)
    open_browser, _ = browser()

    env = {"PSEPHI_TOKEN": "psephi_pat_ci", "DISPLAY": ":0"}
    result = run("login", open_browser=open_browser, env=env)

    assert result.code == 0
    assert "PSEPHI_TOKEN is set" in result.err


def test_credentials_expiry_margin():
    assert credentials(expires_at=0).expired()
    assert not credentials().expired()
