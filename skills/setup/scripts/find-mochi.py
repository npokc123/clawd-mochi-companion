#!/usr/bin/env python3
"""
find-mochi.py — locate a Clawd Mochi S3 on the LAN and check the config
against the device's dictionary.

A host "is Mochi" iff GET http://HOST/api/capabilities returns JSON with
"device": "clawd-mochi-s3". The same answer carries states[], reactions[] and
captionMaxBytes, which `--check` validates the user's `events` against.

Discovery, fastest-first:
  1. clawd-mochi.local       — mDNS (survives DHCP changes).
  2. <local /24> subnet sweep — parallel probe of every host.
  3. nothing found           — exit 2 (caller asks the user for an IP).

stdout: just the host; all diagnostics go to stderr.

Usage:
  find-mochi.py                  # discover, print host
  find-mochi.py --host H         # skip discovery, only verify H
  find-mochi.py --write          # also write device.host into the config
  find-mochi.py --check          # also validate config events (exit 3 on problems)
  find-mochi.py --config PATH    # default ~/.config/clawd-mochi/config.json
  find-mochi.py --timeout 0.4    # per-host probe timeout, seconds

Exit codes: 0 ok · 1 error · 2 not found · 3 config problems.
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.request import ProxyHandler, build_opener

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mochi_core as core  # noqa: E402

# No proxy: urllib honors proxy env vars and the macOS system proxy, and no
# proxy can reach LAN-only hosts.
DIRECT = build_opener(ProxyHandler({}))

MDNS_HOST = "clawd-mochi.local"
DEVICE = "clawd-mochi-s3"
DEFAULT_CONFIG = Path(os.environ.get("CLAWD_MOCHI_CONFIG")
                      or Path.home() / ".config" / "clawd-mochi" / "config.json")


def err(msg: str) -> None:
    print(msg, file=sys.stderr)


def capabilities(host: str, timeout: float) -> dict | None:
    """GET /api/capabilities if host is a Clawd Mochi S3, else None."""
    try:
        name, _, port = host.partition(":")
        # IPv4-only resolve: asking for AAAA too makes .local hang on macOS.
        ip = socket.getaddrinfo(name, int(port or 80), socket.AF_INET, socket.SOCK_STREAM)[0][4][0]
        with DIRECT.open(f"http://{ip}:{port or 80}/api/capabilities", timeout=timeout) as r:
            if r.status != 200:
                return None
            caps = json.loads(r.read(8192).decode("utf-8", "replace"))
    except (OSError, ValueError):
        return None
    return caps if isinstance(caps, dict) and caps.get("device") == DEVICE else None


def primary_ipv4() -> str | None:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))  # UDP connect only picks the route, sends nothing
        return s.getsockname()[0]
    except OSError:
        return None
    finally:
        s.close()


def sweep_subnet(timeout: float) -> "list[tuple[str, dict]]":
    ip = primary_ipv4()
    if not ip or ip.startswith("127."):
        err("[find-mochi] could not determine a LAN address for this machine")
        return []
    base = ip.rsplit(".", 1)[0]
    err(f"[find-mochi] scanning {base}.1-254 (self={ip}) ...")
    candidates = [f"{base}.{n}" for n in range(1, 255) if f"{base}.{n}" != ip]
    with ThreadPoolExecutor(max_workers=128) as pool:
        results = pool.map(lambda h: capabilities(h, timeout), candidates)
        return [(h, c) for h, c in zip(candidates, results) if c]


def discover(timeout: float) -> "tuple[str, dict] | None":
    err(f"[find-mochi] trying {MDNS_HOST} ...")
    caps = capabilities(MDNS_HOST, max(timeout, 1.5))
    if caps:
        err(f"[find-mochi] found via mDNS: {MDNS_HOST}")
        return MDNS_HOST, caps  # the name is immune to DHCP changes
    hits = sweep_subnet(timeout)
    if not hits:
        return None
    if len(hits) > 1:
        err(f"[find-mochi] WARNING: several devices matched ({', '.join(h for h, _ in hits)}); "
            f"using the first. Pass the right one with --host if this is wrong.")
    err(f"[find-mochi] found via scan: {hits[0][0]}")
    return hits[0]


def read_config(path: Path) -> dict:
    try:
        cfg = json.loads(path.read_text(encoding="utf-8"))
        return cfg if isinstance(cfg, dict) else {}
    except (OSError, ValueError):
        return {}


def write_host(path: Path, host: str) -> None:
    cfg = read_config(path) or {"enabled": True}
    device = cfg.setdefault("device", {})
    device["host"] = host
    device.setdefault("timeout_ms", 500)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    err(f"[find-mochi] wrote device.host = {host} -> {path}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Find a Clawd Mochi S3 on the LAN.")
    ap.add_argument("--host", help="verify this host instead of discovering")
    ap.add_argument("--write", action="store_true", help="write device.host into the config")
    ap.add_argument("--check", action="store_true", help="validate config events against the device")
    ap.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    ap.add_argument("--timeout", type=float, default=0.4)
    args = ap.parse_args()

    if args.host:
        caps = capabilities(args.host, max(args.timeout, 2.0))
        if not caps:
            err(f"[find-mochi] {args.host} does not answer /api/capabilities as {DEVICE}")
            return 2
        found = (args.host, caps)
    else:
        found = discover(args.timeout)
    if not found:
        err("[find-mochi] no Clawd Mochi S3 found. Check that it is powered and on the same "
            "Wi-Fi/subnet as this machine. If it shows a QR code for the 'ClaWD-Mochi' network, "
            "it is not on your Wi-Fi yet — join that network and finish its setup page first.")
        return 2
    host, caps = found
    err(f"[find-mochi] fw {caps.get('fw')}, states {caps.get('states')}, reactions {caps.get('reactions')}")

    if args.write:
        try:
            write_host(args.config, host)
        except OSError as e:
            err(f"[find-mochi] could not write config: {e}")
            return 1

    print(host)
    if args.check:
        problems = core.check_events(read_config(args.config).get("events"), caps)
        for p in problems:
            err(f"[find-mochi] config: {p}")
        if problems:
            return 3
        err("[find-mochi] config: ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
