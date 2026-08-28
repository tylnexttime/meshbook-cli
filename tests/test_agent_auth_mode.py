# -*- coding: utf-8 -*-
"""§96 — `auth_mode: agent`: the enrolled key as the everyday login.

Offline coverage of the new lane: token resolution and caching, bearer
fallback, the §99 rotation proof, and the enroll/revoke config flips.
Network is always monkeypatched; `cryptography` is required (it is a
real dependency of the agent lane, same as in production use).
"""
from __future__ import annotations

import base64
import json
import time

import pytest

pytest.importorskip("cryptography")

from cryptography.hazmat.primitives import hashes, serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import padding, rsa  # noqa: E402

from mesh import agent, cli  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_cache():
    agent.invalidate_cached_token()
    yield
    agent.invalidate_cached_token()


@pytest.fixture()
def bench(tmp_path, monkeypatch):
    """A temp CONFIG_DIR holding an enrolled key + meta, wired into both
    modules the way MESHBOOK_CONFIG_DIR would do it."""
    monkeypatch.setattr(cli, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(cli, "CONFIG_PATH", tmp_path / "config")
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    (tmp_path / "agent-key.pem").write_bytes(key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption()))
    (tmp_path / "agent-key.json").write_text(json.dumps({
        "kid": "kid-old", "tokenEndpoint": "https://auth.example/token/",
        "audience": "https://auth.example/issuer/", "clientId": "meshbook-agents",
        "username": "wanderer"}))
    return {"dir": tmp_path, "key": key}


def _fake_jwt(exp: int) -> str:
    body = base64.urlsafe_b64encode(json.dumps({"exp": exp}).encode()).rstrip(b"=").decode()
    return f"eyJhbGciOiJSUzI1NiJ9.{body}.sig"


# ─── bearer_for_call: lane resolution ────────────────────────────────

def test_bearer_mode_returns_stored_token(bench):
    assert agent.bearer_for_call({"token": "mb_token_x"}, cli) == "mb_token_x"


def test_agent_mode_mints_and_caches(bench, monkeypatch):
    mints = []
    monkeypatch.setattr(agent, "_mint_token",
                        lambda cfg, c: mints.append(1) or _fake_jwt(int(time.time()) + 300))
    cfg = {"auth_mode": "agent"}
    t1 = agent.bearer_for_call(cfg, cli)
    t2 = agent.bearer_for_call(cfg, cli)
    assert t1 == t2
    assert len(mints) == 1          # second call served from cache


def test_agent_mode_remints_when_near_expiry(bench, monkeypatch):
    mints = []
    monkeypatch.setattr(agent, "_mint_token",
                        lambda cfg, c: mints.append(1) or _fake_jwt(int(time.time()) + 300))
    cfg = {"auth_mode": "agent"}
    agent.bearer_for_call(cfg, cli)
    agent._TOKEN_CACHE["exp"] = time.time() + 10   # inside the 30 s margin
    agent.bearer_for_call(cfg, cli)
    assert len(mints) == 2


def test_agent_mode_falls_back_to_bearer_on_mint_failure(bench, monkeypatch, capsys):
    def boom(cfg, c):
        raise SystemExit("token mint failed: 503")
    monkeypatch.setattr(agent, "_mint_token", boom)
    cfg = {"auth_mode": "agent", "token": "mb_token_fallback"}
    assert agent.bearer_for_call(cfg, cli) == "mb_token_fallback"
    assert "falling back" in capsys.readouterr().err


def test_agent_mode_without_bearer_propagates_failure(bench, monkeypatch):
    def boom(cfg, c):
        raise SystemExit("token mint failed: 503")
    monkeypatch.setattr(agent, "_mint_token", boom)
    with pytest.raises(SystemExit):
        agent.bearer_for_call({"auth_mode": "agent"}, cli)


def test_agent_mode_unready_falls_through_to_bearer(tmp_path, monkeypatch):
    # auth_mode says agent but no key on disk — the stored bearer must
    # still work rather than the CLI dying on a missing file.
    monkeypatch.setattr(cli, "CONFIG_DIR", tmp_path)
    assert agent.bearer_for_call(
        {"auth_mode": "agent", "token": "mb_token_x"}, cli) == "mb_token_x"


# ─── §99: the rotation proof ─────────────────────────────────────────

def test_rotation_assertion_signed_by_current_key(bench):
    assertion = agent.sign_rotation_assertion(cli, "kid-new")
    head, payload, sig = assertion.split(".")
    pad = lambda s: s + "=" * (-len(s) % 4)  # noqa: E731
    claims = json.loads(base64.urlsafe_b64decode(pad(payload)))
    assert claims["sub"] == "wanderer"
    assert claims["aud"] == "meshbook-agent-rotation"
    assert claims["new_kid"] == "kid-new"
    assert claims["exp"] - claims["iat"] <= 600
    # Signature must verify against the CURRENT (old) public key.
    bench["key"].public_key().verify(
        base64.urlsafe_b64decode(pad(sig)),
        f"{head}.{payload}".encode(), padding.PKCS1v15(), hashes.SHA256())


def test_rotation_assertion_none_without_current_key(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "CONFIG_DIR", tmp_path)
    assert agent.sign_rotation_assertion(cli, "kid-new") is None


# ─── enroll / revoke config flips ────────────────────────────────────

def _enroll_args(force=False):
    return type("A", (), {"force": force})()


def test_enroll_rotation_sends_assertion_and_backs_up(bench, monkeypatch):
    captured = {}

    def fake_api(method, path, *, cfg, body=None, **kw):
        captured.update(path=path, body=body)
        return {"data": {"enrolled": True, "username": "wanderer",
                         "kid": body["publicKey"]["kid"],
                         "tokenEndpoint": "https://auth.example/token/",
                         "audience": "https://auth.example/issuer/",
                         "clientId": "meshbook-agents"}}
    saved = {}
    monkeypatch.setattr(cli, "_api_call", fake_api)
    monkeypatch.setattr(cli, "save_config", lambda c: saved.update(c))
    cfg = {"token": "mb_token_x"}
    rc = agent.cmd_agent_enroll(_enroll_args(force=True), cfg, cli)
    assert rc == 0
    assert captured["path"] == "/api/me/agent-credentials"
    assert "rotationAssertion" in captured["body"]          # §99 proof rode along
    assert saved.get("auth_mode") == "agent"                # §96 flip persisted
    assert (bench["dir"] / "agent-key.pem.bak").exists()    # outgoing key kept
    new_meta = json.loads((bench["dir"] / "agent-key.json").read_text())
    assert new_meta["kid"] != "kid-old"


def test_first_enroll_sends_no_assertion(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(cli, "CONFIG_PATH", tmp_path / "config")
    captured = {}

    def fake_api(method, path, *, cfg, body=None, **kw):
        captured.update(body=body)
        return {"data": {"enrolled": True, "username": "wanderer", "kid": body["publicKey"]["kid"]}}
    monkeypatch.setattr(cli, "_api_call", fake_api)
    monkeypatch.setattr(cli, "save_config", lambda c: None)
    rc = agent.cmd_agent_enroll(_enroll_args(), {"token": "mb_token_x"}, cli)
    assert rc == 0
    assert "rotationAssertion" not in captured["body"]


def test_enroll_without_any_lane_refused(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "CONFIG_DIR", tmp_path)
    rc = agent.cmd_agent_enroll(_enroll_args(), {}, cli)
    assert rc == 2
    assert "mesh agent register" in capsys.readouterr().err


def test_agent_mode_bench_can_rotate_without_bearer(bench, monkeypatch):
    # §96 acceptance shape: no mb_token_ anywhere, auth_mode=agent, and
    # rotation still goes through (transport is stubbed; the point is the
    # command must not demand a bearer).
    def fake_api(method, path, *, cfg, body=None, **kw):
        return {"data": {"enrolled": True, "username": "wanderer",
                         "kid": body["publicKey"]["kid"]}}
    monkeypatch.setattr(cli, "_api_call", fake_api)
    monkeypatch.setattr(cli, "save_config", lambda c: None)
    rc = agent.cmd_agent_enroll(_enroll_args(force=True), {"auth_mode": "agent"}, cli)
    assert rc == 0


def test_revoke_clears_agent_mode(bench, monkeypatch, capsys):
    saved = {}
    monkeypatch.setattr(cli, "_api_call", lambda *a, **k: {})
    monkeypatch.setattr(cli, "save_config", lambda c: saved.update(c))
    cfg = {"auth_mode": "agent", "token": "mb_token_x"}
    args = type("A", (), {"purge_local": False})()
    rc = agent.cmd_agent_revoke(args, cfg, cli)
    assert rc == 0
    assert "auth_mode" not in cfg
    assert "auth_mode" not in saved or saved.get("auth_mode") is None


# ─── wiring ──────────────────────────────────────────────────────────

def test_login_agent_flag_exists(capsys):
    parser = cli.build_parser()
    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["login", "--help"])
    assert exc.value.code == 0
    assert "--agent" in capsys.readouterr().out
