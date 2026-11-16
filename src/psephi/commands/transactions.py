"""Transactions: list, show, edit, confirm, category suggestions."""

from __future__ import annotations

from psephi.cli.context import Context
from psephi.cli.registry import arg, group
from psephi.commands._common import date_arg, fields, list_rows, uuid_arg
from psephi.endpoints import (
    TRANSACTION,
    TRANSACTION_CONFIRM,
    TRANSACTION_SUGGESTIONS,
    TRANSACTION_UPDATE,
    TRANSACTIONS,
)
from psephi.errors import UsageError

COLUMNS = [
    "id", "date", "direction", "amount", "merchant_name", "description", "category",
    "status", "confirmed",
]  # fmt: skip
NO_CATEGORY = "none"

transactions = group("transactions", "your transactions")


@transactions.command(
    "list",
    "transactions, newest first",
    args=[
        arg("--from", dest="date_from", type=date_arg, help="from this day, YYYY-MM-DD"),
        arg("--to", dest="date_to", type=date_arg, help="up to this day, YYYY-MM-DD"),
        arg("--account", type=uuid_arg, help="account id (includes its sub-accounts)"),
        arg("--category", type=uuid_arg, help="category id (a group includes its children)"),
        arg("--in-review", action="store_true", default=None, help="only those waiting for review"),
    ],
    examples=(
        "transactions list --from 2026-11-01",
        "transactions list --in-review --limit 20",
        "transactions list --account <id> --all --format csv > november.csv",
    ),
    paged=True,
)
def list_transactions(ctx: Context, opts) -> None:
    params = {
        "date_from": opts.date_from,
        "date_to": opts.date_to,
        "account": opts.account,
        "category": opts.category,
        "in_review": "true" if opts.in_review else None,
    }
    list_rows(ctx, opts, TRANSACTIONS, COLUMNS, params)


@transactions.command(
    "show",
    "one transaction",
    args=[arg("id", type=uuid_arg, help="transaction id")],
    examples=("transactions show <id>",),
)
def show(ctx: Context, opts) -> None:
    ctx.record(ctx.api.call(TRANSACTION, id=opts.id))


def _category(value: str) -> str:
    if value.lower() == NO_CATEGORY:
        return NO_CATEGORY
    return uuid_arg(value)


@transactions.command(
    "edit",
    "set the category or the comment",
    args=[
        arg("id", type=uuid_arg, help="transaction id"),
        arg("--category", type=_category, help=f"category id, or '{NO_CATEGORY}' to clear it"),
        arg("--description", help="your comment"),
        arg(
            "--drop-parts", action="store_true", default=None, help="merge a split transaction back"
        ),
    ],
    examples=(
        "transactions edit <id> --category <category-id>",
        "transactions edit <id> --description 'Gift for Anna' --dry-run",
    ),
    mutates=True,
)
def edit(ctx: Context, opts) -> None:
    body = fields(description=opts.description, drop_parts=opts.drop_parts)
    if opts.category is not None:
        body["category"] = None if opts.category == NO_CATEGORY else opts.category
    if not body:
        raise UsageError("nothing to change: give --category, --description or --drop-parts")
    result = ctx.change(TRANSACTION_UPDATE, f"Change transaction {opts.id}?", body=body, id=opts.id)
    if result is not None:
        ctx.record(result)


@transactions.command(
    "confirm",
    "mark as checked (like «Correct» in the inbox; the mark says it came from the API)",
    args=[arg("id", type=uuid_arg, help="transaction id")],
    examples=("transactions confirm <id> --yes",),
    mutates=True,
)
def confirm(ctx: Context, opts) -> None:
    result = ctx.change(TRANSACTION_CONFIRM, f"Confirm transaction {opts.id}?", id=opts.id)
    if result is not None:
        ctx.record(result)


@transactions.command(
    "suggest-category",
    "category suggestions from the categorizer",
    args=[arg("id", type=uuid_arg, help="transaction id")],
    examples=("transactions suggest-category <id>",),
)
def suggest_category(ctx: Context, opts) -> None:
    data = ctx.api.call(TRANSACTION_SUGGESTIONS, id=opts.id)
    if ctx.format == "json":
        ctx.record(data)
        return
    ctx.table(data.get("suggestions") or [], ["category", "probability"])
    ctx.info(
        f"Best guess: {data.get('category') or '—'} "
        f"(confidence {data.get('confidence')}, source {data.get('source')})"
    )
