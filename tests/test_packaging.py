"""The Homebrew formula is rendered from uv.lock with macOS-only resources."""

from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "packaging" / "homebrew" / "render_formula.py"


def load_script():
    spec = importlib.util.spec_from_file_location("render_formula", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_resources_cover_runtime_dependencies_for_macos():
    render = load_script()
    names = {p["name"] for p in render.runtime_closure(render.lock_packages(ROOT / "uv.lock"))}

    assert {"httpx", "httpcore", "h11", "anyio", "idna", "certifi", "keyring"} <= names
    assert not names & {"secretstorage", "jeepney", "cryptography", "pywin32-ctypes"}
    assert not names & {"pytest", "respx", "ruff", "pyyaml", "psephi"}


def test_rendered_formula_has_package_and_resources():
    render = load_script()
    text = render.render(
        "https://example.test/psephi-1.0.tar.gz", "ab" * 32, render.lock_packages(ROOT / "uv.lock")
    )

    assert 'url "https://example.test/psephi-1.0.tar.gz"' in text
    assert f'sha256 "{"ab" * 32}"' in text
    assert 'resource "keyring" do' in text
    assert "virtualenv_install_with_resources" in text


def test_committed_template_is_up_to_date(capsys):
    render = load_script()
    render.main([])
    assert capsys.readouterr().out == (ROOT / "packaging" / "homebrew" / "psephi.rb").read_text()
