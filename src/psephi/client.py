"""Thin HTTP client for /v1: bearer token, transparent refresh, problem+json errors,
one polite retry on 429 and cursor pagination."""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator, Mapping
from email.utils import parsedate_to_datetime
from typing import Any, Protocol
from urllib.parse import parse_qs, urlsplit

import httpx

from psephi.config import USER_AGENT
from psephi.endpoints import Endpoint
from psephi.errors import ApiError, CliError

TIMEOUT = httpx.Timeout(30.0, connect=10.0)
MAX_RETRY_WAIT = 120  # seconds: a longer Retry-After is reported, not waited out
MAX_PAGE_SIZE = 200  # /v1 caps page_size at 200


def new_http() -> httpx.Client:
    return httpx.Client(
        timeout=TIMEOUT,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        follow_redirects=False,  # a redirect must never carry the token to another place
    )


class TokenSource(Protocol):
    def token(self) -> str: ...

    def after_unauthorized(self) -> bool:
        """The server refused the token: True — a fresh one is ready, try once more."""
        ...


def retry_after_seconds(response: httpx.Response, now: float | None = None) -> int:
    """Retry-After as seconds: a number or an HTTP date (RFC 9110, 10.2.3); 1 if absent."""
    value = response.headers.get("Retry-After", "").strip()
    if value.isdigit():
        return int(value)
    if value:
        try:
            moment = parsedate_to_datetime(value).timestamp()
        except (TypeError, ValueError):
            return 1
        return max(0, int(moment - (now if now is not None else time.time()) + 0.999))
    return 1


def problem_of(response: httpx.Response) -> dict[str, Any]:
    """The problem+json body, or a stand-in when the server sent something else."""
    try:
        body = response.json()
    except ValueError:
        body = None
    if isinstance(body, dict) and ("title" in body or "code" in body):
        return body
    if isinstance(body, dict) and "error" in body:  # OAuth style (RFC 6749, 5.2)
        return {"title": body["error"], "detail": body.get("error_description"), "code": None}
    return {"title": f"HTTP {response.status_code} {response.reason_phrase}".strip()}


def send(
    request: Callable[[], httpx.Response],
    *,
    sleep: Callable[[float], None],
    notice: Callable[[str], None],
    where: str,
) -> httpx.Response:
    """Send once; on 429 wait Retry-After and send one more time."""
    response = _transport(request, where)
    if response.status_code != 429:
        return response
    wait = retry_after_seconds(response)
    if wait > MAX_RETRY_WAIT:
        raise _throttled(response, wait)
    notice(f"Rate limited, retrying in {wait} s…")
    sleep(wait)
    response = _transport(request, where)
    if response.status_code == 429:
        raise _throttled(response, retry_after_seconds(response))
    return response


def _throttled(response: httpx.Response, wait: int) -> ApiError:
    problem = problem_of(response)
    problem.setdefault("detail", None)
    if not problem.get("detail"):
        problem["detail"] = f"too many requests, try again in {wait} s"
    return ApiError(429, problem)


def _transport(request: Callable[[], httpx.Response], where: str) -> httpx.Response:
    try:
        return request()
    except httpx.TimeoutException as err:
        raise CliError(f"{where}: no answer in time ({type(err).__name__})") from err
    except httpx.HTTPError as err:
        raise CliError(f"cannot reach {where}: {err}") from err


def cursor_of(url: str) -> str | None:
    values = parse_qs(urlsplit(url).query).get("cursor")
    return values[0] if values else None


class Api:
    def __init__(
        self,
        api_url: str,
        http: httpx.Client,
        tokens: TokenSource | None,
        *,
        sleep: Callable[[float], None] = time.sleep,
        notice: Callable[[str], None] = lambda _msg: None,
    ):
        self.api_url = api_url
        self.http = http
        self.tokens = tokens
        self.sleep = sleep
        self.notice = notice
        self.more: str | None = None  # cursor of the page after the last one read by pages()

    def full_url(self, endpoint: Endpoint, **path: object) -> str:
        return self.api_url + endpoint.url(**path)

    def call(
        self,
        endpoint: Endpoint,
        *,
        params: Mapping[str, Any] | None = None,
        body: Mapping[str, Any] | None = None,
        url: str | None = None,
        **path: object,
    ) -> Any:
        """One operation; JSON answer (None for 204). Errors raise ApiError."""
        target = url or self.full_url(endpoint, **path)
        response = self._authorized(endpoint.method, target, params, body)
        if response.status_code == 401 and self.tokens and self.tokens.after_unauthorized():
            response = self._authorized(endpoint.method, target, params, body)
        if response.is_error:
            raise ApiError(response.status_code, problem_of(response))
        if response.status_code == 204 or not response.content:
            return None
        try:
            return response.json()
        except ValueError as err:
            raise CliError(f"{target}: the answer is not JSON") from err

    def _authorized(self, method, target, params, body) -> httpx.Response:
        headers = {"Authorization": f"Bearer {self.tokens.token()}"} if self.tokens else {}
        # None, not {}: an empty params mapping would wipe the query of a `next` URL
        clean = {k: v for k, v in (params or {}).items() if v is not None} or None
        return send(
            lambda: self.http.request(method, target, params=clean, json=body, headers=headers),
            sleep=self.sleep,
            notice=self.notice,
            where=self.api_url,
        )

    def pages(
        self,
        endpoint: Endpoint,
        *,
        params: Mapping[str, Any] | None = None,
        limit: int | None = 50,
        cursor: str | None = None,
    ) -> Iterator[dict]:
        """Rows of a /v1 list, following `next` until `limit` rows (None — all of them)."""
        query = dict(params or {})
        query["page_size"] = MAX_PAGE_SIZE if limit is None else min(limit, MAX_PAGE_SIZE)
        if cursor:
            query["cursor"] = cursor
        url: str | None = None
        left = limit
        self.more = None
        while True:
            page = self.call(endpoint, params=None if url else query, url=url)
            rows = page.get("results", [])
            url = page.get("next")
            if left is not None and len(rows) > left:
                yield from rows[:left]
                return  # the rest of this page is not shown; no cursor points to it
            yield from rows
            if left is not None:
                left -= len(rows)
            if not url:
                return
            self._check_same_origin(url)
            if left is not None and left <= 0:
                self.more = cursor_of(url)
                return

    def _check_same_origin(self, url: str) -> None:
        """`next` is a full URL from the server; the token goes only to the API host."""
        ours, theirs = urlsplit(self.api_url), urlsplit(url)
        if (ours.scheme, ours.netloc) != (theirs.scheme, theirs.netloc.lower()):
            raise CliError(f"the server sent a next page on another host ({theirs.netloc})")
