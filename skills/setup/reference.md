# Clawd Mochi setup — reference

Detail behind the `setup` skill. Load this only when configuring, tuning the
event→emotion mapping, or debugging. The two bridges and the config are
fire-and-forget: if the device is offline, the request dies on its timeout and
Claude Code never notices.

## The two bridges

Both are tiny, stateless, dependency-free Python (stdlib only, no curl). They
share one config file: `~/.config/clawd-mochi/config.json` (override with
`CLAWD_MOCHI_CONFIG`). Each ships with an extensionless `sh` launcher of the
same name that picks the machine's Python (`python3` → `python` → `py`).

| Bridge               | Runs from                                   | Trigger                          | Sends                                  |
| -------------------- | ------------------------------------------- | -------------------------------- | -------------------------------------- |
| `mochi-hook.py`      | the installed plugin (`hooks/hooks.json`)   | every Claude Code hook event     | `GET /robo?...` (expression / caption) |
| `mochi-statusline.py`| copy in `~/.config/clawd-mochi/bin/`        | every statusLine render (~300ms) | `GET /limit?pct=N` (5-hour usage bar)  |

Rate-limit usage is exposed **only** to the statusLine command, never in the
hook payload — that is why the limit bar needs its own bridge. And the
statusLine bridge runs from a copy because `settings.json` cannot expand
`${CLAUDE_PLUGIN_ROOT}` and the plugin cache path changes on every update.

Neither bridge blocks: the parent process reads stdin, builds the URL and
re-spawns itself detached (`--fire URL TIMEOUT [DELAY]`) to do the actual
HTTP request. The worker resolves the host IPv4-only (A record) — an
unrestricted lookup also asks for AAAA, which hangs for seconds on `.local`
(mDNS) names on macOS.

## Config schema (`config.json`)

Top-level:
- `enabled` — master on/off for both bridges.
- `device.host` — IP or hostname of the device. `clawd-mochi.local` is best
  (mDNS, survives DHCP changes); an IP also works.
- `device.timeout_ms` — HTTP timeout in ms (default 500).
- `log_file` — optional; every dispatched URL is appended here.
- `statusline.enabled` — optional, default `true`. Set `false` to mute just the
  limit bar while keeping hook-driven expressions.
- `events` — map of hook event name → params.

Event entry forms:
- **Flat object** — params become the query string. `_path` overrides the
  default `/robo` (e.g. `/backlight`, `/redraw`).
- **`default` + `by_tool`** — for `PreToolUse`/`PostToolUse`; `tool_name` from
  the payload picks a branch, `default` is the fallback.

Per-entry optional fields (work in any leaf, incl. `by_tool` branches):
- `reset_after_ms` — schedule a follow-up request this many ms after the
  primary one (a detached worker sleeps then fires, independently of Claude
  Code).
- `reset` — the mapping for that follow-up (same shape; `_path` supported).
  Keep it minimal — only override fields the primary set, so a later event that
  lands meanwhile is not clobbered.

`/robo` params (see also the firmware `/robo` route):
- `expr`: 0=default, 1=tired, 2=angry, 3=happy
- `pos`: 0=center,1=up,2=up-right,3=right,4=down-right,5=down,6=down-left,7=left,8=up-left
- `curious` / `sweat` / `idle` / `thinking` / `lively`: 1 or 0
- `caption`: text in the thought bubble (empty string hides it)
- `shot`: one-shot animation — `blink` | `laugh` | `confused`

Example — "happy for 5s, then relax":
```json
"Stop": {
  "expr": 3, "shot": "laugh", "curious": 0, "sweat": 0,
  "reset_after_ms": 5000,
  "reset": { "expr": 0 }
}
```

## Toggling

- `enabled: false` in config — mute everything (hooks + bar).
- `statusline.enabled: false` — mute just the limit bar.
- `export CLAWD_MOCHI_DISABLED=1` — wins over the config (per-shell kill switch).
- Device side: web UI checkbox, or `GET /limit?on=0` to hide the bar.

## Debug

`<skill>` below is the skill directory inside the installed plugin. Dry-run a
fake hook event against the bundled config template (note: `CLAWD_MOCHI_CONFIG`
must be set on the *bridge*, not on `echo`):
```sh
echo '{"hook_event_name":"PreToolUse","tool_name":"Bash"}' \
  | CLAWD_MOCHI_CONFIG=<skill>/templates/config.json sh <skill>/scripts/mochi-hook
```

Dry-run the statusline bridge:
```sh
echo '{"rate_limits":{"five_hour":{"used_percentage":42.7}}}' \
  | ~/.config/clawd-mochi/bin/mochi-statusline
# expected log line: statusline 5h=42.7 -> http://<host>/limit?pct=43
```

Neither bridge prints to stdout/stderr — set `log_file` in the config and
`tail -f` it (default suggestion: `~/.cache/clawd-mochi.log`).

Confirm the device answers at all:
```sh
curl -s --ipv4 http://<host>/state     # JSON with roboLively/limitPct → it's Mochi
curl --ipv4 "http://<host>/robo?shot=blink"   # make it blink as a smoke test
```

## Windows notes

- Claude Code on Windows runs shell commands (hooks, statusLine, the Bash
  tool) under **Git Bash** from Git for Windows — treat it as required. A
  PowerShell-only machine is not supported by this plugin.
- Python: any of `python3`, `python` or `py` on PATH works — the launchers and
  the setup skill try them in that order. Beware the Microsoft Store `python`
  stub that opens the Store instead of running; install real Python first.
- Paths: the config lives at `~/.config/clawd-mochi/` here too, which is
  `C:\Users\<you>\.config\clawd-mochi\` — unconventional on Windows but kept
  identical across platforms on purpose.
- mDNS: Windows 10+ resolves `.local` natively, so `clawd-mochi.local` works;
  if it doesn't on a particular network, the subnet sweep in `find-mochi.py`
  finds the device by IP anyway.

## Re-discovery

The IP can change when the DHCP lease rotates. If reactions stop and
`curl http://<host>/state` fails, re-run `/mochi:setup` (or just
`find-mochi.py --write`) to re-resolve. With mDNS firmware, prefer
`clawd-mochi.local` as `device.host` and this never happens.
