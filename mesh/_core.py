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

    def __init__(self, code: str, message: str, *, exit_code: int = 2, hint: str | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.exit_code = exit_code
        self.hint = hint
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


def exit_code_for(err: MeshbookError | CliError) -> int:
    if isinstance(err, CliError):
        return err.exit_code
    return EXIT_CODES.get(err.code, 1)


def report_error(err: MeshbookError | CliError, *, as_json: bool) -> int:
    """Print an error to stderr (JSON under --json) and return the exit code."""
    hint = err.hint if isinstance(err, CliError) else HINTS.get(err.code)
    if err.code == "missing_dependency":
        # The SDK names its own extra; this tool's users install the CLI's.
        message = f"mesh agent needs the optional `cryptography` package. Install it with:  {AGENT_INSTALL}"
    else:
        message = err.message
    status = getattr(err, "status", 0) or 0
    if as_json:
        payload = {
            "ok": False,
            "error": {"code": err.code, "message": message, "hint": hint, "status": status},
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
        source = _SOURCE_LABEL.get(c.mesh_source or "", c.mesh_source or "?")
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
        more = _has_more(rows, len(items), total, offset)
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
        footer = count_line(len(items), total, offset, more)
        if footer and (items or (total or 0) > 0):
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


def _has_more(rows: Any, n: int, total: int | None, offset: int | None) -> bool:
    if total is not None:
        return total > (offset or 0) + n or bool(getattr(rows, "truncated", False))
    return bool(getattr(rows, "has_more", False))


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
    "exit_code_for",
    "first_line",
    "need_uuid",
    "new_client",
    "report_error",
    "split_tags",
    "ts",
]
