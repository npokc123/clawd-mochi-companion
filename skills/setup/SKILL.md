---
name: setup
description: Connect this machine's Claude Code to a Clawd Mochi desk companion — discover the device, write the config, wire the statusLine bridge, smoke-test.
disable-model-invocation: true
allowed-tools: Bash, Read, Write, Edit
argument-hint: "[optional host/IP]"
---

# Clawd Mochi — Claude Code setup

Wire this machine's Claude Code to a Clawd Mochi (ESP32-C3 display). The
plugin already registers the hook bridge by itself (`hooks/hooks.json` runs
`scripts/mochi-hook` straight from the installed plugin, so it follows plugin
updates). What is left for this skill, in order:

1. install the config and the statusLine bridge,
2. discover the device and write its host into the config,
3. smoke-test end to end,
4. wire the statusLine, clean up a legacy pre-plugin install if present.

In the commands below `$SKILL_DIR` means this skill's base directory (shown
when the skill loads) — set it first in each shell you use. All commands are
POSIX shell: Claude Code runs them in bash on macOS/Linux and in **Git Bash on
Windows**; the same commands work in both, including `~`-paths.

If the user passed a host/IP as `$ARGUMENTS`, skip discovery (step 3) and use
it directly as `<host>`.

Detailed config schema, event mapping, toggles, debug and Windows notes live
in [reference.md](reference.md) — read it only if tuning or debugging.

## Conventions

- **The statusLine bridge runs from a copy** in `~/.config/clawd-mochi/bin/`,
  not from the plugin: `settings.json` cannot expand `${CLAUDE_PLUGIN_ROOT}`,
  and the plugin's cache path changes on every update. Hooks need no copy —
  the plugin resolves its own path.
- Touch the **user-level** `~/.claude/settings.json`, not a project one — the
  companion should react in every project.
- When editing `~/.claude/settings.json`, preserve all existing keys and emit
  valid JSON. Never overwrite the file blindly.

## Steps

### 1. Preflight
```bash
PY=$(command -v python3 || command -v python || command -v py); echo "PY=$PY"
command -v curl >/dev/null && echo "curl ok" || echo "MISSING curl"
```
If `PY` is empty, stop and tell the user to install Python 3 (macOS:
`brew install python3`; Windows: the python.org installer — and Claude Code on
Windows also needs Git for Windows, whose Git Bash runs the bridges). `curl`
ships with macOS and Windows 10+; if missing, stop likewise. Use `"$PY"` for
every Python invocation below.

### 2. Install the config and the statusLine bridge
```bash
mkdir -p ~/.config/clawd-mochi/bin
[ -f ~/.config/clawd-mochi/config.json ] || cp "$SKILL_DIR/templates/config.json" ~/.config/clawd-mochi/config.json
cp "$SKILL_DIR/scripts/mochi-statusline" "$SKILL_DIR/scripts/mochi-statusline.py" ~/.config/clawd-mochi/bin/
chmod +x ~/.config/clawd-mochi/bin/mochi-statusline 2>/dev/null || true
echo "installed -> $HOME/.config/clawd-mochi"
```
An existing config is kept — it holds the user's event→emotion mapping; only
`device.host` gets updated below. Re-run this step after plugin updates to
refresh the bridge copy.

### 3. Discover the device
Skip if the user supplied a host in `$ARGUMENTS`.
```bash
"$PY" "$SKILL_DIR/scripts/find-mochi.py" --write
```
- **Exit 0** — it printed the host (e.g. `clawd-mochi.local` or an IP) and wrote
  it into the config. Use that as `<host>`.
- **Exit 2** — not found. Ask the user for Mochi's IP (use AskUserQuestion).
  Tell them where to find it: their router's client list, or — if the device
  shows the "CONNECT TO WI-FI" screen — it is in fallback-AP mode, so they
  should join the `ClaWD-Mochi` Wi-Fi and the host is always `192.168.4.1`.
  Then verify: `curl -s http://<ip>/state` must return JSON containing
  `roboLively`. If it does, set `device.host` to `<ip>` in
  `~/.config/clawd-mochi/config.json` and use `<ip>` as `<host>`.

### 4. Smoke test
```bash
curl -s --ipv4 --max-time 2 "http://<host>/state" | head -c 200; echo
curl -s --ipv4 --max-time 2 "http://<host>/robo?shot=blink&caption=hi%20:)"  >/dev/null
```
The device should blink and show a small caption. If `/state` fails, the host
is wrong or the device is offline — fix that before continuing.

### 5. Wire the statusLine (limit bar)
Check `statusLine` in `~/.claude/settings.json`:
- **Already configured** (`statusLine.command` points at a script): open that
  script. If it already pipes to a `mochi-statusline` (even an old path), just
  fix that path to `$HOME/.config/clawd-mochi/bin/mochi-statusline`.
  Otherwise, right after it captures stdin (e.g. `input=$(cat)`), add:
  `printf '%s' "$input" | "$HOME/.config/clawd-mochi/bin/mochi-statusline" &`
  If the script does not capture stdin into a variable, adapt minimally so both
  the status line and the bridge get the JSON. Don't duplicate the line if present.
- **Not configured**: install the bundled example —
  ```bash
  cp "$SKILL_DIR/scripts/statusline-command.sh" ~/.claude/statusline-command.sh
  chmod +x ~/.claude/statusline-command.sh 2>/dev/null || true
  ```
  then set in `~/.claude/settings.json`:
  `"statusLine": { "type": "command", "command": "bash ~/.claude/statusline-command.sh" }`.

### 6. Clean up a legacy pre-plugin install
Older setups (before this plugin existed) merged Mochi hooks into
`~/.claude/settings.json` by hand. Read that file: if any `hooks` entry
command references `mochi-hook` (a repo path or
`~/.config/clawd-mochi/bin/mochi-hook`), remove those entries — the plugin
registers its own now, and stale ones double-fire every event. Keep all
non-Mochi hooks intact. Also delete a leftover
`~/.config/clawd-mochi/bin/mochi-hook*` if present (only the statusLine bridge
belongs there now). If nothing references `mochi-hook`, skip this step.

### 7. Verify the hook bridge, then confirm
End-to-end dry-run through the real config:
```bash
echo '{"hook_event_name":"Stop"}' | sh "$SKILL_DIR/scripts/mochi-hook"
```
The device should play the Stop animation (default mapping: laugh, then relax
after 5 s). Then tell the user, concisely:
- Device host that was wired in, and that the smoke-test blink fired.
- Hook reactions come from the plugin itself and follow plugin updates; the
  statusLine bridge is a copy — re-run `/mochi:setup` after an update to
  refresh it.
- Hooks and statusLine apply to **new** Claude Code sessions (restart to see
  reactions).
- Toggles: `export CLAWD_MOCHI_DISABLED=1` (per-shell kill switch),
  `"enabled": false` in the config (mute all), `"statusline":{"enabled":false}`
  (mute bar).
- If reactions ever stop, the DHCP IP probably changed — re-run `/mochi:setup`.
  (Prefer `clawd-mochi.local` as the host on mDNS firmware; it never drifts.)
