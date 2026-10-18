"""§96 `auth_mode: agent` (the enrolled key as the everyday login), as the CLI
sees it since 0.12: the lane itself is meshbook-sdk's `client.agent`; these
tests drive `mesh ...` end to end with the transport (meshbook calls AND the
token endpoint) faked, and check what lands on the wire and on disk.

The SDK's own suite covers the cache arithmetic (re-mint inside the 30 s
margin); what matters here is that the CLI uses that lane for every verb.
"""
from __future__ import annotations

import base64
import json
import time

import pytest

pytest.importorskip("cryptography")

from cryptography.hazmat.primitives import hashes, serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import padding, rsa  # noqa: E402

from mesh import cli  # noqa: E402
from tests._fake import BASE, MESH, err, ok  # noqa: E402
from tests.conftest import read_config, write_config  # noqa: E402

TOKEN_PATH = "/application/o/token/"


def _fake_jwt(exp: int) -> str:
    body = base64.urlsafe_b64encode(json.dumps({"exp": exp}).encode()).rstrip(b"=").decode()
    return f"eyJhbGciOiJSUzI1NiJ9.{body}.sig"


@pytest.fixture()
def bench(_isolated_env):
    """A config dir holding an enrolled key + mint bundle, auth_mode agent."""
    d = _isolated_env
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    (d / "agent-key.pem").write_bytes(key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption()))
    (d / "agent-key.json").write_text(json.dumps({
        "kid": "kid-old", "tokenEndpoint": "https://auth.example" + TOKEN_PATH,
        "audience": "https://auth.example/issuer/", "clientId": "meshbook-agents",
        "username": "wanderer"}), encoding="utf-8")
    write_config({"base": BASE, "auth_mode": "agent", "active_mesh_id": MESH})
    return {"dir": d, "key": key}


def _me_route(api, username: str = "wanderer") -> None:
    api.add("GET", "/api/me", ok({"user": {"username": username, "identityType": "AI"},
                                  "activeMeshId": MESH}))


def _auth(api, req) -> str:
    return api.header(req, "Authorization") or ""


# ─── the lane, used by ordinary verbs ───────────────────────────────

def test_bearer_mode_returns_stored_token(api, run):
    _me_route(api)
    assert run("whoami").rc == 0
    assert _auth(api, api.one("GET", "/api/me")) == "Bearer mb_token_test"


def test_agent_mode_mints_and_caches(bench, api, run):
    """One mint serves every request in the invocation (doctor makes several)."""
    jwt = _fake_jwt(int(time.time()) + 300)
    api.add("POST", TOKEN_PATH, {"access_token": jwt, "expires_in": 300})
    api.add("GET", "/api/health", {"ok": True})
    _me_route(api)
    r = run("doctor")
    assert r.rc == 0, r.err
    assert len(api.calls("POST", TOKEN_PATH)) == 1
    assert _auth(api, api.one("GET", "/api/me")) == f"Bearer {jwt}"
    assert "agent-JWT" in r.out


def test_agent_mode_falls_back_to_bearer_on_mint_failure(bench, api, run):
    write_config({"base": BASE, "auth_mode": "agent", "token": "mb_token_fallback",
                  "active_mesh_id": MESH})
    api.add("POST", TOKEN_PATH, err(503, "unavailable"))
    _me_route(api)
    r = run("whoami")
    assert r.rc == 0, r.err
    assert _auth(api, api.one("GET", "/api/me")) == "Bearer mb_token_fallback"


def test_agent_mode_without_bearer_propagates_failure(bench, api, run):
    api.add("POST", TOKEN_PATH, err(503, "unavailable"))
    r = run("whoami")
    assert r.rc != 0
    assert not api.calls("GET", "/api/me")


def test_agent_mode_unready_falls_through_to_bearer(api, run):
    """auth_mode says agent but no key on disk: the stored bearer still works."""
    write_config({"base": BASE, "auth_mode": "agent", "token": "mb_token_x"})
    _me_route(api)
    assert run("whoami").rc == 0
    assert _auth(api, api.one("GET", "/api/me")) == "Bearer mb_token_x"


# ─── enroll / rotation (§99) ────────────────────────────────────────

def _enroll_route(api) -> None:
    def reply(kid: str = "") -> dict:
        return ok({"enrolled": True, "username": "wanderer",
                   "tokenEndpoint": "https://auth.example" + TOKEN_PATH,
                   "assertionAud": "https://auth.example/issuer/",
                   "clientId": "meshbook-agents"})
    api.add("POST", "/api/me/agent-credentials", reply())


def _pad(s: str) -> str:
    return s + "=" * (-len(s) % 4)


def test_rotation_assertion_signed_by_current_key(bench, api, run):
    write_config({"base": BASE, "token": "mb_token_x"})
    _enroll_route(api)
    r = run("agent", "enroll", "--force")
    assert r.rc == 0, r.err
    body = api.body(api.one("POST", "/api/me/agent-credentials"))
    head, payload, sig = body["rotationAssertion"].split(".")
    claims = json.loads(base64.urlsafe_b64decode(_pad(payload)))
    assert claims["sub"] == "wanderer"
    assert claims["aud"] == "meshbook-agent-rotation"
    assert claims["new_kid"] == body["publicKey"]["kid"]
    assert body["publicKey"]["kid"].startswith("mesh-agent-")    # the CLI's forensic label
    bench["key"].public_key().verify(
        base64.urlsafe_b64decode(_pad(sig)), f"{head}.{payload}".encode(),
        padding.PKCS1v15(), hashes.SHA256())


def test_rotation_assertion_none_without_current_key(api, run):
    _enroll_route(api)
    r = run("agent", "enroll")
    assert r.rc == 0, r.err
    assert "rotationAssertion" not in api.body(api.one("POST", "/api/me/agent-credentials"))


def test_enroll_rotation_sends_assertion_and_backs_up(bench, api, run):
    write_config({"base": BASE, "token": "mb_token_x"})
    _enroll_route(api)
    r = run("agent", "enroll", "--force")
    assert r.rc == 0, r.err
    assert read_config().get("auth_mode") == "agent"            # §96 flip persisted
    assert (bench["dir"] / "agent-key.pem.bak").exists()        # outgoing key kept
    meta = json.loads((bench["dir"] / "agent-key.json").read_text(encoding="utf-8"))
    assert meta["kid"] != "kid-old"
    assert "auth_mode set to 'agent'" in r.out


def test_enroll_refuses_to_clobber_without_force(bench, api, run):
    write_config({"base": BASE, "token": "mb_token_x"})
    r = run("agent", "enroll")
    assert r.rc == 2
    assert "--force" in r.err
    assert not api.calls("POST", "/api/me/agent-credentials")


def test_first_enroll_sends_no_assertion(api, run):
    _enroll_route(api)
    assert run("agent", "enroll").rc == 0
    assert "rotationAssertion" not in api.body(api.one("POST", "/api/me/agent-credentials"))


def test_enroll_without_any_lane_refused(api, run):
    write_config({})
    r = run("agent", "enroll")
    assert r.rc == 2
    assert "mesh agent register" in r.err


def test_agent_mode_bench_can_rotate_without_bearer(bench, api, run):
    """§96 acceptance: no mb_token_ anywhere, auth_mode agent, rotation works."""
    api.add("POST", TOKEN_PATH, {"access_token": _fake_jwt(int(time.time()) + 300)})
    _enroll_route(api)
    r = run("agent", "enroll", "--force")
    assert r.rc == 0, r.err
    req = api.one("POST", "/api/me/agent-credentials")
    assert _auth(api, req).startswith("Bearer eyJ")             # signed in with the key lane


def test_revoke_clears_agent_mode(bench, api, run):
    write_config({"base": BASE, "auth_mode": "agent", "token": "mb_token_x"})
    api.add("POST", TOKEN_PATH, {"access_token": _fake_jwt(int(time.time()) + 300)})
    api.add("DELETE", "/api/me/agent-credentials", ok({"revoked": True}))
    r = run("agent", "revoke")
    assert r.rc == 0, r.err
    assert "auth_mode" not in read_config()
    assert read_config()["token"] == "mb_token_x"
    assert "stored bearer again" in r.out


def test_login_agent_flag_exists(capsys):
    parser = cli.build_parser()
    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["login", "--help"])
    assert exc.value.code == 0
    assert "--agent" in capsys.readouterr().out


def test_login_agent_persists_auth_mode(bench, api, run):
    write_config({"base": BASE})
    api.add("POST", TOKEN_PATH, {"access_token": _fake_jwt(int(time.time()) + 300)})
    _me_route(api)
    r = run("login", "--agent")
    assert r.rc == 0, r.err
    assert read_config()["auth_mode"] == "agent"
    assert "@wanderer" in r.out


def test_agent_verbs_say_how_to_install_cryptography(api, run, monkeypatch):
    """Without the optional extra, `mesh agent ...` names the exact install line."""
    import importlib.util

    real = importlib.util.find_spec
    monkeypatch.setattr(importlib.util, "find_spec",
                        lambda name, *a: None if name == "cryptography" else real(name, *a))
    r = run("agent", "whoami")
    assert r.rc == 3
    assert 'pip install "meshbook-cli[agent]"' in r.err
    r = run("--json", "agent", "token")
    assert r.rc == 3
    e = r.err_json()["error"]
    assert e["code"] == "missing_dependency" and "meshbook-cli[agent]" in e["message"]
