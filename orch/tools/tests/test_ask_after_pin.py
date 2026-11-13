"""The ask-after-pin rule on transcript shapes captured from a real session (push and dialog in one assistant
message, push result in between; texts replaced) and the `metadata.source` clause grammar shared with the
`Existing:` rule."""
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
HOOK = ROOT / "orch" / "hooks" / "ask-existing-gate.py"
SID = "6f1c2a3b-4d5e-4f60-8a7b-9c0d1e2f3a4b"
PUSH = "QUESTION: merge #12 (export button on the report page)? Recommended: merge"
STEM = "Part two of the report export (#12): merge?"


def assistant(msg, block):
    return {"type": "assistant", "isSidechain": False, "message": {"id": msg, "role": "assistant", "content": [block]}}


def call(msg, use_id, name, args):
    return assistant(msg, {"type": "tool_use", "id": use_id, "name": name, "input": args})


def result(use_id, text, error=False):
    block = {"type": "tool_result", "tool_use_id": use_id, "content": text}
    if error:
        block["is_error"] = True
    return {"type": "user", "isSidechain": False, "message": {"role": "user", "content": [block]}}


def push(msg="m1", use_id="p1", message=PUSH):
    return [call(msg, use_id, "PushNotification", {"message": message}),
            result(use_id, "Terminal notification sent. Mobile push not sent (Remote Control inactive).")]


def dialog(msg="m1", use_id="d1", source=None):
    args = {"questions": [{"question": STEM, "options": [{"label": "Merge"}, {"label": "Hold"}]}]}
    if source is not None:
        args["metadata"] = {"source": source}
    return call(msg, use_id, "AskUserQuestion", args)


def payload(source=None, **extra):
    return dict({"session_id": SID, "transcript_path": "/x/" + SID + ".jsonl", "hook_event_name": "PreToolUse",
                 "tool_name": "AskUserQuestion", "tool_input": dialog(source=source)["message"]["content"][0]["input"],
                 "tool_use_id": "d2"}, **extra)


def decide(entries, pinned=True, **kwargs):
    return gate_policy.ask_after_pin_decision(payload(**kwargs), pinned, reversed(entries))


REFUSED = "PreToolUse:AskUserQuestion hook error: [python3 ask-existing-gate.py]: Refused by ask-existing-gate"
BASH = [call("m2", "b1", "Bash", {"command": "git worktree add .worktrees/next"}), result("b1", "ok")]
TURN_END = {"type": "system", "subtype": "turn_duration", "durationMs": 1000}
TYPED = {"type": "user", "message": {"role": "user", "content": "go on"}}


class AskAfterPinDecisionTest(unittest.TestCase):
    def test_dialog_in_the_push_message_is_refused_before_the_pin_file_exists(self):
        self.assertTrue(decide([*push(), dialog()], pinned=False).startswith("Refused by ask-existing-gate (ask-after-pin"))

    def test_a_tool_call_after_the_push_lets_the_dialog_through(self):
        self.assertIsNone(decide([*push(), *BASH, dialog("m3")]))

    def test_no_push_lets_the_dialog_through(self):
        for pinned in (False, True):
            with self.subTest(pinned=pinned):
                self.assertIsNone(decide([*BASH, dialog("m3")], pinned=pinned))

    def test_a_notification_without_the_question_prefix_is_no_pin(self):
        self.assertIsNone(decide([*push(message="Rollout done"), dialog()]))

    def test_bare_retry_after_the_refusal_is_refused_again(self):
        entries = [*push(), dialog(), result("d1", REFUSED, error=True), dialog("m2", "d2")]
        self.assertIsNotNone(decide(entries))

    def test_named_reason_passes_on_the_first_call_and_on_a_retry(self):
        for entries in ([*push(), dialog()], [*push(), dialog(), result("d1", REFUSED, error=True), dialog("m2", "d2")]):
            with self.subTest(n=len(entries)):
                self.assertIsNone(decide(entries, source="blocked: the next slice edits the files this answer picks"))

    def test_answered_dialog_after_the_push_ends_the_pin(self):
        entries = [*push(), dialog(), result("d1", 'Your questions have been answered: "merge?"="Merge"'), dialog("m2", "d2")]
        self.assertIsNone(decide(entries))

    def test_dialog_the_owner_rejected_counts_as_answered(self):
        rejected = ("The user doesn't want to proceed with this tool use. The tool use was rejected (eg. if it was a"
                    " file edit, the new_string was NOT written to the file). To tell you how to proceed, the user"
                    " said:\nfirst finish the export tests")
        for text in (rejected, rejected.split(" To tell")[0]):
            with self.subTest(comment="said" in text):
                self.assertIsNone(decide([*push(), dialog(), result("d1", text, error=True), dialog("m2", "d2")]))

    def test_malformed_input_passes(self):
        junk = [1, "x", {"type": "assistant", "message": "s"}, call("m1", "p1", "PushNotification", "s"), dialog()]
        cases = {"string tool_input": (dict(payload(), tool_input="x"), [*push(), dialog()]),
                 "list payload": ([], [*push(), dialog()]),
                 "junk transcript entries": (payload(), junk)}
        for name, (body, entries) in cases.items():
            with self.subTest(name):
                self.assertIsNone(gate_policy.ask_after_pin_decision(body, True, reversed(entries)))

    def test_non_string_session_id_is_no_session(self):
        self.assertEqual([gate_policy.session_id_ok(v) for v in (5, None, ["x"], SID)], [False, False, False, True])

    def test_subagent_payload_passes(self):
        self.assertIsNone(decide([*push(), dialog()], agent_id="ab12cd34", agent_type="orch:builder"))

    def test_push_of_an_earlier_turn_counts_only_while_the_file_holds_the_pin(self):
        # Stop with a live agent keeps the pin; the session wakes on its notification and asks at once.
        notification = {"type": "user", "message": {"role": "user", "content": "<task-notification>done</task-notification>"}}
        entries = [*push(), TURN_END, notification, dialog("m2", "d2")]
        self.assertEqual((decide(entries, pinned=True) is None, decide(entries, pinned=False) is None), (False, True))

    def test_typed_prompt_after_the_push_without_a_pin_file_passes(self):
        self.assertIsNone(decide([*push(), TYPED, dialog("m2", "d2")], pinned=False))

    def test_subagent_calls_in_the_main_transcript_are_not_work(self):
        side = dict(call("s1", "s1", "Bash", {"command": "ls"}), isSidechain=True)
        self.assertIsNotNone(decide([*push(), side, dialog("m2", "d2")]))


class SourceClausesTest(unittest.TestCase):
    def ask_both(self, source, stem=STEM):
        tool_input = {"questions": [{"question": stem, "options": []}], "metadata": {"source": source}}
        pin = gate_policy.ask_after_pin_decision(dict(payload(), tool_input=tool_input), True, reversed([*push(), dialog()]))
        return pin, gate_policy.ask_existing_decision(tool_input)

    def test_both_clauses_pass_both_rules_in_either_order(self):
        for source in ("existing: n/a; blocked: waits on the owner's choice of the domain",
                       "blocked: waits on the owner's choice of the domain;existing: N/A"):
            with self.subTest(source=source):
                self.assertEqual(self.ask_both(source), (None, None))

    def test_each_clause_alone_passes_only_its_own_rule(self):
        pin, existing = self.ask_both("existing: n/a")
        self.assertEqual((pin is not None, existing), (True, None))
        pin, existing = self.ask_both("blocked: waits on the owner's choice")
        self.assertEqual((pin, existing is not None), (None, True))

    def test_empty_or_embedded_blocked_does_not_pass(self):
        for source in ("existing: n/a; blocked:", "existing: n/a (not blocked: anything)", "blocked"):
            with self.subTest(source=source):
                self.assertIsNotNone(self.ask_both(source)[0])


class BackwardReaderTest(unittest.TestCase):
    def test_reads_every_entry_newest_first_across_block_edges(self):
        entries = [*push(), *BASH, dialog("m3")]
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as fh:
            fh.write("\n".join(json.dumps(e) for e in entries) + "\nnot json\n")
        self.addCleanup(os.remove, fh.name)
        for size in (7, 1 << 16):
            with self.subTest(block_size=size):
                self.assertEqual(list(transcript.entries_from_end(fh.name, 1 << 20, size)), entries[::-1])

    def test_stops_after_the_byte_bound(self):
        entries = [dialog("m%d" % n, "d%d" % n) for n in range(10)]
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as fh:
            fh.write("".join(json.dumps(e) + "\n" for e in entries))
        self.addCleanup(os.remove, fh.name)
        read = list(transcript.entries_from_end(fh.name, 3 * len(json.dumps(entries[0])), 16))
        self.assertEqual((read, len(read) < len(entries)), (entries[::-1][:len(read)], True))


class HookTest(unittest.TestCase):
    def test_hook_refuses_from_the_transcript_and_passes_with_a_reason(self):
        with tempfile.TemporaryDirectory() as home:
            path = os.path.join(home, SID + ".jsonl")
            with open(path, "w") as fh:
                fh.write("\n".join(json.dumps(e) for e in [*push(), dialog()]) + "\n")
            env = dict(os.environ, HOME=home, ASK_SPEND_JUDGE="1")
            outs = [subprocess.run([sys.executable, str(HOOK)], input=json.dumps(dict(payload(source=s), transcript_path=path)),
                                   capture_output=True, text=True, timeout=20, env=env)
                    for s in ("existing: n/a", "existing: n/a; blocked: the answer picks the files the next slice edits")]
        self.assertEqual([(o.returncode, o.stderr) for o in outs],
                         [(2, gate_policy._ASK_AFTER_PIN_REASON + "\n"), (0, "")])


if __name__ == "__main__":
    unittest.main()
