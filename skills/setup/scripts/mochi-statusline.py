#!/usr/bin/env python3
"""
mochi-statusline — fire-and-forget bridge from Claude Code's statusLine data
to the two Clawd Mochi limit bars.

Claude Code exposes rate-limit usage only to the statusLine command, so this
reads the statusLine JSON from stdin and sends one request per tick:
    POST /api/limit {"five_hour_pct": 37, "seven_day_pct": 12}
Integers 0-100. A window missing from the payload is left out of the body —
the device keeps that bar as it was. No window at all: nothing is sent.

Standalone on purpose: setup copies it (with its `mochi-statusline` launcher)
to ~/.config/clawd-mochi/bin/, because settings.json cannot expand
${CLAUDE_PLUGIN_ROOT} and the plugin cache path changes on every update.
The detached worker is this file re-invoked as:
    mochi-statusline.py --fire URL TIMEOUT_S BODY

Config:   ~/.config/clawd-mochi/config.json (shared with mochi-hook;
          override via CLAWD_MOCHI_CONFIG)
Disable:  "statusline": {"enabled": false} or top-level "enabled": false,
          or export CLAWD_MOCHI_DISABLED=1
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, Request, build_opener

CONFIG_PATH = Path(os.environ.get("CLAWD_MOCHI_CONFIG")
                   or Path.home() / ".config" / "clawd-mochi" / "config.json")

WINDOWS = (("five_hour", "five_hour_pct"), ("seven_day", "seven_day_pct"))


def limit_body(payload: dict) -> dict:
    """{"five_hour_pct": int, "seven_day_pct": int} for the windows present."""
    limits = payload.get("rate_limits")
    body: dict = {}
    if not isinstance(limits, dict):
        return body
    for window, key in WINDOWS:
        used = (limits.get(window) or {}).get("used_percentage")
        if isinstance(used, (int, float)) and not isinstance(used, bool):
            body[key] = max(0, min(100, round(float(used))))
    return body


def do_fire(url: str, body: str, timeout_s: float) -> None:
    """Worker: POST body as JSON, don't read the answer, never raise."""
    parts = urlsplit(url)
    port = parts.port or 80
    try:
        # IPv4 only: .local names hang for seconds on the AAAA leg on macOS.
        ip = socket.getaddrinfo(parts.hostname, port, socket.AF_INET, socket.SOCK_STREAM)[0][4][0]
        req = Request(f"http://{ip}:{port}{parts.path or '/'}", data=body.encode("utf-8"),
                      headers={"Content-Type": "application/json"}, method="POST")
        # No proxy: no proxy can reach a LAN-only device.
        build_opener(ProxyHandler({})).open(req, timeout=timeout_s).close()
    except Exception:
        pass


def fire(url: str, body: dict, timeout_s: float) -> None:
    argv = [sys.executable, os.path.abspath(__file__), "--fire", url, f"{timeout_s}",
            json.dumps(body, separators=(",", ":"))]
    kwargs: dict = {}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, **kwargs)


def load_config() -> dict | None:
    try:
        with CONFIG_PATH.open(encoding="utf-8") as f:
            cfg = json.load(f)
        return cfg if isinstance(cfg, dict) else None
    except (OSError, ValueError):
        return None


def log(cfg: dict, line: str) -> None:
    path = cfg.get("log_file")
    if not path:
        return
    try:
        with Path(path).expanduser().open("a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {line}\n")
    except OSError:
        pass


def main() -> int:
    if len(sys.argv) >= 5 and sys.argv[1] == "--fire":
        do_fire(sys.argv[2], sys.argv[4], float(sys.argv[3]))
        return 0
    if os.environ.get("CLAWD_MOCHI_DISABLED") == "1":
        return 0
    cfg = load_config()
    if cfg is None or not cfg.get("enabled", True):
        return 0
    if not (cfg.get("statusline") or {}).get("enabled", True):
        return 0
    device = cfg.get("device") or {}
    host = device.get("host")
    if not host:
        return 0
    try:
        payload = json.load(sys.stdin)
    except ValueError:
        return 0
    body = limit_body(payload) if isinstance(payload, dict) else {}
    if not body:
        return 0  # no subscription windows (API key, before first reply)
    log(cfg, f"statusline -> {json.dumps(body)}")
    fire(f"http://{host}/api/limit", body, float(device.get("timeout_ms", 500)) / 1000.0)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        sys.exit(0)  # the statusLine must never fail
