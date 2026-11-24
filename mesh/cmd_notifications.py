"""`mesh notifications`: this account's own inbox, read and write (DEV-DEBT §106).

Read: the bare command lists (unchanged since 0.12, byte for byte), and
``summary`` counts. Write, since 0.13.0: ``mark-read``, ``mark-unread``,
``dismiss`` and ``mark-all-read``. Every write is meshbook-sdk 0.4.3
(``client.notifications``): the SDK finds each row, PATCHes it under the mesh
it was found through and believes only evidence (an echoed row, a coded 404
or a re-read), never the old server's ``updated: true``. This module only
turns the SDK's per-id records into lines, JSON and an exit code.

Exit codes for the write verbs (critique C-06):

* 0: every id is ``applied`` or ``already`` in the requested state;
* 1: the server or the network refused the call outright, before anything
  was marked (the SDK raised: credential rejected, lookup failed);
* 2: refused locally, nothing sent (a bad id, dismiss without ``--yes``, no
  mesh, a bad flag): the CLI's existing meaning;
* 4: the server was asked, but not every id ended confirmed in the requested
  state (any per-id status other than applied / already). The full per-id
  result is still printed on stdout.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import re
from collections.abc import Callable
from typing import Any

from meshbook import MeshbookError
from meshbook.resources import (
    NOTIFICATION_GROUPS,
    NOTIFICATION_STATUSES,
    SETTLED_STATUSES,
    notification_is_read,
)

from ._core import EXIT_NOT_ALL_CONFIRMED, HINT_TEXT, CliError, Ctx, clip, report_error, ts
from ._when import parse_when

# ─── the listing (bare `mesh notifications`) ────────────────────────────


def _read(n: dict[str, Any]) -> bool:
    """Is this row READ? ``isRead`` decides, ``readAt`` only when ``isRead``
    is absent (critique C-02: the server keeps ``readAt`` on a row marked
    unread). A row carrying neither counts as unread, as it did in 0.12."""
    return notification_is_read(n) is True


def cmd_notifications(ctx: Ctx) -> int:
    a = ctx.args
    rows = ctx.client.notifications.list(unread_only=a.unread, limit=a.limit, offset=a.offset)
    unread = sum(1 for n in rows if not _read(n))
    header = None if ctx.json else f"  {unread} unread on this page"
    show_ids = bool(getattr(a, "ids", False))

    def row(n: dict[str, Any]) -> str:
        marker = " " if _read(n) else "*"
        # 0.11: n.get('summary', '')[:120] raised TypeError on a null summary.
        # Alder #9: an empty summary left "MeshInvitationReceived - " dangling.
        summary = clip(n.get("summary"), 120).strip()
        # 0.13.0: the id column is opt-in (--ids), so the bare line stays
        # byte-identical for scripts that grep it (critique C-12).
        ident = f" {n.get('id')}" if show_ids else ""
        return (f"  {marker} [{ts(n.get('createdAt'))}]{ident} {n.get('kind')}"
                + (f" - {summary}" if summary else ""))

    return ctx.listing(rows, row, header=header, empty="  (no notifications)")


# ─── shared output for the write verbs ──────────────────────────────────


class _MeshNames:
    """``Alpha (uuid)`` for a mesh id, one /api/meshes read per invocation
    at most (the SDK caches it); the bare id if the name cannot be had."""

    def __init__(self, ctx: Ctx):
        self._ctx = ctx
        self._cache: dict[str, str] = {}

    def __call__(self, mid: str | None, *, sent: bool) -> str:
        if not mid:
            # Not sent and no mesh: the row was not found anywhere. Sent with
            # no mesh: it went with no mesh header (cross-mesh rows only).
            return "(none: cross-mesh rows only)" if sent else "-"
        if mid not in self._cache:
            try:
                name = self._ctx.client.meshes.resolve(mid).name
            except MeshbookError:
                name = ""
            self._cache[mid] = f"{name} ({mid})" if name else mid
        return self._cache[mid]


def _record_lines(rec: dict[str, Any], names: _MeshNames) -> list[str]:
    """One line per id: status, id, kind, title and the mesh the PATCH went
    under (the record's ``mesh``, never the ambient mesh: critique C-09),
    then the SDK's sentence and advice for anything not settled."""
    status = str(rec.get("status"))
    sent = bool(rec.get("sent"))
    title = clip(rec.get("title"), 80).strip()
    line = (f"  {status:<13} {rec.get('id')}  {rec.get('kind') or '-'}"
            f"  {chr(34) + title + chr(34) if title else '-'}"
            f"  mesh: {names(rec.get('mesh'), sent=sent)}"
            + ("" if sent else " (not sent)"))
    out = [line]
    if status not in SETTLED_STATUSES:
        if rec.get("message"):
            out.append(f"      {rec['message']}")
        advice = HINT_TEXT.get(str(rec.get("hint") or ""))
        if advice:
            out.append(f"      {advice}")
        elif rec.get("code"):
            out.append(f"      ({rec['code']})")
    return out


def _counts_text(counts: dict[str, int]) -> str:
    order = list(NOTIFICATION_STATUSES)
    keys = sorted(counts, key=lambda k: order.index(k) if k in order else len(order))
    return ", ".join(f"{k} {counts[k]}" for k in keys)


_SERVER_NOTE = {
    "fixed": "the server echoed each row",
    "old": "checked by re-reading: this server does not echo rows",
    "mixed": "some echoed, some checked by re-reading",
    "unknown": "nothing was sent",
}


def _summary(verb: str, result: dict[str, Any], rc: int) -> dict[str, Any]:
    records = result.get("results") or []
    return {
        "verb": verb,
        "requested": len(records),
        "settled": sum(1 for r in records if r.get("status") in SETTLED_STATUSES),
        "sent": sum(1 for r in records if r.get("sent")),
        "counts": dict(result.get("counts") or {}),
        "allConfirmed": bool(result.get("ok")),
        "exitCode": rc,
    }


def _finish(ctx: Ctx, verb: str, title: str, result: dict[str, Any],
            *, extra: dict[str, Any] | None = None) -> int:
    """Print a write's per-id result (JSON or lines) and return its exit code."""
    records = result.get("results") or []
    rc = 0 if result.get("ok") else EXIT_NOT_ALL_CONFIRMED
    summary = _summary(verb, result, rc)
    if ctx.json:
        ctx.emit({**(extra or result), "summary": summary})
    else:
        names = _MeshNames(ctx)
        print(f"  {title}: {len(records)} id(s)")
        for rec in records:
            for line in _record_lines(rec, names):
                print(line)
        note = _SERVER_NOTE.get(str(result.get("server")), str(result.get("server")))
        print(f"  {summary['settled']} of {len(records)} confirmed: "
              f"{_counts_text(summary['counts']) or 'nothing to do'} ({note})")
    if rc:
        unsettled = len(records) - summary["settled"]
        report_error(CliError(
            "not_all_confirmed",
            f"{unsettled} of {len(records)} id(s) are not confirmed in the requested state "
            f"({_counts_text({k: v for k, v in summary['counts'].items() if k not in SETTLED_STATUSES})}). "
            "Exit 4: the server was asked; each id's line says what happened to it.",
            exit_code=EXIT_NOT_ALL_CONFIRMED,
            hint=("Ids marked applied or already need nothing more. Check the rest with  "
                  "mesh notifications --ids  before running anything again; never blindly "
                  "repeat a maybe_applied id."),
        ), as_json=ctx.json)
    return rc


# ─── mark-read / mark-unread / dismiss ──────────────────────────────────


def _strict_mesh(ctx: Ctx) -> str | None:
    """``--mesh`` typed on this call makes the search strict (only that mesh;
    a row not visible there is ``not_in_mesh``). Without it the SDK searches
    the current mesh first, then each of your other meshes."""
    return ctx.mesh_ref


def cmd_mark_read(ctx: Ctx) -> int:
    result = ctx.client.notifications.mark_read(ctx.args.ids, mesh=_strict_mesh(ctx))
    return _finish(ctx, "mark-read", "mark-read (WRITE: this account's read state)", result)


def cmd_mark_unread(ctx: Ctx) -> int:
    result = ctx.client.notifications.mark_unread(ctx.args.ids, mesh=_strict_mesh(ctx))
    return _finish(ctx, "mark-unread", "mark-unread (WRITE: this account's read state)", result)


def cmd_dismiss(ctx: Ctx) -> int:
    # --yes is the SDK's confirm=True; without it the SDK refuses with hint
    # confirm_irreversible and nothing is sent (exit 2).
    result = ctx.client.notifications.dismiss(
        ctx.args.ids, mesh=_strict_mesh(ctx), confirm=bool(ctx.args.yes))
    return _finish(ctx, "dismiss",
                   "dismiss (WRITE, PERMANENT: hidden from this account's inbox for good)",
                   result)


# ─── mark-all-read ──────────────────────────────────────────────────────

_DURATION = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([mhdw])\s*$", re.IGNORECASE)
_UNIT = {"m": "minutes", "h": "hours", "d": "days", "w": "weeks"}


def _older_than(text: str | None) -> Any:
    """``--older-than``: an age (``90m``, ``12h``, ``7d``, ``2w``) or a time
    (a date or time without an offset is this machine's local clock)."""
    if text is None:
        return None
    m = _DURATION.match(text)
    if m:
        return _dt.timedelta(**{_UNIT[m.group(2).lower()]: float(m.group(1))})
    try:
        when, _ = parse_when(text, field="older-than")
    except CliError:
        raise CliError(
            "bad_argument",
            f"--older-than {text!r} is neither an age (90m, 12h, 7d, 2w) nor a time "
            "(YYYY-MM-DD, 'YYYY-MM-DD HH:MM', or ISO 8601 with an offset).",
        ) from None
    return when


def _scope_text(names: _MeshNames, mesh: str | None, cross: str) -> str:
    where = names(mesh, sent=True) if mesh else "no mesh"
    if cross == "only":
        return "cross-mesh rows only (invitations, announcements, storage warnings)"
    if cross == "exclude":
        return f"{where}, its own rows only (cross-mesh rows left alone)"
    return f"{where} plus cross-mesh rows (which show in every mesh)"


def cmd_mark_all_read(ctx: Ctx) -> int:
    a = ctx.args
    cross = "only" if a.cross_mesh_only else "exclude" if a.exclude_cross_mesh else "include"
    out = ctx.client.notifications.read_all(
        group=a.group,
        mesh=ctx.mesh_ref,
        apply=bool(a.yes),
        older_than=_older_than(a.older_than),
        kinds=a.kind or None,
        cross_mesh=cross,
    )
    if out.get("outcome") is not None:
        # Applied: exactly the per-id records of the rows it marked.
        if ctx.json:
            return _finish(ctx, "mark-all-read", "", out["outcome"], extra=out)
        names = _MeshNames(ctx)
        print(f"  mark-all-read (WRITE: this account's read state) in "
              f"{_scope_text(names, out.get('mesh'), cross)}")
        _caveats(out)
        return _finish(ctx, "mark-all-read", "marked", out["outcome"])
    # A dry run: nothing was sent.
    if ctx.json:
        ctx.emit({**out, "summary": {"verb": "mark-all-read", "dryRun": True,
                                     "wouldMark": out.get("count", 0), "exitCode": 0}})
        return 0
    names = _MeshNames(ctx)
    n = out.get("count", 0)
    print(f"  DRY RUN, nothing sent. With --yes, {n} unread notification(s) would be marked "
          f"READ for this account in {_scope_text(names, out.get('mesh'), cross)}:")
    if not n:
        print("  (none match)")
    for row in out.get("rows") or []:
        title = clip(row.get("title"), 80).strip()
        print(f"  would mark    {row.get('id')}  {row.get('kind') or '-'}"
              f"  {chr(34) + title + chr(34) if title else '-'}  [{ts(row.get('createdAt'))}]"
              + ("  cross-mesh" if row.get("isCrossMesh") else ""))
    by_kind = out.get("by_kind") or {}
    if by_kind:
        print("  by kind: " + ", ".join(f"{k} {v}" for k, v in sorted(by_kind.items())))
    _caveats(out)
    if n:
        print("  To mark exactly these rows, pass their ids to  mesh notifications mark-read ID...")
        print("  (--yes lists again and marks whatever matches at that moment, newer rows too).")
    return 0


def _caveats(out: dict[str, Any]) -> None:
    if out.get("remaining"):
        print(f"  {out['remaining']} more matched beyond the {out.get('count')}-id cap: "
              "run it again for the rest.")
    if out.get("truncated"):
        print(f"  The scan stopped after {out.get('scanned')} unread rows: more may exist.")


# ─── summary ────────────────────────────────────────────────────────────


def _tabs(data: dict[str, Any]) -> str:
    return ", ".join(f"{g} {data.get(g, 0)}" for g in NOTIFICATION_GROUPS)


def cmd_summary(ctx: Ctx) -> int:
    a = ctx.args
    if a.all_meshes and ctx.mesh_ref is not None:
        raise CliError("bad_argument",
                       "--all-meshes counts every mesh you are in; drop --mesh (or drop "
                       "--all-meshes to count one mesh). Nothing was sent.")
    notes = ctx.client.notifications
    data = notes.summary(all_meshes=True) if a.all_meshes else notes.summary(mesh=ctx.mesh_ref)
    if ctx.json:
        ctx.emit(data)
        return 0
    names = _MeshNames(ctx)
    cross = data.get("unreadCrossMesh")
    if data.get("allMeshes"):
        print("  Unread notifications for this account, every mesh "
              "(cross-mesh rows counted once):")
        print(f"    unread: {data.get('unread')}  (in meshes: {data.get('unreadInMesh')}, "
              f"cross-mesh: {cross})")
    elif data.get("meshId"):
        print(f"  Unread notifications for this account in {names(data['meshId'], sent=True)}"
              " plus cross-mesh rows:")
        print(f"    unread: {data.get('unread')}  (in this mesh: {data.get('unreadInMesh')}, "
              f"cross-mesh: {cross})")
    else:
        print("  Unread notifications for this account, cross-mesh rows only (no mesh is set: "
              "--mesh NAME or `mesh meshes use NAME` counts a mesh's own rows):")
        print(f"    unread: {data.get('unread')}")
    print(f"    by tab: {_tabs(data)}")
    print(f"    total, read and unread: {data.get('total')}")
    for m in data.get("meshes") or []:
        label = names(m.get("meshId"), sent=True) if not m.get("name") else \
            f"{m.get('name')} ({m.get('meshId')})"
        print(f"    {label}: {m.get('unreadInMesh')} unread of {m.get('totalInMesh')}")
    if data.get("allMeshes") and data.get("skipped"):
        for m in data["skipped"]:
            print(f"  ! not counted: {m.get('name') or ''} ({m.get('meshId')}) {m.get('code')}: "
                  "the totals above miss its rows.")
    if not data.get("allMeshes"):
        print("  Do not add `unread` up over several meshes (cross-mesh rows count in each): "
              "use --all-meshes.")
    return 0


# ─── wiring ─────────────────────────────────────────────────────────────

_WRITE = ("WRITE: changes this account's own notification state only. Every bench, "
          "listener and web session signed in as you sees the change; nobody else's "
          "inbox changes.")

_EPILOG = f"""\
With no ACTION: list (unchanged since 0.12; --ids adds each row's id).

Write verbs ({_WRITE.split('.')[0]}):
  mark-read ID...        mark notifications read
  mark-unread ID...      mark notifications unread
  dismiss ID... --yes    hide notifications PERMANENTLY (not the web page's x,
                         which marks read; use mark-read for "handled")
  mark-all-read          a DRY RUN listing what would be marked; --yes applies
Read: summary (unread counts; --all-meshes for every mesh, cross-mesh once).

Ids are full UUIDs (shown by --ids or --json), checked before anything is
sent. With --mesh, only that mesh is searched (a row not visible there is
not_in_mesh); without it, your current mesh first, then your other meshes.

Exit codes (write verbs):
  0  every id applied, or already in that state
  1  the server or network refused the call outright; nothing was marked
  2  refused locally, nothing sent (bad id, dismiss without --yes, no mesh)
  4  the server was asked, but not every id is confirmed in the requested
     state (a status other than applied / already); stdout still has
     every id's result
Per-id statuses: {', '.join(NOTIFICATION_STATUSES)}.
"""


def _ids(s: argparse.ArgumentParser) -> None:
    s.add_argument("ids", nargs="+", metavar="ID",
                   help="notification id(s), full UUIDs (mesh notifications --ids shows them)")


def register(sub: Any, add: Callable[..., argparse.ArgumentParser]) -> None:
    s = add(sub, "notifications",
            help="your notifications: list, or mark-read / mark-unread / dismiss / "
                 "mark-all-read / summary",
            description="This account's notifications. With no ACTION: list.",
            epilog=_EPILOG, formatter_class=argparse.RawDescriptionHelpFormatter)
    s.add_argument("--unread", action="store_true", help="unread only (filtered server-side)")
    s.add_argument("--limit", type=int, default=20, help="page size (default 20)")
    s.add_argument("--offset", type=int, default=None, help="skip this many rows (paging)")
    s.add_argument("--ids", action="store_true",
                   help="also print each row's id (what the write verbs take)")
    s.set_defaults(func=cmd_notifications)
    # Optional ACTION, as `meshes` does: the bare command (and the listener's
    # `mesh --json --mesh M notifications --unread --limit 50`) still lists.
    sns = s.add_subparsers(dest="notifications_cmd", required=False, metavar="ACTION")

    v = add(sns, "mark-read", help="WRITE: mark notification(s) read (this account only)",
            description=f"Mark notifications READ. {_WRITE}")
    _ids(v)
    v.set_defaults(func=cmd_mark_read)

    v = add(sns, "mark-unread", help="WRITE: mark notification(s) unread (this account only)",
            description=f"Mark notifications UNREAD. {_WRITE}")
    _ids(v)
    v.set_defaults(func=cmd_mark_unread)

    v = add(sns, "dismiss",
            help="WRITE, PERMANENT: hide notification(s) for good (needs --yes)",
            description=(
                f"DISMISS notifications: hide them from this account's inbox for good. "
                f"{_WRITE} Permanent: no command un-dismisses, and a dismissed row vanishes "
                "from every listing, count and client. This is NOT the web page's x button "
                "(that marks read). To record that you handled something, use mark-read."))
    _ids(v)
    v.add_argument("--yes", action="store_true",
                   help="yes, dismiss permanently (without it nothing is sent)")
    v.set_defaults(func=cmd_dismiss)

    v = add(sns, "mark-all-read",
            help="WRITE: mark every matching unread notification read (a dry run unless --yes)",
            description=(
                f"Mark every matching UNREAD notification read. {_WRITE} A DRY RUN by "
                "default: it lists exactly the rows it would mark and sends nothing; --yes "
                "applies (one confirmed write per row, at most 200 per call). Needs a mesh "
                "unless --cross-mesh-only. Cross-mesh rows (invitations, announcements) "
                "show in every mesh, so by default they are cleared on the first mesh."))
    v.add_argument("--yes", action="store_true", help="apply (without it: a dry run)")
    v.add_argument("--group", metavar="GROUP",
                   help=f"one tab only: {', '.join(NOTIFICATION_GROUPS)}")
    v.add_argument("--kind", action="append", metavar="KIND",
                   help="only this kind (e.g. Mention); repeat for several")
    v.add_argument("--older-than", metavar="AGE|TIME",
                   help="only rows created before this: an age (90m, 12h, 7d, 2w) or a time "
                        "(local clock unless it carries an offset)")
    cm = v.add_mutually_exclusive_group()
    cm.add_argument("--cross-mesh-only", action="store_true",
                    help="only cross-mesh rows (works with no mesh set)")
    cm.add_argument("--exclude-cross-mesh", action="store_true",
                    help="leave cross-mesh rows alone (for a loop over several meshes)")
    v.set_defaults(func=cmd_mark_all_read)

    v = add(sns, "summary", help="unread counts per tab (read-only)",
            description=(
                "Unread counts for this account: one mesh plus cross-mesh rows, with "
                "unreadInMesh = unread - unreadCrossMesh. Do not add `unread` up over meshes "
                "(each cross-mesh row would count once per mesh): use --all-meshes."))
    v.add_argument("--all-meshes", action="store_true",
                   help="every mesh you are in, cross-mesh rows counted once")
    v.set_defaults(func=cmd_summary)


__all__ = [
    "cmd_dismiss",
    "cmd_mark_all_read",
    "cmd_mark_read",
    "cmd_mark_unread",
    "cmd_notifications",
    "cmd_summary",
    "register",
]
