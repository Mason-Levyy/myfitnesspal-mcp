# Installing mfp-mcp (for AI coding agents)

These steps install the MyFitnessPal MCP server for Cline, Claude Code, Cursor,
or any other MCP client. The package is `mfp-mcp` on PyPI and runs with `uvx`.

## 1. Check for uv

```bash
uvx --version
```

If that fails, install uv (https://docs.astral.sh/uv/getting-started/installation/):

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

On Windows: `powershell -c "irm https://astral.sh/uv/install.ps1 | iex"`.

## 2. Have the user connect their account

Authentication needs the user's browser session cookie, pasted at a hidden
prompt. Don't ask the user to paste the cookie into chat. Ask them to run this
in their own terminal:

```bash
uvx mfp-mcp auth
```

The prompt explains where to find the cookie: log in at myfitnesspal.com, open
DevTools → Application (Chrome) or Storage (Firefox) → Cookies →
`https://www.myfitnesspal.com`, and copy `__Secure-next-auth.session-token`.
The session is saved to the user config directory, so the server config needs
no secrets.

To confirm it worked (no prompt, safe to run yourself):

```bash
uvx mfp-mcp auth --check
```

## 3. Add the server to the client

Server name: `myfitnesspal`. Command: `uvx`. Args: `["mfp-mcp"]`. Transport:
stdio.

Claude Code:

```bash
claude mcp add myfitnesspal -- uvx mfp-mcp
```

Cline (`cline_mcp_settings.json`), Cursor (`~/.cursor/mcp.json`), Claude Desktop
(`claude_desktop_config.json`), and Windsurf (`~/.codeium/windsurf/mcp_config.json`)
all take the same entry:

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

Restart or reload the client afterwards.

## 4. Verify

Call `fitness_get_day` with no arguments. It returns today's diary. An error
mentioning `myfitnesspal-mcp auth` means step 2 didn't complete: run
`uvx mfp-mcp auth --check` and have the user re-run `uvx mfp-mcp auth`.

## Optional: auto-refresh

Sessions expire after about 30 days. To renew them automatically with a
headless browser, use the `autorefresh` extra everywhere instead:

```bash
uvx --from 'mfp-mcp[autorefresh]' playwright install chromium
uvx --from 'mfp-mcp[autorefresh]' mfp-mcp auth
```

Then set the client's args to `["--from", "mfp-mcp[autorefresh]", "mfp-mcp"]`.

## Optional environment variables

- `MFP_USERNAME`: the user's MFP username (not email). Set it only if `auth`
  reports that it couldn't read the profile.
- `MFP_IMPERSONATE`: set to `chrome124` if requests fail with HTTP 403.
