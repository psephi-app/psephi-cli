"""Small pieces: URLs, PKCE, loopback checks, keyring entry, cells, browser detection."""

from __future__ import annotations

import io
import urllib.error
import urllib.request

import pytest
from keyring.errors import KeyringError

from psephi.auth import store
from psephi.auth.flows import browser_available
from psephi.auth.loopback import LoopbackReceiver, check_callback
from psephi.auth.oauth import Pkce, challenge_for, new_state
from psephi.cli.context import cell
from psephi.cli.main import main
from psephi.config import normalize_api_url, resolve_api_url
from psephi.errors import ApiError, CliError, UsageError
from tests.conftest import API, credentials

ISSUER = "https://api.test"


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("https://api.psephi.app", "https://api.psephi.app"),
        ("https://API.example.test/", "https://api.example.test"),
        ("https://api.psephi.app/v1/", "https://api.psephi.app"),
        ("http://127.0.0.1:8000", "http://127.0.0.1:8000"),
    ],
)
def test_api_url_normalized(given, expected):
    assert normalize_api_url(given) == expected


@pytest.mark.parametrize(
    "bad",
    [
        "api.psephi.app",
        "http://api.psephi.app",
        "https://user:pw@api.psephi.app",
        "https://api.psephi.app/other",
        "https://api.psephi.app/?x=1",
        "ftp://api.psephi.app",
    ],
)
def test_api_url_rejected(bad):
    with pytest.raises(UsageError):
        normalize_api_url(bad)


def test_api_url_priority():
    env = {"PSEPHI_API_URL": "https://api.example.test"}
    assert resolve_api_url(None, {}) == "https://api.psephi.app"
    assert resolve_api_url(None, env) == "https://api.example.test"
    assert resolve_api_url("https://x.test", env) == "https://x.test"


def test_pkce_matches_rfc7636_appendix_b():
    verifier = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
    assert challenge_for(verifier) == "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"


def test_pkce_and_state_are_fresh_each_time():
    first, second = Pkce.new(), Pkce.new()
    assert first.verifier != second.verifier
    assert 43 <= len(first.verifier) <= 128
    assert set(first.verifier) <= set(
        "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~"
    )
    assert new_state() != new_state()


@pytest.mark.parametrize(
    ("query", "expected_error"),
    [
        ("code=c&state=s&iss=https://api.test", None),
        ("code=c&state=s&iss=https://api.test/", None),
        ("code=c&state=other&iss=https://api.test", "state mismatch"),
        ("code=c&iss=https://api.test", "state mismatch"),
        ("code=c&state=s&iss=https://evil.test", "another server"),
        ("code=c&state=s", "another server"),
        ("error=access_denied&state=s&iss=https://api.test", "declined"),
        (
            "error=invalid_scope&error_description=nope&state=s&iss=https://api.test",
            "invalid_scope: nope",
        ),
        ("state=s&iss=https://api.test", "no authorization code"),
    ],
)
def test_check_callback(query, expected_error):
    result = check_callback(query, "s", ISSUER, require_iss=True)
    if expected_error is None:
        assert result.code == "c"
    else:
        assert expected_error in result.error


def test_iss_is_optional_when_the_server_does_not_promise_it():
    assert check_callback("code=c&state=s", "s", ISSUER, require_iss=False).code == "c"
    assert check_callback("code=c&state=s&iss=https://x", "s", ISSUER, require_iss=False).error


def _get(url):
    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            return response.status
    except urllib.error.HTTPError as err:
        return err.code


def test_loopback_ignores_other_paths_and_times_out():
    import threading

    with LoopbackReceiver("s", ISSUER, True) as receiver:
        assert receiver.redirect_uri.startswith("http://127.0.0.1:")
        base = receiver.redirect_uri.rsplit("/", 1)[0]
        statuses = []
        thread = threading.Thread(target=lambda: statuses.append(_get(base + "/favicon.ico")))
        thread.start()
        with pytest.raises(CliError, match="no answer from the browser"):
            receiver.wait(timeout=1.5)
        thread.join(5)
    assert statuses == [404]


def test_keyring_entry_round_trip_and_damage(memory_keyring):
    creds = credentials()
    store.save(API, creds)
    assert store.load(API) == creds
    memory_keyring.items[(store.SERVICE, API)] = "not json"
    assert store.load(API) is None
    assert store.delete(API) is True
    assert store.delete(API) is False


def test_keyring_failure_is_explained(monkeypatch):
    def broken(*_args):
        raise KeyringError("no backend")

    monkeypatch.setattr(store.keyring, "get_password", broken)
    with pytest.raises(CliError, match="PSEPHI_TOKEN"):
        store.load(API)


def test_refresh_lock_can_be_taken_twice_in_a_row():
    with store.refresh_lock(API):
        pass
    with store.refresh_lock(API):
        pass


@pytest.mark.parametrize(
    ("value", "text"),
    [
        (None, ""),
        (True, "yes"),
        ({"amount": "-1.5000", "currency": "EUR"}, "-1.5000 EUR"),
        (["a", "b"], "a,b"),
        ({"b": 1, "a": 2}, '{"a": 2, "b": 1}'),
        (3, "3"),
    ],
)
def test_cell(value, text):
    assert cell(value) == text


@pytest.mark.parametrize(
    ("env", "platform", "expected"),
    [
        ({}, "darwin", True),
        ({}, "win32", True),
        ({}, "linux", False),
        ({"DISPLAY": ":0"}, "linux", True),
        ({"WAYLAND_DISPLAY": "wayland-0"}, "linux", True),
        ({"SSH_CONNECTION": "1 2 3 4"}, "darwin", False),
        ({"SSH_TTY": "/dev/pts/1", "DISPLAY": "localhost:10"}, "linux", True),
    ],
)
def test_browser_available(env, platform, expected):
    assert browser_available(env, platform) is expected


def test_api_error_text():
    err = ApiError(
        400,
        {
            "title": "Invalid input",
            "detail": "Invalid input",
            "fields": {"name": ["This field is required."]},
        },
    )
    assert str(err) == "Invalid input\n  name: This field is required."
    assert str(ApiError(500, {})) == "HTTP 500"


def test_help_and_version():
    out = io.StringIO()
    assert main(["--version"], stdout=out, environ={}) == 0
    assert out.getvalue().startswith("psephi ")
    out = io.StringIO()
    assert main([], stdout=out, environ={}) == 0
    assert "transactions" in out.getvalue()
    out = io.StringIO()
    assert main(["transactions"], stdout=out, environ={}) == 0
    assert "suggest-category" in out.getvalue()
    assert main(["no-such-command"], stdout=io.StringIO(), stderr=io.StringIO(), environ={}) == 2
