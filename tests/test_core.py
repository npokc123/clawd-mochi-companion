"""Host tests of the hook mapping and the statusline body: python3 -m unittest"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "skills" / "setup" / "scripts"
sys.path.insert(0, str(SCRIPTS))
import mochi_core as core  # noqa: E402


def load(name: str, file: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


statusline = load("mochi_statusline", "mochi-statusline.py")

CAPS = {
    "apiVersion": 1, "device": "clawd-mochi-s3", "fw": "0.0.1",
    "states": ["idle", "thinking", "reading", "working", "waiting_user", "sleeping"],
    "reactions": ["success", "error", "nudge", "blink", "wink", "wake", "shutdown"],
    "captionMaxBytes": 64,
}


def event(hook: str, cfg: dict | None = None, **payload) -> dict | None:
    payload["hook_event_name"] = hook
    entry = core.resolve(core.effective_events(cfg or {}), payload)
    return None if entry is None else core.build_event(entry, payload)


class DefaultTable(unittest.TestCase):
    """SPEC §8.3 row by row."""

    def test_session_start_blinks(self):
        self.assertEqual(event("SessionStart"), {"reaction": "blink"})

    def test_prompt_claims_the_session_with_thinking(self):
        # CompanionLogic (#16) makes a session Active on state=thinking.
        self.assertEqual(event("UserPromptSubmit"), {"state": "thinking"})

    def test_read_tools_are_reading(self):
        for tool in ("Read", "Grep", "Glob", "WebFetch", "WebSearch"):
            self.assertEqual(event("PreToolUse", tool_name=tool)["state"], "reading", tool)

    def test_other_tools_are_working(self):
        for tool in ("Bash", "Edit", "Write", "Task", "mcp__playwright__browser_click"):
            self.assertEqual(event("PreToolUse", tool_name=tool)["state"], "working", tool)

    def test_post_tool_goes_back_to_thinking_not_idle(self):
        self.assertEqual(event("PostToolUse", tool_name="Read"), {"state": "thinking"})

    def test_failures_are_error(self):
        self.assertEqual(event("PostToolUseFailure", tool_name="Bash"), {"reaction": "error"})
        self.assertEqual(event("StopFailure"), {"reaction": "error"})

    def test_notification_by_type(self):
        for kind in ("permission_prompt", "elicitation_dialog", "elicitation_url_dialog", "agent_needs_input"):
            self.assertEqual(event("Notification", notification_type=kind), {"state": "waiting_user"}, kind)
        for kind in ("idle_prompt", "elicitation_complete", "agent_completed", "auth_success"):
            self.assertIsNone(event("Notification", notification_type=kind), kind)
        self.assertIsNone(event("Notification"))

    def test_stop_and_subagents(self):
        self.assertEqual(event("Stop"), {"reaction": "success"})
        self.assertEqual(event("SubagentStart"), {"state": "working"})
        self.assertIsNone(event("SubagentStop"))

    def test_session_end_and_compact(self):
        self.assertEqual(event("SessionEnd"), {"state": "sleeping"})
        self.assertEqual(event("PreCompact"), {"state": "working", "caption": "сжимаю контекст"})

    def test_unknown_hook_sends_nothing(self):
        self.assertIsNone(event("SomethingNew"))

    def test_default_table_passes_its_own_check(self):
        self.assertEqual(core.check_events(core.DEFAULT_EVENTS, CAPS), [])


class UserConfig(unittest.TestCase):
    def test_override_replaces_one_hook(self):
        cfg = {"events": {"Stop": {"reaction": "wink"}}}
        self.assertEqual(event("Stop", cfg), {"reaction": "wink"})
        self.assertEqual(event("SessionStart", cfg), {"reaction": "blink"})

    def test_null_mutes_a_hook(self):
        self.assertIsNone(event("SessionStart", {"events": {"SessionStart": None}}))

    def test_glob_first_match_in_file_order(self):
        cfg = {"events": {"PreToolUse": {
            "by_tool": {"mcp__playwright__*": {"state": "reading", "caption": "браузер"},
                        "mcp__*": {"state": "working"}},
            "default": {"state": "working"}}}}
        self.assertEqual(event("PreToolUse", cfg, tool_name="mcp__playwright__browser_snapshot"),
                         {"state": "reading", "caption": "браузер"})
        self.assertEqual(event("PreToolUse", cfg, tool_name="mcp__github__get_issue"), {"state": "working"})

    def test_glob_is_case_sensitive(self):
        cfg = {"events": {"PreToolUse": {"by_tool": {"read": {"state": "reading"}}}}}
        self.assertIsNone(event("PreToolUse", cfg, tool_name="Read"))

    def test_matched_null_sends_nothing(self):
        cfg = {"events": {"PreToolUse": {"by_tool": {"TodoWrite": None}, "default": {"state": "working"}}}}
        self.assertIsNone(event("PreToolUse", cfg, tool_name="TodoWrite"))


class Caption(unittest.TestCase):
    def test_file_placeholder_is_basename(self):
        self.assertEqual(event("PreToolUse", tool_name="Read", tool_input={"file_path": "/a/b/README.md"}),
                         {"state": "reading", "caption": "README.md"})

    def test_notebook_path(self):
        e = event("PreToolUse", tool_name="NotebookEdit", tool_input={"notebook_path": "C:\\x\\nb.ipynb"})
        self.assertEqual(e["caption"], "nb.ipynb")

    def test_missing_file_gives_empty_caption(self):
        self.assertEqual(event("PreToolUse", tool_name="Read", tool_input={}),
                         {"state": "reading", "caption": ""})

    def test_tool_and_model(self):
        payload = {"tool_name": "mcp__github__get_issue"}
        self.assertEqual(core.render_caption("{tool} · {model}", payload, "Opus"), "get_issue · Opus")
        self.assertEqual(core.render_caption("{tool}", {"tool_name": "Bash"}), "Bash")

    def test_unknown_braces_stay(self):
        self.assertEqual(core.render_caption("{x} {", {}), "{x} {")

    def test_whitespace_collapses(self):
        self.assertEqual(core.render_caption("a\n  b\t", {}), "a b")

    def test_cut_on_character_boundary(self):
        text = "я" * 40  # 80 bytes
        cut = core.render_caption(text, {})
        self.assertEqual(cut, "я" * 32)
        self.assertEqual(core.truncate_utf8("a" + "я" * 40, 64), "a" + "я" * 31)
        self.assertEqual(len(core.truncate_utf8("a" + "я" * 40, 64).encode()), 63)
        self.assertEqual(core.truncate_utf8("😀😀", 5), "😀")

    def test_model_of(self):
        self.assertEqual(core.model_of({"model": "claude-opus-5"}), "claude-opus-5")
        self.assertEqual(core.model_of({"model": {"id": "x", "display_name": "Opus"}}), "Opus")
        self.assertEqual(core.model_of({}), "")

    def test_long_session_id_cut_for_device(self):
        self.assertEqual(core.device_session_id("5b0c3e1e-0000-4000-8000-000000000000"),
                         "5b0c3e1e-0000-4000-8000-000000000000")
        self.assertEqual(len(core.device_session_id("x" * 100)), 63)


class Seq(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = core.SessionStore(Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def test_monotonic_per_session_and_remembers_model(self):
        self.assertEqual(self.store.next("a", "Opus"), (1, "Opus"))
        self.assertEqual(self.store.next("a"), (2, "Opus"))
        self.assertEqual(self.store.next("b"), (1, ""))

    def test_threads_never_share_a_seq(self):
        got: list = []
        lock = threading.Lock()

        def worker():
            for _ in range(25):
                seq, _ = self.store.next("s")
                with lock:
                    got.append(seq)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(sorted(got), list(range(1, 201)))

    def test_processes_never_share_a_seq(self):
        code = ("import sys; sys.path.insert(0, sys.argv[1]); import mochi_core as c; from pathlib import Path\n"
                "s = c.SessionStore(Path(sys.argv[2]))\n"
                "print(' '.join(str(s.next('p')[0]) for _ in range(20)))")
        procs = [subprocess.Popen([sys.executable, "-c", code, str(SCRIPTS), self.tmp.name],
                                  stdout=subprocess.PIPE, text=True) for _ in range(5)]
        seqs = [int(n) for p in procs for n in p.communicate()[0].split()]
        self.assertEqual(sorted(seqs), list(range(1, 101)))

    def test_corrupt_file_restarts(self):
        self.store.path("a").write_text("not json")
        self.assertEqual(self.store.next("a"), (1, ""))

    def test_hostile_session_id_stays_in_dir(self):
        p = self.store.path("../../etc/passwd")
        self.assertEqual(p.parent, Path(self.tmp.name))

    def test_prune_forgets_old_sessions(self):
        self.store.next("old")
        self.store.next("new")
        old = self.store.path("old")
        os.utime(old, (0, 0))
        self.store.prune()
        self.assertFalse(old.exists())
        self.assertTrue(self.store.path("new").exists())


class CheckConfig(unittest.TestCase):
    def check(self, events):
        return core.check_events(events, CAPS)

    def test_typo_in_state_is_caught(self):
        errors = self.check({"UserPromptSubmit": {"state": "thinkng"}})
        self.assertEqual(len(errors), 1)
        self.assertIn("thinkng", errors[0])

    def test_typo_in_nested_reaction_is_caught(self):
        errors = self.check({"PreToolUse": {"by_tool": {"Bash": {"reaction": "sucess"}}}})
        self.assertIn("sucess", errors[0])

    def test_unknown_key_and_hook(self):
        self.assertIn("'stat'", self.check({"Stop": {"stat": "idle"}})[0])
        self.assertIn("PreTool", self.check({"PreTool": {"state": "idle"}})[0])

    def test_long_static_caption(self):
        self.assertTrue(self.check({"Stop": {"reaction": "success", "caption": "я" * 33}}))
        self.assertEqual(self.check({"Stop": {"reaction": "success", "caption": "я" * 32}}), [])

    def test_null_and_notes_are_fine(self):
        self.assertEqual(self.check({"Stop": None, "_notes": "x"}), [])

    def test_empty_entry(self):
        self.assertTrue(self.check({"Stop": {}}))


class Statusline(unittest.TestCase):
    def test_both_windows_rounded_and_clamped(self):
        payload = {"rate_limits": {"five_hour": {"used_percentage": 14.000000000000002},
                                   "seven_day": {"used_percentage": 100.7}}}
        self.assertEqual(statusline.limit_body(payload), {"five_hour_pct": 14, "seven_day_pct": 100})

    def test_missing_window_is_left_out(self):
        payload = {"rate_limits": {"seven_day": {"used_percentage": 41.2, "resets_at": 1}}}
        self.assertEqual(statusline.limit_body(payload), {"seven_day_pct": 41})

    def test_no_limits_sends_nothing(self):
        self.assertEqual(statusline.limit_body({}), {})
        self.assertEqual(statusline.limit_body({"rate_limits": {"five_hour": {}}}), {})


class HookEndToEnd(unittest.TestCase):
    """The hook script with a device that is not there: must exit 0 fast."""

    def test_logs_the_body_it_would_send(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "hook.log"
            config = Path(tmp) / "config.json"
            config.write_text(json.dumps({"device": {"host": "127.0.0.1:9", "timeout_ms": 100},
                                          "log_file": str(log)}))
            env = dict(os.environ, CLAWD_MOCHI_CONFIG=str(config), CLAWD_MOCHI_STATE=str(Path(tmp) / "s"))
            env.pop("CLAWD_MOCHI_DISABLED", None)
            payload = {"hook_event_name": "PreToolUse", "session_id": "s1", "tool_name": "Read",
                       "tool_input": {"file_path": "/x/main.cpp"}}
            proc = subprocess.run([sys.executable, str(SCRIPTS / "mochi-hook.py")], input=json.dumps(payload),
                                  capture_output=True, text=True, env=env, timeout=5)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            line = log.read_text()
            self.assertIn('"session_id": "s1"', line)
            self.assertIn('"seq": 1', line)
            self.assertIn('"state": "reading"', line)
            self.assertIn('"caption": "main.cpp"', line)

    def test_garbage_stdin_is_silent(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "c.json"
            config.write_text('{"device":{"host":"127.0.0.1:9"}}')
            env = dict(os.environ, CLAWD_MOCHI_CONFIG=str(config), CLAWD_MOCHI_STATE=tmp)
            proc = subprocess.run([sys.executable, str(SCRIPTS / "mochi-hook.py")], input="not json",
                                  capture_output=True, text=True, env=env, timeout=5)
            self.assertEqual((proc.returncode, proc.stdout, proc.stderr), (0, "", ""))


if __name__ == "__main__":
    unittest.main()
