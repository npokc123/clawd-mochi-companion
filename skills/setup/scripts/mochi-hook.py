#!/usr/bin/env python3
"""
mochi-hook — fire-and-forget bridge from Claude Code hooks to Clawd Mochi.

Reads a hook-event JSON object from stdin, looks up the matching animation
in a local config file, and spawns a detached HTTP request to the device.
The parent exits immediately; Claude Code is never blocked.

Pure stdlib, no curl/sh — behaves the same on macOS, Linux and Windows
(where Claude Code runs commands under Git Bash). The detached worker is
this same file re-invoked as:  mochi-hook.py --fire URL TIMEOUT_S [DELAY_S]

Config:   ~/.config/clawd-mochi/config.json   (override via CLAWD_MOCHI_CONFIG)
Disable:  set "enabled": false in config, or export CLAWD_MOCHI_DISABLED=1
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

RESERVED_KEYS = {"_path", "reset_after_ms", "reset"}

DEFAULT_CONFIG = Path.home() / ".config" / "clawd-mochi" / "config.json"
CONFIG_PATH = Path(os.environ.get("CLAWD_MOCHI_CONFIG") or DEFAULT_CONFIG)


def load_config() -> dict | None:
    try:
        with CONFIG_PATH.open() as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def resolve_mapping(cfg: dict, event: str, payload: dict) -> dict | None:
    entry = cfg.get("events", {}).get(event)
    if entry is None:
        return None
    if not isinstance(entry, dict):
        return None
    if "by_tool" in entry or "default" in entry:
        tool = payload.get("tool_name")
        by_tool = entry.get("by_tool", {})
        if tool and tool in by_tool:
            return by_tool[tool]
        return entry.get("default")
    return entry


def build_url(host: str, mapping: dict) -> str:
    path = mapping.get("_path", "/robo")
    params = {k: v for k, v in mapping.items() if k not in RESERVED_KEYS and v is not None}
    query = urlencode(params)
    base = f"http://{host}{path}"
    return f"{base}?{query}" if query else base


def do_fire(url: str, timeout_s: float, delay_s: float) -> None:
    """Worker mode: optionally sleep, then GET the URL. Never raises."""
    if delay_s > 0:
        time.sleep(delay_s)
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


def fire(url: str, timeout_s: float, delay_s: float = 0.0) -> None:
    """Spawn a detached worker so this process can exit immediately."""
    argv = [sys.executable, os.path.abspath(__file__),
            "--fire", url, f"{timeout_s}", f"{delay_s}"]
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
        do_fire(sys.argv[2], float(sys.argv[3]),
                float(sys.argv[4]) if len(sys.argv) > 4 else 0.0)
        return 0

    if os.environ.get("CLAWD_MOCHI_DISABLED") == "1":
        return 0

    cfg = load_config()
    if cfg is None or not cfg.get("enabled", True):
        return 0

    host = (cfg.get("device") or {}).get("host")
    if not host:
        return 0
    timeout_s = float((cfg.get("device") or {}).get("timeout_ms", 500)) / 1000.0

    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0

    event = payload.get("hook_event_name")
    if not event:
        return 0

    mapping = resolve_mapping(cfg, event, payload)
    if not mapping:
        log(cfg, f"{event} tool={payload.get('tool_name','-')} -> (no mapping)")
        return 0

    url = build_url(host, mapping)
    log(cfg, f"{event} tool={payload.get('tool_name','-')} -> {url}")
    fire(url, timeout_s)

    reset_after_ms = mapping.get("reset_after_ms")
    reset = mapping.get("reset")
    if isinstance(reset_after_ms, (int, float)) and isinstance(reset, dict):
        reset_url = build_url(host, reset)
        log(cfg, f"  reset in {int(reset_after_ms)}ms -> {reset_url}")
        fire(reset_url, timeout_s, delay_s=float(reset_after_ms) / 1000.0)
    return 0


if __name__ == "__main__":
    sys.exit(main())
