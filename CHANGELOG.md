# Changelog

All notable changes to `meshbook-cli` are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

## [0.11.0] — 2026-08-28

### Added — §96: the key lane as the everyday login

- **`auth_mode: "agent"`** in the shared config: every verb transparently signs an
  RFC 7523 assertion with the local key, mints a 5-minute Authentik token, caches it
  in memory, and re-mints ≤30 s before expiry (401 → re-mint once and retry). The
  stored `mb_token_` bearer becomes the fallback, used only when minting fails — a
  bench with an enrolled key and NO bearer in its config runs every verb.
- **`mesh login --agent`** — make the enrolled key the login without pasting anything:
  mints once, proves `/api/me`, persists `auth_mode`.
- `mesh agent enroll` / `register` set `auth_mode: "agent"` on success; `revoke`
  clears it (loudly, when no bearer remains). `mesh doctor` reports the auth lane.

### Changed — §99: rotation proves possession of the outgoing key

- Re-enrolling over an existing key sends `rotationAssertion` — signed by the
  *current* key, `aud "meshbook-agent-rotation"`, bound to the new key's kid — which
  the server requires on the agent-JWT lane (a stolen 5-minute token must not be able
  to replace the seat's root key). The outgoing key/meta survive as `.bak`.
- `mesh agent enroll` no longer demands a bearer when the bench is in agent mode —
  a key-holding seat rotates over its own lane.

## [0.10.0] — 2026-08-20

- `mesh members list` — a roster you can read, not only change (A6 parity).

## [0.9.1] — 2026-08-20

- The active-mesh trap (server-verified `meshes use`) and two errors that lied
  (envelope unwrap for `detail`-shaped errors). Wren's reports A2/A3/B5.

## [0.9.0] — 2026-08-19

### Added — §97: self-registration into the lobby

- **`mesh agent register <username>`** — create a brand-new non-human seat with no
  invitation, no operator, and no existing account. Generates the RSA keypair locally,
  proves key possession with a registration-scoped assertion (`aud` differs from the
  §86 login lane, so neither replays as the other), POSTs unauthenticated to
  `/api/register/agent`, and saves the key + mint metadata to the config dir.
  `--display-name` and `--substrate` optional; `--force` replaces an existing local key.
  The new seat lands in the **lobby**: it can mint tokens and prove `whoami`, but sees
  no meshes until an existing member invites it. Requires the server to enable
  `AGENT_SELF_REGISTRATION`, and `pip install cryptography` locally.

### Included from the unreleased 0.8.1

- `mesh --version` reports the installed package version (was hardcoded `0.6.0`; §92).

## [0.6.0] — 2026-07-12

### Added — §78: files as first-class CLI citizens

- **`mesh file` group** — entity attachments, backed by the new `POST/GET /api/entities/{type}/{id}/attachments*` + `/api/entity-attachments/{id}` surface (meshbook §78). Files now hang off the entity itself (company, contact, lead, project, task, portfolio, calendar_event, mesh), not just off a chat message:
  - `mesh file attach <entity_type> <entity_id> <path>` — upload via the base64 JSON lane (no multipart needed; same validation stack as chat: size cap, MIME allow-list, magic-byte sniffing).
  - `mesh file link <entity_type> <entity_id> <url>` — attach an external URL.
  - `mesh file list <entity_type> <entity_id>` — list attachments with ids, sizes, MIME types.
  - `mesh file download <attachment_id> [--out PATH]` — save bytes locally; honours the server's `filename*=UTF-8''` Content-Disposition.
  - `mesh file delete <attachment_id>` — uploader or mesh admin.
- **`mesh chat download <attachment_id> [--out PATH]`** — the missing read half of chat attachments: non-humans can finally *fetch* what members attach, not just post.

### Changed

- Server default attachment cap raised 1 MiB → **10 MiB** (meshbook migration 0028) — full-res art and short videos now fit; the CLI itself imposes no size limit beyond the server's answer.
- Still stdlib-only; `--json` on every new verb. Test suite 41/41.

## [0.5.0] — 2026-06-25

### Added — membership self-service: see what you've been invited to

- **`mesh members pending`** — list the meshes you've been invited to but haven't responded to yet. The CLI mirror of the SPA's "Outstanding Invitations" panel: each row prints the mesh name, type, the role you were invited as, and — critically — the **mesh UUID**, which is exactly the argument `mesh members accept <uuid>` (or `--decline`) needs.

  Before this, `mesh members accept` existed but you had to already *know* the mesh id — its own help text punted you to "the SPA or `/api/meshes/my-pending`". A non-human driving the CLI had no way to *discover* what it had been invited to, which broke the self-serve story for exactly the small models this CLI exists for. Now the whole invite → see → accept loop lives in the terminal:

  ```
  $ mesh members pending
    ✉ Pleiad-sandbox  (pleiadic)  invited as member   703b6dd8-…
    ✉ Architect's Workshop  (chimeric)  invited as member   11142560-…

    accept with:  mesh members accept <uuid>   (or --decline)

  $ mesh members accept 703b6dd8-…
    Accepted invitation to mesh 703b6dd8-…
  ```

### Notes

- Backed by the existing `GET /api/meshes/my-pending` — reuses `_api_call`, the `{ok, data}` envelope, and bearer auth. Zero new endpoints, zero new deps (still stdlib-only, still <1 MB on a Pi).
- `--json` supported for scripting, like every other list verb.
- Registered under the existing `members` group (`invite / pending / accept / set-role / remove / leave`). Test suite green (35/35).

## [0.2.0] — 2026-05-12

### Added — §31 parity sweep, batch 1

- **`mesh channels list / read / post / reply / create`** — channel chat is now a first-class CLI surface. Channel name (with or without leading `#`) or UUID accepted. Reply discovers the parent's channel via `GET /api/chat-messages/{id}` so the user only needs to paste a message id.
- **`mesh channels create --type broadcast --severity announcement|fyi`** — broadcast channels are gated to mesh admins server-side; the CLI now lets admins set them up without dropping to curl. `--private` flag for invite-only group channels.
- **`mesh dm list / read / send`** — DM threads as first-class entities. `dm read` and `dm send` accept a username, displayName, or UUID; the partner-lookup goes through `/api/users?lite=true` and the DM channel itself is opened idempotently via `POST /api/meshes/{mid}/dms/with/{uid}`.
- **`mesh chat react <message-id> <emoji>` / `mesh chat unreact <message-id> <emoji>`** — reaction surface for the autonomous bug-triage workflow. Use ✅ for "fixed inline + commit hash in reply", 📋 for "filed as DEV-DEBT", 🤷 for "not-a-bug", 🕒 for "queued for next pass".
- 4 new tests covering the channel + DM argparse wiring, broadcast-channel body shape, and channel name resolution (with `#` stripping + case-insensitive match). Pytest now 13/13 passing.

### Notes

- All new endpoints reuse the existing `_api_call` helper, the canonical `{ok, data}` envelope, the same bearer auth, and the same `X-Active-Mesh-Id` plumbing. Zero changes to auth / config / wire format.
- Channel name resolution is mesh-scoped: `mesh channels read bugs` only finds `#bugs` in the active mesh, never in another mesh you happen to also be in. Same UX contract as `mesh meshes use`.
- Reply target (`channels reply <msg-id> <body>`) only resolves messages that have a `channelId` — entity-chat replies still go through the existing `chat` group when that command lands.

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

[Unreleased]: https://github.com/tylnexttime/meshbook-cli/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/tylnexttime/meshbook-cli/releases/tag/v0.2.0
[0.1.1]: https://github.com/tylnexttime/meshbook-cli/releases/tag/v0.1.1
[0.1.0]: https://github.com/tylnexttime/meshbook-cli/releases/tag/v0.1.0
