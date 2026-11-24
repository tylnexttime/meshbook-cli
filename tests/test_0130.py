"""meshbook-cli 0.13.0: the notification write verbs (meshbook DEV-DEBT §106).

Built on meshbook-sdk 0.4.3 (``client.notifications.mark_read`` /
``mark_unread`` / ``dismiss`` / ``read_all`` / ``summary``). The end-to-end
tests run against ``meshbook.testing.FakeNotificationServer``, the SDK's
stateful fake, in both of its modes: ``old`` (the production server on
2026-11-24, which answers ``updated: true`` whether or not a row matched)
and ``fixed`` (the §106 server branch). Each test that claims a write landed
checks the fake's row, not only the CLI's output.

Critique items (CRITIQUE-meshbook-106.md): C-02 (isRead is the read state),
C-04 (mark-all-read is a dry run by default), C-06 (exit 4), C-07 (the SDK
floor), C-12 (verb names, routing, opt-in --ids), C-13 (dismiss), C-16
(summary), C-20 (ids checked locally, every changed row printed).
"""
from __future__ import annotations

import re
from importlib.metadata import version
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


# ─── the dependency floor: 0.13.0 needs SDK 0.4.3 (C-07) ────────────────


def _sdk_specs() -> list[str]:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    return re.findall(r'"(meshbook-sdk(?:\[agent\])?[^"]*)"', text)


def test_every_sdk_requirement_has_the_043_floor():
    """The write verbs are SDK 0.4.3 surface: with the old floor, pip could
    resolve 0.4.2 and `mesh notifications mark-read` would die with an
    AttributeError. Runtime, [agent] and [dev] all say so."""
    specs = _sdk_specs()
    assert len(specs) == 3, specs
    for spec in specs:
        assert ">=0.4.3,<0.5" in spec, spec


def test_the_cli_docstring_states_the_same_floor():
    text = (ROOT / "mesh" / "cli.py").read_text(encoding="utf-8")
    assert "meshbook-sdk (>=0.4.3,<0.5)" in text


def test_the_installed_sdk_meets_the_043_floor():
    major, minor, patch = (int(x) for x in version("meshbook-sdk").split(".")[:3])
    assert (major, minor) == (0, 4) and patch >= 3


# ─── shared setup ───────────────────────────────────────────────────────

import datetime as dt  # noqa: E402
import json  # noqa: E402

import pytest  # noqa: E402
from meshbook.testing import (  # noqa: E402
    MESH_A,
    MESH_B,
    MESH_C,
    FakeNotificationServer,
)

from mesh import cli  # noqa: E402
from mesh._core import EXIT_CODES, EXIT_NOT_ALL_CONFIRMED, HINT_TEXT  # noqa: E402
from tests._fake import BASE, MESH, ok_list  # noqa: E402
from tests.conftest import write_config  # noqa: E402

MODES = ("old", "fixed")
NOT_A_ROW = "0fffffff-0000-4000-8000-00000000ffff"


@pytest.fixture()
def server(monkeypatch):
    """``server("old")`` -> a FakeNotificationServer installed for this test,
    with the config's active mesh set to MESH_A ("Alpha"). Installed after
    conftest's Router, so it wins; conftest's registry hook still checks
    every request."""

    def make(mode: str = "old", *, active: str | None = MESH_A, **kw):
        cfg = {"token": "mb_token_test", "base": BASE}
        if active:
            cfg["active_mesh_id"] = active
        write_config(cfg)
        return FakeNotificationServer(mode=mode, **kw).install(monkeypatch)

    return make


def _lines_with(out: str, needle: str) -> list[str]:
    return [ln for ln in out.splitlines() if needle in ln]


# ─── the bare listing stays byte-identical (C-12) ───────────────────────

GOLDEN_ROWS = [
    {"id": "0a000000-0000-4000-8000-000000000001", "kind": "Mention",
     "title": "Rook mentioned you", "summary": "Rook mentioned you in #general",
     "isRead": False, "readAt": None, "meshId": MESH, "isCrossMesh": False,
     "createdAt": "2026-11-24T09:05:00+00:00"},
    {"id": "0a000000-0000-4000-8000-000000000002", "kind": "TaskAssigned", "title": "Task",
     "summary": "x" * 150, "isRead": True, "readAt": "2026-11-24T09:10:00+00:00",
     "meshId": MESH, "isCrossMesh": False, "createdAt": "2026-11-24T09:00:00+00:00"},
    {"id": "0a000000-0000-4000-8000-000000000003", "kind": "MeshInvitationReceived",
     "title": "Invite", "summary": "", "isRead": False, "readAt": None,
     "meshId": "22222222-2222-2222-2222-222222222222", "isCrossMesh": True,
     "createdAt": "2026-11-23T22:00:00+00:00"},
    # An older server's rows: no isRead (readAt decides), no meshId.
    {"id": "0a000000-0000-4000-8000-000000000004", "kind": "SystemAnnouncement",
     "summary": None, "readAt": "2026-11-20T01:00:00+00:00",
     "createdAt": "2026-11-20T00:00:00"},
    {"id": "0a000000-0000-4000-8000-000000000005", "kind": "Mention",
     "summary": "line one\nline two", "createdAt": "2026-11-19T00:00:00Z"},
]

#: Captured from meshbook-cli 0.12.4 (main 54bac74) with these rows.
GOLDEN_0124 = (
    "  3 unread on this page\n"
    "  * [2026-11-24 09:05:00] Mention - Rook mentioned you in #general\n"
    "    [2026-11-24 09:00:00] TaskAssigned - " + "x" * 120 + "\n"
    "  * [2026-11-23 22:00:00] MeshInvitationReceived\n"
    "    [2026-11-20 00:00:00] SystemAnnouncement\n"
    "  * [2026-11-19 00:00:00] Mention - line one line two\n"
    "  5 of 12 - more: --offset 5\n"
)


@pytest.mark.parametrize("argv", [
    ("notifications",),
    ("notifications", "--limit", "5"),
    ("notifications", "--unread", "--offset", "5"),
])
def test_the_bare_listing_is_byte_identical_to_0124(api, run, argv):
    api.add("GET", "/api/notifications", ok_list(GOLDEN_ROWS, total=12, limit=5, offset=0))
    r = run(*argv)
    assert r.rc == 0 and r.err == ""
    assert r.out == GOLDEN_0124


def test_the_listeners_json_listing_is_byte_identical_to_0124(api, run):
    """consumer/listener.py runs exactly this and reads items[].id / kind /
    title / meshId / createdAt / actorUserId / bodyMd."""
    api.add("GET", "/api/notifications", ok_list(GOLDEN_ROWS, total=12, limit=5, offset=0))
    r = run("--json", "--mesh", MESH, "notifications", "--unread", "--limit", "50")
    assert r.rc == 0 and r.err == ""
    want = {"items": GOLDEN_ROWS, "count": 5, "total": 12, "limit": 5, "offset": 0,
            "hasMore": True, "paging": "offset"}
    assert r.out == json.dumps(want, indent=2) + "\n"
    q = api.query(api.one("GET", "/api/notifications"))
    assert q["unreadOnly"] == "true" and q["limit"] == "50"


def test_ids_is_opt_in(api, run):
    api.add("GET", "/api/notifications", ok_list(GOLDEN_ROWS[:1]))
    bare = run("notifications").out
    assert GOLDEN_ROWS[0]["id"] not in bare
    with_ids = run("notifications", "--ids").out
    assert ("  * [2026-11-24 09:05:00] 0a000000-0000-4000-8000-000000000001 Mention - "
            "Rook mentioned you in #general") in with_ids.splitlines()


def test_ids_does_not_change_the_json_listing(api, run):
    api.add("GET", "/api/notifications", ok_list(GOLDEN_ROWS))
    assert run("--json", "notifications").out == run("--json", "notifications", "--ids").out


def test_a_row_marked_unread_with_a_stale_readat_counts_as_unread(api, run):
    """C-02: the server keeps readAt when a row is marked unread; isRead decides."""
    api.add("GET", "/api/notifications", ok_list([
        {"id": "n1", "kind": "Mention", "summary": "back to unread", "isRead": False,
         "readAt": "2026-11-24T08:00:00+00:00", "createdAt": "2026-11-24T09:00:00+00:00"},
        {"id": "n2", "kind": "Mention", "summary": "read", "isRead": True, "readAt": None,
         "createdAt": "2026-11-24T08:00:00+00:00"},
    ]))
    out = run("notifications").out.splitlines()
    assert out[0] == "  1 unread on this page"
    assert out[1].startswith("  * [") and "back to unread" in out[1]
    assert out[2].startswith("    [") and out[2].endswith("Mention - read")


# ─── routing: the bare command and the listener's calls (C-12) ──────────


@pytest.mark.parametrize("argv,func,attrs", [
    (["notifications"], "cmd_notifications", {"unread": False, "limit": 20, "ids": False}),
    (["--json", "--mesh", MESH_A, "notifications", "--unread", "--limit", "50"],
     "cmd_notifications", {"unread": True, "limit": 50, "json": True, "mesh": MESH_A}),
    (["--json", "--mesh", MESH_A, "notifications", "--unread", "--limit", "50",
      "--offset", "50"], "cmd_notifications", {"unread": True, "limit": 50, "offset": 50}),
    (["notifications", "--ids"], "cmd_notifications", {"ids": True}),
    (["--json", "whoami"], "cmd_whoami", {}),
    (["--json", "meshes"], "cmd_meshes_list", {}),
    (["--json", "--mesh", MESH_A, "dm", "list", "--limit", "50"], "cmd_dm_list", {}),
    (["--json", "--mesh", MESH_A, "dm", "read", "rook", "--limit", "10"], "cmd_dm_read", {}),
    (["notifications", "mark-read", NOT_A_ROW], "cmd_mark_read", {"ids": [NOT_A_ROW]}),
    (["notifications", "mark-unread", NOT_A_ROW, NOT_A_ROW], "cmd_mark_unread", {}),
    (["notifications", "dismiss", NOT_A_ROW, "--yes"], "cmd_dismiss", {"yes": True}),
    (["notifications", "mark-all-read"], "cmd_mark_all_read", {"yes": False}),
    (["notifications", "summary", "--all-meshes"], "cmd_summary", {"all_meshes": True}),
])
def test_routing(argv, func, attrs):
    args = cli.build_parser().parse_args(argv)
    assert args.func.__name__ == func
    for k, v in attrs.items():
        assert getattr(args, k) == v, (k, getattr(args, k))


def test_the_listener_invocation_lists_and_sends_no_write(server, run):
    fake = server("old")
    nid = fake.add(mesh=MESH_A, title="hello")
    r = run("--json", "--mesh", MESH_A, "notifications", "--unread", "--limit", "50")
    assert r.rc == 0, r.err
    assert [i["id"] for i in r.json()["items"]] == [nid]
    assert not fake.patches and fake.row(nid).is_read is False


def test_viewing_a_notification_by_id_is_a_usage_error_not_a_write(server, run):
    """`dm read` views; `notifications <id>` must never mark anything (C-12)."""
    fake = server("old")
    nid = fake.add(mesh=MESH_A)
    for verb in ((nid,), ("read", nid), ("unread", nid)):
        r = run("notifications", *verb)
        assert r.rc == 2
    assert not fake.log


# ─── mark-read / mark-unread ────────────────────────────────────────────


@pytest.mark.parametrize("mode", MODES)
def test_mark_read_applies_and_prints_the_mesh_it_went_under(server, run, mode):
    fake = server(mode)
    a = fake.add(mesh=MESH_A, title="hello A")
    b = fake.add(mesh=MESH_B, kind="TaskAssigned", title="hello B")
    r = run("notifications", "mark-read", a, b)
    assert r.rc == 0, r.err
    assert fake.row(a).is_read and fake.row(b).is_read
    (la,) = _lines_with(r.out, a)
    assert la.split()[0] == "applied" and "Mention" in la and '"hello A"' in la
    assert la.endswith(f"mesh: Alpha ({MESH_A})")
    (lb,) = _lines_with(r.out, b)
    # The row in Beta went under Beta, not the ambient Alpha (C-09).
    assert lb.split()[0] == "applied" and lb.endswith(f"mesh: Beta ({MESH_B})")
    assert [p.mesh for p in fake.patches] == [MESH_A, MESH_B]
    assert "WRITE" in r.out and r.err == ""


@pytest.mark.parametrize("mode", MODES)
def test_already_read_is_not_sent(server, run, mode):
    fake = server(mode)
    nid = fake.add(mesh=MESH_A, is_read=True)
    r = run("notifications", "mark-read", nid)
    assert r.rc == 0
    assert _lines_with(r.out, nid)[0].split()[0] == "already"
    assert not fake.patches


@pytest.mark.parametrize("mode", MODES)
def test_mark_unread_applies(server, run, mode):
    fake = server(mode)
    nid = fake.add(mesh=MESH_A, is_read=True)
    r = run("notifications", "mark-unread", nid)
    assert r.rc == 0, r.err
    assert fake.row(nid).is_read is False
    assert _lines_with(r.out, nid)[0].split()[0] == "applied"


@pytest.mark.parametrize("mode", MODES)
def test_a_silent_miss_is_exit_4_not_success(server, run, mode):
    """The s106 trap: the server answers a PATCH that changed nothing. The CLI
    must say so (exit 4, unconfirmed), never 'marked'."""
    fake = server(mode)
    nid = fake.add(mesh=MESH_A, title="dropped")
    fake.drop(nid)
    r = run("notifications", "mark-read", nid)
    assert r.rc == EXIT_NOT_ALL_CONFIRMED == 4
    assert fake.row(nid).is_read is False
    assert len(fake.patches) == 1
    assert _lines_with(r.out, nid)[0].split()[0] == "unconfirmed"
    assert "not confirmed" in r.err


@pytest.mark.parametrize("mode", MODES)
def test_a_wrong_mesh_id_is_exit_4_not_in_mesh(server, run, mode):
    fake = server(mode)
    nid = fake.add(mesh=MESH_B)
    r = run("--json", "--mesh", "Alpha", "notifications", "mark-read", nid)
    assert r.rc == 4
    rec = r.json()["results"][0]
    assert rec["status"] == "not_in_mesh" and rec["sent"] is False and rec["mesh"] == MESH_A
    assert not fake.patches and fake.row(nid).is_read is False
    assert r.err_json()["error"]["code"] == "not_all_confirmed"


@pytest.mark.parametrize("mode", MODES)
def test_an_unknown_id_is_exit_4_not_found(server, run, mode):
    fake = server(mode)
    fake.add(mesh=MESH_A)
    r = run("notifications", "mark-read", NOT_A_ROW)
    assert r.rc == 4
    assert _lines_with(r.out, NOT_A_ROW)[0].split()[0] == "not_found"
    assert not fake.patches


@pytest.mark.parametrize("mode", MODES)
def test_a_mixed_batch_is_exit_4_and_reports_every_id(server, run, mode):
    fake = server(mode)
    good = fake.add(mesh=MESH_A)
    bad = fake.add(mesh=MESH_A)
    fake.drop(bad)
    r = run("--json", "notifications", "mark-read", good, bad)
    assert r.rc == 4
    j = r.json()
    assert [x["status"] for x in j["results"]] == ["applied", "unconfirmed"]
    assert j["summary"] == {"verb": "mark-read", "requested": 2, "settled": 1, "sent": 2,
                            "counts": {"applied": 1, "unconfirmed": 1},
                            "allConfirmed": False, "exitCode": 4}
    assert fake.row(good).is_read and not fake.row(bad).is_read


def test_a_read_only_token_is_exit_4_with_the_server_code(server, run):
    fake = server("fixed", readonly=True)
    a, b = fake.add(mesh=MESH_A), fake.add(mesh=MESH_A)
    r = run("--json", "notifications", "mark-read", a, b)
    assert r.rc == 4
    recs = r.json()["results"]
    assert recs[0]["status"] == "error" and recs[0]["code"] == "token_readonly"
    assert recs[1]["status"] == "not_attempted"


def test_a_refused_lookup_is_exit_1_and_marks_nothing(server, run):
    fake = server("old")
    nid = fake.add(mesh=MESH_A)
    fake.fail("GET", route="notifications.list", status=401,
              payload={"ok": False, "error": {"code": "invalid_token", "message": "no"}})
    r = run("--json", "notifications", "mark-read", nid)
    assert r.rc == 1
    assert not fake.patches and fake.row(nid).is_read is False
    assert r.out == ""


@pytest.mark.parametrize("bad", ["0a00", "not-an-id", "0a000000-0000-4000-8000-00000000000g"])
def test_a_bad_id_is_refused_locally_exit_2_nothing_sent(server, run, bad):
    """C-20: a truncated id is a local usage error, not a server 422."""
    fake = server("old")
    good = fake.add(mesh=MESH_A)
    r = run("--json", "notifications", "mark-read", good, bad)
    assert r.rc == 2
    err = r.err_json()["error"]
    assert err["code"] == "bad_argument" and "Nothing was sent" in err["message"]
    assert not fake.log


def test_ids_are_deduplicated_and_case_folded(server, run):
    fake = server("fixed")
    nid = fake.add(mesh=MESH_A)
    r = run("--json", "notifications", "mark-read", nid, nid.upper())
    assert r.rc == 0
    assert [x["id"] for x in r.json()["results"]] == [nid]
    assert len(fake.patches) == 1


# ─── --json shape ───────────────────────────────────────────────────────

RECORD_KEYS = {"id", "status", "sent", "mesh", "kind", "title", "message", "code", "hint"}


@pytest.mark.parametrize("mode", MODES)
def test_the_json_is_the_sdk_result_verbatim_plus_a_summary(server, run, mode):
    fake = server(mode)
    nid = fake.add(mesh=MESH_A, title="hi")
    r = run("--json", "notifications", "mark-read", nid)
    assert r.rc == 0 and r.err == ""
    j = r.json()
    assert set(j) == {"action", "ok", "counts", "results", "server", "searched", "summary"}
    assert j["action"] == "mark_read" and j["ok"] is True
    assert j["server"] == mode
    (rec,) = j["results"]
    assert set(rec) == RECORD_KEYS
    assert rec["status"] == "applied" and rec["sent"] is True and rec["mesh"] == MESH_A
    assert rec["kind"] == "Mention" and rec["title"] == "hi"
    assert j["summary"]["exitCode"] == 0 and j["summary"]["allConfirmed"] is True


# ─── dismiss (C-13) ─────────────────────────────────────────────────────


def test_dismiss_without_yes_is_refused_locally_and_sends_nothing(server, run):
    fake = server("old")
    nid = fake.add(mesh=MESH_A)
    r = run("notifications", "dismiss", nid)
    assert r.rc == 2
    assert "permanently" in r.err and "--yes" in r.err and "confirm=True" not in r.err
    assert not fake.log and fake.row(nid).dismissed_at is None
    err = run("--json", "notifications", "dismiss", nid).err_json()["error"]
    assert err["code"] == "bad_argument" and err["hintCode"] == "confirm_irreversible"
    assert err["hint"] == HINT_TEXT["confirm_irreversible"]


@pytest.mark.parametrize("mode", MODES)
def test_dismiss_with_yes_dismisses_and_says_permanent(server, run, mode):
    fake = server(mode)
    nid = fake.add(mesh=MESH_A, kind="SystemAnnouncement", title="maintenance")
    r = run("notifications", "dismiss", nid, "--yes")
    assert r.rc == 0, r.err
    assert fake.row(nid).dismissed_at is not None
    assert "PERMANENT" in r.out
    line = _lines_with(r.out, nid)[0]
    assert line.split()[0] == "applied" and "SystemAnnouncement" in line
    assert "maintenance" in line


@pytest.mark.parametrize("mode", MODES)
def test_a_dismiss_that_did_not_land_is_exit_4(server, run, mode):
    fake = server(mode)
    nid = fake.add(mesh=MESH_A)
    fake.drop(nid)
    r = run("notifications", "dismiss", nid, "--yes")
    assert r.rc == 4
    assert fake.row(nid).dismissed_at is None


def test_dismiss_help_says_permanent_and_not_the_web_x(run):
    r = run("notifications", "dismiss", "--help")
    text = " ".join(r.out.split())
    assert "Permanent: no command un-dismisses" in text
    assert "NOT the web page's x button" in text and "mark-read" in text


@pytest.mark.parametrize("verb", ["mark-read", "mark-unread", "dismiss", "mark-all-read"])
def test_every_write_verb_says_write_in_its_help(run, verb):
    r = run("notifications", verb, "--help")
    assert r.rc == 0
    text = " ".join(r.out.split())
    assert "WRITE" in text and "this account's own notification state only" in text
    listing = " ".join(run("notifications", "--help").out.split())
    assert verb in listing


def test_the_exit_codes_are_documented(run):
    text = run("notifications", "--help").out
    for line in ("0  every id applied", "1  the server or network refused",
                 "2  refused locally", "4  the server was asked"):
        assert line in text
    assert EXIT_CODES["not_all_confirmed"] == 4


# ─── mark-all-read (C-04) ───────────────────────────────────────────────


@pytest.mark.parametrize("mode", MODES)
def test_mark_all_read_without_yes_changes_nothing(server, run, mode):
    fake = server(mode)
    ids = [fake.add(mesh=MESH_A),
           fake.add(cross_mesh=True, mesh=MESH_C, kind="MeshInvitationReceived")]
    other = fake.add(mesh=MESH_B)
    r = run("notifications", "mark-all-read")
    assert r.rc == 0, r.err
    assert "DRY RUN" in r.out and "nothing sent" in r.out
    for nid in ids:
        assert _lines_with(r.out, nid)[0].split()[:2] == ["would", "mark"]
    assert other not in r.out
    assert not fake.patches
    assert fake.requests("POST") == []
    assert all(not row.is_read for row in fake.rows.values())


def test_mark_all_read_json_dry_run(server, run):
    fake = server("old")
    a = fake.add(mesh=MESH_A)
    b = fake.add(mesh=MESH_A)
    j = run("--json", "notifications", "mark-all-read").json()
    assert j["dry_run"] is True and j["outcome"] is None
    assert j["ids"] == [b, a] and j["count"] == 2 and j["mesh"] == MESH_A
    assert j["summary"] == {"verb": "mark-all-read", "dryRun": True, "wouldMark": 2,
                            "exitCode": 0}
    assert not fake.patches


@pytest.mark.parametrize("mode", MODES)
def test_mark_all_read_yes_marks_exactly_the_matching_rows(server, run, mode):
    fake = server(mode)
    mention = fake.add(mesh=MESH_A, kind="Mention")
    task = fake.add(mesh=MESH_A, kind="TaskAssigned")
    invite = fake.add(cross_mesh=True, mesh=MESH_C, kind="MeshInvitationReceived")
    r = run("--json", "notifications", "mark-all-read", "--yes", "--kind", "Mention")
    assert r.rc == 0, r.err
    j = r.json()
    assert j["dry_run"] is False and j["ids"] == [mention]
    assert [x["status"] for x in j["outcome"]["results"]] == ["applied"]
    assert j["summary"]["exitCode"] == 0
    assert fake.row(mention).is_read
    assert not fake.row(task).is_read and not fake.row(invite).is_read
    assert fake.requests("POST") == []      # never the server's blanket read-all


def test_mark_all_read_filters_group_age_and_cross_mesh(server, run):
    utc = dt.timezone.utc
    fake = server("fixed")
    old = fake.add(mesh=MESH_A, kind="Mention", created_at=dt.datetime(2026, 1, 1, tzinfo=utc))
    new = fake.add(mesh=MESH_A, kind="Mention", created_at=dt.datetime.now(utc))
    invite = fake.add(cross_mesh=True, mesh=MESH_C, kind="MeshInvitationReceived",
                      created_at=dt.datetime(2026, 1, 2, tzinfo=utc))
    j = run("--json", "notifications", "mark-all-read", "--older-than", "7d").json()
    assert set(j["ids"]) == {old, invite}
    j = run("--json", "notifications", "mark-all-read", "--older-than", "7d",
            "--exclude-cross-mesh").json()
    assert j["ids"] == [old] and j["filters"]["cross_mesh"] == "exclude"
    j = run("--json", "notifications", "mark-all-read", "--group", "mentions").json()
    assert set(j["ids"]) == {old, new}
    j = run("--json", "notifications", "mark-all-read", "--cross-mesh-only").json()
    assert j["ids"] == [invite]
    j = run("--json", "notifications", "mark-all-read",
            "--older-than", "2026-01-01T12:00+00:00").json()
    assert j["ids"] == [old]
    assert not fake.patches


def test_mark_all_read_with_a_silent_miss_is_exit_4(server, run):
    fake = server("old")
    ok_row = fake.add(mesh=MESH_A)
    miss = fake.add(mesh=MESH_A)
    fake.drop(miss)
    r = run("notifications", "mark-all-read", "--yes")
    assert r.rc == 4
    assert fake.row(ok_row).is_read and not fake.row(miss).is_read
    assert _lines_with(r.out, miss)[0].split()[0] == "unconfirmed"


def test_mark_all_read_needs_a_mesh_unless_cross_mesh_only(server, run):
    fake = server("old", active=None)
    invite = fake.add(cross_mesh=True, mesh=MESH_C, kind="MeshInvitationReceived")
    r = run("--json", "notifications", "mark-all-read", "--yes")
    assert r.rc == 2 and r.err_json()["error"]["code"] == "no_active_mesh"
    assert not fake.log
    r = run("--json", "notifications", "mark-all-read", "--yes", "--cross-mesh-only")
    assert r.rc == 0, r.err
    assert fake.row(invite).is_read


@pytest.mark.parametrize("argv", [
    ("--group", "invitations"),          # C-03: the server would filter NOTHING
    ("--older-than", "yesterday"),
    ("--cross-mesh-only", "--exclude-cross-mesh"),
])
def test_mark_all_read_bad_flags_are_exit_2_and_send_nothing(server, run, argv):
    fake = server("old")
    fake.add(mesh=MESH_A)
    r = run("notifications", "mark-all-read", "--yes", *argv)
    assert r.rc == 2
    assert not fake.patches and all(not row.is_read for row in fake.rows.values())


# ─── summary (C-16) ─────────────────────────────────────────────────────


def test_summary_one_mesh(server, run):
    fake = server("old")
    fake.add(mesh=MESH_A)
    fake.add(mesh=MESH_A, is_read=True)
    fake.add(mesh=MESH_B)
    fake.add(cross_mesh=True, mesh=MESH_C, kind="MeshInvitationReceived")
    r = run("notifications", "summary")
    assert r.rc == 0, r.err
    assert f"in Alpha ({MESH_A}) plus cross-mesh rows" in r.out
    assert "unread: 2  (in this mesh: 1, cross-mesh: 1)" in r.out
    assert "--all-meshes" in r.out
    j = run("--json", "notifications", "summary").json()
    assert j["unread"] == 2 and j["unreadInMesh"] == 1 and j["unreadCrossMesh"] == 1
    assert j["meshId"] == MESH_A and j["allMeshes"] is False
    assert not fake.patches


def test_summary_all_meshes_counts_cross_mesh_once(server, run):
    fake = server("old")
    fake.add(mesh=MESH_A)
    fake.add(mesh=MESH_B)
    fake.add(mesh=MESH_B)
    fake.add(cross_mesh=True, mesh=MESH_C, kind="MeshInvitationReceived")
    j = run("--json", "notifications", "summary", "--all-meshes").json()
    assert j["unread"] == 4 and j["unreadCrossMesh"] == 1 and j["unreadInMesh"] == 3
    assert j["complete"] is True
    assert {m["meshId"]: m["unreadInMesh"] for m in j["meshes"]} == {
        MESH_A: 1, MESH_B: 2, MESH_C: 0}
    r = run("notifications", "summary", "--all-meshes")
    assert "unread: 4  (in meshes: 3, cross-mesh: 1)" in r.out
    assert f"Beta ({MESH_B}): 2 unread" in r.out


def test_summary_all_meshes_names_a_mesh_it_could_not_count(server, run):
    fake = server("old", scope_meshes={MESH_A})
    fake.add(mesh=MESH_A)
    r = run("notifications", "summary", "--all-meshes")
    assert r.rc == 0
    assert "not counted" in r.out and MESH_B in r.out


def test_summary_all_meshes_with_mesh_is_refused(server, run):
    fake = server("old")
    r = run("--json", "--mesh", "Alpha", "notifications", "summary", "--all-meshes")
    assert r.rc == 2 and r.err_json()["error"]["code"] == "bad_argument"
    assert not fake.log


def test_summary_with_no_mesh_says_cross_mesh_only(server, run):
    fake = server("old", active=None)
    fake.add(cross_mesh=True, mesh=MESH_C, kind="MeshInvitationReceived")
    r = run("notifications", "summary")
    assert r.rc == 0
    assert "cross-mesh rows only" in r.out


# ─── every request is a registered SDK endpoint ─────────────────────────


def test_the_verbs_use_only_registered_routes(server, run):
    """conftest's hook fails any unregistered request; this pins which ones
    the write verbs use (the contract check reads the same set)."""
    fake = server("old")
    nid = fake.add(mesh=MESH_B)
    run("notifications", "mark-read", nid)
    run("notifications", "summary")
    routes = {r.route for r in fake.log}
    assert routes <= {"me", "meshes.list", "notifications.list", "notifications.summary",
                      "notifications.mark"}
    assert "notifications.mark" in routes
