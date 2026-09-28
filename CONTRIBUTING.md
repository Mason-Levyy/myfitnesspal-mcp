# Contributing to myfitnesspal-mcp

Thanks for your interest. This project reverse-engineers MyFitnessPal's web
app, so contributions that keep it working as MFP changes are especially
valuable. Bug reports, endpoint captures, and pull requests are all welcome.

## Ground rules

- Be respectful. See the [Code of Conduct](CODE_OF_CONDUCT.md).
- **Never commit credentials.** No session cookies, `MFP_COOKIE` values,
  `cookies*` files, or real diary data in fixtures. See [SECURITY.md](SECURITY.md).
- Open an issue before starting large changes (new tools, transport changes,
  auth rework) so we can agree on the approach first.
- Small, focused pull requests are easier to review and land faster.

## Development setup

Requires [uv](https://docs.astral.sh/uv/) and Python 3.10+.

```bash
git clone https://github.com/Mason-Levyy/myfitnesspal-mcp
cd myfitnesspal-mcp
uv sync --extra autorefresh
uv run pre-commit install    # optional: runs lint/format on every commit
```

Run the same checks CI runs:

```bash
uv run pytest                 # tests (synthetic fixtures, no MFP account needed)
uv run ruff check .           # lint
uv run ruff format --check .  # formatting
```

`uv run ruff format .` fixes formatting; `uv run ruff check --fix .` applies
safe lint fixes.

To exercise the server against a real account, follow the
[Authentication](README.md#authentication) steps in the README and run
`uv run mfp-mcp`. Point an MCP client at it, or use the
[MCP Inspector](https://github.com/modelcontextprotocol/inspector).

## Project layout

| Path | Purpose |
| --- | --- |
| `src/myfitnesspal_mcp/server.py` | MCP tool definitions (`fitness_*`) |
| `src/myfitnesspal_mcp/diary.py` | Diary read/write against MFP endpoints |
| `src/myfitnesspal_mcp/mfp_client.py` | `curl_cffi` session with a Chrome TLS fingerprint |
| `src/myfitnesspal_mcp/auth.py` | Cookie capture, storage, and validation |
| `src/myfitnesspal_mcp/refresh.py` | Headless-browser session refresh |
| `src/myfitnesspal_mcp/store.py` | Local SQLite cache |
| `src/myfitnesspal_mcp/sync.py` | Gap-fill sync from MFP into the cache |
| `src/myfitnesspal_mcp/cli.py` | `mfp-mcp` entry point |
| `tests/` | Pytest suite with synthetic HTML/JSON fixtures |

## Making changes

1. Fork the repo and create a branch from `main`
   (`fix/short-description`, `feat/short-description`, `docs/...`, `chore/...`).
2. Make your change. Add or update tests for any behaviour change; new MFP
   endpoint parsing should ship with a fixture in `tests/`.
3. Keep fixtures synthetic. Scrub usernames, ids, and real food entries from
   anything captured from your own account.
4. Run `pytest`, `ruff check`, and `ruff format --check` locally.
5. Update `CHANGELOG.md` under **Unreleased** if the change is user-visible.
6. Open a pull request against `main`. Fill in the template and link any
   related issue.

### Pull request requirements

`main` is protected. A pull request can be merged only when:

- CI is green (tests on Python 3.10 and 3.13, lint, version consistency).
- All review conversations are resolved.

Maintainers squash-merge by default; keep the PR title in the style below
because it becomes the commit subject.

### Commit and PR titles

Use the imperative mood and say what changed, not how:

```
Fix weight backfill stranding weigh-ins on already-cached days
Add read/write for the MyFitnessPal daily diary note
```

### Code style

- Formatting and linting are enforced by [ruff](https://docs.astral.sh/ruff/);
  configuration lives in `pyproject.toml`.
- Prefer descriptive names over comments. Comment only where the *why* is not
  obvious (for example, an MFP quirk being worked around).
- Type hints on public functions. Tests are the exception; keep them readable.
- Match the surrounding code's idiom rather than introducing new patterns.

## Capturing MyFitnessPal endpoints

If MFP changes an endpoint, or you have found how a missing feature (such as
water logging) works, the most useful thing you can share is the request shape:

1. Open DevTools → Network on myfitnesspal.com and perform the action.
2. Note the method, URL, request headers that matter (CSRF token name, content
   type), and the request/response bodies.
3. **Redact** your cookie, any `authenticity_token`/CSRF value, user ids, and
   personal data before pasting into an issue.

## Releasing (maintainers)

Releases are cut automatically. Bump `version` in `pyproject.toml` **and** the
two version fields in `server.json` (CI fails if they drift), move the
**Unreleased** section of `CHANGELOG.md` under the new version, and merge to
`main`. The publish workflow tags `vX.Y.Z`, creates a GitHub release, publishes
to PyPI via trusted publishing, and pushes to the MCP Registry.

## License

By contributing you agree that your contributions are licensed under the
[MIT License](LICENSE).
