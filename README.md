<div align="center">

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/Mason-Levyy/myfitnesspal-mcp/main/assets/wordmark-dark.png">
  <img alt="myfitnesspal-mcp" src="https://raw.githubusercontent.com/Mason-Levyy/myfitnesspal-mcp/main/assets/wordmark-light.png" width="520">
</picture>

**Log MyFitnessPal by talking to your AI.**

[![PyPI](https://img.shields.io/pypi/v/mfp-mcp?color=0a64e6)](https://pypi.org/project/mfp-mcp/)
[![GitHub stars](https://img.shields.io/github/stars/Mason-Levyy/myfitnesspal-mcp?style=flat&color=0a64e6)](https://github.com/Mason-Levyy/myfitnesspal-mcp/stargazers)
[![CI](https://github.com/Mason-Levyy/myfitnesspal-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/Mason-Levyy/myfitnesspal-mcp/actions/workflows/ci.yml)
[![Python](https://img.shields.io/pypi/pyversions/mfp-mcp)](https://pypi.org/project/mfp-mcp/)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](https://github.com/Mason-Levyy/myfitnesspal-mcp/blob/main/LICENSE)

[Quickstart](#quickstart) · [Tools](#tools) · [Authentication](#authentication) · [Changelog](https://github.com/Mason-Levyy/myfitnesspal-mcp/blob/main/CHANGELOG.md)

<img alt="Demo: logging a banana takes 14 taps in the app and 2 with myfitnesspal-mcp" src="https://raw.githubusercontent.com/Mason-Levyy/myfitnesspal-mcp/main/assets/demo.gif" width="720">

<sub>▶ [Watch with sound (MP4)](https://github.com/Mason-Levyy/myfitnesspal-mcp/blob/main/assets/demo.mp4)</sub>

</div>

Connect MyFitnessPal to Claude or any MCP client. Log meals by talking, search
the food database with macros, track trends, and export your nutrition history, all against your real MyFitnessPal diary.

Published on PyPI as [`mfp-mcp`](https://pypi.org/project/mfp-mcp/).

<!-- mcp-name: io.github.Mason-Levyy/mfp-mcp -->

> **Unofficial.** MyFitnessPal has no public API; this reverse-engineers the
> web app's own endpoints. It can break whenever MFP changes their site. Use at
> your own risk, with your own account.

## Why this one?

MyFitnessPal moved behind Cloudflare + NextAuth, which broke the
username/password login that most existing integrations rely on. This server:

- **Authenticates with your browser session cookie** over a real Chrome TLS
  fingerprint ([curl_cffi](https://github.com/lexiforest/curl_cffi)), which
  passes Cloudflare
- **Auto-refreshes the session** (optional): a headless browser profile rotates
  the token when it expires, and failed calls retry automatically
- **Writes, not just reads**: log, modify, and delete real diary entries
- **Search-then-log**: get candidates with macros, then log the exact item

## Quickstart

1. Connect your account (one-time; prompts you to paste a cookie — see
   [Authentication](#authentication)):

   ```bash
   uvx mfp-mcp auth
   ```

2. Add the server to your client.

   **Claude Code**

   ```bash
   claude mcp add myfitnesspal -- uvx mfp-mcp
   ```

   **Claude Desktop** (`claude_desktop_config.json`)

   ```json
   {
     "mcpServers": {
       "myfitnesspal": {
         "command": "uvx",
         "args": ["mfp-mcp"]
       }
     }
   }
   ```

3. Talk to it: *"log a banana as a snack"*, *"what did I eat yesterday?"*,
   *"chart my weight this month"*.

Requires [uv](https://docs.astral.sh/uv/). Any MCP client that speaks stdio or
streamable HTTP works, not just Claude.

## Authentication

This server signs in with your browser's MyFitnessPal session cookie:

1. Log in at [myfitnesspal.com](https://www.myfitnesspal.com)
2. Open DevTools (F12) → **Application** (Chrome) or **Storage** (Firefox) →
   **Cookies** → `https://www.myfitnesspal.com`
3. Copy the value of `__Secure-next-auth.session-token`
4. Paste it into the `mfp-mcp auth` prompt

Sessions last around 30 days. When one expires, either re-run `auth` or
enable auto-refresh so you never have to.

### Auto-refresh (recommended)

With the `autorefresh` extra, `auth` also seeds a persistent headless browser
profile. When MyFitnessPal rejects the session mid-call, the server tells your
client it is retrying, boots the profile headlessly, lets MyFitnessPal rotate
the session token, saves the fresh cookie, and retries the call.

```bash
uvx --from 'mfp-mcp[autorefresh]' playwright install chromium
uvx --from 'mfp-mcp[autorefresh]' mfp-mcp auth
```

Then use the same `--from 'mfp-mcp[autorefresh]'` form in your client config
(e.g. `uvx --from 'mfp-mcp[autorefresh]' mfp-mcp`).

## Tools

| Tool | What it does |
| --- | --- |
| `fitness_get_day` | Nutrition totals, diary entries, the MFP daily note, and feel note for a day |
| `fitness_search_food` | Candidate matches with brand, calories, macros, serving, and ids |
| `fitness_draft_food` | Numbered options with every serving size, filtered/ranked by optional calorie and macro targets |
| `fitness_log_food` | Log a draft option (and serving), a remembered food, or exact ids to the real diary |
| `fitness_list_food_pins` | Remembered query → food/serving choices (local) |
| `fitness_clear_food_pin` | Forget one remembered choice, or all of them |
| `fitness_delete_food` | Remove a diary entry by name match |
| `fitness_modify_food` | Replace an entry (or change its quantity), choosing the replacement like `fitness_log_food` |
| `fitness_log_weight` | Log a weight measurement (updates the same day on re-log) |
| `fitness_log_water` | Add water (cups, fl oz, mL, L) or set the day's total with `replace` |
| `fitness_get_exercise` | Read the exercise diary (cardio + strength) |
| `fitness_get_exercise_entries` | List cardio and strength entries with each section's columns |
| `fitness_delete_exercise` | Remove an exercise entry by name match (`all_matches` for multi-row sync cleanup) |
| `fitness_get_note` | Read the MyFitnessPal daily diary note (the "Notes" box) for a day |
| `fitness_log_note` | Write that daily note to MFP (replace, or `append` a new line) |
| `fitness_log_feel` | Save a subjective "how I feel" note (stored locally, never sent to MFP) |
| `fitness_get_trends` | One metric over a date range: weight, calories_in, protein, carbs, fat |
| `fitness_bulk_export` | Whole date range in one call, for analysis |

### Logging flow

Food logging is draft-then-confirm, so what lands in your diary never
depends on MyFitnessPal's search order:

1. `fitness_draft_food("greek yogurt", min_protein=15, max_calories=150)`
   returns numbered options. Each lists every serving size with calories and
   macros for the whole entry (serving × quantity) plus a suggested serving;
   options that meet the targets come first, near misses follow. Call it
   again with different targets to refine.
2. `fitness_log_food(draft_id=..., option=2, serving=1)` logs exactly that
   food and serving, and remembers the choice for that query.
3. Next time, `fitness_log_food(query="greek yogurt")` logs the remembered
   food and serving without searching.

A bare `fitness_log_food(query=...)` with nothing remembered logs only when
exactly one result matches the name exactly; otherwise it logs nothing and
returns a draft to choose from. Remembered choices live in the local cache
(`fitness_list_food_pins`, `fitness_clear_food_pin`). Drafts expire after 24
hours.

`fitness_modify_food` picks the replacement the same way, before deleting
anything: a remembered food or a single exact-name match is used directly;
otherwise the entry is left alone and a draft comes back — call
`fitness_modify_food` again with the same `query` plus `draft_id` and
`option`.

`fitness_log_food`, `fitness_delete_food`, and `fitness_modify_food` all take
a `meal` argument the same way: `breakfast`/`lunch`/`dinner`/`snacks`, or the
literal name of any meal section currently on your diary — including a
renamed default meal or one of the up to two extra meals MyFitnessPal lets
you add in Diary Settings. Matching is case-insensitive against your
account's current meal labels. If you renamed a default meal, the keywords
still work: `breakfast`/`lunch`/`dinner`/`snacks` fall back to your first
through fourth meal, unless that slot now carries a different default name
(e.g. `lunch` won't land in a second meal you renamed to "Dinner"). Any other unrecognized `meal` raises an error instead
of silently logging into the wrong section.

Day summaries and trends read from a local SQLite cache that gap-fills from
MyFitnessPal (first call on a fresh install fetches up to 30 days, one request
per day).

Water logging reads the day's current total from `/food/water`, adds the requested
quantity, and posts the new total back to the same endpoint. Pass
`replace=True` to set the day's total outright (e.g. to fix a mislogged amount);
every call returns `previous_ml`, so a mistake can be reverted by passing it
back as `amount` with `unit="ml"` and `replace=True`. The separate
`/stats` request with a `water_logged` event is analytics telemetry; it does not
persist the water total.

## Remote / HTTP mode

The default transport is stdio. For network clients:

```bash
mfp-mcp --http --host 127.0.0.1 --port 8484
```

This serves streamable HTTP at `/mcp`. **There is no built-in authentication —
never expose it to the internet.** Bind to localhost and front it with
something that authenticates for you: a VPN/tailnet (e.g. `tailscale serve`),
an authenticating reverse proxy, or an OAuth-aware MCP gateway.

## Configuration

| Variable | Purpose | Default |
| --- | --- | --- |
| `MFP_COOKIE` | Session cookie (full header or bare token); overrides the saved file | – |
| `MFP_USERNAME` | Your MFP username (not email); only needed if profile lookup fails | auto-detected |
| `MFP_IMPERSONATE` | curl_cffi browser fingerprint (try `chrome124` on 403s) | `chrome` |
| `MFP_SYNC_DAYS` | Gap-fill lookback window in days | `30` |
| `MFP_MCP_DATA_DIR` | Where the SQLite cache + browser profile live | platform data dir |

## Troubleshooting

- **403 / Cloudflare blocked**: try `MFP_IMPERSONATE=chrome124` (or another
  [curl_cffi target](https://github.com/lexiforest/curl_cffi#supported-browsers)).
  Datacenter IPs get challenged far more than residential ones.
- **"Session expired"**: re-run `mfp-mcp auth`, or set up
  [auto-refresh](#auto-refresh-recommended).
- **"couldn't read your MyFitnessPal profile"**: MFP's profile endpoint 500s
  for some accounts. Set `MFP_USERNAME` to your username (not your email).
- **curl_cffi install issues**: prebuilt wheels cover Linux/macOS/Windows;
  musl (Alpine) builds from source.

## How it works

- [python-myfitnesspal](https://github.com/coddingtonbear/python-myfitnesspal)
  parses the diary, measurements, and exercise pages — run over a `curl_cffi`
  session that impersonates Chrome's TLS fingerprint so Cloudflare lets it
  through with just the NextAuth session cookie.
- Writes replicate the web app's own XHR calls: the legacy food-search page
  supplies the `food_id`/`weight_id` that `/food/add` accepts, deletes go
  through `/food/remove`, water reads/writes via `/food/water`, and the daily
  note reads/writes via `/food/note` — each with the page CSRF token.
- Day summaries, trends, and exports read a local SQLite cache that gap-fills
  missing days. The MyFitnessPal daily note syncs both ways; feel notes are
  local-only.

## Development

```bash
git clone https://github.com/Mason-Levyy/myfitnesspal-mcp
cd myfitnesspal-mcp
uv sync --extra autorefresh
uv run pytest
```

Tests run against synthetic MyFitnessPal HTML/JSON fixtures — no account
needed. Lint and formatting are enforced with `ruff` (`uv run ruff check .`,
`uv run ruff format .`).

## Contributing

Issues and pull requests are welcome, especially endpoint captures when
MyFitnessPal changes something. See [CONTRIBUTING.md](CONTRIBUTING.md) for
setup, style, and the PR checklist, and [SECURITY.md](SECURITY.md) for how to
report vulnerabilities privately. Release history is in
[CHANGELOG.md](CHANGELOG.md).

## License

[MIT](LICENSE)
