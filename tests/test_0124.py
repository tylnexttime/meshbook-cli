"""meshbook-cli 0.12.4: the version follows pyproject.toml even when install metadata is stale.

An editable install writes its metadata once. On 22 Oct 2026 the BGGR-K
workspace venv reported 0.6.0 while running 0.12.3's code, because the
editable install predated eight releases. From a source checkout, the
checkout's pyproject.toml now wins; a wheel install still reads metadata.
"""
from __future__ import annotations

import importlib.metadata

from mesh import _version
from tests.test_cli_smoke import _pyproject_version


def test_checkout_beats_stale_editable_metadata(monkeypatch):
    monkeypatch.setattr(importlib.metadata, "version", lambda name: "0.6.0")
    assert _version._installed_version() == "0.6.0"  # the stale metadata is really in play
    assert (_version._checkout_version() or _version._installed_version()) == _pyproject_version()


def test_module_version_is_pyprojects():
    assert _version.VERSION == _pyproject_version()


def test_wheel_install_falls_back_to_metadata(monkeypatch, tmp_path):
    monkeypatch.setattr(_version, "_PYPROJECT", tmp_path / "pyproject.toml")  # no file beside the package
    monkeypatch.setattr(importlib.metadata, "version", lambda name: "9.9.9")
    assert _version._checkout_version() is None
    assert _version._installed_version() == "9.9.9"


def test_someone_elses_pyproject_is_ignored(monkeypatch, tmp_path):
    other = tmp_path / "pyproject.toml"
    other.write_text('[project]\nname = "not-meshbook"\nversion = "1.2.3"\n', encoding="utf-8")
    monkeypatch.setattr(_version, "_PYPROJECT", other)
    assert _version._checkout_version() is None


def test_version_outside_project_table_is_not_read(monkeypatch, tmp_path):
    f = tmp_path / "pyproject.toml"
    f.write_text('[tool.x]\nversion = "6.6.6"\n\n[project]\nname = "meshbook-cli"\nversion = "1.0.0"\n',
                 encoding="utf-8")
    monkeypatch.setattr(_version, "_PYPROJECT", f)
    assert _version._checkout_version() == "1.0.0"
