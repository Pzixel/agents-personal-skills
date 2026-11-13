"""The ask-after-pin rule on transcript shapes captured from a real session (push and dialog in one assistant
message, push result in between; texts replaced) and the `metadata.source` clause grammar shared with the
`Existing:` rule."""
import datetime
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


T0 = 1_800_000_000.0  # epoch of the fixtures' clock; times below are seconds after it
PIN = T0 + 10  # mtime of the pin file: the push's PostToolUse wrote it


def at(t, entry):
    stamp = datetime.datetime.fromtimestamp(T0 + t, datetime.timezone.utc).isoformat(timespec="milliseconds")
    return dict(entry, timestamp=stamp.replace("+00:00", "Z"))


def assistant(msg, block):
    return {"type": "assistant", "isSidechain": False, "message": {"id": msg, "role": "assistant", "content": [block]}}


def call(msg, use_id, name, args):
    return assistant(msg, {"type": "tool_use", "id": use_id, "name": name, "input": args})


def result(use_id, text, error=False):
    block = {"type": "tool_result", "tool_use_id": use_id, "content": text}
    if error:
        block["is_error"] = True
    return {"type": "user", "isSidechain": False, "message": {"role": "user", "content": [block]}}


def dialog(msg="m1", use_id="d1", source=None):
    args = {"questions": [{"question": STEM, "options": [{"label": "Merge"}, {"label": "Hold"}]}]}
    if source is not None:
        args["metadata"] = {"source": source}
    return call(msg, use_id, "AskUserQuestion", args)


def payload(source=None, **extra):
    return dict({"session_id": SID, "transcript_path": "/x/" + SID + ".jsonl", "hook_event_name": "PreToolUse",
                 "tool_name": "AskUserQuestion", "tool_input": dialog(source=source)["message"]["content"][0]["input"],
                 "tool_use_id": "d2"}, **extra)


def decide(entries, pin=PIN, **kwargs):
    return gate_policy.ask_after_pin_decision(payload(**kwargs), pin, reversed(entries))


REFUSED = "PreToolUse:AskUserQuestion hook error: [python3 ask-existing-gate.py]: Refused by ask-existing-gate"
REJECTED = ("The user doesn't want to proceed with this tool use. The tool use was rejected (eg. if it was a file"
            " edit, the new_string was NOT written to the file). To tell you how to proceed, the user said:\n"
            "first finish the export tests")
# What is on disk when the dialog of the push's own assistant message hits PreToolUse: the earlier work only;
# the push and the dialog blocks of the running message are not written yet.
ON_DISK = [at(1, call("m0", "b0", "Bash", {"command": "git status"})), at(2, result("b0", "clean"))]
# The same message after the flush: push, its result, the refused dialog.
FLUSHED = [*ON_DISK, at(9, call("m1", "p1", "PushNotification", {"message": PUSH})),
           at(11, result("p1", "Terminal notification sent.")), at(11.5, dialog())]


class AskAfterPinDecisionTest(unittest.TestCase):
    def test_dialog_in_the_push_message_is_refused_from_the_pin_alone(self):
        self.assertTrue(decide(ON_DISK).startswith("Refused by ask-existing-gate (ask-after-pin"))

    def test_work_written_after_the_pin_lets_the_dialog_through(self):
        work = [at(20, call("m2", "b1", "Bash", {"command": "git worktree add .worktrees/next"})), at(21, result("b1", "ok"))]
        self.assertIsNone(decide([*FLUSHED, at(12, result("d1", REFUSED, error=True)), *work]))

    def test_no_pin_passes(self):
        self.assertIsNone(decide(ON_DISK, pin=None))

    def test_bare_retry_after_the_refusal_is_refused_again(self):
        self.assertIsNotNone(decide([*FLUSHED, at(12, result("d1", REFUSED, error=True))]))

    def test_work_in_the_refused_dialogs_own_message_passes_the_retry_after_the_flush(self):
        # Accepted cost: that work was not on disk when the first dialog was judged, so it was refused once.
        entries = [*ON_DISK, at(9, call("m1", "p1", "PushNotification", {"message": PUSH})), at(11, result("p1", "sent")),
                   at(20, call("m2", "b1", "Bash", {"command": "ls"})), at(20, dialog("m2", "d1")),
                   at(21, result("b1", "ok")), at(21, result("d1", REFUSED, error=True))]
        self.assertIsNone(decide(entries))

    def test_named_reason_passes_on_the_first_call_and_on_a_retry(self):
        for entries in (ON_DISK, [*FLUSHED, at(12, result("d1", REFUSED, error=True))]):
            with self.subTest(n=len(entries)):
                self.assertIsNone(decide(entries, source="blocked: the next slice edits the files this answer picks"))

    def test_dialog_the_owner_rejected_after_the_pin_passes(self):
        # An answered dialog clears the pin file (no pin passes); a dismissed or rejected one leaves it.
        for text in (REJECTED, REJECTED.split(" To tell")[0]):
            with self.subTest(comment="said" in text):
                self.assertIsNone(decide([*FLUSHED, at(30, result("d1", text, error=True))]))

    def test_malformed_input_passes(self):
        junk = [1, "x", {"type": "assistant", "message": "s"}, {"type": "assistant", "timestamp": "yesterday"},
                at(20, {"type": "user", "message": "s"})]
        cases = {"string tool_input": (dict(payload(), tool_input="x"), PIN, ON_DISK),
                 "list payload": ([], PIN, ON_DISK),
                 "pin time not a number": (payload(), "10", ON_DISK),
                 "junk transcript entries": (payload(), PIN, junk)}
        for name, (body, pin, entries) in cases.items():
            with self.subTest(name):
                self.assertIsNone(gate_policy.ask_after_pin_decision(body, pin, reversed(entries)))

    def test_a_read_that_ends_before_the_pin_passes(self):
        self.assertIsNone(decide([at(11, result("p1", "sent"))]))

    def test_non_string_session_id_is_no_session(self):
        self.assertEqual([gate_policy.session_id_ok(v) for v in (5, None, ["x"], SID)], [False, False, False, True])

    def test_subagent_payload_passes(self):
        self.assertIsNone(decide(ON_DISK, agent_id="ab12cd34", agent_type="orch:builder"))

    def test_subagent_calls_in_the_main_transcript_are_not_work(self):
        side = at(20, dict(call("s1", "s1", "Bash", {"command": "ls"}), isSidechain=True))
        self.assertIsNotNone(decide([*ON_DISK, side]))


class SourceClausesTest(unittest.TestCase):
    def ask_both(self, source, stem=STEM):
        tool_input = {"questions": [{"question": stem, "options": []}], "metadata": {"source": source}}
        pin = gate_policy.ask_after_pin_decision(dict(payload(), tool_input=tool_input), PIN, reversed(ON_DISK))
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
        entries = FLUSHED
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
    def test_hook_refuses_from_the_pin_file_and_passes_with_a_reason(self):
        with tempfile.TemporaryDirectory() as home:
            path = os.path.join(home, SID + ".jsonl")
            with open(path, "w") as fh:
                fh.write("\n".join(json.dumps(e) for e in ON_DISK) + "\n")
            pin = gate_policy.open_question_path(home, SID)
            os.makedirs(os.path.dirname(pin), exist_ok=True)
            with open(pin, "w") as fh:
                fh.write(PUSH + "\n")
            os.utime(pin, (PIN, PIN))
            env = dict(os.environ, HOME=home, ASK_SPEND_JUDGE="1")
            outs = [subprocess.run([sys.executable, str(HOOK)], input=json.dumps(dict(payload(source=s), transcript_path=path)),
                                   capture_output=True, text=True, timeout=20, env=env)
                    for s in ("existing: n/a", "existing: n/a; blocked: the answer picks the files the next slice edits")]
        self.assertEqual([(o.returncode, o.stderr) for o in outs],
                         [(2, gate_policy._ASK_AFTER_PIN_REASON + "\n"), (0, "")])


if __name__ == "__main__":
    unittest.main()
