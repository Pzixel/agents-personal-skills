"""The live-work rule on AskUserQuestion: the dialog is modal, so it is refused while an agent, background task or
Monitor of the session runs; its chain with the ask-after-pin rule; the hook's order and fail-open."""
import json
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.realpath(__file__)), ".."))
sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))

import gate_policy  # noqa: E402
import transcript  # noqa: E402
from test_ask_after_pin import HOOK, PIN, PUSH, REFUSED, SID, at, call, dialog, payload, result  # noqa: E402

LIVE_WORK = "Refused by ask-existing-gate (live-work rule)"
AGENT = [at(1, call("m0", "g1", "Agent", {"prompt": "build", "run_in_background": True})),
         at(2, result("g1", "Async agent launched successfully.\nagentId: ag1 (internal ID)"))]
TASK = [at(1, call("m0", "b1", "Bash", {"command": "make test", "run_in_background": True})),
        at(2, result("b1", "Command running in background with ID: bx1. Output is being written to: /tmp/tasks/bx1.output"))]
MONITOR = [at(1, call("m0", "w1", "Monitor", {"command": "gh run watch"})),
           at(2, result("w1", "Monitor started (task mo1, timeout 1800s)"))]
CRON = [at(1, call("m0", "c1", "CronCreate", {"cron": "30 9 18 11 *", "recurring": False, "prompt": "recount"})),
        at(2, result("c1", "Scheduled one-shot task 7d7f12e2 (30 9 18 11 *)."))]


def notified(t, task):
    return at(t, {"type": "user", "isSidechain": False, "message": {"role": "user", "content":
              "<task-notification>\n<task-id>%s</task-id>\n<status>completed</status>\n</task-notification>" % task}})


def turn_end(t):
    return at(t, {"type": "system", "subtype": "turn_duration"})


def live(entries, **extra):
    facts = None
    for facts in transcript.background_watch_scan([("", e) for e in entries], "\x00"):
        pass
    return gate_policy.ask_live_work_decision(payload(**extra), facts)


class AskLiveWorkDecisionTest(unittest.TestCase):
    def test_running_agent_task_or_monitor_refuses(self):
        for name, entries in (("agent", AGENT), ("task", TASK), ("monitor", MONITOR)):
            with self.subTest(name):
                self.assertTrue((live(entries) or "").startswith(LIVE_WORK))

    def test_nothing_that_returns_a_result_running_passes(self):
        for name, entries in (("nothing", []), ("one-shot cron", CRON),
                              ("agent call without result", [at(1, call("m0", "g2", "Agent", {"prompt": "x"}))])):
            with self.subTest(name):
                self.assertIsNone(live(entries))

    def test_retry_after_the_notification_passes_and_the_refused_dialog_is_not_work(self):
        entries = [*AGENT, at(3, dialog("m1", "d1")), at(4, result("d1", REFUSED, error=True)), notified(60, "ag1")]
        self.assertIsNone(live(entries))

    def test_subagent_payload_and_unread_transcript_pass(self):
        self.assertIsNone(live(AGENT, agent_id="ab12cd34", agent_type="orch:builder"))
        self.assertIsNone(gate_policy.ask_live_work_decision(payload(), None))


class PinAfterLiveWorkChainTest(unittest.TestCase):
    """Refusal, `QUESTION:` push, turn end, the agent's notification, the dialog with nothing live."""

    PUSHED = [*AGENT, at(3, dialog("m1", "d1")), at(4, result("d1", REFUSED, error=True)),
              at(9, call("m2", "p1", "PushNotification", {"message": PUSH})), at(10, result("p1", "sent"))]

    def pin_rule(self, entries):
        return gate_policy.ask_after_pin_decision(payload(), PIN, reversed(entries))

    def test_dialog_after_the_turn_end_and_the_notification_passes_both_rules(self):
        entries = [*self.PUSHED, turn_end(11), notified(70, "ag1")]
        self.assertEqual((live(entries), self.pin_rule(entries)), (None, None))

    def test_notification_delivered_from_the_queue_re_invokes(self):
        queued = at(70, {"type": "attachment", "attachment": {"type": "queued_command", "prompt":
                         "<task-notification>\n<task-id>ag1</task-id>\n<status>completed</status>\n</task-notification>"}})
        self.assertIsNone(self.pin_rule([*self.PUSHED, queued]))

    def test_enqueued_notice_or_a_quoted_one_is_no_re_invocation(self):
        enqueued = at(70, {"type": "queue-operation", "operation": "enqueue", "content": "<task-notification>x</task-notification>"})
        quoted = at(70, result("r9", "grep hit: <task-notification><task-id>ag1</task-id>"))
        for entry in (enqueued, quoted):
            with self.subTest(entry["type"]):
                self.assertEqual(self.pin_rule([*self.PUSHED, entry]), gate_policy._ASK_AFTER_PIN_REASON)


class HookOrderTest(unittest.TestCase):
    def run_hook(self, entries, pinned):
        with tempfile.TemporaryDirectory() as home:
            path = os.path.join(home, SID + ".jsonl")
            with open(path, "w") as fh:
                fh.write("".join(json.dumps(e) + "\n" for e in entries))
            if pinned:
                pin = gate_policy.open_question_path(home, SID)
                os.makedirs(os.path.dirname(pin), exist_ok=True)
                with open(pin, "w") as fh:
                    fh.write(PUSH + "\n")
                os.utime(pin, (PIN, PIN))
            out = subprocess.run([sys.executable, str(HOOK)], capture_output=True, text=True, timeout=20,
                                 input=json.dumps(dict(payload(source="existing: n/a"), transcript_path=path)),
                                 env=dict(os.environ, HOME=home, ASK_SPEND_JUDGE="1"))
        return out.returncode, out.stderr

    def test_live_work_refuses_before_the_pin_rule(self):
        reason = gate_policy._ASK_LIVE_WORK_REASON.format(count=1, ids="ag1")
        self.assertEqual(self.run_hook(AGENT, pinned=True), (2, reason + "\n"))

    def test_unreadable_transcript_fails_open(self):
        with tempfile.TemporaryDirectory() as home:
            path = os.path.join(home, SID + ".jsonl")
            os.mkdir(path)  # a directory where the transcript should be: unreadable
            out = subprocess.run([sys.executable, str(HOOK)], capture_output=True, text=True, timeout=20,
                                 input=json.dumps(dict(payload(source="existing: n/a"), transcript_path=path)),
                                 env=dict(os.environ, HOME=home, ASK_SPEND_JUDGE="1"))
        self.assertEqual((out.returncode, out.stderr), (0, ""))


if __name__ == "__main__":
    unittest.main()
