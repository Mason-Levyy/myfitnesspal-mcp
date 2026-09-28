# Security Policy

## Scope

This server handles a MyFitnessPal session cookie that grants full access to
the account it belongs to. Issues in the following areas are in scope:

- Cookie/credential storage, permissions, or leakage (logs, error messages,
  MCP tool output, cache files).
- The HTTP transport (`--http`): anything that weakens the documented
  "localhost-only, no built-in auth" posture.
- The headless auto-refresh browser profile.
- Dependency vulnerabilities that are reachable through this package.

Out of scope: MyFitnessPal's own services, and the fact that this project
passes Cloudflare's browser checks with a real TLS fingerprint (that is its
documented purpose).

## Supported versions

Only the latest release on PyPI receives fixes.

## Reporting a vulnerability

**Please do not open a public issue for security problems.**

Use GitHub's private reporting: go to the repository's **Security** tab and
choose **Report a vulnerability**, or open
<https://github.com/Mason-Levyy/myfitnesspal-mcp/security/advisories/new>.

Include what you found, how to reproduce it, and the version affected. You can
expect an acknowledgement within 7 days. Once a fix is released the advisory
will be published with credit to the reporter unless you ask otherwise.

## Handling your own credentials

- Never paste a live session cookie into an issue, PR, or test fixture.
- Cookies are stored with owner-only permissions in the platform config
  directory (or read from `MFP_COOKIE`). If you think a cookie has leaked, log
  out of MyFitnessPal in your browser to invalidate the session, then re-run
  `mfp-mcp auth`.
