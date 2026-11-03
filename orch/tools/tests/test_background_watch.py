import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.realpath(__file__)), ".."))

import gate_policy  # noqa: E402
import transcript  # noqa: E402

MARKER = gate_policy.BACKGROUND_WATCH_MARKER


def owner(text):
    return {"type": "user", "message": {"role": "user", "content": text}}


def turn_end():
    return {"type": "system", "subtype": "turn_duration"}


def tool_use(use_id, name, args):
    return {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": use_id, "name": name, "input": args}]}}


def tool_result(use_id, text):
    return {"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": use_id, "content": text}]}}


def launch(task, command="./run_full.sh", use_id=None):
    use_id = use_id or "u-" + task
    return [tool_use(use_id, "Bash", {"command": command, "run_in_background": True}),
            tool_result(use_id, "Command running in background with ID: " + task)]


def monitor(mon):
    return [tool_use("m-" + mon, "Monitor", {"command": "tail -F log"}),
            tool_result("m-" + mon, "Monitor started (task {}, expires in 30m)".format(mon))]


def notification(task, body):
    return owner("<task-notification><task-id>{}</task-id>{}</task-notification>".format(task, body))


def stop_feedback():
    return owner("Stop hook feedback:\n" + MARKER + " Task(s): b1.")


def decide(entries):
    raw = [(json.dumps(e), e) for e in entries]
    facts = list(transcript.background_watch_scan(raw, MARKER))[-1]
    return gate_policy.background_watch_decision(facts)["decision"]


class BackgroundWatchTest(unittest.TestCase):
    def test_launch_this_turn_unwatched_blocks(self):
        self.assertEqual(decide([owner("go"), *launch("b1")]), "block")

    def test_live_monitor_after_launch_allows(self):
        self.assertEqual(decide([owner("go"), *launch("b1"), *monitor("m1")]), "allow")

    def test_monitor_event_without_status_keeps_watch(self):
        entries = [owner("go"), *launch("b1"), *monitor("m1"),
                   notification("m1", "<summary>Monitor event: \"x\"</summary><event>line</event>")]
        self.assertEqual(decide(entries), "allow")

    def test_cron_after_launch_allows(self):
        entries = [owner("go"), *launch("b1"), tool_use("c", "CronCreate", {"cron": "0 9 * * *"})]
        self.assertEqual(decide(entries), "allow")

    def test_monitor_expired_this_turn_without_rearm_blocks(self):
        entries = [owner("go"), *launch("b1"), *monitor("m1"), turn_end(),
                   notification("m1", "<event>[Monitor expired after 30m with no events]</event>")]
        self.assertEqual(decide(entries), "block")

    def test_monitor_stream_ended_this_turn_without_rearm_blocks(self):
        entries = [owner("go"), *launch("b1"), *monitor("m1"), turn_end(),
                   notification("m1", "<status>completed</status><summary>stream ended</summary>")]
        self.assertEqual(decide(entries), "block")

    def test_rearmed_monitor_after_expiry_allows(self):
        entries = [owner("go"), *launch("b1"), *monitor("m1"), turn_end(),
                   notification("m1", "<event>[Monitor expired after 30m]</event>"), *monitor("m2")]
        self.assertEqual(decide(entries), "allow")

    def test_launch_in_earlier_turn_unwatched_allows(self):
        self.assertEqual(decide([owner("go"), *launch("b1"), turn_end(), owner("next")]), "allow")

    def test_task_stop_allows(self):
        entries = [owner("go"), *launch("b1"), tool_use("s", "TaskStop", {"task_id": "b1"})]
        self.assertEqual(decide(entries), "allow")

    def test_notified_task_allows(self):
        entries = [owner("go"), *launch("b1"), notification("b1", "<status>completed</status>")]
        self.assertEqual(decide(entries), "allow")

    def test_pure_wait_launch_allows(self):
        cmd = "~/.claude/tools/wait-for --every 30 --max 570 -- test -f done"
        self.assertEqual(decide([owner("go"), *launch("b1", cmd)]), "allow")

    def test_marker_after_launch_allows_second_stop(self):
        self.assertEqual(decide([owner("go"), *launch("b1"), stop_feedback()]), "allow")

    def test_marker_quoted_in_a_tool_result_still_blocks(self):
        entries = [owner("go"), *launch("b1"), tool_use("r", "Read", {"file_path": "gate_policy.py"}),
                   tool_result("r", 'BACKGROUND_WATCH_MARKER = "' + MARKER + '"')]
        self.assertEqual(decide(entries), "block")

    def test_marker_before_a_new_launch_does_not_allow(self):
        entries = [owner("go"), *launch("b1"), stop_feedback(), *launch("b2")]
        self.assertEqual(decide(entries), "block")


class LaunchAckTest(unittest.TestCase):
    def ack(self, text):
        return transcript.launch_id({"type": "tool_result", "content": text})

    def test_timeout_moved_form_is_a_launch(self):
        self.assertEqual(self.ack(
            "Command did not complete within its 120s timeout and was moved to the "
            "background (ID: bx12). Output is being written to: /tmp/x"), "bx12")

    def test_moved_for_arriving_message_form_is_a_launch(self):
        self.assertEqual(self.ack(
            "Command was moved to the background (ID: bfepb7b13) so that a message that "
            "arrived while it was running can reach you; it was not interrupted. Output is "
            "being written to: /private/tmp/claude-501/x/tasks/bfepb7b13.output"), "bfepb7b13")

    def test_poll_gate_denial_is_not_a_launch(self):
        self.assertIsNone(self.ack(
            "PreToolUse:Bash hook error: Polling loops are refused: A Bash call without a "
            "timeout is moved to the background by the harness; Every 30 s ... ID: E"))


if __name__ == "__main__":
    unittest.main()
