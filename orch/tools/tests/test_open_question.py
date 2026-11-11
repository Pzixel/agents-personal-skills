import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.realpath(__file__)), ".."))

import gate_policy  # noqa: E402
import transcript  # noqa: E402

ROOT = Path(__file__).resolve().parents[3]
HOOK = ROOT / "orch" / "hooks" / "open-question.py"
STATUSLINE = ROOT / "orch" / "tools" / "open-question-statusline.py"
SID = "6f1c2a3b-4d5e-4f60-8a7b-9c0d1e2f3a4b"
QUESTION = "QUESTION: switch the tile to hRR too (#583)? Recommended: yes, now"


def post_tool(tool, tool_input, **extra):
    return dict({"session_id": SID, "transcript_path": "/x/" + SID + ".jsonl", "cwd": "/repo",
                 "hook_event_name": "PostToolUse", "tool_name": tool, "tool_input": tool_input,
                 "tool_response": {}}, **extra)


def entry(kind, content):
    return {"type": kind, "message": {"role": kind, "content": content}}


def tool_use(use_id, name, args):
    return entry("assistant", [{"type": "tool_use", "id": use_id, "name": name, "input": args}])


def tool_result(use_id, text):
    return entry("user", [{"type": "tool_result", "tool_use_id": use_id, "content": text}])


CRON_PROMPT = "Check the 09:30 UTC oracle-conciliation run"
CRON = [tool_use("c1", "CronCreate", {"cron": "30 9 5 11 *", "prompt": CRON_PROMPT}),
        tool_result("c1", "Scheduled one-shot task 1db1ccfe (30 9 5 11 *). Next run: Nov 5 9:30am.")]
AGENT = [tool_use("a1", "Agent", {"prompt": "build", "run_in_background": True}),
         tool_result("a1", "Async agent launched successfully.\nagentId: ab12cd34 (internal ID)")]


LOOP_PROMPT = "/loop keep working until the numbers hold; then post the reply-source table"
RECURRING_PROMPT = "Re-read the reply-lint log"
RECURRING = [tool_use("r1", "CronCreate", {"cron": "0 * * * *", "prompt": RECURRING_PROMPT, "recurring": True}),
             tool_result("r1", "Scheduled recurring job 9e8d7c6b (0 * * * *).")]


def stamped(record, timestamp):
    return dict(record, timestamp=timestamp)


# One /loop wakeup in the order the harness writes it: the ScheduleWakeup call
# and its result, the two prompt-queue records at the due instant, the fire entry, the healthy-loop note.
WAKEUP = [
    stamped(tool_use("w1", "ScheduleWakeup", {"delaySeconds": 1800, "reason": "fallback", "prompt": LOOP_PROMPT}),
            "2026-11-05T10:28:00.000Z"),
    stamped(tool_result("w1", "Next wakeup scheduled for 12:59:00 (in 1800s). If you owe the user a status"
                              " update this tick, write it now."), "2026-11-05T10:29:01.000Z"),
    {"type": "queue-operation", "operation": "enqueue", "timestamp": "2026-11-05T10:59:01.071Z", "content": LOOP_PROMPT},
    {"type": "queue-operation", "operation": "dequeue", "timestamp": "2026-11-05T10:59:01.097Z"},
    {"type": "system", "subtype": "scheduled_task_fire", "content": "Claude resuming /loop wakeup (Nov 5 12:59pm)",
     "timestamp": "2026-11-05T10:59:01.068Z", "taskId": "f1716d29"},
    dict(entry("user", "[1 prior /loop wakeup found nothing actionable; loop is healthy.]"), isMeta=True,
         timestamp="2026-11-05T10:59:01.100Z"),
]


def facts_of(entries):
    facts = None
    for facts in transcript.background_watch_scan(list(enumerate(entries)), ""):
        pass
    return facts


class DecisionTest(unittest.TestCase):
    def test_question_notification_appends_the_question_without_prefix(self):
        self.assertEqual(gate_policy.open_question_decision(post_tool("PushNotification", {"message": QUESTION, "status": "proactive"})),
                         ("append", "switch the tile to hRR too (#583)? Recommended: yes, now"))

    def test_multi_line_question_is_pinned_as_one_line(self):
        message = "QUESTION: buy 3 domains?\nRecommended: no,\n  reuse the pool"
        self.assertEqual(gate_policy.open_question_decision(post_tool("PushNotification", {"message": message})),
                         ("append", "buy 3 domains? Recommended: no, reuse the pool"))

    def test_session_id_that_is_not_a_uuid_does_nothing(self):
        for sid in ("../../.ssh/authorized_keys", "", None):
            with self.subTest(sid=sid):
                payload = post_tool("PushNotification", {"message": QUESTION}, session_id=sid)
                self.assertEqual(gate_policy.open_question_decision(payload), ("none", None))

    def test_other_notification_leaves_the_file(self):
        self.assertEqual(gate_policy.open_question_decision(post_tool("PushNotification", {"message": "Rollout done", "status": "proactive"})),
                         ("none", None))

    def test_ask_user_question_clears(self):
        self.assertEqual(gate_policy.open_question_decision(post_tool("AskUserQuestion", {"questions": []})), ("clear", None))

    def test_subagent_question_does_nothing(self):
        payload = post_tool("PushNotification", {"message": QUESTION}, agent_id="ab12cd34", agent_type="orch:builder")
        self.assertEqual(gate_policy.open_question_decision(payload), ("none", None))

    def test_stop_with_a_live_background_agent_keeps_the_file(self):
        payload = {"session_id": SID, "hook_event_name": "Stop"}
        self.assertEqual(gate_policy.open_question_decision(payload, facts_of([entry("user", "go"), *AGENT])), ("none", None))

    def test_stop_with_nothing_running_clears(self):
        payload = {"session_id": SID, "hook_event_name": "Stop"}
        self.assertEqual(gate_policy.open_question_decision(payload, facts_of([entry("user", "go")])), ("clear", None))

    def test_stop_with_only_a_pending_wakeup_keeps_the_file(self):
        payload = {"session_id": SID, "hook_event_name": "Stop"}
        self.assertEqual(gate_policy.open_question_decision(payload, facts_of([entry("user", "go"), *WAKEUP[:2]])),
                         ("none", None))

    def test_wakeup_prompt_keeps_the_file_at_every_point_of_its_fire(self):
        for cut in range(2, len(WAKEUP) + 1):
            facts = facts_of([entry("user", "go"), *WAKEUP[:cut]])
            with self.subTest(entries=cut):
                payload = {"session_id": SID, "hook_event_name": "UserPromptSubmit", "prompt": LOOP_PROMPT}
                self.assertEqual(gate_policy.open_question_decision(payload, facts), ("none", None))
                payload["prompt"] = "yes, switch it"
                self.assertEqual(gate_policy.open_question_decision(payload, facts), ("clear", None))

    def test_wakeup_prompt_typed_after_the_loop_was_stopped_clears(self):
        stop = [tool_use("w2", "ScheduleWakeup", {"stop": True}), tool_result("w2", "Wakeup cancelled.")]
        payload = {"session_id": SID, "hook_event_name": "UserPromptSubmit", "prompt": LOOP_PROMPT}
        self.assertEqual(gate_policy.open_question_decision(payload, facts_of([entry("user", "go"), *WAKEUP, *stop])),
                         ("clear", None))

    def test_recurring_job_prompt_keeps_the_file(self):
        payload = {"session_id": SID, "hook_event_name": "UserPromptSubmit", "prompt": RECURRING_PROMPT}
        self.assertEqual(gate_policy.open_question_decision(payload, facts_of([entry("user", "go"), *RECURRING])),
                         ("none", None))

    def test_scheduled_job_prompt_keeps_the_file(self):
        payload = {"session_id": SID, "hook_event_name": "UserPromptSubmit", "prompt": CRON_PROMPT}
        self.assertEqual(gate_policy.open_question_decision(payload, facts_of([entry("user", "go"), *CRON])), ("none", None))

    def test_scheduled_job_prompt_after_its_fire_entry_keeps_the_file(self):
        fired = {"type": "system", "subtype": "scheduled_task_fire", "taskId": "1db1ccfe"}
        payload = {"session_id": SID, "hook_event_name": "UserPromptSubmit", "prompt": CRON_PROMPT}
        self.assertEqual(gate_policy.open_question_decision(payload, facts_of([entry("user", "go"), *CRON, fired])), ("none", None))

    def test_typed_owner_prompt_clears(self):
        payload = {"session_id": SID, "hook_event_name": "UserPromptSubmit", "prompt": "yes, switch it"}
        self.assertEqual(gate_policy.open_question_decision(payload, facts_of([entry("user", "go"), *CRON])), ("clear", None))

    def test_task_notification_prompt_keeps_the_file(self):
        payload = {"session_id": SID, "hook_event_name": "UserPromptSubmit",
                   "prompt": "<task-notification><task-id>ab12cd34</task-id></task-notification>"}
        self.assertEqual(gate_policy.open_question_decision(payload, None), ("none", None))


class PinnedQuestionHandoffTest(unittest.TestCase):
    VERDICT = {"handoff": True, "sentence": "The retry-window question is still open.", "deferral": False,
               "deferral_sentence": "none"}
    REPLY = "The builder runs the migration. The retry-window question (3 or 5 days) is still open."

    def decision(self, pinned_text, entries):
        pinned = gate_policy.question_pinned_while_working(pinned_text, facts_of(entries))
        return gate_policy.reply_lint_decision(self.REPLY, False, self.VERDICT, pinned_while_working=pinned)["decision"]

    def test_hand_off_with_a_pinned_question_and_live_work_passes(self):
        self.assertEqual(self.decision("retry window 3 or 5 days?\n", [entry("user", "go"), *AGENT]), "allow")

    def test_hand_off_with_a_pinned_question_and_nothing_running_blocks(self):
        self.assertEqual(self.decision("retry window 3 or 5 days?\n", [entry("user", "go")]), "block")

    def test_hand_off_with_no_pin_and_live_work_blocks(self):
        for pinned_text in ("", "\n"):
            with self.subTest(pinned_text=pinned_text):
                self.assertEqual(self.decision(pinned_text, [entry("user", "go"), *AGENT]), "block")


def run(script, payload, home, columns="200", env=None, text=True):
    env = dict(os.environ, HOME=home, COLUMNS=columns, **(env or {}))
    data = payload if text or isinstance(payload, bytes) else payload.encode("utf-8")
    if not text:
        return subprocess.run([sys.executable, str(script)], input=data, capture_output=True, env=env)
    return subprocess.run([sys.executable, str(script)], input=payload, capture_output=True, text=True,
                          encoding="utf-8", env=env)


STATUS_STDIN = json.dumps({"session_id": SID, "session_name": "email-number-hrr-chart",
                           "transcript_path": "/x/" + SID + ".jsonl", "cwd": "/repo",
                           "model": {"id": "claude-opus-5-5", "display_name": "Opus"},
                           "workspace": {"current_dir": "/repo", "project_dir": "/repo"}, "version": "2.1.322"})


class EndToEndTest(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp()
        self.file = Path(gate_policy.open_question_path(self.home, SID))

    def test_two_questions_pin_and_render_then_ask_clears(self):
        for text in (QUESTION, "QUESTION: buy 3 domains? Recommended: no"):
            out = run(HOOK, json.dumps(post_tool("PushNotification", {"message": text})), self.home)
            self.assertEqual((out.returncode, out.stdout), (0, ""))
        line = run(STATUSLINE, STATUS_STDIN, self.home).stdout
        self.assertEqual(line, "OPEN QUESTIONS 2: switch the tile to hRR too (#583)? Recommended: yes, now"
                               " | buy 3 domains? Recommended: no\n")
        run(HOOK, json.dumps(post_tool("AskUserQuestion", {"questions": []})), self.home)
        self.assertFalse(self.file.exists())

    def test_stop_with_nothing_running_removes_the_file(self):
        run(HOOK, json.dumps(post_tool("PushNotification", {"message": QUESTION})), self.home)
        log = Path(self.home) / "t.jsonl"
        log.write_text(json.dumps(entry("user", "go")) + "\n")
        out = run(HOOK, json.dumps({"session_id": SID, "transcript_path": str(log), "hook_event_name": "Stop"}), self.home)
        self.assertEqual((out.returncode, out.stdout, self.file.exists()), (0, "", False))

    def test_hook_writes_nothing_for_a_session_id_that_is_not_a_uuid(self):
        payload = post_tool("PushNotification", {"message": QUESTION}, session_id="../escape")
        out = run(HOOK, json.dumps(payload), self.home)
        self.assertEqual((out.returncode, out.stdout, (Path(self.home) / ".claude").exists()), (0, "", False))

    def test_non_ascii_question_round_trips_under_a_latin_1_locale(self):
        latin = {"LC_ALL": "en_US.ISO8859-1", "PYTHONIOENCODING": "", "PYTHONUTF8": "0"}
        run(HOOK, json.dumps(post_tool("PushNotification", {"message": "QUESTION: Разбивка готова?"}), ensure_ascii=False),
            self.home, env=latin)
        out = run(STATUSLINE, STATUS_STDIN, self.home, env=latin, text=False)
        self.assertEqual(out.stdout, "OPEN QUESTION Разбивка готова?\n".encode("utf-8"))

    def test_hook_on_garbage_input_is_silent(self):
        out = run(HOOK, "not json", self.home)
        self.assertEqual((out.returncode, out.stdout, out.stderr), (0, "", ""))

    def test_status_line_one_question(self):
        self.file.parent.mkdir(parents=True)
        self.file.write_text("switch the tile? Recommended: yes\n")
        self.assertEqual(run(STATUSLINE, STATUS_STDIN, self.home).stdout, "OPEN QUESTION switch the tile? Recommended: yes\n")

    def test_status_line_cuts_to_terminal_width(self):
        self.file.parent.mkdir(parents=True)
        self.file.write_text("x" * 50 + "\n")
        self.assertEqual(run(STATUSLINE, STATUS_STDIN, self.home, columns="30").stdout, "OPEN QUESTION " + "x" * 15 + "…\n")

    def test_status_line_empty_file_prints_nothing(self):
        self.file.parent.mkdir(parents=True)
        self.file.write_text("")
        out = run(STATUSLINE, STATUS_STDIN, self.home)
        self.assertEqual((out.returncode, out.stdout), (0, ""))

    def test_status_line_no_file_or_bad_stdin_prints_nothing(self):
        for stdin in (STATUS_STDIN, "not json"):
            out = run(STATUSLINE, stdin, self.home)
            self.assertEqual((out.returncode, out.stdout, out.stderr), (0, "", ""))

    def test_hooks_json_routes_the_three_events(self):
        hooks = json.loads((ROOT / "orch" / "hooks" / "hooks.json").read_text())["hooks"]
        routed = {event for event, groups in hooks.items() for g in groups for h in g["hooks"]
                  if "open-question.py" in h["command"]
                  and (event != "PostToolUse" or {"PushNotification", "AskUserQuestion"} <= set(g["matcher"].split("|")))}
        self.assertEqual(routed, {"PostToolUse", "UserPromptSubmit", "Stop"})


if __name__ == "__main__":
    unittest.main()
