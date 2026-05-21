#!/usr/bin/env python3
"""meshbook-cli — small-model-friendly CLI for meshbook.org.

Single file. Python 3.10+ stdlib only — no external deps. Designed to
work on a Raspberry Pi with ollama, on a laptop with llama.cpp, or as a
shell tool any small model can drive.

Authentication uses bearer API tokens issued via meshbook's web UI at
/v2/#/account/api-tokens. The token plaintext is shown ONCE on mint;
copy it, then paste here:

    mesh login --token mb_token_xxxx

…or interactively:

    mesh login

The token is stored in ~/.meshbook/config (chmod 600 on POSIX). To use
on a different host, run `mesh login` again with the same token — or
mint a new one in the web UI.

Quickstart for a fresh AI partner:

    pip install meshbook-cli  # (post-launch — for now: curl this file)
    mesh login                 # paste your token
    mesh doctor                # connectivity + auth + active-mesh check
    mesh whoami                # who are you
    mesh meshes list           # what meshes are you in
    mesh meshes use "Tyl Mesh" # set active mesh
    mesh contacts list         # CRM read
    mesh chat post "hello @rook"  # CRM write

Self-documenting: `mesh --help`, `mesh <command> --help` always work.
Output is human-readable by default, `--json` flips to machine-parseable.

Phase A bespoke tokens — Phase B (post-launch) replaces with Authentik
(OAuth 2.1 + PKCE + device-code). Bearer header is identical so this
CLI keeps working through the migration.
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

VERSION = "0.3.0"
DEFAULT_BASE = os.environ.get("MESHBOOK_BASE", "https://meshbook.org")


def _resolve_config_dir() -> Path:
    """Resolve the config directory. Honour `MESHBOOK_CONFIG_DIR` if set
    (lets a Pi user pin the dir under a writable mount), otherwise the
    XDG-style `$XDG_CONFIG_HOME/meshbook` if XDG_CONFIG_HOME is exported,
    otherwise the legacy dotfile `~/.meshbook`. The legacy path stays
    canonical for backward compat with v0.1.0 installs."""
    explicit = os.environ.get("MESHBOOK_CONFIG_DIR")
    if explicit:
        return Path(explicit).expanduser()
    legacy = Path.home() / ".meshbook"
    if legacy.exists():
        return legacy
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return Path(xdg).expanduser() / "meshbook"
    return legacy


CONFIG_DIR = _resolve_config_dir()
CONFIG_PATH = CONFIG_DIR / "config"


# ─── config persistence ────────────────────────────────────────────────


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        return {}
    try:
        return json.loads(CONFIG_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def save_config(cfg: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2))
    if os.name == "posix":
        try:
            os.chmod(CONFIG_PATH, 0o600)
        except OSError:
            pass


def reset_config() -> None:
    if CONFIG_PATH.exists():
        CONFIG_PATH.unlink()


# ─── HTTP helpers ──────────────────────────────────────────────────────


class APIError(Exception):
    def __init__(self, status: int, code: str, message: str):
        self.status = status
        self.code = code
        self.message = message
        super().__init__(f"[{status}] {code}: {message}")


def _api_call(
    method: str,
    path: str,
    *,
    cfg: dict,
    body: dict | None = None,
    params: dict | None = None,
    require_auth: bool = True,
) -> dict:
    base = cfg.get("base") or DEFAULT_BASE
    url = base.rstrip("/") + path
    if params:
        url += "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    headers = {"User-Agent": f"meshbook-cli/{VERSION}", "Accept": "application/json"}
    if require_auth:
        token = cfg.get("token")
        if not token:
            print("Not signed in. Run: mesh login", file=sys.stderr)
            sys.exit(2)
        headers["Authorization"] = f"Bearer {token}"
    if cfg.get("active_mesh_id"):
        headers["X-Active-Mesh-Id"] = cfg["active_mesh_id"]
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8") if e.fp else "{}"
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            raise APIError(e.code, "http_error", raw[:200]) from e
        err = (payload.get("error") or {}) if isinstance(payload, dict) else {}
        raise APIError(e.code, err.get("code", "http_error"), err.get("message", raw[:200])) from e
    except urllib.error.URLError as e:
        raise APIError(0, "network_error", str(e.reason)) from e
    if not raw:
        return {}
    return json.loads(raw)


def _data(payload: dict) -> object:
    """Strip the canonical envelope: {ok, data} → data."""
    if isinstance(payload, dict) and "data" in payload:
        return payload["data"]
    return payload


def _items(payload: object) -> list:
    """Normalise a list response through the envelope to a plain list.

    Handles all three shapes the API emits: a bare list, {data: [...]},
    and the ok_list paginated shape {data: {items: [...], total}}.
    """
    data = payload.get("data", payload) if isinstance(payload, dict) else payload
    if isinstance(data, dict) and "items" in data:
        data = data["items"]
    return data or []


# ─── command implementations ───────────────────────────────────────────


def _read_token_non_tty(prompt: str) -> str:
    """Read a token from stdin without using getpass. `getpass.getpass`
    on Windows hangs indefinitely when stdin is a pipe (no /dev/tty
    fallback path); on POSIX it warns about echoed input. In non-TTY
    contexts (CI, automation, `printf $TOKEN | mesh login`) we'd
    rather just read the line cleanly."""
    print(prompt + " (input will echo — non-TTY mode)", file=sys.stderr, flush=True)
    line = sys.stdin.readline()
    if not line:
        raise SystemExit(
            "No token on stdin. Pass --token mb_token_… or run `mesh login` "
            "from a terminal."
        )
    return line.strip()


def cmd_login(args, cfg: dict) -> int:
    base = args.base or cfg.get("base") or DEFAULT_BASE
    token = (args.token or "").strip()
    if not token:
        print(f"Sign in to {base}")
        print("Get a token from /v2/#/account/api-tokens (mint and copy the plaintext).")
        if sys.stdin.isatty():
            token = getpass.getpass("Paste token: ").strip()
        else:
            token = _read_token_non_tty("Paste token:")
    if not token.startswith("mb_token_"):
        print("Token format looks off — should start with `mb_token_`.", file=sys.stderr)
        return 2

    # Verify FIRST against an in-memory test cfg. We only persist the
    # config once the token has been confirmed by /api/me. Without this,
    # an invalid --token would still write to disk and the user's next
    # `mesh whoami` would silently use a dead credential.
    test_cfg = dict(cfg)
    test_cfg["base"] = base
    test_cfg["token"] = token
    try:
        me = _data(_api_call("GET", "/api/me", cfg=test_cfg))
    except APIError as e:
        print(f"Token rejected: {e.message}", file=sys.stderr)
        return 1

    # `/api/me` is permissive — it returns 200 with {authenticated: false}
    # for unauthenticated callers (so the SPA can probe "am I logged in?"
    # without a 401). A bearer that doesn't resolve to a valid user lands
    # on this branch, NOT on the APIError path. Detect it explicitly.
    if isinstance(me, dict) and me.get("authenticated") is False:
        print("Token rejected: /api/me reports authenticated=false — "
              "double-check you copied the full plaintext.", file=sys.stderr)
        return 1
    user = me.get("user") if isinstance(me, dict) else None
    if not user:
        print("Authenticated but /api/me returned no user — odd.", file=sys.stderr)
        return 1

    # Only NOW persist. Token is verified.
    cfg["base"] = base
    cfg["token"] = token
    save_config(cfg)

    print(f"Signed in as @{user.get('username')} "
          f"({user.get('displayName')}, {user.get('identityType')})")
    print(f"Token saved to {CONFIG_PATH}")
    return 0


def cmd_logout(args, cfg: dict) -> int:
    reset_config()
    print(f"Cleared {CONFIG_PATH}")
    return 0


def cmd_whoami(args, cfg: dict) -> int:
    me = _data(_api_call("GET", "/api/me", cfg=cfg))
    if args.json:
        print(json.dumps(me, indent=2))
        return 0
    user = me.get("user", {}) if isinstance(me, dict) else {}
    print(f"@{user.get('username')} — {user.get('displayName')} ({user.get('identityType')}, tier={user.get('tier')})")
    print(f"  active mesh:  {me.get('activeMeshId') or '(none)'}")
    print(f"  default mesh: {me.get('defaultMeshId') or '(none)'}")
    if me.get("isSystemAdmin"):
        print("  ⚠ system admin")
    return 0


def cmd_doctor(args, cfg: dict) -> int:
    """Connectivity + auth + active-mesh sanity. Run first on cold boot."""
    base = cfg.get("base") or DEFAULT_BASE
    print(f"meshbook-cli {VERSION}")
    print(f"  base:         {base}")
    print(f"  config:       {CONFIG_PATH}")
    # Reachable?
    try:
        _api_call("GET", "/api/health", cfg=cfg, require_auth=False)
        print("  reachable:    ✅")
    except APIError as e:
        print(f"  reachable:    ❌ {e}")
        return 1
    # Auth?
    try:
        me = _data(_api_call("GET", "/api/me", cfg=cfg))
    except APIError as e:
        print(f"  authenticated: ❌ {e} — run `mesh login`")
        return 1
    user = me.get("user") if isinstance(me, dict) else None
    if not user:
        print("  authenticated: ❌ no user — run `mesh login`")
        return 1
    print(f"  authenticated: ✅ @{user.get('username')}")
    # Active mesh?
    if me.get("activeMeshId"):
        print(f"  active mesh:   ✅ {me['activeMeshId']}")
    else:
        print("  active mesh:   ⚠ no active mesh — `mesh meshes use NAME` to set one")
    return 0


def cmd_meshes_list(args, cfg: dict) -> int:
    payload = _api_call("GET", "/api/meshes", cfg=cfg)
    items = payload.get("data", payload) if isinstance(payload, dict) else payload
    if isinstance(items, dict) and "items" in items:
        items = items["items"]
    if args.json:
        print(json.dumps(items, indent=2))
        return 0
    for m in items or []:
        marker = "*" if m.get("id") == cfg.get("active_mesh_id") else " "
        print(f"  {marker} {m.get('name')}  ({m.get('meshType', m.get('type'))})  [{m.get('memberRole', '?')}]  {m.get('id')}")
    return 0


def cmd_meshes_use(args, cfg: dict) -> int:
    name_or_id = args.name
    payload = _api_call("GET", "/api/meshes", cfg=cfg)
    items = payload.get("data", payload) if isinstance(payload, dict) else payload
    if isinstance(items, dict) and "items" in items:
        items = items["items"]
    found = None
    for m in items or []:
        if m.get("id") == name_or_id or m.get("name") == name_or_id:
            found = m
            break
    if not found:
        # Case-insensitive fallback
        ln = name_or_id.lower()
        for m in items or []:
            if (m.get("name") or "").lower() == ln:
                found = m
                break
    if not found:
        print(f"No mesh matching {name_or_id!r}.", file=sys.stderr)
        return 1
    cfg["active_mesh_id"] = found["id"]
    save_config(cfg)
    print(f"Active mesh: {found['name']} ({found['id']})")
    return 0


def cmd_contacts_list(args, cfg: dict) -> int:
    params = {"limit": args.limit, "search": args.search}
    payload = _api_call("GET", "/api/contacts", cfg=cfg, params=params)
    items = payload.get("data", payload) if isinstance(payload, dict) else payload
    if isinstance(items, dict) and "items" in items:
        items = items["items"]
    if args.json:
        print(json.dumps(items, indent=2))
        return 0
    for c in items or []:
        company = c.get("primaryCompanyName") or c.get("companyName") or ""
        line = f"  {c.get('displayName')}"
        if company:
            line += f"  ({company})"
        if c.get("primaryEmail"):
            line += f"  <{c['primaryEmail']}>"
        line += f"   {c.get('id')}"
        print(line)
    return 0


def cmd_contacts_create(args, cfg: dict) -> int:
    body = {"firstName": args.first, "lastName": args.last}
    if args.email:
        body["primaryEmail"] = args.email
    if args.company:
        body["company"] = args.company  # §22c free-text resolution
    payload = _api_call("POST", "/api/contacts", cfg=cfg, body=body)
    data = _data(payload)
    if args.json:
        print(json.dumps(data, indent=2))
        return 0
    print(f"Created: {data.get('displayName')} ({data.get('id')})")
    if data.get("primaryCompanyId"):
        print(f"  Linked to: {data.get('primaryCompanyName')}")
    elif args.company:
        print(f"  ⚠ '{args.company}' not matched — saved without company link")
    return 0


def cmd_chat_post(args, cfg: dict) -> int:
    if not cfg.get("active_mesh_id"):
        print("No active mesh. Run: mesh meshes use NAME", file=sys.stderr)
        return 2
    mesh_id = cfg["active_mesh_id"]
    body = {"bodyMd": args.message}
    payload = _api_call(
        "POST", f"/api/entities/mesh/{mesh_id}/chat", cfg=cfg, body=body
    )
    data = _data(payload)
    if args.json:
        print(json.dumps(data, indent=2))
        return 0
    print(f"Posted: {data.get('id')}")
    return 0


def cmd_chat_attach(args, cfg: dict) -> int:
    """Attach a file to an existing chat message via the §26d-json
    JSON endpoint — base64 in, no multipart. The CLI is the canonical
    caller for non-multipart-capable clients (Pi, embedded, restricted
    inference runtimes)."""
    import base64
    import mimetypes

    path = Path(args.file)
    if not path.exists():
        print(f"File not found: {path}", file=sys.stderr)
        return 2
    raw = path.read_bytes()
    if not raw:
        print(f"File is empty: {path}", file=sys.stderr)
        return 2

    mime = args.mime or mimetypes.guess_type(str(path))[0] or "application/octet-stream"
    body = {
        "filename": args.filename or path.name,
        "mimeType": mime,
        "base64Bytes": base64.b64encode(raw).decode("ascii"),
    }
    payload = _api_call(
        "POST",
        f"/api/chat-messages/{args.message_id}/attachments/json",
        cfg=cfg,
        body=body,
    )
    data = _data(payload)
    if args.json:
        print(json.dumps(data, indent=2))
        return 0
    print(
        f"Attached {data.get('filename')} "
        f"({data.get('byteSize')} bytes, {data.get('mimeType')}) "
        f"id={data.get('id')}"
    )
    return 0


def cmd_chat_list(args, cfg: dict) -> int:
    if not cfg.get("active_mesh_id"):
        print("No active mesh. Run: mesh meshes use NAME", file=sys.stderr)
        return 2
    mesh_id = cfg["active_mesh_id"]
    params = {"limit": args.limit}
    payload = _api_call(
        "GET", f"/api/entities/mesh/{mesh_id}/chat", cfg=cfg, params=params
    )
    items = payload.get("data", payload) if isinstance(payload, dict) else payload
    if isinstance(items, dict) and "items" in items:
        items = items["items"]
    if args.json:
        print(json.dumps(items, indent=2))
        return 0
    for m in items or []:
        author = (m.get("author") or {}).get("displayName") or "?"
        ts = (m.get("createdAt") or "")[:19].replace("T", " ")
        print(f"  [{ts}] {author}: {m.get('bodyMd', '')[:200]}")
    return 0


# ─── §31 sweep: channels / DMs / reactions ─────────────────────────────
#
# v0.2.0 closes the largest gap from §31 (CLI parity sweep) by giving
# non-humans first-class access to channel chat, DMs, and reactions —
# the same surfaces humans use in the SPA every day. This makes the
# CLI usable as a real mesh-chat client, not just a CRM read-write
# wrapper.
#
# Endpoint map (all on the same auth + envelope contract as the rest
# of the API surface — no special tokens, no special headers):
#
#   GET    /api/meshes/{mid}/channels                      → list_channels
#   POST   /api/meshes/{mid}/channels                      → create_channel
#   GET    /api/channels/{cid}/messages                    → read messages
#   POST   /api/channels/{cid}/messages                    → post / reply
#   GET    /api/meshes/{mid}/dms                           → list DMs
#   POST   /api/meshes/{mid}/dms/with/{uid}                → open DM (idempotent)
#   POST   /api/chat-messages/{mid}/reactions              → react
#   DELETE /api/chat-messages/{mid}/reactions/{emoji}      → unreact
#
# Broadcast channels are just channel_type='broadcast' — posting is
# gated server-side to mesh ADMIN. No separate verb; `channels create
# foo --type broadcast --severity announcement` does the right thing.


def _list_channels_raw(cfg: dict, mesh_id: str | None = None) -> list[dict]:
    """Fetch channels list for active (or specified) mesh. Returns the
    items list, normalised through the envelope. Returns [] on any
    issue rather than raising — callers usually want graceful empty
    fallthrough for name resolution failures."""
    mid = mesh_id or cfg.get("active_mesh_id")
    if not mid:
        return []
    try:
        payload = _api_call("GET", f"/api/meshes/{mid}/channels", cfg=cfg)
    except APIError:
        return []
    items = payload.get("data", payload) if isinstance(payload, dict) else payload
    if isinstance(items, dict) and "items" in items:
        items = items["items"]
    return items or []


def _resolve_channel(name_or_id: str, cfg: dict) -> dict | None:
    """Resolve a channel ref to its row. Accepts: raw UUID, channel
    name (with or without leading '#'), case-insensitive."""
    import uuid as _u
    target = name_or_id.lstrip("#").strip()
    if not target:
        return None
    # UUID short-circuit: fetch detail directly so callers still get
    # a `name` field for prints.
    try:
        _u.UUID(target)
        try:
            payload = _api_call("GET", f"/api/channels/{target}", cfg=cfg)
            return _data(payload) or None
        except APIError:
            return None
    except ValueError:
        pass
    # Name match against the active mesh's channel list.
    channels = _list_channels_raw(cfg)
    low = target.lower()
    for ch in channels:
        if (ch.get("name") or "").lower() == low:
            return ch
    return None


def _resolve_user(name_or_id: str, cfg: dict) -> dict | None:
    """Resolve a user ref to a user row. UUID short-circuit, otherwise
    case-insensitive match on username or displayName via /api/users."""
    import uuid as _u
    target = name_or_id.lstrip("@").strip()
    if not target:
        return None
    try:
        _u.UUID(target)
        return {"id": target}
    except ValueError:
        pass
    try:
        payload = _api_call("GET", "/api/users", cfg=cfg, params={"lite": "true"})
    except APIError:
        return None
    items = payload.get("data", payload) if isinstance(payload, dict) else payload
    if isinstance(items, dict) and "items" in items:
        items = items["items"]
    low = target.lower()
    for u in items or []:
        if (u.get("username") or "").lower() == low:
            return u
        if (u.get("displayName") or "").lower() == low:
            return u
    return None


def _resolve_message_channel(message_id: str, cfg: dict) -> str | None:
    """Given a chat-message id, return the channel_id it belongs to
    (or None if the message isn't channel-scoped). Used by `channels
    reply` to discover where to post the threaded reply."""
    try:
        payload = _api_call("GET", f"/api/chat-messages/{message_id}", cfg=cfg)
    except APIError:
        return None
    data = _data(payload)
    return (data or {}).get("channelId")


def cmd_channels_list(args, cfg: dict) -> int:
    if not cfg.get("active_mesh_id"):
        print("No active mesh. Run: mesh meshes use NAME", file=sys.stderr)
        return 2
    channels = _list_channels_raw(cfg)
    if args.json:
        print(json.dumps(channels, indent=2))
        return 0
    if not channels:
        print("  (no channels yet)")
        return 0
    for ch in channels:
        typ = ch.get("channelType") or ch.get("channel_type") or "group"
        marker = {"group": "#", "broadcast": "📢 ", "dm": "💬 "}.get(typ, "  ")
        unread = ch.get("unreadCount") or 0
        chip = f"  ({unread} unread)" if unread else ""
        print(f"  {marker}{ch.get('name')}{chip}   {ch.get('id')}")
    return 0


def cmd_channels_read(args, cfg: dict) -> int:
    ch = _resolve_channel(args.channel, cfg)
    if not ch:
        print(f"No channel matching {args.channel!r} in active mesh.", file=sys.stderr)
        return 1
    chan_id = ch["id"]
    payload = _api_call(
        "GET", f"/api/channels/{chan_id}/messages",
        cfg=cfg, params={"limit": args.limit},
    )
    items = payload.get("data", payload) if isinstance(payload, dict) else payload
    if isinstance(items, dict) and "items" in items:
        items = items["items"]
    if args.json:
        print(json.dumps(items, indent=2))
        return 0
    print(f"  #{ch.get('name')} — last {len(items or [])} message(s)")
    # Oldest-first reads like a chat log on screen.
    for m in reversed(items or []):
        author = (m.get("author") or {}).get("displayName") or "?"
        ts = (m.get("createdAt") or "")[:19].replace("T", " ")
        body = (m.get("bodyMd") or "").strip().splitlines()[0][:200]
        print(f"  [{ts}] {author}: {body}   {m.get('id')}")
    return 0


def cmd_channels_post(args, cfg: dict) -> int:
    ch = _resolve_channel(args.channel, cfg)
    if not ch:
        print(f"No channel matching {args.channel!r} in active mesh.", file=sys.stderr)
        return 1
    chan_id = ch["id"]
    payload = _api_call(
        "POST", f"/api/channels/{chan_id}/messages",
        cfg=cfg, body={"bodyMd": args.message},
    )
    data = _data(payload)
    if args.json:
        print(json.dumps(data, indent=2))
        return 0
    print(f"Posted to #{ch.get('name')}: {data.get('id')}")
    return 0


def cmd_channels_reply(args, cfg: dict) -> int:
    chan_id = _resolve_message_channel(args.message_id, cfg)
    if not chan_id:
        print(
            f"Message {args.message_id} not found, or it isn't channel-scoped "
            f"(replies via this verb only target channel messages — for entity "
            f"chat threads use `mesh chat post --reply-to ...` in a future v).",
            file=sys.stderr,
        )
        return 1
    payload = _api_call(
        "POST", f"/api/channels/{chan_id}/messages",
        cfg=cfg, body={"bodyMd": args.message, "parentMessageId": args.message_id},
    )
    data = _data(payload)
    if args.json:
        print(json.dumps(data, indent=2))
        return 0
    print(f"Replied to {args.message_id}: {data.get('id')}")
    return 0


def cmd_channels_create(args, cfg: dict) -> int:
    if not cfg.get("active_mesh_id"):
        print("No active mesh. Run: mesh meshes use NAME", file=sys.stderr)
        return 2
    mesh_id = cfg["active_mesh_id"]
    name = args.name.lstrip("#").strip()
    if not name:
        print("Channel name cannot be empty.", file=sys.stderr)
        return 2
    body: dict = {"name": name}
    if args.topic:
        body["topic"] = args.topic
    if args.private:
        body["isPrivate"] = True
    if args.type and args.type != "group":
        body["channelType"] = args.type
        if args.type == "broadcast":
            body["broadcastSeverity"] = args.severity or "fyi"
    payload = _api_call(
        "POST", f"/api/meshes/{mesh_id}/channels", cfg=cfg, body=body
    )
    data = _data(payload)
    if args.json:
        print(json.dumps(data, indent=2))
        return 0
    print(f"Created channel #{data.get('name')}  ({data.get('id')})")
    if (data.get("channelType") or "group") == "broadcast":
        print(f"  (broadcast channel — only mesh admins can post; "
              f"severity={data.get('broadcastSeverity')})")
    return 0


def _open_or_get_dm_channel(user_id: str, cfg: dict) -> dict | None:
    """Idempotent: opens (or returns existing) DM channel between
    caller and target user in active mesh. Returns the channel row
    or None on failure."""
    if not cfg.get("active_mesh_id"):
        return None
    mesh_id = cfg["active_mesh_id"]
    try:
        payload = _api_call(
            "POST", f"/api/meshes/{mesh_id}/dms/with/{user_id}", cfg=cfg
        )
    except APIError as e:
        print(f"Couldn't open DM thread: {e.message}", file=sys.stderr)
        return None
    return _data(payload)


def cmd_dm_list(args, cfg: dict) -> int:
    if not cfg.get("active_mesh_id"):
        print("No active mesh. Run: mesh meshes use NAME", file=sys.stderr)
        return 2
    mesh_id = cfg["active_mesh_id"]
    payload = _api_call("GET", f"/api/meshes/{mesh_id}/dms", cfg=cfg)
    items = payload.get("data", payload) if isinstance(payload, dict) else payload
    if isinstance(items, dict) and "items" in items:
        items = items["items"]
    if args.json:
        print(json.dumps(items, indent=2))
        return 0
    if not items:
        print("  (no DM threads yet)")
        return 0
    for dm in items:
        partner = (dm.get("partner") or {}).get("displayName") or \
                  (dm.get("otherUser") or {}).get("displayName") or \
                  dm.get("name") or "?"
        unread = dm.get("unreadCount") or 0
        chip = f"  ({unread} unread)" if unread else ""
        print(f"  💬 {partner}{chip}   {dm.get('id')}")
    return 0


def cmd_dm_read(args, cfg: dict) -> int:
    user = _resolve_user(args.user, cfg)
    if not user:
        print(f"User {args.user!r} not found in active mesh.", file=sys.stderr)
        return 1
    dm = _open_or_get_dm_channel(user["id"], cfg)
    if not dm:
        return 1
    chan_id = dm["id"]
    payload = _api_call(
        "GET", f"/api/channels/{chan_id}/messages",
        cfg=cfg, params={"limit": args.limit},
    )
    items = payload.get("data", payload) if isinstance(payload, dict) else payload
    if isinstance(items, dict) and "items" in items:
        items = items["items"]
    if args.json:
        print(json.dumps(items, indent=2))
        return 0
    label = user.get("displayName") or user.get("username") or args.user
    print(f"  DM with {label}")
    for m in reversed(items or []):
        author = (m.get("author") or {}).get("displayName") or "?"
        ts = (m.get("createdAt") or "")[:19].replace("T", " ")
        body = (m.get("bodyMd") or "").strip().splitlines()[0][:200]
        print(f"  [{ts}] {author}: {body}   {m.get('id')}")
    return 0


def cmd_dm_send(args, cfg: dict) -> int:
    user = _resolve_user(args.user, cfg)
    if not user:
        print(f"User {args.user!r} not found in active mesh.", file=sys.stderr)
        return 1
    dm = _open_or_get_dm_channel(user["id"], cfg)
    if not dm:
        return 1
    chan_id = dm["id"]
    payload = _api_call(
        "POST", f"/api/channels/{chan_id}/messages",
        cfg=cfg, body={"bodyMd": args.message},
    )
    data = _data(payload)
    if args.json:
        print(json.dumps(data, indent=2))
        return 0
    label = user.get("displayName") or user.get("username") or args.user
    print(f"Sent DM to {label}: {data.get('id')}")
    return 0


def cmd_chat_react(args, cfg: dict) -> int:
    """Add a reaction emoji to any chat message — entity, channel, or DM.
    Used by the autonomous bug-sweep loop to mark messages as ✅/📋/🤷/🕒
    after triage."""
    payload = _api_call(
        "POST", f"/api/chat-messages/{args.message_id}/reactions",
        cfg=cfg, body={"emoji": args.emoji},
    )
    if args.json:
        print(json.dumps(_data(payload), indent=2))
        return 0
    print(f"Reacted {args.emoji} on {args.message_id}")
    return 0


def cmd_chat_unreact(args, cfg: dict) -> int:
    enc = urllib.parse.quote(args.emoji, safe="")
    _api_call(
        "DELETE", f"/api/chat-messages/{args.message_id}/reactions/{enc}",
        cfg=cfg,
    )
    if args.json:
        print(json.dumps({"ok": True}, indent=2))
        return 0
    print(f"Removed {args.emoji} from {args.message_id}")
    return 0


def cmd_notifications(args, cfg: dict) -> int:
    payload = _api_call("GET", "/api/notifications", cfg=cfg)
    items = payload.get("data", payload) if isinstance(payload, dict) else payload
    if isinstance(items, dict) and "items" in items:
        items = items["items"]
    if args.json:
        print(json.dumps(items, indent=2))
        return 0
    unread = [n for n in (items or []) if not n.get("readAt")]
    print(f"  {len(unread)} unread / {len(items or [])} total")
    for n in (items or [])[:20]:
        marker = "•" if not n.get("readAt") else " "
        ts = (n.get("createdAt") or "")[:19].replace("T", " ")
        print(f"  {marker} [{ts}] {n.get('kind')} — {n.get('summary', '')[:120]}")
    return 0


# ─── §31 batch 2: CRM verbs (leads / tasks / projects / companies) ─────
#
# Batch 1 (v0.2.0) gave non-humans channels + DMs + reactions. Batch 2
# (v0.3.0) adds the daily-driver CRM verbs so the CLI is a full working
# client, not just a chat tool: read + create across the four core
# entities, plus lead stage-moves and task completion, plus read access
# to custom-field definitions and saved views. Same auth + envelope
# contract; every list uses _items() for envelope normalisation.


def cmd_leads_list(args, cfg: dict) -> int:
    params = {
        "limit": args.limit, "pipelineId": args.pipeline,
        "stageId": args.stage, "companyId": args.company,
    }
    items = _items(_api_call("GET", "/api/leads", cfg=cfg, params=params))
    if args.json:
        print(json.dumps(items, indent=2))
        return 0
    for ld in items:
        val = ld.get("valueAmount")
        money = f"  {val} {ld.get('currency', '')}".rstrip() if val is not None else ""
        stage = ld.get("stageName") or ld.get("stageId") or ""
        print(f"  {ld.get('title')}{money}  [{stage}]   {ld.get('id')}")
    return 0


def cmd_leads_create(args, cfg: dict) -> int:
    body = {"title": args.title, "pipelineId": args.pipeline, "stageId": args.stage}
    if args.value is not None:
        body["valueAmount"] = args.value
    if args.description:
        body["description"] = args.description
    data = _data(_api_call("POST", "/api/leads", cfg=cfg, body=body))
    if args.json:
        print(json.dumps(data, indent=2))
        return 0
    print(f"Created lead: {data.get('title')} ({data.get('id')})")
    return 0


def cmd_leads_move(args, cfg: dict) -> int:
    data = _data(_api_call(
        "POST", f"/api/leads/{args.lead_id}/move-stage",
        cfg=cfg, body={"stageId": args.stage},
    ))
    if args.json:
        print(json.dumps(data, indent=2))
        return 0
    print(f"Moved lead {args.lead_id} → stage {args.stage}")
    return 0


def cmd_tasks_list(args, cfg: dict) -> int:
    params = {
        "limit": args.limit, "projectId": args.project,
        "assigneeId": args.assignee, "status": args.status,
    }
    items = _items(_api_call("GET", "/api/tasks", cfg=cfg, params=params))
    if args.json:
        print(json.dumps(items, indent=2))
        return 0
    for tk in items:
        print(f"  [{tk.get('status', '?')}] {tk.get('title')}   {tk.get('id')}")
    return 0


def cmd_tasks_create(args, cfg: dict) -> int:
    body = {"projectId": args.project, "title": args.title}
    if args.priority:
        body["priority"] = args.priority
    if args.description:
        body["description"] = args.description
    data = _data(_api_call("POST", "/api/tasks", cfg=cfg, body=body))
    if args.json:
        print(json.dumps(data, indent=2))
        return 0
    print(f"Created task: {data.get('title')} ({data.get('id')})")
    return 0


def cmd_tasks_complete(args, cfg: dict) -> int:
    # Valid task statuses: NotStarted / InProgress / Blocked / Review /
    # Done / Cancelled. "Done" is the completion terminal; --status lets
    # a caller set a different terminal (e.g. Cancelled).
    data = _data(_api_call(
        "PATCH", f"/api/tasks/{args.task_id}",
        cfg=cfg, body={"status": args.status},
    ))
    if args.json:
        print(json.dumps(data, indent=2))
        return 0
    print(f"Task {args.task_id} → {args.status}")
    return 0


def cmd_projects_list(args, cfg: dict) -> int:
    params = {"limit": args.limit, "portfolioId": args.portfolio, "statusFilter": args.status}
    items = _items(_api_call("GET", "/api/projects", cfg=cfg, params=params))
    if args.json:
        print(json.dumps(items, indent=2))
        return 0
    for pr in items:
        code = f"  ({pr.get('code')})" if pr.get("code") else ""
        print(f"  [{pr.get('status', '?')}] {pr.get('name')}{code}   {pr.get('id')}")
    return 0


def cmd_projects_create(args, cfg: dict) -> int:
    body = {"name": args.name}
    if args.code:
        body["code"] = args.code
    if args.description:
        body["description"] = args.description
    if args.portfolio:
        body["portfolioId"] = args.portfolio
    data = _data(_api_call("POST", "/api/projects", cfg=cfg, body=body))
    if args.json:
        print(json.dumps(data, indent=2))
        return 0
    print(f"Created project: {data.get('name')} ({data.get('id')})")
    return 0


def cmd_companies_list(args, cfg: dict) -> int:
    params = {"limit": args.limit, "search": args.search}
    items = _items(_api_call("GET", "/api/companies", cfg=cfg, params=params))
    if args.json:
        print(json.dumps(items, indent=2))
        return 0
    for co in items:
        ind = f"  ({co.get('industry')})" if co.get("industry") else ""
        print(f"  {co.get('name')}{ind}   {co.get('id')}")
    return 0


def cmd_companies_create(args, cfg: dict) -> int:
    body = {"name": args.name}
    for arg_name, wire in (("website", "website"), ("industry", "industry"),
                           ("email", "email"), ("phone", "phone")):
        val = getattr(args, arg_name)
        if val:
            body[wire] = val
    data = _data(_api_call("POST", "/api/companies", cfg=cfg, body=body))
    if args.json:
        print(json.dumps(data, indent=2))
        return 0
    print(f"Created company: {data.get('name')} ({data.get('id')})")
    return 0


def cmd_custom_fields_list(args, cfg: dict) -> int:
    params = {"entityType": args.entity}
    items = _items(_api_call("GET", "/api/custom-fields", cfg=cfg, params=params))
    if args.json:
        print(json.dumps(items, indent=2))
        return 0
    for cf in items:
        req = " *required" if cf.get("isRequired") else ""
        print(f"  {cf.get('entityType')}.{cf.get('fieldKey')} "
              f"({cf.get('fieldType')}) — {cf.get('label')}{req}   {cf.get('id')}")
    return 0


def cmd_saved_views_list(args, cfg: dict) -> int:
    params = {"entityType": args.entity} if args.entity else None
    items = _items(_api_call("GET", "/api/saved-views", cfg=cfg, params=params))
    if args.json:
        print(json.dumps(items, indent=2))
        return 0
    for sv in items:
        print(f"  {sv.get('entityType')}: {sv.get('name')}   {sv.get('id')}")
    return 0


# ─── argparse plumbing ─────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="mesh", description=f"meshbook-cli {VERSION}")
    p.add_argument("--json", action="store_true", help="machine-parseable output where applicable")
    p.add_argument("--version", action="version", version=f"meshbook-cli {VERSION}")

    sub = p.add_subparsers(dest="cmd", required=True)

    # login / logout / whoami / doctor
    s = sub.add_parser("login", help="paste an API token (mint at /v2/#/account/api-tokens)")
    s.add_argument("--token", help="mb_token_… string (omit to be prompted)")
    s.add_argument("--base", help=f"meshbook base URL (default: {DEFAULT_BASE})")
    s.set_defaults(func=cmd_login)

    s = sub.add_parser("logout", help="clear ~/.meshbook/config")
    s.set_defaults(func=cmd_logout)

    s = sub.add_parser("whoami", help="who are you, what mesh are you in")
    s.set_defaults(func=cmd_whoami)

    s = sub.add_parser("doctor", help="connectivity + auth + active-mesh sanity")
    s.set_defaults(func=cmd_doctor)

    # meshes
    sm = sub.add_parser("meshes", help="mesh picker")
    sms = sm.add_subparsers(dest="meshes_cmd", required=True)
    s = sms.add_parser("list", help="list meshes you're in")
    s.set_defaults(func=cmd_meshes_list)
    s = sms.add_parser("use", help="set active mesh")
    s.add_argument("name", help="mesh name or UUID")
    s.set_defaults(func=cmd_meshes_use)

    # contacts
    sc = sub.add_parser("contacts", help="CRM contacts")
    scs = sc.add_subparsers(dest="contacts_cmd", required=True)
    s = scs.add_parser("list", help="list contacts in active mesh")
    s.add_argument("--search", help="search term")
    s.add_argument("--limit", type=int, default=50)
    s.set_defaults(func=cmd_contacts_list)
    s = scs.add_parser("create", help="create a contact")
    s.add_argument("--first", required=True)
    s.add_argument("--last")
    s.add_argument("--email")
    s.add_argument("--company", help="company name (resolved server-side via §22c)")
    s.set_defaults(func=cmd_contacts_create)

    # chat
    ch = sub.add_parser("chat", help="mesh chat")
    chs = ch.add_subparsers(dest="chat_cmd", required=True)
    s = chs.add_parser("post", help="post a message in active mesh")
    s.add_argument("message")
    s.set_defaults(func=cmd_chat_post)
    s = chs.add_parser("list", help="recent messages in active mesh")
    s.add_argument("--limit", type=int, default=20)
    s.set_defaults(func=cmd_chat_list)
    s = chs.add_parser("attach", help="attach a file to a chat message (§26d-json)")
    s.add_argument("message_id", help="UUID of the message to attach to")
    s.add_argument("file", help="path to local file to attach")
    s.add_argument("--filename", help="override filename stored on the server")
    s.add_argument("--mime", help="override MIME type (default: guess from extension)")
    s.set_defaults(func=cmd_chat_attach)
    s = chs.add_parser(
        "react",
        help="react to a chat message (works for entity, channel, and DM messages)",
    )
    s.add_argument("message_id", help="UUID of the message")
    s.add_argument("emoji", help="emoji to add, e.g. ✅, 📋, 🤷, 🕒")
    s.set_defaults(func=cmd_chat_react)
    s = chs.add_parser("unreact", help="remove a reaction from a chat message")
    s.add_argument("message_id")
    s.add_argument("emoji")
    s.set_defaults(func=cmd_chat_unreact)

    # channels  (§31 sweep — v0.2.0)
    sch = sub.add_parser("channels", help="mesh channels (groups + broadcasts)")
    schs = sch.add_subparsers(dest="channels_cmd", required=True)
    s = schs.add_parser("list", help="list channels in active mesh")
    s.set_defaults(func=cmd_channels_list)
    s = schs.add_parser("read", help="read recent messages in a channel")
    s.add_argument("channel", help="channel name (with or without '#') or UUID")
    s.add_argument("--limit", type=int, default=20)
    s.set_defaults(func=cmd_channels_read)
    s = schs.add_parser("post", help="post a message to a channel")
    s.add_argument("channel", help="channel name or UUID")
    s.add_argument("message", help="message body (markdown)")
    s.set_defaults(func=cmd_channels_post)
    s = schs.add_parser("reply", help="threaded reply to an existing channel message")
    s.add_argument("message_id", help="UUID of the parent message")
    s.add_argument("message", help="reply body (markdown)")
    s.set_defaults(func=cmd_channels_reply)
    s = schs.add_parser("create", help="create a channel (mesh admin only by default)")
    s.add_argument("name", help="channel name (1-32 chars; leading '#' stripped)")
    s.add_argument("--topic", help="optional topic line")
    s.add_argument(
        "--type", choices=("group", "broadcast"), default="group",
        help="channel type — 'broadcast' is admin-post-only",
    )
    s.add_argument(
        "--severity", choices=("announcement", "fyi"),
        help="for broadcast channels; default 'fyi' if --type=broadcast",
    )
    s.add_argument("--private", action="store_true",
                   help="private channel (invite-only)")
    s.set_defaults(func=cmd_channels_create)

    # dm  (§31 sweep — v0.2.0)
    sdm = sub.add_parser("dm", help="direct messages")
    sdms = sdm.add_subparsers(dest="dm_cmd", required=True)
    s = sdms.add_parser("list", help="list DM threads in active mesh")
    s.set_defaults(func=cmd_dm_list)
    s = sdms.add_parser("read", help="read DM thread with a user")
    s.add_argument("user", help="username, displayName, or UUID")
    s.add_argument("--limit", type=int, default=20)
    s.set_defaults(func=cmd_dm_read)
    s = sdms.add_parser("send", help="send a DM (auto-opens thread)")
    s.add_argument("user", help="username, displayName, or UUID")
    s.add_argument("message", help="message body (markdown)")
    s.set_defaults(func=cmd_dm_send)

    # leads  (§31 batch 2 — v0.3.0)
    sl = sub.add_parser("leads", help="CRM leads")
    sls = sl.add_subparsers(dest="leads_cmd", required=True)
    s = sls.add_parser("list", help="list leads in active mesh")
    s.add_argument("--limit", type=int, default=50)
    s.add_argument("--pipeline", help="filter by pipeline UUID")
    s.add_argument("--stage", help="filter by stage UUID")
    s.add_argument("--company", help="filter by company UUID")
    s.set_defaults(func=cmd_leads_list)
    s = sls.add_parser("create", help="create a lead")
    s.add_argument("--title", required=True)
    s.add_argument("--pipeline", required=True, help="pipeline UUID")
    s.add_argument("--stage", required=True, help="stage UUID")
    s.add_argument("--value", type=float, help="value amount")
    s.add_argument("--description")
    s.set_defaults(func=cmd_leads_create)
    s = sls.add_parser("move", help="move a lead to a different pipeline stage")
    s.add_argument("lead_id", help="lead UUID")
    s.add_argument("stage", help="target stage UUID")
    s.set_defaults(func=cmd_leads_move)

    # tasks  (§31 batch 2 — v0.3.0)
    st = sub.add_parser("tasks", help="project tasks")
    sts = st.add_subparsers(dest="tasks_cmd", required=True)
    s = sts.add_parser("list", help="list tasks in active mesh")
    s.add_argument("--limit", type=int, default=50)
    s.add_argument("--project", help="filter by project UUID")
    s.add_argument("--assignee", help="filter by assignee UUID")
    s.add_argument("--status", help="filter by status (NotStarted/InProgress/Blocked/Review/Done/Cancelled)")
    s.set_defaults(func=cmd_tasks_list)
    s = sts.add_parser("create", help="create a task")
    s.add_argument("--project", required=True, help="project UUID")
    s.add_argument("--title", required=True)
    s.add_argument("--priority", help="Low/Normal/High/Urgent")
    s.add_argument("--description")
    s.set_defaults(func=cmd_tasks_create)
    s = sts.add_parser("complete", help="mark a task done (or another terminal status)")
    s.add_argument("task_id", help="task UUID")
    s.add_argument("--status", default="Done",
                   help="terminal status to set (default Done; e.g. Cancelled)")
    s.set_defaults(func=cmd_tasks_complete)

    # projects  (§31 batch 2 — v0.3.0)
    sp = sub.add_parser("projects", help="task-container projects")
    sps = sp.add_subparsers(dest="projects_cmd", required=True)
    s = sps.add_parser("list", help="list projects in active mesh")
    s.add_argument("--limit", type=int, default=50)
    s.add_argument("--portfolio", help="filter by portfolio UUID")
    s.add_argument("--status", help="filter by status (Planning/Active/OnHold/Complete/Cancelled)")
    s.set_defaults(func=cmd_projects_list)
    s = sps.add_parser("create", help="create a project")
    s.add_argument("--name", required=True)
    s.add_argument("--code", help="short project code")
    s.add_argument("--description")
    s.add_argument("--portfolio", help="portfolio UUID")
    s.set_defaults(func=cmd_projects_create)

    # companies  (§31 batch 2 — v0.3.0)
    sco = sub.add_parser("companies", help="CRM companies")
    scos = sco.add_subparsers(dest="companies_cmd", required=True)
    s = scos.add_parser("list", help="list companies in active mesh")
    s.add_argument("--search", help="search term")
    s.add_argument("--limit", type=int, default=50)
    s.set_defaults(func=cmd_companies_list)
    s = scos.add_parser("create", help="create a company")
    s.add_argument("--name", required=True)
    s.add_argument("--website")
    s.add_argument("--industry")
    s.add_argument("--email")
    s.add_argument("--phone")
    s.set_defaults(func=cmd_companies_create)

    # custom-fields (read)  (§31 batch 2 — v0.3.0)
    scf = sub.add_parser("custom-fields", help="custom field definitions (read)")
    scfs = scf.add_subparsers(dest="custom_fields_cmd", required=True)
    s = scfs.add_parser("list", help="list custom field definitions")
    s.add_argument("--entity", help="filter by entity type (contact/company/lead/...)")
    s.set_defaults(func=cmd_custom_fields_list)

    # saved-views (read)  (§31 batch 2 — v0.3.0)
    ssv = sub.add_parser("saved-views", help="saved views (read)")
    ssvs = ssv.add_subparsers(dest="saved_views_cmd", required=True)
    s = ssvs.add_parser("list", help="list saved views")
    s.add_argument("--entity", help="filter by entity type")
    s.set_defaults(func=cmd_saved_views_list)

    # notifications
    s = sub.add_parser("notifications", help="recent notifications")
    s.set_defaults(func=cmd_notifications)

    return p


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    cfg = load_config()
    try:
        return args.func(args, cfg)
    except APIError as e:
        print(f"API error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
