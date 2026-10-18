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
        assert ">=0.4.1,<0.5" in spec, spec


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
    # FastAPI's bare {"detail": ...}: the SDK maps it to http_error, whose default
    # hint is check_status; the CLI still knows a 401 means sign in again
    (Err(401, {"detail": "Invalid token"}), "check_status"),
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
