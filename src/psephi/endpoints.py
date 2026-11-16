"""Every API operation the client calls — one place, checked against the /v1 schema snapshot
by tests/test_contract.py. Commands never build paths by hand."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import quote


@dataclass(frozen=True)
class Endpoint:
    method: str
    path: str  # OpenAPI template: /v1/transactions/{id}/

    def url(self, **params: object) -> str:
        """The path with parameters filled in; each value is quoted as one path segment."""
        return self.path.format(**{k: quote(str(v), safe="") for k, v in params.items()})


OAUTH_METADATA = Endpoint("GET", "/.well-known/oauth-authorization-server")
OAUTH_TOKEN = Endpoint("POST", "/oauth/token")
OAUTH_REVOKE = Endpoint("POST", "/oauth/revoke")
OAUTH_DEVICE = Endpoint("POST", "/oauth/device_authorization")

ROOT = Endpoint("GET", "/v1/")
TOKEN_CURRENT = Endpoint("GET", "/v1/token/")

ME = Endpoint("GET", "/v1/me/")
ME_UPDATE = Endpoint("PATCH", "/v1/me/")

TRANSACTIONS = Endpoint("GET", "/v1/transactions/")
TRANSACTION = Endpoint("GET", "/v1/transactions/{id}/")
TRANSACTION_UPDATE = Endpoint("PATCH", "/v1/transactions/{id}/")
TRANSACTION_CONFIRM = Endpoint("POST", "/v1/transactions/{id}/confirm/")
TRANSACTION_SUGGESTIONS = Endpoint("GET", "/v1/transactions/{id}/category-suggestions/")

ACCOUNTS = Endpoint("GET", "/v1/accounts/")
ACCOUNT = Endpoint("GET", "/v1/accounts/{id}/")
ACCOUNT_UPDATE = Endpoint("PATCH", "/v1/accounts/{id}/")

CATEGORIES = Endpoint("GET", "/v1/categories/")
CATEGORY = Endpoint("GET", "/v1/categories/{id}/")

ANALYTICS_CATEGORIES = Endpoint("GET", "/v1/analytics/categories/")
ANALYTICS_BALANCES = Endpoint("GET", "/v1/analytics/balances/")

TOKENS = Endpoint("GET", "/v1/tokens/")
TOKEN_CREATE = Endpoint("POST", "/v1/tokens/")
TOKEN_REVOKE = Endpoint("DELETE", "/v1/tokens/{id}/")

API_CALLS = Endpoint("GET", "/v1/api-calls/")

ALL: tuple[Endpoint, ...] = tuple(v for v in dict(globals()).values() if isinstance(v, Endpoint))
