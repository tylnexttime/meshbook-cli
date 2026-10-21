"""The 0.11 smoke suite, ported to 0.12: argparse wiring plus the wire shape
of every verb it covered, now asserted on the requests the real SDK sends
through a faked transport (tests/_fake.py) instead of on a monkeypatched
private ``_api_call``.

Tests of internals that moved into meshbook-sdk (envelope parsing, error-body
unwrapping, config-dir resolution, Content-Disposition parsing) are kept as
CLI-level behaviour tests where the behaviour is visible from the command line.
"""
from __future__ import annotations

import base64
import datetime as dt
import json

import pytest

from mesh import cli
from tests._fake import (
    CH1,
    E1,
    MESH,
    T1,
    U1,
    Err,
    Resp,
    err,
    ok,
    ok_list,
)
from tests.conftest import read_config, write_config

PID = "cccccccc-1111-0000-0000-000000000001"
SID = "cccccccc-2222-0000-0000-000000000002"
LEAD = "77777777-0000-0000-0000-000000000009"


def _help(capsys, *argv: str) -> str:
    parser = cli.build_parser()
    with pytest.raises(SystemExit) as exc:
        parser.parse_args([*argv, "--help"])
    assert exc.value.code == 0
    return capsys.readouterr().out


def _pyproject_version() -> str:
    import re
    from pathlib import Path

    text = (Path(__file__).resolve().parent.parent / "pyproject.toml").read_text(encoding="utf-8")
    project = text.split("[project]", 1)[1].split("\n[", 1)[0]
    m = re.search(r'^version\s*=\s*"([^"]+)"', project, re.M)
    assert m, "no version in pyproject [project]"
    return m.group(1)


def test_version_constant(run, api):
    """The version must equal pyproject's.

    0.11's test only checked the string's shape, which is how a stale venv
    reporting 0.1.0 passed it. Since 0.12.4 a source checkout reads
    pyproject.toml directly (tests/test_0124.py), so a mismatch here means a
    literal crept back in or _version.py stopped finding the checkout.
    """
    import mesh

    want = _pyproject_version()
    assert cli.VERSION == want
    assert mesh.__version__ == want
    assert run("--version").out.strip() == f"meshbook-cli {want}"
    api.add("GET", "/api/me", ok({"user": {"username": "x"}}))
    run("whoami")
    ua = api.header(api.one("GET", "/api/me"), "User-Agent")
    assert ua.startswith(f"meshbook-cli/{want} meshbook-sdk/")


def test_help_runs(capsys):
    out = _help(capsys)
    for cmd in ("login", "logout", "whoami", "doctor",
                "meshes", "contacts", "chat", "channels", "dm",
                "leads", "tasks", "projects", "companies",
                "custom-fields", "saved-views", "notifications", "events"):
        assert cmd in out


def test_chat_subcommands_present(capsys):
    out = _help(capsys, "chat")
    for sub in ("post", "list", "search", "attach", "react", "unreact"):
        assert sub in out


def test_chat_search_wire_shape(api, run):
    """§84: the hybrid endpoint with q+limit; place labels for channel / feed hits."""
    hits = [
        {"id": "m1", "entityType": "mesh", "entityId": "e1", "authorName": "Electra",
         "preview": "I name myself.", "createdAt": "2026-08-15T09:58:00",
         "channelId": None, "matchedArms": ["semantic"]},
        {"id": "m2", "entityType": "mesh", "entityId": "e1", "authorName": "Rook",
         "preview": "in-channel", "createdAt": "2026-08-15T10:00:00",
         "channelId": "c9", "channelName": "general", "channelType": "group",
         "matchedArms": ["fts_en"]},
    ]
    api.add("GET", "/api/chat/search", ok_list(hits, semantic=True))
    r = run("chat", "search", "who named herself", "--limit", "5")
    assert r.rc == 0, r.err
    q = api.query(api.one("GET", "/api/chat/search"))
    assert q["q"] == "who named herself" and q["limit"] == "5"
    assert "feed" in r.out and "#general" in r.out and "semantic" in r.out


def test_chat_search_needs_active_mesh(run, api):
    write_config({"token": "mb_token_test", "base": "https://mesh.test"})
    r = run("chat", "search", "x")
    assert r.rc == 2
    assert "No active mesh" in r.err
    assert not api.calls("GET", "/api/chat/search")


def test_channels_subcommands_present(capsys):
    out = _help(capsys, "channels")
    for sub in ("list", "read", "post", "reply", "create",
                "members", "add-member", "remove-member"):
        assert sub in out


def test_channels_add_member_wire_shape(api, run):
    """§88a: resolves channel + user by name, POSTs {userId}."""
    api.add("GET", f"/api/meshes/{MESH}/channels", ok_list([{"id": CH1, "name": "leadership"}]))
    api.add("GET", "/api/users", ok_list([{"id": U1, "username": "ember", "displayName": "Ember"}]))
    api.add("POST", f"/api/channels/{CH1}/members", ok_list([]))
    r = run("channels", "add-member", "#leadership", "ember")
    assert r.rc == 0, r.err
    assert api.body(api.one("POST", f"/api/channels/{CH1}/members")) == {"userId": U1}
    assert "Added ember" in r.out


def test_dm_subcommands_present(capsys):
    out = _help(capsys, "dm")
    for sub in ("list", "read", "send"):
        assert sub in out


def test_channels_create_broadcast_severity_required_via_default(api, run):
    """`channels create foo --type broadcast` defaults broadcastSeverity to 'fyi'."""
    api.add("POST", f"/api/meshes/{MESH}/channels",
            ok({"id": CH1, "name": "test-bc", "channelType": "broadcast",
                "broadcastSeverity": "fyi"}))
    r = run("channels", "create", "#test-bc", "--type", "broadcast")
    assert r.rc == 0, r.err
    body = api.body(api.one("POST", f"/api/meshes/{MESH}/channels"))
    assert body["name"] == "test-bc"           # '#' stripped
    assert body["channelType"] == "broadcast"
    assert body["broadcastSeverity"] == "fyi"  # default applied


def test_resolve_channel_strips_hash(api, run):
    """Humans type `#BUGS`; the channel is `bugs` (stripped, case-insensitive)."""
    api.add("GET", f"/api/meshes/{MESH}/channels",
            ok_list([{"id": CH1, "name": "bugs"}, {"id": "ch-2", "name": "general"}]))
    api.add("GET", f"/api/channels/{CH1}/messages", ok_list([]))
    assert run("channels", "read", "#BUGS").rc == 0
    assert api.calls("GET", f"/api/channels/{CH1}/messages")
    r = run("channels", "read", "nope")
    assert r.rc == 1 and "No channel matching" in r.err


@pytest.mark.parametrize("group,subs", [
    ("leads", ("list", "create", "move")),
    ("tasks", ("list", "create", "complete")),
    ("projects", ("list", "create")),
    ("companies", ("list", "create")),
    ("custom-fields", ("list",)),
    ("saved-views", ("list",)),
])
def test_batch2_subcommands_present(capsys, group, subs):
    out = _help(capsys, group)
    for sub in subs:
        assert sub in out, f"{group} missing subcommand {sub}"


def test_items_envelope_shapes(api, run):
    """Bare-list and paginated server shapes both list (was: cli._items)."""
    api.add("GET", "/api/companies", [{"id": "c1", "name": "Bare"}])
    r = run("companies", "list")
    assert r.rc == 0 and "Bare" in r.out
    api.add("GET", "/api/companies", ok_list([{"id": "c2", "name": "Paged"}], total=1))
    r = run("companies", "list")
    assert r.rc == 0 and "Paged" in r.out and "1 of 1" in r.out


def test_leads_create_wire_shape(api, run):
    """Lead create sends camelCase pipelineId/stageId + title (ids pass through)."""
    api.add("POST", "/api/leads", ok({"id": LEAD, "title": "Big deal"}))
    r = run("leads", "create", "--title", "Big deal", "--pipeline", PID, "--stage", SID,
            "--value", "1000")
    assert r.rc == 0, r.err
    assert api.body(api.one("POST", "/api/leads")) == {
        "title": "Big deal", "pipelineId": PID, "stageId": SID, "valueAmount": 1000.0,
    }


def test_tasks_complete_wire_shape(api, run):
    api.add("PATCH", f"/api/tasks/{T1}", ok({"id": T1, "status": "Done"}))
    r = run("tasks", "complete", T1)
    assert r.rc == 0, r.err
    assert api.body(api.one("PATCH", f"/api/tasks/{T1}")) == {"status": "Done"}


def test_leads_move_wire_shape(api, run):
    api.add("POST", f"/api/leads/{LEAD}/move-stage", ok({"id": LEAD}))
    r = run("leads", "move", LEAD, SID)
    assert r.rc == 0, r.err
    assert api.body(api.one("POST", f"/api/leads/{LEAD}/move-stage")) == {"stageId": SID}


def test_help_includes_members(capsys):
    assert "members" in _help(capsys)


@pytest.mark.parametrize("group,subs", [
    ("members", ("invite", "accept", "set-role", "remove", "leave")),
    ("tasks", ("log",)),
    ("saved-views", ("create",)),
])
def test_batch2b_subcommands_present(capsys, group, subs):
    out = _help(capsys, group)
    for sub in subs:
        assert sub in out, f"{group} missing subcommand {sub}"


def test_members_invite_wire_shape(api, run):
    api.add("POST", f"/api/meshes/{MESH}/invite", ok({"invited": True}))
    r = run("members", "invite", "@rook", "--role", "member")
    assert r.rc == 0, r.err
    assert api.body(api.one("POST", f"/api/meshes/{MESH}/invite")) == {
        "username": "rook", "role": "member"}


def test_members_invite_default_role_omitted(api, run):
    api.add("POST", f"/api/meshes/{MESH}/invite", ok({}))
    run("members", "invite", "rook")
    assert api.body(api.one("POST", f"/api/meshes/{MESH}/invite")) == {"username": "rook"}


def test_members_set_role_wire_shape(api, run):
    api.add("GET", "/api/users", ok_list([{"id": U1, "username": "rook"}]))
    api.add("POST", f"/api/meshes/{MESH}/set-role", ok({}))
    r = run("members", "set-role", "rook", "reader")
    assert r.rc == 0, r.err
    assert api.body(api.one("POST", f"/api/meshes/{MESH}/set-role")) == {
        "userId": U1, "role": "reader"}


def test_members_accept_wire_shape(api, run):
    other = "33333333-3333-3333-3333-333333333333"
    api.add("POST", f"/api/meshes/{other}/respond", ok({}))
    r = run("members", "accept", other)
    assert r.rc == 0, r.err
    assert api.body(api.one("POST", f"/api/meshes/{other}/respond")) == {"action": "accept"}


def test_tasks_log_wire_shape_defaults_today(api, run):
    api.add("POST", "/api/task-time-logs", ok({}))
    r = run("tasks", "log", T1, "--hours", "2.5")
    assert r.rc == 0, r.err
    body = api.body(api.one("POST", "/api/task-time-logs"))
    assert body["taskId"] == T1 and body["hours"] == 2.5
    # The machine's own calendar day (0.11 used UTC's: a Sydney morning was yesterday).
    assert body["loggedForDate"] == dt.date.today().isoformat()
    assert "note" not in body


def test_saved_views_create_uses_module_field(api, run):
    """Regression guard: the create body uses `module`, NOT `entityType`."""
    api.add("POST", "/api/saved-views", ok({"id": "sv-1"}))
    r = run("saved-views", "create", "--module", "leads", "--name", "Hot",
            "--filter", '{"stageId":"x"}', "--shared")
    assert r.rc == 0, r.err
    assert api.body(api.one("POST", "/api/saved-views")) == {
        "module": "leads", "name": "Hot", "filterJson": {"stageId": "x"}, "isShared": True}


def test_saved_views_create_bad_filter_json(api, run):
    r = run("saved-views", "create", "--module", "leads", "--name", "X", "--filter", "{not json")
    assert r.rc == 2
    assert not api.calls("POST", "/api/saved-views")


def test_chat_post_reply_to_threads(api, run):
    api.add("POST", f"/api/entities/mesh/{MESH}/chat", ok({"id": "msg-2"}))
    r = run("chat", "post", "re: that", "--reply-to", "msg-1")
    assert r.rc == 0, r.err
    assert api.body(api.one("POST", f"/api/entities/mesh/{MESH}/chat")) == {
        "bodyMd": "re: that", "parentMessageId": "msg-1"}


def test_config_round_trip(api, run, _isolated_env):
    """login writes the config (through meshbook.config), logout removes it."""
    write_config({})
    api.add("GET", "/api/me", ok({"user": {"username": "wren", "displayName": "Wren"}}))
    r = run("login", "--token", "mb_token_fresh", "--base", "https://mesh.test")
    assert r.rc == 0, r.err
    cfg = read_config()
    assert cfg["token"] == "mb_token_fresh" and cfg["base"] == "https://mesh.test"
    assert cfg["schema_version"] == 1          # stamped by the SDK's writer
    assert "mb_token_fresh" not in r.out       # never echoed
    r = run("logout")
    assert r.rc == 0
    assert read_config() == {}


def test_corrupt_config_is_reported_not_read_as_signed_out(api, run, _isolated_env):
    """A garbled config file does not crash the CLI, and nothing is sent.

    0.12.0 (on meshbook-sdk 0.4.0) read it as signed out. Since SDK 0.4.1
    (C-40) a broken file is reported as broken, with its path, because it may
    be a half-written file that still holds the token; the CLI says how to
    repair it in flags (exit 2: a local problem), never in Python."""
    path = _isolated_env / "config"
    path.write_text("{ not valid json", encoding="utf-8")
    r = run("whoami")
    assert r.rc == 2, r.err
    assert str(path) in r.err
    assert "mesh login --repair-config" in r.err
    assert "quarantine_config(" not in r.err          # the SDK's Python wording
    assert not api.requests                            # nothing sent
    assert path.read_text(encoding="utf-8") == "{ not valid json"   # untouched


def test_invalid_token_does_not_persist(api, run):
    """Rook, 2026-05-10: a bad --token must not reach the disk."""
    write_config({})
    api.add("GET", "/api/me", ok({"authenticated": False}))
    r = run("login", "--token", "mb_token_garbage")
    assert r.rc == 1, r.err
    assert "Token rejected" in r.err
    assert read_config() == {}


def test_explicit_meshbook_config_dir_wins(run, api, _isolated_env):
    """MESHBOOK_CONFIG_DIR is where the CLI reads and writes (via the SDK)."""
    api.add("GET", "/api/health", {"ok": True})
    api.add("GET", "/api/me", ok({"user": {"username": "wren"}, "activeMeshId": MESH}))
    r = run("--json", "doctor")
    api_path = str(_isolated_env / "config")
    assert r.json()["configPath"] == api_path


def test_file_subcommands_present(capsys):
    out = _help(capsys, "file")
    for sub in ("attach", "link", "list", "download", "delete"):
        assert sub in out


def test_chat_download_present(capsys):
    assert "download" in _help(capsys, "chat")


def test_file_attach_posts_json_body(api, run, tmp_path):
    """`mesh file attach` hits the §78 JSON endpoint with the base64 body."""
    f = tmp_path / "pic.png"
    f.write_bytes(b"\x89PNG\r\n\x1a\nfake")
    api.add("POST", f"/api/entities/lead/{E1}/attachments/json",
            ok({"id": "att-1", "filename": "pic.png", "byteSize": 12, "mimeType": "image/png"}))
    r = run("file", "attach", "lead", E1, str(f))
    assert r.rc == 0, r.err
    body = api.body(api.one("POST", f"/api/entities/lead/{E1}/attachments/json"))
    assert body["filename"] == "pic.png" and body["mimeType"] == "image/png"
    assert base64.b64decode(body["base64Bytes"]).startswith(b"\x89PNG")


def test_file_download_writes_bytes(api, run, tmp_path):
    api.add("GET", "/api/entity-attachments/att-9/download",
            Resp(b"hello-bytes", {"Content-Disposition": 'attachment; filename="served-name.bin"'}))
    outdir = tmp_path / "dl"
    outdir.mkdir()
    r = run("file", "download", "att-9", "--out", str(outdir))
    assert r.rc == 0, r.err
    assert (outdir / "served-name.bin").read_bytes() == b"hello-bytes"


def test_chat_download_uses_chat_endpoint(api, run, tmp_path):
    api.add("GET", "/api/chat-attachments/att-5/download", Resp(b"x"))
    r = run("chat", "download", "att-5", "--out", str(tmp_path / "f.txt"))
    assert r.rc == 0, r.err
    assert (tmp_path / "f.txt").read_bytes() == b"x"


def test_content_disposition_parsing(api, run, tmp_path):
    """The UTF-8 filename* form wins over the plain fallback."""
    api.add("GET", "/api/entity-attachments/x/download", Resp(b"data", {
        "Content-Disposition":
            "attachment; filename=\"fallback.txt\"; filename*=UTF-8''r%C3%B3%C5%BCa.png"}))
    r = run("file", "download", "x", "--out", str(tmp_path))
    assert r.rc == 0, r.err
    assert (tmp_path / "róża.png").read_bytes() == b"data"


def test_agent_subcommands_present(capsys):
    out = _help(capsys, "agent")
    for sub in ("enroll", "token", "whoami", "status", "revoke", "register"):
        assert sub in out


def test_agent_status_wire(api, run):
    """status maps to GET /api/me/agent-credentials (and sends no mesh header)."""
    api.add("GET", "/api/me/agent-credentials", ok({"enrolled": True, "kid": "mesh-agent-x"}))
    r = run("agent", "status")
    assert r.rc == 0, r.err
    req = api.one("GET", "/api/me/agent-credentials")
    assert api.header(req, "X-Active-Mesh-Id") is None
    assert "mesh-agent-x" in r.out


def test_agent_revoke_wire(api, run):
    api.add("DELETE", "/api/me/agent-credentials", ok({"revoked": True}))
    r = run("agent", "revoke")
    assert r.rc == 0, r.err
    assert api.one("DELETE", "/api/me/agent-credentials")


def test_list_channels_raw_raises_by_default(api, run):
    """An authorisation failure is NOT an empty channel list (Wren, report A4)."""
    api.add("GET", f"/api/meshes/{MESH}/channels", err(403, "token_out_of_scope", "out of scope"))
    r = run("channels", "list")
    assert r.rc == 1
    assert "no channels" not in r.out
    assert "out of scope" in r.err


def test_err_fields_unwraps_fastapi_detail(api, run):
    """FastAPI's {"detail": {...}} yields the real code/message (Wren, report A2)."""
    api.add("GET", "/api/contacts",
            Err(400, {"detail": {"code": "no_active_mesh", "message": "Select a mesh."}}))
    r = run("contacts", "list")
    assert r.rc == 2
    assert "Select a mesh." in r.err
    assert "detail" not in r.err
    assert "mesh meshes use" in r.err   # the hint


def test_members_list_reads_the_detail_endpoint(api, run):
    """Wren, report A6: members could be invited/removed but never listed."""
    api.add("GET", f"/api/meshes/{MESH}/detail", ok({
        "mesh": {"name": "The Tyl Mesh"},
        "members": [{"username": "wren", "displayName": "Wren", "role": "member",
                     "identityType": "ai", "joinedAt": "2026-07-22"}],
        "pendingInvites": [], "pendingRequests": [],
    }))
    r = run("members", "list")
    assert r.rc == 0, r.err
    assert "1 member(s)" in r.out and "@wren" in r.out
    assert json.loads(run("--json", "members", "list").out)["count"] == 1
