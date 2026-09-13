# Clawd Mochi setup — reference

Detail behind the `setup` skill: the device contract, the hook → Event table,
the config schema, testing with `curl`, debugging. Both bridges are
fire-and-forget: if the device is offline, the request dies on its timeout and
Claude Code never notices.

The firmware side of the contract is ADR-0005 and SPEC §7–8 in
`npokc123/clawd-mochi-s3`. This plugin speaks only to the S3 firmware; the
ESP32-C3 routes (`/robo`, `/limit?pct=`, `/state` with `roboLively`) are gone.

## The two bridges

Stdlib-only Python 3.9+. They share `~/.config/clawd-mochi/config.json`
(override: `CLAWD_MOCHI_CONFIG`). Each has an extensionless `sh` launcher that
picks the machine's Python (`python3` → `python` → `py`).

| Bridge | Runs from | Trigger | Sends |
|---|---|---|---|
| `mochi-hook.py` (+ `mochi_core.py`) | the installed plugin (`hooks/hooks.json`) | 12 Claude Code hooks | `POST /api/event` |
| `mochi-statusline.py` | copy in `~/.config/clawd-mochi/bin/` | every statusLine render | `POST /api/limit` |

Neither blocks: the parent builds the body and re-spawns itself detached
(`--fire URL TIMEOUT_S BODY`); the worker POSTs with a 500 ms timeout, never
reads the answer, never retries, swallows every error. The host is resolved
IPv4-only (an AAAA lookup of `.local` hangs for seconds on macOS) and no
proxy is used.

## Device contract

```
GET  /api/capabilities   -> {"apiVersion":1,"device":"clawd-mochi-s3","fw":"…",
                             "states":[…],"reactions":[…],"captionMaxBytes":64}
POST /api/event          {"session_id":"…","seq":42,"state":"reading","caption":"main.cpp"}
POST /api/limit          {"five_hour_pct":37,"seven_day_pct":12}
```

- **Event** — `state` (a long phase, looped), `reaction` (a short clip on top),
  `caption` (≤ 64 bytes UTF-8, `""` hides). Any subset. Unknown names → `400`;
  that is what `find-mochi.py --check` guards against.
- **`session_id` + `seq`** — every hook Event carries Claude Code's
  `session_id` (subagents share the parent's) and a per-session counter kept in
  `~/.cache/clawd-mochi/sessions/<id>.json` (override: `CLAWD_MOCHI_STATE`),
  incremented under a file lock. Workers may overtake each other; the device
  drops a `seq` lower than the last it saw. Files untouched for a week are
  removed on `SessionStart`.
- **No `session_id`** — a Local Event: applied always, no ordering. Used by
  `curl` and the setup smoke test.
- The device owns all policy: minimum visibility 800 ms with coalescing, the
  Active session (the one whose last Event was `state: thinking`, i.e. the last
  `UserPromptSubmit`; other sessions only get through with `waiting_user` and
  `error`), return to `idle` after 60 s and `sleeping` after 10 min. The plugin
  sends no delayed requests.
- **Limit** — integers 0–100, one request per statusLine tick. A window missing
  from the statusLine payload is left out of the body (the device keeps that
  bar); no windows at all (API key, Bedrock, before the first reply) — nothing
  is sent.

## Default hook → Event table

Built into `mochi_core.DEFAULT_EVENTS` (SPEC §8.3):

| Hook | Event |
|---|---|
| `SessionStart` | reaction `blink` |
| `UserPromptSubmit` | state `thinking` |
| `PreToolUse` `Read` | state `reading`, caption `{file}` |
| `PreToolUse` `WebSearch` / `WebFetch` | state `reading`, caption «ищу в сети» / «читаю сайт» |
| `PreToolUse` `Grep`, `Glob` | state `reading` |
| `PreToolUse` `Edit`, `Write`, `NotebookEdit` | state `working`, caption `{file}` |
| `PreToolUse` anything else (`Bash`, `Task`, `mcp__*`, …) | state `working` |
| `PostToolUse` | state `thinking` (not `idle` — that was the C3 strobe) |
| `PostToolUseFailure`, `StopFailure` | reaction `error` |
| `Notification` `permission_prompt`, `elicitation_dialog`, `elicitation_url_dialog`, `agent_needs_input` | state `waiting_user` |
| `Notification` other types | — |
| `Stop` | state `idle` + reaction `success` (without `idle` the last `thinking` would stay for 60 s) |
| `SubagentStart` | state `working` |
| `SubagentStop` | — |
| `SessionEnd` | state `sleeping` (the device applies it only for the Active session) |
| `PreCompact` | state `working`, caption «сжимаю контекст» |

## Config schema (`config.json`)

- `enabled` — master switch for both bridges.
- `device.host` — `clawd-mochi.local` (mDNS, survives DHCP) or an IP.
- `device.timeout_ms` — HTTP timeout, default 500.
- `log_file` — optional; every sent body is appended here.
- `statusline.enabled` — default `true`; `false` mutes only the limit bars.
- `events` — overrides of the default table, **hook by hook**: a hook you put
  here replaces its whole default entry; hooks you leave out keep the default,
  and follow plugin updates. `null` mutes a hook. Keys starting with `_` are
  ignored.

An entry is either an Event — any of `state`, `reaction`, `caption` — or a
switch:

```json
"PreToolUse": {
  "by_tool": {
    "mcp__playwright__*": { "state": "reading", "caption": "браузер" },
    "Read|Grep|Glob":     { "state": "reading" },
    "TodoWrite":          null
  },
  "default": { "state": "working", "caption": "{tool}" }
}
```

- `by_tool` matches `tool_name`; `by_type` matches Notification's
  `notification_type`. Keys are case-sensitive globs (`*`, `?`, `[…]`), `|`
  separates alternatives. The first matching key in file order wins, then
  `default`. A matched `null` sends nothing.
- Caption placeholders: `{tool}` — `tool_name` (for MCP, the part after the last
  `__`), `{file}` — basename of `tool_input.file_path` / `notebook_path` /
  `path`, `{model}` — the model from `SessionStart`. Missing values become
  empty; whitespace collapses; the result is cut to 64 bytes on a character
  boundary. The device fits it into a 16-character bubble with «…» and shows
  ASCII and Cyrillic.
- An Event with `caption` replaces the bubble; a new `state` without `caption`
  clears it; the device hides it after 10 s.

After editing, run `/mochi:setup` again or just
`find-mochi.py --host <host> --check` from the skill's `scripts/`.

## Testing with curl

```sh
H=clawd-mochi.local
curl -s --ipv4 http://$H/api/capabilities; echo
# Local Events: always applied
curl --ipv4 -X POST -H 'Content-Type: application/json' -d '{"state":"reading","caption":"читаю README"}' http://$H/api/event
curl --ipv4 -X POST -H 'Content-Type: application/json' -d '{"reaction":"success"}' http://$H/api/event
curl --ipv4 -X POST -H 'Content-Type: application/json' -d '{"state":"thinkng"}' http://$H/api/event   # 400
# a host session: the second request is dropped (seq went back)
curl --ipv4 -X POST -H 'Content-Type: application/json' -d '{"session_id":"t","seq":2,"state":"working"}' http://$H/api/event
curl --ipv4 -X POST -H 'Content-Type: application/json' -d '{"session_id":"t","seq":1,"state":"idle"}' http://$H/api/event
curl -s --ipv4 http://$H/api/state; echo       # state, reaction, caption, session_id
# limit bars
curl --ipv4 -X POST -H 'Content-Type: application/json' -d '{"five_hour_pct":37,"seven_day_pct":12}' http://$H/api/limit
curl --ipv4 -X POST -H 'Content-Type: application/json' -d '{"seven_day_pct":null}' http://$H/api/limit   # hide one
```

## Debug

Neither bridge prints anything — set `log_file` and `tail -f` it. Dry-run a
hook through the real config (`<skill>` is the skill directory in the plugin):

```sh
echo '{"hook_event_name":"PreToolUse","session_id":"dbg","tool_name":"Read","tool_input":{"file_path":"/x/main.cpp"}}' \
  | sh <skill>/scripts/mochi-hook
# log: PreToolUse Read -> {"session_id": "dbg", "seq": 1, "state": "reading", "caption": "main.cpp"}
echo '{"rate_limits":{"five_hour":{"used_percentage":42.7},"seven_day":{"used_percentage":8}}}' \
  | ~/.config/clawd-mochi/bin/mochi-statusline
# log: statusline -> {"five_hour_pct": 43, "seven_day_pct": 8}
```

Host tests (no device): `python3 -m unittest discover -s tests` in the repo.

## Toggling

- `"enabled": false` — mute everything; `"statusline": {"enabled": false}` —
  only the bars; `export CLAWD_MOCHI_DISABLED=1` — per-shell kill switch.
- Mute one hook: `"events": {"SessionStart": null}`.

## Windows notes

- Claude Code on Windows runs hooks and the statusLine under **Git Bash** —
  required. PowerShell-only machines are not supported.
- Any of `python3`, `python`, `py` works. Beware the Microsoft Store `python`
  stub; install real Python first.
- The config is at `~/.config/clawd-mochi/` here too
  (`C:\Users\<you>\.config\clawd-mochi\`), identical across platforms.
- `seq` locking uses `msvcrt.locking` on Windows, `flock` elsewhere.
- Windows 10+ resolves `.local`; otherwise the subnet sweep finds the IP.

## Re-discovery

With `clawd-mochi.local` as the host the address never drifts. With an IP,
if reactions stop, re-run `/mochi:setup` (or `find-mochi.py --write`).
