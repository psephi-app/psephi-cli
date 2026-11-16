"""Argument parsing and running a command.

`psephi` — the list of groups, `psephi <group>` — its commands, `--help` on any command —
flags and examples. Common flags (--api, --format, --yes, --quiet, --debug) work anywhere
on the line; --dry-run — only on commands that change data.
"""

from __future__ import annotations

import argparse
import contextlib
import os
import sys
import traceback
import webbrowser
from collections.abc import Callable, Mapping

import httpx

from psephi import __version__
from psephi.cli.context import FORMATS, Context
from psephi.cli.registry import GROUPS, TOP, Command
from psephi.client import new_http
from psephi.config import resolve_api_url
from psephi.errors import CliError

PROG = "psephi"
DEFAULTS = {"format": "table", "yes": False, "quiet": False, "debug": False, "api": None}
DEFAULT_LIMIT = 50


def _common() -> argparse.ArgumentParser:
    # SUPPRESS: a flag not given at this level does not overwrite one given at another
    common = argparse.ArgumentParser(add_help=False, argument_default=argparse.SUPPRESS)
    common.add_argument(
        "--api", metavar="URL", help="API host (default https://api.psephi.app, or PSEPHI_API_URL)"
    )
    common.add_argument("--format", choices=FORMATS, help="output: table (default), json, csv")
    common.add_argument("--yes", "-y", action="store_true", help="confirm changes without asking")
    common.add_argument("--quiet", "-q", action="store_true", help="no messages on stderr")
    common.add_argument("--debug", action="store_true", help="show a traceback on errors")
    return common


def _epilog(command: Command) -> str | None:
    notes = []
    if command.mutates:
        notes.append("Changes data: asks for confirmation (--yes to skip), --dry-run to preview.")
    if command.examples:
        notes.append("Examples:\n" + "\n".join(f"  {PROG} {e}" for e in command.examples))
    return "\n\n".join(notes) or None


def _add_command(subs, command: Command, common) -> None:
    parser = subs.add_parser(
        command.name,
        help=command.help,
        description=command.help,
        epilog=_epilog(command),
        parents=[common],
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    for names, kwargs in command.args:
        parser.add_argument(*names, **kwargs)
    if command.paged:
        pages = parser.add_mutually_exclusive_group()
        pages.add_argument(
            "--limit", type=_positive, default=DEFAULT_LIMIT, help=f"rows (default {DEFAULT_LIMIT})"
        )
        pages.add_argument("--all", action="store_true", help="all rows, page after page")
        parser.add_argument("--cursor", help="continue from this cursor (printed after a page)")
    if command.mutates:
        parser.add_argument(
            "--dry-run", action="store_true", help="show the request, do not send it"
        )
    parser.set_defaults(_command=command, _parser=parser)


def _positive(value: str) -> int:
    try:
        number = int(value)
    except ValueError:
        number = 0
    if number <= 0:
        raise argparse.ArgumentTypeError(f"expected a positive number, got {value!r}")
    return number


def build_parser() -> argparse.ArgumentParser:
    from psephi import commands  # noqa: F401 - registers the groups

    common = _common()
    formatter = argparse.RawDescriptionHelpFormatter
    root = argparse.ArgumentParser(
        prog=PROG,
        description="Psephi from the command line: psephi <group> <command> [flags]",
        epilog="Sign in first: psephi login (or set PSEPHI_TOKEN). Docs: https://api.psephi.app/v1/docs/",
        parents=[common],
        formatter_class=formatter,
    )
    root.add_argument("--version", action="version", version=f"{PROG} {__version__}")
    root.set_defaults(_parser=root)
    subs = root.add_subparsers(title="commands", metavar="<command>")
    for name in sorted(TOP):
        _add_command(subs, TOP[name], common)
    for name in sorted(GROUPS):
        group = GROUPS[name]
        parser = subs.add_parser(
            name,
            help=group.help,
            description=group.help,
            parents=[common],
            formatter_class=formatter,
        )
        parser.set_defaults(_parser=parser)
        group_subs = parser.add_subparsers(title="commands", metavar="<command>")
        for command in group.commands.values():
            _add_command(group_subs, command, common)
    return root


def main(
    argv: list[str] | None = None,
    *,
    stdin=None,
    stdout=None,
    stderr=None,
    environ: Mapping[str, str] | None = None,
    http_factory: Callable[[], httpx.Client] = new_http,
    sleep: Callable[[float], None] | None = None,
    open_browser: Callable[[str], bool] | None = None,
) -> int:
    """Run a command and return the exit code. Streams and environment are replaced in tests."""
    stdin, stdout, stderr = stdin or sys.stdin, stdout or sys.stdout, stderr or sys.stderr
    env = dict(os.environ if environ is None else environ)
    parser = build_parser()
    try:
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            opts = parser.parse_args(sys.argv[1:] if argv is None else argv)
    except SystemExit as exit_:  # --help (0) or wrong arguments (2)
        return exit_.code if isinstance(exit_.code, int) else 2
    for key, value in DEFAULTS.items():
        if not hasattr(opts, key):
            setattr(opts, key, value)
    command = getattr(opts, "_command", None)
    if command is None:
        opts._parser.print_help(stdout)
        return 0
    ctx = Context(
        api_url="",
        stdin=stdin,
        stdout=stdout,
        stderr=stderr,
        format=opts.format,
        yes=opts.yes,
        dry_run=getattr(opts, "dry_run", False),
        quiet=opts.quiet,
        interactive=bool(getattr(stdin, "isatty", lambda: False)()),
        environ=env,
        http_factory=http_factory,
        sleep=sleep,
        open_browser=open_browser or webbrowser.open,
    )
    try:
        ctx.api_url = resolve_api_url(opts.api, env)
        command.handler(ctx, opts)
    except CliError as err:
        if opts.debug:
            traceback.print_exc(file=stderr)
        ctx.error(err)
        return err.code
    except KeyboardInterrupt:
        stderr.write("\nInterrupted\n")
        return 130
    except Exception as err:  # unexpected: a short message, the traceback only with --debug
        if opts.debug:
            traceback.print_exc(file=stderr)
        ctx.error(CliError(f"{type(err).__name__}: {err}"))
        return 1
    finally:
        ctx.close()
    return 0


def run() -> None:
    sys.exit(main())
