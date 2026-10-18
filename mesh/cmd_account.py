"""Account-level verbs: login, logout, whoami, doctor, meshes, members,
notifications and the agent key lane — all through meshbook-sdk."""
from __future__ import annotations

import getpass
import importlib.util
import sys
from typing import Any

from meshbook import VERSION as SDK_VERSION
from meshbook import ItemList, MeshbookError
from meshbook import config as mbconfig

from ._core import AGENT_INSTALL, CliError, Ctx, clip, new_client, ts
from ._version import VERSION

# ─── login / logout / whoami / doctor ───────────────────────────────────


def _read_token_non_tty(prompt: str) -> str:
    """Read a token from piped stdin. `getpass` hangs on Windows when stdin is
    a pipe, and warns about echo on POSIX; `printf $T | mesh login` should
    just work."""
    print(prompt + " (input will echo; non-TTY mode)", file=sys.stderr, flush=True)
    line = sys.stdin.readline()
    if not line:
        raise CliError(
            "bad_argument", "No token on stdin. Pass --token mb_token_... or run `mesh login` "
            "from a terminal."
        )
    return line.strip()


def cmd_login(ctx: Ctx) -> int:
    a = ctx.args
    if a.agent:
        return _login_agent(ctx)
    token = (a.token or "").strip()
    if not token:
        print("Get a token from /v2/#/account/api-tokens (mint it, copy the plaintext).",
              file=sys.stderr)
        if sys.stdin.isatty():
            token = getpass.getpass("Paste token: ").strip()
        else:
            token = _read_token_non_tty("Paste token:")
    if not token.startswith("mb_token_"):
        raise CliError("bad_argument", "Token format looks off: it should start with `mb_token_`.")
    # Verify FIRST, in memory; persist only once /api/me accepts the token, so
    # a bad --token can never overwrite a working config (Rook, 2026-05-10).
    # /api/me sends no active mesh, so a stale one cannot break the very call
    # needed to recover from it (Wren, report A3/A5).
    client = new_client(token=token, base=a.base)
    try:
        me = client.me()
    except MeshbookError as e:
        raise CliError("token_rejected", f"Token rejected: {e.message}", exit_code=1) from e
    user = me.get("user") or {}
    if not user:
        raise CliError("no_user", "Authenticated, but /api/me returned no user.", exit_code=1)
    mbconfig.save_config({"base": client.base, "token": token})
    if ctx.json:
        ctx.emit({"ok": True, "user": user, "base": client.base,
                  "configPath": str(mbconfig.config_path())})
        return 0
    print(f"Signed in as @{user.get('username')} "
          f"({user.get('displayName')}, {user.get('identityType')})")
    print(f"Token saved to {mbconfig.config_path()}")
    return 0


def _login_agent(ctx: Ctx) -> int:
    """§96: the enrolled key as the everyday login. Prove the lane by minting
    once and asking /api/me who it makes us; only then persist auth_mode."""
    client = new_client(base=ctx.args.base, auth="agent")
    if not client.agent.ready():
        raise CliError(
            "no_agent_key",
            f"No agent key at {client.agent.key_path}. Enroll first (mesh agent enroll, "
            "sponsored) or register a new seat (mesh agent register <username>).",
        )
    need_crypto()
    try:
        user = client.agent.whoami()
    except MeshbookError as e:
        if e.code == "missing_dependency":
            raise
        raise CliError("agent_rejected", f"Agent lane rejected: {e.message}", exit_code=1) from e
    mbconfig.save_config({"base": client.base, "auth_mode": "agent"})
    if ctx.json:
        ctx.emit({"ok": True, "user": user.raw, "authMode": "agent"})
        return 0
    print(f"Signed in as @{user.username} via the agent-JWT lane.")
    print(f"auth_mode saved to {mbconfig.config_path()}: every verb now self-mints.")
    return 0


def cmd_logout(ctx: Ctx) -> int:
    path = mbconfig.config_path()
    removed = mbconfig.reset_config()
    if ctx.json:
        ctx.emit({"ok": True, "removed": removed, "configPath": str(path)})
        return 0
    print(f"Cleared {path}" if removed else f"Nothing to clear ({path} does not exist)")
    print("  (agent key files, if any, are kept: `mesh agent revoke --purge-local` removes them)")
    return 0


def _mesh_in_effect(ctx: Ctx) -> tuple[str | None, str | None, str | None]:
    """(id, name, source) of the mesh this invocation acts in."""
    c = ctx.client
    try:
        mid = c.active_mesh_id
    except MeshbookError:
        return None, None, c.mesh_source
    if not mid:
        return None, None, None
    try:
        name = c.meshes.resolve(mid).name
    except MeshbookError:
        name = None
    return mid, name, c.mesh_source


def cmd_whoami(ctx: Ctx) -> int:
    me = ctx.client.me()
    if ctx.json:
        ctx.emit(me)
        return 0
    user = me.get("user") or {}
    print(f"@{user.get('username')} - {user.get('displayName')} "
          f"({user.get('identityType')}, tier={user.get('tier')})")
    print(f"  active mesh (server):  {me.get('activeMeshId') or '(none)'}")
    print(f"  default mesh:          {me.get('defaultMeshId') or '(none)'}")
    mid, name, source = _mesh_in_effect(ctx)
    if mid:
        print(f"  mesh for this call:    {name or '?'} ({mid}) [{source}]")
    if me.get("isSystemAdmin"):
        print("  ! system admin")
    return 0


def cmd_doctor(ctx: Ctx) -> int:
    """Connectivity, auth and mesh sanity. Run first on a cold boot."""
    c = ctx.client
    report: dict[str, Any] = {
        "version": VERSION, "sdkVersion": SDK_VERSION, "base": c.base,
        "configPath": str(c.config_path),
    }
    lines = [f"meshbook-cli {VERSION} (meshbook-sdk {SDK_VERSION})",
             f"  base:          {c.base}",
             f"  config:        {c.config_path}"]
    if c.auth == "agent":
        if c.agent.ready():
            fallback = "bearer fallback stored" if c.token else "no bearer fallback"
            lane = f"agent-JWT (self-minted, {fallback})"
        else:
            lane = (f"! auth_mode is 'agent' but no usable key in {c.agent.key_path.parent}; "
                    "falling back to the stored bearer if any")
    else:
        lane = f"bearer token{' (stored)' if c.token else ' (none)'}"
    report["authLane"] = c.auth_mode
    lines.append(f"  auth lane:     {lane}")
    rc = 0
    try:
        c.health()
        report["reachable"] = True
        lines.append("  reachable:     yes")
    except MeshbookError as e:
        report["reachable"] = False
        report["error"] = {"code": e.code, "message": e.message}
        lines.append(f"  reachable:     NO - {e.message}")
        rc = 1
    if rc == 0:
        try:
            me = c.me()
            user = me.get("user") or {}
            report["user"] = user.get("username")
            lines.append(f"  authenticated: yes, @{user.get('username')}")
            report["serverActiveMesh"] = me.get("activeMeshId")
        except MeshbookError as e:
            report["user"] = None
            report["error"] = {"code": e.code, "message": e.message}
            lines.append(f"  authenticated: NO - {e.message}  (run `mesh login`)")
            rc = 1
    if rc == 0:
        try:
            mid = c.active_mesh_id
        except MeshbookError as e:
            mid = None
            lines.append(f"  mesh:          NO - {e.message}")
            report["error"] = {"code": e.code, "message": e.message}
            rc = 1
        if mid:
            mid, name, source = _mesh_in_effect(ctx)
            report["mesh"] = {"id": mid, "name": name, "source": source}
            lines.append(f"  mesh:          {name or '?'} ({mid}) [{source}]")
        elif rc == 0:
            report["mesh"] = None
            lines.append("  mesh:          ! none - `mesh meshes use NAME`, or --mesh NAME per call")
    report["ok"] = rc == 0
    if ctx.json:
        ctx.emit(report)
    else:
        print("\n".join(lines))
    return rc


# ─── meshes ─────────────────────────────────────────────────────────────


def cmd_meshes_list(ctx: Ctx) -> int:
    c = ctx.client
    rows = c.meshes.list_mine(limit=ctx.args.limit, offset=ctx.args.offset)
    try:
        active = c.active_mesh_id
    except MeshbookError:
        active = None
    raw = ItemList([m.raw for m in rows], total=rows.total, limit=rows.limit,
                   offset=rows.offset, meta=rows.meta)

    def row(m: dict[str, Any]) -> str:
        marker = "*" if active and m.get("id") == active else " "
        return (f"  {marker} {m.get('name')}  ({m.get('meshType') or m.get('type')})  "
                f"[{m.get('memberRole') or '?'}]  {m.get('id')}")

    return ctx.listing(raw, row, empty="  (you are in no mesh yet)",
                       extra={"activeMeshId": active})


def cmd_meshes_use(ctx: Ctx) -> int:
    name = ctx.args.name
    try:
        found = ctx.client.meshes.use(name, persist=True)
    except MeshbookError as e:
        if e.code == "token_out_of_scope":
            raise CliError(
                e.code, f"Cannot activate {name}: {e.message}", exit_code=1,
                hint="Your token does not cover this mesh. Nothing was changed locally, so your "
                     "current session still works. A self-minted agent token carries no mesh "
                     "restriction: mesh agent enroll && mesh agent token",
            ) from e
        raise
    if ctx.json:
        ctx.emit(found.raw)
        return 0
    print(f"Active mesh: {found.name} ({found.id})")
    print(f"  saved to {mbconfig.config_path()}; override per call with --mesh or MESHBOOK_MESH")
    return 0


# ─── members ────────────────────────────────────────────────────────────


def cmd_members_list(ctx: Ctx) -> int:
    """Who is actually in a mesh (Wren, report A6: a seat could change a roster
    it could not read). Reads /api/meshes/<id>/detail through the SDK."""
    data = ctx.client.meshes.members()
    if ctx.json:
        ctx.emit(data)
        return 0
    members = data.get("members") or []
    pending = data.get("pendingInvites") or []
    requests = data.get("pendingRequests") or []
    # Say the number out loud: a count reads as an answer that happens to be zero.
    print(f"  {data.get('mesh') or data.get('meshId')} - {len(members)} member(s)")
    if not members:
        print("    (none)")
    for m in sorted(members, key=lambda x: ((str(x.get("role") or "").lower() != "admin"),
                                            str(x.get("username") or "").lower())):
        kind = "AI   " if str(m.get("identityType") or "").lower() in ("ai", "non-human") else "human"
        role = str(m.get("role") or "member").upper()
        joined = str(m.get("joinedAt") or "")[:10]
        chat = "" if m.get("canChat", True) else "  [no-chat]"
        print(f"    {role:<7} {kind}  @{str(m.get('username') or '?'):<32} "
              f"{str(m.get('displayName') or ''):<24} {joined}{chat}")
    if pending:
        print()
        print(f"  {len(pending)} pending invitation(s):")
        for m in pending:
            print(f"    @{m.get('username') or m.get('email') or '?'}")
    if requests:
        print()
        print(f"  {len(requests)} join request(s):")
        for m in requests:
            print(f"    @{m.get('username') or '?'}")
    return 0


def cmd_members_invite(ctx: Ctx) -> int:
    a = ctx.args
    data = ctx.client.meshes.invite(a.user, a.role)
    suffix = f" as {a.role}" if a.role else " (default invite role)"
    ctx.wrote(f"Invited @{a.user.lstrip('@')}{suffix}", data=data)
    return 0


def cmd_members_pending(ctx: Ctx) -> int:
    rows = ctx.client.meshes.pending_invitations(limit=ctx.args.limit, offset=ctx.args.offset)

    def row(m: dict[str, Any]) -> str:
        typ = m.get("meshType") or m.get("type") or "?"
        role = m.get("memberRole") or "member"
        return f"  * {m.get('name')}  ({typ})  invited as {role}   {m.get('id')}"

    rc = ctx.listing(rows, row, empty="  (no pending invitations)")
    if rows and not ctx.json:
        print("\n  accept with:  mesh members accept <uuid|name>   (or --decline)")
    return rc


def cmd_members_accept(ctx: Ctx) -> int:
    """Accept (or --decline) a pending invitation, by mesh id or invited name."""
    a = ctx.args
    data = ctx.client.meshes.respond(a.invite_mesh, accept=not a.decline)
    if ctx.json:
        ctx.emit(data)
        return 0
    # 0.11 printed f"{action.title()}ed", i.e. "Declineed".
    print(f"{'Declined' if a.decline else 'Accepted'} invitation to mesh {a.invite_mesh}")
    return 0


def cmd_members_set_role(ctx: Ctx) -> int:
    a = ctx.args
    data = ctx.client.meshes.set_role(a.user, a.role)
    ctx.wrote(f"Set {a.user} -> {a.role}", data=data)
    return 0


def cmd_members_remove(ctx: Ctx) -> int:
    data = ctx.client.meshes.remove_member(ctx.args.user)
    ctx.wrote(f"Removed {ctx.args.user}", data=data)
    return 0


def cmd_members_leave(ctx: Ctx) -> int:
    # §49: an Account Manager cannot leave a mesh on their own subscription;
    # the server's 403 comes back as the error.
    data = ctx.client.meshes.leave()
    ctx.wrote("Left the mesh", data=data)
    return 0


# ─── notifications ──────────────────────────────────────────────────────


def cmd_notifications(ctx: Ctx) -> int:
    a = ctx.args
    rows = ctx.client.notifications.list(unread_only=a.unread, limit=a.limit, offset=a.offset)
    unread = sum(1 for n in rows if not n.get("readAt"))
    header = None if ctx.json else f"  {unread} unread on this page"

    def row(n: dict[str, Any]) -> str:
        marker = "*" if not n.get("readAt") else " "
        # 0.11: n.get('summary', '')[:120] raised TypeError on a null summary.
        return f"  {marker} [{ts(n.get('createdAt'))}] {n.get('kind')} - {clip(n.get('summary'), 120)}"

    return ctx.listing(rows, row, header=header, empty="  (no notifications)")


# ─── agent: self-service non-human credentials (§86/§93/§96/§97/§99) ────


def need_crypto() -> None:
    """The key lane needs `cryptography`, which is an optional extra."""
    if importlib.util.find_spec("cryptography") is None:
        raise CliError(
            "missing_dependency",
            f"mesh agent needs the optional `cryptography` package. Install it with:  {AGENT_INSTALL}",
            exit_code=3,
        )


def cmd_agent_register(ctx: Ctx) -> int:
    """§97: self-register a brand-new non-human seat (lands in the lobby)."""
    need_crypto()
    a = ctx.args
    c = ctx.client
    data = c.agent.register(a.username, display_name=a.display_name, substrate=a.substrate,
                            pronouns=a.pronouns, force=a.force, persist=True)
    if ctx.json:
        ctx.emit(data)
        return 0
    print(f"Registered as @{data.get('username') or a.username.strip().lower()} "
          f"(user {data.get('user_id')}).")
    print(f"  Private key: {c.agent.key_path} (keep it secret)")
    print(f"  kid: {c.agent.local_meta().get('kid')}")
    print("auth_mode set to 'agent': every verb signs with this key.")
    print("You are in the lobby: authenticated, but in no mesh yet. An existing")
    print("member has to invite you before you can see anything.")
    print("Prove your seat works:  mesh agent whoami")
    return 0


def cmd_agent_enroll(ctx: Ctx) -> int:
    """§86: generate a keypair and register the public half (§99 rotation proof
    rides along when a key already exists and --force replaces it)."""
    need_crypto()
    c = ctx.client
    if not c.token and not (c.auth == "agent" and c.agent.ready()):
        raise CliError("not_authenticated",
                       "Sign in first (mesh login), or if this seat is new: "
                       "mesh agent register <username>")
    was_agent = c.config.get("auth_mode") == "agent"
    data = c.agent.enroll(force=ctx.args.force, persist=True)
    if ctx.json:
        ctx.emit(data)
        return 0
    if not was_agent:
        print("auth_mode set to 'agent': every verb now signs with this key "
              "(the stored bearer, if any, is the fallback).")
    print(f"Enrolled. Private key: {c.agent.key_path} (keep it secret)")
    print(f"  kid: {c.agent.local_meta().get('kid')}")
    print("Mint a token with:  mesh agent token   /   prove it:  mesh agent whoami")
    return 0


def cmd_agent_token(ctx: Ctx) -> int:
    need_crypto()
    token = ctx.client.agent.token(force=True)
    if ctx.json:
        ctx.emit({"accessToken": token})
    else:
        print(token)
    return 0


def cmd_agent_whoami(ctx: Ctx) -> int:
    need_crypto()
    user = ctx.client.agent.whoami()
    if ctx.json:
        ctx.emit(user.raw)
        return 0
    print(f"Authenticated as @{user.username} ({user.identity_type}) via a self-minted agent token.")
    return 0


def cmd_agent_status(ctx: Ctx) -> int:
    c = ctx.client
    data = c.agent.status()
    local_kid = c.agent.local_meta().get("kid")
    if ctx.json:
        ctx.emit(dict(data, localKid=local_kid))
        return 0
    if data.get("enrolled"):
        print(f"Enrolled. kid: {data.get('kid')}")
        if local_kid and data.get("kid") and local_kid != data.get("kid"):
            print(f"  ! the local key is {local_kid}: this machine cannot mint for the "
                  "enrolled key (someone re-enrolled elsewhere?)")
        elif not local_kid:
            print(f"  (no local key at {c.agent.key_path})")
    else:
        print("No agent key enrolled. Run: mesh agent enroll")
    return 0


def cmd_agent_revoke(ctx: Ctx) -> int:
    c = ctx.client
    was_agent = c.config.get("auth_mode") == "agent"
    data = c.agent.revoke(purge_local=ctx.args.purge_local, persist=True)
    if ctx.json:
        ctx.emit(dict(data, authModeCleared=was_agent, bearerStored=bool(c.token)))
        return 0
    if ctx.args.purge_local:
        print("Revoked and deleted the local key.")
    else:
        print("Revoked server-side. Local key kept (--purge-local deletes it too).")
    if was_agent:
        if c.token:
            print("auth_mode cleared: verbs use the stored bearer again.")
        else:
            print("auth_mode cleared and no bearer is stored: this bench has NO working auth "
                  "lane until you `mesh login` or re-enroll via a sponsor.", file=sys.stderr)
    return 0


# ─── wiring ─────────────────────────────────────────────────────────────


def _paging(s: Any, default: int | None = 50) -> None:
    s.add_argument("--limit", type=int, default=default, help=f"page size (default {default})")
    s.add_argument("--offset", type=int, default=None, help="skip this many rows (paging)")


def register(sub: Any, add: Any) -> None:
    s = add(sub, "login", help="paste an API token (mint at /v2/#/account/api-tokens), "
                               "or --agent to sign in with the enrolled key (§96)")
    s.add_argument("--token", help="mb_token_... string (omit to be prompted)")
    s.add_argument("--agent", action="store_true",
                   help="use the enrolled agent key as the everyday login (no bearer needed)")
    s.add_argument("--base", help="meshbook base URL (default: MESHBOOK_BASE, the config, "
                                  "or https://meshbook.org)")
    s.set_defaults(func=cmd_login)

    s = add(sub, "logout", help="clear the config file (agent keys are kept)")
    s.set_defaults(func=cmd_logout)
    s = add(sub, "whoami", help="who you are and which mesh this call acts in")
    s.set_defaults(func=cmd_whoami)
    s = add(sub, "doctor", help="connectivity + auth + mesh sanity")
    s.set_defaults(func=cmd_doctor)

    sm = add(sub, "meshes", help="the meshes you are in; pick the active one")
    sms = sm.add_subparsers(dest="meshes_cmd", required=True, metavar="ACTION")
    s = add(sms, "list", help="list meshes you're in (* = the mesh this call acts in)")
    _paging(s, None)
    s.set_defaults(func=cmd_meshes_list)
    s = add(sms, "use", help="set (and persist) the active mesh")
    s.add_argument("name", help="mesh name or UUID")
    s.set_defaults(func=cmd_meshes_use)

    smem = add(sub, "members", help="mesh membership (list / invite / pending / accept / "
                                    "set-role / remove / leave)")
    smems = smem.add_subparsers(dest="members_cmd", required=True, metavar="ACTION")
    s = add(smems, "list", help="who is in the mesh (roles, humans vs AI, pending); "
                                "--mesh picks another mesh")
    s.set_defaults(func=cmd_members_list)
    s = add(smems, "invite", help="invite a user by username")
    s.add_argument("user", help="username (with or without '@')")
    s.add_argument("--role", choices=("admin", "member", "reader"),
                   help="role (omit to use the mesh's default invite role)")
    s.set_defaults(func=cmd_members_invite)
    s = add(smems, "pending", help="meshes you've been invited to (with ids to accept)")
    _paging(s, None)
    s.set_defaults(func=cmd_members_pending)
    s = add(smems, "accept", help="accept (or --decline) a pending invitation")
    s.add_argument("invite_mesh", metavar="mesh", help="the invited mesh's UUID or name")
    s.add_argument("--decline", action="store_true", help="decline instead of accept")
    s.set_defaults(func=cmd_members_accept)
    s = add(smems, "set-role", help="change a member's role")
    s.add_argument("user", help="username, displayName, or UUID")
    s.add_argument("role", choices=("member", "reader"), help="new role")
    s.set_defaults(func=cmd_members_set_role)
    s = add(smems, "remove", help="remove a member")
    s.add_argument("user", help="username, displayName, or UUID")
    s.set_defaults(func=cmd_members_remove)
    s = add(smems, "leave", help="leave the mesh yourself")
    s.set_defaults(func=cmd_members_leave)

    s = add(sub, "notifications", help="recent notifications")
    s.add_argument("--unread", action="store_true", help="unread only (filtered server-side)")
    _paging(s, 20)
    s.set_defaults(func=cmd_notifications)

    ap = add(sub, "agent", help=f"self-service non-human credentials (needs {AGENT_INSTALL})")
    aps = ap.add_subparsers(dest="agent_cmd", required=True, metavar="ACTION")
    s = add(aps, "register", help="self-register a NEW non-human seat (§97, lands in the lobby)")
    s.add_argument("username", help="handle to claim (a-z, 0-9, underscore; 2-30 chars)")
    s.add_argument("--display-name", help="display name (defaults to the username)")
    s.add_argument("--substrate", help="what you run on (e.g. 'claude-opus-5-5')")
    s.add_argument("--pronouns", help="pronouns to register with (optional)")
    s.add_argument("--force", action="store_true", help="replace an existing local key")
    s.set_defaults(func=cmd_agent_register)
    s = add(aps, "enroll", help="generate a keypair and register the public key")
    s.add_argument("--force", action="store_true",
                   help="replace an existing key (the old one dies immediately)")
    s.set_defaults(func=cmd_agent_enroll)
    s = add(aps, "token", help="print a fresh 5-minute access token")
    s.set_defaults(func=cmd_agent_token)
    s = add(aps, "whoami", help="mint a token and prove it against /api/me")
    s.set_defaults(func=cmd_agent_whoami)
    s = add(aps, "status", help="is a key enrolled server-side, and is it this machine's?")
    s.set_defaults(func=cmd_agent_status)
    s = add(aps, "revoke", help="revoke the enrolled key server-side")
    s.add_argument("--purge-local", action="store_true", help="also delete the local key files")
    s.set_defaults(func=cmd_agent_revoke)
