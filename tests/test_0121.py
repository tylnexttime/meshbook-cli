"""meshbook-cli 0.12.1: the CLI absorbs meshbook-sdk 0.4.1.

Each test here names the 0.12.0 behaviour it replaces. The SDK side of every
change is in meshbook-sdk's CHANGELOG 0.4.1.
"""
from __future__ import annotations

import re
from importlib.metadata import version
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


# ─── the dependency floor ────────────────────────────────────────────────


def _sdk_specs() -> list[str]:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    return re.findall(r'"(meshbook-sdk(?:\[agent\])?[^"]*)"', text)


def test_every_sdk_requirement_has_the_041_floor():
    """0.12.0 allowed meshbook-sdk 0.4.0, whose downloads could write outside
    their directory (C-03). Runtime, [agent] and [dev] must all say 0.4.1."""
    specs = _sdk_specs()
    assert len(specs) == 3, specs
    for spec in specs:
        # 0.12.2 raised the floor to 0.4.2 (test_0122); it may never drop below 0.4.1.
        floor = re.search(r">=0\.4\.(\d+),<0\.5", spec)
        assert floor and int(floor.group(1)) >= 1, spec


def test_the_installed_sdk_meets_the_floor():
    major, minor, patch = (int(x) for x in version("meshbook-sdk").split(".")[:3])
    assert (major, minor) == (0, 4) and patch >= 1


# ─── errors: the SDK's machine hint, in the CLI's words ─────────────────

import json  # noqa: E402

import pytest  # noqa: E402

from tests._fake import BASE, MESH, Err, Resp, ok, ok_list  # noqa: E402,F401


def _corrupt(_isolated_env):
    path = _isolated_env / "config"
    path.write_text("{ half-written", encoding="utf-8")
    return path


def test_corrupt_config_json_error_carries_the_sdk_hint(api, run, _isolated_env):
    _corrupt(_isolated_env)
    r = run("--json", "whoami")
    assert r.rc == 2
    e = r.err_json()["error"]
    assert e["code"] == "config_unreadable"
    assert e["hintCode"] == "fix_config"
    assert "--repair-config" in e["hint"]
    assert "quarantine_config" not in e["message"]


def test_login_refuses_to_write_over_a_corrupt_config(api, run, _isolated_env):
    """The SDK never writes over a file it cannot parse; login says how to repair."""
    path = _corrupt(_isolated_env)
    api.add("GET", "/api/me", ok({"authenticated": True, "user": {"username": "c"}}))
    r = run("login", "--token", "mb_token_fresh")
    assert r.rc == 2, r.err
    assert "--repair-config" in r.err
    assert path.read_text(encoding="utf-8") == "{ half-written"
    assert not list(_isolated_env.glob("config.bad*"))


def test_login_repair_config_moves_it_aside_then_saves(api, run, _isolated_env):
    path = _corrupt(_isolated_env)
    api.add("GET", "/api/me", ok({"authenticated": True, "user": {"username": "c"}}))
    r = run("login", "--token", "mb_token_fresh", "--repair-config")
    assert r.rc == 0, r.err
    bad = list(_isolated_env.glob("config.bad.*"))
    assert len(bad) == 1 and bad[0].read_text(encoding="utf-8") == "{ half-written"
    assert "Moved the unreadable config aside" in r.out
    assert json.loads(path.read_text(encoding="utf-8"))["token"] == "mb_token_fresh"
    assert "mb_token_fresh" not in r.out


def test_login_repair_config_never_quarantines_for_a_rejected_token(api, run, _isolated_env):
    """The repair runs only after the new credential is proven."""
    path = _corrupt(_isolated_env)
    api.add("GET", "/api/me", ok({"authenticated": False}))
    r = run("login", "--token", "mb_token_bad", "--repair-config")
    assert r.rc == 1
    assert path.read_text(encoding="utf-8") == "{ half-written"
    assert not list(_isolated_env.glob("config.bad*"))


def test_login_repair_config_is_a_no_op_on_a_good_config(api, run, _isolated_env):
    api.add("GET", "/api/me", ok({"authenticated": True, "user": {"username": "c"}}))
    r = run("--json", "login", "--token", "mb_token_fresh", "--repair-config")
    assert r.rc == 0, r.err
    assert r.json()["quarantined"] is None
    assert not list(_isolated_env.glob("config.bad*"))


def test_doctor_names_a_broken_config(api, run, _isolated_env):
    _corrupt(_isolated_env)
    api.add("GET", "/api/health", ok({"status": "ok"}))
    r = run("doctor")
    assert r.rc == 1
    assert "config:        !" in r.out and "--repair-config" in r.out
    assert "quarantine_config" not in r.out
    j = run("--json", "doctor").json()
    assert j["configError"]["hintCode"] == "fix_config"
    assert j["error"]["code"] == "config_unreadable"


@pytest.mark.parametrize("resp, hint_code", [
    # an envelope code the SDK has no default for: the SDK says reauthenticate
    (Err(401, {"ok": False, "error": {"code": "invalid_token", "message": "Invalid token"}}),
     "reauthenticate"),
    # /api/me's 200 + authenticated:false shape
    (ok({"authenticated": False}), "reauthenticate"),
    # FastAPI's bare {"detail": ...}: http_error; since SDK 0.4.2 every 401
    # gets reauthenticate (0.12.1 had a CLI-side fallback for this)
    (Err(401, {"detail": "Invalid token"}), "reauthenticate"),
])
def test_a_rejected_credential_gets_the_reauthenticate_advice(api, run, resp, hint_code):
    api.add("GET", "/api/me", resp)
    r = run("--json", "whoami")
    assert r.rc in (1, 2)       # not_authenticated is 2 (0.12.0's mapping, unchanged)
    e = r.err_json()["error"]
    assert e["hintCode"] == hint_code
    assert "mesh login" in e["hint"]


class _CutOff(Resp):
    """A response whose body is lost mid-read (C-16)."""

    def read(self) -> bytes:
        raise ConnectionResetError("connection reset by peer")


def test_a_write_whose_answer_was_lost_says_check_before_retry(api, run):
    """SDK 0.4.1: a POST whose response was cut off may have landed. The CLI's
    generic network advice ("check connectivity") would invite a duplicate."""
    api.add("POST", f"/api/entities/mesh/{MESH}/chat", _CutOff(b""))
    r = run("--json", "chat", "post", "hello")
    assert r.rc == 1
    e = r.err_json()["error"]
    assert (e["code"], e["hintCode"]) == ("network_error", "check_before_retry")
    assert "check" in e["hint"] and "before" in e["hint"]
    human = run("chat", "post", "hello")
    assert "may already have reached the server" in human.err


def test_a_read_whose_answer_was_lost_keeps_the_connectivity_advice(api, run):
    api.add("GET", "/api/contacts", _CutOff(b""))
    e = run("--json", "contacts", "list").err_json()["error"]
    assert (e["code"], e["hintCode"]) == ("network_error", "retry_later")
    assert "connectivity" in e["hint"]


@pytest.mark.parametrize("sdk_hint", ["use_before_cursor", "allow_overwrite", "choose_out_path",
                                      "check_before_retry", "reauthenticate", "fix_config",
                                      "resolve_pending_key", "fix_base_url",
                                      "install_agent_extra"])
def test_every_cli_hint_text_is_a_real_sdk_hint(sdk_hint):
    """HINT_TEXT keys must be hints the SDK documents, or a typo silently
    falls back to the per-code text."""
    from meshbook.errors import HINT_MEANINGS

    from mesh._core import HINT_TEXT

    assert sdk_hint in HINT_TEXT and sdk_hint in HINT_MEANINGS
    assert set(HINT_TEXT) <= set(HINT_MEANINGS)


def test_cli_hint_texts_carry_no_python():
    from mesh._core import HINT_TEXT

    for text in HINT_TEXT.values():
        assert "()" not in text and "=True" not in text and "before=" not in text, text


# ─── cursor paging: --before, never --offset, on the chat routes ────────

import datetime as _dt  # noqa: E402

from tests._fake import CH1, U1  # noqa: E402

FEED = f"/api/entities/mesh/{MESH}/chat"
CHAN = f"/api/channels/{CH1}/messages"


def _msgs(*stamps: str) -> list[dict]:
    return [{"id": f"m{i}", "createdAt": t, "bodyMd": f"msg {i}", "authorName": "a"}
            for i, t in enumerate(stamps)]


def _dm_routes(api):
    api.add("GET", "/api/users", ok_list([{"id": U1, "username": "sam", "displayName": "Sam"}]))
    api.add("POST", f"/api/meshes/{MESH}/dms/with/{U1}", ok({"id": CH1}))


@pytest.mark.parametrize("argv, cursor", [
    (("chat", "list"), True),
    (("chat", "search", "q"), False),
    (("channels", "read", CH1), True),
    (("dm", "read", "sam"), True),
])
def test_offset_is_refused_before_anything_is_sent(api, run, argv, cursor):
    """0.12.0 sent --offset; the server ignored it and the "next page" was the
    first page again. SDK 0.4.1 refuses offset= there; the CLI refuses first,
    in its own words, exit 2 like every other bad argument."""
    r = run(*argv, "--offset", "20")
    assert r.rc == 2, r.err
    assert "--offset is not supported" in r.err
    assert ("--before" in r.err) is cursor
    assert not api.requests
    e = run("--json", *argv, "--offset", "20").err_json()["error"]
    assert e["code"] == "bad_argument"
    assert e["hintCode"] == ("use_before_cursor" if cursor else "fix_argument")
    assert e["status"] == 0


@pytest.mark.parametrize("argv", [("chat", "list"), ("chat", "search", "q"),
                                  ("channels", "read", "x"), ("dm", "read", "x")])
def test_help_offers_no_offset_on_cursor_routes(run, argv):
    out = run(*argv[:2], "--help").out
    assert "--offset" not in out
    assert ("--before" in out) is (argv[1] != "search")


def test_chat_list_before_is_sent_as_given(api, run):
    api.add("GET", FEED, ok_list([]))
    r = run("chat", "list", "--before", "2026-10-19T07:00:00+00:00", "--limit", "5")
    assert r.rc == 0, r.err
    q = api.query(api.one("GET", FEED))
    assert "offset" not in q and q["limit"] == "5"
    assert _dt.datetime.fromisoformat(q["before"]) == _dt.datetime(2026, 10, 19, 7, tzinfo=_dt.timezone.utc)


def test_a_naive_before_is_this_machines_zone(api, run):
    """The SDK refuses a naive time; the CLI localises it, as for events."""
    api.add("GET", FEED, ok_list([]))
    assert run("chat", "list", "--before", "2026-10-19 07:00").rc == 0
    sent = _dt.datetime.fromisoformat(api.query(api.one("GET", FEED))["before"])
    assert sent.utcoffset() is not None
    assert sent == _dt.datetime(2026, 10, 19, 7).astimezone()


def test_a_bad_before_is_a_bad_argument(api, run):
    r = run("--json", "chat", "list", "--before", "yesterday-ish")
    assert r.rc == 2
    assert r.err_json()["error"]["code"] == "bad_argument"
    assert not api.requests


def test_full_page_footer_offers_the_next_before_cursor(api, run):
    """0.12.0's footer said "more: --offset 2" on routes that ignore offset."""
    rows = _msgs("2026-10-19T09:00:00+00:00", "2026-10-19T08:00:00Z")
    api.add("GET", FEED, ok_list(rows, total=2))
    r = run("chat", "list", "--limit", "2")
    assert "more: --before 2026-10-19T08:00:00+00:00" in r.out
    assert "--offset" not in r.out
    env = run("--json", "chat", "list", "--limit", "2").json()
    assert env["hasMore"] is True and env["nextBefore"] == "2026-10-19T08:00:00+00:00"


def test_footer_cursor_pastes_back_unchanged(api, run):
    """The printed cursor, passed back as --before, is the same instant."""
    rows = _msgs("2026-10-19T09:00:00+00:00", "2026-10-19T08:00:00")   # naive = UTC
    api.add("GET", FEED, ok_list(rows, total=2))
    cursor = run("--json", "chat", "list", "--limit", "2").json()["nextBefore"]
    assert cursor == "2026-10-19T08:00:00+00:00"
    run("chat", "list", "--limit", "2", "--before", cursor)
    sent = api.query(api.calls("GET", FEED)[-1])["before"]
    assert _dt.datetime.fromisoformat(sent) == _dt.datetime.fromisoformat(cursor)


def test_a_short_page_offers_no_more(api, run):
    api.add("GET", FEED, ok_list(_msgs("2026-10-19T09:00:00+00:00"), total=1))
    r = run("chat", "list", "--limit", "20")
    assert "more" not in r.out
    assert run("--json", "chat", "list").json()["nextBefore"] is None


def test_channels_read_pages_by_before(api, run):
    rows = _msgs("2026-10-19T09:00:00+00:00", "2026-10-19T08:00:00+00:00")
    api.add("GET", CHAN, ok_list(rows, total=2))
    r = run("channels", "read", CH1, "--limit", "2", "--before", "2026-10-20T00:00:00+00:00")
    assert r.rc == 0, r.err
    assert "before" in api.query(api.one("GET", CHAN))
    assert "more: --before 2026-10-19T08:00:00+00:00" in r.out
    # displayed oldest first
    assert r.out.index("msg 1") < r.out.index("msg 0")


def test_dm_read_pages_by_before(api, run):
    _dm_routes(api)
    api.add("GET", CHAN, ok_list(_msgs("2026-10-19T09:00:00+00:00", "2026-10-19T08:00:00+00:00"),
                                 total=2))
    r = run("--json", "dm", "read", "sam", "--limit", "2", "--before", "2026-10-20T00:00:00Z")
    assert r.rc == 0, r.err
    assert "before" in api.query(api.one("GET", CHAN))
    assert r.json()["nextBefore"] == "2026-10-19T08:00:00+00:00"


def test_search_footer_says_raise_limit_not_offset(api, run):
    hits = [{"id": f"h{i}", "createdAt": "2026-10-19T09:00:00Z", "preview": "p",
             "entityType": "mesh"} for i in range(2)]
    api.add("GET", "/api/chat/search", ok_list(hits, total=2, semantic=True))
    r = run("chat", "search", "q", "--limit", "2")
    assert r.rc == 0, r.err
    assert "--offset" not in r.out
    assert "more: raise --limit" in r.out
    assert "offset" not in api.query(api.one("GET", "/api/chat/search"))


def test_offset_listings_keep_their_offset_footer(api, run):
    """Routes that DO page by offset are unchanged."""
    api.add("GET", "/api/contacts", ok_list([{"id": "c1", "firstName": "A"}] * 2,
                                            total=10, limit=2, offset=4))
    assert "more: --offset 6" in run("contacts", "list", "--limit", "2", "--offset", "4").out


# ─── downloads: --overwrite, and the SDK's refusals explained ───────────

import os  # noqa: E402

DL = {"chat": "/api/chat-attachments/{}/download", "file": "/api/entity-attachments/{}/download"}


@pytest.mark.parametrize("verb", ["chat", "file"])
def test_download_refuses_to_replace_a_file_by_default(api, run, tmp_path, verb):
    """0.12.0 (SDK 0.4.0) replaced an existing file silently."""
    target = tmp_path / "keep.txt"
    target.write_bytes(b"precious")
    api.add("GET", DL[verb].format("a1"), Resp(b"new"))
    r = run(verb, "download", "a1", "--out", str(target))
    assert r.rc == 2, r.err
    assert target.read_bytes() == b"precious"
    assert "--overwrite" in r.err
    assert "overwrite=True" not in r.err
    assert not api.requests                  # an explicit path is checked before the request
    e = run("--json", verb, "download", "a1", "--out", str(target)).err_json()["error"]
    assert (e["code"], e["hintCode"]) == ("file_exists", "allow_overwrite")


@pytest.mark.parametrize("verb", ["chat", "file"])
def test_download_overwrite_replaces(api, run, tmp_path, verb):
    target = tmp_path / "keep.txt"
    target.write_bytes(b"old")
    api.add("GET", DL[verb].format("a1"), Resp(b"new"))
    r = run(verb, "download", "a1", "--out", str(target), "--overwrite")
    assert r.rc == 0, r.err
    assert target.read_bytes() == b"new"


@pytest.mark.parametrize("verb", ["chat", "file"])
def test_download_into_a_directory_refuses_an_existing_server_name(api, run, tmp_path, verb):
    (tmp_path / "served.bin").write_bytes(b"mine")
    api.add("GET", DL[verb].format("a1"),
            Resp(b"theirs", {"Content-Disposition": 'attachment; filename="served.bin"'}))
    r = run(verb, "download", "a1", "--out", str(tmp_path))
    assert r.rc == 2
    assert (tmp_path / "served.bin").read_bytes() == b"mine"
    assert run(verb, "download", "a1", "--out", str(tmp_path), "--overwrite").rc == 0
    assert (tmp_path / "served.bin").read_bytes() == b"theirs"


@pytest.mark.parametrize("hostile", ["../../evil.txt", "..\\..\\evil.txt", "C:\\evil.txt",
                                     "/etc/passwd", "CON"])
def test_a_hostile_server_name_lands_inside_out(api, run, tmp_path, hostile):
    """The security fix behind the 0.4.1 floor (C-03), through the CLI."""
    out = tmp_path / "dl"
    out.mkdir()
    api.add("GET", DL["file"].format("a1"),
            Resp(b"x", {"Content-Disposition": f'attachment; filename="{hostile}"'}))
    r = run("--json", "file", "download", "a1", "--out", str(out))
    assert r.rc == 0, r.err
    saved = r.json()["path"]
    assert os.path.dirname(os.path.abspath(saved)) == os.path.abspath(out)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["dl"]


def test_unsafe_path_is_explained(api, run, tmp_path):
    """A name that resolves outside --out (a planted symlink) is refused."""
    out = tmp_path / "dl"
    out.mkdir()
    outside = tmp_path / "outside.txt"
    try:
        os.symlink(outside, out / "served.bin")
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not permitted here")
    api.add("GET", DL["file"].format("a1"),
            Resp(b"x", {"Content-Disposition": 'attachment; filename="served.bin"'}))
    r = run("--json", "file", "download", "a1", "--out", str(out), "--overwrite")
    assert r.rc == 2, r.err
    e = r.err_json()["error"]
    assert (e["code"], e["hintCode"]) == ("unsafe_path", "choose_out_path")
    assert "--out" in e["hint"]
    assert not outside.exists()


def test_every_download_verb_has_overwrite(run):
    for verb in ("chat", "file"):
        assert "--overwrite" in run(verb, "download", "--help").out


# ─── saved views: the server's module names, singular or plural ─────────


@pytest.mark.parametrize("typed, sent", [
    ("lead", "leads"), ("leads", "leads"), ("Leads", "leads"),
    ("contact", "contacts"), ("company", "companies"), ("companies", "companies"),
    ("project", "projects"), ("task", "tasks"), ("tasks", "tasks"),
    ("event", "calendar"), ("calendar", "calendar"),
    ("activity_log", "activity-log"), ("activity-log", "activity-log"), ("activity", "activity-log"),
])
def test_saved_views_list_maps_to_the_server_module(api, run, typed, sent):
    """SDK 0.4.1 filters by module for real (0.4.0's entityType was ignored),
    so "lead" and "leads" must send the same thing."""
    api.add("GET", "/api/saved-views", ok_list([]))
    r = run("saved-views", "list", "--module", typed)
    assert r.rc == 0, r.err
    q = api.query(api.one("GET", "/api/saved-views"))
    assert q["module"] == sent and "entityType" not in q


def test_saved_views_list_keeps_the_entity_spelling(api, run):
    api.add("GET", "/api/saved-views", ok_list([]))
    assert run("saved-views", "list", "--entity", "lead").rc == 0
    assert api.query(api.one("GET", "/api/saved-views"))["module"] == "leads"


def test_saved_views_list_without_module_sends_no_filter(api, run):
    api.add("GET", "/api/saved-views", ok_list([{"module": "leads", "name": "Hot", "id": "sv"}]))
    r = run("saved-views", "list")
    assert "module" not in api.query(api.one("GET", "/api/saved-views"))
    assert "leads: Hot" in r.out      # the route's own field


@pytest.mark.parametrize("typed, sent", [("lead", "leads"), ("contacts", "contacts"),
                                         ("events", "calendar")])
def test_saved_views_create_maps_the_module(api, run, typed, sent):
    api.add("POST", "/api/saved-views", ok({"id": "sv-1"}))
    r = run("saved-views", "create", "--module", typed, "--name", "Hot")
    assert r.rc == 0, r.err
    assert api.body(api.one("POST", "/api/saved-views"))["module"] == sent
    assert f"for {sent}" in r.out


@pytest.mark.parametrize("verb", [("list", "--module", "deals"),
                                  ("create", "--module", "deals", "--name", "X")])
def test_an_unknown_module_is_refused_before_sending(api, run, verb):
    """The column has a CHECK constraint: an unknown module was a 500 on
    create and an always-empty list."""
    r = run("--json", "saved-views", *verb)
    assert r.rc == 2
    e = r.err_json()["error"]
    assert e["code"] == "bad_argument" and "leads" in e["message"]
    assert not api.requests


def test_cli_modules_match_the_server_constraint():
    """Pinned to the server's ck_saved_views_module (migrations 0001); read
    from the sibling checkout when it is there, so a server change shows up."""
    from mesh.cmd_crm import SAVED_VIEW_MODULES

    assert set(SAVED_VIEW_MODULES) == {"contacts", "companies", "leads", "projects", "tasks",
                                       "calendar", "activity-log"}
    mig = ROOT.parent / "meshbook" / "migrations" / "versions" / "0001_meshbook_initial.py"
    if not mig.exists():
        pytest.skip("meshbook server checkout not beside this repo")
    m = re.search(r"module IN \(([^)]*)\)", mig.read_text(encoding="utf-8"))
    assert m is not None
    server = {x.strip().strip("'") for x in m.group(1).split(",")}
    assert server == set(SAVED_VIEW_MODULES)


# ─── the rest of SDK 0.4.1's behaviour changes, as the CLI shows them ───

from tests._fake import MESH2, meshes_payload  # noqa: E402


def test_an_empty_mesh_flag_is_refused_not_ignored(api, run):
    """0.12.0 turned --mesh "" into "no flag" and quietly used the config's
    mesh: the very fallthrough the SDK closed in 0.4.1 (C-10)."""
    api.add("GET", "/api/tasks", ok_list([]))
    r = run("--json", "--mesh", "", "tasks", "list")
    assert r.rc == 2, r.err
    assert r.err_json()["error"]["code"] == "bad_argument"
    assert not api.calls("GET", "/api/tasks")


def test_an_empty_meshbook_mesh_is_refused(api, run, monkeypatch):
    """A script that exports MESHBOOK_MESH= to mean "unset" now gets told."""
    monkeypatch.setenv("MESHBOOK_MESH", "")
    api.add("GET", "/api/tasks", ok_list([]))
    r = run("tasks", "list")
    assert r.rc == 2, r.err
    assert "MESHBOOK_MESH" in r.err
    assert not api.calls("GET", "/api/tasks")


def test_meshes_use_says_when_meshbook_mesh_will_win(api, run, monkeypatch, recwarn):
    monkeypatch.setenv("MESHBOOK_MESH", "Other")
    api.add("GET", "/api/meshes", meshes_payload((MESH, "Test Mesh"), (MESH2, "Other")))
    api.add("POST", "/api/meshes/active", ok({}))
    r = run("meshes", "use", "Test Mesh")
    assert r.rc == 0, r.err
    assert "MESHBOOK_MESH=Other is set in this shell" in r.err
    assert "UserWarning" not in r.err
    assert not [w for w in recwarn if issubclass(w.category, UserWarning)]
    j = run("--json", "meshes", "use", "Test Mesh")
    assert j.json()["envOverride"] == "Other"


def test_meshes_use_without_an_override_says_nothing_extra(api, run):
    api.add("POST", "/api/meshes/active", ok({}))
    r = run("meshes", "use", "Test Mesh")
    assert r.rc == 0, r.err
    assert "MESHBOOK_MESH=" not in r.err
    assert "envOverride" not in run("--json", "meshes", "use", "Test Mesh").json()


@pytest.mark.parametrize("base", ["http://192.168.1.5:8000", "http://meshbook.org"])
def test_a_plain_http_base_is_refused_locally(api, run, base):
    """SDK 0.4.1: the token never crosses the network unencrypted."""
    r = run("--json", "login", "--token", "mb_token_x", "--base", base)
    assert r.rc == 2, r.err
    e = r.err_json()["error"]
    assert (e["code"], e["hintCode"]) == ("insecure_base", "fix_base_url")
    assert "https" in e["hint"]
    assert not api.requests


def test_a_loopback_http_base_is_still_allowed(api, run):
    api.add("GET", "/api/me", ok({"authenticated": True, "user": {"username": "c"}}))
    r = run("login", "--token", "mb_token_x", "--base", "http://localhost:8000")
    assert r.rc == 0, r.err


@pytest.mark.parametrize("code, hint, must, must_not", [
    ("pending_key", None, "mesh agent token", "recover_pending("),
    ("missing_dependency", None, "meshbook-cli[agent]", "meshbook-sdk[agent]"),
    ("bad_argument", "use_before_cursor", "--before", "before="),
    ("file_exists", None, "--overwrite", "overwrite=True"),
])
def test_sdk_python_wording_never_reaches_the_terminal(capsys, code, hint, must, must_not):
    """Messages chosen by hint: the SDK's sentence names Python calls."""
    from meshbook import MeshbookError

    from mesh._core import report_error

    sdk_msg = {"pending_key": "Call client.agent.recover_pending() to try it.",
               "missing_dependency": "pip install meshbook-sdk[agent]",
               "bad_argument": "chat.list() does not support offset=: pages by limit= and before=.",
               "file_exists": "x already exists; not overwritten."}[code]
    for as_json in (False, True):
        rc = report_error(MeshbookError(code, sdk_msg, hint=hint), as_json=as_json)
        err = capsys.readouterr().err
        assert must in err and must_not not in err, err
        assert rc in (2, 3) if code != "pending_key" else rc == 1
