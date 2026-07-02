#!/usr/bin/env python3
"""
mochi-statusline — fire-and-forget bridge from Claude Code's statusLine data
to the Clawd Mochi 5-hour limit bar.

Claude Code only exposes rate-limit usage to the statusLine command (never to
hooks), so this reads the statusLine JSON object from stdin, pulls
`rate_limits.five_hour.used_percentage`, and spawns a detached HTTP request to
`/limit?pct=N`. The parent exits immediately; the statusLine render is never
blocked. If `five_hour` is absent (free tier, or before the first API response)
nothing is sent and the bar simply keeps its last state.

Pure stdlib, no curl/sh — behaves the same on macOS, Linux and Windows
(where Claude Code runs commands under Git Bash). The detached worker is
this same file re-invoked as:  mochi-statusline.py --fire URL TIMEOUT_S

This file is standalone on purpose: setup copies it (with its `mochi-statusline`
launcher) to ~/.config/clawd-mochi/bin/, because settings.json cannot expand
${CLAUDE_PLUGIN_ROOT} and the plugin cache path changes on every update.

Wire it into ~/.claude/statusline-command.sh by piping the same JSON in:
    printf '%s' "$input" | "$HOME/.config/clawd-mochi/bin/mochi-statusline" &

Config:   ~/.config/clawd-mochi/config.json   (shared with mochi-hook;
          override via CLAWD_MOCHI_CONFIG)
Disable:  "statusline": {"enabled": false} or top-level "enabled": false in
          config, or export CLAWD_MOCHI_DISABLED=1
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlencode, urlsplit
from urllib.request import ProxyHandler, build_opener

# Never route device requests through a proxy: urllib otherwise honors both
# proxy env vars and the macOS *system* proxy settings (which curl ignored),
# and no proxy can reach a LAN-only device.
DIRECT = build_opener(ProxyHandler({}))

DEFAULT_CONFIG = Path.home() / ".config" / "clawd-mochi" / "config.json"
CONFIG_PATH = Path(os.environ.get("CLAWD_MOCHI_CONFIG") or DEFAULT_CONFIG)


def load_config() -> dict | None:
    try:
        with CONFIG_PATH.open() as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def do_fire(url: str, timeout_s: float) -> None:
    """Worker mode: GET the URL. Never raises."""
    parts = urlsplit(url)
    port = parts.port or 80
    try:
        # IPv4 only (A record): an unrestricted lookup also asks for AAAA, and
        # .local (mDNS) names hang for seconds on that leg on macOS — the reason
        # the old curl bridge used --ipv4. Same fix, portable.
        ip = socket.getaddrinfo(
            parts.hostname, port, socket.AF_INET, socket.SOCK_STREAM
        )[0][4][0]
        path = parts.path or "/"
        if parts.query:
            path += "?" + parts.query
        with DIRECT.open(f"http://{ip}:{port}{path}", timeout=timeout_s) as r:
            r.read(64)
    except Exception:
        pass  # fire-and-forget: an offline device must never surface an error


def fire(url: str, timeout_s: float) -> None:
    """Spawn a detached worker so this process can exit immediately."""
    argv = [sys.executable, os.path.abspath(__file__),
            "--fire", url, f"{timeout_s}"]
    kwargs: dict = {}
    if os.name == "nt":
        # Own process group, no console window; outlives the parent.
        kwargs["creationflags"] = (
            subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        )
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen(
        argv,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        **kwargs,
    )


def log(cfg: dict, line: str) -> None:
    path = cfg.get("log_file")
    if not path:
        return
    try:
        with Path(path).expanduser().open("a") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {line}\n")
    except OSError:
        pass


def main() -> int:
    if len(sys.argv) >= 4 and sys.argv[1] == "--fire":
        do_fire(sys.argv[2], float(sys.argv[3]))
        return 0

    if os.environ.get("CLAWD_MOCHI_DISABLED") == "1":
        return 0

    cfg = load_config()
    if cfg is None or not cfg.get("enabled", True):
        return 0
    if not (cfg.get("statusline") or {}).get("enabled", True):
        return 0

    host = (cfg.get("device") or {}).get("host")
    if not host:
        return 0
    timeout_s = float((cfg.get("device") or {}).get("timeout_ms", 500)) / 1000.0

    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0

    five = ((payload.get("rate_limits") or {}).get("five_hour") or {})
    used = five.get("used_percentage")
    if used is None:
        # No 5h data yet (free tier / before first API response) — leave the
        # bar as-is rather than forcing it to 0.
        return 0

    pct = max(0, min(100, round(float(used))))
    url = f"http://{host}/limit?{urlencode({'pct': pct})}"
    log(cfg, f"statusline 5h={used} -> {url}")
    fire(url, timeout_s)
    return 0


if __name__ == "__main__":
    sys.exit(main())
