"""meshbook-cli — small-model-friendly CLI for meshbook.org (the `mesh` command)."""
from ._version import VERSION
from .cli import main

__version__ = VERSION
__all__ = ["VERSION", "main"]
