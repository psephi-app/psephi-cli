"""Helpers shared by the command modules: argument types and list output."""

from __future__ import annotations

import argparse
import uuid
from collections.abc import Mapping
from datetime import date
from typing import Any

from psephi.cli.context import Context
from psephi.endpoints import Endpoint


def uuid_arg(value: str) -> str:
    try:
        return str(uuid.UUID(value))
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected an id (UUID), got {value!r}") from None


def date_arg(value: str) -> str:
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected a date YYYY-MM-DD, got {value!r}") from None


def list_rows(
    ctx: Context,
    opts: argparse.Namespace,
    endpoint: Endpoint,
    columns: list[str],
    params: Mapping[str, Any] | None = None,
) -> None:
    """A /v1 list with --limit / --all / --cursor; tells how to get the next page."""
    limit = None if opts.all else opts.limit
    rows = list(ctx.api.pages(endpoint, params=params, limit=limit, cursor=opts.cursor))
    ctx.table(rows, columns)
    if ctx.api.more:
        ctx.info(f"More rows: add --cursor {ctx.api.more} (or --all)")


def fields(**values: Any) -> dict[str, Any]:
    """The body of a PATCH: only what was given on the command line."""
    return {k: v for k, v in values.items() if v is not None}
