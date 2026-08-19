"""Smoke tests for meshbook-cli — argparse wiring + config persistence.

Network-dependent commands aren't exercised here (the CLI calls
meshbook.org directly via stdlib urllib). For end-to-end coverage,
mint a token and run `mesh doctor` against a real or staging server.
"""
from __future__ import annotations

import os

import pytest

from mesh import cli


def test_version_constant():
    assert cli.VERSION
    # Pep 440 sanity — three dot-separated numeric components.
    parts = cli.VERSION.split(".")
    assert len(parts) >= 2
    assert parts[0].isdigit()


def test_help_runs(capsys):
    """`mesh --help` must exit 0 and mention the top-level commands."""
    parser = cli.build_parser()
    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    for cmd in ("login", "logout", "whoami", "doctor",
                "meshes", "contacts", "chat", "channels", "dm",
                "leads", "tasks", "projects", "companies",
                "custom-fields", "saved-views", "notifications"):
        assert cmd in out


def test_chat_subcommands_present(capsys):
    parser = cli.build_parser()
    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["chat", "--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    for sub in ("post", "list", "search", "attach", "react", "unreact"):
        assert sub in out


def test_chat_search_wire_shape(monkeypatch, capsys):
    """§84 — search hits the hybrid endpoint with q+limit and renders
    place labels for channel / DM / feed / entity hits."""
    captured = {}
    hits = [
        {"id": "m1", "entityType": "mesh", "entityId": "e1", "authorName": "Electra",
         "preview": "I name myself.", "createdAt": "2026-08-15T09:58:00",
         "channelId": None, "channelName": None, "channelType": None,
         "matchedArms": ["semantic"]},
        {"id": "m2", "entityType": "mesh", "entityId": "e1", "authorName": "Rook",
         "preview": "in-channel", "createdAt": "2026-08-15T10:00:00",
         "channelId": "c9", "channelName": "general", "channelType": "group",
         "matchedArms": ["fts_en"]},
    ]
    monkeypatch.setattr(
        cli, "_api_call",
        lambda m, p, *, cfg, params=None, **kw: captured.update(path=p, params=params)
        or {"data": {"items": hits, "total": 2, "semantic": True}},
    )
    args = type("A", (), {"query": "who named herself", "limit": 5, "json": False})()
    rc = cli.cmd_chat_search(args, {"active_mesh_id": "m1"})
    out = capsys.readouterr().out
    assert rc == 0
    assert captured["path"] == "/api/chat/search"
    assert captured["params"] == {"q": "who named herself", "limit": 5}
    assert "feed" in out and "#general" in out and "semantic" in out


def test_chat_search_needs_active_mesh():
    args = type("A", (), {"query": "x", "limit": 5, "json": False})()
    assert cli.cmd_chat_search(args, {}) == 2


def test_channels_subcommands_present(capsys):
    """§31 sweep — channel verbs landed in v0.2.0; §88a member verbs v0.7.0."""
    parser = cli.build_parser()
    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["channels", "--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    for sub in ("list", "read", "post", "reply", "create",
                "members", "add-member", "remove-member"):
        assert sub in out


def test_channels_add_member_wire_shape(monkeypatch, capsys):
    """§88a — resolves channel + user, POSTs {userId}."""
    captured = {}

    def fake_api(method, path, *, cfg, body=None, params=None, **kw):
        if path.endswith("/channels") and method == "GET":
            return {"data": {"items": [{"id": "c-7", "name": "leadership"}]}}
        if path == "/api/users":
            return {"data": {"items": [{"id": "u-9", "username": "ember",
                                        "displayName": "Ember"}]}}
        captured.update(method=method, path=path, body=body)
        return {"data": {}}

    monkeypatch.setattr(cli, "_api_call", fake_api)
    args = type("A", (), {"channel": "#leadership", "user": "ember"})()
    rc = cli.cmd_channels_add_member(args, {"active_mesh_id": "m1", "token": "t"})
    assert rc == 0
    assert captured["method"] == "POST"
    assert captured["path"] == "/api/channels/c-7/members"
    assert captured["body"] == {"userId": "u-9"}
    assert "Added @ember" in capsys.readouterr().out


def test_dm_subcommands_present(capsys):
    """§31 sweep — DM verbs landed in v0.2.0."""
    parser = cli.build_parser()
    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["dm", "--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    for sub in ("list", "read", "send"):
        assert sub in out


def test_channels_create_broadcast_severity_required_via_default(capsys, tmp_path, monkeypatch):
    """`channels create foo --type broadcast` should default broadcastSeverity
    to 'fyi' on the wire — verifies the body shape we send to the server."""
    captured = {}

    def fake_api_call(method, path, *, cfg, body=None, **kw):
        captured["method"] = method
        captured["path"] = path
        captured["body"] = body
        return {"data": {"id": "new-id", "name": "test-bc", "channelType": "broadcast",
                         "broadcastSeverity": "fyi"}}

    monkeypatch.setattr(cli, "_api_call", fake_api_call)
    monkeypatch.setattr(cli, "CONFIG_DIR", tmp_path / ".meshbook")
    monkeypatch.setattr(cli, "CONFIG_PATH", tmp_path / ".meshbook" / "config")

    args = type("A", (), {
        "name": "#test-bc", "topic": None, "type": "broadcast",
        "severity": None, "private": False, "json": False,
    })()
    rc = cli.cmd_channels_create(args, {"active_mesh_id": "mesh-1"})
    assert rc == 0
    assert captured["method"] == "POST"
    assert captured["path"] == "/api/meshes/mesh-1/channels"
    assert captured["body"]["name"] == "test-bc"           # # stripped
    assert captured["body"]["channelType"] == "broadcast"
    assert captured["body"]["broadcastSeverity"] == "fyi"  # default applied


def test_resolve_channel_strips_hash(monkeypatch):
    """Channel resolution must strip a leading '#' (humans type `#bugs`,
    the underlying name is just `bugs`)."""
    monkeypatch.setattr(cli, "_list_channels_raw",
                        lambda cfg, mesh_id=None, swallow_errors=False: [
        {"id": "ch-1", "name": "bugs"},
        {"id": "ch-2", "name": "general"},
    ])
    out = cli._resolve_channel("#bugs", {"active_mesh_id": "m"})
    assert out and out["id"] == "ch-1"
    # Also case-insensitive
    out = cli._resolve_channel("BUGS", {"active_mesh_id": "m"})
    assert out and out["id"] == "ch-1"
    # Missing name
    assert cli._resolve_channel("nope", {"active_mesh_id": "m"}) is None


# ─── §31 batch 2 (v0.3.0) — CRM verbs ──────────────────────────────────


@pytest.mark.parametrize("group,subs", [
    ("leads", ("list", "create", "move")),
    ("tasks", ("list", "create", "complete")),
    ("projects", ("list", "create")),
    ("companies", ("list", "create")),
    ("custom-fields", ("list",)),
    ("saved-views", ("list",)),
])
def test_batch2_subcommands_present(capsys, group, subs):
    parser = cli.build_parser()
    with pytest.raises(SystemExit) as exc:
        parser.parse_args([group, "--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    for sub in subs:
        assert sub in out, f"{group} missing subcommand {sub}"


def test_items_envelope_shapes():
    """_items must normalise bare-list, {data:[...]}, and
    {data:{items:[...]}} into a plain list."""
    assert cli._items([1, 2, 3]) == [1, 2, 3]
    assert cli._items({"data": [1, 2]}) == [1, 2]
    assert cli._items({"data": {"items": [9], "total": 1}}) == [9]
    assert cli._items({"data": None}) == []
    assert cli._items(None) == []


def test_leads_create_wire_shape(monkeypatch):
    """Lead create must send camelCase pipelineId/stageId + title."""
    captured = {}

    def fake_api_call(method, path, *, cfg, body=None, **kw):
        captured.update(method=method, path=path, body=body)
        return {"data": {"id": "lead-1", "title": "Big deal"}}

    monkeypatch.setattr(cli, "_api_call", fake_api_call)
    args = type("A", (), {
        "title": "Big deal", "pipeline": "pid", "stage": "sid",
        "value": 1000.0, "description": None, "json": False,
    })()
    rc = cli.cmd_leads_create(args, {"active_mesh_id": "m"})
    assert rc == 0
    assert captured["method"] == "POST"
    assert captured["path"] == "/api/leads"
    assert captured["body"] == {
        "title": "Big deal", "pipelineId": "pid", "stageId": "sid",
        "valueAmount": 1000.0,
    }


def test_tasks_complete_wire_shape(monkeypatch):
    """tasks complete defaults to PATCH status=Done on /api/tasks/{id}."""
    captured = {}

    def fake_api_call(method, path, *, cfg, body=None, **kw):
        captured.update(method=method, path=path, body=body)
        return {"data": {"id": "t-1", "status": "Done"}}

    monkeypatch.setattr(cli, "_api_call", fake_api_call)
    args = type("A", (), {"task_id": "t-1", "status": "Done", "json": False})()
    rc = cli.cmd_tasks_complete(args, {"active_mesh_id": "m"})
    assert rc == 0
    assert captured["method"] == "PATCH"
    assert captured["path"] == "/api/tasks/t-1"
    assert captured["body"] == {"status": "Done"}


def test_leads_move_wire_shape(monkeypatch):
    captured = {}

    def fake_api_call(method, path, *, cfg, body=None, **kw):
        captured.update(method=method, path=path, body=body)
        return {"data": {"id": "lead-1"}}

    monkeypatch.setattr(cli, "_api_call", fake_api_call)
    args = type("A", (), {"lead_id": "lead-1", "stage": "stage-2", "json": False})()
    rc = cli.cmd_leads_move(args, {"active_mesh_id": "m"})
    assert rc == 0
    assert captured["path"] == "/api/leads/lead-1/move-stage"
    assert captured["body"] == {"stageId": "stage-2"}


# ─── §31 batch 2b — members / task time-logs / saved-view create ───────


def test_help_includes_members(capsys):
    parser = cli.build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["--help"])
    assert "members" in capsys.readouterr().out


@pytest.mark.parametrize("group,subs", [
    ("members", ("invite", "accept", "set-role", "remove", "leave")),
    ("tasks", ("log",)),
    ("saved-views", ("create",)),
])
def test_batch2b_subcommands_present(capsys, group, subs):
    parser = cli.build_parser()
    with pytest.raises(SystemExit) as exc:
        parser.parse_args([group, "--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    for sub in subs:
        assert sub in out, f"{group} missing subcommand {sub}"


def test_members_invite_wire_shape(monkeypatch):
    captured = {}

    def fake_api_call(method, path, *, cfg, body=None, **kw):
        captured.update(method=method, path=path, body=body)
        return {"data": {"invited": True}}

    monkeypatch.setattr(cli, "_api_call", fake_api_call)
    args = type("A", (), {"user": "@rook", "role": "member", "json": False})()
    rc = cli.cmd_members_invite(args, {"active_mesh_id": "m1"})
    assert rc == 0
    assert captured["method"] == "POST"
    assert captured["path"] == "/api/meshes/m1/invite"
    assert captured["body"] == {"username": "rook", "role": "member"}


def test_members_invite_default_role_omitted(monkeypatch):
    captured = {}
    monkeypatch.setattr(cli, "_api_call",
                        lambda m, p, *, cfg, body=None, **kw: captured.update(body=body) or {"data": {}})
    args = type("A", (), {"user": "rook", "role": None, "json": False})()
    cli.cmd_members_invite(args, {"active_mesh_id": "m1"})
    assert captured["body"] == {"username": "rook"}  # no role key when default


def test_members_set_role_wire_shape(monkeypatch):
    captured = {}

    def fake_api_call(method, path, *, cfg, body=None, **kw):
        captured.update(method=method, path=path, body=body)
        return {"data": {}}

    monkeypatch.setattr(cli, "_api_call", fake_api_call)
    monkeypatch.setattr(cli, "_resolve_user", lambda u, cfg: {"id": "u-9"})
    args = type("A", (), {"user": "rook", "role": "reader", "json": False})()
    rc = cli.cmd_members_set_role(args, {"active_mesh_id": "m1"})
    assert rc == 0
    assert captured["path"] == "/api/meshes/m1/set-role"
    assert captured["body"] == {"userId": "u-9", "role": "reader"}


def test_members_accept_wire_shape(monkeypatch):
    captured = {}
    monkeypatch.setattr(cli, "_api_call",
                        lambda m, p, *, cfg, body=None, **kw: captured.update(path=p, body=body) or {"data": {}})
    args = type("A", (), {"mesh": "mesh-7", "decline": False, "json": False})()
    cli.cmd_members_accept(args, {})
    assert captured["path"] == "/api/meshes/mesh-7/respond"
    assert captured["body"] == {"action": "accept"}


def test_tasks_log_wire_shape_defaults_today(monkeypatch):
    captured = {}
    monkeypatch.setattr(cli, "_api_call",
                        lambda m, p, *, cfg, body=None, **kw: captured.update(method=m, path=p, body=body) or {"data": {}})
    args = type("A", (), {"task_id": "t-1", "hours": 2.5, "date": None, "note": None, "json": False})()
    rc = cli.cmd_tasks_log(args, {"active_mesh_id": "m1"})
    assert rc == 0
    assert captured["method"] == "POST"
    assert captured["path"] == "/api/task-time-logs"
    assert captured["body"]["taskId"] == "t-1"
    assert captured["body"]["hours"] == 2.5
    # date defaulted to an ISO YYYY-MM-DD string
    assert len(captured["body"]["loggedForDate"]) == 10 and captured["body"]["loggedForDate"].count("-") == 2
    assert "note" not in captured["body"]


def test_saved_views_create_uses_module_field(monkeypatch):
    """Regression guard: create body uses `module`, NOT `entityType`
    (verified against SavedViewIn). A wrong key would 422 server-side."""
    captured = {}
    monkeypatch.setattr(cli, "_api_call",
                        lambda m, p, *, cfg, body=None, **kw: captured.update(path=p, body=body) or {"data": {"id": "sv-1"}})
    args = type("A", (), {"module": "leads", "name": "Hot", "filter": '{"stageId":"x"}', "shared": True, "json": False})()
    rc = cli.cmd_saved_views_create(args, {"active_mesh_id": "m1"})
    assert rc == 0
    assert captured["path"] == "/api/saved-views"
    assert captured["body"] == {"module": "leads", "name": "Hot",
                                "filterJson": {"stageId": "x"}, "isShared": True}


def test_saved_views_create_bad_filter_json(monkeypatch):
    monkeypatch.setattr(cli, "_api_call", lambda *a, **k: {"data": {}})
    args = type("A", (), {"module": "leads", "name": "X", "filter": "{not json", "shared": False, "json": False})()
    assert cli.cmd_saved_views_create(args, {}) == 2


def test_chat_post_reply_to_threads(monkeypatch):
    captured = {}
    monkeypatch.setattr(cli, "_api_call",
                        lambda m, p, *, cfg, body=None, **kw: captured.update(path=p, body=body) or {"data": {"id": "msg-2"}})
    args = type("A", (), {"message": "re: that", "reply_to": "msg-1", "json": False})()
    rc = cli.cmd_chat_post(args, {"active_mesh_id": "m1"})
    assert rc == 0
    assert captured["path"] == "/api/entities/mesh/m1/chat"
    assert captured["body"] == {"bodyMd": "re: that", "parentMessageId": "msg-1"}


def test_config_round_trip(tmp_path, monkeypatch):
    """Config writes + reads cleanly through load/save/reset."""
    monkeypatch.setattr(cli, "CONFIG_DIR", tmp_path / ".meshbook")
    monkeypatch.setattr(cli, "CONFIG_PATH", tmp_path / ".meshbook" / "config")

    assert cli.load_config() == {}

    payload = {"token": "mb_token_test", "active_mesh_id": "abc-123"}
    cli.save_config(payload)
    assert cli.CONFIG_PATH.exists()

    # POSIX permission tightening — only assert on platforms that have
    # `os.chmod` semantics matching ours. Windows masks 0o600 to 0o666;
    # the call is harmless but the bits differ.
    if os.name == "posix":
        assert (cli.CONFIG_PATH.stat().st_mode & 0o777) == 0o600

    loaded = cli.load_config()
    assert loaded["token"] == "mb_token_test"
    assert loaded["active_mesh_id"] == "abc-123"

    cli.reset_config()
    assert cli.load_config() == {}


def test_corrupt_config_returns_empty(tmp_path, monkeypatch):
    """A garbled config file shouldn't crash the CLI."""
    monkeypatch.setattr(cli, "CONFIG_DIR", tmp_path / ".meshbook")
    monkeypatch.setattr(cli, "CONFIG_PATH", tmp_path / ".meshbook" / "config")

    cli.CONFIG_DIR.mkdir()
    cli.CONFIG_PATH.write_text("{ not valid json")
    assert cli.load_config() == {}


def test_invalid_token_does_not_persist(tmp_path, monkeypatch):
    """Bug Rook flagged 2026-05-10 — an invalid `--token` would write
    to disk before /api/me verification. The fix: verify against an
    in-memory test_cfg first, only persist on success."""
    monkeypatch.setattr(cli, "CONFIG_DIR", tmp_path / ".meshbook")
    monkeypatch.setattr(cli, "CONFIG_PATH", tmp_path / ".meshbook" / "config")

    # Stub out _api_call so it returns the "authenticated=false" shape
    # /api/me actually emits for an invalid bearer.
    def fake_api_call(method, path, *, cfg, **kw):
        return {"data": {"authenticated": False}}

    monkeypatch.setattr(cli, "_api_call", fake_api_call)

    args = type("A", (), {"token": "mb_token_garbage", "base": None})()
    rc = cli.cmd_login(args, {})
    assert rc == 1, "cmd_login should fail on authenticated=false response"
    assert not cli.CONFIG_PATH.exists(), \
        "invalid token must NOT have been persisted to config"


def test_xdg_config_home_honoured(monkeypatch, tmp_path):
    """Defensive — if XDG_CONFIG_HOME is set AND ~/.meshbook doesn't
    exist, the CLI should write under XDG. Legacy ~/.meshbook stays
    canonical for upgrade safety."""
    fake_home = tmp_path / "home"
    fake_xdg = tmp_path / "xdg"
    fake_home.mkdir()
    monkeypatch.setattr(cli.Path, "home", classmethod(lambda cls: fake_home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(fake_xdg))
    monkeypatch.delenv("MESHBOOK_CONFIG_DIR", raising=False)
    resolved = cli._resolve_config_dir()
    # Legacy doesn't exist → XDG wins
    assert resolved == fake_xdg / "meshbook", resolved


def test_legacy_config_dir_takes_precedence(monkeypatch, tmp_path):
    """If `~/.meshbook` already exists from a prior install, keep
    using it — don't silently migrate the user's token away."""
    fake_home = tmp_path / "home"
    legacy = fake_home / ".meshbook"
    legacy.mkdir(parents=True)
    monkeypatch.setattr(cli.Path, "home", classmethod(lambda cls: fake_home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.delenv("MESHBOOK_CONFIG_DIR", raising=False)
    assert cli._resolve_config_dir() == legacy


def test_explicit_meshbook_config_dir_wins(monkeypatch, tmp_path):
    """`MESHBOOK_CONFIG_DIR` overrides everything (Pi users w/
    read-only home directories pin to a writable mount)."""
    explicit = tmp_path / "explicit"
    monkeypatch.setenv("MESHBOOK_CONFIG_DIR", str(explicit))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    assert cli._resolve_config_dir() == explicit


# ─── §78 — file group + chat download (v0.6.0) ─────────────────────────


def test_file_subcommands_present(capsys):
    """§78 — entity-attachment verbs landed in v0.6.0."""
    parser = cli.build_parser()
    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["file", "--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    for sub in ("attach", "link", "list", "download", "delete"):
        assert sub in out


def test_chat_download_present(capsys):
    parser = cli.build_parser()
    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["chat", "--help"])
    assert exc.value.code == 0
    assert "download" in capsys.readouterr().out


def test_file_attach_posts_json_body(monkeypatch, tmp_path):
    """`mesh file attach` must hit the §78 JSON endpoint with the
    base64 body shape the server expects."""
    import base64 as b64

    f = tmp_path / "pic.png"
    f.write_bytes(b"\x89PNG\r\n\x1a\nfake")
    captured = {}

    def fake_api_call(method, path, *, cfg, body=None, **kw):
        captured["method"] = method
        captured["path"] = path
        captured["body"] = body
        return {"data": {"id": "att-1", "filename": "pic.png",
                         "byteSize": 12, "mimeType": "image/png"}}

    monkeypatch.setattr(cli, "_api_call", fake_api_call)
    args = type("A", (), {
        "entity_type": "lead", "entity_id": "e-1", "file": str(f),
        "filename": None, "mime": None, "json": False,
    })()
    rc = cli.cmd_file_attach(args, {"token": "t"})
    assert rc == 0
    assert captured["method"] == "POST"
    assert captured["path"] == "/api/entities/lead/e-1/attachments/json"
    assert captured["body"]["filename"] == "pic.png"
    assert captured["body"]["mimeType"] == "image/png"
    assert b64.b64decode(captured["body"]["base64Bytes"]).startswith(b"\x89PNG")


def test_file_download_writes_bytes(monkeypatch, tmp_path):
    """`mesh file download` writes raw bytes to --out and respects the
    server-provided filename when --out is a directory."""
    def fake_download(path, *, cfg):
        assert path == "/api/entity-attachments/att-9/download"
        return b"hello-bytes", "served-name.bin"

    monkeypatch.setattr(cli, "_api_download", fake_download)
    outdir = tmp_path / "dl"
    outdir.mkdir()
    args = type("A", (), {
        "attachment_id": "att-9", "out": str(outdir), "json": False,
    })()
    rc = cli.cmd_file_download(args, {"token": "t"})
    assert rc == 0
    assert (outdir / "served-name.bin").read_bytes() == b"hello-bytes"


def test_chat_download_uses_chat_endpoint(monkeypatch, tmp_path):
    def fake_download(path, *, cfg):
        assert path == "/api/chat-attachments/att-5/download"
        return b"x", "f.txt"

    monkeypatch.setattr(cli, "_api_download", fake_download)
    args = type("A", (), {
        "attachment_id": "att-5", "out": str(tmp_path / "f.txt"), "json": False,
    })()
    assert cli.cmd_chat_download(args, {"token": "t"}) == 0


def test_content_disposition_parsing(monkeypatch):
    """UTF-8 filename* form wins over the plain fallback."""
    class FakeResp:
        headers = {"Content-Disposition":
                   "attachment; filename=\"fallback.txt\"; filename*=UTF-8''r%C3%B3%C5%BCa.png"}
        def read(self):
            return b"data"
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False

    monkeypatch.setattr(cli.urllib.request, "urlopen", lambda req, timeout=60: FakeResp())
    raw, name = cli._api_download("/api/entity-attachments/x/download",
                                  cfg={"token": "t"})
    assert raw == b"data"
    assert name == "róża.png"


def test_agent_subcommands_present(capsys):
    """§86 — self-service agent credential verbs."""
    parser = cli.build_parser()
    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["agent", "--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    for sub in ("enroll", "token", "whoami", "status", "revoke"):
        assert sub in out


def test_agent_status_wire(monkeypatch, capsys):
    """status maps to GET /api/me/agent-credentials."""
    from mesh import agent
    captured = {}

    def fake_api(method, path, *, cfg, **kw):
        captured.update(method=method, path=path)
        return {"data": {"enrolled": True, "kid": "mesh-agent-x"}}

    monkeypatch.setattr(cli, "_api_call", fake_api)
    args = type("A", (), {"json": False})()
    rc = agent.cmd_agent_status(args, {"token": "t"}, cli)
    assert rc == 0
    assert captured == {"method": "GET", "path": "/api/me/agent-credentials"}
    assert "mesh-agent-x" in capsys.readouterr().out


def test_agent_revoke_wire(monkeypatch, capsys):
    from mesh import agent
    captured = {}
    monkeypatch.setattr(cli, "_api_call",
                        lambda m, p, *, cfg, **k: captured.update(method=m, path=p) or {"data": {"revoked": True}})
    args = type("A", (), {"purge_local": False})()
    assert agent.cmd_agent_revoke(args, {"token": "t"}, cli) == 0
    assert captured == {"method": "DELETE", "path": "/api/me/agent-credentials"}


def test_list_channels_raw_raises_by_default(monkeypatch):
    """An authorisation failure must NOT come back as an empty channel list.

    Guards the 2026-08-20 fix (Wren, report A4): `_list_channels_raw` used to
    swallow every APIError and return [], so `mesh channels list` rendered a
    403 as "(no channels yet)". Leniency is now opt-in via swallow_errors,
    which is what `_resolve_channel` actually wanted.
    """
    import pytest

    def boom(*a, **k):
        raise cli.APIError(403, "token_out_of_scope", "nope")

    monkeypatch.setattr(cli, "_api_call", boom)
    cfg = {"active_mesh_id": "m", "token": "t"}

    with pytest.raises(cli.APIError):
        cli._list_channels_raw(cfg)

    assert cli._list_channels_raw(cfg, swallow_errors=True) == []


def test_err_fields_unwraps_fastapi_detail():
    """FastAPI's {"detail": {...}} must yield a real code/message, not raw JSON.

    Guards the 2026-08-20 fix (Wren, report A2): only {"error": {...}} was
    unwrapped, so every CRM verb printed the raw envelope at the user.
    """
    code, msg = cli._err_fields(
        {"detail": {"code": "no_active_mesh", "message": "Select a mesh."}}, "RAW")
    assert (code, msg) == ("no_active_mesh", "Select a mesh.")
    # our own shape still works
    code, msg = cli._err_fields({"error": {"code": "x", "message": "y"}}, "RAW")
    assert (code, msg) == ("x", "y")
    # unparseable falls back to the raw body, not an exception
    assert cli._err_fields("not a dict", "RAW")[0] == "http_error"
