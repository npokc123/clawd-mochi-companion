#!/usr/bin/env python3
"""
find-mochi.py — locate a Clawd Mochi on the LAN and report its host.

A host "is Mochi" iff GET http://HOST/state returns JSON containing the
firmware-unique keys (roboLively + limitPct). No other device on a home
network serves that, so the fingerprint is effectively zero false-positive.

Discovery, fastest-first:
  1. clawd-mochi.local       — mDNS (instant, survives DHCP changes; the
                               firmware advertises it via ESPmDNS). macOS and
                               Windows 10+ resolve `.local` natively.
  2. <local /24> subnet sweep — parallel probe of every host, fingerprinted.
  3. nothing found           — exit 2 (caller asks the user for a manual IP).

On success the resolved host is printed to stdout (just the host, nothing
else, so callers can capture it); all diagnostics go to stderr.

Usage:
  find-mochi.py                 # print host, exit 0 if found / 2 if not
  find-mochi.py --write         # also write device.host into the config
  find-mochi.py --config PATH   # config path (default ~/.config/clawd-mochi/config.json)
  find-mochi.py --timeout 0.4   # per-host probe timeout, seconds (default 0.4)

Exit codes: 0 found · 2 not found · 1 error.
"""
from __future__ import annotations

import argparse
import json
import socket
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.error import URLError
from urllib.request import ProxyHandler, build_opener

# Never probe through a proxy: urllib otherwise honors both proxy env vars and
# the macOS *system* proxy settings, and no proxy can reach LAN-only hosts —
# every probe would false-negative (or false-positive on the proxy itself).
DIRECT = build_opener(ProxyHandler({}))

MDNS_HOST = "clawd-mochi.local"
FINGERPRINT = ('"roboLively"', '"limitPct"')  # both must appear in /state body
DEFAULT_CONFIG = Path.home() / ".config" / "clawd-mochi" / "config.json"


def err(msg: str) -> None:
    print(msg, file=sys.stderr)


def is_mochi(host: str, timeout: float) -> bool:
    """True if http://host/state looks like Clawd Mochi firmware."""
    try:
        # IPv4-only resolve (A record): asking for AAAA too makes .local names
        # hang for seconds on macOS; sweep candidates are raw IPs and pass
        # through unchanged.
        ip = socket.getaddrinfo(host, 80, socket.AF_INET, socket.SOCK_STREAM)[0][4][0]
        with DIRECT.open(f"http://{ip}/state", timeout=timeout) as r:
            if r.status != 200:
                return False
            body = r.read(4096).decode("utf-8", "ignore")
    except (URLError, OSError, ValueError):
        return False
    return all(key in body for key in FINGERPRINT)


def primary_ipv4() -> str | None:
    """Best-guess LAN IPv4 of this machine, no external traffic sent."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))  # UDP connect just picks the route, sends nothing
        return s.getsockname()[0]
    except OSError:
        return None
    finally:
        s.close()


def sweep_subnet(timeout: float) -> list[str]:
    """Fingerprint every host on the local /24. Returns matching IPs."""
    ip = primary_ipv4()
    if not ip or ip.startswith("127."):
        err("[find-mochi] could not determine a LAN address for this machine")
        return []
    base = ip.rsplit(".", 1)[0]
    err(f"[find-mochi] scanning {base}.1-254 (self={ip}) ...")
    candidates = [f"{base}.{n}" for n in range(1, 255) if f"{base}.{n}" != ip]
    found: list[str] = []
    with ThreadPoolExecutor(max_workers=128) as pool:
        for host, hit in zip(candidates, pool.map(lambda h: is_mochi(h, timeout), candidates)):
            if hit:
                found.append(host)
    return found


def discover(timeout: float) -> str | None:
    # 1) mDNS — give .local resolution a moment longer than a raw IP probe.
    err(f"[find-mochi] trying {MDNS_HOST} ...")
    if is_mochi(MDNS_HOST, max(timeout, 1.5)):
        err(f"[find-mochi] found via mDNS: {MDNS_HOST}")
        return MDNS_HOST  # prefer the name — it is immune to DHCP changes

    # 2) subnet sweep
    hits = sweep_subnet(timeout)
    if not hits:
        return None
    if len(hits) > 1:
        err(f"[find-mochi] WARNING: multiple devices matched ({', '.join(hits)}); "
            f"using the first. Pass the right IP manually if this is wrong.")
    err(f"[find-mochi] found via scan: {hits[0]}")
    return hits[0]


def write_host(config_path: Path, host: str) -> None:
    """Set device.host in the config, creating a minimal one if absent."""
    try:
        cfg = json.loads(config_path.read_text())
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        cfg = {"enabled": True, "device": {"host": host, "timeout_ms": 500}}
    dev = cfg.setdefault("device", {})
    dev["host"] = host
    dev.setdefault("timeout_ms", 500)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n")
    err(f"[find-mochi] wrote device.host = {host} -> {config_path}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Find a Clawd Mochi on the LAN.")
    ap.add_argument("--write", action="store_true",
                    help="write device.host into the config")
    ap.add_argument("--config", type=Path, default=DEFAULT_CONFIG,
                    help=f"config path (default {DEFAULT_CONFIG})")
    ap.add_argument("--timeout", type=float, default=0.4,
                    help="per-host probe timeout in seconds (default 0.4)")
    args = ap.parse_args()

    host = discover(args.timeout)
    if not host:
        err("[find-mochi] no Clawd Mochi found. Check that the device is powered, "
            "on the same Wi-Fi/subnet as this machine, and not in fallback-AP mode "
            "(if it is, connect to the 'ClaWD-Mochi' AP — its host is always 192.168.4.1).")
        return 2

    if args.write:
        try:
            write_host(args.config, host)
        except OSError as e:
            err(f"[find-mochi] could not write config: {e}")
            return 1

    print(host)  # stdout: just the host, for the caller to capture
    return 0


if __name__ == "__main__":
    sys.exit(main())
