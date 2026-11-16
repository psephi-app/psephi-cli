"""Command groups; importing this package registers them all."""

from psephi.commands import auth, resources, tokens, transactions

__all__ = ["auth", "resources", "tokens", "transactions"]
