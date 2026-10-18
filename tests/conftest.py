"""Suite-wide guards for meshbook-cli.

1. **Isolation.** No test reads or writes a real ``~/.meshbook``:
   MESHBOOK_CONFIG_DIR is a fresh temp dir holding a signed-in config
   (``mb_token_test``, base ``https://mesh.test``, active mesh MESH), and the
   other MESHBOOK_* variables are cleared. A test that wants something else
   rewrites it.

2. **No network.** ``meshbook.client._urlopen`` (the SDK's transport seam) is
   a ``Router`` for every test; anything unrouted fails the test.

3. **Every request is a registered SDK endpoint.** The SDK calls
   ``_on_request(method, url)`` for each request; the hook asserts the
   (method, path) matches ``meshbook.endpoints.ENDPOINTS`` (public since SDK
   0.4.1; 0.12.0 imported the private ``_endpoints``), so a CLI verb
   that bypassed the SDK's registry (the raw ``request()`` escape hatch)
   fails here. Every request seen is also recorded: when
   ``MESH_CONTRACT_CALLS`` names a file, the session writes the distinct
   (method, path-template) pairs there for ``scripts/contract_check.py
   --calls`` (the CI contract step).
"""
from __future__ import annotations

import json
import os
import re
import urllib.parse
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import meshbook.client as mc
import pytest
from meshbook.endpoints import ENDPOINTS

from tests._fake import BASE, MESH, Router, meshes_payload

_TEMPLATES = [
    (ep.method, ep.path,
     re.compile("^" + re.sub(r"\\\{[^}]+\\\}", "[^/]+", re.escape(ep.path)) + "$"))
    for ep in ENDPOINTS.values()
]

#: (METHOD, path template) for every request the whole suite sent.
SEEN: set[tuple[str, str]] = set()


def _template(method: str, url: str) -> str | None:
    path = urllib.parse.urlsplit(url).path
    for m, template, rx in _TEMPLATES:
        if m == method and rx.match(path):
            return template
    return None


def write_config(cfg: dict[str, Any]) -> Path:
    path = Path(os.environ["MESHBOOK_CONFIG_DIR"]) / "config"
    path.write_text(json.dumps(cfg), encoding="utf-8")
    return path


def read_config() -> dict[str, Any]:
    path = Path(os.environ["MESHBOOK_CONFIG_DIR"]) / "config"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


DEFAULT_CONFIG = {"token": "mb_token_test", "base": BASE, "active_mesh_id": MESH}


@pytest.fixture(autouse=True)
def _isolated_env(tmp_path_factory, monkeypatch):
    d = tmp_path_factory.mktemp("meshbook-config")
    monkeypatch.setenv("MESHBOOK_CONFIG_DIR", str(d))
    for var in ("MESHBOOK_TOKEN", "MESHBOOK_BASE", "MESHBOOK_MESH"):
        monkeypatch.delenv(var, raising=False)
    write_config(DEFAULT_CONFIG)
    yield d


@pytest.fixture(autouse=True)
def api(monkeypatch) -> Router:
    """The fake transport. Knows one route by default: your mesh list."""
    router = Router()
    router.add("GET", "/api/meshes", meshes_payload((MESH, "Test Mesh")))
    monkeypatch.setattr(mc, "_urlopen", router)
    sleeps: list[float] = []
    monkeypatch.setattr(mc, "_sleep", sleeps.append)
    router.sleeps = sleeps  # type: ignore[attr-defined]

    def hook(method: str, url: str) -> None:
        template = _template(method, url)
        assert template is not None, (
            f"{method} {urllib.parse.urlsplit(url).path} is not a meshbook-sdk registry "
            "endpoint: every CLI request must go through an SDK namespace method"
        )
        SEEN.add((method, template))

    monkeypatch.setattr(mc, "_on_request", hook)
    return router


@dataclass
class Result:
    rc: int
    out: str
    err: str

    def json(self) -> Any:
        return json.loads(self.out)

    def err_json(self) -> Any:
        return json.loads(self.err.strip().splitlines()[-1])


@pytest.fixture()
def run(capsys) -> Callable[..., Result]:
    """``run("tasks", "list")`` → Result(rc, out, err), through cli.main()."""
    from mesh import cli

    def _run(*argv: str) -> Result:
        try:
            rc = cli.main(list(argv))
        except SystemExit as e:  # argparse --help / usage errors
            rc = int(e.code or 0)
        cap = capsys.readouterr()
        return Result(rc, cap.out, cap.err)

    return _run


def pytest_sessionfinish(session, exitstatus):
    target = os.environ.get("MESH_CONTRACT_CALLS")
    if not target:
        return
    calls = [{"method": m, "path": p} for m, p in sorted(SEEN)]
    Path(target).parent.mkdir(parents=True, exist_ok=True)
    Path(target).write_text(json.dumps(calls, indent=2) + "\n", encoding="utf-8")
