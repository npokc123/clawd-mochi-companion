# Clawd Mochi — Claude Code companion plugin

Clawd Mochi is a palm-sized desk companion — an
ESP32-C3 with a round-ish 240×240 display where a pair of animated eyes lives.
This plugin makes it react to your Claude Code sessions: the eyes get curious
when Claude reads code, sweat when it runs commands, laugh when it finishes,
and a thin bar at the bottom of the screen tracks your 5-hour usage limit.

No cloud, no daemon: every Claude Code event becomes one fire-and-forget HTTP
request to the device over your home Wi-Fi.

## Requirements

- A Clawd Mochi on the same Wi-Fi network as your computer.
- Python 3.9+ (`python3`, `python`, or the `py` launcher on PATH).
- Claude Code. On **Windows** you also need [Git for Windows](https://gitforwindows.org)
  — Claude Code uses its Git Bash to run hooks (and recommends it anyway).

## Install

In Claude Code:

```
/plugin marketplace add npokc123/clawd-mochi-companion
/plugin install mochi@clawd-mochi
```

Then restart Claude Code and run:

```
/mochi:setup
```

The setup skill finds the device on your LAN (mDNS, then a subnet scan),
writes its address into `~/.config/clawd-mochi/config.json`, wires the
status-line bridge for the limit bar, and smoke-tests the whole chain — you
should see the eyes blink.

## What gets installed where

- **Hook reactions** — registered by the plugin itself (`hooks/hooks.json`),
  nothing to configure; they update together with the plugin.
- **Limit bar** — a small bridge copied to `~/.config/clawd-mochi/bin/` and
  called from your status-line script (settings.json can't reference files
  inside the plugin, so this one lives outside it).
- **Your mapping** — `~/.config/clawd-mochi/config.json` decides which event
  shows which emotion. It's yours to edit; setup never overwrites it. See
  [skills/setup/reference.md](skills/setup/reference.md) for the schema.

## Turning it off

- `export CLAWD_MOCHI_DISABLED=1` — kill switch for one shell.
- `"enabled": false` in the config — mute everything.
- `"statusline": { "enabled": false }` — mute just the limit bar.
- `/plugin uninstall mochi@clawd-mochi` — remove it all.

If the eyes stop reacting, the device's DHCP address probably changed —
re-run `/mochi:setup`.

## License

MIT — see [LICENSE](LICENSE). The Clawd Mochi firmware and case are a separate
project.
