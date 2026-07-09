# myfitnesspal-mcp

Connect MyFitnessPal to Claude or any MCP client. Log meals by talking, search
the food database with macros, track trends, and export your nutrition history —
all against your real MyFitnessPal diary.

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
  passes Cloudflare.
- **Auto-refreshes the session** (optional): a headless browser profile rotates
  the token when it expires, and failed calls retry automatically.
- **Writes, not just reads**: log, modify, and delete real diary entries.
- **Search-then-log**: get candidates with macros, then log the exact item.

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

MyFitnessPal killed headless password login, so this uses your browser's
session cookie:

1. Log in at [myfitnesspal.com](https://www.myfitnesspal.com).
2. Open DevTools (F12) → **Application** (Chrome) or **Storage** (Firefox) →
   **Cookies** → `https://www.myfitnesspal.com`.
3. Copy the value of `__Secure-next-auth.session-token`.
4. Paste it into the `mfp-mcp auth` prompt (input is hidden).

Pasting the entire `Cookie:` header from any request in the Network tab also
works. Cookies are stored with owner-only permissions in your platform config
dir, or supply them via the `MFP_COOKIE` environment variable instead.

Sessions last around 30 days. When one expires, either re-run `auth` — or
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
| `fitness_get_day` | Nutrition totals, diary entries, and feel note for a day |
| `fitness_search_food` | Candidate matches with brand, calories, macros, serving, and ids |
| `fitness_log_food` | Log a food to the real diary (top match, or an exact search candidate) |
| `fitness_delete_food` | Remove a diary entry by name match |
| `fitness_modify_food` | Replace an entry (or change its quantity) |
| `fitness_log_weight` | Log a weight measurement (updates the same day on re-log) |
| `fitness_get_exercise` | Read the exercise diary (cardio + strength) |
| `fitness_log_feel` | Save a subjective "how I feel" note (stored locally, never sent to MFP) |
| `fitness_get_trends` | One metric over a date range: weight, calories_in, protein, carbs, fat |
| `fitness_bulk_export` | Whole date range in one call, for analysis |

The high-accuracy logging flow: `fitness_search_food("greek yogurt")` returns
candidates with macros and a `food_id`/`weight_id`; pass those to
`fitness_log_food` to log exactly that item instead of trusting the top match.

Day summaries and trends read from a local SQLite cache that gap-fills from
MyFitnessPal (first call on a fresh install fetches up to 30 days, one request
per day — subsequent calls are fast).

Water intake is read-only (it appears in day summaries): MyFitnessPal's water
*write* isn't exposed on any endpoint we've found — `/food/water` accepts POSTs
but ignores them. If you capture the real call in your browser, a PR is very
welcome.

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
  supplies the `food_id`/`weight_id` that `/food/add` accepts, and deletes go
  through `/food/remove` with the page CSRF token.
- Day summaries, trends, and exports read a local SQLite cache that gap-fills
  missing days; feel notes are local-only.

## Development

```bash
git clone https://github.com/Mason-Levyy/myfitnesspal-mcp
cd myfitnesspal-mcp
uv sync --extra autorefresh
uv run pytest
```

Tests run against synthetic MyFitnessPal HTML/JSON fixtures — no account
needed.

## License

[MIT](LICENSE)
