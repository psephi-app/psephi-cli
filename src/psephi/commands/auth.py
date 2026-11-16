"""psephi login / logout / auth status."""

from __future__ import annotations

from psephi.auth import store
from psephi.auth.flows import browser_available, login_browser, login_device
from psephi.auth.session import EnvToken, KeyringSession
from psephi.cli.context import Context
from psephi.cli.registry import arg, group, top
from psephi.client import Api
from psephi.config import ENV_TOKEN
from psephi.endpoints import ME, ROOT, TOKEN_CURRENT
from psephi.errors import ApiError, CliError, UsageError

ACCOUNT_READ = ("account:read", "account:write")


@top(
    "login",
    "sign in through the browser (or with a device code) and keep the token in the keyring",
    args=[
        arg(
            "--device",
            action="store_true",
            help="sign in with a code on another device (SSH, no browser)",
        ),
        arg(
            "--scope",
            action="append",
            metavar="SCOPE",
            help="ask only for this scope (repeat; default — all), e.g. transactions:read",
        ),
    ],
    examples=(
        "login",
        "login --device",
        "login --scope transactions:read --scope analytics:read",
        "--api https://api.example.test login --device",
    ),
)
def login(ctx: Context, opts) -> None:
    if ctx.environ.get(ENV_TOKEN):
        ctx.info(
            f"Note: {ENV_TOKEN} is set and is used instead of this sign-in while it stays set."
        )
    meta = ctx.oauth.metadata()
    scope = _scope(opts.scope, meta.scopes_supported)
    device = opts.device or _offer_device(ctx)
    if device:
        creds = login_device(ctx.oauth, meta, scope, notice=ctx.info)
    else:
        creds = login_browser(ctx.oauth, meta, scope, open_browser=_opener(ctx), notice=ctx.info)
    previous = store.load(ctx.api_url)
    store.save(ctx.api_url, creds)
    if previous is not None:
        _revoke_quietly(ctx, previous)
    who = _email(Api(ctx.api_url, ctx.http, KeyringSession(ctx.api_url, ctx.oauth)), creds.scopes)
    ctx.info(f"Signed in to {ctx.api_url}" + (f" as {who}" if who else "") + ".")
    ctx.info(f"Scopes: {', '.join(creds.scopes) or '—'}")


def _scope(requested: list[str] | None, supported: tuple[str, ...]) -> str:
    if not requested:
        return " ".join(supported)
    unknown = [s for s in requested if supported and s not in supported]
    if unknown:
        raise UsageError(f"unknown scope {', '.join(unknown)}; known: {', '.join(supported)}")
    return " ".join(dict.fromkeys(requested))


def _offer_device(ctx: Context) -> bool:
    if browser_available(ctx.environ):
        return False
    if not ctx.interactive:
        ctx.info("No browser here (SSH session or no display): signing in with a device code.")
        return True
    ctx.stderr.write(
        "No browser here (SSH session or no display). Use a device code instead? [Y/n] "
    )
    ctx.stderr.flush()
    return ctx.stdin.readline().strip().lower() not in ("n", "no")


def _opener(ctx: Context):
    def open_browser(url: str) -> bool:
        try:
            return bool(ctx.open_browser(url)) if ctx.open_browser else False
        except Exception:  # no browser is not an error: the URL is printed
            return False

    return open_browser


def _revoke_quietly(ctx: Context, creds: store.Credentials) -> None:
    """The previous sign-in on this host is replaced: disconnect it on the server too."""
    if not creds.revocation_endpoint:
        return
    try:
        ctx.oauth.revoke(creds.revocation_endpoint, creds.refresh_token, "refresh_token")
    except CliError as err:
        ctx.info(f"Could not disconnect the previous sign-in ({err}).")


def _email(api: Api, scopes: list[str]) -> str | None:
    if not any(s in ACCOUNT_READ for s in scopes):
        return None
    try:
        return api.call(ME).get("email")
    except ApiError:
        return None


@top(
    "logout",
    "sign out: revoke the session on the server and remove it from the keyring",
    examples=("logout", "--api https://api.example.test logout"),
)
def logout(ctx: Context, opts) -> None:
    del opts
    creds = store.load(ctx.api_url)
    if ctx.environ.get(ENV_TOKEN):
        ctx.info(
            f"{ENV_TOKEN} is a personal token: unset it, and revoke it with "
            "`psephi tokens revoke <id>` or in Account → API tokens if it is no longer needed."
        )
    if creds is None:
        ctx.info(f"Not signed in to {ctx.api_url}.")
        return
    revoked = True
    if creds.revocation_endpoint:
        try:
            ctx.oauth.revoke(creds.revocation_endpoint, creds.refresh_token, "refresh_token")
        except CliError as err:
            revoked = False
            ctx.info(
                f"Could not revoke the session on the server ({err}). "
                "Disconnect it in Account → Connected apps."
            )
    store.delete(ctx.api_url)
    ctx.info(f"Signed out of {ctx.api_url}" + ("." if revoked else " on this computer."))


auth = group("auth", "sign-in state")


@auth.command(
    "status",
    "who is signed in, with which scopes and until when",
    examples=("auth status", "auth status --format json"),
)
def status(ctx: Context, opts) -> None:
    del opts
    source = ctx.tokens
    current = ctx.api.call(TOKEN_CURRENT)
    scopes = current.get("scopes") or []
    data = {
        "api": ctx.api_url,
        "source": ENV_TOKEN if isinstance(source, EnvToken) else "keyring (psephi login)",
        "email": _email(ctx.api, scopes),
        "kind": current.get("kind"),
        "client": (current.get("client") or {}).get("name") or current.get("name"),
        "token": current.get("prefix"),
        "scopes": scopes,
        "access_expires_at": current.get("access_expires_at"),
        "valid_until": current.get("valid_until") or "no end date",
        "expiry": current.get("expiry"),
    }
    if ctx.format == "json":
        data["token_info"] = current
    ctx.record(data)


@top(
    "ping",
    "check the API host: version and links (no sign-in needed)",
    examples=("ping", "--api https://api.example.test ping"),
)
def ping(ctx: Context, opts) -> None:
    del opts
    root = ctx.anonymous.call(ROOT)
    ctx.record({"api": ctx.api_url, "version": root.get("version"), **(root.get("links") or {})})
