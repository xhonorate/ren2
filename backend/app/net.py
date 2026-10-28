"""LAN serving: origin policy, optional access gate, address discovery.

This server writes to your filesystem, spawns Ren'Py and Kohya, and talks to an
unauthenticated ComfyUI. On a home network that is usually fine, but it is not
something to expose beyond one. Binding to 0.0.0.0 is therefore explicit, and
an optional shared PIN is available for shared or untrusted networks.
"""
from __future__ import annotations

import ipaddress
import os
import re
import socket
from typing import Optional

from fastapi import Request
from fastapi.responses import JSONResponse

# localhost, the three RFC1918 private ranges, link-local, and mDNS .local
LAN_ORIGIN_RE = re.compile(
    r"^https?://("
    r"localhost|127(?:\.\d+){3}|\[::1\]|"
    r"10(?:\.\d+){3}|"
    r"192\.168(?:\.\d+){2}|"
    r"172\.(?:1[6-9]|2\d|3[01])(?:\.\d+){2}|"
    r"169\.254(?:\.\d+){2}|"
    r"[A-Za-z0-9-]+\.local"
    r")(?::\d+)?$"
)

TOKEN_ENV = "VN_ACCESS_TOKEN"
COOKIE_NAME = "vn_access"

# Reachable without the PIN: the health probe, and the login endpoint itself.
OPEN_PATHS = {"/api/health", "/api/auth/login", "/api/auth/status"}


def access_token() -> Optional[str]:
    token = (os.environ.get(TOKEN_ENV) or "").strip()
    return token or None


def auth_enabled() -> bool:
    return access_token() is not None


def is_authorised(request: Request) -> bool:
    token = access_token()
    if token is None:
        return True
    if request.cookies.get(COOKIE_NAME) == token:
        return True
    header = request.headers.get("x-vn-token")
    if header and header == token:
        return True
    # EventSource and <img src> cannot set headers; a query param covers them
    # for clients that have not stored the cookie yet.
    return request.query_params.get("token") == token


async def gate(request: Request, call_next):
    """Reject unauthenticated calls when a PIN is configured."""
    if not auth_enabled():
        return await call_next(request)

    path = request.url.path
    needs_auth = path.startswith("/api") and path not in OPEN_PATHS
    if needs_auth and not is_authorised(request):
        return JSONResponse({"detail": "access token required"}, status_code=401)
    return await call_next(request)


# --------------------------------------------------------------------------
# address discovery
# --------------------------------------------------------------------------

def lan_addresses() -> list[str]:
    """Every private IPv4 address this machine answers on."""
    found: set[str] = set()

    # The address used to reach an off-box route is the one a phone will want.
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("10.255.255.255", 1))
        found.add(probe.getsockname()[0])
    except OSError:
        pass
    finally:
        probe.close()

    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            found.add(info[4][0])
    except OSError:
        pass

    usable: list[str] = []
    for address in found:
        try:
            parsed = ipaddress.ip_address(address)
        except ValueError:
            continue
        if parsed.is_private and not parsed.is_loopback and not parsed.is_link_local:
            usable.append(address)
    return sorted(usable)


def describe(host: str, port: int) -> str:
    lines = ["", "  VN Authoring Tool", ""]
    if host in ("0.0.0.0", "::"):
        lines.append(f"    this computer   http://localhost:{port}")
        addresses = lan_addresses()
        if addresses:
            for address in addresses:
                lines.append(f"    phone / tablet  http://{address}:{port}")
        else:
            lines.append("    (no private network address found — is Wi-Fi connected?)")
    else:
        lines.append(f"    http://{host}:{port}")

    lines.append("")
    if auth_enabled():
        lines.append("    PIN required (VN_ACCESS_TOKEN is set)")
    elif host in ("0.0.0.0", "::"):
        lines.append("    No PIN set — anyone on this network can open the editor.")
        lines.append(f"    Set {TOKEN_ENV} to require one.")
    lines.append("")
    return "\n".join(lines)
