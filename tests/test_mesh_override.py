"""DEV-DEBT §102.1, the safety item: pick a mesh for ONE invocation.

Precedence (the SDK's): --mesh > MESHBOOK_MESH > config active_mesh_id. Only
`mesh meshes use` persists. Every write says which mesh it went to.
"""
from __future__ import annotations

from tests._fake import MESH, MESH2, ok, ok_list
from tests.conftest import read_config


def _two_meshes(api):
    api.add("GET", "/api/meshes", ok_list([
        {"id": MESH, "name": "Test Mesh", "meshType": "family", "memberRole": "admin"},
        {"id": MESH2, "name": "The Tyl Mesh", "meshType": "family", "memberRole": "member"},
    ]))
    api.add("POST", f"/api/entities/mesh/{MESH}/chat", ok({"id": "m-1"}))
    api.add("POST", f"/api/entities/mesh/{MESH2}/chat", ok({"id": "m-2"}))


def _mesh_header(api, req):
    return api.header(req, "X-Active-Mesh-Id")


def test_mesh_flag_by_name_routes_one_call_and_persists_nothing(api, run):
    _two_meshes(api)
    r = run("--mesh", "the tyl mesh", "chat", "post", "hi")
    assert r.rc == 0, r.err
    req = api.one("POST", f"/api/entities/mesh/{MESH2}/chat")
    assert _mesh_header(api, req) == MESH2
    assert read_config()["active_mesh_id"] == MESH          # untouched
    assert f"mesh: The Tyl Mesh ({MESH2}) [--mesh]" in r.out


def test_env_mesh_beats_config(api, run, monkeypatch):
    _two_meshes(api)
    monkeypatch.setenv("MESHBOOK_MESH", MESH2)
    r = run("chat", "post", "hi")
    assert r.rc == 0, r.err
    assert api.calls("POST", f"/api/entities/mesh/{MESH2}/chat")
    assert "[MESHBOOK_MESH]" in r.out


def test_flag_beats_env(api, run, monkeypatch):
    _two_meshes(api)
    monkeypatch.setenv("MESHBOOK_MESH", MESH2)
    run("chat", "post", "hi", "--mesh", MESH)
    assert api.calls("POST", f"/api/entities/mesh/{MESH}/chat")
    assert not api.calls("POST", f"/api/entities/mesh/{MESH2}/chat")


def test_config_mesh_is_the_default_and_is_named(api, run):
    _two_meshes(api)
    r = run("chat", "post", "hi")
    assert r.rc == 0, r.err
    assert f"mesh: Test Mesh ({MESH}) [config]" in r.out


def test_unknown_mesh_name_refuses_before_any_write(api, run):
    _two_meshes(api)
    r = run("--mesh", "Nowhere", "chat", "post", "hi")
    assert r.rc == 1
    assert "No mesh matching 'Nowhere'" in r.err
    assert not [q for q in api.requests if q.get_method() == "POST"]


def test_ambiguous_mesh_name_refuses(api, run):
    api.add("GET", "/api/meshes", ok_list([
        {"id": MESH, "name": "Twin", "meshType": "x"}, {"id": MESH2, "name": "twin", "meshType": "x"}]))
    r = run("--json", "--mesh", "TWIN", "chat", "post", "hi")
    assert r.rc == 1
    assert r.err_json()["error"]["code"] == "mesh_ambiguous"


def test_meshes_use_persists_after_server_verification(api, run):
    _two_meshes(api)
    api.add("POST", "/api/meshes/active", ok({}))
    r = run("meshes", "use", "The Tyl Mesh")
    assert r.rc == 0, r.err
    assert api.body(api.one("POST", "/api/meshes/active")) == {"meshId": MESH2}
    assert read_config()["active_mesh_id"] == MESH2


def test_meshes_use_refused_changes_nothing(api, run):
    from tests._fake import err

    _two_meshes(api)
    api.add("POST", "/api/meshes/active", err(403, "token_out_of_scope", "out of scope"))
    r = run("meshes", "use", "The Tyl Mesh")
    assert r.rc == 1
    assert "Nothing was changed locally" in r.err
    assert read_config()["active_mesh_id"] == MESH


def test_meshes_list_marks_the_mesh_in_effect(api, run):
    _two_meshes(api)
    r = run("--mesh", "The Tyl Mesh", "meshes", "list")
    line = next(x for x in r.out.splitlines() if "The Tyl Mesh" in x)
    assert line.lstrip().startswith("*")


def test_account_level_calls_carry_no_mesh(api, run):
    """/api/me must not inherit a mesh (a stale one must not break login/whoami)."""
    api.add("GET", "/api/me", ok({"user": {"username": "wren"}}))
    _two_meshes(api)
    run("--mesh", "The Tyl Mesh", "whoami")
    assert _mesh_header(api, api.one("GET", "/api/me")) is None


def test_whoami_names_the_mesh_for_this_call(api, run):
    _two_meshes(api)
    api.add("GET", "/api/me", ok({"user": {"username": "wren"}, "activeMeshId": MESH}))
    r = run("--mesh", "The Tyl Mesh", "whoami")
    assert f"mesh for this call:    The Tyl Mesh ({MESH2}) [--mesh]" in r.out
