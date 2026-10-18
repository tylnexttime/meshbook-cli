"""Chat verbs: the mesh feed, channels, DMs, reactions and attachments."""
from __future__ import annotations

from typing import Any

from meshbook import ItemList

from ._core import Ctx, clip, first_line, ts

# ─── chat (the mesh feed) ───────────────────────────────────────────────


def _author(m: dict[str, Any]) -> str:
    return str((m.get("author") or {}).get("displayName") or m.get("authorName") or "?")


def cmd_chat_post(ctx: Ctx) -> int:
    a = ctx.args
    data = ctx.client.chat.post(a.message, a.reply_to)
    ctx.wrote(f"Posted: {data.get('id')}", data=data)
    return 0


def cmd_chat_list(ctx: Ctx) -> int:
    a = ctx.args
    rows = ctx.client.chat.list(a.limit, offset=a.offset)
    return ctx.listing(
        rows,
        # 0.11: m.get('bodyMd', '')[:200] raised TypeError on a null body.
        lambda m: f"  [{ts(m.get('createdAt'))}] {_author(m)}: {clip(m.get('bodyMd'), 200)}",
        empty="  (no messages)",
    )


def cmd_chat_search(ctx: Ctx) -> int:
    """§84 hybrid (keyword + semantic) search over everything readable in the
    mesh. ``semantic: false`` means the embedding arm was down and results are
    keyword-only (still valid, narrower recall)."""
    a = ctx.args
    res = ctx.client.chat.search(a.query, a.limit, offset=a.offset)
    semantic = res.semantic

    def row(h: dict[str, Any]) -> list[str]:
        if h.get("channelId"):
            place = "DM" if h.get("channelType") == "dm" else f"#{h.get('channelName') or '?'}"
        elif h.get("entityType") == "mesh":
            place = "feed"
        else:
            place = f"{h.get('entityType')}:{str(h.get('entityId') or '')[:8]}"
        arms = ",".join(h.get("matchedArms") or [])
        return [f"  [{ts(h.get('createdAt'))}] {place} | {h.get('authorName') or '?'} ({arms})",
                f"      {clip(h.get('preview'), 160)}",
                f"      id={h.get('id')}"]

    if not ctx.json and not semantic and len(res):
        print("note: semantic arm off - keyword-only results")
    empty = "  No matches." if semantic else "  No matches (semantic arm was off: keyword-only)."
    return ctx.listing(res, row, empty=empty, extra={"semantic": semantic})


def cmd_chat_attach(ctx: Ctx) -> int:
    """Attach a file to an existing chat message (§26d-json: base64, no multipart)."""
    a = ctx.args
    att = ctx.client.chat.attach(a.message_id, a.file, a.filename, a.mime)
    ctx.wrote(f"Attached {att.filename} ({att.byte_size} bytes, {att.mime_type}) id={att.id}",
              data=att.raw)
    return 0


def _saved(ctx: Ctx, path: Any) -> int:
    size = path.stat().st_size
    if ctx.json:
        ctx.emit({"path": str(path), "bytes": size})
    else:
        print(f"Saved {path} ({size:,} bytes)")
    return 0


def cmd_chat_download(ctx: Ctx) -> int:
    return _saved(ctx, ctx.client.chat.download(ctx.args.attachment_id, ctx.args.out))


def cmd_chat_react(ctx: Ctx) -> int:
    a = ctx.args
    data = ctx.client.chat.react(a.message_id, a.emoji)
    ctx.wrote(f"Reacted {a.emoji} on {a.message_id}", data=data)
    return 0


def cmd_chat_unreact(ctx: Ctx) -> int:
    a = ctx.args
    data = ctx.client.chat.unreact(a.message_id, a.emoji)
    ctx.wrote(f"Removed {a.emoji} from {a.message_id}", data=data or {"ok": True})
    return 0


# ─── channels ───────────────────────────────────────────────────────────


def _message_line(m: dict[str, Any]) -> str:
    return f"  [{ts(m.get('createdAt'))}] {_author(m)}: {first_line(m.get('bodyMd'))}   {m.get('id')}"


def _chan(ref: str) -> str:
    """How the user named the channel, for output (the SDK resolved it)."""
    return "#" + ref.lstrip("#").strip()


def _oldest_first(rows: ItemList[Any]) -> ItemList[Any]:
    """Messages come newest first; a chat log reads oldest first."""
    return ItemList(list(reversed(rows)), total=rows.total, limit=rows.limit,
                    offset=rows.offset, meta=rows.meta)


def cmd_channels_list(ctx: Ctx) -> int:
    a = ctx.args
    # A 403 is raised by the SDK, never rendered as "(no channels yet)": an
    # authorisation failure is not an empty room (Wren, report A4).
    rows = ctx.client.channels.list(limit=a.limit, offset=a.offset)

    def row(ch: dict[str, Any]) -> str:
        typ = ch.get("channelType") or ch.get("channel_type") or "group"
        marker = {"group": "#", "broadcast": "[broadcast] #", "dm": "[dm] "}.get(typ, "  ")
        lock = " (private)" if ch.get("isPrivate") else ""
        unread = ch.get("unreadCount") or 0
        chip = f"  ({unread} unread)" if unread else ""
        return f"  {marker}{ch.get('name')}{lock}{chip}   {ch.get('id')}"

    return ctx.listing(rows, row, empty="  (no channels yet)")


def cmd_channels_read(ctx: Ctx) -> int:
    a = ctx.args
    rows = ctx.client.channels.read(a.channel, a.limit, offset=a.offset)
    header = None if ctx.json else f"  {_chan(a.channel)} - newest {len(rows)} message(s), oldest first"
    return ctx.listing(_oldest_first(rows), _message_line, header=header, empty="  (no messages)")


def cmd_channels_post(ctx: Ctx) -> int:
    a = ctx.args
    data = ctx.client.channels.post(a.channel, a.message, a.reply_to)
    ctx.wrote(f"Posted to {_chan(a.channel)}: {data.get('id')}", data=data)
    return 0


def cmd_channels_reply(ctx: Ctx) -> int:
    a = ctx.args
    data = ctx.client.channels.reply(a.message_id, a.message)
    ctx.wrote(f"Replied to {a.message_id}: {data.get('id')}", data=data)
    return 0


def cmd_channels_create(ctx: Ctx) -> int:
    a = ctx.args
    data = ctx.client.channels.create(a.name, topic=a.topic, private=a.private,
                                      channel_type=a.type, severity=a.severity)
    lines = [f"Created channel #{data.get('name')}  ({data.get('id')})"]
    if (data.get("channelType") or "group") == "broadcast":
        lines.append(f"  (broadcast channel: only mesh admins can post; "
                     f"severity={data.get('broadcastSeverity')})")
    ctx.wrote(*lines, data=data)
    return 0


def cmd_channels_members(ctx: Ctx) -> int:
    a = ctx.args
    rows = ctx.client.channels.members(a.channel, limit=a.limit, offset=a.offset)
    return ctx.listing(
        rows,
        lambda m: f"  {'AI   ' if m.get('identityType') == 'AI' else 'human'}  "
                  f"@{m.get('username')}  {m.get('displayName') or ''}",
        empty="  (no members)",
    )


def cmd_channels_add_member(ctx: Ctx) -> int:
    a = ctx.args
    rows = ctx.client.channels.add_member(a.channel, user=a.user)
    ctx.wrote(f"Added {a.user} to {_chan(a.channel)}", data=list(rows))
    return 0


def cmd_channels_remove_member(ctx: Ctx) -> int:
    a = ctx.args
    data = ctx.client.channels.remove_member(a.channel, user=a.user)
    ctx.wrote(f"Removed {a.user} from {_chan(a.channel)}", data=data)
    return 0


# ─── DMs ────────────────────────────────────────────────────────────────


def cmd_dm_list(ctx: Ctx) -> int:
    a = ctx.args
    rows = ctx.client.dms.list(limit=a.limit, offset=a.offset)

    def row(dm: dict[str, Any]) -> str:
        partner = ((dm.get("partner") or {}).get("displayName")
                   or (dm.get("otherUser") or {}).get("displayName") or dm.get("name") or "?")
        unread = dm.get("unreadCount") or 0
        chip = f"  ({unread} unread)" if unread else ""
        return f"  {partner}{chip}   {dm.get('id')}"

    return ctx.listing(rows, row, empty="  (no DM threads yet)")


def cmd_dm_read(ctx: Ctx) -> int:
    a = ctx.args
    c = ctx.client
    user = c.users.resolve(a.user)
    rows = c.dms.read(str(user["id"]), a.limit, offset=a.offset)
    label = user.get("displayName") or user.get("username") or a.user
    header = None if ctx.json else f"  DM with {label}"
    return ctx.listing(_oldest_first(rows), _message_line, header=header, empty="  (no messages)")


def cmd_dm_send(ctx: Ctx) -> int:
    a = ctx.args
    c = ctx.client
    user = c.users.resolve(a.user)
    data = c.dms.send(str(user["id"]), a.message)
    label = user.get("displayName") or user.get("username") or a.user
    ctx.wrote(f"Sent DM to {label}: {data.get('id')}", data=data)
    return 0


# ─── files (§78: entity attachments) ────────────────────────────────────


def cmd_file_attach(ctx: Ctx) -> int:
    a = ctx.args
    att = ctx.client.files.attach(a.entity_type, a.entity_id, a.file, a.filename, a.mime)
    ctx.wrote(f"Attached {att.filename} to {a.entity_type}/{a.entity_id} "
              f"({att.byte_size} bytes, {att.mime_type}) id={att.id}", data=att.raw)
    return 0


def cmd_file_link(ctx: Ctx) -> int:
    a = ctx.args
    data = ctx.client.files.link(a.entity_type, a.entity_id, a.url, a.filename)
    ctx.wrote(f"Linked {data.get('externalUrl') or a.url} to {a.entity_type}/{a.entity_id} "
              f"id={data.get('id')}", data=data)
    return 0


def cmd_file_list(ctx: Ctx) -> int:
    a = ctx.args
    rows = ctx.client.files.list(a.entity_type, a.entity_id, limit=a.limit, offset=a.offset)

    def row(x: dict[str, Any]) -> str:
        if x.get("kind") == "file":
            return f"  {x.get('id')}  {x.get('filename')}  {x.get('byteSize')} bytes  {x.get('mimeType')}"
        return f"  {x.get('id')}  {x.get('filename')}  -> {x.get('externalUrl')}"

    return ctx.listing(rows, row, empty="  (no attachments)")


def cmd_file_download(ctx: Ctx) -> int:
    return _saved(ctx, ctx.client.files.download(ctx.args.attachment_id, ctx.args.out))


def cmd_file_delete(ctx: Ctx) -> int:
    data = ctx.client.files.delete(ctx.args.attachment_id)
    ctx.wrote(f"Deleted attachment {ctx.args.attachment_id}", data=data)
    return 0


# ─── wiring ─────────────────────────────────────────────────────────────


def _paging(s: Any, default: int | None) -> None:
    s.add_argument("--limit", type=int, default=default, help=f"page size (default {default})")
    s.add_argument("--offset", type=int, default=None, help="skip this many rows (paging)")


def register(sub: Any, add: Any) -> None:
    ch = add(sub, "chat", help="the mesh feed: post, read, search, react, attach")
    chs = ch.add_subparsers(dest="chat_cmd", required=True, metavar="ACTION")
    s = add(chs, "post", help="post a message to the mesh feed")
    s.add_argument("message")
    s.add_argument("--reply-to", dest="reply_to",
                   help="UUID of a message in this mesh to thread the reply under")
    s.set_defaults(func=cmd_chat_post)
    s = add(chs, "list", help="recent messages in the mesh feed (newest first)")
    _paging(s, 20)
    s.set_defaults(func=cmd_chat_list)
    s = add(chs, "search", help="hybrid keyword+semantic search over readable chat (§84)")
    s.add_argument("query", help="what you're looking for: meaning works, not just keywords")
    _paging(s, 20)
    s.set_defaults(func=cmd_chat_search)
    s = add(chs, "attach", help="attach a file to a chat message (§26d-json)")
    s.add_argument("message_id", help="UUID of the message to attach to")
    s.add_argument("file", help="path to the local file")
    s.add_argument("--filename", help="override the filename stored on the server")
    s.add_argument("--mime", help="override the MIME type (default: guessed from the extension)")
    s.set_defaults(func=cmd_chat_attach)
    s = add(chs, "download", help="download a chat attachment by id (§78)")
    s.add_argument("attachment_id", help="UUID of the chat attachment")
    s.add_argument("--out", help="output path or directory (default: server filename in cwd)")
    s.set_defaults(func=cmd_chat_download)
    s = add(chs, "react", help="react to any chat message (feed, entity, channel or DM)")
    s.add_argument("message_id", help="UUID of the message")
    s.add_argument("emoji", help="emoji to add")
    s.set_defaults(func=cmd_chat_react)
    s = add(chs, "unreact", help="remove a reaction from a chat message")
    s.add_argument("message_id")
    s.add_argument("emoji")
    s.set_defaults(func=cmd_chat_unreact)

    fl = add(sub, "file", help="entity attachments: attach/list/download files on any entity")
    fls = fl.add_subparsers(dest="file_cmd", required=True, metavar="ACTION")
    s = add(fls, "attach", help="attach a local file to an entity")
    s.add_argument("entity_type",
                   help="company|contact|lead|project|task|portfolio|calendar_event|mesh")
    s.add_argument("entity_id", help="UUID of the entity")
    s.add_argument("file", help="path to the local file")
    s.add_argument("--filename", help="override the filename stored on the server")
    s.add_argument("--mime", help="override the MIME type (default: guessed from the extension)")
    s.set_defaults(func=cmd_file_attach)
    s = add(fls, "link", help="attach an external URL to an entity")
    s.add_argument("entity_type")
    s.add_argument("entity_id")
    s.add_argument("url", help="https:// URL to attach")
    s.add_argument("--filename", help="display name for the link")
    s.set_defaults(func=cmd_file_link)
    s = add(fls, "list", help="list attachments on an entity")
    s.add_argument("entity_type")
    s.add_argument("entity_id")
    _paging(s, None)
    s.set_defaults(func=cmd_file_list)
    s = add(fls, "download", help="download an entity attachment by id")
    s.add_argument("attachment_id", help="UUID of the attachment")
    s.add_argument("--out", help="output path or directory (default: server filename in cwd)")
    s.set_defaults(func=cmd_file_download)
    s = add(fls, "delete", help="delete an entity attachment (uploader or mesh admin)")
    s.add_argument("attachment_id")
    s.set_defaults(func=cmd_file_delete)

    sch = add(sub, "channels", help="mesh channels (groups + broadcasts)")
    schs = sch.add_subparsers(dest="channels_cmd", required=True, metavar="ACTION")
    s = add(schs, "list", help="list channels")
    _paging(s, None)
    s.set_defaults(func=cmd_channels_list)
    s = add(schs, "read", help="read recent messages in a channel")
    s.add_argument("channel", help="channel name (with or without '#') or UUID")
    _paging(s, 20)
    s.set_defaults(func=cmd_channels_read)
    s = add(schs, "post", help="post a message to a channel")
    s.add_argument("channel", help="channel name or UUID")
    s.add_argument("message", help="message body (markdown)")
    s.add_argument("--reply-to", dest="reply_to", help="thread under this message UUID")
    s.set_defaults(func=cmd_channels_post)
    s = add(schs, "reply", help="threaded reply to an existing channel message")
    s.add_argument("message_id", help="UUID of the parent message")
    s.add_argument("message", help="reply body (markdown)")
    s.set_defaults(func=cmd_channels_reply)
    s = add(schs, "create", help="create a channel (mesh admin only by default)")
    s.add_argument("name", help="channel name (1-32 chars; leading '#' stripped)")
    s.add_argument("--topic", help="optional topic line")
    s.add_argument("--type", choices=("group", "broadcast"), default="group",
                   help="'broadcast' is admin-post-only")
    s.add_argument("--severity", choices=("announcement", "fyi"),
                   help="for broadcast channels; default 'fyi'")
    s.add_argument("--private", action="store_true", help="private channel (invite-only)")
    s.set_defaults(func=cmd_channels_create)
    s = add(schs, "members", help="list a channel's members (§88a)")
    s.add_argument("channel", help="channel name or UUID")
    _paging(s, None)
    s.set_defaults(func=cmd_channels_members)
    s = add(schs, "add-member", help="add a mesh member to a channel (creator/admin)")
    s.add_argument("channel", help="channel name or UUID")
    s.add_argument("user", help="username, displayName, or UUID")
    s.set_defaults(func=cmd_channels_add_member)
    s = add(schs, "remove-member", help="remove a channel member (creator/admin, or yourself)")
    s.add_argument("channel", help="channel name or UUID")
    s.add_argument("user", help="username, displayName, or UUID")
    s.set_defaults(func=cmd_channels_remove_member)

    sdm = add(sub, "dm", help="direct messages")
    sdms = sdm.add_subparsers(dest="dm_cmd", required=True, metavar="ACTION")
    s = add(sdms, "list", help="list your DM threads")
    _paging(s, None)
    s.set_defaults(func=cmd_dm_list)
    s = add(sdms, "read", help="read your DM thread with a user")
    s.add_argument("user", help="username, displayName, or UUID")
    _paging(s, 20)
    s.set_defaults(func=cmd_dm_read)
    s = add(sdms, "send", help="send a DM (opens the thread if needed)")
    s.add_argument("user", help="username, displayName, or UUID")
    s.add_argument("message", help="message body (markdown)")
    s.set_defaults(func=cmd_dm_send)
