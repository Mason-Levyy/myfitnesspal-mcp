# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

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

### Changed

- `fitness_log_food(query=...)` no longer logs the top search result. It
  logs a remembered food, or a single exact-name match; otherwise it logs
  nothing and returns a draft to choose from.

### Fixed

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

[Unreleased]: https://github.com/Mason-Levyy/myfitnesspal-mcp/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/Mason-Levyy/myfitnesspal-mcp/compare/v0.2.1...v0.3.0
[0.2.1]: https://github.com/Mason-Levyy/myfitnesspal-mcp/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/Mason-Levyy/myfitnesspal-mcp/compare/v0.1.3...v0.2.0
[0.1.3]: https://github.com/Mason-Levyy/myfitnesspal-mcp/compare/v0.1.2...v0.1.3
[0.1.2]: https://github.com/Mason-Levyy/myfitnesspal-mcp/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/Mason-Levyy/myfitnesspal-mcp/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/Mason-Levyy/myfitnesspal-mcp/releases/tag/v0.1.0
