"""Render the Homebrew formula for the tap psephi-app/homebrew-psephi (Formula/psephi.rb).

Resources come from uv.lock: every runtime dependency that applies on macOS with Homebrew's
Python, each with its sdist URL and sha256 (Homebrew builds everything from source). The
package itself comes from PyPI after the release is published:

    uv run python packaging/homebrew/render_formula.py --from-pypi 0.1.0 > psephi.rb
    uv run python packaging/homebrew/render_formula.py --url URL --sha256 HEX > psephi.rb

Without arguments it prints the formula with a placeholder for the package (the committed
template packaging/homebrew/psephi.rb is made that way).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import tomllib
import urllib.request
from pathlib import Path

from packaging.markers import Marker
from packaging.utils import canonicalize_name

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = "psephi"
PYTHON_FORMULA = "python@3.14"
# Homebrew on macOS with PYTHON_FORMULA: markers of dependencies are evaluated for it
TARGET = {
    "sys_platform": "darwin",
    "platform_system": "Darwin",
    "os_name": "posix",
    "implementation_name": "cpython",
    "platform_python_implementation": "CPython",
    "python_version": "3.14",
    "python_full_version": "3.14.0",
    "platform_machine": "arm64",
    "extra": "",
}
PYPI_ATTEMPTS = 10  # a fresh upload takes a moment to show in the JSON API
PYPI_PAUSE = 15

HEADER = """# Homebrew formula for psephi — rendered by packaging/homebrew/render_formula.py
# from uv.lock of psephi-app/psephi-cli. Do not edit resources by hand: re-render.
class Psephi < Formula
  include Language::Python::Virtualenv

  desc "Command-line client for the Psephi personal finance API"
  homepage "https://github.com/psephi-app/psephi-cli"
  url "{url}"
  sha256 "{sha256}"
  license "MIT"

  # Linux users install from PyPI (uv tool install psephi): the Linux keyring backend
  # needs cryptography, which Homebrew would have to build from source with Rust
  depends_on :macos
  depends_on "{python}"
"""

FOOTER = """
  def install
    virtualenv_install_with_resources
  end

  test do
    assert_match version.to_s, shell_output("#{bin}/psephi --version")
    assert_match "transactions", shell_output("#{bin}/psephi --help")
  end
end
"""


def lock_packages(lock_path: Path) -> dict[str, dict]:
    lock = tomllib.loads(lock_path.read_text(encoding="utf-8"))
    return {canonicalize_name(p["name"]): p for p in lock["package"]}


def applies(dependency: dict) -> bool:
    marker = dependency.get("marker")
    return marker is None or Marker(marker).evaluate(TARGET)


def runtime_closure(packages: dict[str, dict]) -> list[dict]:
    """Packages needed at run time on the target, without the package itself and dev groups."""
    seen: dict[str, dict] = {}
    queue = [d for d in packages[PACKAGE].get("dependencies", []) if applies(d)]
    while queue:
        name = canonicalize_name(queue.pop()["name"])
        if name in seen:
            continue
        seen[name] = packages[name]
        queue += [d for d in seen[name].get("dependencies", []) if applies(d)]
    return [seen[n] for n in sorted(seen)]


def resource(package: dict) -> str:
    sdist = package.get("sdist")
    if not sdist:
        raise SystemExit(f"{package['name']}: no sdist in uv.lock — Homebrew needs one")
    algorithm, digest = sdist["hash"].split(":", 1)
    if algorithm != "sha256":
        raise SystemExit(f"{package['name']}: expected a sha256 hash, got {algorithm}")
    return (
        f'\n  resource "{package["name"]}" do\n'
        f'    url "{sdist["url"]}"\n'
        f'    sha256 "{digest}"\n'
        "  end\n"
    )


def from_pypi(version: str) -> tuple[str, str]:
    api = f"https://pypi.org/pypi/{PACKAGE}/{version}/json"
    for attempt in range(PYPI_ATTEMPTS):
        try:
            with urllib.request.urlopen(api, timeout=30) as response:
                data = json.load(response)
            sdist = next(u for u in data["urls"] if u["packagetype"] == "sdist")
            return sdist["url"], sdist["digests"]["sha256"]
        except (OSError, StopIteration, KeyError) as err:
            if attempt == PYPI_ATTEMPTS - 1:
                raise SystemExit(f"{api}: no sdist ({err})") from err
            time.sleep(PYPI_PAUSE)
    raise AssertionError("unreachable")


def render(url: str, sha256: str, packages: dict[str, dict]) -> str:
    body = HEADER.format(url=url, sha256=sha256, python=PYTHON_FORMULA)
    body += "".join(resource(p) for p in runtime_closure(packages))
    return body + FOOTER


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--from-pypi", metavar="VERSION", help="take the sdist of this release")
    source.add_argument("--url", help="sdist URL (with --sha256)")
    parser.add_argument("--sha256", help="sdist sha256 (with --url)")
    parser.add_argument("--lock", type=Path, default=ROOT / "uv.lock")
    opts = parser.parse_args(argv)
    if opts.from_pypi:
        url, sha256 = from_pypi(opts.from_pypi)
    elif opts.url:
        if not opts.sha256:
            parser.error("--url needs --sha256")
        url, sha256 = opts.url, opts.sha256
    else:
        url = f"https://files.pythonhosted.org/packages/source/p/{PACKAGE}/{PACKAGE}-VERSION.tar.gz"
        sha256 = "TODO: sha256 of the sdist, filled in by the release workflow"
    sys.stdout.write(render(url, sha256, lock_packages(opts.lock)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
