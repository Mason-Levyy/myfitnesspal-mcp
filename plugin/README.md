# MyFitnessPal MCP plugin

This plugin connects Claude to your MyFitnessPal diary through
[mfp-mcp](https://github.com/Mason-Levyy/myfitnesspal-mcp), an open-source MCP
server. Log meals by talking, search the food database with macros, log weight,
water, and exercise, and chart your nutrition trends.

## Install

1. Connect your account once, in your own terminal:

   ```bash
   uvx mfp-mcp auth
   ```

   It asks for your browser's MyFitnessPal session cookie at a hidden prompt.
   The [Authentication](https://github.com/Mason-Levyy/myfitnesspal-mcp#authentication)
   section of the main README shows where to find it.

2. Install the plugin in Claude Code:

   ```
   /plugin install myfitnesspal-mcp --marketplace Mason-Levyy/myfitnesspal-mcp
   ```

The plugin doesn't ask for the cookie itself. The server reads the session that
`auth` saved, so the
[auto-refresh](https://github.com/Mason-Levyy/myfitnesspal-mcp#auto-refresh-recommended)
setup can keep rotating it without the plugin's setting going stale.

## What it needs

- [uv](https://docs.astral.sh/uv/) on your machine. The plugin runs the
  server with `uvx`, which downloads the pinned `mfp-mcp` release from PyPI
  the first time it starts.
- A MyFitnessPal account, connected with `uvx mfp-mcp auth` as above.

## What it runs and sends

The server runs locally over stdio and talks only to myfitnesspal.com. It
reads your diary and, when you ask, logs, changes, or deletes diary entries,
weight, water, exercise, and the daily note. Your session cookie stays in your
local config directory. Day summaries, feel notes, and remembered foods stay in
a local SQLite file. Tool results go to the Claude session you use them in.

MyFitnessPal has no public API, so this server makes the same requests the
MyFitnessPal website makes, and it can break when MyFitnessPal changes its
site. Not affiliated with or endorsed by MyFitnessPal. Licensed under MIT.
