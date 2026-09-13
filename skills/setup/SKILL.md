---
name: setup
description: Connect this machine's Claude Code to a Clawd Mochi S3 desk companion — discover the device, check the config against it, wire the statusLine bridge, smoke-test.
disable-model-invocation: true
allowed-tools: Bash, Read, Write, Edit
argument-hint: "[optional host/IP]"
---

# Clawd Mochi — Claude Code setup

Wire this machine's Claude Code to a Clawd Mochi S3 (ESP32-S3, 240×240
display). The plugin registers the hook bridge by itself (`hooks/hooks.json`
runs `scripts/mochi-hook` from the installed plugin, so it follows plugin
updates). What is left for this skill:

1. install the config and the statusLine bridge,
2. discover the device and write its host into the config,
3. check the config against the device's dictionary,
4. smoke-test, wire the statusLine, clean up old installs.

In the commands below `$SKILL_DIR` means this skill's base directory (shown
when the skill loads) — set it first in each shell you use. All commands are
POSIX shell: bash on macOS/Linux, **Git Bash on Windows**.

If the user passed a host/IP as `$ARGUMENTS`, use it as `<host>` and pass it
to `find-mochi.py --host` instead of discovering.

Config schema, the default hook → Event table and debugging live in
[reference.md](reference.md) — read it only when tuning or debugging.

## Conventions

- **The statusLine bridge runs from a copy** in `~/.config/clawd-mochi/bin/`:
  `settings.json` cannot expand `${CLAUDE_PLUGIN_ROOT}`, and the plugin's cache
  path changes on every update. Hooks need no copy.
- Touch the **user-level** `~/.claude/settings.json`, not a project one.
- When editing `~/.claude/settings.json`, preserve all existing keys and emit
  valid JSON. Never overwrite the file blindly.

## Steps

### 1. Preflight
```bash
PY=$(command -v python3 || command -v python || command -v py); echo "PY=$PY"; "$PY" --version
command -v curl >/dev/null && echo "curl ok" || echo "MISSING curl"
```
If `PY` is empty or older than 3.9, stop and tell the user to install Python 3
(macOS: `brew install python3`; Windows: the python.org installer, plus Git for
Windows for Git Bash). Use `"$PY"` for every Python call below.

### 2. Install the config and the statusLine bridge
```bash
mkdir -p ~/.config/clawd-mochi/bin
[ -f ~/.config/clawd-mochi/config.json ] || cp "$SKILL_DIR/templates/config.json" ~/.config/clawd-mochi/config.json
cp "$SKILL_DIR/scripts/mochi-statusline" "$SKILL_DIR/scripts/mochi-statusline.py" ~/.config/clawd-mochi/bin/
chmod +x ~/.config/clawd-mochi/bin/mochi-statusline 2>/dev/null || true
```
An existing config is kept. **Legacy check:** if its `events` entries use the
old ESP32-C3 keys (`expr`, `shot`, `sweat`, `curious`, `reset_after_ms`, …),
that config was written for the old firmware: copy it to
`~/.config/clawd-mochi/config.c3.json` as a backup, then set `"events": {}` in
the live config (the built-in table covers the defaults). Tell the user you
did it.

### 3. Discover the device
```bash
"$PY" "$SKILL_DIR/scripts/find-mochi.py" --write            # or: --host <host> --write
```
- **Exit 0** — stdout is the host (`clawd-mochi.local` or an IP), written into
  the config. Use it as `<host>`.
- **Exit 2** — not found. Ask the user for the device's IP (AskUserQuestion):
  their router's client list, or the address the device shows after joining
  Wi-Fi. If the device shows a QR code for the `ClaWD-Mochi` network, it has
  not joined the home Wi-Fi yet — they should scan it and finish setup first.
  Then re-run with `--host <ip> --write`.

### 4. Check the config against the device
```bash
"$PY" "$SKILL_DIR/scripts/find-mochi.py" --host <host> --check
```
- **Exit 0** — `config: ok`.
- **Exit 3** — every problem is printed as `config: events.<hook>...` (unknown
  state or reaction, a typo in a key, a caption over the byte limit). Show
  them to the user, fix `~/.config/clawd-mochi/config.json` with them (the
  device's valid names are printed on the `fw ...` line), and re-run until
  it passes. Never leave a failing config: the device rejects an unknown name
  with `400`, so that hook would silently do nothing.

### 5. Smoke test
```bash
curl -s --ipv4 --max-time 2 -X POST -H 'Content-Type: application/json' \
  -d '{"reaction":"blink","caption":"привет!"}' -w '%{http_code}\n' "http://<host>/api/event"
```
Expect `204`; the device should blink and show the caption. Anything else
means the host is wrong or the device is offline — fix that first.

### 6. Wire the statusLine (limit bars)
Check `statusLine` in `~/.claude/settings.json`:
- **Already configured**: open its script. If it already pipes to a
  `mochi-statusline` (any path), fix the path to
  `$HOME/.config/clawd-mochi/bin/mochi-statusline`. Otherwise, right after it
  captures stdin (e.g. `input=$(cat)`), add:
  `printf '%s' "$input" | "$HOME/.config/clawd-mochi/bin/mochi-statusline" &`
  If the script does not capture stdin into a variable, adapt minimally so
  both the status line and the bridge get the JSON. Don't duplicate the line.
- **Not configured**: install the bundled example —
  ```bash
  cp "$SKILL_DIR/scripts/statusline-command.sh" ~/.claude/statusline-command.sh
  chmod +x ~/.claude/statusline-command.sh 2>/dev/null || true
  ```
  then set `"statusLine": { "type": "command", "command": "bash ~/.claude/statusline-command.sh" }`.

### 7. Clean up pre-plugin installs
If any `hooks` entry in `~/.claude/settings.json` references `mochi-hook`
(hand-merged by very old setups), remove those entries — the plugin registers
its own and stale ones double-fire. Keep all non-Mochi hooks. Delete a
leftover `~/.config/clawd-mochi/bin/mochi-hook*` if present. Skip if none.

### 8. Verify the hook bridge, then report
```bash
echo '{"hook_event_name":"Stop"}' | sh "$SKILL_DIR/scripts/mochi-hook"
```
No `session_id` makes this a Local Event, which the device always applies:
it should play `success`. Then tell the user, concisely:
- the host that was wired in, and that the smoke test fired;
- hooks follow plugin updates; the statusLine bridge is a copy — re-run
  `/mochi:setup` after an update;
- hooks apply to new sessions; an already open session needs
  `/reload-plugins` (until then it keeps the hooks of the previous install);
- toggles: `export CLAWD_MOCHI_DISABLED=1`, `"enabled": false`,
  `"statusline": {"enabled": false}`; custom mapping — `events` in the config
  (reference.md).
