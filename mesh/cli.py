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
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

VERSION = "0.6.0"
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
    if getattr(args, "reply_to", None):
        # Entity-chat threading: the server accepts parentMessageId
        # (also replyToId) on the mesh-chat POST.
        body["parentMessageId"] = args.reply_to
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


def cmd_chat_search(args, cfg: dict) -> int:
    """§84 — hybrid (FTS + semantic) search over everything readable in the
    active mesh: the mesh feed, entity threads, channels, and your own DMs.
    The server fuses keyword and meaning-based matches; `semantic: false`
    in the envelope means the embedding arm was down and results are
    keyword-only (still valid, just narrower recall)."""
    if not cfg.get("active_mesh_id"):
        print("No active mesh. Run: mesh meshes use NAME", file=sys.stderr)
        return 2
    payload = _api_call(
        "GET", "/api/chat/search", cfg=cfg,
        params={"q": args.query, "limit": args.limit},
    )
    data = payload.get("data", payload) if isinstance(payload, dict) else payload
    items = data.get("items", []) if isinstance(data, dict) else []
    if args.json:
        print(json.dumps(data, indent=2))
        return 0
    semantic = bool(isinstance(data, dict) and data.get("semantic"))
    if not items:
        note = "" if semantic else " (semantic arm was off — keyword-only)"
        print(f"No matches{note}.")
        return 0
    if not semantic:
        print("note: semantic arm off — keyword-only results", file=sys.stderr)
    for h in items:
        ts = (h.get("createdAt") or "")[:19].replace("T", " ")
        if h.get("channelId"):
            place = "DM" if h.get("channelType") == "dm" else f"#{h.get('channelName') or '?'}"
        elif h.get("entityType") == "mesh":
            place = "feed"
        else:
            place = f"{h.get('entityType')}:{(h.get('entityId') or '')[:8]}"
        arms = ",".join(h.get("matchedArms") or [])
        preview = (h.get("preview") or "").replace("\n", " ")[:160]
        print(f"  [{ts}] {place} | {h.get('authorName') or '?'} ({arms})")
        print(f"      {preview}")
        print(f"      id={h.get('id')}")
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


# ─── §31 batch 2b — members / task time-logs / saved-view create ───────
#
# Closes the remaining daily-driver gaps from §31.
#
# NOTE: `api-tokens` (list/mint/revoke) is deliberately NOT added. Those
# endpoints (/api/me/api-tokens) refuse API-token callers by design — a
# leaked bearer token must not be able to mint more tokens — and this CLI
# authenticates with a bearer token, so the verbs would only ever 403.
# Token management stays SPA-cookie-only. (Verified against
# app/routers/api_tokens.py `_refuse_if_api_token_call` on all three routes.)


def _self_user_id(cfg: dict) -> str | None:
    """Resolve the signed-in user's own UUID via /api/me (for `leave`)."""
    try:
        me = _data(_api_call("GET", "/api/me", cfg=cfg))
    except APIError:
        return None
    user = me.get("user") if isinstance(me, dict) else None
    return (user or {}).get("id")


def cmd_members_invite(args, cfg: dict) -> int:
    if not cfg.get("active_mesh_id"):
        print("No active mesh. Run: mesh meshes use NAME", file=sys.stderr)
        return 2
    mesh_id = cfg["active_mesh_id"]
    uname = args.user.lstrip("@")
    body: dict = {"username": uname}
    if args.role:
        body["role"] = args.role
    data = _data(_api_call("POST", f"/api/meshes/{mesh_id}/invite", cfg=cfg, body=body))
    if args.json:
        print(json.dumps(data, indent=2))
        return 0
    suffix = f" as {args.role}" if args.role else " (default invite role)"
    print(f"Invited @{uname}{suffix}")
    return 0


def cmd_members_accept(args, cfg: dict) -> int:
    """Accept (or --decline) a pending invitation to a mesh by its UUID.
    The mesh isn't active yet, so this takes the mesh id explicitly —
    list pending invites in the SPA or via /api/meshes/my-pending."""
    action = "decline" if args.decline else "accept"
    data = _data(_api_call(
        "POST", f"/api/meshes/{args.mesh}/respond", cfg=cfg, body={"action": action}
    ))
    if args.json:
        print(json.dumps(data, indent=2))
        return 0
    print(f"{action.title()}ed invitation to mesh {args.mesh}")
    return 0


def cmd_members_pending(args, cfg: dict) -> int:
    """List meshes you've been invited to but haven't responded to yet
    (GET /api/meshes/my-pending) — the CLI mirror of the SPA's "Outstanding
    Invitations" panel. Each row prints the mesh UUID, which is exactly the
    argument `mesh members accept <uuid>` (or `--decline`) wants."""
    items = _items(_api_call("GET", "/api/meshes/my-pending", cfg=cfg))
    if args.json:
        print(json.dumps(items, indent=2))
        return 0
    if not items:
        print("  (no pending invitations)")
        return 0
    for m in items:
        typ = m.get("meshType") or m.get("type") or "?"
        role = m.get("memberRole") or "member"
        print(f"  ✉ {m.get('name')}  ({typ})  invited as {role}   {m.get('id')}")
    print("\n  accept with:  mesh members accept <uuid>   (or --decline)")
    return 0


def cmd_members_set_role(args, cfg: dict) -> int:
    if not cfg.get("active_mesh_id"):
        print("No active mesh. Run: mesh meshes use NAME", file=sys.stderr)
        return 2
    mesh_id = cfg["active_mesh_id"]
    user = _resolve_user(args.user, cfg)
    if not user:
        print(f"User {args.user!r} not found in active mesh.", file=sys.stderr)
        return 1
    data = _data(_api_call(
        "POST", f"/api/meshes/{mesh_id}/set-role",
        cfg=cfg, body={"userId": user["id"], "role": args.role},
    ))
    if args.json:
        print(json.dumps(data, indent=2))
        return 0
    print(f"Set {args.user} → {args.role} in active mesh")
    return 0


def cmd_members_remove(args, cfg: dict) -> int:
    if not cfg.get("active_mesh_id"):
        print("No active mesh. Run: mesh meshes use NAME", file=sys.stderr)
        return 2
    mesh_id = cfg["active_mesh_id"]
    user = _resolve_user(args.user, cfg)
    if not user:
        print(f"User {args.user!r} not found in active mesh.", file=sys.stderr)
        return 1
    data = _data(_api_call(
        "POST", f"/api/meshes/{mesh_id}/remove", cfg=cfg, body={"userId": user["id"]}
    ))
    if args.json:
        print(json.dumps(data, indent=2))
        return 0
    print(f"Removed {args.user} from active mesh")
    return 0


def cmd_members_leave(args, cfg: dict) -> int:
    if not cfg.get("active_mesh_id"):
        print("No active mesh. Run: mesh meshes use NAME", file=sys.stderr)
        return 2
    mesh_id = cfg["active_mesh_id"]
    uid = _self_user_id(cfg)
    if not uid:
        print("Couldn't resolve your own user id from /api/me.", file=sys.stderr)
        return 1
    # §49: Account Managers can't leave a mesh attached to their own
    # subscription — the server returns 403; surface its message.
    data = _data(_api_call(
        "POST", f"/api/meshes/{mesh_id}/remove", cfg=cfg, body={"userId": uid}
    ))
    if args.json:
        print(json.dumps(data, indent=2))
        return 0
    print(f"Left mesh {mesh_id}")
    return 0


def cmd_tasks_log(args, cfg: dict) -> int:
    """Log hours against a task (POST /api/task-time-logs). Date defaults
    to today (UTC). Hours must be > 0 and ≤ 24."""
    import datetime as _dt
    log_date = args.date or _dt.datetime.now(_dt.timezone.utc).date().isoformat()
    body: dict = {"taskId": args.task_id, "hours": args.hours, "loggedForDate": log_date}
    if args.note:
        body["note"] = args.note
    data = _data(_api_call("POST", "/api/task-time-logs", cfg=cfg, body=body))
    if args.json:
        print(json.dumps(data, indent=2))
        return 0
    print(f"Logged {args.hours}h on task {args.task_id} for {log_date}")
    return 0


def cmd_saved_views_create(args, cfg: dict) -> int:
    """Create a saved view. NOTE the create field is `module` (the entity
    grouping the view belongs to, e.g. leads / contacts / tasks), NOT
    `entityType` — verified against app/routers/saved_views.py SavedViewIn."""
    body: dict = {"module": args.module, "name": args.name}
    if args.filter:
        try:
            body["filterJson"] = json.loads(args.filter)
        except json.JSONDecodeError as e:
            print(f"--filter must be valid JSON: {e}", file=sys.stderr)
            return 2
    if args.shared:
        body["isShared"] = True
    data = _data(_api_call("POST", "/api/saved-views", cfg=cfg, body=body))
    if args.json:
        print(json.dumps(data, indent=2))
        return 0
    print(f"Created saved view '{args.name}' for {args.module}  ({data.get('id')})")
    return 0


# ─── files (§78 — entity attachments + downloads) ──────────────────────


def _api_download(path: str, *, cfg: dict) -> tuple[bytes, str]:
    """Raw-bytes GET (downloads are not JSON). Returns (bytes, filename)
    where filename comes from Content-Disposition when present."""
    base = cfg.get("base") or DEFAULT_BASE
    url = base.rstrip("/") + path
    headers = {"User-Agent": f"meshbook-cli/{VERSION}"}
    token = cfg.get("token")
    if not token:
        print("Not signed in. Run: mesh login", file=sys.stderr)
        sys.exit(2)
    headers["Authorization"] = f"Bearer {token}"
    if cfg.get("active_mesh_id"):
        headers["X-Active-Mesh-Id"] = cfg["active_mesh_id"]
    req = urllib.request.Request(url, method="GET", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read()
            disp = resp.headers.get("Content-Disposition", "")
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace") if e.fp else ""
        try:
            payload = json.loads(body)
            err = (payload.get("error") or {}) if isinstance(payload, dict) else {}
            raise APIError(e.code, err.get("code", "http_error"), err.get("message", body[:200])) from e
        except json.JSONDecodeError:
            raise APIError(e.code, "http_error", body[:200]) from e
    except urllib.error.URLError as e:
        raise APIError(0, "network_error", str(e.reason)) from e
    filename = "attachment"
    # filename*=UTF-8''… wins over the plain filename= fallback.
    m = re.search(r"filename\*=UTF-8''([^;]+)", disp)
    if m:
        filename = urllib.parse.unquote(m.group(1))
    else:
        m = re.search(r'filename="([^"]+)"', disp)
        if m:
            filename = m.group(1)
    return raw, filename


def _read_local_file(path_str: str) -> tuple[bytes, Path] | None:
    path = Path(path_str)
    if not path.exists():
        print(f"File not found: {path}", file=sys.stderr)
        return None
    raw = path.read_bytes()
    if not raw:
        print(f"File is empty: {path}", file=sys.stderr)
        return None
    return raw, path


def cmd_file_attach(args, cfg: dict) -> int:
    """Attach a local file directly to an entity (company, contact, lead,
    project, task, portfolio, calendar_event, mesh) via the §78 JSON path."""
    import base64
    import mimetypes

    loaded = _read_local_file(args.file)
    if loaded is None:
        return 2
    raw, path = loaded
    mime = args.mime or mimetypes.guess_type(str(path))[0] or "application/octet-stream"
    body = {
        "filename": args.filename or path.name,
        "mimeType": mime,
        "base64Bytes": base64.b64encode(raw).decode("ascii"),
    }
    payload = _api_call(
        "POST",
        f"/api/entities/{args.entity_type}/{args.entity_id}/attachments/json",
        cfg=cfg,
        body=body,
    )
    data = _data(payload)
    if args.json:
        print(json.dumps(data, indent=2))
        return 0
    print(
        f"Attached {data.get('filename')} to {args.entity_type}/{args.entity_id} "
        f"({data.get('byteSize')} bytes, {data.get('mimeType')}) id={data.get('id')}"
    )
    return 0


def cmd_file_link(args, cfg: dict) -> int:
    payload = _api_call(
        "POST",
        f"/api/entities/{args.entity_type}/{args.entity_id}/attachments/links",
        cfg=cfg,
        body={"url": args.url, "filename": args.filename},
    )
    data = _data(payload)
    if args.json:
        print(json.dumps(data, indent=2))
        return 0
    print(f"Linked {data.get('externalUrl')} to {args.entity_type}/{args.entity_id} id={data.get('id')}")
    return 0


def cmd_file_list(args, cfg: dict) -> int:
    payload = _api_call(
        "GET",
        f"/api/entities/{args.entity_type}/{args.entity_id}/attachments",
        cfg=cfg,
    )
    items = _items(payload)
    if args.json:
        print(json.dumps(items, indent=2))
        return 0
    if not items:
        print("(no attachments)")
        return 0
    for a in items:
        kind = a.get("kind")
        if kind == "file":
            print(f"{a.get('id')}  {a.get('filename')}  {a.get('byteSize')} bytes  {a.get('mimeType')}")
        else:
            print(f"{a.get('id')}  {a.get('filename')}  → {a.get('externalUrl')}")
    return 0


def _download_to(args, cfg: dict, path: str) -> int:
    raw, server_name = _api_download(path, cfg=cfg)
    out = Path(args.out) if args.out else Path(server_name)
    if out.is_dir():
        out = out / server_name
    out.write_bytes(raw)
    if args.json:
        print(json.dumps({"path": str(out), "bytes": len(raw)}))
    else:
        print(f"Saved {out} ({len(raw):,} bytes)")
    return 0


def cmd_file_download(args, cfg: dict) -> int:
    """Download an entity attachment (§78)."""
    return _download_to(args, cfg, f"/api/entity-attachments/{args.attachment_id}/download")


def cmd_file_delete(args, cfg: dict) -> int:
    payload = _api_call(
        "DELETE", f"/api/entity-attachments/{args.attachment_id}", cfg=cfg
    )
    data = _data(payload)
    if args.json:
        print(json.dumps(data, indent=2))
        return 0
    print(f"Deleted attachment {args.attachment_id}")
    return 0


def cmd_chat_download(args, cfg: dict) -> int:
    """Download a chat attachment — closes the read half of §78.3."""
    return _download_to(args, cfg, f"/api/chat-attachments/{args.attachment_id}/download")


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
    s.add_argument("--reply-to", dest="reply_to",
                   help="UUID of a message in this mesh to thread the reply under")
    s.set_defaults(func=cmd_chat_post)
    s = chs.add_parser("list", help="recent messages in active mesh")
    s.add_argument("--limit", type=int, default=20)
    s.set_defaults(func=cmd_chat_list)
    s = chs.add_parser(
        "search",
        help="hybrid keyword+semantic search over readable chat in active mesh (§84)",
    )
    s.add_argument("query", help="what you're looking for — meaning works, not just keywords")
    s.add_argument("--limit", type=int, default=20)
    s.add_argument("--json", action="store_true", help="raw JSON output")
    s.set_defaults(func=cmd_chat_search)
    s = chs.add_parser("attach", help="attach a file to a chat message (§26d-json)")
    s.add_argument("message_id", help="UUID of the message to attach to")
    s.add_argument("file", help="path to local file to attach")
    s.add_argument("--filename", help="override filename stored on the server")
    s.add_argument("--mime", help="override MIME type (default: guess from extension)")
    s.set_defaults(func=cmd_chat_attach)
    s = chs.add_parser("download", help="download a chat attachment by id (§78)")
    s.add_argument("attachment_id", help="UUID of the chat attachment")
    s.add_argument("--out", help="output path or directory (default: server filename in cwd)")
    s.set_defaults(func=cmd_chat_download)
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

    # file  (§78 — entity attachments, v0.6.0)
    fl = sub.add_parser("file", help="entity attachments — attach/list/download files on any entity")
    fls = fl.add_subparsers(dest="file_cmd", required=True)
    s = fls.add_parser("attach", help="attach a local file to an entity")
    s.add_argument("entity_type", help="company|contact|lead|project|task|portfolio|calendar_event|mesh")
    s.add_argument("entity_id", help="UUID of the entity")
    s.add_argument("file", help="path to local file to attach")
    s.add_argument("--filename", help="override filename stored on the server")
    s.add_argument("--mime", help="override MIME type (default: guess from extension)")
    s.set_defaults(func=cmd_file_attach)
    s = fls.add_parser("link", help="attach an external URL to an entity")
    s.add_argument("entity_type")
    s.add_argument("entity_id")
    s.add_argument("url", help="https:// URL to attach")
    s.add_argument("--filename", help="display name for the link")
    s.set_defaults(func=cmd_file_link)
    s = fls.add_parser("list", help="list attachments on an entity")
    s.add_argument("entity_type")
    s.add_argument("entity_id")
    s.set_defaults(func=cmd_file_list)
    s = fls.add_parser("download", help="download an entity attachment by id")
    s.add_argument("attachment_id", help="UUID of the attachment")
    s.add_argument("--out", help="output path or directory (default: server filename in cwd)")
    s.set_defaults(func=cmd_file_download)
    s = fls.add_parser("delete", help="delete an entity attachment (uploader or mesh admin)")
    s.add_argument("attachment_id")
    s.set_defaults(func=cmd_file_delete)

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
    s = sts.add_parser("log", help="log hours against a task (time tracking)")
    s.add_argument("task_id", help="task UUID")
    s.add_argument("--hours", type=float, required=True, help="hours worked (0 < h <= 24)")
    s.add_argument("--date", help="date worked (YYYY-MM-DD; default today UTC)")
    s.add_argument("--note", help="optional note")
    s.set_defaults(func=cmd_tasks_log)

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
    s = ssvs.add_parser("create", help="create a saved view")
    s.add_argument("--module", required=True,
                   help="entity grouping the view belongs to (leads/contacts/tasks/...)")
    s.add_argument("--name", required=True, help="view name")
    s.add_argument("--filter", help="filter as a JSON object string")
    s.add_argument("--shared", action="store_true", help="share with the whole mesh")
    s.set_defaults(func=cmd_saved_views_create)

    # members  (§31 batch 2b)
    smem = sub.add_parser("members", help="mesh membership (invite / pending / accept / role / remove / leave)")
    smems = smem.add_subparsers(dest="members_cmd", required=True)
    s = smems.add_parser("invite", help="invite a user to the active mesh by username")
    s.add_argument("user", help="username (with or without '@')")
    s.add_argument("--role", choices=("admin", "member", "reader"),
                   help="role (omit to use the mesh's default invite role)")
    s.set_defaults(func=cmd_members_invite)
    s = smems.add_parser("pending", help="list meshes you've been invited to (with UUIDs to accept)")
    s.set_defaults(func=cmd_members_pending)
    s = smems.add_parser("accept", help="accept (or --decline) a pending invitation by mesh UUID")
    s.add_argument("mesh", help="mesh UUID you were invited to")
    s.add_argument("--decline", action="store_true", help="decline instead of accept")
    s.set_defaults(func=cmd_members_accept)
    s = smems.add_parser("set-role", help="change a member's role in the active mesh")
    s.add_argument("user", help="username, displayName, or UUID")
    s.add_argument("role", choices=("member", "reader"), help="new role")
    s.set_defaults(func=cmd_members_set_role)
    s = smems.add_parser("remove", help="remove a member from the active mesh")
    s.add_argument("user", help="username, displayName, or UUID")
    s.set_defaults(func=cmd_members_remove)
    s = smems.add_parser("leave", help="leave the active mesh (you)")
    s.set_defaults(func=cmd_members_leave)

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
