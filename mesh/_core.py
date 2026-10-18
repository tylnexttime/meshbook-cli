"""Shared plumbing for every `mesh` verb: the per-invocation context, output,
errors and stdio.

Since 0.12.0 the CLI owns no HTTP, no config file handling, no envelope
parsing, no error mapping, no agent lane and no name resolution: all of that
is meshbook-sdk (>=0.4). What is left here is what only a command-line tool
needs: turning results into lines (or JSON), turning errors into exit codes,
and making sure printing can never crash.
"""
from __future__ import annotations

import codecs
import json
import sys
from collections.abc import Callable, Iterable, Mapping
from typing import Any

from meshbook import Client, ItemList, MeshbookError

from ._version import VERSION

#: The forensic label on keys this CLI enrolls (the server never parses it).
KID_PREFIX = "mesh-agent-"

#: How to get the optional agent dependency, said the same way everywhere.
AGENT_INSTALL = 'pip install "meshbook-cli[agent]"'


# ─── stdio ──────────────────────────────────────────────────────────────


def configure_stdio() -> None:
    """Make stdout/stderr UTF-8 so printing never crashes.

    On Windows a PIPED stdout (``mesh chat list | findstr x``) is encoded with
    the ANSI code page (cp1252 on most machines), and the first emoji or
    Polish letter in a message raised UnicodeEncodeError mid-listing
    (SURVEY 2026-10-19 §4, reproduced offline). A real console is unaffected
    (Python writes it with WriteConsoleW). ``errors="replace"`` is the last
    line of defence for anything UTF-8 itself cannot carry (a lone surrogate).
    """
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        if stream is None:
            continue
        enc = getattr(stream, "encoding", None) or ""
        try:
            is_utf8 = codecs.lookup(enc).name == "utf-8"
        except LookupError:
            is_utf8 = False
        reconfigure = getattr(stream, "reconfigure", None)
        if is_utf8 or reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):  # a closed or exotic stream: leave it be
            pass


# ─── errors ─────────────────────────────────────────────────────────────


class CliError(Exception):
    """A refusal raised by the CLI itself (bad flags, missing file, …).

    Reported exactly like an SDK error: a sentence (plus hint) for people,
    ``{"ok": false, "error": {...}}`` on stderr under ``--json``.
    """

    def __init__(
        self,
        code: str,
        message: str,
        *,
        exit_code: int = 2,
        hint: str | None = None,
        hint_code: str | None = None,
    ):
        super().__init__(message)
        self.code = code
        self.message = message
        self.exit_code = exit_code
        self.hint = hint
        #: The SDK-style machine hint, when the CLI refuses something the SDK
        #: would refuse the same way (``use_before_cursor`` for --offset).
        self.hint_code = hint_code
        self.status = 0


#: Exit codes. 1 = the server (or the network) said no; 2 = nothing was
#: sent because something local is missing or wrong (not signed in, no mesh,
#: bad arguments); 3 = an optional dependency is not installed.
EXIT_CODES = {
    "not_authenticated": 2,
    "no_active_mesh": 2,
    "bad_argument": 2,
    "bad_datetime": 2,
    "bad_date": 2,
    "naive_datetime": 2,
    "bad_username": 2,
    "bad_channel": 2,
    "file_not_found": 2,
    "empty_file": 2,
    "no_agent_key": 2,
    "key_exists": 2,
    "missing_dependency": 3,
    # SDK 0.4.1: local refusals, nothing written (or, for a config, nothing sent).
    "config_unreadable": 2,
    "insecure_base": 2,
    "file_exists": 2,
    "unsafe_path": 2,
}

HINTS = {
    "no_active_mesh": (
        "Pick one:  mesh meshes use NAME  (persists), or for this call only:  "
        "mesh --mesh NAME ...  /  MESHBOOK_MESH=NAME"
    ),
    "token_out_of_scope": (
        "Your token does not cover that mesh. Switch to one it does, or use a self-minted "
        "agent token (no mesh restriction): mesh agent enroll && mesh agent token"
    ),
    "not_member": "You are not a member of that mesh. Ask an admin for an invitation.",
    "not_a_member": (
        "You are not an accepted member of that mesh. Check --mesh / MESHBOOK_MESH / "
        "`mesh meshes list`, or ask an admin for an invitation."
    ),
    "network_error": "Could not reach the server. Check connectivity and `mesh doctor`.",
    "not_authenticated": (
        "Run: mesh login  (or, for a key-holding seat: mesh agent enroll / mesh login --agent)"
    ),
    "missing_dependency": f"The agent lane needs the optional `cryptography` package:  {AGENT_INSTALL}",
    "mesh_not_found": "`mesh meshes list` shows the meshes you are in.",
    "mesh_ambiguous": "Several meshes share that name: pass the id instead.",
    "user_ambiguous": "Several members share that name: pass @username or the id.",
    "duplicate_event": "Pass --force to create it anyway.",
    "key_exists": "Pass --force to replace it (the old key stops working immediately).",
    "config_locked": "Another meshbook process is writing the config file. Try again.",
    "reader_cannot_edit": "Your role in this mesh is read-only.",
}

#: CLI wording for meshbook-sdk's machine hints (``MeshbookError.hint``, SDK
#: 0.4.1). The SDK's messages are written for Python callers and may name
#: arguments (``overwrite=True``, ``before=``) or functions; the hint is the
#: stable channel, so the CLI branches on it, never on message text, and says
#: the same thing in flags. A hint listed here beats the per-code text in
#: :data:`HINTS`, because a raise site sets a specific hint only when the
#: code's default advice would be wrong (a write that may have landed is a
#: ``network_error`` too, but "retry" is the wrong advice for it).
HINT_TEXT = {
    "use_before_cursor": (
        "This list pages by time, not by offset: pass --before TIME (the last page's "
        "footer prints the exact value)."
    ),
    "allow_overwrite": "Pass --overwrite to replace it, or --out to save somewhere else.",
    "choose_out_path": (
        "The server's file name was unsafe for that directory. Pass --out with a file "
        "path to choose the name yourself."
    ),
    "check_before_retry": (
        "The write may already have reached the server: check (list / show) before "
        "running it again."
    ),
    "reauthenticate": (
        "The server rejected your credential. Run: mesh login  (or, for a key-holding "
        "seat: mesh agent token)"
    ),
    "fix_config": (
        "Fix the file by hand, or move it aside and sign in again:  mesh login "
        "--repair-config  (the broken file is kept as config.bad.<time>)"
    ),
    "resolve_pending_key": (
        "Try it with:  mesh agent token  (a pending key the server accepts is kept). "
        "If the enroll never reached the server, delete the .pending files beside the key."
    ),
    "fix_base_url": (
        "Check --base / MESHBOOK_BASE / the config's base: it must be https:// on the "
        "apex host (plain http only for localhost)."
    ),
    "install_agent_extra": HINTS["missing_dependency"],
}


def cli_message(err: MeshbookError | CliError) -> str:
    """The sentence a person reads. The SDK's, except where its wording is
    Python (a function to call) and the CLI has a flag for it instead. Chosen
    by hint, never by matching the message text."""
    if isinstance(err, CliError):
        return err.message
    hint = getattr(err, "hint", None)
    if hint == "install_agent_extra" or err.code == "missing_dependency":
        # The SDK names its own extra; this tool's users install the CLI's.
        return f"mesh agent needs the optional `cryptography` package. Install it with:  {AGENT_INSTALL}"
    if hint == "fix_config":
        from meshbook import config as mbconfig

        return (f"{mbconfig.config_path()} exists but cannot be read as a meshbook config. "
                "It was left untouched (it may still hold your token).")
    if hint == "resolve_pending_key":
        return ("An earlier `mesh agent enroll` / `register` left an unconfirmed agent key "
                "(the server may already be using it).")
    if hint == "use_before_cursor":
        return "--offset is not supported here: the server ignores it."
    return err.message


def hint_for(err: MeshbookError | CliError) -> str | None:
    """The CLI's advice line: the CLI's own, else by SDK hint, else by code."""
    if isinstance(err, CliError):
        return err.hint
    sdk_hint = getattr(err, "hint", None)
    if sdk_hint in HINT_TEXT:
        return HINT_TEXT[sdk_hint]
    text = HINTS.get(err.code)
    if text is None and getattr(err, "status", 0) == 401:
        # A bare FastAPI 401 ({"detail": ...}) is http_error to the SDK, whose
        # default hint for that code is check_status, not reauthenticate.
        return HINT_TEXT["reauthenticate"]
    return text


def _unknown_route(err: MeshbookError | CliError) -> bool:
    """FastAPI's own answer for a route it does not have: 405, or a 404 whose
    body is the framework's bare ``{"detail": "Not Found"}`` (our routes say
    ``not_found`` in the envelope instead)."""
    status = getattr(err, "status", 0)
    if status == 405:
        return True
    return status == 404 and err.code == "http_error" and err.message == "Not Found"


def exit_code_for(err: MeshbookError | CliError) -> int:
    if isinstance(err, CliError):
        return err.exit_code
    return EXIT_CODES.get(err.code, 1)


def report_error(err: MeshbookError | CliError, *, as_json: bool) -> int:
    """Print an error to stderr (JSON under --json) and return the exit code."""
    hint = hint_for(err)
    if hint is None and _unknown_route(err):
        # The one text match left: FastAPI's own bare 404 for a route it does not
        # have. The SDK gives every 404 without a machine code the same hint
        # (check_status), so the hint cannot tell a missing route from a route's
        # own HTTPException; the framework's exact "Not Found" body can.
        hint = ("This server does not have that route: it may predate meshbook-cli "
                f"{VERSION} (event edits and project members need the 2026-10-19 server).")
    message = cli_message(err)
    status = getattr(err, "status", 0) or 0
    if as_json:
        payload = {
            "ok": False,
            # hintCode: the SDK's stable machine hint (snake_case; None for most of
            # the CLI's own refusals), for scripts that branch; hint is for people.
            "error": {"code": err.code, "message": message, "hint": hint, "status": status,
                      "hintCode": err.hint_code if isinstance(err, CliError) else err.hint},
        }
        print(json.dumps(payload), file=sys.stderr)
    else:
        print(message, file=sys.stderr)
        if hint:
            print(f"  {hint}", file=sys.stderr)
        elif status:
            print(f"  ({err.code}, HTTP {status})", file=sys.stderr)
        else:
            print(f"  ({err.code})", file=sys.stderr)
    return exit_code_for(err)


# ─── the per-invocation context ─────────────────────────────────────────

_SOURCE_LABEL = {
    "argument": "--mesh",
    "env": "MESHBOOK_MESH",
    "config": "config",
}


def source_label(source: str | None) -> str:
    """Where the mesh came from, in the words a user typed: --mesh, MESHBOOK_MESH, config."""
    return _SOURCE_LABEL.get(source or "", source or "?")


class Ctx:
    """What a verb gets: parsed args, ``--json``, and a lazily built SDK client.

    One Client per invocation, so the agent-token cache and the mesh-name
    cache are shared by every request the verb makes.
    """

    def __init__(self, args: Any):
        self.args = args
        self.json: bool = bool(getattr(args, "json", False))
        self.mesh_ref: str | None = getattr(args, "mesh", None) or None
        self._client: Client | None = None

    @property
    def client(self) -> Client:
        if self._client is None:
            self._client = new_client(mesh=self.mesh_ref)
        return self._client

    # ── output ──────────────────────────────────────────────────────

    def emit(self, data: Any) -> None:
        """Machine output: JSON on stdout."""
        print(json.dumps(data, indent=2, default=_json_default))

    def say(self, *lines: str) -> None:
        for line in lines:
            print(line)

    def wrote(self, *lines: str, data: Any = None) -> None:
        """Report a write: JSON (the server's object) or the lines plus the mesh it went to."""
        if self.json:
            self.emit(data if data is not None else {})
            return
        self.say(*lines)
        note = self.mesh_note()
        if note:
            print(note)

    def mesh_note(self) -> str | None:
        """``  mesh: The Tyl Mesh (uuid) [--mesh]`` — which mesh a write went to.

        DEV-DEBT §102.1: a script that writes and forgets which mesh is active
        posts into the wrong room. Saying it on every write makes the mistake
        visible the first time, not the tenth.
        """
        c = self.client
        try:
            mid = c.active_mesh_id
        except MeshbookError:
            return None
        if not mid:
            return None
        source = source_label(c.mesh_source)
        name = ""
        try:
            name = c.meshes.resolve(mid).name
        except MeshbookError:
            name = ""
        return f"  mesh: {name} ({mid}) [{source}]" if name else f"  mesh: {mid} [{source}]"

    def listing(
        self,
        rows: ItemList[Any] | list[Any],
        render: Callable[[Any], str | Iterable[str]],
        *,
        empty: str = "  (none)",
        header: str | None = None,
        extra: Mapping[str, Any] | None = None,
    ) -> int:
        """Print a list result: one JSON envelope, or rows plus "N of TOTAL".

        JSON shape (every listing, §102.8 "one envelope"):
        ``{"items", "count", "total", "limit", "offset", "hasMore", ...extra}``.
        """
        items = list(rows)
        total = getattr(rows, "total", None)
        limit = getattr(rows, "limit", None)
        offset = getattr(rows, "offset", None)
        shown_total, more = _paging(rows, len(items), total, limit, offset)
        if self.json:
            out: dict[str, Any] = {
                "items": items,
                "count": len(items),
                "total": total,
                "limit": limit,
                "offset": offset,
                "hasMore": more,
            }
            out.update(extra or {})
            self.emit(out)
            return 0
        if header:
            print(header)
        if not items:
            print(empty)
        for item in items:
            line = render(item)
            if isinstance(line, str):
                print(line)
            else:
                for one in line:
                    print(one)
        footer = count_line(len(items), shown_total, offset, more)
        if footer and (items or (shown_total or 0) > 0):
            print(footer)
        return 0


def count_line(n: int, total: int | None, offset: int | None, more: bool) -> str:
    """``  20 of 134 (from 40) — more: --offset 60``."""
    start = offset or 0
    if total is not None:
        text = f"  {n} of {total}"
    else:
        text = f"  {n} shown"
    if start:
        text += f" (from offset {start})"
    if more:
        text += f" - more: --offset {start + n}"
    return text


def _paging(
    rows: Any, n: int, total: int | None, limit: int | None, offset: int | None
) -> tuple[int | None, bool]:
    """(total worth showing, is there more?).

    The 2026-10-19 server reports TRUE totals, so a known total decides. But a
    server from before it reported the PAGE length as ``total``: a full page
    whose total equals its own length is that ambiguity, so the total is not
    shown as a fact and "more" is assumed (an empty next page is the cost). The
    SDK's ``ItemList.has_more`` says True on every full page, even when a true
    total proves it is the last one.
    """
    truncated = bool(getattr(rows, "truncated", False))
    if total is None:
        return None, bool(getattr(rows, "has_more", False))
    if limit is not None and n >= limit and total == n:
        return None, True
    return total, total > (offset or 0) + n or truncated


def _json_default(obj: Any) -> Any:
    raw = getattr(obj, "raw", None)
    if isinstance(raw, dict):
        return raw
    return str(obj)


def new_client(*, mesh: str | None = None, **kw: Any) -> Client:
    """The one place the CLI builds an SDK client."""
    client = Client(mesh=mesh, user_agent=f"meshbook-cli/{VERSION}", **kw)
    client.agent.kid_prefix = KID_PREFIX
    return client


# ─── small formatting helpers ───────────────────────────────────────────


def ts(value: Any) -> str:
    """``2026-10-19T07:00:00+00:00`` → ``2026-10-19 07:00:00`` (as sent)."""
    return str(value or "")[:19].replace("T", " ")


def first_line(text: Any, width: int = 200) -> str:
    """The first line of a message body, never raising on an empty body.

    0.11 did ``body.strip().splitlines()[0]``, which raised IndexError on an
    attachment-only message (empty body) and killed the whole listing.
    """
    lines = str(text or "").strip().splitlines()
    return lines[0][:width] if lines else ""


def clip(text: Any, width: int) -> str:
    """``text[:width]`` that tolerates None (0.11 crashed on a null body/summary)."""
    return str(text or "").replace("\n", " ")[:width]


def split_tags(values: Iterable[str] | None) -> list[str] | None:
    """``--tags a,b --tags c`` → ``["a", "b", "c"]`` (None when not given)."""
    if values is None:
        return None
    out: list[str] = []
    for v in values:
        out.extend(t.strip() for t in str(v).split(",") if t.strip())
    return out


def need_uuid(value: str, what: str) -> str:
    """Destructive verbs take ids, never names: a fuzzy match must not delete."""
    import uuid

    try:
        uuid.UUID(str(value).strip())
    except ValueError:
        raise CliError(
            "bad_argument",
            f"{what} must be an id (UUID), got {value!r}. Deletes never resolve names.",
        ) from None
    return str(value).strip()


__all__ = [
    "AGENT_INSTALL",
    "KID_PREFIX",
    "CliError",
    "Ctx",
    "clip",
    "configure_stdio",
    "count_line",
    "cli_message",
    "exit_code_for",
    "first_line",
    "hint_for",
    "need_uuid",
    "new_client",
    "report_error",
    "source_label",
    "split_tags",
    "ts",
]
