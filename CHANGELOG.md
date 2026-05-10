# Changelog

All notable changes to `meshbook-cli` are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

## [0.1.1] — 2026-05-10

### Fixed
- **`mesh login` no longer persists invalid tokens.** Previously the token was written to `~/.meshbook/config` *before* `/api/me` verification, so an invalid `--token` left a dead credential on disk. Now the token is verified against the API in-memory first; only on success does it land on disk. Also detects the "200 + `authenticated:false`" shape `/api/me` returns for invalid bearers (it doesn't 401 — that's a SPA-friendly contract). Bug surfaced by Rook 2026-05-10 during their full CLI E2E walk.
- **`mesh login` on non-TTY (piped stdin / CI / no terminal) no longer hangs.** `getpass.getpass()` blocks forever on Windows when stdin is a pipe with no `/dev/tty` fallback. Now we detect `sys.stdin.isatty()` and fall through to a plain `sys.stdin.readline()` with a "(input will echo — non-TTY mode)" warning. Same bug.
- **Config dir resolution is now overridable.** Honour `MESHBOOK_CONFIG_DIR` env var (Pi users with read-only `$HOME` mounts), then `XDG_CONFIG_HOME` if exported, then the legacy `~/.meshbook` (which stays canonical for upgrade safety — if it already exists, we never silently migrate the user away from it).

### Added
- 4 new tests covering the above (invalid-token-doesn't-persist, XDG honoured when no legacy dir, legacy dir takes precedence when present, explicit `MESHBOOK_CONFIG_DIR` wins). Pytest now 9/9 passing.

## [0.1.0] — 2026-05-10

### Added
- Initial public release.
- `mesh login / logout / whoami / doctor` — auth + sanity check.
- `mesh meshes list / use` — pick the active mesh.
- `mesh contacts list / create` — CRM contact CRUD.
- `mesh chat post / list / attach` — chat thread participation, including the §26d-json JSON-via-base64 attachment path for embedded clients that can't do multipart.
- `mesh notifications` — recent notifications across all your meshes.
- Single-file architecture: `mesh/cli.py` carries the whole program. Stdlib only.
- `pip install meshbook-cli` provisions the `mesh` command via the [project.scripts] entry point.

### Notes
- Targets meshbook **Phase A** auth (bespoke Bearer tokens minted at `/v2/#/account/api-tokens`). Phase B (Authentik OAuth 2.1 + PKCE + device-code) is post-launch — the wire format will be identical so this CLI keeps working.

[Unreleased]: https://github.com/tylnexttime/meshbook-cli/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/tylnexttime/meshbook-cli/releases/tag/v0.1.0
