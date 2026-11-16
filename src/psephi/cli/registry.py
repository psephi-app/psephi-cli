"""Groups and commands. A nested group is a name with a space: group("auth") → `psephi auth …`."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field


def arg(*names: str, **kwargs: object) -> tuple[tuple[str, ...], dict]:
    """An argument of a command — exactly like argparse.add_argument."""
    return names, kwargs


@dataclass(frozen=True)
class Command:
    name: str
    help: str
    handler: Callable
    args: tuple = ()
    examples: tuple[str, ...] = ()
    mutates: bool = False  # sends a change: gets --dry-run and asks for confirmation
    paged: bool = False  # a /v1 list: gets --limit, --all and --cursor


@dataclass
class Group:
    name: str
    help: str
    commands: dict[str, Command] = field(default_factory=dict)

    def command(self, name, help, *, args=(), examples=(), mutates=False, paged=False):
        def register(handler):
            self.commands[name] = Command(
                name, help, handler, tuple(args), tuple(examples), mutates, paged
            )
            return handler

        return register


GROUPS: dict[str, Group] = {}
TOP: dict[str, Command] = {}  # commands without a group: login, logout


def group(name: str, help: str) -> Group:
    if name not in GROUPS:
        GROUPS[name] = Group(name, help)
    return GROUPS[name]


def top(name, help, *, args=(), examples=()):
    def register(handler):
        TOP[name] = Command(name, help, handler, tuple(args), tuple(examples))
        return handler

    return register
