"""SURVEY 2026-10-19 §4 rough edges, and the bugs found while porting to 0.12.

Every test here failed (or would have) against 0.11; the 0.11 behaviour is
named in each docstring.
"""
from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from mesh import cli
from tests._fake import CH1, MESH, U1, U2, err, ok, ok_list

EMOJI_MSG = {"author": {"displayName": "Wren"}, "createdAt": "2026-10-19T07:00:00",
             "bodyMd": "✅ fixed, zażółć"}


# ─── Windows piped output (cp1252) ──────────────────────────────────

def _cp1252_streams(monkeypatch):
    out = io.TextIOWrapper(io.BytesIO(), encoding="cp1252", errors="strict")
    errs = io.TextIOWrapper(io.BytesIO(), encoding="cp1252", errors="strict")
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", errs)
    return out, errs


def test_cp1252_stdout_does_not_crash(api, monkeypatch):
    """0.11: `mesh chat list | findstr x` on Windows raised UnicodeEncodeError
    at the first emoji (stdout piped = cp1252). Now stdio is reconfigured to
    UTF-8 before anything prints."""
    api.add("GET", f"/api/entities/mesh/{MESH}/chat", ok_list([EMOJI_MSG]))
    out, _ = _cp1252_streams(monkeypatch)
    rc = cli.main(["chat", "list"])
    out.flush()
    assert rc == 0
    text = out.buffer.getvalue().decode("utf-8")
    assert "✅ fixed, zażółć" in text


def test_cp1252_control_shows_the_test_can_fail(api, monkeypatch):
    """The same call with the fix switched off crashes exactly as 0.11 did:
    proof the test above is not vacuous."""
    api.add("GET", f"/api/entities/mesh/{MESH}/chat", ok_list([EMOJI_MSG]))
    _cp1252_streams(monkeypatch)
    monkeypatch.setattr(cli, "configure_stdio", lambda: None)
    with pytest.raises(UnicodeEncodeError):
        cli.main(["chat", "list"])


def test_cp1252_subprocess_pipe(tmp_path):
    """End to end in a real child process with a piped, cp1252 stdout and UTF-8
    mode off: the exact SURVEY repro (tools/cli_cp1252_repro.py), transport faked."""
    cfg = tmp_path / "cfg"
    cfg.mkdir()
    (cfg / "config").write_text(json.dumps(
        {"token": "mb_token_test", "base": "https://mesh.test", "active_mesh_id": MESH}),
        encoding="utf-8")
    code = (
        "import json, meshbook.client as mc\n"
        "from tests._fake import Router, ok_list\n"
        f"r = Router(); r.add('GET', '/api/entities/mesh/{MESH}/chat', ok_list([{EMOJI_MSG!r}]))\n"
        "mc._urlopen = r\n"
        "from mesh import cli\n"
        "raise SystemExit(cli.main(['chat', 'list']))\n"
    )
    env = {k: v for k, v in os.environ.items()
           if k not in ("PYTHONUTF8", "PYTHONIOENCODING", "MESHBOOK_TOKEN", "MESHBOOK_MESH")}
    env.update({"PYTHONUTF8": "0", "PYTHONIOENCODING": "cp1252", "MESHBOOK_CONFIG_DIR": str(cfg)})
    repo = Path(__file__).resolve().parent.parent
    proc = subprocess.run([sys.executable, "-c", code], cwd=repo, env=env,
                          capture_output=True, timeout=60)
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")
    assert "✅ fixed, zażółć" in proc.stdout.decode("utf-8")


# ─── --json is never overwritten by a subcommand ────────────────────

@pytest.mark.parametrize("argv", [
    ["chat", "search", "x"],
    ["members", "list"],
    ["channels", "members", "general"],
    ["agent", "status"],
])
@pytest.mark.parametrize("where", ["before", "after"])
def test_global_json_respected_everywhere(api, run, argv, where):
    """0.11: `mesh --json chat search x`, `mesh --json members list` and
    `mesh --json channels members general` printed human text (each subparser
    redefined --json and its default overwrote the global flag); `agent status`
    and `agent whoami` had the same defect."""
    api.add("GET", "/api/chat/search", ok_list([], semantic=True))
    api.add("GET", f"/api/meshes/{MESH}/detail", ok({"mesh": {"name": "M"}, "members": []}))
    api.add("GET", f"/api/meshes/{MESH}/channels", ok_list([{"id": CH1, "name": "general"}]))
    api.add("GET", f"/api/channels/{CH1}/members", ok_list([]))
    api.add("GET", "/api/me/agent-credentials", ok({"enrolled": False}))
    full = ["--json", *argv] if where == "before" else [*argv, "--json"]
    r = run(*full)
    assert r.rc == 0, r.err
    json.loads(r.out)   # parses: it is JSON, not human text


def test_global_mesh_option_works_after_the_subcommand(api, run):
    other = "44444444-4444-4444-4444-444444444444"
    api.add("GET", "/api/contacts", ok_list([]))
    run("contacts", "list", "--mesh", other)
    assert api.header(api.one("GET", "/api/contacts"), "X-Active-Mesh-Id") == other


# ─── errors under --json ────────────────────────────────────────────

def test_api_error_is_json_on_stderr(api, run):
    api.add("GET", "/api/contacts", err(403, "token_out_of_scope", "Token does not cover mesh"))
    r = run("--json", "contacts", "list")
    assert r.rc == 1
    assert r.out == ""
    payload = r.err_json()
    assert payload["ok"] is False
    assert payload["error"]["code"] == "token_out_of_scope"
    assert payload["error"]["message"] == "Token does not cover mesh"
    assert "agent" in payload["error"]["hint"]
    assert payload["error"]["status"] == 403


def test_not_signed_in_is_json_and_exit_2(api, run):
    from tests.conftest import write_config

    write_config({"base": "https://mesh.test", "active_mesh_id": MESH})
    r = run("contacts", "list", "--json")
    assert r.rc == 2
    assert r.err_json()["error"]["code"] == "not_authenticated"


def test_usage_error_is_json_under_json(run):
    r = run("--json", "tasks", "list", "--sort", "sideways")
    assert r.rc == 2
    e = r.err_json()["error"]
    assert e["code"] == "usage" and "sideways" in e["message"]


def test_human_error_keeps_hint(api, run):
    api.add("GET", "/api/contacts", err(503, "unavailable", "down for maintenance"))
    r = run("contacts", "list")
    assert r.rc == 1
    assert "down for maintenance" in r.err
    assert len(api.calls("GET", "/api/contacts")) == 3   # the SDK retried a GET on 503


# ─── listings: N of TOTAL, --limit / --offset, one JSON envelope ────

def test_listing_shows_n_of_total_and_pages(api, run):
    rows = [{"id": f"c{i}", "displayName": f"Contact {i}"} for i in range(2)]
    api.add("GET", "/api/contacts", ok_list(rows, total=10, limit=2, offset=4))
    r = run("contacts", "list", "--limit", "2", "--offset", "4")
    assert r.rc == 0, r.err
    q = api.query(api.one("GET", "/api/contacts"))
    assert q["limit"] == "2" and q["offset"] == "4"
    assert "2 of 10 (from offset 4) - more: --offset 6" in r.out


def test_listing_json_envelope(api, run):
    api.add("GET", "/api/tasks", ok_list([{"id": "t1", "title": "A"}], total=7, limit=1, offset=0))
    r = run("--json", "tasks", "list", "--limit", "1")
    env = r.json()
    assert env["items"] == [{"id": "t1", "title": "A"}]
    assert (env["count"], env["total"], env["limit"], env["offset"], env["hasMore"]) == (1, 7, 1, 0, True)


def test_listing_without_total_says_shown(api, run):
    api.add("GET", f"/api/meshes/{MESH}/dms", [{"id": "d1", "name": "Rook"}])
    r = run("dm", "list")
    assert "1 shown" in r.out


# ─── bugs found while porting (0.11 crashed or lied) ────────────────

def test_chat_list_null_body_does_not_crash(api, run):
    """0.11: m.get('bodyMd', '')[:200] raised TypeError on a null body."""
    api.add("GET", f"/api/entities/mesh/{MESH}/chat",
            ok_list([{"author": {"displayName": "Rook"}, "createdAt": "x", "bodyMd": None}]))
    r = run("chat", "list")
    assert r.rc == 0, r.err
    assert "Rook:" in r.out


@pytest.mark.parametrize("body", ["", None, "   "])
def test_channel_read_empty_body_does_not_crash(api, run, body):
    """0.11: body.strip().splitlines()[0] raised IndexError on an
    attachment-only message and killed the whole `channels read` / `dm read`."""
    api.add("GET", f"/api/meshes/{MESH}/channels", ok_list([{"id": CH1, "name": "general"}]))
    api.add("GET", f"/api/channels/{CH1}/messages",
            ok_list([{"id": "m1", "author": {"displayName": "Ember"}, "bodyMd": body}]))
    r = run("channels", "read", "general")
    assert r.rc == 0, r.err
    assert "Ember:" in r.out


def test_notifications_null_summary_does_not_crash(api, run):
    """0.11: n.get('summary', '')[:120] raised TypeError on a null summary."""
    api.add("GET", "/api/notifications",
            ok_list([{"kind": "Mention", "summary": None, "createdAt": "2026-10-19T01:00:00"}]))
    r = run("notifications")
    assert r.rc == 0, r.err
    assert "Mention" in r.out


def test_members_decline_says_declined(api, run):
    """0.11 printed f"{action.title()}ed": "Declineed"."""
    other = "33333333-3333-3333-3333-333333333333"
    api.add("POST", f"/api/meshes/{other}/respond", ok({}))
    r = run("members", "accept", other, "--decline")
    assert r.rc == 0
    assert "Declined invitation" in r.out
    assert api.body(api.one("POST", f"/api/meshes/{other}/respond")) == {"action": "decline"}


def test_projects_status_filter_uses_server_vocabulary(api, run):
    """0.11 advertised --status Planning/Active/OnHold/Complete/Cancelled, but
    the server's statusFilter only knows active|completed and ignored anything
    else: `--status Active` silently returned every project."""
    api.add("GET", "/api/projects", ok_list([]))
    assert run("projects", "list", "--status", "active").rc == 0
    assert api.query(api.one("GET", "/api/projects"))["statusFilter"] == "active"
    r = run("projects", "list", "--status", "Active")
    assert r.rc == 2                               # refused, not silently ignored


def test_dm_read_reports_a_403_instead_of_user_not_found(api, run):
    """0.11's _resolve_user swallowed every error: a 403 on /api/users became
    "User 'rook' not found in active mesh"."""
    api.add("GET", "/api/users", err(403, "token_out_of_scope", "Token does not cover mesh"))
    r = run("dm", "read", "rook")
    assert r.rc == 1
    assert "Token does not cover mesh" in r.err
    assert "not found" not in r.err


def test_channel_by_uuid_reports_a_403_instead_of_no_match(api, run):
    """0.11's _resolve_channel turned any error on GET /api/channels/<uuid> into
    "No channel matching ..."."""
    api.add("GET", f"/api/channels/{CH1}", err(403, "forbidden", "Private channel"))
    r = run("channels", "read", CH1)
    assert r.rc == 1
    assert "Private channel" in r.err


def test_ambiguous_display_name_is_refused_not_guessed(api, run):
    """0.11 picked the FIRST user whose display name matched; two "Sam"s meant
    a DM could go to the wrong one. Now: user_ambiguous, nothing sent."""
    api.add("GET", "/api/users", ok_list([
        {"id": U1, "username": "sam_a", "displayName": "Sam"},
        {"id": U2, "username": "sam_b", "displayName": "Sam"}]))
    r = run("dm", "send", "Sam", "hello")
    assert r.rc == 1
    assert "sam_a" in r.err and "sam_b" in r.err
    assert not [q for q in api.requests if q.get_method() == "POST"]


# ─── a server older than the client ─────────────────────────────────

@pytest.mark.parametrize("resp", [
    {"status": 405, "payload": {"detail": "Method Not Allowed"}},
    {"status": 404, "payload": {"detail": "Not Found"}},
])
def test_unknown_route_says_the_server_may_predate_the_client(api, run, resp):
    """Prod lacks PATCH /api/calendar-events/{id} and the project-member routes
    until the 2026-10-19 server is deployed; a bare 405/404 told nobody why."""
    from tests._fake import E1, Err

    api.add("PATCH", f"/api/calendar-events/{E1}", Err(resp["status"], resp["payload"]))
    r = run("events", "update", E1, "--title", "x")
    assert r.rc == 1
    assert "may predate meshbook-cli" in r.err


def test_our_own_not_found_is_not_called_an_unknown_route(api, run):
    from tests._fake import E1

    api.add("GET", f"/api/calendar-events/{E1}", err(404, "not_found", "Event not found"))
    r = run("events", "show", E1)
    assert r.rc == 1 and "Event not found" in r.err
    assert "predate" not in r.err


def test_keyword_only_note_goes_to_stderr(api, run):
    """The "semantic arm off" note is a diagnostic: stderr, so `| grep` sees hits only
    (0.11 behaviour, briefly lost in the port)."""
    api.add("GET", "/api/chat/search", ok_list([{"id": "m1", "entityType": "mesh",
                                                  "preview": "hit", "matchedArms": ["fts_en"]}],
                                                semantic=False))
    r = run("chat", "search", "x")
    assert "semantic arm off" in r.err
    assert "semantic arm off" not in r.out
