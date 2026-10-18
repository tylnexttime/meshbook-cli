"""F3, the CLI half: no direct HTTP in mesh/, and every SDK call in the pinned
OpenAPI. Each guard is also shown to go red on a synthetic violation (a check
that cannot fail proves nothing)."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SNAPSHOT = ROOT / "tests" / "contract" / "openapi.json"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


cli_contract = _load("cli_contract")
cc = _load("contract_check")


def test_no_direct_http_in_mesh(capsys):
    assert cli_contract.main([]) == 0, capsys.readouterr().out


@pytest.mark.parametrize("source", [
    "import urllib.request\n",
    "from urllib import request\n",
    "import requests\n",
    "from http.client import HTTPSConnection\n",
    "def f(ctx):\n    return ctx.client.request('GET', '/api/x')\n",
    "def f(c):\n    return c._call('tasks.list')\n",
    "from meshbook.client import _urlopen\n",
    "def f(c):\n    return c.download_raw('/api/x')\n",
])
def test_the_http_guard_can_fail(tmp_path, source):
    bad = tmp_path / "bad.py"
    bad.write_text(source, encoding="utf-8")
    assert cli_contract.scan(bad), source


def test_urllib_parse_is_not_http(tmp_path):
    ok = tmp_path / "ok.py"
    ok.write_text("import urllib.parse\nfrom urllib.parse import quote\n", encoding="utf-8")
    assert cli_contract.scan(ok) == []


def test_every_sdk_call_is_in_the_pinned_spec(capsys):
    """Strict: the SDK's whole registry (a superset of what the CLI calls)."""
    code = cc.main(["--spec", str(SNAPSHOT), "--registry", "meshbook.endpoints"])
    out = capsys.readouterr().out
    assert code == 0, out
    assert "missing: 0" in out


def test_recorded_cli_calls_mode_works(tmp_path, capsys):
    """The CI step feeds the suite's recorded calls back in with --calls."""
    calls = tmp_path / "calls.json"
    calls.write_text(json.dumps([{"method": "GET", "path": "/api/tasks"},
                                 {"method": "PATCH", "path": "/api/calendar-events/{event_id}"}]),
                     encoding="utf-8")
    assert cc.main(["--spec", str(SNAPSHOT), "--calls", str(calls)]) == 0
    calls.write_text(json.dumps([{"method": "POST", "path": "/api/not-a-route"}]), encoding="utf-8")
    assert cc.main(["--spec", str(SNAPSHOT), "--calls", str(calls)]) == 1
    capsys.readouterr()


def test_the_snapshot_has_provenance():
    meta = json.loads((SNAPSHOT.parent / "openapi.source.json").read_text(encoding="utf-8"))
    assert meta["sha256"] == hashlib.sha256(SNAPSHOT.read_bytes()).hexdigest()
    assert meta["operations"] > 200
