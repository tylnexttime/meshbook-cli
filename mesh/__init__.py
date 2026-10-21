"""meshbook-cli — small-model-friendly CLI for meshbook.org (the `mesh` command)."""
from ._preflight import require_sdk

require_sdk()  # before .cli: every command module imports the SDK at import time

from ._version import VERSION  # noqa: E402
from .cli import main  # noqa: E402

__version__ = VERSION
__all__ = ["VERSION", "main"]
