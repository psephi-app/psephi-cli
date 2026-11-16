"""Command-line layer: registry of groups and commands, output, entry point.

A command is a function `(ctx, opts)` registered in a group; `mutates=True` gives it
--dry-run and asks for confirmation (--yes to skip); examples go to --help.
"""
