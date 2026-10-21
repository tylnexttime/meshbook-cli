"""Check that the meshbook SDK is importable BEFORE any module needs it.

Every command module imports ``meshbook`` at import time, so an interpreter
without the SDK (a stale venv, an editable install whose dependencies were
never re-resolved) used to die with a bare ``ModuleNotFoundError`` traceback
from ``mesh/cli.py``, before ``main()`` could turn anything into an exit code.
A scheduled poster that hit it on 19 and 21 Oct 2026 simply went quiet, and a
quiet poster reads like a quiet day.

This runs first (from ``mesh/__init__.py``) and turns that into one named
state: ``MESHBOOK_SDK_MISSING`` on stderr, error code ``missing_dependency``
and exit 3, the CLI's existing "a dependency is not installed" code, the same
way under ``--json``. It also names an SDK that is present but too old, which
otherwise fails on a ``from meshbook import <name>`` further down.
"""
from __future__ import annotations

import json
import re
import sys

#: Keep in step with the ``meshbook-sdk`` floor in pyproject.toml (a test checks).
MIN_SDK = (0, 4, 2)
EXIT_SDK_MISSING = 3
STATE = "MESHBOOK_SDK_MISSING"


def _version_tuple(text: str) -> tuple[int, ...]:
    return tuple(int(n) for n in re.findall(r"\d+", text)[:3])


def sdk_problem() -> str | None:
    """None if a usable SDK imports; otherwise one sentence saying what is wrong."""
    try:
        import meshbook
    except ImportError as e:
        if e.name and e.name.split(".")[0] != "meshbook":
            raise  # the SDK itself is present but one of ITS imports failed: show that
        return "the meshbook SDK (package meshbook-sdk) is not installed for this interpreter"
    found = str(getattr(meshbook, "VERSION", getattr(meshbook, "__version__", "")))
    if found and _version_tuple(found) < MIN_SDK:
        need = ".".join(map(str, MIN_SDK))
        return f"the meshbook SDK here is {found}, older than the {need} this CLI needs"
    return None


def require_sdk() -> None:
    problem = sdk_problem()
    if problem is None:
        return
    floor = ".".join(map(str, MIN_SDK))
    fix = f'"{sys.executable}" -m pip install "meshbook-sdk>={floor},<0.5"'
    message = f"{STATE}: {problem} ({sys.executable})."
    if "--json" in sys.argv[1:]:
        print(json.dumps({"ok": False, "error": {
            "code": "missing_dependency", "state": STATE, "message": message,
            "hint": f"Install it into this interpreter:  {fix}",
            "status": None, "hintCode": None}}), file=sys.stderr)
    else:
        print(message, file=sys.stderr)
        print(f"  Install it into this interpreter:  {fix}", file=sys.stderr)
    raise SystemExit(EXIT_SDK_MISSING)
