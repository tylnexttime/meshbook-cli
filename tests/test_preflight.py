"""The SDK preflight: a missing or too-old meshbook SDK is a named state, not a traceback.

Each case runs in a fresh interpreter, because the state under test is
"what happens when ``import mesh`` meets this environment".
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

from mesh import _preflight

ROOT = Path(__file__).resolve().parents[1]


def _run(setup: str, *argv: str) -> subprocess.CompletedProcess:
    code = f"import sys; {setup}; sys.argv = ['mesh', *{list(argv)!r}]; import mesh"
    return subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=60)


def test_missing_sdk_is_named_and_exits_3():
    r = _run("sys.modules['meshbook'] = None", "whoami")
    assert r.returncode == 3
    assert r.stderr.startswith("MESHBOOK_SDK_MISSING: the meshbook SDK (package meshbook-sdk) is not installed")
    assert "-m pip install" in r.stderr
    assert "Traceback" not in r.stderr


def test_missing_sdk_under_json_is_one_parseable_line():
    r = _run("sys.modules['meshbook'] = None", "--json", "whoami")
    assert r.returncode == 3
    err = json.loads(r.stderr)["error"]
    assert err["code"] == "missing_dependency"
    assert err["state"] == "MESHBOOK_SDK_MISSING"


def test_too_old_sdk_is_named_too():
    fake = "import types; m = types.ModuleType('meshbook'); m.VERSION = '0.3.0'; sys.modules['meshbook'] = m"
    r = _run(fake, "whoami")
    assert r.returncode == 3
    assert "MESHBOOK_SDK_MISSING: the meshbook SDK here is 0.3.0, older than the 0.4.2" in r.stderr


def test_healthy_sdk_passes_silently():
    assert _preflight.sdk_problem() is None
    r = _run("pass")
    assert r.returncode == 0, r.stderr
    assert r.stderr == ""


def test_floor_matches_pyproject():
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    floor = re.search(r'^\s*"meshbook-sdk>=([\d.]+)', text, re.M).group(1)
    assert tuple(int(n) for n in floor.split(".")) == _preflight.MIN_SDK
