from __future__ import annotations

import argparse
import os
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

os.environ.setdefault("VIDEOMEMO_APP_MODE", "1")

import server


APP_QUERY = "?mode=desktop"
STARTUP_GRACE_SECONDS = 45.0
HEARTBEAT_IDLE_SECONDS = 15.0
LOG_PATH = Path(__file__).resolve().parent / ".videomemo-data" / "launcher.log"

def write_log(message: str) -> None:
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(f"[{timestamp}] {message}\n")


def wait_until_ready(base_url: str, timeout_seconds: float = 10.0) -> None:
    deadline = time.time() + timeout_seconds
    config_url = f"{base_url}/api/config"
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(config_url, timeout=1.0) as response:
                if response.status == 200:
                    return
        except OSError:
            time.sleep(0.15)
    raise RuntimeError(f"VideoMemo backend did not become ready: {config_url}")


def open_app_url(url: str) -> None:
    write_log(f"Opening browser URL: {url}")
    if os.name == "nt":
        os.startfile(url)  # type: ignore[attr-defined]
        return
    opened = webbrowser.open(url, new=1)
    if not opened:
        raise RuntimeError(f"Could not open browser for {url}")


def wait_for_app_session() -> None:
    startup_deadline = time.time() + STARTUP_GRACE_SECONDS
    seen_ping = False

    while True:
        age = server.seconds_since_app_ping()
        if age is None:
            if seen_ping:
                return
            if time.time() >= startup_deadline:
                return
        else:
            seen_ping = True
            if age > HEARTBEAT_IDLE_SECONDS:
                return
        time.sleep(1.0)


def run_server(host: str, port: int) -> tuple[server.ThreadingHTTPServer, threading.Thread]:
    httpd = server.build_server(host, port)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd, thread


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Launch VideoMemo as a local desktop-style app.")
    parser.add_argument("--host", default="127.0.0.1", help="Host interface for the embedded backend.")
    parser.add_argument("--port", type=int, default=0, help="Port for the embedded backend. 0 selects a free port.")
    parser.add_argument("--check", action="store_true", help="Start the embedded backend, verify readiness, then exit.")
    parser.add_argument("--no-open-browser", action="store_true", help="Start backend only and do not open a browser.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    write_log(f"Launcher start host={args.host} port={args.port} no_open_browser={args.no_open_browser}")
    httpd, thread = run_server(args.host, args.port)
    host, port = httpd.server_address
    base_url = f"http://{host}:{port}"

    try:
        wait_until_ready(base_url)
        write_log(f"Backend ready at {base_url}")
        if args.check:
            print(f"VideoMemo desktop backend ready at {base_url}")
            return

        app_url = f"{base_url}/{APP_QUERY}"
        if not args.no_open_browser:
            open_app_url(app_url)
        print(f"VideoMemo app running at {app_url}")
        write_log(f"App URL active: {app_url}")
        if not args.no_open_browser and sys.stdout is not None and not sys.stdout.closed:
            print("VideoMemo will stop automatically after the app window is closed.")
        wait_for_app_session()
        write_log("Heartbeat session ended, shutting down backend.")
    except KeyboardInterrupt:
        print("Stopping VideoMemo...")
        write_log("Launcher interrupted by keyboard.")
    except Exception as error:
        write_log(f"Launcher error: {error!r}")
        raise
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=2.0)
        write_log("Launcher stopped.")


if __name__ == "__main__":
    main()
