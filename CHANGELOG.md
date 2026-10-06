# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- An icon, a website link and a clearer description in `server.json`, so the
  MCP Registry and the directories that mirror it show the server properly.
- `glama.json`, so the Glama listing can be claimed from the repo.
- `auth --clipboard` waits for the session cookie on the macOS clipboard and
  connects as soon as you copy it, skipping anything that isn't a session
  token (such as the command itself) and any token that doesn't authenticate.
- Day records (`fitness_get_day`, the `day` in write results, and
  `fitness_bulk_export`) include `goals` (calories, protein, carbs, fat) and
  `remaining` (goal minus logged; negative means over the goal).

### Changed

- `auth --check` renews the session as it checks and saves the cookie
  MyFitnessPal re-issues, and exits 1 when MyFitnessPal won't renew the
  session, so a scheduled check keeps an idle session alive and warns before
  it lapses. Nothing to do on upgrade; scripts that read the exit code now
  also see 1 for a session that is about to lapse.
- `cookies.json` now also holds MyFitnessPal's long-lived remember-me cookies
  next to the session token. The file's format is unchanged.
- The cache database gains three `day_nutrition` columns (`goal_protein`,
  `goal_carbs`, `goal_fat`), added in place on first start. No refetch
  happens on upgrade, so days cached by an earlier release show null
  protein, carbs and fat goals in `fitness_bulk_export` until
  `fitness_get_day` or a write refetches them.

### Fixed

- Saved sessions no longer stop working within about a day. Building a
  client can sign in again through `/account/login`, which issues a new
  session token and refresh token on every call; the server kept them in
  memory only, so `cookies.json` stayed on the pasted login and later
  processes sent a superseded session until MyFitnessPal rejected it. Client
  builds, tool calls, `auth`, and `auth --check` now save the cookies a
  browser would keep whenever MyFitnessPal re-issues them.
- Sessions no longer lapse 30 days after pasting. Signing in through the
  remember-me cookie never re-issues the session token, so each client build
  now reads `/api/auth/session`, which renews it for another 30 days.
- Pasting the cookie into the `auth` prompt no longer hangs on macOS. The
  prompt read a canonical-mode line, which macOS caps at 1024 bytes; the
  session token is about 1,600 characters, so the paste was cut short and
  Enter was dropped. The prompt now reads with canonical mode off.
- `cookies.json` is replaced atomically, so a process loading it while
  another saves never reads half a file.

## [0.4.3] - 2026-10-22

### Added

- Every tool now has a display title and MCP tool annotations
  (`readOnlyHint`, `destructiveHint`, `idempotentHint`, `openWorldHint`).
  Clients that support annotations can skip confirmation for read-only tools
  and warn before deletes and overwrites.
- `llms-install.md`: install steps for AI coding agents.
- A Claude Code plugin in `plugin/`, installable with
  `/plugin install myfitnesspal-mcp --marketplace Mason-Levyy/myfitnesspal-mcp`.
  It uses the session saved by `mfp-mcp auth`.
- One-click install buttons for Cursor, VS Code, and VS Code Insiders, and
  setup commands for Codex CLI and Gemini CLI.

### Fixed

- Reconnect hints now say `uvx mfp-mcp auth`. They named `myfitnesspal-mcp auth`,
  which isn't on the PATH after a `uvx` install and isn't a package on PyPI.
- A tool call that fails after a session refresh no longer repeats the
  reconnect hint twice in its error.

## [0.4.2] - 2026-10-20

### Fixed

- `auth` no longer fails to seed the auto-refresh browser profile when the
  pasted Cookie header includes a `__Host-` cookie (such as
  `__Host-next-auth.csrf-token`). Chrome rejects a `__Host-` cookie that has a
  Domain, and one rejected cookie failed the whole batch with "Invalid cookie
  fields".
- `fitness_delete_food` and `fitness_modify_food` can now remove one of two
  identical diary entries, such as the same food logged twice to one meal.
  Previously no query could tell them apart, so the call always failed with
  "matches multiple diary entries". Entries count as identical only when they
  share a name and meal. MFP's entry name includes the quantity, so the same
  food at different quantities still asks you to pick one. `fitness_delete_exercise` does the
  same for identical exercise rows. (#30)

## [0.4.1] - 2026-09-30

### Added

- `auth --check` reports where the session comes from, whether MyFitnessPal
  still accepts it, and whether auto-refresh is ready. It never prompts or
  saves, and exits 1 when the session needs attention.

### Fixed

- When several tool calls hit an expired session at once, only one
  headless-browser refresh runs and the others reuse it. Previously each call
  launched Chromium against the same profile, and every launch after the
  first failed.
- Food diary reads and writes, and cache syncs, use `/food/diary?date=`
  instead of `/food/diary/{username}?date=`. Since 2026-09-30 MyFitnessPal
  answers the username form with its marketing homepage, which made every
  diary write fail as signed out and let syncs cache days as empty (#24).
- Parallel tool calls on the first request of the day fetch the sync window
  from MyFitnessPal once instead of once per call, and no longer read a day's
  diary while a sync is rewriting it.

## [0.4.0] - 2026-09-28

### Added

- `fitness_get_exercise_entries` lists a day's cardio and strength entries
  with each section's own columns; `fitness_delete_exercise` removes one
  entry by name match, or every match with `all_matches=True` (for syncs that
  split one workout into several same-named rows).
- `fitness_log_water`: add water to MyFitnessPal's water tracker in cups,
  fluid ounces, millilitres, or litres (common spellings accepted), or set the
  day's total with `replace=True`.
- `fitness_draft_food`: numbered food options with every serving size and
  whole-entry macros, ranked deterministically and filtered by optional
  min/max calorie, protein, carb, and fat targets; stored as a 24-hour draft.
- `fitness_log_food` accepts `draft_id` + `option` (+ `serving`) and
  remembers the choice per query; `fitness_list_food_pins` and
  `fitness_clear_food_pin` manage remembered choices.
- Contributor documentation: `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`,
  `SECURITY.md`, issue and pull request templates, `CODEOWNERS`.
- Ruff lint and format checks in CI, with a `pre-commit` config.
- Dependabot for GitHub Actions and Python dependencies.
- README wordmark, a link to the launch video, and a GitHub stars badge.
  The wordmark lives in `assets/`, which is left out of the sdist.

### Changed

- `fitness_log_food(query=...)` no longer logs the top search result. It
  logs a remembered food, or a single exact-name match; otherwise it logs
  nothing and returns a draft to choose from. A single exact match is read
  off the search page, with no per-result detail requests.
- `fitness_modify_food` chooses its replacement the same way (remembered
  food, single exact match, or a draft) before deleting anything; an
  ambiguous replacement changes nothing and is confirmed with `draft_id` +
  `option`. Its result reports `logged` (plus `food_id`, `weight_id`,
  `serving`) in place of `added`.
- `fitness_log_food` with `food_id` + `weight_id` now also returns `serving`.

### Fixed

- A session that lapsed while re-syncing the day after `fitness_log_food`,
  `fitness_delete_food` or `fitness_modify_food` retried the whole call, so
  the food was logged (or removed) twice. Only the re-sync is retried now; if
  it still fails, the result carries `refresh_warning` instead of an error.
- Drafts no longer fail outright when one search result has a malformed
  serving size; that serving is skipped.
- Confirming a pinned food whose serving list failed to load no longer
  replaces the pin with the default serving.
- Foods whose serving sizes can't be loaded are ranked on their search
  listing's calories instead of as a full miss against macro targets.

- Gap-fill now keys off an explicit `diary_synced` flag instead of row
  existence. Previously a weight-only row (from `fitness_log_weight` on a past
  date, or from the weigh-in backfill after a failed day fetch) made sync treat
  that day as cached, so its calories and macros were never fetched. Existing
  databases are migrated on first open; weight-only rows are refetched on the
  next sync.
- `fitness_log_food`'s `meal` argument now resolves against the account's
  actual current meal labels (scraped off the diary page), the same way
  `fitness_delete_food`/`fitness_modify_food` already matched entries. It
  previously only recognized the literal keywords `breakfast`/`lunch`/
  `dinner`/`snacks` via a hardcoded 0-3 index and silently fell back to
  meal_id 0 for anything else — misfiling entries for accounts with renamed
  meals or the up to two extra custom meals MyFitnessPal allows. The four
  keywords still reach the first four meals when those were renamed (but
  never a slot now named after a different default meal); any other
  unresolvable `meal` now raises instead of defaulting to meal 0. Matching
  ignores extra and non-breaking spaces, and an unnamed meal section keeps
  its position. A diary page with no meal sections (a lapsed session) now
  triggers the session refresh instead of an unknown-meal error.

## [0.3.0] - 2026-07-26

### Changed

- Diary entry matching for `fitness_delete_food` and `fitness_modify_food`
  disambiguates instead of guessing when several entries match.
- Releases are tagged and published automatically when the version in
  `pyproject.toml` is bumped on `main`.

### Fixed

- `__version__` no longer drifts from `pyproject.toml`.

## [0.2.1] - 2026-07-25

### Fixed

- Weight backfill no longer strands weigh-ins on days that were already cached.

## [0.2.0] - 2026-07-09

### Added

- `fitness_get_note` and `fitness_log_note` for reading and writing the
  MyFitnessPal daily diary note.
- Demo GIF in the README.

## [0.1.3] - 2026-07-08

### Fixed

- MCP registry namespace case (`io.github.Mason-Levyy`).

## [0.1.2] - 2026-07-08

### Added

- Publishing to the MCP Registry (`server.json`, OIDC login).

## [0.1.1] - 2026-07-08

### Added

- `build_client` accepts username and impersonation overrides so the client
  can be embedded in other projects.

## [0.1.0] - 2026-07-08

### Added

- Initial release: MCP server with diary read/write, food search, weight and
  exercise logging, trends, bulk export, cookie auth with optional headless
  auto-refresh, stdio and streamable HTTP transports. Published to PyPI as
  `mfp-mcp`.

[Unreleased]: https://github.com/Mason-Levyy/myfitnesspal-mcp/compare/v0.4.3...HEAD
[0.4.3]: https://github.com/Mason-Levyy/myfitnesspal-mcp/compare/v0.4.2...v0.4.3
[0.4.2]: https://github.com/Mason-Levyy/myfitnesspal-mcp/compare/v0.4.1...v0.4.2
[0.4.1]: https://github.com/Mason-Levyy/myfitnesspal-mcp/compare/v0.4.0...v0.4.1
[0.4.0]: https://github.com/Mason-Levyy/myfitnesspal-mcp/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/Mason-Levyy/myfitnesspal-mcp/compare/v0.2.1...v0.3.0
[0.2.1]: https://github.com/Mason-Levyy/myfitnesspal-mcp/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/Mason-Levyy/myfitnesspal-mcp/compare/v0.1.3...v0.2.0
[0.1.3]: https://github.com/Mason-Levyy/myfitnesspal-mcp/compare/v0.1.2...v0.1.3
[0.1.2]: https://github.com/Mason-Levyy/myfitnesspal-mcp/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/Mason-Levyy/myfitnesspal-mcp/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/Mason-Levyy/myfitnesspal-mcp/releases/tag/v0.1.0
