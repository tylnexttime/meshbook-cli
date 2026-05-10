# Changelog

All notable changes to `meshbook-cli` are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

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
