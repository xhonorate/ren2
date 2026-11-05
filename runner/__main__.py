"""python -m runner [--port 8000] [--lan] [--pin 1234]"""
from __future__ import annotations

import argparse
import socket

import uvicorn

from .app import create_app


def main():
    ap = argparse.ArgumentParser(prog="runner")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--lan", action="store_true", help="bind 0.0.0.0 so phones on the Wi-Fi can open it")
    ap.add_argument("--pin", help="require this PIN (recommended with --lan)")
    a = ap.parse_args()
    app = create_app(pin=a.pin)
    host = "0.0.0.0" if a.lan else "127.0.0.1"
    print(f"\n  vn console\n    this computer   http://localhost:{a.port}")
    if a.lan:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.connect(("8.8.8.8", 80)); ip = s.getsockname()[0]; s.close()
        except Exception:
            ip = "<this machine's IP>"
        print(f"    phone / tablet  http://{ip}:{a.port}" + ("   (PIN required)" if a.pin else "   (no PIN: anyone on the network can use it)"))
    print()
    uvicorn.run(app, host=host, port=a.port, log_level="warning")


if __name__ == "__main__":
    main()
