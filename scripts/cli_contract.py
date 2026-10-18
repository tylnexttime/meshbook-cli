#!/usr/bin/env python3
"""cli_contract — the CLI half of the contract check (PLAN 2026-10-19, F3).

Since 0.12.0 meshbook-cli sends nothing itself: every request goes through a
meshbook-sdk namespace method, and the SDK sends every request through its
endpoint registry (``meshbook.endpoints``). So the CLI's contract is two
claims, both checked here and in CI:

1. **No direct HTTP in ``mesh/``.** No module imports an HTTP or socket
   library (urllib.request, http.client, requests, httpx, ...), and none
   calls the SDK's unchecked escape hatches (``client.request()``,
   ``client.download_raw()``) or its private transport (``_call``, ``_send``,
   ``_list``, ``_download``, ``_urlopen``). This file does that by AST.

2. **Every SDK call the CLI makes exists in the server's OpenAPI.** That is
   the vendored ``scripts/contract_check.py`` (copied byte-for-byte from
   meshbook-sdk 0.4.1, ``scripts/contract_check.py``, unchanged since 0.4.0), run twice in CI
   against the pinned ``tests/contract/openapi.json`` (copied from the SDK's
   pin; refresh with ``scripts/refresh_openapi.py``):

   * ``--registry meshbook.endpoints``: every call the SDK can make (a
     superset of the CLI's);
   * ``--calls build/contract-calls.json``: exactly the (method, path)
     pairs the CLI test-suite sent, recorded by tests/conftest.py when
     ``MESH_CONTRACT_CALLS`` is set. conftest also fails any test whose
     request is not a registered SDK endpoint.

Exit status: 0 clean, 1 a violation (each printed with file:line).

  python scripts/cli_contract.py            # scans mesh/
  python scripts/cli_contract.py --path X   # scans another tree
"""
from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

#: Modules that put bytes on a network. Importing any of them in mesh/ is a
#: violation: the SDK is the only HTTP client.
BANNED_IMPORTS = (
    "urllib.request", "urllib.error", "urllib3", "http.client", "http.cookiejar",
    "requests", "httpx", "aiohttp", "socket", "ssl", "websocket", "websockets",
)

#: Client attributes that bypass the registry (unchecked raw paths) or reach
#: into the SDK's private transport.
BANNED_ATTRS = (
    "request", "download_raw", "_call", "_send", "_list", "_download", "_urlopen",
    "_on_request",
)


def _banned_module(name: str) -> str | None:
    for banned in BANNED_IMPORTS:
        if name == banned or name.startswith(banned + "."):
            return banned
    if name == "urllib":
        return "urllib"  # `import urllib` then urllib.request.urlopen(...)
    return None


def scan(root: Path) -> list[str]:
    """Return one line per violation under *root*."""
    problems: list[str] = []
    files = [root] if root.is_file() else sorted(root.rglob("*.py"))
    for file in files:
        tree = ast.parse(file.read_text(encoding="utf-8"), filename=str(file))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    hit = _banned_module(alias.name)
                    if hit:
                        problems.append(f"{file}:{node.lineno}: imports {alias.name} (direct HTTP)")
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                hit = _banned_module(mod)
                if hit is None and mod == "urllib":
                    names = {a.name for a in node.names}
                    if names & {"request", "error"}:
                        hit = "urllib"
                if hit:
                    problems.append(f"{file}:{node.lineno}: imports from {mod} (direct HTTP)")
                if mod.startswith("meshbook") and any(a.name == "_urlopen" for a in node.names):
                    problems.append(f"{file}:{node.lineno}: imports the SDK's private transport")
            elif isinstance(node, ast.Attribute) and node.attr in BANNED_ATTRS:
                # `ctx.client.request(...)`, `c._call(...)`, `mc._urlopen = ...`
                problems.append(
                    f"{file}:{node.lineno}: uses .{node.attr} (bypasses the SDK's endpoint registry)"
                )
    return problems


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="cli_contract", description=__doc__.split("\n\n")[0])
    default = Path(__file__).resolve().parent.parent / "mesh"
    ap.add_argument("--path", type=Path, default=default, help="tree to scan (default: mesh/)")
    args = ap.parse_args(argv)
    problems = scan(args.path)
    if problems:
        print(f"cli_contract: {len(problems)} violation(s): the CLI must reach the network "
              "only through meshbook-sdk namespace methods")
        for p in problems:
            print("  " + p)
        return 1
    count = sum(1 for _ in (args.path.rglob("*.py") if args.path.is_dir() else [args.path]))
    print(f"cli_contract: OK - {count} file(s) under {args.path}, no direct HTTP, "
          "no registry bypass")
    return 0


if __name__ == "__main__":
    sys.exit(main())
