# Clawd Mochi — Claude Code companion plugin

Clawd Mochi S3 is a palm-sized desk companion — an ESP32-S3 with a 240×240
display where a small robot lives. This plugin makes it follow your Claude
Code sessions: it thinks while Claude thinks, reads when Claude reads code,
works when it runs commands or edits files, looks at you when Claude needs
permission, cheers when a reply is done and winces on errors. Two thin bars
at the bottom of the screen track your 5-hour and weekly usage limits.

No cloud, no daemon: every Claude Code event becomes one fire-and-forget HTTP
request to the device over your home Wi-Fi. The device decides how to show
it — rapid tool calls never turn into a strobe.

This version talks only to the S3 firmware
([clawd-mochi-s3](https://github.com/npokc123/clawd-mochi-s3)). For the older
ESP32-C3 device use plugin 1.x: the legacy branch
[`c3`](https://github.com/npokc123/clawd-mochi-companion/tree/c3) (tag
`v1.1.1`, frozen). Install it from a local clone:

```
git clone -b c3 https://github.com/npokc123/clawd-mochi-companion clawd-mochi-c3
/plugin marketplace add /path/to/clawd-mochi-c3
/plugin install mochi@clawd-mochi
```

Both use the marketplace name `clawd-mochi`, so only one can be installed at
a time.

## Requirements

- A Clawd Mochi S3 on the same Wi-Fi network as your computer.
- Python 3.9+ (`python3`, `python`, or the `py` launcher on PATH).
- Claude Code. On **Windows** you also need [Git for Windows](https://gitforwindows.org)
  — Claude Code uses its Git Bash to run hooks.

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

The setup skill finds the device (`clawd-mochi.local`, then a subnet scan),
checks your config against the states and reactions the device knows, wires
the status-line bridge for the limit bars, and smoke-tests the chain — you
should see the robot blink.

## What gets installed where

- **Hook reactions** — registered by the plugin itself (`hooks/hooks.json`);
  they update together with the plugin.
- **Limit bars** — a small bridge copied to `~/.config/clawd-mochi/bin/` and
  called from your status-line script (settings.json can't reference files
  inside the plugin).
- **Your config** — `~/.config/clawd-mochi/config.json`: the device address
  and, optionally, your own hook → Event mapping (for example, your MCP tools).
  Setup never overwrites it. See
  [skills/setup/reference.md](skills/setup/reference.md).

## Turning it off

- `export CLAWD_MOCHI_DISABLED=1` — kill switch for one shell.
- `"enabled": false` in the config — mute everything.
- `"statusline": { "enabled": false }` — mute just the limit bars.
- `/plugin uninstall mochi@clawd-mochi` — remove it all.

## Development

Host tests, no device needed: `python3 -m unittest discover -s tests`.

## License

MIT — see [LICENSE](LICENSE). The Clawd Mochi firmware and case are a separate
project.
