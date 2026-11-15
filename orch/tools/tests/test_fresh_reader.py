import json
import os
import shutil
import stat
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.realpath(__file__)), ".."))

import fresh_reader_policy as fr  # noqa: E402
import model_judge  # noqa: E402

CLIENTS = ["clickhouse-client", "psql"]


def owner(text, uuid="o1"):
    return {"type": "user", "uuid": uuid, "message": {"role": "user", "content": text}}


def call(use_id, name, args):
    return {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": use_id, "name": name, "input": args}]}}


def result(use_id, text):
    return {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": use_id, "content": text}]}}


def reply(text, uuid="r1"):
    return {"type": "assistant", "uuid": uuid, "message": {"content": [{"type": "text", "text": text}]}}


def notification(text):
    return {"type": "user", "message": {"content": "<task-notification>\n<task-id>a1</task-id>\n<status>completed"
                                                   "</status>\n<result>%s</result>\n</task-notification>" % text}}


def compaction_summary():
    return {"type": "user", "uuid": "cs1", "isCompactSummary": True, "isVisibleInTranscriptOnly": True,
            "message": {"role": "user", "content": "This session is being continued from a previous conversation "
                                                   "that ran out of context. The summary below covers it."}}


def queued_owner_message(text):
    return {"type": "attachment", "uuid": "o2", "attachment": {"type": "queued_command", "prompt": text,
                                                               "origin": {"kind": "human"}}}


WARM_QUERY = ("python3 tools/db.py --query \"SELECT sum(sent) FROM pool_daily_sends WHERE is_warm = 1 "
              "AND kind = 'delivery'\"")
# Shaped like the witness replies of CASE-84 (a daily warm volume; a split by source), with invented figures.
VOLUME = "Ящики пула сейчас отправляют около 3,0 млн прогревочных писем в сутки."
SPLIT = "Источник A — 2,9 млн в сутки (96 %). Он греет все 41 тыс. ящиков, около 70 писем на ящик."


def fires(entries, text, stop_hook_active=False, last=None, held=None):
    return fr.trigger(entries + [reply(text)], text, stop_hook_active, last, held, CLIENTS)["fire"]


class Trigger(unittest.TestCase):
    def test_witness_volume_after_a_query_fires(self):
        entries = [owner("сколько прогрева?"), call("q1", "Bash", {"command": WARM_QUERY}), result("q1", "3012345")]
        self.assertTrue(fires(entries, VOLUME))

    def test_script_written_by_a_heredoc_is_a_query_only_when_run(self):
        body = "clickhouse-client --query \"SELECT source, count() FROM send_events GROUP BY 1\""
        for write in ("cat > /tmp/src.sh <<'EOF'\n%s\nEOF", "cat <<'EOF' > /tmp/src.sh\n%s\nEOF"):
            entries = [owner("раздели по источникам"), call("w1", "Bash", {"command": write % body}), result("w1", "")]
            ran = entries + [call("q1", "Bash", {"command": "bash /tmp/src.sh"}), result("q1", "a 2900000")]
            with self.subTest(write=write):
                self.assertEqual((fires(entries, SPLIT), fires(ran, SPLIT)), (False, True))

    def test_script_written_by_the_write_tool_and_run_fires(self):
        entries = [owner("split?"), call("w1", "Write", {"file_path": "/tmp/q.py", "content":
                                                         "run('SELECT source, count() FROM send_events GROUP BY 1')"}),
                   result("w1", "ok"), call("q1", "Bash", {"command": "python3 /tmp/q.py"}), result("q1", "a 2900000")]
        self.assertTrue(fires(entries, SPLIT))

    def test_writing_the_script_alone_is_not_a_query(self):
        entries = [owner("prepare"), call("w1", "Write", {"file_path": "/tmp/q.sql",
                                                          "content": "SELECT count() FROM t"}), result("w1", "ok")]
        self.assertFalse(fires(entries, "Подготовил запрос, вернёт около 8 млн строк."))

    def test_metrics_http_query_fires(self):
        entries = [owner("targets?"), call("q1", "Bash", {"command": "curl -s 'http://localhost:9090/api/v1/query"
                                                                    "?query=up'"}), result("q1", "{...}")]
        self.assertTrue(fires(entries, "42 targets are up."))

    def test_configured_client_without_sql_fires(self):
        entries = [owner("tables?"), call("q1", "Bash", {"command": "clickhouse-client --query 'SHOW TABLES'"}),
                   result("q1", "a\nb")]
        self.assertTrue(fires(entries, "The database holds 14 tables."))

    def test_number_without_a_query_does_not_fire(self):
        entries = [owner("how big?"), call("r1", "Read", {"file_path": "/repo/a.py"}), result("r1", "x = 1")]
        self.assertFalse(fires(entries, "The table holds about 8 000 000 rows and 32 columns."))

    def test_query_without_a_number_does_not_fire(self):
        entries = [owner("is it there?"), call("q1", "Bash", {"command": WARM_QUERY}), result("q1", "1")]
        self.assertFalse(fires(entries, "Yes, the mailboxes are in the table."))

    def test_query_covered_by_the_last_judged_stop_does_not_fire_again(self):
        entries = [owner("сколько?"), call("q1", "Bash", {"command": WARM_QUERY}), result("q1", "3012345"),
                   notification("agent finished")]
        self.assertFalse(fires(entries, VOLUME, last="q1"))

    def test_new_query_after_a_passed_stop_fires(self):
        entries = [owner("сколько?"), call("q1", "Bash", {"command": WARM_QUERY}), result("q1", "3012345"),
                   call("q2", "Bash", {"command": WARM_QUERY}), result("q2", "3012346")]
        self.assertTrue(fires(entries, VOLUME, last="q1"))

    def test_nothing_fires_after_a_hold_return_in_the_same_owner_turn(self):
        entries = [owner("сколько?", uuid="o1"), call("q1", "Bash", {"command": WARM_QUERY}), result("q1", "3012345"),
                   reply(VOLUME), notification("agent finished"), call("q2", "Bash", {"command": WARM_QUERY}),
                   result("q2", "3012346")]
        self.assertFalse(fires(entries, VOLUME, last="q1", held="o1"))

    def test_a_hold_of_an_earlier_owner_turn_does_not_block_the_next(self):
        entries = [owner("сколько?", uuid="o1"), call("q1", "Bash", {"command": WARM_QUERY}), result("q1", "3012345"),
                   reply(VOLUME), owner("а за вчера?", uuid="o2"), call("q2", "Bash", {"command": WARM_QUERY}),
                   result("q2", "3012346")]
        self.assertTrue(fires(entries, VOLUME, last="q1", held="o1"))

    def test_query_of_an_earlier_owner_turn_does_not_count(self):
        entries = [owner("сколько?"), call("q1", "Bash", {"command": WARM_QUERY}), result("q1", "3012345"),
                   reply("3 млн"), owner("а в процентах?", uuid="o2")]
        self.assertFalse(fires(entries, "Около 95 % от всех."))

    def test_owner_message_queued_mid_turn_starts_a_new_owner_turn(self):
        entries = [owner("сколько?"), call("q1", "Bash", {"command": WARM_QUERY}), result("q1", "3012345"),
                   queued_owner_message("а в процентах?")]
        self.assertFalse(fires(entries, "Около 95 % от всех."))

    def test_compaction_summary_is_not_an_owner_message(self):
        entries = [owner("сколько?"), call("q1", "Bash", {"command": WARM_QUERY}), result("q1", "3012345"),
                   compaction_summary()]
        self.assertTrue(fires(entries, VOLUME))

    def test_stop_hook_active_stop_is_not_judged(self):
        entries = [owner("сколько?"), call("q1", "Bash", {"command": WARM_QUERY}), result("q1", "3012345")]
        self.assertFalse(fires(entries, VOLUME, stop_hook_active=True))


class Number(unittest.TestCase):
    def test_answer_numbers_count(self):
        for text in ("There are 8012345 emails", "1234567 rows", "The pool has 1,234 mailboxes", "Total 8,012,345 rows",
                     "$1,200 a month", "249/250 mailboxes are warm", "20/20 ящиков", "About 7.96 million emails a day",
                     "p99 is 1.5 seconds", "The share is 0.93", "Bounce rate 0.8 percent",
                     "The API logged 4 312 errors yesterday.", "Yesterday 450 failed and 7 bounced.", "8 млн писем"):
            with self.subTest(text=text):
                self.assertTrue(fr.states_number(text))

    def test_numbers_of_the_agents_own_work_and_references_do_not(self):
        for text in ("Committed 3 commits; 133 tests pass.", "133 passed, 2 failed", "See PR #512 and PR 4521.",
                     "Filed issue #88 and issue 77.", "Bumped to v1.2.3, then v2.10.", "Pushed a1b2c3d4 on top.",
                     "commit 1234567 is on main", "Since 2026-11-16 it ran.", "On 16.11.2026.", "On 16 Nov.",
                     "Since Nov 16 it is green.", "At 12:40, then at 23:59:01.", "See orch/tools/x.py:88 and L171.",
                     "Wrote 12 files and 340 lines.", "Merged deadbeef1234."):
            with self.subTest(text=text):
                self.assertFalse(fr.states_number(text))


class Replies(unittest.TestCase):
    def test_payload_text_not_yet_in_the_transcript_gets_no_uuid(self):
        self.assertEqual(fr.final_reply([reply("Old answer: 12.", uuid="r0")], "New answer: 40."), ("New answer: 40.", ""))

    def test_payload_text_that_is_the_last_reply_carries_its_uuid(self):
        self.assertEqual(fr.final_reply([reply("Answer: 40.", uuid="r1")], "Answer: 40."), ("Answer: 40.", "r1"))

    def test_state_after_a_hold_records_the_owner_turn_and_after_a_pass_keeps_it(self):
        found = {"new": [(3, "q2", ["sql"])], "turn": "o2"}
        self.assertEqual((fr.next_state({"held_turn": "o1"}, found, True), fr.next_state({"held_turn": "o1"}, found, False)),
                         ({"query": "q2", "held_turn": "o2"}, {"query": "q2", "held_turn": "o1"}))


class Packet(unittest.TestCase):
    def packet(self, entries, text):
        found = fr.trigger(entries, text, False, None, None, CLIENTS)
        return fr.build_packet(entries, found["start"], len(entries), text, found["queries"])[0]

    def test_subagent_result_in_a_notification_is_shown(self):
        entries = [owner("daily?"), call("a1", "Agent", {"prompt": "check prod"}),
                   result("a1", "Async agent launched successfully. agentId: a1"),
                   notification("x" * 3500 + " All 20 belong to team alpha, status warming"),
                   call("q1", "Bash", {"command": "psql -c 'SELECT count(*) FROM boxes'"}), result("q1", "20")]
        self.assertIn("All 20 belong to team alpha", self.packet(entries, "Team alpha has 20 boxes."))

    def test_subagent_result_returned_by_the_agent_call_is_shown_past_the_call_cut(self):
        entries = [owner("daily?"), call("a1", "Agent", {"prompt": "check prod"}),
                   result("a1", "y" * 4000 + " All 20 belong to team alpha"),
                   call("q1", "Bash", {"command": "psql -c 'SELECT count(*) FROM boxes'"}), result("q1", "20")]
        self.assertIn("All 20 belong to team alpha", self.packet(entries, "Team alpha has 20 boxes."))

    def test_definition_read_earlier_in_the_session_is_kept(self):
        entries = [owner("what is is_warm?"), call("g1", "Bash", {"command": "grep -rn is_warm src"}),
                   result("g1", "src/ingest.rs:88: is_warm = sender in warm_pool"), reply("It marks pool senders."),
                   owner("how many?", uuid="o2"), call("q1", "Bash", {"command": WARM_QUERY}), result("q1", "3012345")]
        self.assertIn("is_warm = sender in warm_pool", self.packet(entries, VOLUME))

    def test_id_on_its_own_line_of_a_query_brings_back_an_excerpt_of_a_cut_result(self):
        body = "Description. " * 500 + "4407711002233445566 is the collector query. " + "tail " * 900
        entries = [owner("is the heavy query gone?"), call("p1", "Bash", {"command": "gh pr view 77"}),
                   result("p1", body),
                   call("q1", "Bash", {"command": "clickhouse-client --query \"SELECT toDate(event_time), count()\n"
                                                  "FROM system.query_log\nWHERE query_kind IN (\n4407711002233445566)\n"
                                                  "GROUP BY 1\""}),
                   result("q1", "2026-11-05 18816\n2026-11-06 17000")]
        self.assertIn("4407711002233445566 is the collector query", self.packet(entries, "Since Nov 7 it ran 0 times."))

    def test_name_cut_from_a_result_earlier_in_the_session_comes_back_as_an_excerpt(self):
        entries = [owner("what does the job write?"), call("g1", "Bash", {"command": "cat jobs/rollup.sql"}),
                   result("g1", "-- header\n" * 120 + "warm_total counts only senders in the pool"),
                   reply("It writes a rollup."), owner("how many?", uuid="o2"),
                   call("q1", "Bash", {"command": "psql -c 'SELECT warm_total FROM rollup'"}), result("q1", "3012345")]
        self.assertIn("warm_total counts only senders in the pool", self.packet(entries, VOLUME))


class Verdict(unittest.TestCase):
    @staticmethod
    def envelope(text, is_error=False):
        return json.dumps({"type": "result", "is_error": is_error, "result": text})

    def test_hold_carries_the_read_and_the_number_lines(self):
        verdict = fr.parse_verdict(self.envelope(
            "NUMBER: 3.0M | COUNTED: rows | MEANING READ: no | CONFLICT: none\nVERDICT: HOLD - read what sets is_warm"))
        self.assertEqual(verdict, {"verdict": "hold", "hold": "read what sets is_warm",
                                   "numbers": ["NUMBER: 3.0M | COUNTED: rows | MEANING READ: no | CONFLICT: none"]})

    def test_pass(self):
        self.assertEqual(fr.parse_verdict(self.envelope("NUMBER: 3 | MEANING READ: yes\n**VERDICT: PASS**"))["verdict"],
                         "pass")

    def test_text_after_the_verdict_line_is_ignored(self):
        self.assertEqual(fr.parse_verdict(self.envelope("VERDICT: HOLD - read x\nI also think that ..."))["hold"],
                         "read x")

    def test_the_last_verdict_line_decides(self):
        self.assertEqual(fr.parse_verdict(self.envelope("VERDICT: PASS\nOn reflection:\nVERDICT: HOLD - read y"))["hold"],
                         "read y")

    def test_answer_without_a_verdict_line_is_malformed(self):
        self.assertIsNone(fr.parse_verdict(self.envelope("NUMBER: 3 | MEANING READ: yes\nLooks fine.")))

    def test_hold_without_a_read_is_malformed(self):
        self.assertIsNone(fr.parse_verdict(self.envelope("VERDICT: HOLD")))

    def test_failed_run_is_malformed(self):
        self.assertIsNone(fr.parse_verdict(self.envelope("VERDICT: PASS", is_error=True)))

    def test_unparsable_reason_quotes_the_head_of_the_answer(self):
        answer = "I cannot decide this one. " * 20
        stdout = json.dumps({"type": "result", "is_error": False, "result": answer, "usage": {"input_tokens": 1}})
        self.assertEqual(model_judge.answer_head(stdout), answer[:200])


class Decision(unittest.TestCase):
    def test_hold_returns_with_the_number_lines_the_read_and_the_not_known_yet_fallback(self):
        outcome = fr.decision({"verdict": "hold", "hold": "read what sets warm_src",
                               "numbers": ["NUMBER: 2.9M | MEANING READ: no"]})
        self.assertTrue(outcome["returned"])
        self.assertIn("NUMBER: 2.9M | MEANING READ: no", outcome["reason"])
        self.assertIn("read what sets warm_src", outcome["reason"])
        self.assertIn("not known yet", outcome["reason"])

    def test_missing_verdict_fails_open(self):
        self.assertEqual(fr.decision(None), {"returned": False, "reason": ""})


class Preset(unittest.TestCase):
    def test_line_turns_it_on(self):
        self.assertTrue(fr.enabled("# Rules\n- Fresh reader: on\n"))

    def test_absent_line_keeps_it_off(self):
        self.assertFalse(fr.enabled("# Rules\nThe fresh reader rule is described elsewhere.\n"))


class JudgeTransport(unittest.TestCase):
    """model_judge.run against a stub `claude` that records how the prompt arrived."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir)
        self.record = os.path.join(self.dir, "record.json")
        stub = os.path.join(self.dir, "claude")
        with open(stub, "w") as fh:
            fh.write("#!/usr/bin/env python3\nimport json, sys\nstdin = sys.stdin.read()\n"
                     "json.dump({'argv': sys.argv[1:], 'stdin': stdin}, open(%r, 'w'))\n"
                     "print(json.dumps({'type': 'result', 'is_error': False, 'result': 'VERDICT: PASS'}))\n"
                     % self.record)
        os.chmod(stub, os.stat(stub).st_mode | stat.S_IEXEC)
        self.path = os.environ["PATH"]
        os.environ["PATH"] = self.dir + os.pathsep + self.path
        self.addCleanup(os.environ.__setitem__, "PATH", self.path)

    def test_prompt_goes_on_stdin_for_every_judge_command_line(self):
        for args in (model_judge.judge_args("haiku", "You classify one reply. Respond with JSON only."),
                     model_judge.judge_args("opus", "You check numbers in one reply.")):
            with self.subTest(model=args[3]):
                prompt = "P" * 200000
                verdict, error = model_judge.run(args, prompt, 30, "TEST_JUDGE_ENV", fr.parse_verdict)
                with open(self.record) as fh:
                    seen = json.load(fh)
                self.assertEqual((verdict["verdict"], error, seen["stdin"], prompt in seen["argv"]),
                                 ("pass", None, prompt, False))


if __name__ == "__main__":
    unittest.main()
