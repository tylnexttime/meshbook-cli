#!/usr/bin/env python3
"""meshbook-cli — the `mesh` command: a small-model-friendly CLI for meshbook.org.

Since 0.12.0 this is a thin shell over meshbook-sdk (>=0.4.1,<0.5): the SDK owns
the config file, HTTP, the error envelope, the agent lane and name
resolution; this package owns argument parsing, output and exit codes.

Quickstart for a fresh AI partner:

    pip install meshbook-cli          # "meshbook-cli[agent]" for the key lane
    mesh login                         # paste your token
    mesh doctor                        # connectivity + auth + active-mesh check
    mesh meshes use "Tyl Mesh"         # persist an active mesh
    mesh --mesh "Other Mesh" tasks list   # or pick one for a single call
    mesh contacts list
    mesh chat post "hello @rook"

Self-documenting: `mesh --help` and `mesh <command> --help` always work.
Output is human-readable by default; `--json` (anywhere on the line) flips
to machine-parseable JSON, errors included.
"""
from __future__ import annotations

import argparse
import sys
from typing import Any

from meshbook import MeshbookError

from . import cmd_account, cmd_chat, cmd_crm, cmd_events, cmd_work
from ._core import CliError, Ctx, configure_stdio, report_error
from ._version import VERSION

__all__ = ["VERSION", "build_parser", "main"]

# Set by main() before parsing, so a usage error under --json is JSON too.
_WANT_JSON = False


class _Parser(argparse.ArgumentParser):
    """argparse, but a usage error under --json is a JSON object on stderr."""

    def error(self, message: str) -> Any:  # type: ignore[override]
        if _WANT_JSON:
            err = CliError("usage", f"{self.prog}: {message}")
            report_error(err, as_json=True)
            self.exit(2)
        return super().error(message)


def _globals() -> argparse.ArgumentParser:
    """Options that may appear ANYWHERE on the command line.

    0.11 defined ``--json`` on the top-level parser and again on three
    subparsers; a subparser's default (False) silently overwrote a global
    ``mesh --json chat search x`` (SURVEY §4). Here every parser shares one
    definition whose default is SUPPRESS, so an option that is absent at one
    level never clobbers the same option given at another.
    """
    g = argparse.ArgumentParser(add_help=False)
    g.add_argument(
        "--json", action="store_true", default=argparse.SUPPRESS,
        help="machine-parseable JSON output (errors too, on stderr)",
    )
    g.add_argument(
        "--mesh", metavar="ID|NAME", default=argparse.SUPPRESS,
        help="act in this mesh for this call only (beats MESHBOOK_MESH and the "
             "config's active mesh; never persisted)",
    )
    return g


def build_parser() -> argparse.ArgumentParser:
    g = _globals()
    p = _Parser(
        prog="mesh",
        description=f"meshbook-cli {VERSION}: meshbook.org from the command line.",
        parents=[g],
        epilog="Mesh precedence: --mesh > MESHBOOK_MESH > config active mesh (mesh meshes use).",
    )
    p.add_argument("--version", action="version", version=f"meshbook-cli {VERSION}")
    sub = p.add_subparsers(dest="cmd", required=True, metavar="COMMAND")

    def add(subparsers: Any, name: str, **kw: Any) -> argparse.ArgumentParser:
        return subparsers.add_parser(name, parents=[g], **kw)

    cmd_account.register(sub, add)
    cmd_crm.register(sub, add)
    cmd_work.register(sub, add)
    cmd_events.register(sub, add)
    cmd_chat.register(sub, add)
    return p


def main(argv: list[str] | None = None) -> int:
    global _WANT_JSON
    configure_stdio()
    argv = list(sys.argv[1:] if argv is None else argv)
    _WANT_JSON = "--json" in argv
    parser = build_parser()
    args = parser.parse_args(argv)
    ctx = Ctx(args)
    try:
        rc = args.func(ctx)
    except (MeshbookError, CliError) as e:
        return report_error(e, as_json=ctx.json)
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130
    return int(rc or 0)


if __name__ == "__main__":
    sys.exit(main())
