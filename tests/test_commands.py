"""Resource commands against a fake /v1: output formats, pagination, errors, 429, changes."""

from __future__ import annotations

import json
import time

import httpx
import pytest

from psephi.auth import store
from tests.conftest import API, credentials, page, problem, run

ENV = {"PSEPHI_TOKEN": "psephi_pat_ci"}
ACCOUNT = {
    "id": "6c1b7f2e-0a64-4d1f-9a3c-2b2f1c9e0a11",
    "name": "Daily, card",
    "kind": "current",
    "currency": "PLN",
    "parent": None,
    "archived": False,
    "include_in_net_worth": True,
    "created_at": "2026-01-01T00:00:00Z",
}
TX_ID = "0b0f8d1e-2c55-4c43-9a3e-1f0c2b6b7a10"
TRANSACTION = {
    "id": TX_ID,
    "date": "2026-11-14",
    "direction": "debit",
    "amount": {"amount": "42.5000", "currency": "PLN"},
    "merchant_name": "Żabka",
    "description": "",
    "category": None,
    "status": "cleared",
    "confirmed": False,
}


# --- output formats ---


def test_table_format_aligns_columns_and_shows_money_as_decimal(api):
    api.get("/v1/transactions/").respond(json=page([TRANSACTION]))

    result = run("transactions", "list", env=ENV)

    assert result.code == 0, result.err
    header, row = result.out.splitlines()
    assert header.split()[:4] == ["id", "date", "direction", "amount"]
    assert "42.5000 PLN" in row
    assert "Żabka" in row


def test_json_format_keeps_api_objects(api):
    api.get("/v1/accounts/").respond(json=page([ACCOUNT]))

    result = run("accounts", "list", "--format", "json", env=ENV)

    assert result.json() == [ACCOUNT]


def test_csv_format_quotes_values(api):
    api.get("/v1/accounts/").respond(json=page([ACCOUNT]))

    result = run("accounts", "list", "--format=csv", env=ENV)

    lines = result.out.splitlines()
    assert lines[0] == "id,name,kind,currency,archived,include_in_net_worth,parent"
    assert lines[1] == f'{ACCOUNT["id"]},"Daily, card",current,PLN,no,yes,'


def test_record_output(api):
    api.get("/v1/me/").respond(json={"email": "anna@example.com", "locale": "en"})

    result = run("me", "show", env=ENV)

    assert result.out.splitlines() == ["email   anna@example.com", "locale  en"]


def test_analytics_categories_table_and_json(api):
    summary = {
        "date_from": "2026-10-01",
        "date_to": "2026-10-31",
        "expense": [{"category": "c1", "amount": {"amount": "10.0000", "currency": "PLN"}}],
        "income": [{"category": "c2", "amount": {"amount": "99.0000", "currency": "EUR"}}],
    }
    route = api.get("/v1/analytics/categories/").respond(json=summary)

    table = run("analytics", "categories", "--from", "2026-10-01", "--to", "2026-10-31", env=ENV)
    as_json = run("analytics", "categories", "--from", "2026-10-01", "--format", "json", env=ENV)

    assert table.code == 0, table.err
    assert "expense  c1        10.0000 PLN" in table.out
    assert "income   c2        99.0000 EUR" in table.out
    assert as_json.json() == summary
    assert dict(route.calls.last.request.url.params) == {"date_from": "2026-10-01"}


def test_analytics_categories_needs_from():
    assert run("analytics", "categories", env=ENV).code == 2


def test_suggest_category(api):
    api.get(f"/v1/transactions/{TX_ID}/category-suggestions/").respond(
        json={
            "transaction": TX_ID,
            "category": "c1",
            "confidence": 0.9,
            "source": "model",
            "suggestions": [{"category": "c1", "probability": 0.9}],
        }
    )

    result = run("transactions", "suggest-category", TX_ID, env=ENV)

    assert result.code == 0
    assert "c1        0.9" in result.out
    assert "Best guess: c1" in result.err


# --- filters and pagination ---


def test_filters_go_to_query(api):
    route = api.get("/v1/transactions/").respond(json=page([]))

    run(
        "transactions", "list", "--from", "2026-11-01", "--to", "2026-11-30",
        "--account", ACCOUNT["id"], "--in-review", "--limit", "10", env=ENV,
    )  # fmt: skip

    assert dict(route.calls.last.request.url.params) == {
        "date_from": "2026-11-01",
        "date_to": "2026-11-30",
        "account": ACCOUNT["id"],
        "in_review": "true",
        "page_size": "10",
    }


def test_limit_stops_and_prints_the_next_cursor(api):
    route = api.get("/v1/transactions/").respond(
        json=page([TRANSACTION, TRANSACTION], f"{API}/v1/transactions/?cursor=abc&page_size=2")
    )

    result = run("transactions", "list", "--limit", "2", env=ENV)

    assert route.call_count == 1
    assert len(result.out.splitlines()) == 3
    assert "--cursor abc" in result.err


def test_all_follows_next_until_the_end(api):
    route = api.get("/v1/categories/").mock(
        side_effect=[
            httpx.Response(200, json=page([{"id": "1"}], f"{API}/v1/categories/?cursor=p2")),
            httpx.Response(200, json=page([{"id": "2"}], f"{API}/v1/categories/?cursor=p3")),
            httpx.Response(200, json=page([{"id": "3"}])),
        ]
    )

    result = run("categories", "list", "--all", "--format", "json", env=ENV)

    assert [r["id"] for r in result.json()] == ["1", "2", "3"]
    assert route.calls[0].request.url.params["page_size"] == "200"
    assert route.calls[1].request.url.params["cursor"] == "p2"
    assert route.calls[2].request.url.params["cursor"] == "p3"


def test_cursor_continues_a_list(api):
    route = api.get("/v1/tokens/").respond(json=page([]))

    run("tokens", "list", "--cursor", "xyz", env=ENV)

    assert route.calls.last.request.url.params["cursor"] == "xyz"


def test_next_page_on_another_host_is_not_followed(api):
    api.get("/v1/accounts/").respond(json=page([ACCOUNT], "https://evil.test/v1/accounts/?c=1"))

    result = run("accounts", "list", "--all", env=ENV)

    assert result.code == 1
    assert "another host" in result.err


def test_limit_and_all_exclude_each_other():
    assert run("accounts", "list", "--all", "--limit", "5", env=ENV).code == 2


@pytest.mark.parametrize("bad", ["0", "-1", "many"])
def test_limit_must_be_positive(bad):
    assert run("accounts", "list", "--limit", bad, env=ENV).code == 2


# --- errors ---


def test_problem_json_is_shown_in_human_words(api):
    api.patch("/v1/me/").mock(
        return_value=problem(
            400,
            "validation_error",
            "Invalid input",
            "Some fields are wrong.",
            fields={"time_zone": [{"code": "invalid", "message": "Unknown time zone."}]},
        )
    )

    result = run("me", "edit", "--time-zone", "Mars/Base", "--yes", env=ENV)

    assert result.code == 1
    assert result.err.splitlines() == [
        "Error: Invalid input: Some fields are wrong.",
        "  time_zone: Unknown time zone.",
    ]


def test_problem_json_in_json_mode(api):
    api.get("/v1/me/").mock(
        return_value=problem(
            403, "insufficient_scope", "Insufficient scope", required_scopes=["account:read"]
        )
    )

    result = run("me", "show", "--format", "json", env=ENV)

    error = json.loads(result.err)
    assert result.code == 1
    assert error["problem"]["code"] == "insufficient_scope"
    assert "required scopes: account:read" in error["error"]


def test_not_found(api):
    api.get(f"/v1/transactions/{TX_ID}/").mock(return_value=problem(404, "not_found", "Not found"))

    result = run("transactions", "show", TX_ID, env=ENV)

    assert result.code == 1
    assert result.err == "Error: Not found\n"


def test_non_json_error_body(api):
    api.get("/v1/me/").respond(502, text="<html>bad gateway</html>")

    result = run("me", "show", env=ENV)

    assert result.code == 1
    assert "HTTP 502" in result.err


def test_network_error(api):
    api.get("/v1/me/").mock(side_effect=httpx.ConnectError("no route"))

    result = run("me", "show", env=ENV)

    assert result.code == 1
    assert "cannot reach" in result.err


def test_wrong_id_is_a_usage_error():
    assert run("transactions", "show", "not-a-uuid", env=ENV).code == 2


def test_wrong_api_url():
    result = run("--api", "http://example.com", "me", "show", env=ENV)

    assert result.code == 2
    assert "only https" in result.err


# --- 429 ---


def test_429_waits_retry_after_and_retries_once(api):
    route = api.get("/v1/me/").mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "7"}, json={"code": "throttled"}),
            httpx.Response(200, json={"email": "anna@example.com"}),
        ]
    )

    result = run("me", "show", env=ENV)

    assert result.code == 0, result.err
    assert result.sleeps == [7]
    assert route.call_count == 2
    assert "retrying in 7 s" in result.err


def test_second_429_is_an_error(api):
    api.get("/v1/me/").mock(
        return_value=httpx.Response(
            429,
            headers={"Retry-After": "3"},
            json={"title": "Too many requests", "code": "throttled", "detail": "Slow down."},
        )
    )

    result = run("me", "show", env=ENV)

    assert result.code == 1
    assert result.sleeps == [3]
    assert "Too many requests: Slow down." in result.err


def test_long_retry_after_is_not_waited(api):
    route = api.get("/v1/me/").respond(429, headers={"Retry-After": "3600"}, json={})

    result = run("me", "show", env=ENV)

    assert result.code == 1
    assert result.sleeps == []
    assert route.call_count == 1
    assert "3600 s" in result.err


def test_retry_after_as_http_date():
    from email.utils import formatdate

    from psephi.client import retry_after_seconds

    now = time.time()
    response = httpx.Response(429, headers={"Retry-After": formatdate(now + 30, usegmt=True)})

    assert 29 <= retry_after_seconds(response, now=now) <= 31
    assert retry_after_seconds(httpx.Response(429)) == 1
    assert retry_after_seconds(httpx.Response(429, headers={"Retry-After": "soon"})) == 1


# --- changes: --dry-run and --yes ---


def test_dry_run_shows_the_request_and_sends_nothing(api):
    route = api.patch(f"/v1/accounts/{ACCOUNT['id']}/").respond(json=ACCOUNT)

    result = run(
        "accounts", "edit", ACCOUNT["id"], "--name", "Card", "--archived", "--dry-run", env=ENV
    )

    assert result.code == 0
    assert not route.called
    assert result.out.startswith(f"PATCH {API}/v1/accounts/{ACCOUNT['id']}/\n")
    assert json.loads(result.out.split("\n", 1)[1]) == {"name": "Card", "archived": True}
    assert "nothing was sent" in result.err


def test_dry_run_in_json(api):
    result = run("transactions", "confirm", TX_ID, "--dry-run", "--format", "json", env=ENV)

    assert result.json() == {
        "dry_run": True,
        "method": "POST",
        "url": f"{API}/v1/transactions/{TX_ID}/confirm/",
        "body": None,
    }


def test_change_without_terminal_needs_yes(api):
    route = api.post(f"/v1/transactions/{TX_ID}/confirm/").respond(json=TRANSACTION)

    result = run("transactions", "confirm", TX_ID, env=ENV)

    assert result.code == 3
    assert "--yes" in result.err
    assert not route.called


def test_change_with_yes_is_sent(api):
    route = api.post(f"/v1/transactions/{TX_ID}/confirm/").respond(
        json={**TRANSACTION, "confirmed": True}
    )

    result = run("transactions", "confirm", TX_ID, "--yes", env=ENV)

    assert result.code == 0
    assert route.called
    assert result.out.splitlines()[-1].split() == ["confirmed", "yes"]


def test_change_asks_on_a_terminal(api):
    route = api.patch("/v1/me/").respond(json={"locale": "pl"})

    yes = run("me", "edit", "--locale", "pl", env=ENV, stdin="y\n", tty=True)
    no = run("me", "edit", "--locale", "pl", env=ENV, stdin="\n", tty=True)

    assert yes.code == 0
    assert no.code == 3
    assert route.call_count == 1
    assert json.loads(route.calls.last.request.content) == {"locale": "pl"}


def test_edit_category_none_clears_it(api):
    route = api.patch(f"/v1/transactions/{TX_ID}/").respond(json=TRANSACTION)

    run("transactions", "edit", TX_ID, "--category", "none", "--description", "Gift", "-y", env=ENV)

    assert json.loads(route.calls.last.request.content) == {"description": "Gift", "category": None}


def test_edit_without_fields_is_a_usage_error():
    assert run("transactions", "edit", TX_ID, "--yes", env=ENV).code == 2
    assert run("me", "edit", "--yes", env=ENV).code == 2


def test_tokens_create_and_revoke(api):
    create = api.post("/v1/tokens/").respond(
        201, json={"id": 5, "name": "ci", "token": "psephi_pat_secret"}
    )
    revoke = api.delete("/v1/tokens/5/").respond(204)

    created = run(
        "tokens", "create", "--name", "ci", "--scope", "transactions:read", "--idle-days", "30",
        "--yes", env=ENV,
    )  # fmt: skip
    revoked = run("tokens", "revoke", "5", "--yes", env=ENV)

    assert created.code == 0
    assert "psephi_pat_secret" in created.out
    assert json.loads(create.calls.last.request.content) == {
        "name": "ci",
        "scopes": ["transactions:read"],
        "expiry": "sliding",
        "idle_days": 30,
    }
    assert revoked.code == 0
    assert revoke.called
    assert "revoked" in revoked.err


@pytest.mark.parametrize(
    "flags",
    [[], ["--idle-days", "0"], ["--idle-days", "5", "--expires-at", "2027-01-01T00:00:00Z"]],
)
def test_tokens_create_needs_one_expiry(flags):
    args = ["tokens", "create", "--name", "x", "--scope", "account:read", *flags, "--yes"]
    assert run(*args, env=ENV).code == 2


def test_api_calls_filters(api):
    route = api.get("/v1/api-calls/").respond(json=page([]))

    run("api-calls", "list", "--status", "429", "--token", "5", env=ENV)

    assert dict(route.calls.last.request.url.params) == {
        "status": "429",
        "token": "5",
        "page_size": "50",
    }


# --- token refresh ---


def test_expired_access_token_is_refreshed_transparently(api):
    store.save(API, credentials(expires_at=time.time() - 10))
    refresh = api.post("/oauth/token").respond(
        json={
            "access_token": "psephi_oat_fresh",
            "refresh_token": "psephi_ort_fresh",
            "expires_in": 3600,
            "scope": "account:read",
        }
    )
    me = api.get("/v1/me/").respond(json={"email": "anna@example.com"})

    result = run("me", "show")

    assert result.code == 0, result.err
    assert dict(httpx.QueryParams(refresh.calls.last.request.content.decode())) == {
        "grant_type": "refresh_token",
        "refresh_token": "psephi_ort_old",
        "client_id": "psephi-cli",
    }
    assert me.calls.last.request.headers["Authorization"] == "Bearer psephi_oat_fresh"
    saved = store.load(API)
    assert (saved.access_token, saved.refresh_token) == ("psephi_oat_fresh", "psephi_ort_fresh")
    assert saved.scope == "account:read"


def test_refused_access_token_is_refreshed_once_and_retried(api, signed_in):
    api.post("/oauth/token").respond(
        json={"access_token": "psephi_oat_2", "refresh_token": "psephi_ort_2", "expires_in": 3600}
    )
    me = api.get("/v1/me/").mock(
        side_effect=[
            problem(401, "token_expired", "Token expired"),
            httpx.Response(200, json={"email": "a@example.com"}),
        ]
    )

    result = run("me", "show")

    assert result.code == 0, result.err
    assert [c.request.headers["Authorization"] for c in me.calls] == [
        "Bearer psephi_oat_old",
        "Bearer psephi_oat_2",
    ]


def test_refresh_invalid_grant_asks_to_sign_in_again(api):
    store.save(API, credentials(expires_at=0))
    api.post("/oauth/token").respond(400, json={"error": "invalid_grant"})

    result = run("me", "show")

    assert result.code == 1
    assert "Sign in again: psephi login" in result.err
    assert store.load(API) is None


def test_refresh_by_another_process_is_reused(api, monkeypatch):
    """Another psephi refreshed while this one waited for the lock: no second refresh."""
    from psephi.auth import session

    store.save(API, credentials(expires_at=0))
    refreshed_elsewhere = credentials(
        access_token="psephi_oat_other", refresh_token="psephi_ort_other"
    )
    real_lock = store.refresh_lock

    def lock_then_other_process(url):
        store.save(API, refreshed_elsewhere)
        return real_lock(url)

    monkeypatch.setattr(session.store, "refresh_lock", lock_then_other_process)
    refresh = api.post("/oauth/token").respond(json={})
    me = api.get("/v1/me/").respond(json={})

    assert run("me", "show").code == 0

    assert not refresh.called
    assert me.calls.last.request.headers["Authorization"] == "Bearer psephi_oat_other"


def test_personal_token_is_not_refreshed_on_401(api):
    refresh = api.post("/oauth/token").respond(json={})
    api.get("/v1/me/").mock(return_value=problem(401, "token_revoked", "Token revoked"))

    result = run("me", "show", env=ENV)

    assert result.code == 1
    assert not refresh.called
    assert "Token revoked" in result.err
