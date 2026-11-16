"""Profile, accounts, categories and analytics."""

from __future__ import annotations

import argparse

from psephi.cli.context import Context
from psephi.cli.registry import arg, group
from psephi.commands._common import date_arg, fields, list_rows, uuid_arg
from psephi.endpoints import (
    ACCOUNT,
    ACCOUNT_UPDATE,
    ACCOUNTS,
    ANALYTICS_BALANCES,
    ANALYTICS_CATEGORIES,
    CATEGORIES,
    CATEGORY,
    ME,
    ME_UPDATE,
)
from psephi.errors import UsageError

LOCALES = ("en", "ru", "pl")
ACCOUNT_COLUMNS = ["id", "name", "kind", "currency", "archived", "include_in_net_worth", "parent"]
CATEGORY_COLUMNS = ["id", "type", "name", "slug", "parent", "enabled"]
BALANCE_COLUMNS = ["account", "name", "balance", "booked_balance", "on_hold"]


def _nothing_to_change(body: dict) -> None:
    if not body:
        raise UsageError("nothing to change: give at least one field flag (see --help)")


# --- me ---
me = group("me", "your profile")


@me.command(
    "show", "profile: email, name, language, base currency, time zone", examples=("me show",)
)
def me_show(ctx: Context, opts) -> None:
    del opts
    ctx.record(ctx.api.call(ME))


@me.command(
    "edit",
    "change name, language, base currency or time zone",
    args=[
        arg("--name", dest="display_name", help="display name"),
        arg("--locale", choices=LOCALES, help="interface language"),
        arg("--currency", dest="base_currency", type=str.upper, help="base currency, ISO 4217"),
        arg("--time-zone", help="IANA name like Europe/Warsaw; '' to clear"),
    ],
    examples=("me edit --locale pl", "me edit --time-zone Europe/Warsaw --dry-run"),
    mutates=True,
)
def me_edit(ctx: Context, opts) -> None:
    body = fields(
        display_name=opts.display_name,
        locale=opts.locale,
        base_currency=opts.base_currency,
        time_zone=opts.time_zone,
    )
    _nothing_to_change(body)
    result = ctx.change(ME_UPDATE, f"Change profile ({', '.join(body)})?", body=body)
    if result is not None:
        ctx.record(result)


# --- accounts ---
accounts = group("accounts", "accounts and their settings")


@accounts.command(
    "list",
    "accounts (without service ones)",
    examples=("accounts list", "accounts list --all --format csv"),
    paged=True,
)
def accounts_list(ctx: Context, opts) -> None:
    list_rows(ctx, opts, ACCOUNTS, ACCOUNT_COLUMNS)


@accounts.command(
    "show",
    "one account",
    args=[arg("id", type=uuid_arg, help="account id")],
    examples=("accounts show <id>",),
)
def accounts_show(ctx: Context, opts) -> None:
    ctx.record(ctx.api.call(ACCOUNT, id=opts.id))


@accounts.command(
    "edit",
    "rename, archive, or include in net worth",
    args=[
        arg("id", type=uuid_arg, help="account id"),
        arg("--name", help="new name"),
        arg("--archived", action=argparse.BooleanOptionalAction, help="archive or unarchive"),
        arg(
            "--include-in-net-worth",
            action=argparse.BooleanOptionalAction,
            help="count in net worth or not",
        ),
    ],
    examples=("accounts edit <id> --name 'Daily card'", "accounts edit <id> --archived --yes"),
    mutates=True,
)
def accounts_edit(ctx: Context, opts) -> None:
    body = fields(
        name=opts.name, archived=opts.archived, include_in_net_worth=opts.include_in_net_worth
    )
    _nothing_to_change(body)
    result = ctx.change(ACCOUNT_UPDATE, f"Change account {opts.id}?", body=body, id=opts.id)
    if result is not None:
        ctx.record(result)


# --- categories ---
categories = group("categories", "the category list")


@categories.command(
    "list",
    "categories, optionally of one type",
    args=[arg("--type", choices=("expense", "income"), help="only expense or income categories")],
    examples=("categories list --all", "categories list --type income --format json"),
    paged=True,
)
def categories_list(ctx: Context, opts) -> None:
    list_rows(ctx, opts, CATEGORIES, CATEGORY_COLUMNS, {"type": opts.type})


@categories.command(
    "show",
    "one category",
    args=[arg("id", type=uuid_arg, help="category id")],
    examples=("categories show <id>",),
)
def categories_show(ctx: Context, opts) -> None:
    ctx.record(ctx.api.call(CATEGORY, id=opts.id))


# --- analytics ---
analytics = group("analytics", "summaries computed from transactions")


@analytics.command(
    "categories",
    "spending and income by category for a period (no conversion between currencies)",
    args=[
        arg("--from", dest="date_from", type=date_arg, required=True, help="first day, YYYY-MM-DD"),
        arg("--to", dest="date_to", type=date_arg, help="last day (default — today, UTC)"),
    ],
    examples=("analytics categories --from 2026-10-01 --to 2026-10-31",),
)
def analytics_categories(ctx: Context, opts) -> None:
    summary = ctx.api.call(
        ANALYTICS_CATEGORIES, params={"date_from": opts.date_from, "date_to": opts.date_to}
    )
    if ctx.format == "json":
        ctx.record(summary)
        return
    rows = [
        {"type": kind, **row} for kind in ("expense", "income") for row in summary.get(kind) or []
    ]
    ctx.table(rows, ["type", "category", "amount"])
    ctx.info(f"Period: {summary.get('date_from')} — {summary.get('date_to')}")


@analytics.command(
    "balances",
    "balances by account: available, booked, on hold",
    examples=("analytics balances", "analytics balances --all --format csv"),
    paged=True,
)
def analytics_balances(ctx: Context, opts) -> None:
    list_rows(ctx, opts, ANALYTICS_BALANCES, BALANCE_COLUMNS)
