#!/usr/bin/env bash
# Example Claude Code statusLine command for Clawd Mochi.
#
# It does two things:
#   1. Forwards the 5-hour usage to Mochi's limit bar (fire-and-forget).
#   2. Prints a minimal status line.
#
# If you already have a statusline-command.sh, you do NOT need this file —
# just add the one forwarding line (marked below) to your own script, right
# after it has captured stdin into a variable.
#
# Works on macOS/Linux and on Windows under Git Bash (the shell Claude Code
# uses there). Wire it up in ~/.claude/settings.json:
#   "statusLine": { "type": "command", "command": "bash ~/.claude/statusline-command.sh" }

input=$(cat)

# ── forward 5h usage to Mochi (non-blocking) ────────────────────────────────
# The bridge is installed by /mochi:setup. The trailing & keeps the prompt
# snappy; the bridge also detaches its own HTTP worker, so this is doubly
# non-blocking.
printf '%s' "$input" | "$HOME/.config/clawd-mochi/bin/mochi-statusline" &

# ── your status line (replace with whatever you like) ───────────────────────
PY=$(command -v python3 || command -v python || command -v py)
model=$(printf '%s' "$input" | "$PY" -c 'import sys,json; print(json.load(sys.stdin).get("model",{}).get("display_name","Claude"))' 2>/dev/null)
dir=$(printf '%s'  "$input" | "$PY" -c 'import sys,json,os; print(os.path.basename(json.load(sys.stdin).get("workspace",{}).get("current_dir","")))' 2>/dev/null)
printf '%s  %s' "${model:-Claude}" "${dir:-}"
