"""meshbook-cli 0.12.3: legacy JSON keys are documented, with a removal date.

Alder's re-test (Tentyl ty-06d2b52d01a5): `members list --json` carries both
`items` and the 0.12.1 key `members`. The alias stays for now; 0.12.3 names
its removal (0.13.0) in the CHANGELOG's Deprecations and in the README.

The second guard audits every `ctx.listing(...)` call: an `extra` key that is
not in KNOWN_EXTRAS fails here, so a new listing cannot grow a legacy key
without someone deciding (and documenting) it.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Extra keys a listing envelope may carry beside the common ones, and why.
KNOWN_EXTRAS = {
    "activeMeshId": "meshes list: which row is active",
    "mesh": "members list: the mesh's name",
    "meshId": "members list: the mesh's id",
    "pendingInvites": "members list: invitations not yet answered",
    "pendingRequests": "members list: join requests not yet answered",
    "semantic": "chat search: false when the embedding arm was down",
    "truncated": "events list: the server capped an expanded window",
    "expanded": "events list: occurrences rather than series",
    "tz": "events list: the zone the window was read in",
    "members": "DEPRECATED alias of items on members list; removed in 0.13.0",
}
DEPRECATED = {"members": "0.13.0"}


def _section(text: str, version: str) -> str:
    m = re.search(rf"^## \[{re.escape(version)}\].*?(?=^## \[)", text, re.M | re.S)
    assert m, f"no CHANGELOG section for {version}"
    return m.group(0)


def test_changelog_names_the_alias_removal():
    text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    sec = _section(text, "0.12.3")
    m = re.search(r"^### Deprecations\n(.*?)(?=^### |\Z)", sec, re.M | re.S)
    assert m, "0.12.3 has no Deprecations heading"
    for key, removal in DEPRECATED.items():
        assert f"`{key}`" in m.group(1) and removal in m.group(1), key


def test_readme_names_the_alias_removal():
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    for key, removal in DEPRECATED.items():
        assert re.search(rf"`{key}`[^\n]*deprecated[^\n]*{re.escape(removal)}", text, re.I), key


def _listing_extra_keys() -> dict[str, set[str]]:
    """Every literal key passed as a listing's extra, per source file."""
    found: dict[str, set[str]] = {}
    for path in sorted((ROOT / "mesh").glob("cmd_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        dicts: dict[str, ast.Dict] = {}
        for node in ast.walk(tree):
            if (isinstance(node, ast.Assign) and isinstance(node.value, ast.Dict)
                    and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)):
                dicts[node.targets[0].id] = node.value
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "listing"):
                continue
            for kw in node.keywords:
                if kw.arg != "extra":
                    continue
                value = kw.value
                if isinstance(value, ast.Name):
                    value = dicts.get(value.id)
                assert isinstance(value, ast.Dict), f"{path.name}: extra= is not a literal dict"
                for k in value.keys:
                    assert isinstance(k, ast.Constant), f"{path.name}: computed extra key"
                    found.setdefault(path.name, set()).add(k.value)
    return found


def test_every_listing_extra_is_known():
    found = _listing_extra_keys()
    assert found, "the scan found no listing extras at all"
    unknown = {f: keys - set(KNOWN_EXTRAS) for f, keys in found.items() if keys - set(KNOWN_EXTRAS)}
    assert not unknown, f"undocumented listing extras: {unknown}"
    # the only legacy key today is the members alias
    seen = set().union(*found.values())
    assert DEPRECATED.keys() <= seen
