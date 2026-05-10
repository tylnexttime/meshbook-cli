"""Smoke tests for meshbook-cli — argparse wiring + config persistence.

Network-dependent commands aren't exercised here (the CLI calls
meshbook.org directly via stdlib urllib). For end-to-end coverage,
mint a token and run `mesh doctor` against a real or staging server.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

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
                "meshes", "contacts", "chat", "notifications"):
        assert cmd in out


def test_chat_subcommands_present(capsys):
    parser = cli.build_parser()
    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["chat", "--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    for sub in ("post", "list", "attach"):
        assert sub in out


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
