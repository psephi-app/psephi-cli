"""The two ways to sign in: browser with a loopback redirect (default) and device code."""

from __future__ import annotations

import os
import sys
from collections.abc import Callable, Mapping
from urllib.parse import urlencode

from psephi.auth.loopback import LoopbackReceiver
from psephi.auth.oauth import Metadata, OAuth, Pkce, new_state
from psephi.auth.store import Credentials
from psephi.config import CLIENT_ID

BROWSER_TIMEOUT = 300  # seconds to finish the sign-in in the browser


def browser_available(
    environ: Mapping[str, str] | None = None, platform: str = sys.platform
) -> bool:
    """False where opening a browser makes no sense: an SSH session or Linux without a display."""
    env = os.environ if environ is None else environ
    has_display = bool(env.get("DISPLAY") or env.get("WAYLAND_DISPLAY"))
    if env.get("SSH_CONNECTION") or env.get("SSH_TTY"):
        return has_display
    if platform.startswith("linux") or "bsd" in platform:
        return has_display
    return True


def authorize_url(meta: Metadata, redirect_uri: str, scope: str, state: str, pkce: Pkce) -> str:
    query = {
        "response_type": "code",
        "client_id": CLIENT_ID,
        "redirect_uri": redirect_uri,
        "scope": scope,
        "state": state,
        "code_challenge": pkce.challenge,
        "code_challenge_method": "S256",
    }
    return f"{meta.authorization_endpoint}?{urlencode(query)}"


def login_browser(
    oauth: OAuth,
    meta: Metadata,
    scope: str,
    *,
    open_browser: Callable[[str], bool],
    notice: Callable[[str], None],
    timeout: float | None = None,
) -> Credentials:
    state, pkce = new_state(), Pkce.new()
    with LoopbackReceiver(state, meta.issuer, meta.iss_parameter_supported) as receiver:
        url = authorize_url(meta, receiver.redirect_uri, scope, state, pkce)
        opened = open_browser(url)
        if opened:
            notice("Opened the browser to sign in. If it did not open, visit:")
        else:
            notice("Open this address in a browser on this computer to sign in:")
        notice(f"  {url}")
        notice("Waiting for the browser…")
        code = receiver.wait(BROWSER_TIMEOUT if timeout is None else timeout)
        return oauth.exchange_code(meta, code, receiver.redirect_uri, pkce.verifier)


def login_device(
    oauth: OAuth, meta: Metadata, scope: str, *, notice: Callable[[str], None]
) -> Credentials:
    device = oauth.device_authorization(meta, scope)
    minutes = max(1, int(device.get("expires_in") or 300) // 60)
    notice(f"On any device where you are signed in to Psephi, open {device['verification_uri']}")
    notice(f"and enter the code:  {device['user_code']}")
    if device.get("verification_uri_complete"):
        notice(f"(or open {device['verification_uri_complete']})")
    notice(
        f"Check that the code matches this terminal. The code is valid for {minutes} min. "
        "Waiting for approval…"
    )
    return oauth.poll_device(meta, device)
