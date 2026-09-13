"""
mochi_core — the pure part of the Clawd Mochi hook bridge (ADR-0005).

Hook payload + config -> the body of one `POST /api/event`:
    {"session_id": ..., "seq": N, "state": ..., "reaction": ..., "caption": ...}

No network here except `fire`/`do_fire`, which the hook calls last. Everything
else is testable on the host with `python -m unittest` (see tests/).

Mapping: `events[<hook_event_name>]` is either
  - null / absent                  -> nothing is sent,
  - an Event entry                 {"state", "reaction", "caption"} (any subset),
  - a switch                       {"by_tool": {...}, "by_type": {...}, "default": {...}}
    `by_tool` matches `tool_name`, `by_type` matches `notification_type`.
    Keys are fnmatch globs, `|` separates alternatives; the first match in
    file order wins, then `default`. A matched value of null sends nothing.
The built-in table (DEFAULT_EVENTS, SPEC §8.3) is the base; `events` in the
user's config replaces it hook by hook.

Caption templates: {tool} (tool_name, the part after the last `__` for MCP
tools), {file} (basename of the tool's file path), {model} (model display name
from SessionStart). Unknown braces stay as typed. The result is cut to
captionMaxBytes of UTF-8 on a character boundary — the device answers 400 to
anything longer.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from contextlib import contextmanager
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any, Iterator, Optional

CAPTION_MAX_BYTES = 64  # GET /api/capabilities captionMaxBytes, firmware v1
SESSION_ID_MAX = 63     # firmware companion::kSessionIdMax

HOOKS = (
    "SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse",
    "PostToolUseFailure", "Notification", "Stop", "StopFailure",
    "SubagentStart", "SubagentStop", "SessionEnd", "PreCompact",
)

READ_TOOLS = "Read|Grep|Glob|WebFetch|WebSearch"

# SPEC §8.3. Captions are the only thing added on top of the table.
DEFAULT_EVENTS: dict = {
    "SessionStart": {"reaction": "blink"},
    "UserPromptSubmit": {"state": "thinking"},
    "PreToolUse": {
        "by_tool": {
            "Read": {"state": "reading", "caption": "{file}"},
            "WebSearch": {"state": "reading", "caption": "ищу в сети"},
            "WebFetch": {"state": "reading", "caption": "читаю сайт"},
            "Grep|Glob": {"state": "reading"},
            "Edit|Write|NotebookEdit": {"state": "working", "caption": "{file}"},
        },
        "default": {"state": "working"},
    },
    "PostToolUse": {"state": "thinking"},
    "PostToolUseFailure": {"reaction": "error"},
    "Notification": {
        "by_type": {
            "permission_prompt|elicitation_dialog|elicitation_url_dialog|agent_needs_input":
                {"state": "waiting_user"},
        },
        "default": None,
    },
    "Stop": {"reaction": "success"},
    "StopFailure": {"reaction": "error"},
    "SubagentStart": {"state": "working"},
    "SubagentStop": None,
    "SessionEnd": {"state": "sleeping"},
    "PreCompact": {"state": "working", "caption": "сжимаю контекст"},
}

ENTRY_KEYS = {"state", "reaction", "caption"}
SWITCH_KEYS = {"by_tool", "by_type", "default"}


# ── mapping ────────────────────────────────────────────────────────────────

def effective_events(cfg: dict) -> dict:
    events = dict(DEFAULT_EVENTS)
    user = cfg.get("events")
    if isinstance(user, dict):
        events.update(user)
    return events


def glob_match(pattern: str, value: str) -> bool:
    return any(fnmatchcase(value, p.strip()) for p in pattern.split("|"))


def resolve(events: dict, payload: dict) -> Optional[dict]:
    """The Event entry for this hook payload, or None to send nothing."""
    entry = events.get(payload.get("hook_event_name") or "")
    if not isinstance(entry, dict):
        return None
    if not SWITCH_KEYS & entry.keys():
        return entry
    for field, key in (("by_tool", "tool_name"), ("by_type", "notification_type")):
        table = entry.get(field)
        value = payload.get(key)
        if isinstance(table, dict) and isinstance(value, str):
            for pattern, sub in table.items():
                if glob_match(pattern, value):
                    return sub if isinstance(sub, dict) else None
    default = entry.get("default")
    return default if isinstance(default, dict) else None


# ── caption ────────────────────────────────────────────────────────────────

def truncate_utf8(text: str, max_bytes: int) -> str:
    """At most max_bytes of UTF-8, cut on a character boundary."""
    raw = text.encode("utf-8")
    if len(raw) <= max_bytes:
        return text
    return raw[:max_bytes].decode("utf-8", "ignore")


def short_tool(name: str) -> str:
    return name.rsplit("__", 1)[-1] if name.startswith("mcp__") else name


def file_of(payload: dict) -> str:
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return ""
    for key in ("file_path", "notebook_path", "path"):
        value = tool_input.get(key)
        if isinstance(value, str) and value:
            # Both separators: a Windows path reaches the hook on any OS in tests
            # and through WSL/remote setups.
            return re.split(r"[\\/]", value.rstrip("/\\"))[-1] or value
    return ""


def model_of(payload: dict) -> str:
    """Model name from a SessionStart payload, "" if absent."""
    model = payload.get("model")
    if isinstance(model, dict):
        model = model.get("display_name") or model.get("id")
    return model if isinstance(model, str) else ""


PLACEHOLDER = re.compile(r"\{(tool|file|model)\}")


def render_caption(template: str, payload: dict, model: str = "",
                   max_bytes: int = CAPTION_MAX_BYTES) -> str:
    values = {
        "tool": short_tool(str(payload.get("tool_name") or "")),
        "file": file_of(payload),
        "model": model,
    }
    text = PLACEHOLDER.sub(lambda m: values[m.group(1)], template)
    text = " ".join(text.split())  # newlines from paths/models never reach the bubble
    return truncate_utf8(text, max_bytes)


def build_event(entry: dict, payload: dict, model: str = "",
                max_bytes: int = CAPTION_MAX_BYTES) -> dict:
    """The Event fields of the body (no session_id/seq)."""
    body: dict = {}
    for key in ("state", "reaction"):
        if isinstance(entry.get(key), str):
            body[key] = entry[key]
    caption = entry.get("caption")
    if isinstance(caption, str):
        body["caption"] = render_caption(caption, payload, model, max_bytes)
    return body


def device_session_id(session_id: str) -> str:
    """Claude Code session ids are UUIDs; anything longer than the firmware
    keeps is cut, which still identifies the session within one machine."""
    return truncate_utf8(session_id, SESSION_ID_MAX)


# ── session state: seq + model ─────────────────────────────────────────────

@contextmanager
def _locked(path: Path) -> Iterator[Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    f = open(path, "a+", encoding="utf-8")
    try:
        if os.name == "nt":
            import msvcrt
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(f, fcntl.LOCK_EX)
        yield f
    finally:
        try:
            if os.name == "nt":
                import msvcrt
                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
        f.close()  # closing drops a POSIX flock


class SessionStore:
    """One small JSON file per session: {"seq": N, "model": "..."}.

    `seq` is what lets the device drop requests whose detached workers
    overtook each other (ADR-0005 §2), so it is incremented under a file lock.
    """

    MAX_AGE_S = 7 * 24 * 3600

    def __init__(self, root: Path):
        self.root = root

    def path(self, session_id: str) -> Path:
        return self.root / (re.sub(r"[^A-Za-z0-9_.-]", "_", session_id)[:80] + ".json")

    def next(self, session_id: str, model: str = "") -> "tuple[int, str]":
        """Increment seq; remember model if given. Returns (seq, model)."""
        with _locked(self.path(session_id)) as f:
            f.seek(0)
            try:
                state = json.loads(f.read() or "{}")
            except ValueError:
                state = {}
            if not isinstance(state, dict):
                state = {}
            seq = int(state.get("seq", 0)) + 1
            state["seq"] = seq
            if model:
                state["model"] = model
            f.seek(0)
            f.truncate()
            f.write(json.dumps(state, ensure_ascii=False))
            f.flush()
            return seq, str(state.get("model") or "")

    def prune(self, now: Optional[float] = None) -> None:
        """Forget sessions untouched for a week. Never raises."""
        cutoff = (now if now is not None else time.time()) - self.MAX_AGE_S
        try:
            for p in self.root.glob("*.json"):
                if p.stat().st_mtime < cutoff:
                    p.unlink()
        except OSError:
            pass


# ── config check for /mochi:setup ──────────────────────────────────────────

def _check_entry(where: str, entry: Any, caps: dict, errors: list) -> None:
    if entry is None:
        return
    if not isinstance(entry, dict):
        errors.append(f"{where}: expected an object or null")
        return
    for key in entry.keys() - ENTRY_KEYS:
        errors.append(f"{where}: unknown key {key!r} (allowed: state, reaction, caption)")
    state, reaction, caption = entry.get("state"), entry.get("reaction"), entry.get("caption")
    if state is not None and state not in caps.get("states", []):
        errors.append(f"{where}: unknown state {state!r}; device knows {caps.get('states')}")
    if reaction is not None and reaction not in caps.get("reactions", []):
        errors.append(f"{where}: unknown reaction {reaction!r}; device knows {caps.get('reactions')}")
    if caption is not None:
        if not isinstance(caption, str):
            errors.append(f"{where}: caption must be a string")
        else:
            static = PLACEHOLDER.sub("", caption)
            limit = int(caps.get("captionMaxBytes", CAPTION_MAX_BYTES))
            if len(static.encode("utf-8")) > limit:
                errors.append(f"{where}: caption {caption!r} is over {limit} bytes UTF-8 and will be cut")
    if not entry.keys() & {"state", "reaction", "caption"}:
        errors.append(f"{where}: empty entry sends nothing — use null to mute the hook")


def check_events(events: Any, caps: dict) -> "list[str]":
    """Problems in a user's `events` against GET /api/capabilities."""
    if events is None:
        return []
    if not isinstance(events, dict):
        return ["events: expected an object"]
    errors: list = []
    for hook, entry in events.items():
        if hook.startswith("_"):
            continue
        if hook not in HOOKS:
            errors.append(f"events.{hook}: not a hook this plugin subscribes to ({', '.join(HOOKS)})")
            continue
        if isinstance(entry, dict) and SWITCH_KEYS & entry.keys():
            for key in entry.keys() - SWITCH_KEYS:
                errors.append(f"events.{hook}: {key!r} next to by_tool/by_type/default")
            for field in ("by_tool", "by_type"):
                table = entry.get(field)
                if table is None:
                    continue
                if not isinstance(table, dict):
                    errors.append(f"events.{hook}.{field}: expected an object")
                    continue
                for pattern, sub in table.items():
                    _check_entry(f"events.{hook}.{field}[{pattern!r}]", sub, caps, errors)
            _check_entry(f"events.{hook}.default", entry.get("default"), caps, errors)
        else:
            _check_entry(f"events.{hook}", entry, caps, errors)
    return errors


# ── transport ──────────────────────────────────────────────────────────────

def do_fire(url: str, body: str, timeout_s: float) -> None:
    """Worker: POST body as JSON, don't read the answer, never raise."""
    import socket
    from urllib.parse import urlsplit
    from urllib.request import ProxyHandler, Request, build_opener

    parts = urlsplit(url)
    port = parts.port or 80
    try:
        # IPv4 only (A record): an unrestricted lookup also asks for AAAA, and
        # .local (mDNS) names hang for seconds on that leg on macOS.
        ip = socket.getaddrinfo(parts.hostname, port, socket.AF_INET, socket.SOCK_STREAM)[0][4][0]
        req = Request(f"http://{ip}:{port}{parts.path or '/'}", data=body.encode("utf-8"),
                      headers={"Content-Type": "application/json"}, method="POST")
        # No proxy: urllib honors proxy env vars and the macOS system proxy,
        # and no proxy can reach a LAN-only device.
        build_opener(ProxyHandler({})).open(req, timeout=timeout_s).close()
    except Exception:
        pass  # fire-and-forget: an offline device must never surface an error


def fire(script: str, url: str, body: dict, timeout_s: float) -> None:
    """Spawn `script --fire URL TIMEOUT_S BODY` detached; return at once."""
    argv = [sys.executable, script, "--fire", url, f"{timeout_s}",
            json.dumps(body, ensure_ascii=False, separators=(",", ":"))]
    kwargs: dict = {}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, **kwargs)
