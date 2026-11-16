"""Personal API tokens and the API call log."""

from __future__ import annotations

from psephi.cli.context import Context
from psephi.cli.registry import arg, group
from psephi.commands._common import list_rows
from psephi.endpoints import API_CALLS, TOKEN_CREATE, TOKEN_REVOKE, TOKENS
from psephi.errors import UsageError

SCOPES = (
    "account:read", "account:write", "transactions:read", "transactions:write",
    "accounts:read", "accounts:write", "analytics:read", "analytics:write",
    "categorization:read", "categorization:write",
)  # fmt: skip
TOKEN_COLUMNS = [
    "id",
    "name",
    "prefix",
    "scopes",
    "expiry",
    "valid_until",
    "last_used_at",
    "expired",
]
CALL_COLUMNS = [
    "id", "created_at", "method", "path", "status", "duration_ms", "token_prefix",
    "connection_client", "ip",
]  # fmt: skip
MAX_IDLE_DAYS = 365

tokens = group("tokens", "personal API tokens (psephi_pat_…) for scripts and CI")


@tokens.command(
    "list", "your active tokens", examples=("tokens list", "tokens list --format json"), paged=True
)
def list_tokens(ctx: Context, opts) -> None:
    list_rows(ctx, opts, TOKENS, TOKEN_COLUMNS)


def _expiry(opts) -> dict:
    if opts.expires_at and opts.idle_days:
        raise UsageError("give --expires-at or --idle-days, not both")
    if opts.expires_at:
        return {"expiry": "date", "expires_at": opts.expires_at}
    if opts.idle_days:
        if not 1 <= opts.idle_days <= MAX_IDLE_DAYS:
            raise UsageError(f"--idle-days: from 1 to {MAX_IDLE_DAYS}")
        return {"expiry": "sliding", "idle_days": opts.idle_days}
    if opts.never:
        return {"expiry": "never"}
    raise UsageError("choose how long the token lives: --expires-at, --idle-days or --never")


@tokens.command(
    "create",
    "create a token; it is shown once — keep it like a password",
    args=[
        arg("--name", required=True, help="what the token is for"),
        arg("--scope", action="append", required=True, choices=SCOPES, help="scope (repeat)"),
        arg(
            "--expires-at",
            metavar="ISO-DATETIME",
            help="ends at this moment, e.g. 2027-01-01T00:00:00Z",
        ),
        arg("--idle-days", type=int, help="ends after this many days without use (sliding)"),
        arg("--never", action="store_true", help="no end date"),
    ],
    examples=(
        "tokens create --name ci --scope transactions:read --idle-days 30",
        "tokens create --name backup --scope analytics:read --expires-at 2027-01-01T00:00:00Z",
    ),
    mutates=True,
)
def create(ctx: Context, opts) -> None:
    body = {"name": opts.name, "scopes": list(dict.fromkeys(opts.scope)), **_expiry(opts)}
    created = ctx.change(
        TOKEN_CREATE, f"Create token {opts.name!r} ({', '.join(body['scopes'])})?", body=body
    )
    if created is None:
        return
    ctx.record(created)
    ctx.info("The token is shown only now. Store it in a secret manager; it cannot be shown again.")


@tokens.command(
    "revoke",
    "revoke a token (and the tokens it created)",
    args=[arg("id", type=int, help="token id (from tokens list)")],
    examples=("tokens revoke 42", "tokens revoke 42 --yes"),
    mutates=True,
)
def revoke(ctx: Context, opts) -> None:
    if ctx.change(TOKEN_REVOKE, f"Revoke token {opts.id}?", id=opts.id) is None and not ctx.dry_run:
        ctx.info(f"Token {opts.id} revoked.")


api_calls = group("api-calls", "the log of your API calls (last 90 days)")


@api_calls.command(
    "list",
    "calls made with your tokens and connected apps, newest first",
    args=[
        arg("--token", type=int, help="only this token id"),
        arg("--connection", type=int, help="only this connected app id"),
        arg("--status", type=int, help="only this HTTP status, e.g. 429"),
    ],
    examples=("api-calls list --status 429", "api-calls list --token 42 --all --format csv"),
    paged=True,
)
def list_calls(ctx: Context, opts) -> None:
    params = {"token": opts.token, "connection": opts.connection, "status": opts.status}
    list_rows(ctx, opts, API_CALLS, CALL_COLUMNS, params)
