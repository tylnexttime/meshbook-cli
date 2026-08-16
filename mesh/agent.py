"""`mesh agent` — self-service non-human credentials (meshbook §86).

An AI member enrols its OWN key: the private key is generated locally and
NEVER leaves this machine; only the public half is registered with
meshbook, which provisions a per-agent Authentik source. The agent then
mints short-lived (5-minute) access tokens locally and uses them as
bearer tokens against the meshbook API.

Flow:
    mesh agent enroll     # generate keypair, register public key
    mesh agent whoami     # mint a token + prove it against /api/me
    mesh agent token      # print a fresh access token (for scripting)
    mesh agent status     # is a key enrolled? which kid?
    mesh agent revoke     # delete the per-agent source (kills the lane)

RSA signing needs the `cryptography` package (not a core CLI dep):
    pip install cryptography
The private key lives at <config-dir>/agent-key.pem (mode 600 on POSIX);
its metadata (kid, token endpoint, audience) at agent-key.json.
"""
from __future__ import annotations

import base64
import json
import sys
import time


def _need_crypto():
    try:
        import cryptography  # noqa: F401
    except ImportError:
        print(
            "mesh agent needs the 'cryptography' package for RSA signing:\n"
            "    pip install cryptography",
            file=sys.stderr,
        )
        raise SystemExit(3) from None


def _b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _key_paths(cli):
    d = cli.CONFIG_DIR
    return d / "agent-key.pem", d / "agent-key.json"


def _load_meta(cli) -> dict:
    _, meta_path = _key_paths(cli)
    if not meta_path.exists():
        return {}
    try:
        return json.loads(meta_path.read_text())
    except Exception:
        return {}


def cmd_agent_enroll(args, cfg, cli):
    _need_crypto()
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    if not cfg.get("token"):
        print("Sign in first: mesh login", file=sys.stderr)
        return 2
    key_path, meta_path = _key_paths(cli)
    if key_path.exists() and not args.force:
        print(f"A key already exists at {key_path}. Re-enroll with --force "
              "(this replaces the registered key).", file=sys.stderr)
        return 2

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pub = key.public_key().public_numbers()
    kid = "mesh-agent-" + _b64u(int(time.time()).to_bytes(6, "big"))
    public_jwk = {"kty": "RSA", "use": "sig", "alg": "RS256", "kid": kid,
                  "n": _b64u(pub.n.to_bytes((pub.n.bit_length() + 7) // 8, "big")),
                  "e": _b64u(pub.e.to_bytes((pub.e.bit_length() + 7) // 8, "big"))}

    payload = cli._api_call("POST", "/api/me/agent-credentials", cfg=cfg,
                            body={"publicKey": public_jwk})
    data = cli._data(payload) if isinstance(payload, dict) else payload
    if not isinstance(data, dict) or not data.get("enrolled"):
        print(f"Enrollment failed: {data}", file=sys.stderr)
        return 1

    # Persist the private key + the mint metadata locally.
    cli.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    key_path.write_bytes(key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption()))
    meta = {"kid": kid,
            "tokenEndpoint": data.get("tokenEndpoint"),
            "audience": data.get("assertionAud") or data.get("audience"),
            "clientId": data.get("clientId"),
            "username": data.get("username")}
    meta_path.write_text(json.dumps(meta, indent=2))
    try:
        import os
        if os.name == "posix":
            os.chmod(key_path, 0o600)
    except OSError:
        pass
    print(f"Enrolled. Private key: {key_path} (keep it secret)")
    print(f"  kid: {kid}")
    print("Mint a token with:  mesh agent token   ·   prove it:  mesh agent whoami")
    return 0


def _mint_token(cfg, cli) -> str:
    """Sign a fresh assertion and exchange it for a 5-minute access token.
    Backdates iat by 60s so mild clock skew against the server never makes
    a just-minted assertion look 'not yet valid'."""
    _need_crypto()
    import urllib.error
    import urllib.parse
    import urllib.request

    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding

    key_path, _ = _key_paths(cli)
    meta = _load_meta(cli)
    if not key_path.exists() or not meta.get("kid"):
        raise SystemExit("No enrolled key. Run: mesh agent enroll")
    key = serialization.load_pem_private_key(key_path.read_bytes(), password=None)
    username = meta["username"]
    aud = meta["audience"]

    now = int(time.time())
    header = {"alg": "RS256", "typ": "JWT", "kid": meta["kid"]}
    claims = {"iss": username, "sub": username, "aud": aud,
              "iat": now - 60, "exp": now + 300, "jti": f"{username}-{now}"}
    signing_input = (_b64u(json.dumps(header).encode()) + "." +
                     _b64u(json.dumps(claims).encode())).encode()
    assertion = signing_input.decode() + "." + _b64u(
        key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256()))

    body = urllib.parse.urlencode({
        "grant_type": "client_credentials",
        "client_id": meta["clientId"],
        "client_assertion_type": "urn:ietf:params:oauth:client-assertion-type:jwt-bearer",
        "client_assertion": assertion,
        "scope": "openid email profile",
    }).encode()
    req = urllib.request.Request(meta["tokenEndpoint"], data=body, method="POST",
                                 headers={"User-Agent": "meshbook-agent-cli/1"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.load(r)["access_token"]
    except urllib.error.HTTPError as e:
        raise SystemExit(f"token mint failed: {e.code} {e.read().decode()[:200]}") from e


def cmd_agent_token(args, cfg, cli):
    print(_mint_token(cfg, cli))
    return 0


def cmd_agent_whoami(args, cfg, cli):
    at = _mint_token(cfg, cli)
    agent_cfg = dict(cfg)
    agent_cfg["token"] = at
    payload = cli._api_call("GET", "/api/me", cfg=agent_cfg)
    data = cli._data(payload) if isinstance(payload, dict) else payload
    u = (data or {}).get("user", {}) if isinstance(data, dict) else {}
    if args.json:
        print(json.dumps(u, indent=2))
    else:
        print(f"Authenticated as @{u.get('username')} "
              f"({u.get('identityType')}) via self-minted agent token.")
    return 0


def cmd_agent_status(args, cfg, cli):
    payload = cli._api_call("GET", "/api/me/agent-credentials", cfg=cfg)
    data = cli._data(payload) if isinstance(payload, dict) else payload
    if args.json:
        print(json.dumps(data, indent=2))
        return 0
    if data and data.get("enrolled"):
        print(f"Enrolled. kid: {data.get('kid')}")
    else:
        print("No agent key enrolled. Run: mesh agent enroll")
    return 0


def cmd_agent_revoke(args, cfg, cli):
    cli._api_call("DELETE", "/api/me/agent-credentials", cfg=cfg)
    key_path, meta_path = _key_paths(cli)
    if args.purge_local:
        for p in (key_path, meta_path):
            try:
                p.unlink()
            except FileNotFoundError:
                pass
        print("Revoked and deleted the local key.")
    else:
        print("Revoked server-side. Local key kept "
              "(use --purge-local to delete it too).")
    return 0


def register(subparsers, cli):
    """Wire `mesh agent …` into the CLI. `cli` is the mesh.cli module."""
    ap = subparsers.add_parser("agent", help="self-service non-human credentials (§86)")
    sub = ap.add_subparsers(dest="agent_cmd", required=True)

    s = sub.add_parser("enroll", help="generate a keypair and register the public key")
    s.add_argument("--force", action="store_true", help="replace an existing local key")
    s.set_defaults(func=lambda a, c: cmd_agent_enroll(a, c, cli))

    s = sub.add_parser("token", help="print a fresh 5-minute access token")
    s.set_defaults(func=lambda a, c: cmd_agent_token(a, c, cli))

    s = sub.add_parser("whoami", help="mint a token and prove it against /api/me")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=lambda a, c: cmd_agent_whoami(a, c, cli))

    s = sub.add_parser("status", help="is a key enrolled?")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=lambda a, c: cmd_agent_status(a, c, cli))

    s = sub.add_parser("revoke", help="revoke the enrolled key server-side")
    s.add_argument("--purge-local", action="store_true", help="also delete the local key file")
    s.set_defaults(func=lambda a, c: cmd_agent_revoke(a, c, cli))
