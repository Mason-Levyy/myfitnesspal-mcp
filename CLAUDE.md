# CLAUDE.md

Guidance for Claude Code when working in this repository.

## Project

mfp-mcp is an MCP server that reads and writes a MyFitnessPal account from any
MCP client: search and log foods, weight, water, exercise, and the daily note,
plus trends and exports from a local cache. It ships to PyPI as `mfp-mcp` and to
the MCP Registry, and people run it against their real accounts.

MyFitnessPal has no public API. Every read and write replicates a request the
MFP web app itself sends, over a `curl_cffi` session that impersonates Chrome so
Cloudflare lets it through. When MFP changes its site, this project breaks, and
most maintenance is keeping up with those changes.

## Commands

Use uv for everything. `uv.lock` is gitignored, so don't commit one, and don't
`pip install` into the project.

```bash
uv sync --extra autorefresh     # install, including the optional browser refresh
uv run pytest                   # tests: no network, no MFP account needed
uv run ruff check .             # lint (--fix applies safe fixes)
uv run ruff format --check .    # formatting (drop --check to apply)
uv run mfp-mcp auth             # connect a real account
uv run mfp-mcp                  # serve over stdio against that account
```

CI runs lint, pytest on Python 3.10 and 3.13, and a version-consistency check.
Run pytest and both ruff commands before pushing.

## Layout

The package is `src/myfitnesspal_mcp/`. A tool call flows top to bottom:

- `server.py` defines the MCP tools (`fitness_*`). It parses arguments, runs
  blocking work through `with_session`, and shapes the response. HTTP calls and
  SQL don't go here.
- `food_logging.py` owns the draft, confirm, and pin flow for logging food.
  `food_ranking.py` ranks candidates as pure functions.
- `diary.py` makes the MFP web requests: food search, diary add and remove,
  weight, water, notes, and exercise. Each function takes the client first.
- `mfp_client.py` holds the `curl_cffi` client, the cached instance, and
  `is_auth_error`. `auth.py` captures and stores cookies, `refresh.py` renews
  the session in a headless browser, and `config.py` reads env vars and paths.
- `store.py` is the local SQLite cache, and all SQL lives there. `sync.py`
  gap-fills the cache from MFP.

## Rules that aren't obvious from one file

- **stdout belongs to the MCP protocol.** Under stdio, a `print` anywhere on the
  server path corrupts the stream. Use `logging`, which `cli.py` sends to
  stderr. `print` is fine only in the `auth` command.
- **Every MFP call from a tool goes through `with_session` or
  `run_with_refresh`.** They run blocking work in a thread, and on an auth
  failure they refresh the session and retry once. Calling
  `mfp_client.get_client()` directly from a tool skips both.
- **Keep user-input errors out of the auth path.** `mfp_client.is_auth_error`
  decides whether a failure triggers a session refresh, and unrecognised
  exceptions fall through to a keyword regex over the message. Errors caused by
  user input, like an unknown meal or no matching entry, subclass
  `diary.DiaryLookupError`. Their messages quote food names that can contain
  words like "session", so they must never reach that regex.
- **A model reads the error messages, so make them actionable.** Say what was
  wrong and how to recover: list what's actually logged, which meals exist, or
  what the valid range is. `no_matching_entry` and `AmbiguousEntry` show the
  pattern.
- **Tool docstrings are the descriptions clients show the model.** They are
  product surface, not comments. When behavior changes, update the argument
  formats, defaults, and cross-tool guidance in them to match.
- **MFP writes are not idempotent.** `/food/add` adds a duplicate every time,
  and `/food/water` sets the whole day's total. Don't add retries around writes
  beyond the single auth-refresh retry. After a write, refresh the cached day
  with `refresh_after_write` instead of re-sending anything.
- **Local-only data never goes to MFP.** Feel notes, food pins, and drafts live
  only in SQLite.
- **Schema changes need a migration.** Users keep databases from older
  releases. New tables go in `SCHEMA` as `CREATE ... IF NOT EXISTS`, and new
  columns on existing tables also go in `Store._migrate`.
- **Dependency pins are deliberate.** `myfitnesspal` is pinned exactly because
  `CurlCffiClient` overrides and calls its private methods
  (`_get_user_metadata`, `_get_food_item_details`, `_get_exercises`), so check
  that they still exist before bumping it. The upper bounds on `mcp` and
  `curl-cffi` stay until someone has tested a new major against a real account.

## Code standards

- **Names over comments.** Comment only the *why* that the code can't show,
  which here is usually an MFP quirk and how it was verified (see
  `ML_PER_WATER_UNIT`). Don't write comments that restate the code,
  commented-out code, changelog notes, or section banners, and delete dead
  code. Docstrings follow the same rule, except tool docstrings (above).
- **Extract on the second use, not the first.** Don't build abstractions for
  needs that don't exist yet. Once logic appears twice, pull it out and put it
  next to its peers. Request plumbing that endpoints share, for example, goes
  with the other request helpers instead of being copied into each endpoint.
- **One job per module.** If a module needs "and" to describe it, it should be
  two. A function past about 50 lines wants splitting, and parameters after the
  fourth should be keyword-only (`*`). These are smells to investigate, not hard
  failures.
- **Swallowed exceptions are narrow or logged.** Never write
  `except Exception: pass`. Catch the specific error, or log what was dropped
  and why it's safe to continue, as `sync.tolerating_failures` does.
- **Public functions have type hints.** MFP-facing functions take the client as
  their first argument and leave it duck-typed, so tests can pass
  `conftest.FakeClient`.
- **New runtime dependencies need a reason.** Every user installs them. Prefer
  the stdlib and what's already in `pyproject.toml`.

## Testing

- The pytest suite lives in `tests/` and never touches the network.
  `conftest.py` provides `FakeClient` and `FakeSession`, which route by URL
  fragment and serve HTML fixtures from `tests/fixtures/`.
- Every behavior change needs a test, and a bug fix needs a test that fails
  without the fix. New endpoint parsing ships with a fixture.
- Fixtures are synthetic. Scrub usernames, user ids, tokens, and real food
  entries from anything captured from a real account.
- Tests can't prove that a changed request still works against MFP. If you
  couldn't try it against a real account, say so in the PR.

## Security

- Never commit or log cookies, `MFP_COOKIE` values, bearer or CSRF tokens, or
  real diary data.
- `cookies.json` is written with mode `0600`, and it stays that way.
- Exception messages can reach the MCP client verbatim, so they must never
  include cookies or tokens.

## Conventions

- PR and commit titles use the imperative mood and say what changed, e.g. "Fix
  weight backfill stranding weigh-ins on already-cached days". Maintainers
  squash-merge, so the PR title becomes the commit subject.
- User-visible changes get an entry under **Unreleased** in `CHANGELOG.md`.
  Don't create any other new docs unless asked.
- A release bumps `version` in `pyproject.toml` and both version fields in
  `server.json` together, and CI fails if they drift. CONTRIBUTING.md has the
  full steps.
