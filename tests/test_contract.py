"""Contract: every operation the client calls exists in the /v1 schema snapshot.

The snapshot is tests/fixtures/v1.yaml (its first line says when it was taken). When the
API changes, download the schema again (GET /v1/schema/) and run this test.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
import yaml

from psephi import endpoints
from psephi.cli.main import build_parser

SNAPSHOT = Path(__file__).parent / "fixtures" / "v1.yaml"
SOURCE = Path(__file__).parents[1] / "src" / "psephi"


@pytest.fixture(scope="module")
def schema():
    return yaml.safe_load(SNAPSHOT.read_text(encoding="utf-8"))


def test_snapshot_says_what_it_is():
    first = SNAPSHOT.read_text(encoding="utf-8").splitlines()[0]
    assert first.startswith("# Snapshot of the public Psephi API schema")


@pytest.mark.parametrize("endpoint", endpoints.ALL, ids=lambda e: f"{e.method} {e.path}")
def test_endpoint_is_in_schema(schema, endpoint):
    operations = schema["paths"].get(endpoint.path)
    assert operations is not None, f"{endpoint.path} is not in the /v1 schema"
    assert endpoint.method.lower() in operations, (
        f"{endpoint.method} {endpoint.path} is not in the schema"
    )


def test_registry_is_complete():
    assert len(endpoints.ALL) == len({(e.method, e.path) for e in endpoints.ALL})
    assert len(endpoints.ALL) >= 20


def _string_constants(path: Path):
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            yield node.value


def test_commands_do_not_build_api_paths_by_hand():
    """Paths live in psephi/endpoints.py only — so the contract test above sees them all."""
    offenders = [
        f"{path.name}: {value!r}"
        for path in SOURCE.rglob("*.py")
        if path.name != "endpoints.py"
        for value in _string_constants(path)
        if value.startswith(("/v1/", "/oauth/", "/.well-known/"))
    ]
    assert offenders == []


def test_query_parameters_are_in_schema(schema):
    """Filters the commands send exist on their operations."""
    expected = {
        "/v1/transactions/": {"date_from", "date_to", "account", "category", "in_review"},
        "/v1/categories/": {"type"},
        "/v1/analytics/categories/": {"date_from", "date_to"},
        "/v1/api-calls/": {"token", "connection", "status"},
    }
    for path, names in expected.items():
        declared = {p["name"] for p in schema["paths"][path]["get"].get("parameters", [])}
        assert names <= declared, (path, names - declared)
        if "cursor" in declared:
            assert {"cursor", "page_size"} <= declared


def test_patch_fields_are_in_schema(schema):
    components = schema["components"]["schemas"]
    expected = {
        "PatchedV1MeUpdateRequest": {"display_name", "locale", "base_currency", "time_zone"},
        "PatchedV1AccountUpdateRequest": {"name", "archived", "include_in_net_worth"},
        "PatchedV1TransactionUpdateRequest": {"category", "description", "drop_parts"},
        "ApiTokenCreateRequest": {"name", "scopes", "expiry", "expires_at", "idle_days"},
    }
    for name, fields in expected.items():
        assert fields <= set(components[name]["properties"]), name


def test_scope_choices_match_schema(schema):
    from psephi.commands.tokens import SCOPES

    assert list(SCOPES) == schema["components"]["schemas"]["ScopesEnum"]["enum"]


def test_locale_choices_match_schema(schema):
    from psephi.commands.resources import LOCALES

    assert list(LOCALES) == schema["components"]["schemas"]["LocaleEnum"]["enum"]


def test_every_command_has_help_and_examples():
    parser = build_parser()
    from psephi.cli.registry import GROUPS, TOP

    commands = list(TOP.values()) + [c for g in GROUPS.values() for c in g.commands.values()]
    assert commands
    for command in commands:
        assert command.help, command.name
        assert command.examples, command.name
    assert parser.prog == "psephi"
