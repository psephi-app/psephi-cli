# psephi

Command-line client for the [Psephi](https://psephi.app) personal finance API (`/v1`):
transactions, accounts, categories, analytics and API tokens from your terminal and scripts.

> Not released yet: the first version is coming to PyPI and Homebrew.

## Install

```bash
uv tool install psephi        # recommended
pipx install psephi           # or with pipx
brew install psephi-app/psephi/psephi   # macOS, Homebrew tap
```

Python 3.11 or newer. Dependencies: `httpx` and `keyring`.

## Sign in

```bash
psephi login            # opens the browser, you approve the scopes in Psephi
psephi login --device   # SSH or no browser: enter a short code on any device
psephi auth status      # who is signed in, scopes, until when
psephi logout           # revokes the session on the server and forgets it here
```

- `login` uses OAuth 2.1 with PKCE (S256) and a one-shot listener on
  `http://127.0.0.1:<random port>/callback` (RFC 8252). It checks `state` and the issuer (`iss`,
  RFC 9207) of the answer before exchanging the code.
- Over SSH or on Linux without a display `login` offers the device code (RFC 8628) itself.
- By default the sign-in asks for all scopes; narrow it with `--scope` (repeat), e.g.
  `psephi login --scope transactions:read --scope analytics:read`.
- Tokens are kept in the system keyring (macOS Keychain, Windows Credential Manager, Secret
  Service on Linux), one entry per API host — never in a plain file. The access token lives an
  hour and is refreshed transparently; refresh tokens rotate on every use. Parallel `psephi`
  runs take turns refreshing (a lock file in `~/.cache/psephi`), because the server treats a
  reused refresh token as theft and disconnects the app.
- When the session ends (signed out elsewhere, disconnected in **Account → Connected apps**,
  expired), commands say so and ask you to run `psephi login` again.
- Headless Linux without a Secret Service: use a personal token in `PSEPHI_TOKEN` instead.

### CI and scripts: personal token

Create a token in **Account → API tokens** (or `psephi tokens create`) with only the scopes the
job needs, store it as a CI secret and pass it in the environment:

```bash
export PSEPHI_TOKEN=psephi_pat_…
psephi transactions list --from 2026-11-01 --format json
```

`PSEPHI_TOKEN` wins over the keyring sign-in while it is set. It is used as is: personal
tokens are not refreshed.

## Examples

```bash
psephi me show
psephi me edit --time-zone Europe/Warsaw

psephi transactions list --from 2026-11-01 --to 2026-11-30
psephi transactions list --in-review --limit 20
psephi transactions list --account <account-id> --all --format csv > november.csv
psephi transactions show <id>
psephi transactions edit <id> --category <category-id> --description "Gift for Anna"
psephi transactions edit <id> --category none          # clear the category
psephi transactions confirm <id> --yes                 # mark as checked (source: api)
psephi transactions suggest-category <id>

psephi accounts list
psephi accounts edit <id> --name "Daily card" --dry-run
psephi categories list --type expense --all

psephi analytics categories --from 2026-10-01 --to 2026-10-31
psephi analytics balances

psephi tokens list
psephi tokens create --name ci --scope transactions:read --idle-days 30
psephi tokens revoke 42
psephi api-calls list --status 429

psephi ping             # API host, version and links; no sign-in needed
```

Every command has `--help` with its flags and examples.

### Output, changes and exit codes

- `--format table` (default), `json` (the API objects as they are — money stays a decimal
  string like `"42.5000"`) or `csv`. Data goes to stdout, messages to stderr.
- Lists are paged by the server with a cursor: `--limit N` (default 50), `--all` for every
  page, `--cursor` to continue where a previous call stopped (it prints the cursor).
- Commands that change data ask for confirmation on a terminal; `--yes` skips the question
  (required in scripts), `--dry-run` prints the request and sends nothing.
- Errors from the API (`application/problem+json`) are shown as title, detail and field
  messages. On `429` the client waits `Retry-After` once and retries; a second `429` is an error.
- Exit codes: `0` success, `1` error, `2` wrong arguments, `3` refused (no confirmation),
  `130` interrupted.

### Environment

| Variable | What |
|---|---|
| `PSEPHI_TOKEN` | personal token (`psephi_pat_…`); wins over `psephi login` |
| `PSEPHI_API_URL` | API host, default `https://api.psephi.app`; `--api` wins over it |

`--api` points the client at another Psephi server, e.g. a test one.

## Not in the API yet

Importing bank statements and exporting data are not part of `/v1`, so there are no commands
for them. They need, roughly:

- **import:** `POST /v1/imports/` (multipart file, account, format) → import job;
  `GET /v1/imports/{id}/` (status, counts, errors), and a dry-run/preview mode;
- **export:** `POST /v1/exports/` (period, accounts, format CSV/OFX) → job;
  `GET /v1/exports/{id}/` and a download link.

## Development

```bash
uv sync
uv run ruff check . && uv run ruff format --check .
uv run pytest --cov
uv run psephi ping
```

- Every API operation the client calls is declared in `src/psephi/endpoints.py`;
  `tests/test_contract.py` checks them against a snapshot of the server schema,
  `tests/fixtures/v1.yaml`. When the API changes, download `GET /v1/schema/` there, update
  the date in its first line and run the tests.
- A command is a function `(ctx, opts)` in
  a group, `mutates=True` gives `--dry-run` and the confirmation, `paged=True` gives
  `--limit/--all/--cursor`.

## Releasing

The workflow `.github/workflows/release.yml` runs on a tag `v*`: tests → build → PyPI via
trusted publishing (OIDC, no API tokens) → build provenance attestation → GitHub Release with
wheel and sdist → a PR to the tap `psephi-app/homebrew-psephi` with the new
`Formula/psephi.rb` (rendered by `packaging/homebrew/render_formula.py` from PyPI and `uv.lock`).

Set up once: the PyPI trusted publisher for this workflow (project `psephi`, owner
`psephi-app`, repository `psephi-cli`, workflow `release.yml`, environment `pypi`), the
GitHub environment `pypi` (tags `v*` only), and a GitHub App with *Contents* and *Pull
requests* read & write on `homebrew-psephi` only — secrets `TAP_APP_ID` and
`TAP_APP_PRIVATE_KEY` here.

To release: bump `__version__` in `src/psephi/__init__.py`, then
`git tag -s vX.Y.Z && git push origin vX.Y.Z` (the tag must match the version), review and
merge the formula PR in the tap.

The committed `packaging/homebrew/psephi.rb` is the same formula with a placeholder for the
package; CI checks that it matches `uv.lock`.

## License

MIT — see [LICENSE](LICENSE).
