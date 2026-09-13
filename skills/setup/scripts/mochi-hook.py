#!/usr/bin/env python3
"""
mochi-hook — fire-and-forget bridge from Claude Code hooks to Clawd Mochi S3.

Reads a hook payload from stdin, maps it to a semantic Event (mochi_core,
ADR-0005), stamps session_id + seq, and spawns a detached worker that sends
`POST /api/event`. The parent exits immediately; Claude Code is never blocked.

Pure stdlib — same behaviour on macOS, Linux and Windows (Git Bash). The
worker is this file re-invoked as:  mochi-hook.py --fire URL TIMEOUT_S BODY

Config:   ~/.config/clawd-mochi/config.json   (override via CLAWD_MOCHI_CONFIG)
State:    ~/.cache/clawd-mochi/sessions/      (override via CLAWD_MOCHI_STATE)
Disable:  "enabled": false in config, or export CLAWD_MOCHI_DISABLED=1
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mochi_core as core  # noqa: E402

CONFIG_PATH = Path(os.environ.get("CLAWD_MOCHI_CONFIG")
                   or Path.home() / ".config" / "clawd-mochi" / "config.json")
STATE_DIR = Path(os.environ.get("CLAWD_MOCHI_STATE")
                 or Path.home() / ".cache" / "clawd-mochi" / "sessions")


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
        core.do_fire(sys.argv[2], sys.argv[4], float(sys.argv[3]))
        return 0
    if os.environ.get("CLAWD_MOCHI_DISABLED") == "1":
        return 0
    cfg = load_config()
    if cfg is None or not cfg.get("enabled", True):
        return 0
    device = cfg.get("device") or {}
    host = device.get("host")
    if not host:
        return 0
    try:
        payload = json.load(sys.stdin)
    except ValueError:
        return 0
    if not isinstance(payload, dict):
        return 0

    hook = payload.get("hook_event_name", "")
    tool = payload.get("tool_name") or payload.get("notification_type") or "-"
    entry = core.resolve(core.effective_events(cfg), payload)
    if entry is None:
        log(cfg, f"{hook} {tool} -> (nothing)")
        return 0

    store = core.SessionStore(STATE_DIR)
    session_id = payload.get("session_id")
    model = core.model_of(payload)
    body: dict = {}
    if isinstance(session_id, str) and session_id:
        if hook == "SessionStart":
            store.prune()
        try:
            seq, model = store.next(session_id, model)
        except OSError:
            return 0  # without seq the device can't order us; better silent
        body = {"session_id": core.device_session_id(session_id), "seq": seq}
    body.update(core.build_event(entry, payload, model))
    if not body.keys() & {"state", "reaction", "caption"}:
        return 0

    timeout_s = float(device.get("timeout_ms", 500)) / 1000.0
    log(cfg, f"{hook} {tool} -> {json.dumps(body, ensure_ascii=False)}")
    core.fire(os.path.abspath(__file__), f"http://{host}/api/event", body, timeout_s)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        sys.exit(0)  # a hook must never fail Claude Code
