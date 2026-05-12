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
                "notifications"):
        assert cmd in out


def test_chat_subcommands_present(capsys):
    parser = cli.build_parser()
    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["chat", "--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    for sub in ("post", "list", "attach", "react", "unreact"):
        assert sub in out


def test_channels_subcommands_present(capsys):
    """§31 sweep — channel verbs landed in v0.2.0."""
    parser = cli.build_parser()
    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["channels", "--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    for sub in ("list", "read", "post", "reply", "create"):
        assert sub in out


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
    monkeypatch.setattr(cli, "_list_channels_raw", lambda cfg, mesh_id=None: [
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
