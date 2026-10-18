"""A routing fake for meshbook-sdk's transport: no network, ever.

``Router`` replaces ``meshbook.client._urlopen`` (the SDK's one transport
seam), so every CLI test exercises the real SDK (headers, envelope parsing,
error mapping, retries, name resolution) with only the socket faked. Routes
match METHOD + URL path (query ignored); a route replays its responses in
order and the last one sticks. Unrouted requests fail loudly, so a test
cannot pass by accident on a call it did not expect.
"""
from __future__ import annotations

import io
import json
import urllib.error
import urllib.parse
from dataclasses import dataclass, field
from typing import Any

BASE = "https://mesh.test"
MESH = "11111111-1111-1111-1111-111111111111"
MESH2 = "22222222-2222-2222-2222-222222222222"
U1 = "aaaaaaaa-0000-0000-0000-000000000001"
U2 = "aaaaaaaa-0000-0000-0000-000000000002"
T1 = "bbbbbbbb-0000-0000-0000-000000000001"
P1 = "cccccccc-0000-0000-0000-000000000001"
C1 = "dddddddd-0000-0000-0000-000000000001"
E1 = "eeeeeeee-0000-0000-0000-000000000001"
A1 = "ffffffff-0000-0000-0000-000000000001"
CH1 = "99999999-0000-0000-0000-000000000001"
L1 = "77777777-0000-0000-0000-000000000001"
CO1 = "66666666-0000-0000-0000-000000000001"


@dataclass
class Err:
    status: int
    payload: Any = None
    headers: dict[str, str] = field(default_factory=dict)


class Resp:
    def __init__(self, body: bytes, headers: dict[str, str] | None = None):
        self._body = body
        self.headers = headers or {}

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> Resp:
        return self

    def __exit__(self, *exc: Any) -> bool:
        return False


def ok(data: Any) -> dict[str, Any]:
    return {"ok": True, "data": data}


def ok_list(items: list[Any], total: int | None = None, **extra: Any) -> dict[str, Any]:
    return {"ok": True, "data": {"items": items,
                                 "total": len(items) if total is None else total, **extra}}


def err(status: int, code: str, message: str = "nope") -> Err:
    return Err(status, {"ok": False, "error": {"code": code, "message": message}})


class Router:
    def __init__(self) -> None:
        self.routes: list[tuple[str, str, list[Any]]] = []
        self.requests: list[Any] = []

    def add(self, method: str, path: str, *responses: Any) -> Router:
        """Add (or REPLACE) the route for METHOD path."""
        method = method.upper()
        self.routes = [r for r in self.routes if not (r[0] == method and r[1] == path)]
        self.routes.append((method, path, list(responses) or [ok({})]))
        return self

    def __call__(self, req: Any, timeout: float) -> Resp:
        self.requests.append(req)
        path = urllib.parse.urlsplit(req.get_full_url()).path
        for method, rpath, responses in self.routes:
            if method == req.get_method() and rpath == path:
                resp = responses.pop(0) if len(responses) > 1 else responses[0]
                if isinstance(resp, Err):
                    body = json.dumps(resp.payload if resp.payload is not None else {}).encode()
                    raise urllib.error.HTTPError(
                        req.get_full_url(), resp.status, "err", resp.headers, io.BytesIO(body)
                    )
                if isinstance(resp, Resp):
                    return resp
                if isinstance(resp, bytes):
                    return Resp(resp)
                return Resp(json.dumps(resp).encode())
        raise AssertionError(f"unrouted request: {req.get_method()} {req.get_full_url()}")

    # ── inspection ──
    def calls(self, method: str | None = None, path: str | None = None) -> list[Any]:
        out = []
        for r in self.requests:
            p = urllib.parse.urlsplit(r.get_full_url()).path
            if (method is None or r.get_method() == method) and (path is None or p == path):
                out.append(r)
        return out

    def one(self, method: str, path: str) -> Any:
        hits = self.calls(method, path)
        assert len(hits) == 1, f"expected one {method} {path}, got {len(hits)}: " + ", ".join(
            f"{r.get_method()} {r.get_full_url()}" for r in self.requests)
        return hits[0]

    @staticmethod
    def body(req: Any) -> Any:
        return json.loads(req.data.decode("utf-8"))

    @staticmethod
    def query(req: Any) -> dict[str, str]:
        return dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(req.get_full_url()).query))

    @staticmethod
    def header(req: Any, name: str) -> str | None:
        # urllib capitalises header names: X-active-mesh-id
        for k, v in req.header_items():
            if k.lower() == name.lower():
                return v
        return None


def meshes_payload(*rows: tuple[str, str]) -> dict[str, Any]:
    return ok_list([{"id": i, "name": n, "meshType": "family", "memberRole": "member"}
                    for i, n in rows])
