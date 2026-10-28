"""LAN serving: origin policy and the optional shared PIN."""
from __future__ import annotations

import importlib
import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _fresh_client(pin: str | None):
    """A client with the auth env applied before the app is imported."""
    tmp = tempfile.mkdtemp(prefix="vn_net_")
    os.environ["VN_DATA_DIR"] = tmp
    if pin is None:
        os.environ.pop("VN_ACCESS_TOKEN", None)
    else:
        os.environ["VN_ACCESS_TOKEN"] = pin
    for mod in [m for m in list(sys.modules) if m.startswith("app")]:
        del sys.modules[mod]
    from fastapi.testclient import TestClient
    from app.main import app
    client = TestClient(app)
    # Entering the context runs startup, which creates the schema.
    client.__enter__()
    return client


# --------------------------------------------------------------------------
# origin policy
# --------------------------------------------------------------------------

@pytest.mark.parametrize("origin", [
    "http://localhost:5173",
    "http://127.0.0.1:8000",
    "http://192.168.2.13:8000",
    "http://192.168.1.44:5173",
    "http://10.0.0.7:8000",
    "http://172.16.5.9:8000",
    "http://172.31.255.1:8000",
    "http://macbook.local:8000",
])
def test_private_network_origins_are_allowed(origin):
    from app.net import LAN_ORIGIN_RE
    assert LAN_ORIGIN_RE.match(origin), origin


@pytest.mark.parametrize("origin", [
    "http://example.com",
    "https://evil.example.com:8000",
    "http://8.8.8.8:8000",
    "http://172.32.0.1:8000",       # just outside the private 172.16/12 block
    "http://192.169.1.1:8000",      # just outside 192.168/16
    "http://notlocalhost.com",
])
def test_public_origins_are_rejected(origin):
    from app.net import LAN_ORIGIN_RE
    assert not LAN_ORIGIN_RE.match(origin), origin


def test_lan_addresses_are_private_and_routable():
    import ipaddress
    from app.net import lan_addresses
    for address in lan_addresses():
        parsed = ipaddress.ip_address(address)
        assert parsed.is_private
        assert not parsed.is_loopback


# --------------------------------------------------------------------------
# no PIN configured (the default)
# --------------------------------------------------------------------------

def test_without_a_pin_everything_is_open():
    client = _fresh_client(None)
    assert client.get("/api/health").status_code == 200
    assert client.get("/api/projects").status_code == 200
    assert client.get("/api/auth/status").json() == {"required": False, "authorised": True}


# --------------------------------------------------------------------------
# PIN configured
# --------------------------------------------------------------------------

def test_pin_blocks_the_api_until_you_log_in():
    client = _fresh_client("4821")

    # Health stays open so a phone can tell the server is up.
    assert client.get("/api/health").status_code == 200
    assert client.get("/api/auth/status").json()["required"] is True

    assert client.get("/api/projects").status_code == 401
    assert client.post("/api/projects", json={"name": "x"}).status_code == 401

    assert client.post("/api/auth/login", json={"token": "0000"}).status_code == 401
    assert client.get("/api/projects").status_code == 401

    assert client.post("/api/auth/login", json={"token": "4821"}).status_code == 200
    # The cookie the client now holds authenticates everything else.
    assert client.get("/api/projects").status_code == 200
    assert client.get("/api/auth/status").json()["authorised"] is True

    client.post("/api/auth/logout")
    assert client.get("/api/projects").status_code == 401


def test_pin_can_also_be_sent_as_a_header_or_query_param():
    client = _fresh_client("4821")

    # A header, for scripted access.
    assert client.get("/api/projects", headers={"X-VN-Token": "4821"}).status_code == 200
    # A query param, because EventSource and <img src> cannot set headers.
    assert client.get("/api/projects?token=4821").status_code == 200
    assert client.get("/api/projects?token=nope").status_code == 401


def test_the_app_shell_loads_without_a_pin_but_carries_no_data():
    """The SPA has to render in order to show the PIN prompt."""
    client = _fresh_client("4821")
    response = client.get("/")
    assert response.status_code in (200, 404)   # 404 only if dist is unbuilt
    assert client.get("/api/projects").status_code == 401


def test_describe_reports_the_phone_url_and_warns_when_open(monkeypatch):
    from app import net

    monkeypatch.delenv("VN_ACCESS_TOKEN", raising=False)
    text = net.describe("0.0.0.0", 8000)
    assert "localhost:8000" in text
    assert "No PIN set" in text          # binding wide open is worth saying out loud

    monkeypatch.setenv("VN_ACCESS_TOKEN", "1234")
    assert "PIN required" in net.describe("0.0.0.0", 8000)

    # Bound to loopback only: no phone URL, and nothing to warn about.
    monkeypatch.delenv("VN_ACCESS_TOKEN", raising=False)
    local = net.describe("127.0.0.1", 8000)
    assert "http://127.0.0.1:8000" in local
    assert "No PIN set" not in local


def test_teardown_restores_the_default_environment():
    os.environ.pop("VN_ACCESS_TOKEN", None)
    client = _fresh_client(None)
    assert client.get("/api/projects").status_code == 200
