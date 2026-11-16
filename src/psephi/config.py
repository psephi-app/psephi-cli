"""Where the API lives and who the client is."""

from __future__ import annotations

import os
from collections.abc import Mapping
from urllib.parse import urlsplit

from psephi import __version__
from psephi.errors import UsageError

DEFAULT_API_URL = "https://api.psephi.app"
CLIENT_ID = "psephi-cli"  # public OAuth client (no secret), known to the server
USER_AGENT = f"psephi-cli/{__version__}"

ENV_API_URL = "PSEPHI_API_URL"
# a personal token (psephi_pat_…) for CI; wins over the keyring
ENV_TOKEN = "PSEPHI_TOKEN"  # noqa: S105 - the name of a variable, not a secret

# plain http only for a server on this machine (development, tests)
_LOCAL_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})


def normalize_api_url(value: str) -> str:
    """`https://host[:port]` without a path: the API root, where /v1/ and /oauth/ live."""
    raw = value.strip()
    parts = urlsplit(raw)
    if parts.scheme not in ("https", "http") or not parts.hostname:
        raise UsageError(f"--api {raw!r}: expected a URL like https://api.psephi.app")
    if parts.scheme == "http" and parts.hostname not in _LOCAL_HOSTS:
        raise UsageError(f"--api {raw!r}: only https is allowed (http — for 127.0.0.1)")
    if parts.username or parts.password or parts.query or parts.fragment:
        raise UsageError(f"--api {raw!r}: no credentials, query or fragment in the URL")
    if parts.path.rstrip("/") not in ("", "/v1"):
        raise UsageError(f"--api {raw!r}: give the host only, e.g. https://api.psephi.app")
    return f"{parts.scheme}://{parts.netloc}".lower()


def resolve_api_url(flag: str | None, environ: Mapping[str, str] | None = None) -> str:
    """--api, then PSEPHI_API_URL, then production."""
    env = os.environ if environ is None else environ
    return normalize_api_url(flag or env.get(ENV_API_URL) or DEFAULT_API_URL)
