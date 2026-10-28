"""Run the authoring tool, optionally on the local network.

    python serve.py                 # this computer only (default)
    python serve.py --lan           # reachable from a phone on the same Wi-Fi
    python serve.py --lan --pin 4821

Binding to the network is deliberately opt-in: this process writes to your
filesystem, launches Ren'Py and Kohya, and talks to an unauthenticated ComfyUI.
On a home network that is normally fine; --pin adds a shared code for anything
less private.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--lan", action="store_true",
                        help="listen on all interfaces so other devices can connect")
    parser.add_argument("--host", default=None, help="explicit bind address")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--pin", default=None,
                        help="require this code before the editor will open")
    parser.add_argument("--reload", action="store_true", help="reload on code changes")
    args = parser.parse_args()

    host = args.host or ("0.0.0.0" if args.lan else "127.0.0.1")
    if args.pin:
        os.environ["VN_ACCESS_TOKEN"] = str(args.pin)

    from app import net

    dist = Path(__file__).resolve().parents[1] / "frontend" / "dist"
    if not dist.exists():
        print("\n  The built UI is missing. Run this first:\n"
              "      cd frontend && npm install && npm run build\n")

    print(net.describe(host, args.port))
    if args.pin:
        print(f"    PIN: {args.pin}\n")

    import uvicorn
    # The job event stream (SSE) keeps a connection open for every browser tab.
    # Without a bound, Ctrl+C makes uvicorn "wait for connections to close"
    # forever, because that stream only ends when the tab does. Cap the wait so
    # the first Ctrl+C always exits within a few seconds.
    uvicorn.run("app.main:app", host=host, port=args.port, reload=args.reload,
                log_level="warning", timeout_graceful_shutdown=3)


if __name__ == "__main__":
    main()
