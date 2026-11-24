import hashlib
import json
import re
import os
import shutil
import sys
import subprocess
import tempfile
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.realpath(__file__)), ".."))

import gate_policy  # noqa: E402

PARAMS_FILE = os.path.join(
    os.path.dirname(os.path.realpath(__file__)), "..", "orchestrate-params.json"
)


class ClassifyCommandTest(unittest.TestCase):
    def test_search_for_the_word_sleep_is_not_polling(self):
        self.assertEqual(gate_policy.classify_command('grep -r "sleep" .'), "hands_on")

    def test_search_for_a_loop_pattern_is_not_polling(self):
        self.assertEqual(gate_policy.classify_command("rg -n 'while true' src/"), "hands_on")

    def test_sleep_loop_is_polling(self):
        self.assertEqual(
            gate_policy.classify_command("while ! test -f /tmp/x; do sleep 5; done"),
            "poll",
        )

    def test_bare_sleep_is_polling(self):
        self.assertEqual(gate_policy.classify_command("sleep 60"), "poll")

    def test_wait_for_invocation_is_not_polling(self):
        self.assertEqual(
            gate_policy.classify_command(
                "~/.claude/tools/wait-for --every 30 --max 600 -- test -f /tmp/x"
            ),
            "wait_for",
        )

    def test_git_diff_without_stat_is_not_exempt(self):
        self.assertEqual(gate_policy.classify_command("git diff"), "hands_on")

    def test_delivery_commands_main_keeps_are_exempt(self):
        for cmd in (
            "gh pr merge 7 --squash",
            "git push -u origin x",
            "git -C x push",
        ):
            with self.subTest(cmd=cmd):
                self.assertEqual(gate_policy.classify_command(cmd), "exempt")

    def test_watching_pr_checks_is_hands_on(self):
        self.assertEqual(gate_policy.classify_command("gh pr checks 7 --watch"), "hands_on")


class MainBudgetSessionTest(unittest.TestCase):
    def test_main_only_skill_session_is_never_throttled(self):
        self.assertEqual(
            gate_policy.budget_decision(
                "", 100000, 16, "cat src/app.py",
                loaded_skills=["test-audit", "dialogue-to-knowledge"],
            ),
            {"decision": "allow", "reason": "main-only skill dialogue-to-knowledge loaded"},
        )

    def test_hand_edit_counts_even_on_a_free_to_read_path(self):
        self.assertEqual(
            gate_policy.budget_decision(
                "", 100000, 16, "/Users/x/plans/handoff-1.md", tool_name="Edit"
            )["decision"],
            "remind",
        )


class PollDecisionTest(unittest.TestCase):
    def test_foreground_sleep_loop_is_denied(self):
        self.assertEqual(
            gate_policy.poll_decision("until gh run view; do sleep 30; done", False)["decision"],
            "deny",
        )

    def test_background_sleep_loop_is_allowed(self):
        self.assertEqual(
            gate_policy.poll_decision("until gh run view; do sleep 30; done", True)["decision"],
            "allow",
        )


class BudgetDecisionTest(unittest.TestCase):
    def test_subagent_is_never_budgeted(self):
        self.assertEqual(
            gate_policy.budget_decision("builder", 200000, 50, "cat big.py")["decision"],
            "allow",
        )

    def test_cheap_context_is_never_budgeted(self):
        self.assertEqual(
            gate_policy.budget_decision("", 40000, 50, "cat big.py")["decision"], "allow"
        )

    def test_git_status_does_not_consume_budget(self):
        self.assertEqual(
            gate_policy.budget_decision("", 200000, 7, "git status")["decision"], "allow"
        )

    def test_git_diff_stat_does_not_consume_budget(self):
        self.assertEqual(
            gate_policy.budget_decision("", 200000, 7, "git diff --stat")["decision"], "allow"
        )

    def test_wait_for_does_not_consume_budget(self):
        self.assertEqual(
            gate_policy.budget_decision(
                "", 200000, 7, "wait-for --every 30 --max 600 -- test -f /tmp/x"
            )["decision"],
            "allow",
        )

    def test_usage_audit_does_not_consume_budget(self):
        self.assertEqual(
            gate_policy.budget_decision("", 200000, 7, "python3 usage-audit.py --tree")[
                "decision"
            ],
            "allow",
        )

    def test_reading_a_handoff_does_not_consume_budget(self):
        self.assertEqual(
            gate_policy.budget_decision("", 200000, 7, "/Users/x/plans/handoff-1.md")["decision"],
            "allow",
        )

    def test_hands_on_call_at_seven_prior_calls_is_reminded_not_exempt(self):
        self.assertEqual(
            gate_policy.budget_decision("", 200000, 7, "git diff")["decision"], "remind"
        )

    def test_hands_on_call_past_any_count_is_reminded_never_denied(self):
        self.assertEqual(
            gate_policy.budget_decision("", 200000, 50, "cat src/app.py")["decision"], "remind"
        )

    def test_fourth_hands_on_call_reminds(self):
        self.assertEqual(
            gate_policy.budget_decision(
                "", 200000, 4, "cat src/app.py", params=_shipped_params()
            )["decision"],
            "remind",
        )

    def test_third_hands_on_call_is_allowed(self):
        self.assertEqual(
            gate_policy.budget_decision(
                "", 200000, 3, "cat src/app.py", params=_shipped_params()
            )["decision"],
            "allow",
        )

    def test_trigger_follows_the_params_values(self):
        cases = [
            ("call count raised", {"remind_from_window_calls": 6}, 200000, 5, "allow"),
            ("call count lowered", {"remind_from_window_calls": 2}, 200000, 2, "remind"),
            ("context floor raised", {"remind_above_context_tokens": 250000}, 200000, 9, "allow"),
        ]
        for name, budget, ctx, calls, expected in cases:
            with self.subTest(name):
                self.assertEqual(
                    gate_policy.budget_decision(
                        "", ctx, calls, "cat src/app.py", params={"main_budget": budget}
                    )["decision"],
                    expected,
                )

    def test_reminder_states_the_size_rule_from_params_without_delegate(self):
        message = gate_policy.budget_decision(
            "", 200000, 4, "cat src/app.py",
            params={"main_budget": {
                "delegate_above_result_tokens": 30000, "result_chars_per_token": 2.0,
            }},
        )["message"]
        self.assertIn("more than about 30,000 tokens of results (about 60 KB of output)", message)
        self.assertIn("otherwise read them here", message)
        self.assertNotRegex(message.lower(), r"delegat")

    def test_default_main_budget_equals_the_shipped_params(self):
        shipped = {
            k: v for k, v in _shipped_params()["main_budget"].items() if not k.startswith("_")
        }
        self.assertEqual(gate_policy.DEFAULT_PARAMS["main_budget"], shipped)


class SkillThresholdsTest(unittest.TestCase):
    def test_skill_investigations_numbers_equal_the_params(self):
        skill = os.path.join(
            os.path.dirname(os.path.realpath(__file__)),
            "..", "..", "skills", "orchestrate", "SKILL.md",
        )
        with open(skill) as fh:
            text = fh.read()
        budget = _shipped_params()["main_budget"]
        tokens = re.search(
            r"`main_budget\.delegate_above_result_tokens` tokens of results "
            r"\(([\d,]+), about (\d+) KB of output\)", text,
        )
        requests = re.search(
            r"`main_budget\.delegate_above_requests` separate requests \((\d+)\)", text
        )
        self.assertEqual(
            (int(tokens.group(1).replace(",", "")), int(tokens.group(2)), int(requests.group(1))),
            (
                budget["delegate_above_result_tokens"],
                round(budget["delegate_above_result_tokens"] * budget["result_chars_per_token"] / 1000),
                budget["delegate_above_requests"],
            ),
        )


def _shipped_params():
    with open(PARAMS_FILE) as fh:
        return json.load(fh)


class OverrideMarkerTest(unittest.TestCase):
    CASES = [
        ("colon form", "NO_POLL_OVERRIDE: reads for the retro", "reads for the retro"),
        ("equals with quotes", 'NO_POLL_OVERRIDE="reads for the retro report"\nls', "reads for the retro report"),
        ("equals with quotes then a command", 'NO_POLL_OVERRIDE="reads for the retro report" cat a', "reads for the retro report"),
        ("unquoted equals form", "NO_POLL_OVERRIDE=reads cat src/app.py", None),
        ("trailing comment after a command", "cat x # NO_POLL_OVERRIDE: reason text", None),
        ("leading hash", "cat a\n  # NO_POLL_OVERRIDE: delivery setup for slices", "delivery setup for slices"),
        ("inside a grep argument", "rtk grep NO_POLL_OVERRIDE ~/.claude/x.jsonl", None),
        ("inside an echo string", 'echo "NO_POLL_OVERRIDE: delivery setup for slices"', None),
        ("reason under the minimum", "# NO_POLL_OVERRIDE: ok", None),
        ("other marker name", "LOOP_COST: waiting on a deploy", None),
    ]

    def test_cases(self):
        for label, text, expected in self.CASES:
            with self.subTest(label):
                self.assertEqual(
                    gate_policy.override_marker(text, "NO_POLL_OVERRIDE"), expected
                )



class AgentBudgetDecisionTest(unittest.TestCase):
    def test_work_agent_below_both_thresholds_is_allowed(self):
        self.assertEqual(
            gate_policy.agent_budget_decision(100000, 2.0, 30, "builder")["decision"],
            "allow",
        )

    def test_work_agent_over_the_soft_context_threshold_is_reminded(self):
        self.assertEqual(
            gate_policy.agent_budget_decision(160000, 2.0, 30, "builder")["decision"],
            "remind",
        )

    def test_work_agent_at_the_hard_context_threshold_is_stopped(self):
        self.assertEqual(
            gate_policy.agent_budget_decision(230000, 2.0, 30, "builder")["decision"],
            "stop",
        )

    def test_work_agent_over_its_cost_ceiling_is_stopped_on_a_cheap_context(self):
        self.assertEqual(
            gate_policy.agent_budget_decision(90000, 26.0, 30, "builder")["decision"],
            "stop",
        )

    def test_builder_carries_the_higher_cost_ceiling(self):
        params = json.load(open(PARAMS_FILE))
        for p in (None, params):
            with self.subTest(params="file" if p else "defaults"):
                self.assertEqual(
                    gate_policy.agent_budget_decision(90000, 8.0, 30, "builder", p)[
                        "decision"
                    ],
                    "allow",
                )
                self.assertEqual(
                    gate_policy.agent_budget_decision(90000, 8.0, 30, "reviewer", p)[
                        "decision"
                    ],
                    "stop",
                )

    def test_tool_calls_never_budget_a_work_agent(self):
        self.assertEqual(
            gate_policy.agent_budget_decision(60000, 1.0, 300, "builder")["decision"],
            "allow",
        )

    def test_call_capped_agent_is_reminded_at_its_soft_cap(self):
        self.assertEqual(
            gate_policy.agent_budget_decision(9000, 0.1, 12, "scout")["decision"],
            "remind",
        )

    def test_call_capped_agent_is_stopped_at_its_hard_cap(self):
        self.assertEqual(
            gate_policy.agent_budget_decision(9000, 0.1, 18, "scout")["decision"], "stop"
        )

    def test_call_capped_agent_below_its_soft_cap_is_allowed(self):
        self.assertEqual(
            gate_policy.agent_budget_decision(9000, 0.1, 11, "scout")["decision"],
            "allow",
        )

    def test_context_never_budgets_a_call_capped_agent(self):
        self.assertEqual(
            gate_policy.agent_budget_decision(300000, 9.0, 3, "prod-probe")["decision"],
            "allow",
        )

    def test_uncapped_agent_type_is_always_allowed(self):
        self.assertEqual(
            gate_policy.agent_budget_decision(300000, 20.0, 400, "watcher")["decision"],
            "allow",
        )

    def test_supplied_parameters_replace_the_defaults(self):
        decision = gate_policy.agent_budget_decision(
            60000, 0.5, 3, "builder", {"context_soft": 50000}
        )
        self.assertEqual(decision["decision"], "remind")

    def test_partial_parameters_keep_the_remaining_defaults(self):
        decision = gate_policy.agent_budget_decision(
            230000, 0.5, 3, "builder", {"poll_sleep_min_seconds": 10}
        )
        self.assertEqual(decision["decision"], "stop")

    def test_stop_offers_the_commit_path_only_to_branch_owning_types(self):
        self.assertIn(
            "handoff.sh",
            gate_policy.agent_budget_decision(230000, 1.0, 3, "builder")["reason"],
        )
        self.assertIn(
            "does not commit",
            gate_policy.agent_budget_decision(230000, 1.0, 3, "reviewer")["reason"],
        )


class SkillLoadDecisionTest(unittest.TestCase):
    def test_subagent_may_load_a_large_expert_skill(self):
        self.assertEqual(
            gate_policy.skill_load_decision(False, 90000, "example-skill")["decision"],
            "allow",
        )

    def test_main_session_is_denied_a_large_expert_skill(self):
        self.assertEqual(
            gate_policy.skill_load_decision(True, 90000, "example-skill")["decision"],
            "deny",
        )

    def test_main_session_may_load_a_small_expert_skill(self):
        self.assertEqual(
            gate_policy.skill_load_decision(True, 12000, "example-skill")["decision"],
            "allow",
        )

    def test_main_session_may_load_a_large_process_skill(self):
        self.assertEqual(
            gate_policy.skill_load_decision(True, 90000, "orchestrate")["decision"],
            "allow",
        )

    def test_process_skills_can_be_added_by_naming_convention(self):
        self.assertEqual(
            gate_policy.skill_load_decision(True, 90000, "workflow-authoring")["decision"],
            "allow",
        )
        self.assertEqual(
            gate_policy.skill_load_decision(True, 90000, "release-process")["decision"],
            "allow",
        )

    def test_override_reason_allows_and_is_reported_back(self):
        decision = gate_policy.skill_load_decision(
            True, 90000, "example-skill", None, "user asked for the contract text"
        )
        self.assertEqual(decision["decision"], "allow")
        self.assertEqual(decision["override"], "user asked for the contract text")

    def test_threshold_comes_from_the_parameters(self):
        self.assertEqual(
            gate_policy.skill_load_decision(
                True, 20000, "example-skill", {"skill_bytes_threshold": 10000}
            )["decision"],
            "deny",
        )


    def test_deny_names_on_load_and_docs_sizes(self):
        reason = gate_policy.skill_load_decision(
            True, 12400, "example-skill", None, None, 44000
        )["reason"]
        self.assertIn("12400 bytes", reason)
        self.assertIn("threshold is 12000 bytes", reason)
        self.assertIn("44k", reason)
        self.assertIn("SKILL_LOAD_OVERRIDE", reason)

    def test_allowed_load_with_large_docs_reports_both_sizes(self):
        decision = gate_policy.skill_load_decision(
            True, 8872, "example-skill", None, None, 43999
        )
        self.assertEqual(decision["decision"], "allow")
        self.assertIn("9k on load", decision["context"])
        self.assertIn("44k", decision["context"])

    def test_process_skill_gets_sizes_without_delegation_advice(self):
        context = gate_policy.skill_load_decision(
            True, 30000, "orchestrate", None, None, 50000
        )["context"]
        self.assertIn("50k", context)
        self.assertNotIn("delegate", context)

    def test_allowed_load_with_small_docs_adds_no_context(self):
        self.assertNotIn(
            "context",
            gate_policy.skill_load_decision(True, 2000, "small-skill", None, None, 2500),
        )


class ChecksDecisionTest(unittest.TestCase):
    def test_declared_check_run_after_the_last_edit_is_allowed(self):
        events = [
            {"kind": "edit"},
            {"kind": "command", "command": "rtk proxy poetry run pytest tests/x.py -q"},
        ]
        self.assertEqual(
            gate_policy.checks_decision("poetry run pytest tests/x.py", events)["decision"],
            "allow",
        )

    def test_check_run_before_the_last_edit_returns_the_agent(self):
        events = [
            {"kind": "edit"},
            {"kind": "command", "command": "poetry run pytest tests/x.py"},
            {"kind": "edit"},
        ]
        self.assertEqual(
            gate_policy.checks_decision("poetry run pytest tests/x.py", events)["decision"],
            "return",
        )

    def test_an_agent_that_edited_nothing_is_allowed(self):
        events = [{"kind": "command", "command": "rtk git status"}]
        self.assertEqual(
            gate_policy.checks_decision("poetry run pytest tests/x.py", events)["decision"],
            "allow",
        )

    def test_no_declared_check_is_allowed_and_flagged_undeclared(self):
        decision = gate_policy.checks_decision("", [{"kind": "edit"}])
        self.assertEqual(decision["decision"], "allow")
        self.assertTrue(decision["undeclared"])

    def test_declared_check_is_flagged_only_when_missing(self):
        decision = gate_policy.checks_decision(
            "pytest", [{"kind": "edit"}, {"kind": "command", "command": "pytest -q"}]
        )
        self.assertFalse(decision.get("undeclared"))

    def test_the_agent_is_returned_only_once_per_edit(self):
        events = [{"kind": "edit"}, {"kind": "reminder"}]
        self.assertEqual(gate_policy.checks_decision("pytest", events)["decision"], "allow")

    def test_editing_again_after_a_reminder_returns_the_agent_again(self):
        events = [{"kind": "edit"}, {"kind": "reminder"}, {"kind": "edit"}]
        self.assertEqual(gate_policy.checks_decision("pytest", events)["decision"], "return")

    def test_an_unrelated_command_after_the_edit_does_not_count_as_the_check(self):
        events = [{"kind": "edit"}, {"kind": "command", "command": "rtk git diff --stat"}]
        self.assertEqual(gate_policy.checks_decision("pytest", events)["decision"], "return")

    def test_a_check_run_of_a_declared_backticked_command_settles_a_prose_line(self):
        line = "`cargo test -p campaign-engine` and the vitest suite of the app"
        events = [
            {"kind": "edit"},
            {"kind": "check", "command": "~/.claude/tools/check-run -- rtk cargo test -p campaign-engine 2>&1 | tail -5"},
        ]
        self.assertEqual(gate_policy.checks_decision(line, events)["decision"], "allow")

    def test_a_check_run_of_another_command_does_not_settle_a_line_with_commands(self):
        line = "`cargo test -p campaign-engine` and the vitest suite of the app"
        events = [{"kind": "edit"}, {"kind": "check", "command": "check-run -- npx vitest run"}]
        self.assertEqual(gate_policy.checks_decision(line, events)["decision"], "return")

    def test_any_check_run_settles_a_prose_only_line(self):
        line = "native tests of the touched crate plus the harness run"
        events = [{"kind": "edit"}, {"kind": "check", "command": "check-run -- cargo test -p backend"}]
        self.assertEqual(gate_policy.checks_decision(line, events)["decision"], "allow")

    def test_a_line_with_two_commands_settles_only_when_both_ran_after_the_last_edit(self):
        line = "`cargo test -p a` and `cargo clippy --all-targets`"
        one = [{"kind": "edit"}, {"kind": "check", "command": "check-run -- cargo test -p a"}]
        both = one + [{"kind": "command", "command": "rtk cargo clippy --all-targets 2>&1 | tail -3"}]
        self.assertEqual(
            [gate_policy.checks_decision(line, events)["decision"] for events in (one, both)],
            ["return", "allow"],
        )

    def test_a_path_span_declares_no_command_that_a_run_naming_it_settles(self):
        line = "`cargo test -p a` covering `src/a.rs`"
        events = [{"kind": "edit"}, {"kind": "check", "command": "check-run -- wc -l src/a.rs"}]
        self.assertEqual(gate_policy.checks_decision(line, events)["decision"], "return")

    def test_a_declared_command_only_mentioned_after_the_last_edit_does_not_settle_it(self):
        line = "`cargo test -p a` and `cargo clippy --all-targets`"
        mentions = {
            "note": "cat >> orch-notes/HANDOFF.md <<'EOF'\nnot run: cargo test -p a, cargo clippy --all-targets\nEOF",
            "commit message": "git commit -qm 'wip; todo: cargo test -p a and cargo clippy --all-targets'",
            "search": "grep -rn 'cargo test -p a' docs; grep -rn 'cargo clippy --all-targets' docs",
            "echo": "check-run -- cargo test -p a; echo skipped cargo clippy --all-targets",
        }
        for name, command in mentions.items():
            with self.subTest(name):
                events = [{"kind": "edit"}, {"kind": "command", "command": command}]
                self.assertEqual(gate_policy.checks_decision(line, events)["decision"], "return")

    def test_a_declared_loop_run_through_a_shell_script_argument_settles_it(self):
        line = ("`for test in orch/tools/tests/test_*.py; do python3 \"$test\" || exit 1; done`, then "
                "`python3 scripts/validate-skills.py`")
        runs = (
            "~/.claude/tools/check-run -- sh -c 'for test in orch/tools/tests/test_*.py; do python3 \"$test\" "
            "|| exit 1; done; python3 scripts/validate-skills.py'",
            "check-run -- sh -c \"for test in orch/tools/tests/test_*.py; do python3 \\\"\\$test\\\" || exit 1; "
            "done; python3 scripts/validate-skills.py\"",
            "for test in orch/tools/tests/test_*.py\ndo\n  python3 \"$test\" || exit 1\ndone\n"
            "python3 scripts/validate-skills.py",
        )
        for command in runs:
            with self.subTest(command=command):
                events = [{"kind": "edit"}, {"kind": "command", "command": command}]
                self.assertEqual(gate_policy.checks_decision(line, events)["decision"], "allow")


def _events(tool, cwd="/repo", **tool_input):
    return gate_policy.builder_events(tool, tool_input, cwd)


class BuilderEventsTest(unittest.TestCase):
    def test_notes_and_tmp_written_by_any_tool_are_not_edits(self):
        cases = [
            ("Write", {"file_path": "/repo/orch-notes/DESIGN.md"}),
            ("Write", {"file_path": "/repo/tmp/m6/reader_probe.py"}),
            ("Bash", {"command": "cd /repo/orch-notes && python3 - <<'EOF'\n"
                                 "p = 'DESIGN.md'\nopen(p, 'w').write(open(p).read().replace('a', 'b'))\nEOF"}),
            ("Bash", {"command": "R=/repo; cat > $R/orch-notes/HANDOFF.md <<'EOF'\n"
                                 "8. Run `~/.claude/tools/check-run -- pytest`\nEOF"}),
            ("Bash", {"command": "echo exclude >> \"$(git rev-parse --git-common-dir)/info/exclude\""}),
            ("Bash", {"command": "cd /gone/wt/orch-notes; python3 - <<'EOF'\n"
                                 "s = open('SPEC.md').read()\nopen('SPEC.md', 'w').write(s)\nEOF"}),
            ("Bash", {"command": "python3 - <<'EOF'\nimport sys; sys.path.insert(0, 'orch/tools')\n"
                                 "s = open('src/app.py').read()\nopen('/tmp/out.txt', 'w').write(s)\nEOF"}),
        ]
        for tool, tool_input in cases:
            with self.subTest(tool_input=tool_input):
                self.assertNotIn("edit", [e["kind"] for e in _events(tool, **tool_input)])

    def test_writes_into_the_tree_are_edits(self):
        cases = [
            ("Edit", {"file_path": "/repo/src/app.py"}),
            ("Bash", {"command": "sed -i '' 's/a/b/' src/app.py"}),
            ("Bash", {"command": "cd /repo/tmp && echo x > /repo/src/app.py"}),
            ("Bash", {"command": "python3 - <<'EOF'\nfrom pathlib import Path\n"
                                 "Path('crates/core/tests/send.rs').write_text('x')\nEOF"}),
            ("Bash", {"command": "git apply <<'EOF'\n--- a/x\n+++ b/x\nEOF"}),
            ("Bash", {"command": "python3 - <<'EOF'\nimport json, os\nplan = json.load(open('/tmp/plan.json'))\n"
                                 "for n in plan:\n    open(os.path.join('src', n), 'w').write('x')\nEOF"}),
            ("Bash", {"command": "python3 - src/app.py <<'EOF'\nimport sys\np = sys.argv[1]\n"
                                 "open(p, 'w').write('x')\nopen('tmp/patch.log', 'a').write('done')\nEOF"}),
            ("Bash", {"command": "python3 - <<'EOF'\nf = open('src/app.py', 'wt')\nprint('x', file=f)\nEOF"}),
            ("Bash", {"command": "cat <<'EOF' | python3 -\nopen('src/app.py', 'w').write('x')\nEOF"}),
        ]
        for tool, tool_input in cases:
            with self.subTest(tool_input=tool_input):
                self.assertEqual(_events(tool, **tool_input)[0]["kind"], "edit")

    def test_a_tmp_directory_above_the_working_directory_is_the_repository_not_scratch(self):
        events = _events("Edit", cwd="/x/tmp/case/wt", file_path="/x/tmp/case/wt/src/app.py")
        self.assertEqual([e["kind"] for e in events], ["edit"])

    def test_check_run_counts_only_as_the_command_word(self):
        cases = {
            "~/.claude/tools/check-run -- sh -c 'pytest -q' 2>&1 | tail -3": "check",
            "rtk grep -n check-run orch/README.md": "command",
            "cat > /repo/orch-notes/HANDOFF.md <<'EOF'\nrun check-run -- pytest\nEOF": "command",
        }
        for command, kind in cases.items():
            with self.subTest(command=command):
                self.assertEqual(_events("Bash", command=command)[-1]["kind"], kind)

    def test_reads_and_commits_are_not_edits(self):
        for command in ("rtk cargo test 2>&1 | tail -5",
                        "git commit -q -m \"$(cat <<'EOF'\nfix\nEOF\n)\""):
            with self.subTest(command=command):
                self.assertEqual([e["kind"] for e in _events("Bash", command=command)], ["command"])

    def test_write_targets_of_each_shell_write_form(self):
        cases = {
            "cd /srv/app && echo x > out.txt": ["/srv/app/out.txt"],
            "cat <<'EOF' > /srv/f\nbody\nEOF": ["/srv/f"],
            "printf 'x' >> \"/srv/a log\"": ["/srv/a log"],
            "rtk cat a.txt | tee /srv/copy": ["/srv/copy"],
            "sed -i '' s/a/b/ /srv/file.txt": ["/srv/file.txt"],
            "rg foo src > /srv/hits.txt": ["/srv/hits.txt"],
            "sed -n '10,40p' file.txt": [],
            "cargo check 2>&1 | tail -40": [],
            "rg -n 'x > y' src/": [],
            "echo x >| /srv/f": ["/srv/f"],
            "sed -i -e 's/a/b/' -e '/c/d' /srv/x": ["/srv/x"],
            "cd /gone/wt; echo x > src/a.py": ["/gone/wt/src/a.py"],
            "cd \"$D\"; echo 1 > a.rs": [None],
            "cd /gone/wt || exit 1; echo 1 > tmp/a.txt": [None],
            "python3 - <<'EOF'\nimport sys; sys.path.insert(0, 'orch/tools')\nimport gate_policy\n"
            "open('/srv/runs/refusal.txt', 'w').write(gate_policy.X)\nEOF": ["/srv/runs/refusal.txt"],
        }
        for command, writes in cases.items():
            with self.subTest(command=command):
                self.assertEqual(gate_policy.bash_effects(command, "/repo")["writes"], writes)


    def test_running_a_script_the_session_wrote_is_read_for_its_writes(self):
        scripts = {"/repo/tmp/patch.py": "s = open('src/a.py').read()\nopen('src/a.py', 'w').write(s)\n",
                   "/tmp/probe.py": "print(open('src/a.py').read())\n"}
        kinds = {command: [e["kind"] for e in gate_policy.builder_events("Bash", {"command": command}, "/repo", scripts)]
                 for command in ("python3 tmp/patch.py", "python3 /tmp/probe.py")}
        self.assertEqual(kinds, {"python3 tmp/patch.py": ["edit", "command"],
                                 "python3 /tmp/probe.py": ["command"]})

    def test_a_script_written_and_run_in_one_command_is_read_for_its_writes(self):
        cases = {
            "cat > /tmp/p.py <<'EOF'\nopen('src/a.py', 'w').write('x')\nEOF\npython3 /tmp/p.py": ["edit"],
            "cat > /tmp/e.sh <<'EOF'\nsed -i '' s/a/b/ src/a.rs\nEOF\nbash /tmp/e.sh": ["edit"],
            "cat > /tmp/r.py <<'EOF'\nprint(open('src/a.py').read())\nEOF\npython3 /tmp/r.py": [],
        }
        for command, edits in cases.items():
            with self.subTest(command=command):
                self.assertEqual([e["kind"] for e in _events("Bash", command=command) if e["kind"] == "edit"], edits)

    def test_a_shell_script_the_session_wrote_earlier_is_read_for_its_writes(self):
        scripts = {"/tmp/e.sh": "sed -i '' s/a/b/ src/a.rs\n"}
        events = gate_policy.builder_events("Bash", {"command": "sh /tmp/e.sh"}, "/repo", scripts)
        self.assertEqual([e["kind"] for e in events], ["edit", "command"])

    def test_a_script_written_by_a_heredoc_and_then_run_is_the_edit(self):
        written = _events("Bash", command="cat > /tmp/p.py <<'EOF'\nopen('src/a.py', 'w').write('x')\nEOF")
        scripts = {e["path"]: e["body"] for e in written if e["kind"] == "script"}
        events = gate_policy.builder_events("Bash", {"command": "python3 /tmp/p.py"}, "/repo", scripts)
        self.assertEqual([e["kind"] for e in written + events], ["script", "command", "edit", "command"])

    def test_a_syntax_check_of_a_session_written_script_runs_nothing(self):
        scripts = {"/tmp/e.sh": "sed -i '' s/a/b/ src/a.rs\n"}
        for command in ("bash -n /tmp/e.sh && echo syntax ok", "zsh -n /tmp/e.sh", "sh -n /tmp/e.sh"):
            with self.subTest(command=command):
                events = gate_policy.builder_events("Bash", {"command": command}, "/repo", scripts)
                self.assertEqual([e["kind"] for e in events], ["command"])

    CHECK = "check: `cargo test -p a` and `cargo clippy --all-targets`"
    CHECK_SCRIPT = "set -e\ncargo test -p a\ncargo clippy --all-targets\n"

    def _calls(self, *calls):
        """The hook's record of ``calls`` (tool, input): script bodies kept by path, as checks-before-done does."""
        events, scripts = [], {}
        for tool, tool_input in calls:
            for event in gate_policy.builder_events(tool, tool_input, "/repo", scripts):
                if event["kind"] == "script":
                    scripts[event["path"]] = event["body"]
                else:
                    events.append(event)
        return events

    def test_a_declared_command_inside_a_session_written_script_run_after_the_last_edit_counts(self):
        for run in ("~/.claude/tools/check-run -- bash /repo/tmp/check.sh", "check-run -- tmp/check.sh",
                    "bash tmp/check.sh"):
            with self.subTest(run=run):
                events = self._calls(("Write", {"file_path": "/repo/tmp/check.sh", "content": self.CHECK_SCRIPT}),
                                     ("Edit", {"file_path": "/repo/src/a.rs"}),
                                     ("Bash", {"command": run}))
                self.assertEqual(gate_policy.checks_decision(self.CHECK, events)["decision"], "allow")

    def test_a_script_changed_after_its_last_whole_write_credits_nothing(self):
        write = ("Write", {"file_path": "/repo/tmp/check.sh", "content": self.CHECK_SCRIPT})
        edit, run = ("Edit", {"file_path": "/repo/src/a.rs"}), ("Bash", {"command": "check-run -- bash tmp/check.sh"})
        for change in (("Bash", {"command": "sed -i '' '/clippy/d' tmp/check.sh"}),
                       ("Edit", {"file_path": "/repo/tmp/check.sh", "old_string": "cargo clippy --all-targets\n",
                                 "new_string": ""}),
                       ("Bash", {"command": "printf 'cargo test -p a\\n' > tmp/check.sh"}),
                       ("Bash", {"command": "cat >> tmp/check.sh <<'EOF'\nls\nEOF"}),
                       ("Bash", {"command": "rm tmp/check.sh"})):
            with self.subTest(change=change):
                events = self._calls(write, edit, change, run)
                self.assertEqual(gate_policy.checks_decision(self.CHECK, events)["decision"], "return")
        rewrite = ("Bash", {"command": "cat > tmp/check.sh <<'EOF'\n" + self.CHECK_SCRIPT + "EOF"})
        events = self._calls(write, edit, ("Bash", {"command": "sed -i '' '/clippy/d' tmp/check.sh"}), rewrite, run)
        self.assertEqual(gate_policy.checks_decision(self.CHECK, events)["decision"], "allow")

    def test_a_run_of_a_script_changed_after_its_last_whole_write_is_an_edit(self):
        write = ("Write", {"file_path": "/repo/tmp/patch.py", "content": "print('read only')\n"})
        change = ("Bash", {"command": "sed -i '' 's/read only/x/' tmp/patch.py"})
        events = self._calls(write, change, ("Bash", {"command": "python3 tmp/patch.py"}))
        self.assertEqual([e["kind"] for e in events], ["command", "edit", "command"])
        self.assertEqual(gate_policy.checks_decision(self.CHECK, events)["decision"], "return")
        ran = self._calls(write, change, ("Bash", {"command": "python3 tmp/patch.py"}),
                          ("Bash", {"command": "cargo test -p a && cargo clippy --all-targets"}))
        self.assertEqual(gate_policy.checks_decision(self.CHECK, ran)["decision"], "allow")

    def test_a_note_written_to_an_unresolved_path_is_no_script_for_an_unresolved_run(self):
        events = self._calls(("Edit", {"file_path": "/repo/src/a.rs"}),
                             ("Bash", {"command": "cd \"$WT\" && cat > orch-notes/HANDOFF.md <<'EOF'\n"
                                                  "Run `cargo test -p a` and `cargo clippy --all-targets`.\nEOF"}),
                             ("Bash", {"command": "cd \"$WT\" && ./gradlew build"}))
        self.assertEqual(gate_policy.checks_decision(self.CHECK, events)["decision"], "return")

    def test_a_declared_command_only_in_a_written_script_that_did_not_run_is_returned(self):
        events = self._calls(("Write", {"file_path": "/repo/tmp/check.sh", "content": self.CHECK_SCRIPT}),
                             ("Edit", {"file_path": "/repo/src/a.rs"}),
                             ("Bash", {"command": "bash -n tmp/check.sh && cat tmp/check.sh"}))
        self.assertEqual(gate_policy.checks_decision(self.CHECK, events)["decision"], "return")


class SpawnDecisionTest(unittest.TestCase):
    def test_main_session_may_spawn_the_flow_types(self):
        params = json.load(open(PARAMS_FILE))
        for p in (None, params):
            for requested in ("builder", "reviewer", "scout", "prod-probe", "watcher", "claude-code-guide"):
                with self.subTest(params="file" if p else "defaults", requested=requested):
                    self.assertEqual(
                        gate_policy.spawn_decision("", requested, p)["decision"], "allow"
                    )

    def test_main_session_may_not_spawn_a_type_outside_the_flow(self):
        params = json.load(open(PARAMS_FILE))
        for p in (None, params):
            with self.subTest(params="file" if p else "defaults"):
                decision = gate_policy.spawn_decision("", "Plan", p)
                self.assertEqual(decision["decision"], "deny")
                for replacement in ("superpowers-writing-plans-psilon", "`orch:scout`", "`orch:reviewer`"):
                    self.assertIn(replacement, decision["reason"])

    def test_the_builtin_explore_is_refused_with_the_orch_scout_replacement(self):
        params = json.load(open(PARAMS_FILE))
        for p in (None, params):
            with self.subTest(params="file" if p else "defaults"):
                decision = gate_policy.spawn_decision("", "Explore", p)
                self.assertEqual(decision["decision"], "deny")
                self.assertIn("`orch:scout`", decision["reason"])

    def test_both_default_copies_grant_main_the_same_rights(self):
        params = json.load(open(PARAMS_FILE))
        self.assertEqual(
            params["spawn_rights"]["main"],
            gate_policy.DEFAULT_PARAMS["spawn_rights"]["main"],
        )

    def test_untyped_request_is_refused_even_from_the_main_session(self):
        self.assertEqual(
            gate_policy.spawn_decision("", "general-purpose")["decision"], "deny"
        )

    def test_builder_may_spawn_only_a_search_agent(self):
        params = json.load(open(PARAMS_FILE))
        for p in (None, params):
            with self.subTest(params="file" if p else "defaults"):
                self.assertEqual(
                    gate_policy.spawn_decision("builder", "scout", p)["decision"],
                    "allow",
                )
                for requested in ("builder", "reviewer", "prod-probe", "Plan"):
                    decision = gate_policy.spawn_decision("builder", requested, p)
                    self.assertEqual(decision["decision"], "deny")
                    self.assertIn("may spawn only `orch:scout`", decision["reason"])

    def test_refusal_texts_name_orch_agents_as_the_model_spawns_them(self):
        # The model sees orch agents only as `orch:<type>`; a bare name or a
        # pointer at ~/.claude/agents sends it to a type that does not exist.
        bare = re.compile(r"`(%s)`" % "|".join(map(re.escape, gate_policy.ORCH_AGENTS)))
        texts = [
            v for k, v in vars(gate_policy).items()
            if isinstance(v, str) and re.search(r"_(REASON|DENY)$", k)
        ]
        texts += [
            gate_policy.spawn_decision("builder", "orch:reviewer")["reason"],
            gate_policy.spawn_decision("", "general-purpose")["reason"],
            gate_policy.model_override_decision("builder", "orch:scout", "opus")["reason"],
        ]
        self.assertIn("scout", gate_policy.ORCH_AGENTS)
        for text in texts:
            with self.subTest(text=text[:60]):
                self.assertIsNone(bare.search(text))
                self.assertNotIn("~/.claude/agents", text)

    def test_reviewer_may_spawn_a_search_agent(self):
        self.assertEqual(
            gate_policy.spawn_decision("reviewer", "scout")["decision"], "allow"
        )

    def test_a_type_without_rights_may_spawn_nothing(self):
        self.assertEqual(
            gate_policy.spawn_decision("watcher", "scout")["decision"], "deny"
        )

    def test_params_can_grant_a_type_not_granted_by_default(self):
        params = {"spawn_rights": {"reviewer": ["prod-probe"], "default": []}}
        self.assertEqual(
            gate_policy.spawn_decision("reviewer", "prod-probe", params)["decision"],
            "allow",
        )

    def test_params_can_withdraw_a_type_granted_by_default(self):
        params = {"spawn_rights": {"reviewer": ["prod-probe"], "default": []}}
        self.assertEqual(
            gate_policy.spawn_decision("reviewer", "scout", params)["decision"],
            "deny",
        )


_SONNET_SCOUT = {"model_override_rights": {"builder": {"scout": ["sonnet"]}}}


class ModelOverrideDecisionTest(unittest.TestCase):
    def test_main_session_may_choose_the_model(self):
        self.assertEqual(
            gate_policy.model_override_decision("", "reviewer", "fable")["decision"],
            "allow",
        )

    def test_a_call_without_a_model_takes_it_from_the_definition(self):
        self.assertEqual(
            gate_policy.model_override_decision("builder", "scout", None)["decision"],
            "allow",
        )

    def test_a_spawner_may_not_raise_the_model_of_the_type_it_spawns(self):
        self.assertEqual(
            gate_policy.model_override_decision("builder", "scout", "opus")["decision"],
            "deny",
        )

    def test_a_listed_triple_is_allowed(self):
        self.assertEqual(
            gate_policy.model_override_decision(
                "builder", "scout", "sonnet", _SONNET_SCOUT
            )["decision"],
            "allow",
        )

    def test_a_right_for_one_requested_type_does_not_cover_another(self):
        self.assertEqual(
            gate_policy.model_override_decision(
                "builder", "prod-probe", "sonnet", _SONNET_SCOUT
            )["decision"],
            "deny",
        )

    def test_a_right_for_one_model_does_not_cover_another(self):
        self.assertEqual(
            gate_policy.model_override_decision(
                "builder", "scout", "opus", _SONNET_SCOUT
            )["decision"],
            "deny",
        )


class PluginPrefixedTypeTest(unittest.TestCase):
    """A plugin agent arrives as `<plugin>:<type>` in both the spawner's
    agent_type and the requested subagent_type; it keeps the bare type's rules."""

    def test_prefixed_builder_keeps_the_builder_spawn_rights(self):
        self.assertEqual(
            gate_policy.spawn_decision("orch:builder", "orch:scout")["decision"], "allow"
        )
        self.assertEqual(
            gate_policy.spawn_decision("orch:builder", "orch:reviewer")["decision"],
            "deny",
        )

    def test_prefixed_untyped_request_is_still_refused(self):
        self.assertEqual(
            gate_policy.spawn_decision("", "orch:general-purpose")["decision"], "deny"
        )

    def test_prefixed_types_keep_their_cost_ceilings(self):
        self.assertEqual(
            gate_policy.agent_budget_decision(90000, 8.0, 30, "orch:builder")["decision"],
            "allow",
        )
        self.assertEqual(
            gate_policy.agent_budget_decision(90000, 8.0, 30, "orch:reviewer")["decision"],
            "stop",
        )

    def test_prefixed_pair_keeps_its_model_override_right(self):
        self.assertEqual(
            gate_policy.model_override_decision(
                "orch:builder", "orch:scout", "sonnet", _SONNET_SCOUT
            )["decision"],
            "allow",
        )




_MERGE_FIELDS = (
    "CHECK_RAN: ring r10 on prod, 3635/s",
    "PROD_POSITION: mailboxes touched, prod 187k, example-db count",
    "WAY_BACK: revert PR, 10 min",
    "WORSE_FOR: none, the change touches one tenant's own path",
)
_MERGE_FULL = "echo '" + "; ".join(_MERGE_FIELDS) + "'; gh pr merge 12 --squash"


class MergeGateDecisionTest(unittest.TestCase):
    def test_merge_without_token_is_denied(self):
        self.assertEqual(gate_policy.merge_gate_decision("gh pr merge 12 --squash")["decision"], "deny")

    def test_push_to_master_without_token_is_denied(self):
        self.assertEqual(
            gate_policy.merge_gate_decision("git push origin HEAD:master")["decision"], "deny"
        )

    def test_push_with_git_global_options_is_gated(self):
        for cmd in ("git -C /w push origin x:" + "master", "git -c a=b --git-dir=/w/.git push origin " + "main"):
            result = gate_policy.merge_gate_decision(cmd)
            self.assertEqual(result["decision"], "deny", cmd)
            self.assertIn("`CHECK_RAN", result["reason"].split("Missing:")[1], cmd)

    def test_merge_with_all_fields_is_allowed(self):
        self.assertEqual(gate_policy.merge_gate_decision(_MERGE_FULL)["decision"], "allow")

    def test_merge_missing_any_field_is_denied_naming_it(self):
        for field in _MERGE_FIELDS:
            cmd = _MERGE_FULL.replace(field, "")
            result = gate_policy.merge_gate_decision(cmd)
            self.assertEqual(result["decision"], "deny", field)
            self.assertIn("`" + field.split(" ")[0], result["reason"].split("Missing:")[1], field)

    # Incident fixture: the merge of a shared-mechanism change with the three older fields (retro CASE-50, N-04).
    _SHARED_LEVER = (
        "gh pr merge 237 --squash --delete-branch # CHECK_RAN: build-and-test success run 39100814200; "
        "PROD_POSITION: all ClickHouse query CPU scheduling on node-1 prod; "
        "WAY_BACK: run gitops/infra/sql/workloads/rollback.sql via Job + revert PR, ~25 min"
    )

    def test_shared_lever_without_worse_for_is_denied_naming_it(self):
        result = gate_policy.merge_gate_decision(self._SHARED_LEVER)
        self.assertEqual(result["decision"], "deny")
        self.assertIn("`WORSE_FOR", result["reason"].split("Missing:")[1])

    def test_branch_push_and_pr_create_are_allowed(self):
        for cmd in ("git push -u origin retro-fixes-2", "gh pr create --fill"):
            self.assertEqual(gate_policy.merge_gate_decision(cmd)["decision"], "allow", cmd)

    def test_delegate_landing_is_denied_even_with_all_fields(self):
        for cmd in (_MERGE_FULL, _MERGE_FULL.replace("gh pr merge 12 --squash", "git push origin HEAD:" + "master")):
            result = gate_policy.merge_gate_decision(cmd, None, delegate=True)
            self.assertEqual(result["decision"], "deny", cmd)
            self.assertIn("a delegate never lands", result["reason"])

    def test_delegate_text_holding_the_phrase_falls_through_to_the_field_check(self):
        verb = "m" + "erge"
        fields = "# " + "; ".join(_MERGE_FIELDS) + "\n"
        for cmd in (
            "cat > notes.md <<'EOF'\nthen gh pr %s 12 --squash\nEOF" % verb,
            "grep -n 'gh pr %s' orch/hooks/*.py" % verb,
        ):
            bare = gate_policy.merge_gate_decision(cmd, None, delegate=True)
            self.assertEqual(bare["decision"], "deny", cmd)
            self.assertNotIn("a delegate never lands", bare["reason"])
            self.assertEqual(gate_policy.merge_gate_decision(fields + cmd, None, delegate=True)["decision"], "allow", cmd)

    # Landing shapes from the owner's transcripts (recheck 2026-11-04) and their
    # wrapper siblings; the first is builder aa90618c, 2026-10-21.
    _VERB = "m" + "erge"
    _LANDING_FORMS = (
        "for p in repo-a:281 repo-b:422; do gh pr %s ${p#*:} --squash; done" % _VERB,
        "# CHECK_RAN: the revision's SQL ran on today's state\ngh pr %s 410 --squash" % _VERB,
        'case "$r" in success*) gh pr %s 336 --squash;; esac' % _VERB,
        "if gh pr checks 9; then gh pr %s 9 --squash; fi" % _VERB,
        "{ gh pr %s 1; }" % _VERB,
        'bash -c "cd /r && gh pr %s 1 --squash"' % _VERB,
        "time gh pr %s 1" % _VERB,
        "env X=1 gh pr %s 1" % _VERB,
        "exec gh pr %s 1" % _VERB,
        "git push origin HEAD:refs/heads/" + "master",
        "git push origin '" + "main'",
        # Main 3da26fbb, 2026-10-17: an absolute program path.
        "/usr/bin/git push origin HEAD:" + "master",
        "timeout 120 /opt/homebrew/bin/gh pr %s 7" % _VERB,
    )

    def test_delegate_landing_forms_are_refused_as_delegate(self):
        for cmd in self._LANDING_FORMS:
            result = gate_policy.merge_gate_decision(cmd, None, delegate=True)
            self.assertIn("a delegate never lands", result.get("reason", ""), cmd)

    def test_delegate_carrying_the_fields_as_quoted_assignments_is_refused_as_delegate(self):
        # Builder 52051dcf, 2026-10-14 shape with all four fields.
        cmd = ('CHECK_RAN="CI pass 833 tests" PROD_POSITION="2 pods" WAY_BACK="revert, 15 min" '
               'WORSE_FOR="none" gh pr %s 1220 --squash' % self._VERB)
        self.assertEqual(gate_policy.merge_gate_decision(cmd, None, delegate=True).get("kind"), "delegate")

    def test_main_landing_forms_need_the_fields(self):
        fields = "\n# " + "; ".join(_MERGE_FIELDS)
        for cmd in self._LANDING_FORMS:
            self.assertEqual(gate_policy.merge_gate_decision(cmd)["decision"], "deny", cmd)
            self.assertEqual(gate_policy.merge_gate_decision(cmd + fields)["decision"], "allow", cmd)

    def test_delegate_rtk_wrapped_landing_is_denied(self):
        # A watcher landing seen in the replay, 2026-10-10 (CASE-62).
        cmd = "cd /r/repo-a && rtk gh pr " + "m" + "erge 89 -R org/repo-a --squash"
        self.assertEqual(gate_policy.merge_gate_decision(cmd, None, delegate=True)["decision"], "deny")

    def test_delegate_branch_push_and_pr_create_are_allowed(self):
        for cmd in ("git push -u origin merge-gate-delegate", "gh pr create --fill"):
            self.assertEqual(gate_policy.merge_gate_decision(cmd, None, delegate=True)["decision"], "allow", cmd)


class IsDelegateTest(unittest.TestCase):
    _MAIN = "/h/.claude/projects/-w/e021b9bc.jsonl"

    def test_subagent_payloads_are_delegates(self):
        for payload in (
            {"transcript_path": self._MAIN, "agent_id": "afd9ad9d", "agent_type": "orch:builder"},
            {"transcript_path": "/h/.claude/projects/-w/e021b9bc/subagents/agent-afd9ad9d.jsonl"},
            {"agent_id": "afd9ad9d"},
        ):
            self.assertTrue(gate_policy.is_delegate(payload), payload)

    def test_main_session_started_with_agent_flag_is_not_a_delegate(self):
        # Probe 2026-11-04: `claude --agent orch:scout -p` main-session payload.
        payload = {"transcript_path": self._MAIN, "agent_type": "orch:scout", "agent_id": None}
        self.assertFalse(gate_policy.is_delegate(payload))


class AlwaysOnFieldTest(unittest.TestCase):
    _PUSH = "git push origin always-on-gate:master  # " + "; ".join(_MERGE_FIELDS)
    _RULES = ["orch/README.md", "rules/always-on-claude.md"]

    def _d(self, extra, paths=_RULES):
        return gate_policy.merge_gate_decision(self._PUSH + "; " + extra, paths)

    def test_rules_change_without_field_is_denied_naming_it(self):
        result = self._d("")
        self.assertEqual(result["decision"], "deny")
        self.assertIn("ALWAYS_ON: <before> -> <after> chars", result["reason"])

    def test_growth_needs_removed_text_or_reason(self):
        for extra in ("ALWAYS_ON: 2438 -> 2500 chars", "ALWAYS_ON: 2438 -> 2500 chars; removed: none",
                      "ALWAYS_ON: 2438 -> 2500 chars; removed: ''"):
            self.assertEqual(self._d(extra)["decision"], "deny", extra)
        for extra in (
            "ALWAYS_ON: 2438 -> 2500 chars; removed: the reply-lint clause",
            "ALWAYS_ON: 2438 -> 2500 chars; removed: 'the reply-lint clause; and its example'",
            "ALWAYS_ON: 2438 -> 2500 chars; reason: gate field must be known up front",
        ):
            self.assertEqual(self._d(extra)["decision"], "allow", extra)

    def test_no_growth_needs_no_justification(self):
        self.assertEqual(self._d("ALWAYS_ON: 2438 -> 2400 chars")["decision"], "allow")

    def test_all_missing_fields_are_named_in_one_refusal(self):
        result = gate_policy.merge_gate_decision("git push origin x:" + "master", self._RULES)
        reason = result["reason"].split("Missing:")[1]
        for field in ("`CHECK_RAN", "`PROD_POSITION", "`WAY_BACK", "`WORSE_FOR", "`ALWAYS_ON"):
            self.assertIn(field, reason)

    def test_unknown_or_other_paths_need_no_field(self):
        for paths in (None, ["orch/README.md", "rules/always-on-claude.details.md"]):
            self.assertEqual(self._d("", paths)["decision"], "allow", paths)


class PushRangeTest(unittest.TestCase):
    def test_push_forms_that_merge_gate_gates(self):
        repo = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, repo)
        wt = os.path.realpath(repo)
        m = "ma" + "ster"
        cases = (
            ("git push origin HEAD:" + m, "/elsewhere", ("/elsewhere", "origin", "HEAD", m)),
            ("git push origin " + m, "/elsewhere", ("/elsewhere", "origin", "HEAD", m)),
            ("git push -u origin feat:" + m, "/w", ("/w", "origin", "feat", m)),
            ("git push --force-with-lease origin feat:main", "/w", ("/w", "origin", "feat", "main")),
            ("git push --force-with-lease=main:abc up +feat:refs/heads/main", "/w", ("/w", "up", "feat", "main")),
            ("git -C %s push origin feat:%s" % (repo, m), "/elsewhere", (wt, "origin", "feat", m)),
            ("cd %s && git push origin HEAD:%s" % (repo, m), "/elsewhere", (wt, "origin", "HEAD", m)),
            ("rtk git push origin feat:" + m + "  # CHECK_RAN: x", "/w", ("/w", "origin", "feat", m)),
        )
        for cmd, cwd, want in cases:
            got = gate_policy.push_range(cmd, cwd)
            self.assertEqual((got["dir"], got["remote"], got["src"], got["target"]), want, cmd)

    def test_unknown_directory_or_other_target_is_none(self):
        for cmd, cwd in (
            ("git push origin HEAD:" + "master", None),
            ("cd $X && git push origin HEAD:" + "master", "/w"),
            ("git push origin feat:release", "/w"),
            ("git push -u origin feat", "/w"),
        ):
            self.assertIsNone(gate_policy.push_range(cmd, cwd), cmd)


_M = "ma" + "ster"
_TIP = "a6213bb2de6a2258d96bfafa3b191d6eb0efefbd"
_OLD = "7c2651a2cfaff1ff6e9cab561c8c100546000636"


def _run(sha, conclusion, wf="orch-tools", event="push", status="completed", rid=1):
    return {"status": status, "conclusion": conclusion if status == "completed" else "",
            "databaseId": rid, "headSha": sha, "event": event, "workflowName": wf}


class CiTipStateTest(unittest.TestCase):
    def test_success_is_green(self):
        self.assertEqual(gate_policy.ci_tip_state([_run(_TIP, "success"), _run(_OLD, "failure")])["state"], "green")

    def test_red_conclusions_are_red_with_their_runs(self):
        for c in ("failure", "timed_out", "action_required", "startup_failure"):
            got = gate_policy.ci_tip_state([_run(_TIP, c, rid=41945456898)])
            self.assertEqual((got["state"], got["sha"], got["runs"][0]["databaseId"]), ("red", _TIP, 41945456898), c)

    def test_one_failed_workflow_of_the_tip_makes_it_red(self):
        runs = [_run(_TIP, "success", wf="Deploy"), _run(_TIP, "failure", wf="Tests", rid=7)]
        got = gate_policy.ci_tip_state(runs)
        self.assertEqual([r["workflowName"] for r in got["runs"]], ["Tests"])

    def test_skipped_and_neutral_count_green(self):
        self.assertEqual(gate_policy.ci_tip_state([_run(_TIP, "skipped"), _run(_TIP, "neutral")])["state"], "green")

    def test_cancelled_only_is_unknown(self):
        got = gate_policy.ci_tip_state([_run(_TIP, "cancelled"), _run(_OLD, "failure")])
        self.assertEqual(got["state"], "unknown")
        self.assertIn("cancelled", got["cause"])

    def test_in_progress_tip_falls_back_to_the_last_completed_sha(self):
        got = gate_policy.ci_tip_state([_run("f" * 40, "", status="in_progress"), _run(_OLD, "failure")])
        self.assertEqual((got["state"], got["sha"]), ("red", _OLD))

    def test_tip_with_a_run_still_in_progress(self):
        running = _run(_TIP, "", wf="Deploy", status="in_progress")
        self.assertEqual(gate_policy.ci_tip_state([running, _run(_TIP, "success")])["state"], "unknown")
        self.assertEqual(gate_policy.ci_tip_state([running, _run(_TIP, "failure")])["state"], "red")

    def test_scheduled_and_manual_runs_are_not_the_tip(self):
        runs = [_run("e" * 40, "success", event="schedule"), _run("d" * 40, "success", event="workflow_dispatch"),
                _run(_TIP, "failure")]
        self.assertEqual(gate_policy.ci_tip_state(runs)["sha"], _TIP)

    def test_empty_list_and_failed_read_are_unknown(self):
        self.assertEqual(gate_policy.ci_tip_state([])["state"], "unknown")
        self.assertEqual(gate_policy.ci_tip_state(None)["state"], "unknown")


class CiGateDecisionTest(unittest.TestCase):
    RED = {"state": "red", "sha": _TIP, "runs": [_run(_TIP, "failure", rid=41945456898)]}

    def test_red_tip_with_all_fields_is_denied_naming_run_and_fix_line(self):
        got = gate_policy.merge_gate_decision(_MERGE_FULL, ci=self.RED)
        self.assertEqual(got["decision"], "deny")
        for part in ("41945456898", "failure", "`# CI_FIX: a6213bb <what the push repairs>`"):
            self.assertIn(part, got["reason"])

    def test_red_item_joins_the_field_refusal(self):
        got = gate_policy.merge_gate_decision("git push origin x:" + _M, ci=self.RED)
        self.assertIn("`CHECK_RAN", got["reason"])
        self.assertIn("CI_FIX: a6213bb", got["reason"])

    def test_ci_fix_with_the_tip_sha_lifts_it(self):
        cmd = _MERGE_FULL + "\n# CI_FIX: a6213bb pin ubuntu-24.04"
        self.assertEqual(gate_policy.merge_gate_decision(cmd, ci=self.RED)["decision"], "allow")

    def test_ci_fix_without_or_with_another_sha_does_not_lift(self):
        for line in ("# CI_FIX: pin ubuntu-24.04 runner", "# CI_FIX: 7c2651a pin ubuntu-24.04"):
            got = gate_policy.merge_gate_decision(_MERGE_FULL + "\n" + line, ci=self.RED)
            self.assertEqual(got["decision"], "deny", line)

    def test_green_or_unknown_passes(self):
        for ci in ({"state": "green", "sha": _TIP}, {"state": "unknown", "cause": "gh read failed"}):
            self.assertEqual(gate_policy.merge_gate_decision(_MERGE_FULL, ci=ci)["decision"], "allow")

    def test_unknown_cause_is_named_in_another_refusal(self):
        got = gate_policy.merge_gate_decision("gh pr merge 12", ci={"state": "unknown", "cause": "TimeoutExpired"})
        self.assertIn("Workflow state unknown: TimeoutExpired", got["reason"])

    def test_non_landing_is_untouched(self):
        self.assertEqual(gate_policy.merge_gate_decision("git status", ci=self.RED)["decision"], "allow")


class LandingTargetTest(unittest.TestCase):
    def test_dir_branch_and_pr(self):
        repo = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, repo)
        wt = os.path.realpath(repo)
        cases = (
            ("cd %s && git push -q origin %s" % (repo, _M), "/other", (wt, _M, None)),
            ("git push origin x:main", "/w", ("/w", "main", None)),
            ("git -C %s push origin %s" % (repo, _M), "/other", (wt, _M, None)),
            ("gh pr merge 12 --squash", "/w", ("/w", None, "12")),
            ("cd %s && gh pr merge --squash" % repo, "/w", (wt, None, None)),
            ("gh pr merge --squash --body x 12", "/w", ("/w", None, "12")),
            ("pushd %s && git push origin %s" % (repo, _M), "/other", (wt, _M, None)),
            ('cd "$R" && git push origin ' + _M, "/w", (None, _M, None)),
            ("cd $W && git push -q origin " + _M, "/w", (None, _M, None)),
            ("cd $D && gh pr merge 12", "/w", (None, None, "12")),
            ("git push origin " + _M, None, (None, _M, None)),
        )
        for cmd, cwd, want in cases:
            got = gate_policy.landing_target(cmd, cwd)
            self.assertEqual((got["dir"], got["branch"], got["pr"]), want, cmd)


class PlanOpeningLinesTest(unittest.TestCase):
    E = "Existing: decisions/x.md:3 as is\n"

    def test_opening_lines_are_read_through_markdown(self):
        self.assertEqual(
            gate_policy.plan_head_lines("# Title\n\n**Task:** сделать X\n- **Done when:** вижу Y у клиента\n"
                                        "**Existing:** `a.md:1` as is\n"),
            {"task": "сделать X", "done_when": "вижу Y у клиента", "existing": "`a.md:1` as is"})

    def test_each_missing_or_empty_line_is_named(self):
        for text, missing in (
            ("# T\nTask: x\n" + self.E, "`Done when:"),
            ("# T\nDone when: y\n" + self.E, "`Task:"),
            ("# T\nTask: x\nDone when: y\n", "`Existing:"),
            ("# T\n**Task:** x\n**Done when:**\n" + self.E, "`Done when:"),
            ("# T\nTask: [what is made]\nDone when: y\n" + self.E, "`Task:"),
            ("# T\nTask: x\nDone when: y\nExisting: <value>\n", "`Existing:"),
            ("# T\nTask: **x**\nDone when: y\n" + self.E, None),
        ):
            reason = gate_policy.plan_lines_decision(text)
            if missing is None:
                self.assertIsNone(reason, text)
                continue
            named = reason.split("Missing or empty:")[1].split("Keep the labels")[0]
            for label in ("`Task:", "`Done when:", "`Existing:"):
                self.assertEqual("a line " + label in named, label == missing, (text, label))

    def test_a_list_under_a_label_is_its_value_and_not_a_head_line(self):
        plan = ("# T\nExisting:\n\n- **hRR** `decisions/a.md:4` as is\n  - code `ui/model.ts:9`\n- RR as is\n"
                "\nTask: x\nDone when: y\n- seen on the screen\n- by the owner\nNotes: z\n")
        self.assertIsNone(gate_policy.plan_lines_decision(plan))
        self.assertIn("ui/model.ts:9", gate_policy.plan_head_lines(plan)["existing"])

    def test_a_label_with_only_a_list_after_two_blank_lines_is_empty(self):
        plan = "# T\nTask: x\nDone when: y\nExisting:\n\n\n- decisions/a.md:4 as is\n"
        self.assertIn("`Existing:", gate_policy.plan_lines_decision(plan))

    def test_head_lines_are_counted_within_six(self):
        head = "# T\na\nb\nTask: x\nDone when: y\n"
        self.assertIsNone(gate_policy.plan_lines_decision(head + "\nExisting: z\n"))
        self.assertIn("`Existing:", gate_policy.plan_lines_decision(head + "c\nExisting: z\n"))

    def test_value_rule_rows(self):
        tail = "Done when: y\nExisting: a.md:1 as is\n"
        for text, missing in (
            ("# T\nExisting:\n- a.md:1 as is\n\n  - late\nTask: x\nDone when: y\n", None),
            ("# T\nTask: x\nDone when: y\nExisting:\n- a.md:1\n\na\nb\nc\nd\n", None),
            ("# T\nTask:\n- a\n\n- b\n- c\n- d\n- e\n" + tail, "`Done when:"),
            ("# T\nTask: x\nDone when: y\nExisting:\n1. a.md:1 as is\n2. b.md:2 as is\nc\nd\n", None),
            ("# T\nTask: x\nDone when: y\nExisting: [record]\n- a.md:1 as is\n", None),
            ("# T\n**Task:**\nTask: x\n" + tail, None),
            ("# T\nTask: x\nDone when: y\nExisting:\n  # a.md:1 as is\n", None),
        ):
            reason = gate_policy.plan_lines_decision(text)
            if missing is None:
                self.assertIsNone(reason, text)
            else:
                self.assertIn("a line " + missing, reason, text)

    def test_a_wrapped_unindented_line_is_a_head_line(self):
        head = "# T\nTask: x\nwrapped\nDone when: y\nwrapped\nwrapped\n"
        self.assertIn("`Existing:", gate_policy.plan_lines_decision(head + "Existing: z\n"))

    def test_plan_skill_carries_the_gate_property_text(self):
        prop = gate_policy._PLAN_LINE_PROPERTY["existing"][1]
        core = prop[prop.index("`Existing:` says"):prop.index(" The value may be")]
        skill = open(os.path.join(os.path.dirname(__file__), "..", "..", "..",
                                  "superpowers-writing-plans-psilon", "SKILL.md")).read()
        self.assertIn(" ".join(core.split()), " ".join(skill.split()))


class ReplyLintDecisionTest(unittest.TestCase):
    def _d(self, text):
        return gate_policy.reply_lint_decision(text, False)["decision"]

    def test_russian_not_verified_disclosure_is_an_observation(self):
        for text in (
            "Готово; цифру не перепроверял.",
            "Исправлено, на проде не воспроизводил.",
            "Готово, со слов автора работает.",
            "Сделано, по словам владельца сервиса всё работает.",
        ):
            self.assertEqual(self._d(text), "allow", text)
        for text in ("Исправлено, всё работает.", "Исправлено, ошибка не воспроизводится.", "Исправлено, раньше не воспроизводилось."):
            self.assertEqual(self._d(text), "block", text)

    def test_first_person_not_read_disclosure_counts_only_in_the_done_words_sentence(self):
        for text in ("Субъект 1 варианта 2 готов (delegate's claim, файл не читал); жду субъектов 2 и 3.",
                     "Тесты исправлены по отчёту сборщика, сам я их не запускал.",
                     "Done, but I did not run the tests."):
            self.assertEqual(self._d(text), "allow", text)
        for text in ("Исправлено. Логи я не смотрел.",
                     "Исправлено: парсер не читал последнюю строку файла.",
                     "Баг исправлен, хук не открывал транскрипт субагента.",
                     "Дизайн исправления готов. У кампаний, которые ещё не запускали, статистика тоже есть.",
                     "Мелкие замечания тоже исправлены. Ещё не запускалось нигде."):
            self.assertEqual(self._d(text), "block", text)

    def test_source_or_pre_existing_complement_is_not_a_completion_claim(self):
        for text in ("Старый путь закрыт ещё до вашей просьбы.", "Снимок сделан из админского аккаунта."):
            self.assertEqual(self._d(text), "allow", text)
        for text in ("Задача закрыта до конца.", "Снимок сделан, данные свежие."):
            self.assertEqual(self._d(text), "block", text)

    def test_done_word_before_colon_is_a_label(self):
        for text in ("Что сделано: ничего не записано.", "What was done: nothing yet.",
                     "Блокер: нет прав.\n- **Что сделано:** ничего не записано."):
            self.assertEqual(self._d(text), "allow", text)
        for text in ("Прогоны готовы: 27 сессий.", "Сделано: задеплоил.", "Done: merged and deployed.", "Что сделано: PR влит.",
                     "Вижу, что готово.", "Подтверждаю, что сделано.", "Сообщаю, что исправлено.", "I confirm what is done."):
            self.assertEqual(self._d(text), "block", text)

    def test_done_word_with_manner_complement_describes_shape(self):
        for text in ("CASE-13 готов за минуту, идёт CASE-14.", "Готово за 2 часа.", "Фильтр сделан `allowlist`ом.", "The filter is done as a denylist.", "Он сделан через хук."):
            self.assertEqual(self._d(text), "allow", text)
        for text in ("Шаги 1-4 готовы в ветке X.", "Сделано полностью.", "Сделано мной.", "Done as requested."):
            self.assertEqual(self._d(text), "block", text)

    def test_done_claim_without_observation_is_returned(self):
        self.assertEqual(gate_policy.reply_lint_decision("Готово, PR влит.", False)["decision"], "block")

    def test_done_claim_with_observation_passes(self):
        for text in (
            "Готово. Замер 10:12Z: 3635/s на кольце r10.",
            "Done.\n```\n$ curl -s localhost/health\nok\n```",
            "Исправлено; не проверено на проде.",
        ):
            self.assertEqual(gate_policy.reply_lint_decision(text, False)["decision"], "allow", text)

    def test_negated_done_is_not_a_claim(self):
        for text in ("Ещё не готово, CI идёт.", "Работа ещё идёт, сделано пока не всё.", "Done not yet: CI runs."):
            self.assertEqual(gate_policy.reply_lint_decision(text, False)["decision"], "allow", text)

    def test_second_stop_passes(self):
        self.assertEqual(gate_policy.reply_lint_decision("Готово.", True)["decision"], "allow")


    def _decide(self, text):
        return gate_policy.reply_lint_decision(text, False)["decision"]

    def test_non_assertions_of_done_pass(self):
        for text in (
            "Хук работает так: читает последний ответ.",
            "Когда пакеты будут готовы, сравню их.",
            "Три из 13 прогонов готовы.",
            "NS готовы: 0 из 15.",
            "Готово, когда CI зелёный.",
            "Если всё исправлено, вливаю ветку.",
            "**Почему так сделано:** Макс просил один раз классифицировать теги.",
        ):
            self.assertEqual(self._decide(text), "allow", text)
        for text in ("Если тесты зелёные, всё готово.", "Когда я проверил, всё было исправлено.",
                     "Ещё он проверяет, как был сделан сегодняшний перенос.",
                     "Теперь, когда всё исправлено, вливаю."):
            self.assertEqual(self._decide(text), "block", text)

    def test_complete_progress_is_a_claim(self):
        self.assertEqual(self._decide("13 из 13 готовы."), "block")

    def test_record_references_count_as_observation(self):
        for text in (
            "Fix round готов (ce382a8).",
            "Готов: example-service #7.",
            "Готово, exit 0.",
            "Готово, 20 из 20 тестов прошли.",
            "Исправлено: 3/10 → 9/10 по ключам.",
        ):
            self.assertEqual(self._decide(text), "allow", text)

    def test_bare_mentions_are_not_observations(self):
        for text in ("Шаг #2 готов.", "Готово, проверено.", "Готово, 20 тестов."):
            self.assertEqual(self._decide(text), "block", text)

    @staticmethod
    def _verdict(handoff=False, deferral=False):
        return {"handoff": handoff, "sentence": "Решение за вами.", "deferral": deferral,
                "deferral_sentence": "Поправлю отдельным PR."}

    def test_judged_hand_off_is_returned_with_its_sentence(self):
        decision = gate_policy.reply_lint_decision("Есть два варианта.", False, self._verdict(handoff=True))
        self.assertEqual(decision["decision"], "block")
        self.assertEqual(decision["rules"], ["handoff"])
        self.assertIn("Решение за вами.", decision["reason"])
        self.assertIn("AskUserQuestion", decision["reason"])

    def test_judged_deferral_is_returned_with_its_sentence(self):
        decision = gate_policy.reply_lint_decision("Текст алерта неверный.", False, self._verdict(deferral=True))
        self.assertEqual(decision["decision"], "block")
        self.assertEqual(decision["rules"], ["deferral"])
        self.assertIn("Поправлю отдельным PR.", decision["reason"])

    def test_first_person_commitment_without_a_deferral_verdict_passes(self):
        for verdict in (None, self._verdict()):
            self.assertEqual(gate_policy.reply_lint_decision("Потом поправлю тесты.", False, verdict)["decision"], "allow")

    def test_every_fired_rule_is_named(self):
        decision = gate_policy.reply_lint_decision("Готово, PR влит.", False, self._verdict(True, True))
        self.assertEqual(decision["rules"], ["claim", "handoff", "deferral"])

    def test_no_hand_off_or_no_verdict_passes(self):
        for verdict in (None, {"handoff": False, "sentence": "none", "deferral": False, "deferral_sentence": "none"}):
            self.assertEqual(
                gate_policy.reply_lint_decision("Есть два варианта, решение за вами.", False, verdict)["decision"],
                "allow",
                verdict,
            )

    def test_second_stop_passes_a_judged_hand_off(self):
        decision = gate_policy.reply_lint_decision("Your call.", True, self._verdict(True, True))
        self.assertEqual(decision["decision"], "allow")


class LaterStopDeferralTest(unittest.TestCase):
    """A reply rewritten after a Stop-hook return is returned once per turn when it still defers own work."""

    REPLY = "Готово, PR влит. Логи я ещё не читал, прочитаю следующим шагом."
    FIRST_STOP_FEEDBACK = "Stop hook feedback:\nreply-lint: the reply leaves work waiting on the owner in plain text"

    TIMED = "Логи прочитаю завтра утром."

    @staticmethod
    def _verdict(handoff=False, deferral=False, next_step=False, lever=False, timed_step=False):
        return {"handoff": handoff, "sentence": "Решение за вами.", "deferral": deferral,
                "deferral_sentence": "прочитаю следующим шагом", "next_step": next_step,
                "next_step_sentence": "прочитаю следующим шагом", "lever": lever, "lever_sentence": "",
                "timed_step": timed_step, "timed_step_sentence": "Логи прочитаю завтра утром."}

    def _later(self, verdict, next_step="", lever="", open_=True, cause_open=False, text=REPLY, timed_step=""):
        return gate_policy.reply_lint_decision(text, True, verdict, next_step, lever, timed_step=timed_step,
                                               first_cause_open=cause_open, later_deferral_open=open_)

    def test_open_budget_returns_the_deferral_family(self):
        for verdict, next_step, timed, rules in (
                (self._verdict(deferral=True), "", "", ["deferral"]),
                (self._verdict(deferral=True, next_step=True), "прочитаю", "", ["next-step"]),
                (self._verdict(timed_step=True), "", self.TIMED, ["timed-step"]),
                (self._verdict(deferral=True, timed_step=True), "", self.TIMED, ["deferral", "timed-step"])):
            decision = self._later(verdict, next_step, timed_step=timed)
            self.assertEqual((decision["decision"], decision["rules"]), ("block", rules), rules)

    def test_a_two_rule_return_carries_one_later_stop_marker_and_closes_the_budget(self):
        reason = self._later(self._verdict(deferral=True, timed_step=True), timed_step=self.TIMED)["reason"]
        self.assertEqual([sentence in reason for sentence in ("прочитаю следующим шагом", self.TIMED)], [True, True])
        self.assertFalse(gate_policy.later_deferral_open([self.FIRST_STOP_FEEDBACK, "Stop hook feedback:\n" + reason]))

    def test_closed_budget_passes(self):
        self.assertEqual(self._later(self._verdict(deferral=True), open_=False), {"decision": "allow", "rules": []})

    def test_a_handoff_verdict_suppresses_the_deferral(self):
        self.assertEqual(self._later(self._verdict(handoff=True, deferral=True)), {"decision": "allow", "rules": []})

    def test_a_lever_verdict_stays_first_stop_only(self):
        decision = self._later(self._verdict(lever=True), lever="Рычаги: флаг пропуска прогрева")
        self.assertEqual(decision, {"decision": "allow", "rules": []})

    def test_the_return_closes_the_budget_and_a_first_stop_deferral_does_not(self):
        later = self._later(self._verdict(deferral=True))["reason"]
        first = gate_policy.reply_lint_decision(self.REPLY, False, self._verdict(deferral=True))["reason"]
        self.assertFalse(gate_policy.later_deferral_open([]))
        self.assertTrue(gate_policy.later_deferral_open([self.FIRST_STOP_FEEDBACK, "Stop hook feedback:\n" + first]))
        self.assertFalse(gate_policy.later_deferral_open([self.FIRST_STOP_FEEDBACK, "Stop hook feedback:\n" + later]))

    def test_deferral_and_first_cause_return_together(self):
        decision = self._later(self._verdict(deferral=True), cause_open=True,
                               text="Отправку держали лимиты.\n\nПрочитаю логи следующим шагом.")
        self.assertEqual(decision["rules"], ["deferral", "first-cause"])

    def test_judge_runs_on_a_later_stop_only_while_the_budget_is_open(self):
        owner_reply = "Если вас устроит, прочитаю логи."
        self.assertTrue(gate_policy.reply_lint_judge_needed(owner_reply, True, True))
        self.assertFalse(gate_policy.reply_lint_judge_needed(owner_reply, True, False, next_step="прочитаю"))
        self.assertFalse(gate_policy.reply_lint_judge_needed("Таблица ниже.", True, True))
        self.assertTrue(gate_policy.reply_lint_judge_needed("Таблица ниже.", False, False, lever="Рычаги: x"))


class StopReasonRuleTest(unittest.TestCase):
    """An idle interactive first stop closes with a `Why work stopped` section naming its reason; the section is
    checked by the other rules, not trusted, and hides nothing from them."""

    SECTION = "\n\n## Why work stopped\nDone: 20 of 20 tests passed, run at 14:05."
    WITNESS_EN = ("Measured: 41 of 120 replies (34 %) carry the label, query on the replies table at 14:05.\n\n"
                  "Recounting the remaining days is due in roughly a week with no timer set; it waits until you "
                  "confirm or a later session picks it up.")
    LEFT = "The remaining migration of 12 files is left for a later session."
    PROVEN = ("Proven: the deploy failed on an expired token — `kubectl logs deploy/api`, 14:05 → `401 token expired`; "
              "explains 3 of 3")

    @staticmethod
    def _verdict(handoff=False, deferral=False, next_step=False):
        return {"handoff": handoff, "sentence": "Your call.", "deferral": deferral, "deferral_sentence": StopReasonRuleTest.LEFT,
                "next_step": next_step, "next_step_sentence": "I will run the recount next.", "lever": False,
                "lever_sentence": "", "timed_step": False, "timed_step_sentence": ""}

    def _first(self, text, verdict=None, next_step="", idle=True):
        return gate_policy.reply_lint_decision(text, False, verdict, next_step, stop_idle=idle)

    def _answering(self, text, verdict=None):
        return gate_policy.reply_lint_decision(text, True, verdict, later_deferral_open=True, stop_reason_answer=True,
                                               first_cause_open=True)

    def test_section_forms_are_found(self):
        for body, section in (("Table below.\n\n", "## Why work stopped\nDone."),
                              ("Table below.\n\n", "**Why work stopped:**\nThe owner said to stop."),
                              ("Table below.\n\n", "## Why work stopped\n**Blocked:**\nthe deploy token is refused."),
                              ("Table below.\n\n", "**Why work stopped**\n\n**Done: 20 of 20 tests passed at 14:05.**"),
                              ("Таблица ниже.\n\n", "### Почему работа остановлена\nГотово: 20 из 20 тестов прошли.")):
            with self.subTest(section=section):
                self.assertEqual(gate_policy.stop_reason_section(body + section), (body, section))

    def test_a_label_that_is_not_a_whole_line_or_has_no_reason_is_no_section(self):
        for text in ("Table below.\n\n## Why work stopped\n",
                     "Table below.\n\n> Why work stopped: done.",
                     "Table below.\n\n```\nWhy work stopped\ndone.\n```",
                     "Why work stopped early yesterday is not known yet.\n\nI will run the recount next.",
                     "- Why work stopped section is required by the rule.\n\nDone."):
            with self.subTest(text=text):
                self.assertEqual(gate_policy.stop_reason_section(text), (text, ""))

    def test_section_ends_at_a_heading_after_a_blank_line(self):
        body, section = gate_policy.stop_reason_section("A.\n\n## Why work stopped\nDone.\n\n## Appendix\nB.")
        self.assertEqual((body, section), ("A.\n\n## Appendix\nB.", "## Why work stopped\nDone.\n\n"))

    def test_section_search_is_linear_on_long_blank_and_fenced_lines(self):
        for text in ("A.\n" + " " * 40000 + "\nB.", "A.\n```\n" + "x" * 40000 + "\n```\nB."):
            started = time.monotonic()
            gate_policy.stop_reason_section(text)
            self.assertLess(time.monotonic() - started, 1.0)

    def test_idle_stop_without_the_section_is_returned_once_last(self):
        for text in (self.WITNESS_EN,
                     "Замер: 41 из 120 ответов (34 %) несут метку, запрос к таблице ответов в 14:05.\n\n"
                     "Пересчёт остальных дней ждёт примерно неделю без таймера, до подтверждения или до другой сессии."):
            with self.subTest(text=text):
                decision = self._first(text)
                self.assertEqual((decision["rules"], decision["reason"].startswith(gate_policy.STOP_REASON_MARKER)),
                                 (["stop-reason"], True))
        dirty = self._first("Отправку держали лимиты по провайдерам.")
        self.assertEqual(dirty["rules"], ["first-cause", "stop-reason"])

    def test_section_or_a_live_stop_passes(self):
        self.assertEqual(self._first(self.WITNESS_EN + self.SECTION)["decision"], "allow")
        self.assertEqual(self._first(self.WITNESS_EN, idle=False)["decision"], "allow")

    def test_a_later_stop_never_returns_stop_reason(self):
        self.assertEqual(self._answering(self.WITNESS_EN)["decision"], "allow")

    def test_the_section_is_checked_by_the_other_rules(self):
        reply = "Recount reported." + self.SECTION
        self.assertEqual(self._first(reply, self._verdict(next_step=True), "I will run the recount next.")["rules"],
                         ["next-step"])
        self.assertEqual(self._first("Report sent.\n\n## Why work stopped\n" + self.LEFT, self._verdict(deferral=True))["rules"],
                         ["deferral"])

    def test_a_section_reply_goes_to_the_judge(self):
        self.assertTrue(gate_policy.reply_lint_judge_needed("Table below.", False, False, section=True))
        self.assertFalse(gate_policy.reply_lint_judge_needed("Table below.", False, False))

    def test_closing_candidates_read_the_section_and_the_reply_without_it(self):
        timed = ("Доля посчитана.\n\n## Почему работа остановлена\nГотово: доля 34 % на 14:05. "
                 "Завтра в 09:00 перезапущу пересчёт.")
        self.assertIn("Завтра в 09:00 перезапущу пересчёт",
                      gate_policy.stop_reason_candidates(timed, gate_policy.timed_step_sentence))
        long_section = "I will run the recount for the other days next.\n\n## Why work stopped\n" + "Done. " * 120
        self.assertIn("recount for the other days",
                      gate_policy.stop_reason_candidates(long_section, gate_policy.next_step_sentence))

    BLANK_AFTER_LABEL = ("## Why work stopped\n\nDone, because the tests passed: 20 of 20 at 14:05.",
                         "**Почему работа остановлена**\n\n\nВсё сделано, потому что тесты прошли: 20 из 20, 14:05.")

    def test_first_cause_reads_the_reply_without_the_section(self):
        for section in self.BLANK_AFTER_LABEL + ("## Почему работа остановлена\nВсё сделано, потому что тесты прошли: 20 из 20, 14:05.",):
            with self.subTest(section=section):
                self.assertEqual((self._first(section)["decision"], self._answering(section)["decision"]),
                                 ("allow", "allow"))
        proven = self.PROVEN + "\n\n## Why work stopped\nDone: the token is renewed.\nUnread: none"
        self.assertEqual((self._first(proven)["decision"], self._answering(proven)["decision"]), ("allow", "allow"))
        cause_before = "Отправку держали лимиты по провайдерам.\n\n## Why work stopped\nThe owner said to stop."
        self.assertEqual(self._first(cause_before)["rules"], ["first-cause"])

    def test_the_answering_stop_runs_the_deferral_family_only(self):
        first = "Stop hook feedback:\n" + self._first(self.WITNESS_EN)["reason"]
        other = "Stop hook feedback:\nbackground-watch-gate: x"
        self.assertTrue(gate_policy.stop_reason_answer_open([first, other]))
        reply = "Report sent.\n\n## Why work stopped\n" + self.LEFT
        for verdict in (self._verdict(deferral=True), self._verdict(handoff=True, deferral=True)):
            decision = self._answering(reply, verdict)
            self.assertEqual((decision["rules"], decision["reason"].startswith(gate_policy.LATER_DEFERRAL_MARKER)),
                             (["deferral"], True))
        for plain in ("4.\n\n## Why work stopped\nDone.", "4.\n\n## Почему работа остановлена\nГотово: ответ дан."):
            with self.subTest(plain=plain):
                self.assertEqual(self._answering(plain, self._verdict())["decision"], "allow")
        spent = "Stop hook feedback:\n" + self._answering(reply, self._verdict(deferral=True))["reason"]
        self.assertFalse(gate_policy.stop_reason_answer_open([first, spent]))

    def test_payload_lists_with_entries_are_live(self):
        self.assertEqual([gate_policy.payload_live(p) for p in (
            {"background_tasks": [{"id": "b1"}]}, {"session_crons": [{"id": "c1"}]},
            {"background_tasks": [], "session_crons": []}, {})], [True, True, False, False])


class FirstCauseRuleTest(unittest.TestCase):
    """A cause stands in the first paragraph of a reply only in the full `Proven:` form (CASE-81)."""

    ROW_33 = ("Факт: 9–10 ноября отправку держали не сами лимиты и не delivery_mix. Лимиты новых до 14:54 11 ноября "
              "в сумме давали 3 500 в день, а ушло 449–1 511. Источник: `history.py logs --service gateway --since "
              "2026-11-11T14:39Z --until 2026-11-11T14:55Z` → `\"maxNewLeadsPerDayByEsp\":{\"old\":{\"gsuite\":1450,…}` "
              "(google), 1250 (other), 800 (outlook).")
    PROVEN = ("Proven: sending was held by the per-provider caps — `history.py logs --service gateway`, 15:18Z → "
              "`old 1450`; explains 3,384 of 3,384")
    UNREAD_NONE = "Unread: none\nRead: the caps' change log and the gateway code below the counter, both at 15:20Z."

    def _rules(self, text, stop_hook_active=False, first_cause_open=False):
        return gate_policy.reply_lint_decision(text, stop_hook_active, first_cause_open=first_cause_open)["rules"]

    def test_a_cause_in_the_first_paragraph_is_returned(self):
        for opening in ("Факт: 9–10 ноября отправку держали лимиты по провайдерам — `q`, 15:18Z → `x`",
                        "**Факт:** отправку сдерживали лимиты, а не delivery_mix. Источник: `q`",
                        "- **Fact**: the send failed because the pool was empty — `q`, 15:18Z → `x`",
                        "> Факт: заказ отказал из-за Ndops — `q`",
                        "Факт: держали не сами лимиты, а учёт их расхода — `q`",
                        "Факт: лимиты не менялись, держали отправку — `q`",
                        "Факт: лимиты стояли выше отправки — `q`, 15:18Z → `x`. Поэтому держали не они, а учёт.",
                        "Отправку держали лимиты, потому что так было настроено.",
                        "Сводка за день.\nФакт: отправку держали лимиты.",
                        "9–10 ноября отправку остановили дневные лимиты по провайдерам.",
                        "Факт: 9–10 ноября отправку остановила проверка дневных лимитов.",
                        "Причина та же: дневные лимиты.",
                        "The root cause was a race in the scheduler.",
                        "Неизвестно: почему отправку держали лимиты — blocked by запрет владельца",
                        "Неизвестно: что держало отправку, если её держали лимиты по провайдерам.",
                        "Что держало отправку 9–11 ноября, всё ещё не установлено.",
                        "Я проверил логи, лимиты держали отправку.",
                        "Я откатил PR #12, потому что тесты красные.",
                        "Мы стояли час из-за пустого пула.",
                        "Факт: отправка шла — `history.py logs, а лимиты держали её до 14:54."):
            self.assertEqual(self._rules(opening + "\n\nДальше текст."), ["first-cause"], opening)

    def test_openings_without_an_asserted_cause_pass(self):
        for opening in (self.ROW_33,
                        "Факт: лимиты отправку не держали — `q`, 15:18Z → `x`",
                        "Факт: файл содержал 40 строк, а клиенты поддержали формат — `q`",
                        "Факт: отправка остановилась в 14:00, проверка останавливалась дважды — `q`, 14:05Z → `x`",
                        "Fact: sending stopped at 14:00, v2.3 was released at 14:02 — `q`",
                        "Fact: the merge is blocked by a red CI run; the pool is capped at 20; one bug is open — `q`",
                        "Факт: лимиты стояли выше отправки — `q`, 15:18Z → `\"reason\": \"held because quota\"`",
                        "Fact: `maxPerDay` was 2900 for google, `held because of quota` — `q`",
                        "Я остановил раскатку в 14:00.",
                        "Фоновую проверку я остановил, она больше не нужна.",
                        "Скрипт освободил 40 ГБ на диске.",
                        "Причину ещё не нашёл; termination reason is OOMKilled.",
                        "Неизвестно: что именно держало отправку 9–10 ноября.",
                        "Неизвестно: Что держало отправку 9–11 ноября, всё ещё не установлено.",
                        "Unknown: what caused the stop on 10 November — blocked by `permission denied`",
                        "Неизвестно: счётчики escrow по ws2300 — blocked by «чтение отклонено из-за отсутствия роли»",
                        "Fact: шлюз ответил 429 — `curl -s gw/status`, 12:04 → \"reason\": \"held because quota\"",
                        "Fact: the pool is empty — `q`, 12:04 → “drained due to a restart”",
                        "Хук остановил запуск в 14:00.",
                        "Джоб урезал выборку до 100 строк.",
                        "Cron остановил проверку в 03:00.",
                        "Команда освободила 40 ГБ.",
                        "Подтверждено: отправка шла.\n\nОтправку держали лимиты, потому что так было настроено."):
            self.assertEqual(self._rules(opening), [], opening)

    def test_a_cause_word_in_a_clause_that_states_no_event_passes(self):
        for opening in ("Ревьюер нашёл три проблемы, из-за которых прод упал бы сразу после выкатки.",
                        "Пока не знаю, будет ли тормозить Inbox из-за кастомных лейблов.",
                        "Перекрёстная проверка нашла один дефект, из-за которого мержить нельзя.",
                        "Builder вы остановили, изменений он не внёс.",
                        "Токены остаются прежними, потому что ты выбрал креды не менять.",
                        "Я пока не знаю, что держало отправку 9–10 ноября.",
                        "Пока не знаю, почему встала отправка."):
            self.assertEqual(self._rules(opening + "\n\nДальше текст."), [], opening)

    def test_an_asserted_cause_beside_such_a_clause_is_still_returned(self):
        for opening in ("Отправку держал лимит, проверю, вырос ли он.",
                        "Отправка встала, потому что я не перезапустил воркер.",
                        "Пока не знаю, почему встала отправка, но держали её лимиты.",
                        "Неизвестно: почему отправку держали лимиты — blocked by запрет владельца",
                        "The deploy failed because the pod would not start.",
                        "Из-за лимита Google письма отправить нельзя.",
                        "Sending stalled because, as you know, the quota ran out."):
            self.assertEqual(self._rules(opening + "\n\nДальше текст."), ["first-cause"], opening)

    def test_proven_form_ends_at_the_first_explains_after_the_arrow(self):
        for line in ("Доказано: сборку ускорил пропуск тестов — `gh run view 1 --log`, 18:50 → `skip: tests passed`; "
                     "объясняет 228 из 232 секунд разницы в шаге сборки. Рычага не было; от вас ничего не нужно.",
                     "Proven: Overview hides today — code and recount q8 at 17:23 → «not read per slice», «27+181=208»; "
                     "explains 935 of 936. I am changing it now."):
            self.assertEqual(self._rules(line + "\n" + self.UNREAD_NONE), [], line)
        decision = gate_policy.reply_lint_decision(
            "Proven: the caps held sending — `q`, 15:18Z → read «x»; explains 3 of 3. More text.\n" + self.UNREAD_NONE,
            False)
        self.assertIn("lacks its full form", decision["reason"])

    def test_full_proven_forms_pass(self):
        for line in (self.PROVEN,
                     "Доказано: отправку держали лимиты — `q`, 15:18Z → «3 384 писем»; объясняет 3 384 письма из 3 384",
                     "Proven: the caps held sending — history.py logs, 2026-11-12T15:18Z → \"old 1450\"; "
                     "explains 3,384 emails of 3,384",
                     "Proven: the caps held sending — `q`, 15:18Z → “old 1450”; explains 3 of 3 emails",
                     "Доказано: отправку держали лимиты — `q`, 15:18Z → «3 384» «писем»; объясняет 3 из 3"):
            self.assertEqual(self._rules(line + "\nОтправку держали лимиты.\n\nДальше.\n" + self.UNREAD_NONE), [], line)

    def test_incomplete_proven_forms_are_returned(self):
        for line in ("Proven: sending was held by the caps — `q`, 15:18Z → `x`; explains all of it",
                     "Доказано: отправку держали лимиты; объясняет 3 из 3",
                     "Доказано: отправку держали лимиты — `q`, 15:18Z → `x`; объясняет 5 из 3",
                     "Proven: the caps held sending — `q`, 15:18Z → `x`; explains 0 of 3,384",
                     "Proven: the caps held sending — `q`, 11 November → `x`; explains 3 of 3",
                     "Proven: the caps held sending — `q`, 15:18Z → x; explains 3 of 3",
                     "Proven: the caps held sending — 15:18Z → `x`; explains 3 of 3",
                     "Proven: the caps held sending — `q`, 15:18Z → `x; explains 3 of 3"):
            decision = gate_policy.reply_lint_decision(line + "\n\n" + self.UNREAD_NONE, False)
            self.assertEqual(decision["rules"], ["first-cause"], line)
            self.assertIn("lacks its full form", decision["reason"], line)

    def test_a_proven_opening_without_unread_none_is_returned_for_its_unread_records(self):
        for tail in ("",
                     "\n\nUnread: the vendor's public record of the caps — one search by the error string.",
                     "\n\nUnread:\n- the vendor's public record of the caps",
                     "\n\n**Unread — what is not read yet**\n- the gateway code below the counter",
                     "\n\n| ESP | count |\n- unread: 340",
                     "\n\nThe caps' log shows unread: none of the alerts."):
            for stop_hook_active in (False, True):
                decision = gate_policy.reply_lint_decision(self.PROVEN + tail, stop_hook_active, first_cause_open=True)
                self.assertEqual(decision["rules"], ["first-cause"], tail)
                self.assertIn("carries a line `Unread:`", decision["reason"], tail)

    def test_a_line_holding_only_unread_and_the_word_for_nothing_clears_a_full_proven_opening(self):
        for line in ("Unread: none", "Unread: нет", "Unread: ничего.", "UNREAD: Нет", "- **Unread:** none",
                     "**Unread**: `none`", "`Unread: none`", "1. Unread: ничего"):
            text = self.PROVEN + "\n\n" + line + "\nRead: the change log and the gateway code, at 15:20Z."
            self.assertEqual(self._rules(text), [], line)

    def test_unread_none_followed_by_a_record_on_its_line_is_returned_for_its_unread_records(self):
        for line in ("Unread: none except the vendor tracker",
                     "Unread: none, кроме страницы статуса вендора.",
                     "Unread: нет доступа к журналу вендора",
                     "Unread: none — the change log read at 15:20Z; the gateway code is not read.",
                     "Unread: none yet checked"):
            decision = gate_policy.reply_lint_decision(self.PROVEN + "\n\n" + line, False)
            self.assertEqual(decision["rules"], ["first-cause"], line)
            self.assertIn("carries a line `Unread:`", decision["reason"], line)

    def test_unread_none_quoted_in_a_fence_or_blockquote_does_not_clear(self):
        for tail in ("```\nUnread: none\n```", "> Unread: none"):
            decision = gate_policy.reply_lint_decision(self.PROVEN + "\n\n" + tail, False)
            self.assertIn("carries a line `Unread:`", decision["reason"], tail)

    def test_an_incomplete_proven_opening_without_unread_none_is_returned_for_its_unread_records_first(self):
        reason = gate_policy.reply_lint_decision("Proven: the caps held sending; explains 3 of 3", False)["reason"]
        self.assertIn("carries a line `Unread:`", reason)
        self.assertNotIn("lacks its full form", reason)

    def test_a_demoted_cause_with_an_unread_list_is_outside_the_unread_return(self):
        for opening in ("Fact: 3,384 emails were not sent on 10 November — `q`, 15:18Z → `3384`",
                        "Unknown: the caps' change log — blocked by `permission denied`"):
            text = opening + "\n\nUnread: the vendor's public record — one search.\n\nHypothesis: the caps held it."
            self.assertEqual(self._rules(text), [], opening)

    def test_every_first_cause_reason_carries_the_marker_a_later_stop_looks_for(self):
        for text, reason_part in (("Отправку держали лимиты.", "labelled hypothesis"),
                                  ("Proven: лимиты; explains 3 of 3", "carries a line `Unread:`"),
                                  ("Proven: лимиты; explains 3 of 3\n\nUnread: нет", "lacks its full form"),
                                  ("Unknown: почему лимиты срезали отправку.", "a why-question carries its own premise")):
            reason = gate_policy.reply_lint_decision(text, False)["reason"]
            self.assertIn(reason_part, reason, text)
            self.assertTrue(reason.startswith(gate_policy.FIRST_CAUSE_MARKER), text)

    def test_unknown_line_with_a_why_question_is_returned_with_its_own_reason(self):
        for opening in ("Unknown: почему лимиты по провайдерам срезали отправку 9–11 ноября, хотя письма расходовали "
                        "лишь 2–19 % каждого лимита.",
                        "Неизвестно: 1,511 и 449 писем 9–10 ноября. Отчего квота кончалась, решает чтение escrow.",
                        "**Неизвестно:** из-за чего кампании упирались в лимиты — blocked by `refused`",
                        "`Unknown:` почему 9–11 ноября лимиты отпускали меньше писем, чем в них было заложено.",
                        "Unknown: why the caps fired at 2 % use — blocked by `permission denied`"):
            decision = gate_policy.reply_lint_decision(opening + "\n\nДальше текст.", False)
            self.assertEqual(decision["rules"], ["first-cause"], opening)
            self.assertIn("a why-question carries its own premise", decision["reason"], opening)

    def test_why_word_passes_when_quoted_or_outside_an_unknown_paragraph(self):
        for opening in ("Unknown: счётчики escrow за 9–11 ноября — blocked by «почему отказано: нет роли»",
                        "Unknown: what caps sending at 2 % use — blocked by `permission denied`",
                        "Unknown: счётчики escrow — blocked by `refused`.\n\nПочему лимиты срезали отправку, неизвестно.",
                        "Факт: письма расходовали 2–19 % каждого лимита; почему, решает чтение escrow."):
            self.assertEqual(self._rules(opening), [], opening)

    def test_later_stop_runs_this_rule_only_while_it_has_not_returned_this_turn(self):
        dirty = "Готово. Отправку держали лимиты."
        self.assertEqual(self._rules(dirty, stop_hook_active=True), [])
        self.assertEqual(self._rules(dirty, stop_hook_active=True, first_cause_open=True), ["first-cause"])

    def test_first_cause_is_open_only_after_another_return_of_this_turn(self):
        returned = gate_policy.reply_lint_decision("Отправку держали лимиты.", False)["reason"]
        self.assertFalse(gate_policy.first_cause_open([]))
        self.assertTrue(gate_policy.first_cause_open(["Stop hook feedback:\nreply-lint: the reply leaves work"]))
        self.assertTrue(gate_policy.first_cause_open(["Stop hook feedback:\nx", "Stop hook feedback:\n" + returned]))
        self.assertFalse(gate_policy.first_cause_open(["Stop hook feedback:\n" + returned] * 2))

    COUNT_OPENING = ("За 14 дней в интерактивных сессиях было 1700 возвратов стоп-хуков, и примерно половина–две "
                     "трети из них шум: около 870–1110 остановили агента зря. Почти весь шум дают три хука. От вас "
                     "ничего не нужно.")
    NO_CAUSE_NOTE = "(В этом абзаце нет утверждения о причине: это подсчёт возвратов и их распределение по хукам.)"

    def test_a_count_as_the_subject_of_a_cause_verb_is_a_distribution_not_a_cause(self):
        self.assertEqual(self._rules(self.COUNT_OPENING + "\n\n| Хук | Возвратов |"), [])
        self.assertEqual(self._rules("Три пода остановили отправку."), ["first-cause"])

    def test_a_line_saying_the_paragraph_names_no_cause_does_not_clear_a_cause_opening(self):
        dirty = "Отправку держали лимиты по провайдерам.\n\n" + self.NO_CAUSE_NOTE
        self.assertEqual(self._rules(dirty), ["first-cause"])
        self.assertEqual(self._rules(dirty, stop_hook_active=True, first_cause_open=True), ["first-cause"])
        clean = self.COUNT_OPENING + "\n\n" + self.NO_CAUSE_NOTE + "\n\n| Хук | Возвратов |"
        self.assertEqual(self._rules(clean, stop_hook_active=True, first_cause_open=True), [])

    def test_merged_return_names_the_rule_without_the_word_and_one_next_opening(self):
        decision = gate_policy.reply_lint_decision("Факт: отправку держали лимиты. Готово.", False)
        self.assertEqual(set(decision["rules"]), {"claim", "first-cause"})
        self.assertNotIn("держали", decision["reason"])
        self.assertEqual(decision["reason"].count("The last message you send in this turn is the whole reply"), 1)
        self.assertTrue(decision["reason"].endswith('"append only the missing part" in any other part of this return.'))
        for exit_form in ("labelled hypothesis", "`Unknown:", "`Proven:"):
            self.assertIn(exit_form, decision["reason"])


class FirstCauseHookProcessTest(unittest.TestCase):
    """The reply-lint hook process returns first-cause at most twice per turn (CASE-81): a later stop is checked
    only while fewer than two returns of this turn carry the first-cause marker."""

    HOOK = os.path.join(os.path.dirname(os.path.realpath(__file__)), "..", "..", "hooks", "reply-lint.py")
    DIRTY = "Отправку держали лимиты по провайдерам."

    def setUp(self):
        self.home = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.home)
        self.env = {k: v for k, v in os.environ.items() if k != "REPLY_LINT_JUDGE"}
        self.env.update(HOME=self.home, PATH="/usr/bin:/bin")  # no `claude` binary: a judge call would fail open

    def _transcript(self, *returns):
        entries = [{"type": "user", "message": {"role": "user", "content": "Почему встала отправка?"}},
                   {"type": "assistant", "message": {"content": [{"type": "text", "text": self.DIRTY}]}}]
        entries += [{"type": "user", "isMeta": True, "message": {"role": "user", "content": "Stop hook feedback:\n" + r}}
                    for r in returns]
        path = os.path.join(self.home, "t.jsonl")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("".join(json.dumps(e) + "\n" for e in entries))
        return path

    def _reason(self, path, stop_hook_active, message=DIRTY):
        payload = {"session_id": "s", "transcript_path": path, "stop_hook_active": stop_hook_active,
                   "last_assistant_message": message}
        out = subprocess.run([sys.executable, self.HOOK], input=json.dumps(payload), stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, universal_newlines=True, env=self.env, timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)
        return json.loads(out.stdout)["reason"] if out.stdout.strip() else None

    def test_first_stop_returns_a_dirty_opening(self):
        self.assertTrue(self._reason(self._transcript(), False).startswith(gate_policy.FIRST_CAUSE_MARKER))

    def test_later_stop_after_another_rules_return_returns_it(self):
        path = self._transcript("background-watch-gate: the turn ends with a background task open")
        self.assertTrue(self._reason(path, True).startswith(gate_policy.FIRST_CAUSE_MARKER))

    def test_second_stop_still_dirty_is_returned_again(self):
        first = gate_policy.reply_lint_decision(self.DIRTY, False)["reason"]
        self.assertTrue(self._reason(self._transcript(first, "open-question: x"), True)
                        .startswith(gate_policy.FIRST_CAUSE_MARKER))

    def test_third_stop_after_two_first_cause_returns_passes(self):
        first = gate_policy.reply_lint_decision(self.DIRTY, False)["reason"]
        self.assertIsNone(self._reason(self._transcript(first, first), True))

    def test_second_stop_clean_passes(self):
        first = gate_policy.reply_lint_decision(self.DIRTY, False)["reason"]
        self.assertIsNone(self._reason(self._transcript(first), True, "Факт: 1,511 и 449 писем 9–10 ноября."))

    def test_second_stop_with_a_dirty_opening_and_a_no_cause_line_is_returned(self):
        first = gate_policy.reply_lint_decision(self.DIRTY, False)["reason"]
        for reply in (self.DIRTY + "\n\n(В этом абзаце нет утверждения о причине: это подсчёт.)",
                      self.DIRTY + "\n\n(this paragraph names no cause)"):
            with self.subTest(reply=reply):
                self.assertTrue(self._reason(self._transcript(first), True, reply)
                                .startswith(gate_policy.FIRST_CAUSE_MARKER))

    def test_later_stop_with_an_unreadable_transcript_passes(self):
        path = self._transcript("background-watch-gate: x")
        os.chmod(path, 0)
        self.addCleanup(os.chmod, path, 0o600)
        if os.access(path, os.R_OK):
            self.skipTest("the file stays readable for this user (root)")
        self.assertIsNone(self._reason(path, True))


class StopReasonHookProcessTest(unittest.TestCase):
    """The reply-lint hook process returns an idle first stop of the terminal client without the stop-reason
    section, and no stop where something of the session is live or the entrypoint is any other."""

    HOOK = FirstCauseHookProcessTest.HOOK
    REPLY = ("Measured: 41 of 120 replies (34 %) carry the label, query on the replies table at 14:05.\n\n"
             "The recount over the other days runs in about a week, not on a timer.")
    SECTION = "\n\n## Why work stopped\nDone: the owner asked for the share only."
    NO_VERDICT = {"handoff": False, "sentence": "", "deferral": False, "deferral_sentence": "", "next_step": False,
                  "next_step_sentence": ""}

    def setUp(self):
        self.home = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.home)
        os.makedirs(os.path.join(self.home, ".claude"))
        os.makedirs(os.path.join(self.home, "bin"))
        self.env = {k: v for k, v in os.environ.items() if k not in ("REPLY_LINT_JUDGE", "CLAUDE_CODE_ENTRYPOINT")}
        # no `claude` binary unless a test writes the fake judge: a judge call fails open
        self.env.update(HOME=self.home, PATH=os.path.join(self.home, "bin") + ":/usr/bin:/bin")

    def _judge(self, verdict):
        """A fake `claude` on PATH that answers every judge call with ``verdict``."""
        answer = os.path.join(self.home, "verdict.json")
        with open(answer, "w", encoding="utf-8") as fh:
            json.dump({"result": json.dumps(verdict)}, fh)
        script = os.path.join(self.home, "bin", "claude")
        with open(script, "w", encoding="utf-8") as fh:
            fh.write("#!/bin/sh\ncat >/dev/null\ncat '%s'\n" % answer)
        os.chmod(script, 0o755)

    @staticmethod
    def _use(use_id, name, args, **extra):
        return dict({"type": "assistant", "message": {"content": [{"type": "tool_use", "id": use_id, "name": name, "input": args}]}}, **extra)

    @staticmethod
    def _result(use_id, text, **extra):
        return dict({"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": use_id, "content": text}]}}, **extra)

    def _run(self, work=(), message=REPLY, entrypoint="cli", returns=(), stamp=None, **payload_extra):
        """(reason or None, the hook's log record) for a stop; ``returns`` are this turn's earlier Stop-hook
        returns, which make it a later stop."""
        entries = [{"type": "user", "message": {"role": "user", "content": "What share carries the label?"}}]
        entries += list(work)
        for reason in returns:
            entries += [{"type": "assistant", "message": {"content": [{"type": "text", "text": "Report sent."}]}},
                        {"type": "user", "isMeta": True, "message": {"role": "user", "content": "Stop hook feedback:\n" + reason}}]
        entries.append(dict({"type": "assistant", "message": {"content": [{"type": "text", "text": message}]}},
                            **({"timestamp": stamp} if stamp else {})))
        path = os.path.join(self.home, "t.jsonl")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("".join(json.dumps(e) + "\n" for e in entries))
        payload = dict({"session_id": "s", "transcript_path": path, "stop_hook_active": bool(returns),
                        "last_assistant_message": message}, **payload_extra)
        env = dict(self.env, **({"CLAUDE_CODE_ENTRYPOINT": entrypoint} if entrypoint is not None else {}))
        out = subprocess.run([sys.executable, self.HOOK], input=json.dumps(payload), stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, universal_newlines=True, env=env, timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)
        with open(os.path.join(self.home, ".claude", "reply-lint-log.jsonl"), encoding="utf-8") as fh:
            record = json.loads(fh.read().splitlines()[-1])
        return (json.loads(out.stdout)["reason"] if out.stdout.strip() else None), record

    def test_idle_terminal_stop_without_the_section_is_returned(self):
        reason, record = self._run()
        self.assertEqual((reason.startswith(gate_policy.STOP_REASON_MARKER), record["idle"], record["outside_terminal_client"]),
                         (True, True, False))

    def test_every_other_entrypoint_is_exempt(self):
        for entrypoint in (None, "sdk-cli", "sdk-ts", "claude-code-github-action", "claude-vscode"):
            with self.subTest(entrypoint=entrypoint):
                reason, record = self._run(entrypoint=entrypoint)
                self.assertEqual((reason, record["outside_terminal_client"], record["idle"]), (None, True, None))
        section_reply = "Share: 34 %.\n\n## Why work stopped\nThe question is answered."  # judged under `cli`
        self.assertIsNone(self._run(message=section_reply, entrypoint="sdk-cli")[1]["judge_ms"])

    def test_only_a_whole_label_line_with_a_reason_is_the_section(self):
        for message, returned in ((self.REPLY + self.SECTION, False),
                                  (self.REPLY + "\n\n## Why work stopped\n**Blocked:**\nthe deploy token is refused.", False),
                                  ("Why work stopped early yesterday is not known yet.\n\nI will run the recount next.", True)):
            with self.subTest(message=message):
                reason = self._run(message=message)[0]
                self.assertEqual(bool(reason) and reason.startswith(gate_policy.STOP_REASON_MARKER), returned)

    def test_live_work_exempts(self):
        cases = {
            "monitor": [self._use("m1", "Monitor", {"command": "tail -F log"}),
                        self._result("m1", "Monitor started (task mx1, expires in 30m)")],
            "agent": [self._use("g1", "Agent", {"prompt": "build", "run_in_background": True}),
                      self._result("g1", "Async agent launched successfully.\nagentId: ag1 (internal)")],
            "pending agent call": [self._use("g2", "Agent", {"prompt": "build"})],
            "one-shot cron": [self._use("c1", "CronCreate", {"cron": "37 19 29 10 *", "recurring": False, "prompt": "measure"}),
                              self._result("c1", "Scheduled one-shot task 1db1ccfe (37 19 29 10 *). Next run: Oct 29 7:37pm.")],
            "recurring cron": [self._use("c2", "CronCreate", {"cron": "23 * * * *", "recurring": True, "prompt": "tick"}),
                               self._result("c2", "Scheduled recurring job 5e0a11c3 (Every hour at :23).")],
            "pending wakeup": [self._use("w1", "ScheduleWakeup", {"delaySeconds": 1800}, timestamp="2026-11-05T10:28:58.100Z"),
                               self._result("w1", "Next wakeup scheduled for 13:00:00 (in 1859s).",
                                            timestamp="2026-11-05T10:29:00.200Z")],
        }
        for name, work in cases.items():
            with self.subTest(name):
                reason, record = self._run(work, stamp="2026-11-05T10:30:00.000Z")
                self.assertEqual((reason, record["idle"]), (None, False))
        for listed in ({"background_tasks": [{"id": "bx1"}]}, {"session_crons": [{"id": "c1"}]}):
            with self.subTest(listed):
                reason, record = self._run(**listed)
                self.assertEqual((reason, record["idle"]), (None, None))

    def test_closing_candidates_read_the_whole_reply_and_the_reply_without_the_section(self):
        timed = ("Доля посчитана.\n\n## Почему работа остановлена\nГотово: доля 34 % на 14:05. "
                 "Завтра в 09:00 перезапущу пересчёт.")
        self.assertIn("Завтра в 09:00 перезапущу пересчёт", self._run(message=timed)[1]["timed_step_candidate"])
        after = ("Share reported.\n\n## Why work stopped\nThe owner asked for the share only.\n\n"
                 "I will run the recount for the other days next.")
        self.assertIn("recount for the other days", self._run(message=after)[1]["next_step_candidate"])

    def test_first_cause_reads_past_the_section(self):
        proven = ("Proven: the deploy failed on an expired token — `kubectl logs deploy/api`, 14:05 → "
                  "`401 token expired`; explains 3 of 3\n\n## Why work stopped\nDone: the token is renewed.\nUnread: none")
        self.assertIsNone(self._run(message=proven)[0])
        before = "Отправку держали лимиты по провайдерам.\n\n## Why work stopped\nThe owner said to stop."
        self.assertTrue(self._run(message=before)[0].startswith(gate_policy.FIRST_CAUSE_MARKER))
        first = gate_policy.reply_lint_decision(self.REPLY, False, stop_idle=True)["reason"]
        for message in StopReasonRuleTest.BLANK_AFTER_LABEL:
            for returns in ((), (first,)):
                with self.subTest(message=message, answering=bool(returns)):
                    self.assertIsNone(self._run(message=message, returns=returns)[0])

    def test_a_section_reply_is_judged(self):
        self._judge(self.NO_VERDICT)
        message = "Share: 34 %.\n\n## Why work stopped\nThe question is answered."
        reason, record = self._run(message=message)
        self.assertEqual((reason, record["next_step_candidate"], record["timed_step_candidate"], record["verdict"] is None),
                         (None, "", "", False))

    def test_the_answering_stop_returns_a_deferral_despite_a_handoff_verdict(self):
        self._judge(dict(self.NO_VERDICT, handoff=True, sentence="Your call.", deferral=True,
                         deferral_sentence="The rest is left for a later session."))
        first = gate_policy.reply_lint_decision(self.REPLY, False, stop_idle=True)["reason"]
        reason, _ = self._run(message="Report sent.\n\n## Why work stopped\nThe rest is left for a later session.",
                              returns=[first])
        self.assertTrue(reason.startswith(gate_policy.LATER_DEFERRAL_MARKER))


class HandoffJudgeTest(unittest.TestCase):
    def test_reply_that_addresses_the_owner_goes_to_the_judge(self):
        for text in (
            "Решение за вами.",
            "Жду твоего ответа.",
            "Нужно согласие владельца.",
            "Your call.",
            "Waiting on the owner.",
            "Берём A или B?",
            "Баунсы больше 100% не трогаю, пока не скажете.",
            "Начинаю по команде «го».",
            "Скажите, если делать.",
            "Жду подтверждения перезапуска базы.",
            "Напиши — запущу проверку.",
            "Ship it once approved; let me know.",
            "Если подходит, сделаю так.",
            "If that works for you, I'll add it.",
        ):
            self.assertTrue(gate_policy.addresses_owner(text), text)

    def test_reply_that_names_no_owner_skips_the_judge(self):
        for text in (
            "Вывод: сборка идёт, тесты зелёные.",
            "The layout is fixed; CI runs.",
            "Если нормально отработает, открою PR.",
        ):
            self.assertFalse(gate_policy.addresses_owner(text), text)

    def test_first_person_commitment_goes_to_the_judge(self):
        for text in ("Потом поправлю тесты.", "Если нормально отработает, открою PR.", "I'll fix it in a follow-up PR."):
            self.assertTrue(gate_policy.needs_judge(text), text)

    def test_reply_with_no_owner_and_no_commitment_skips_the_judge(self):
        for text in ("Вывод: сборка идёт, тесты зелёные.", "The layout is fixed; CI runs."):
            self.assertFalse(gate_policy.needs_judge(text), text)

    @staticmethod
    def _cli(result, is_error=False):
        return json.dumps({"type": "result", "is_error": is_error, "result": result})

    def test_reply_reaches_the_prompt_verbatim(self):
        reply = 'Pick {a} or "b"?\nЖду ответа.'
        prompt = gate_policy.judge_prompt(reply)
        self.assertIn(json.dumps(reply, ensure_ascii=False), prompt)

    def test_verdict_object_is_parsed(self):
        for result in (
            '{"sentence": "Your call.", "handoff": true, "deferral_sentence": "none", "deferral": false, '
            '"next_step_sentence": "none", "next_step": false}',
            '```json\n{"sentence": "Your call.", "handoff": true, "deferral_sentence": "none", "deferral": false, '
            '"next_step_sentence": "none", "next_step": false}\n```',
        ):
            self.assertEqual(
                gate_policy.parse_judge_verdict(self._cli(result)),
                {"handoff": True, "sentence": "Your call.", "deferral": False, "deferral_sentence": "none",
                 "next_step": False, "next_step_sentence": "none", "lever": False, "lever_sentence": "",
                 "timed_step": False, "timed_step_sentence": "", "duration": False, "duration_sentence": ""},
                result,
            )

    def test_duration_answer_is_parsed(self):
        result = ('{"sentence": "none", "handoff": false, "deferral": false, "next_step": false, '
                  '"duration_sentence": "Займёт 2 недели.", "duration": true}')
        verdict = gate_policy.parse_judge_verdict(self._cli(result))
        self.assertEqual((verdict["duration"], verdict["duration_sentence"]), (True, "Займёт 2 недели."))

    def test_anything_but_one_verdict_object_is_no_verdict(self):
        for stdout in (
            "",
            "not json",
            self._cli('{"sentence": "x", "handoff": true}', is_error=True),
            self._cli('Verdict: {"sentence": "x", "handoff": true}'),
            self._cli('{"sentence": "x", "handoff": "false"}'),
            self._cli('{"sentence": "x", "handoff": true}'),
            self._cli('{"sentence": "x", "handoff": true, "deferral": "false"}'),
            self._cli('{"sentence": "x", "handoff": true, "deferral": false}'),
            self._cli('{"sentence": "x"} {"handoff": true}'),
        ):
            self.assertIsNone(gate_policy.parse_judge_verdict(stdout), stdout)


class BlankQuotedTest(unittest.TestCase):
    def test_low_double_quote_closes_on_high_left_quote(self):
        self.assertEqual(gate_policy._blank_quoted("a \u201ex\u201c b \u201cy\u201d c"), "a     b     c")


class BriefDecisionTest(unittest.TestCase):
    def test_builder_without_files_or_check_is_denied(self):
        decision = gate_policy.brief_decision("orch:builder", "task: fix it\nworkspace: /x")
        self.assertEqual(decision["decision"], "deny")
        self.assertIn("`files:`", decision["reason"])
        self.assertIn("`check:`", decision["reason"])

    def test_builder_with_lines_is_allowed(self):
        prompt = "task: fix it\nfiles: a.py:10, b.py:3\ncheck: cargo test -p x\nparts: 30 min; whole; mine: whole"
        self.assertEqual(gate_policy.brief_decision("builder", prompt)["decision"], "allow")

    def test_probe_without_access_is_denied(self):
        self.assertEqual(gate_policy.brief_decision("prod-probe", "Measure x")["decision"], "deny")

    def test_other_types_are_not_gated(self):
        self.assertEqual(gate_policy.brief_decision("scout", "where is x")["decision"], "allow")


class RedactSecretsTest(unittest.TestCase):
    def test_named_values_bearer_jwt_and_k8s_secret_are_masked(self):
        text = (
            "DB_PASSWORD=hunter2secret\nAuthorization: Bearer abcdefghij123\n"
            "eyJ" + "fakehdr" * 2 + "." + "fakebody" * 2 + "." + "fakesig" * 2 + "\n"
            "kind: Secret\ndata:\n  password: aHVudGVyMnNlY3JldA==\n"
        )
        out = gate_policy.redact_secrets(text)
        for secret in ("hunter2secret", "abcdefghij123", "fakehdrfakehdr", "aHVudGVyMnNlY3JldA=="):
            self.assertNotIn(secret, out)
        self.assertIn("Bearer <redacted sha256:", out)

    def test_yaml_keys_and_code_stay(self):
        # A key on its own line and a Python assignment are not secret values
        # (CI workflow `secrets:` job and gate_policy itself were masked, 2026-10-12).
        text = "secrets:\n    runs-on: ubuntu-latest\n_SECRET_RE = re.compile(pattern)"
        self.assertEqual(gate_policy.redact_secrets(text), text)

    def test_counts_and_short_values_stay(self):
        text = "tokens: 15000\ntoken: null\nimage: nginx:1.27\nsubagent_tokens 1234"
        self.assertEqual(gate_policy.redact_secrets(text), text)


class OverrideGatesTest(unittest.TestCase):
    BRIEF = "files: a\ncheck: b\nparts: 30 min; whole; mine: whole\n"

    def test_main_marker_turns_a_refused_spawn_into_an_override(self):
        self.assertEqual(
            gate_policy.spawn_gate_decision(
                "", "Plan", "", "AGENT_TYPE_OVERRIDE: second opinion on the design"
            ),
            {"decision": "override", "reason": "second opinion on the design"},
        )

    def test_subagent_marker_is_refused(self):
        decision = gate_policy.spawn_gate_decision(
            "builder", "reviewer", "", self.BRIEF + "AGENT_TYPE_OVERRIDE: second opinion on the design"
        )
        self.assertEqual(decision["decision"], "deny")
        self.assertIn("the main session spawns reviewers; return the request to main", decision["reason"])

    def test_main_marker_on_an_allowed_spawn_stays_a_plain_allow(self):
        self.assertEqual(
            gate_policy.spawn_gate_decision(
                "", "builder", "", self.BRIEF + "AGENT_TYPE_OVERRIDE: second opinion on the design"
            ),
            {"decision": "allow"},
        )

    def test_subagent_background_with_marker_line_is_an_override(self):
        self.assertEqual(
            gate_policy.poll_decision("make test\n# NO_POLL_OVERRIDE: waiting on the deploy", True, "builder"),
            {"decision": "override", "reason": "waiting on the deploy"},
        )

    def test_subagent_background_with_trailing_marker_is_refused(self):
        self.assertEqual(
            gate_policy.poll_decision("make test # NO_POLL_OVERRIDE: waiting on the deploy", True, "builder")["decision"],
            "deny",
        )
_PLAN = (
    "# scope: orders table, rows of tenant acme\n"
    "# undo: restore from tmp/orders-acme.csv\n"
    "# irreversible: the delete trigger notifies the billing service\n"
    "# count: 120 rows\n"
    "# contradiction: none\n"
    "# prod_position: rows deleted, acme has 120 of 9.1M, SELECT count(*) 10:02Z\n"
    "# way_back: COPY from tmp/orders-acme.csv, 2 min\n"
    "# effect_read: dry run on a scratch copy: DELETE 120, other tenants' rows unchanged\n"
    "psql -c \"DELETE FROM orders WHERE tenant = 'acme'\"\n"
)
_PLAN_HASH = "a" * 64
_PLAN_CMD = "bash /tmp/plan.sh  # DESTRUCTIVE_PLAN sha256=" + _PLAN_HASH


def _destructive(command, agent_type="", plan_text=None, plan_hash=None):
    return gate_policy.destructive_decision(command, agent_type, plan_text, plan_hash)


_PIN_HASH = "f" * 64
_PIN_LINE = "# pin: /repo/run.sh sha256=" + _PIN_HASH + "\n"
# The headers of _PLAN without its psql line: the head of every pinned plan below (lines 1-8, pin on 9).
_PINNED_HEAD = _PLAN[:_PLAN.index("psql")] + _PIN_LINE
_PINNED_PLAN = _PINNED_HEAD + "bash /repo/run.sh --apply 5\n"
_PIN_FACTS = {"real_path": "/repo/run.sh", "sha256": _PIN_HASH, "file_blob": "b" * 40, "tree_mode": "100644",
              "tree_blob": "b" * 40, "origin_url": "git@github.com:o/r.git", "tip": "c" * 40,
              "tip_date": "2026-11-17T10:00:00+00:00", "branch": "origin/main", "text": "echo apply\n"}


def _pinned(plan_text, facts=_PIN_FACTS, mode="default", approved=False):
    return gate_policy.destructive_decision(_PLAN_CMD, "", plan_text, _PLAN_HASH, None, mode, None, approved, None,
                                            {"/repo/run.sh": facts, "/repo/b.sh": dict(_PIN_FACTS, real_path="/repo/b.sh")})


class DestructiveDecisionTest(unittest.TestCase):
    def test_rm_of_scratch_path_is_allowed(self):
        self.assertEqual(_destructive("rm -rf tmp/build /tmp/x")["decision"], "allow")

    def test_rm_outside_ours_is_denied(self):
        self.assertEqual(
            {k: _destructive("rm -rf tmp/x ~/Documents")[k] for k in ("decision", "class")},
            {"decision": "deny", "class": "impossible"},
        )

    def test_rm_climbing_out_of_scratch_is_denied(self):
        self.assertEqual(_destructive("rm -rf tmp/../../src")["decision"], "deny")

    def test_kubectl_delete_by_session_label_is_allowed(self):
        self.assertEqual(
            _destructive("kubectl -n jobs delete pods -l orch.session=abc")["decision"], "allow"
        )

    def test_kubectl_delete_by_other_label_asks(self):
        self.assertEqual(_destructive("kubectl delete pods -l app=api")["decision"], "ask")

    def test_secretctl_apply_k8s_is_a_secret_write(self):
        # CASE-72 L1504: the wrapper wrote a live Secret and passed unclassed.
        result = _destructive(
            "~/.claude/tools/secretctl apply-k8s backend/encryption-key-new -n default backend-env "
            "SEQUENCERS_ENCRYPTION_READ_KEY --confirm")
        self.assertEqual((result["decision"], result["class"]), ("deny", "impossible"))
        self.assertIn("writes a live Secret", result["reason"])
        self.assertIn("# effect_read", result["reason"])

    def test_wrappers_carry_the_gate_of_the_command_after_dashes(self):
        for wrapper in (
            "~/.claude/tools/secretctl run backend/x LBL --",
            "secretctl each backend/x --env V --",
            "secretctl capture backend/x --",
            "~/.claude/tools/check-run --",
            "~/.claude/tools/wait-for --every 30 --max 60 --",
        ):
            result = _destructive(wrapper + " kubectl -n default delete secret backend-env")
            self.assertEqual((result["decision"], result["class"]), ("deny", "impossible"), wrapper)

    def test_wrapped_kill_probe_with_redirect_is_a_read(self):
        self.assertEqual(_destructive("wait-for --every 5 --max 60 -- kill -0 12345 2>/dev/null")["decision"], "allow")

    def test_wrapped_reads_stay_unclassed(self):
        for cmd in (
            "secretctl run backend/x LBL -- curl -H \"X: $SECRET_VALUE\" https://example.com",
            "secretctl list backend/x",
            "~/.claude/tools/secretctl apply-k8s --help",
            "secretctl pull-k8s backend/x -n default backend-env KEY",
            "check-run -- python3 -m pytest -q",
            "wait-for --every 30 --max 60 -- kubectl -n default get pods",
        ):
            self.assertEqual(_destructive(cmd)["decision"], "allow", cmd)

    def test_kubectl_delete_secret_is_denied(self):
        self.assertEqual(_destructive("kubectl delete secret/db-creds -n prod")["decision"], "deny")

    def test_kubectl_writes_with_a_mechanical_undo_run_at_once(self):
        for cmd in (
            "kubectl scale deploy/api --replicas=0",
            "kubectl -n app rollout restart deploy/x",
            "kubectl -n app scale deploy api --replicas 3",
            "kubectl rollout undo deploy/x",
            "kubectl rollout pause deploy/x",
            "kubectl rollout resume deploy/x",
        ):
            with self.subTest(cmd=cmd):
                result = _destructive(cmd)
                self.assertEqual(result["decision"], "allow")
                self.assertIn("undo", result["reason"])

    def test_kubectl_mechanical_undo_beyond_one_object_asks(self):
        for cmd in (
            "kubectl scale --all -n prod deploy --replicas=0",
            "kubectl scale deploy -l app=web -n prod --replicas=0",
            "kubectl scale deploy --selector=tier=api --replicas=0",
            "kubectl -n prod rollout restart deploy --all",
            "kubectl -n prod rollout restart deploy -l app=api",
            "kubectl -n prod scale deploy a b --replicas=0",
            "kubectl -n prod scale deploy/a deploy/b --replicas 0",
            "kubectl -n prod rollout restart deploy a b",
            "kubectl -n prod scale -f deploys.yaml --replicas=0",
            "kubectl -n prod rollout restart -k overlays/prod",
        ):
            with self.subTest(cmd=cmd):
                result = _destructive(cmd)
                self.assertEqual((result["decision"], result["class"]), ("ask", "approval"))

    def test_kubectl_scale_naming_a_secret_is_denied(self):
        for cmd in ("kubectl -n prod scale secret/x --replicas=0", "kubectl scale -f secret.yaml --replicas=0"):
            with self.subTest(cmd=cmd):
                self.assertEqual(_destructive(cmd)["decision"], "deny")

    def test_impossible_refusal_sends_the_step_to_a_plan_the_hook_decides(self):
        result = _destructive('psql -c "DROP TABLE orders"')
        self.assertEqual((result["decision"], result["class"]), ("deny", "impossible"))
        self.assertIn("DESTRUCTIVE_PLAN sha256=<hex>", result["reason"])
        self.assertIn("only when it says so", result["reason"])

    def test_bare_sql_drop_remedy_names_the_way_back_or_the_read(self):
        result = _destructive('psql -c "TRUNCATE orders_old"')
        self.assertEqual((result["decision"], result["class"]), ("deny", "impossible"))
        self.assertIn("its way back if it has one, or the read that showed nothing reads the object",
                      result["reason"])

    def test_kubectl_verbs_moved_from_the_hook_table(self):
        for cmd, decision in [
            ("kubectl rollout history deploy/x", "allow"),
            ("kubectl -n app patch deploy/x -p '{}'", "ask"),
            ("kubectl drain node-3", "ask"),
            ("kubectl -v 6 apply -f m.yaml", "ask"),
            ("kubectl rollout status deploy/x", "allow"),
            ("kubectl get pods -n set", "allow"),
            ("kubectl -n app exec pod -- redis-cli set k v", "allow"),
            ("kubectl get pods # then patch it", "allow"),
        ]:
            with self.subTest(cmd=cmd):
                self.assertEqual(_destructive(cmd)["decision"], decision)

    def test_ask_in_bypass_mode_becomes_deny(self):
        result = gate_policy.destructive_decision(
            "kubectl patch deploy/api -p '{}'", "", None, None,
            permission_mode="bypassPermissions",
        )
        self.assertEqual(result["decision"], "deny")
        self.assertIn("kubectl patch", result["reason"])
        self.assertIn("APPROVE <sha256>", result["reason"])

    def test_sql_delete_without_where_is_denied(self):
        self.assertEqual(_destructive('psql -c "DELETE FROM orders"')["decision"], "deny")

    def test_plan_with_matching_hash_asks_and_shows_the_script(self):
        result = _destructive(_PLAN_CMD, plan_text=_PLAN, plan_hash=_PLAN_HASH)
        self.assertEqual(result["decision"], "ask")
        self.assertIn(_PLAN, result["reason"])

    def test_plan_with_tampered_hash_is_denied(self):
        result = _destructive(_PLAN_CMD, plan_text=_PLAN, plan_hash="b" * 64)
        self.assertEqual(
            (result["decision"], result["reason"].split("\n")[0]),
            ("deny", "plan hash mismatch: rewrite the marker after editing the plan"),
        )

    def test_plan_missing_any_header_is_denied_naming_it(self):
        for line in _PLAN.splitlines()[:len(gate_policy._PLAN_HEADERS)]:
            header = line.split(":")[0] + ":"
            text = _PLAN.replace(line + "\n", "")
            result = _destructive(_PLAN_CMD, plan_text=text, plan_hash=_PLAN_HASH)
            self.assertEqual(result["decision"], "deny", header)
            self.assertIn("`" + header + " ...` is missing", result["reason"], header)

    def test_plan_from_a_subagent_is_denied(self):
        result = _destructive(_PLAN_CMD, "builder", _PLAN, _PLAN_HASH)
        self.assertEqual(result["decision"], "deny")

    def test_marker_words_inside_grep_are_not_an_override(self):
        for command in (
            'grep -rn "DESTRUCTIVE_OK" orch',
            'echo "bash x.sh  # DESTRUCTIVE_PLAN sha256=' + _PLAN_HASH + '"',
        ):
            with self.subTest(command=command):
                self.assertEqual(_destructive(command)["class"], "none")


    def test_ask_in_bypass_true_keeps_ask(self):
        result = gate_policy.destructive_decision(
            "kubectl patch deploy/api -p '{}'", "", None, None,
            {"destructive": {"ask_in_bypass": True}}, "bypassPermissions",
        )
        self.assertEqual(result["decision"], "ask")

    def test_privileged_commands_are_denied(self):
        for cmd in (
            "sudo ls", "apt-get install x", "brew uninstall x", "kill -9 1", "pkill -f x",
            "killall x", "chmod +x /etc/x", "chmod 600 ~/.ssh/config", "chown me tmp/x",
            "chown me /tmp/q.sh", "eval \"$CMD\"",
        ):
            with self.subTest(cmd=cmd):
                self.assertEqual(_destructive(cmd)["class"], "privileged")

    def test_git_option_before_subcommand_is_skipped(self):
        self.assertEqual(_destructive("git -C /x reset --hard HEAD~1")["decision"], "deny")

    def test_cd_voids_relative_ours_prefixes(self):
        self.assertEqual(
            (_destructive("rm -rf tmp/x")["decision"], _destructive("cd ~ && rm -rf tmp/x")["decision"]),
            ("allow", "deny"),
        )

    def test_rm_of_repo_paths_is_denied(self):
        for cmd in ("rm -rf .", "rm -rf ./.", "rm -rf .git", "rm -rf src", "rm -rf tmp/$X/etc"):
            with self.subTest(cmd=cmd):
                self.assertEqual(_destructive(cmd)["decision"], "deny")

    def test_wrappers_do_not_hide_rm(self):
        for cmd in ("nice -n 10 rm -rf /", "find . | xargs -I {} rm -rf {}", "xargs -n 1 rm -rf"):
            with self.subTest(cmd=cmd):
                self.assertEqual(_destructive(cmd)["decision"], "deny")

    def test_command_inside_backticks_is_classed(self):
        self.assertEqual(_destructive("echo `rm -rf /`")["decision"], "deny")

    def test_inner_commands_are_classed(self):
        for cmd in (
            "kubectl -n prod exec db-0 -- psql -c 'DROP TABLE orders'",
            "docker exec db psql -c 'DROP TABLE t'",
            "ssh host psql -c 'DROP TABLE t'",
            "kubectl exec db-0 -- rm -rf /data",
        ):
            with self.subTest(cmd=cmd):
                self.assertEqual(_destructive(cmd)["class"], "impossible")

    def test_inline_python_scan(self):
        self.assertEqual(
            (
                _destructive("python3 -c 'import shutil; shutil.rmtree(\"/srv\")'")["decision"],
                _destructive("python3 -c 'print(1)'")["class"],
            ),
            ("ask", "none"),
        )

    def test_inline_subprocess_call_is_not_destructive(self):
        cmd = "python3 - <<'EOF'\nimport subprocess\nsubprocess.run(['psql', '-f', 'q.sql'])\nEOF"
        self.assertEqual(_destructive(cmd)["class"], "none")

    def test_inline_sql_text_needs_a_db_client_to_count(self):
        edit = "python3 - <<'EOF'\ns = open('q.sql').read().replace('DROP TABLE t', '')\nEOF"
        drop = "python3 - <<'EOF'\nimport psycopg2\ncur.execute('DROP TABLE t')\nEOF"
        self.assertEqual(
            (_destructive(edit)["class"], _destructive(drop)["class"]), ("none", "impossible")
        )

    def test_inline_update_where_through_a_client_asks(self):
        cmd = "python3 - <<'EOF'\nimport psycopg2\ncur.execute('UPDATE t SET a=1 WHERE id=2')\nEOF"
        self.assertEqual(_destructive(cmd)["class"], "approval")

    def test_sql_file_written_by_heredoc_and_run_in_one_command_asks_unless_its_text_has_no_undo(self):
        # A write that does not happen leaves the old disk text for the client (fixture row W8), so a
        # file written and read in one command asks; the written text is still classed.
        read = "cat > q.sql <<'EOF'\nSELECT 1;\nEOF\npsql -f q.sql"
        drop = "cat > q.sql <<'EOF'\nDROP TABLE t;\nEOF\npsql -f q.sql"
        self.assertEqual(
            (_destructive(read)["class"], _destructive(drop)["class"]), ("approval", "impossible")
        )

    def test_sql_file_read_by_the_hook_is_classified_by_its_text(self):
        result = gate_policy.destructive_decision(
            "clickhouse-client < q.sql", "", None, None,
            sql_file_texts={"q.sql": "UPDATE t SET a = 1 WHERE id = 2;"},
        )
        self.assertEqual(result["class"], "approval")

    def test_unreadable_sql_file_asks(self):
        result = gate_policy.destructive_decision(
            "psql -f q.sql", "", None, None, sql_file_texts={"q.sql": None}
        )
        self.assertEqual(result["class"], "approval")

    def test_sql_file_paths_lists_literal_client_inputs(self):
        self.assertEqual(
            gate_policy.sql_file_paths("psql -f a.sql; clickhouse-client < $f; cat b.sql"),
            ["a.sql"],
        )

    def test_shell_c_string_is_classed_as_a_command(self):
        cases = {
            "sh -c 'rm -rf /data'": "deny",
            "kubectl exec p -- sh -c 'rm -rf /data'": "deny",
            "/usr/bin/time -p sh -c 'rm -rf tmp/x'": "allow",
            "find . -name x | xargs -I{} sh -c 'echo {}'": "allow",
        }
        for cmd, decision in cases.items():
            with self.subTest(cmd=cmd):
                self.assertEqual(_destructive(cmd)["decision"], decision)

    def test_sql_without_undo_is_denied(self):
        for cmd in (
            "psql -c 'DROP MATERIALIZED VIEW x'", "psql -c 'ALTER TABLE t DROP COLUMN c'",
            "clickhouse-client --query 'ALTER TABLE t DELETE WHERE 1'",
            "mongosh --eval 'db.t.drop()'", "mysql -e 'TRUNCATE t'",
        ):
            with self.subTest(cmd=cmd):
                self.assertEqual(_destructive(cmd)["decision"], "deny")

    def test_sql_from_a_file_asks(self):
        for cmd in ("psql -f file.sql", "psql < dump.sql"):
            with self.subTest(cmd=cmd):
                self.assertEqual(_destructive(cmd)["decision"], "ask")

    def test_secret_writes_are_denied(self):
        for cmd in (
            "kubectl patch secret x -p '{}'", "kubectl edit secret x",
            "kubectl apply -f - <<EOF\nkind: Secret\nEOF", "kubectl apply -f k8s/secret.yaml",
        ):
            with self.subTest(cmd=cmd):
                self.assertEqual(_destructive(cmd)["decision"], "deny")

    def test_find_delete_follows_the_rm_rule(self):
        self.assertEqual(
            (_destructive("find tmp/x -delete")["decision"], _destructive("find / -name x -delete")["decision"]),
            ("allow", "deny"),
        )

    def test_relative_plan_path_is_denied(self):
        result = _destructive(
            "bash tmp/plan.sh  # DESTRUCTIVE_PLAN sha256=" + _PLAN_HASH, "", _PLAN, _PLAN_HASH
        )
        self.assertEqual((result["decision"], result["reason"].split("\n")[0]), ("deny", "plan path must be absolute"))

    def test_plan_running_another_file_is_denied(self):
        for line in ("source other.sh\n", ". other.sh\n", "exec bash evil.sh\n", "bash other.sh\n"):
            with self.subTest(line=line):
                result = _destructive(_PLAN_CMD, "", _PLAN + line, _PLAN_HASH)
                self.assertEqual(result["decision"], "deny")

    def test_pinned_plan_of_allowed_lines_asks_showing_the_facts_in_prompt_and_approve_request(self):
        run = "bash /repo/run.sh --apply 5 k=v\n"
        plans = [
            _PINNED_PLAN,
            "#!/bin/bash\n" + _PINNED_HEAD + "set -e\n" + run,
            _PINNED_HEAD + "set -eu\n\n   \n" + run,
            _PINNED_HEAD + "  set -euo pipefail\n" + run,
            _PINNED_HEAD + "# the owner's step: \"a\" $(b) `c` <<EOF \\\n" + run + "  # done \\\n",
            _PINNED_HEAD + "# pin: /repo/b.sh sha256=" + _PIN_HASH + "\n" + run + "\tbash /repo/b.sh\n",
        ]
        for plan in plans:
            for mode, decision in (("default", "ask"), ("bypassPermissions", "deny")):
                with self.subTest(plan=plan[len(_PINNED_HEAD) - len(_PIN_LINE):], mode=mode):
                    result = _pinned(plan, mode=mode)
                    reason = result["reason"]
                    self.assertEqual(result["decision"], decision)
                    self.assertIn("APPROVE " + _PLAN_HASH if decision == "deny" else plan, reason)
                    for fact in ("file /repo/run.sh |", _PIN_HASH, "git@github.com:o/r.git",
                                 "origin/main at " + "c" * 40, "2026-11-17T10:00:00+00:00"):
                        self.assertIn(fact, reason)

    def test_pinned_run_whose_facts_disagree_is_denied_naming_the_step(self):
        rows = [
            ({"error": "unreadable", "detail": "No such file"}, "fix the path"),
            ({"error": "git", "detail": "no refs/remotes/origin/HEAD"}, "fix the repository"),
            (None, "fix the repository"),
            (dict(_PIN_FACTS, tree_blob="d" * 40), "merged on the default branch"),
            (dict(_PIN_FACTS, tree_blob=None, tree_mode=None), "merged on the default branch"),
            (dict(_PIN_FACTS, tree_mode="120000"), "merged on the default branch"),
            (dict(_PIN_FACTS, sha256="e" * 64), "rewrite the pin line"),
        ]
        for facts, step in rows:
            with self.subTest(facts=facts):
                result = _pinned(_PINNED_PLAN, facts)
                self.assertEqual(result["decision"], "deny")
                self.assertIn(step, result["reason"])

    def test_pinned_plan_holding_a_line_outside_the_allowed_kinds_is_denied_naming_it(self):
        # Single lines before the run line, then the forms of review-recheck.md N1, N2 and its "inside a
        # construct" list, which bash reads as data or runs inside a construct (r: the run line).
        c, r = "ssh host ", "bash /repo/run.sh"
        forms = [[line, r] for line in [
            "X=1", "cd /tmp", "f() { true; }", "if true; then true; fi", "echo a | tee /tmp/o", "true && true",
            "echo 'a'", "cat <<EOF", "echo a \\", "echo a # c \\", "echo \\\\", "source /repo/run.sh",
            ". /repo/run.sh", "sh /repo/run.sh", "zsh /repo/run.sh", "exec bash /repo/run.sh", "bash /other.sh",
            "bash repo/run.sh", "bash -x /repo/run.sh", "set -x", "set -e; rm -rf /x", "BASH_ENV=/tmp/e " + r,
        ] + [r + tail for tail in (
            " $X", ' "a"', " 'a'", " $(date)", " `date`", " > /tmp/o", " && true", "; true", " | tee /tmp/o", " &",
            " =x", " a*", " {a,b}", " ~/x", " a\\ b", " # c", " (x)",
        )]] + [
            [c + "a\\ #'", r, "'"], [c + "a\\\t#'", r, "'"], [c + "true\\;#'", r, "'"], [c + "a\\&#'", r, "'"],
            [c + "a\\|#'", r, "'"], [c + "\\(#'", r, "'"], [c + "$(echo a)#'", r, "'"], [c + "a$((1))#'", r, "'"],
            [c + "a\\", "#'", r, "'"], [c + "${x:-true #}'", r, "'"], [c + "${x/ #/}'", r, "'"],
            [c + "${x:- #'", r, "'}"], [c + "a\\ # x \\", r], [c + "$(echo a)# x \\", r], [c + "a\\", "# x \\", r],
            ["[[ $q =~ x\\|#'", r, "' ]] ; " + c + "done"],
            [": $$'\\'", c + "'", r, "'"], ['x="$(echo "it\'s")"', c + "'", r, "'"],
            ['x="$(echo \'say "\')"', c + "'", r, "'"], ['x="`echo "it\'s"`"', c + "'", r, "'"],
            ["x=`# it's`", c + "'", r, "'"], ['x="${y#\'"\'}"', c + "'", r, "'"], [c + '"${y#\'"', r, "'}\""],
            ["a=(", r, ")", c + '"${a[@]}"'], ["f() {", r, "}", c + '"$(declare -f f)"'],
            [c + '"$(tail -1 "$0")"', "exit 0", r],
            ["echo x |", r], ["true &&", r], ["false ||", r], ["x=`", r, "`"], ["x=$(", r, ")"], [c + "$(", r, ")"],
            ["{", r, "}"], ["(", r, ")"], ["for x in 1; do", r, "done"], ["case $q in", "*)", r, ";;", "esac"],
            ["if false; then", r, "fi"],
        ]
        for form in forms:
            with self.subTest(form=form):
                result = _pinned(_PINNED_HEAD + "\n".join(form) + "\n")
                self.assertEqual(result["decision"], "deny")
                self.assertIn("plan line 10: a pinned plan holds only comments", result["reason"])
                self.assertIn("logic belongs in the pinned file", result["reason"])
        result = _pinned(_PINNED_HEAD + r + "\nrm -rf /x\n")
        self.assertEqual((result["decision"], result["reason"][:12]), ("deny", "plan line 11"))

    def test_pinned_plan_with_a_pin_problem_or_a_hidden_line_break_is_denied_naming_it(self):
        run = "bash /repo/run.sh\n"
        rows = [
            (_PINNED_HEAD + "# a plan with no run line\n", "runs none"),
            (_PINNED_HEAD + "# pin: /repo/b.sh sha256=" + _PIN_HASH + "\n" + run, "/repo/b.sh is pinned and never run"),
            (_PINNED_HEAD + _PIN_LINE + run, "pinned more than once"),
        ] + [(_PINNED_HEAD + "# x" + ch + "true\n" + run, "save it as plain LF-separated") for ch in (
            "\r", "\f", "\v", "\x00", "\x1c", "\x1d", "\x1e", "�", " ", " ", "\x85",
        )]
        for plan, why in rows:
            with self.subTest(plan=plan[len(_PINNED_HEAD):]):
                result = _pinned(plan)
                self.assertEqual(result["decision"], "deny")
                self.assertIn(why, result["reason"])

    def test_approved_pinned_plan_runs_only_while_its_facts_still_match(self):
        rows = [(_PIN_FACTS, "allow"), (dict(_PIN_FACTS, sha256="e" * 64), "deny"),
                (dict(_PIN_FACTS, tree_blob="d" * 40), "deny"), (None, "deny")]
        for facts, decision in rows:
            with self.subTest(facts=facts):
                self.assertEqual(_pinned(_PINNED_PLAN, facts, approved=True)["decision"], decision)

    def test_facts_name_the_real_path_a_symlinked_pin_runs(self):
        result = _pinned(_PINNED_PLAN, dict(_PIN_FACTS, real_path="/other/run.sh"))
        self.assertIn("file /repo/run.sh (runs /other/run.sh) |", result["reason"])

    def test_refusals_of_a_long_plan_and_a_file_run_name_the_pinned_form(self):
        for plan in (_PLAN + "true\n" * 40, _PLAN + "bash /repo/run.sh\n"):
            with self.subTest(lines=plan.count("\n")):
                self.assertIn("# pin: /<abs path> sha256=<hex>", _pinned(plan)["reason"])

    def test_only_a_plan_that_pins_and_runs_a_file_names_files_to_observe(self):
        self.assertEqual(
            [gate_policy.plan_pinned_paths(p) for p in (_PLAN, _PLAN + "bash /repo/run.sh\n", _PINNED_PLAN)],
            [[], [], ["/repo/run.sh"]],
        )

    def test_line_after_a_comment_is_classed(self):
        self.assertEqual(_destructive("ls # note\nrm -rf /")["decision"], "deny")

    def test_heredoc_authoring_a_plan_is_data(self):
        cmd = "cat > /tmp/plan.sh <<'EOF'\n# undo: none\nrm -rf /var/lib/foo\nEOF"
        self.assertEqual(_destructive(cmd)["class"], "none")

    def test_heredoc_fed_to_a_shell_is_classed(self):
        self.assertEqual(_destructive("bash <<EOF\nrm -rf /\nEOF")["decision"], "deny")

    def test_brace_groups_and_functions_are_classed(self):
        for cmd in ("{ rm -rf /; }", "x() { rm -rf /; }; x", "function x { rm -rf /; }; x"):
            with self.subTest(cmd=cmd):
                self.assertEqual(_destructive(cmd)["decision"], "deny")

    def test_alias_is_privileged(self):
        self.assertEqual(_destructive("alias ls='rm -rf /'")["class"], "privileged")

    def test_oversized_command_is_denied(self):
        cmd = "# " + "x" * 70000 + "\nrm -rf /"
        self.assertEqual(
            _destructive(cmd)["reason"].split("\n")[0], "command too long for the destructive gate"
        )

    def test_relative_rename_is_unclassed(self):
        self.assertEqual(
            (_destructive("mv src/a.py src/b.py")["class"], _destructive("mv src/a.py /etc/a.py")["decision"]),
            ("none", "ask"),
        )

    def test_force_with_lease_is_unclassed(self):
        self.assertEqual(_destructive("git push --force-with-lease")["class"], "none")

    def test_ours_root_directory_is_ours(self):
        for cmd in ("rm -rf tmp/", "rm -rf tmp", "find tmp/ -delete", "rm -rf .worktrees/x"):
            with self.subTest(cmd=cmd):
                self.assertEqual(_destructive(cmd)["decision"], "allow")

    def test_absolute_worktree_task_dir_is_ours(self):
        cases = {
            "rm -rf /repo/.worktrees/task": "allow",
            "cd /repo && rm -rf /repo/.worktrees/task": "allow",
            "rm -rf /repo/.worktrees": "deny",
            "rm -rf /repo/.worktrees/../src": "deny",
            "rm -rf /repo/src/worktrees/task": "deny",
        }
        for cmd, decision in cases.items():
            with self.subTest(cmd=cmd):
                self.assertEqual(_destructive(cmd)["decision"], decision)

    def test_redirects_are_not_rm_operands(self):
        cases = {
            "rm -rf /repo/.worktrees/task 2>&1 | head -5": "allow",
            "rm -rf /repo/.worktrees/task >/dev/null": "allow",
            "rm -rf /repo/.worktrees/task 2> /dev/null": "allow",
            "rm -rf /repo/.worktrees/task > out.log": "allow",
            "rm -rf /etc 2>&1": "deny",
        }
        for cmd, decision in cases.items():
            with self.subTest(cmd=cmd):
                self.assertEqual(_destructive(cmd)["decision"], decision)

    def test_absolute_tmp_inside_a_checkout_is_ours(self):
        # Outside the system temp dir, which is ours as a whole.
        with tempfile.TemporaryDirectory(dir=os.path.expanduser("~")) as root:
            repo, plain = os.path.join(root, "repo"), os.path.join(root, "plain")
            os.makedirs(os.path.join(repo, ".git"))
            cases = {
                f"rm -rf {repo}/tmp/df167": "allow",
                f"rm -rf {repo}/tmp": "allow",
                f"rm -rf {repo}/tmp/../src": "deny",
                f"rm -rf {plain}/tmp/x": "deny",
            }
            decisions = {cmd: _destructive(cmd)["decision"] for cmd in cases}
        self.assertEqual(decisions, cases)

    def test_rm_inside_a_loop_body_is_classed(self):
        self.assertEqual(_destructive('for f in a b; do rm -rf "$f"; done')["decision"], "deny")

    def test_xargs_placeholder_is_an_unknown_path(self):
        self.assertEqual(_destructive("echo /etc | xargs -I{} rm -rf tmp/x {}")["decision"], "deny")

    def test_heredoc_piped_into_a_shell_is_classed(self):
        self.assertEqual(_destructive("cat <<EOF | bash\nrm -rf /\nEOF")["decision"], "deny")

    def test_heredoc_piped_into_a_sql_client_is_classed(self):
        self.assertEqual(_destructive("cat <<EOF | psql\nDROP TABLE t;\nEOF")["decision"], "deny")

    def test_old_override_text_does_not_lift_a_denial(self):
        self.assertEqual(_destructive("rm -rf /etc  # DESTRUCTIVE_OK: owner said")["decision"], "deny")


def _at(command, cwd, sql_file_texts=None):
    return gate_policy.destructive_decision(
        command, "", None, None, sql_file_texts=sql_file_texts, cwd=cwd
    )["decision"]


# Real launch shapes of 28 days of transcripts, paths made neutral: each carries the
# marker in a shell comment but is not the accepted form, so the approval was never looked up.
_H = _PLAN_HASH
_OFF_FORM_LAUNCHES = (
    "bash /w/plan.sh pilot > tmp/p.out 2>&1; echo \"exit=$?\" >> tmp/p.out  # DESTRUCTIVE_PLAN sha256=" + _H
    + "\n# OUTCOMES: exit 0 -> measure; error -> read log",
    "cd /w/repo && bash /w/plan.sh  # DESTRUCTIVE_PLAN sha256=" + _H,
    "bash /w/plan.sh 2>&1 | tail -4  # DESTRUCTIVE_PLAN sha256=" + _H,
    "bash /w/plan.sh  # DESTRUCTIVE_PLAN sha256=" + _H + "\necho \"exit=$?\"; date -u +%H:%M:%S",
    "date -u +%H:%M:%S && bash tmp/plan.sh  # DESTRUCTIVE_PLAN sha256=" + _H,
    "bash /w/plan.sh  # DESTRUCTIVE_PLAN sha256=$(shasum -a 256 /w/plan.sh | cut -c1-64)",
    "zsh /w/plan.sh 2>&1 | tail -5  # DESTRUCTIVE_PLAN sha256=" + _H,
    "echo bash x.sh  # DESTRUCTIVE_PLAN sha256=" + _H,
)
_PLAN_MENTIONS = (
    'grep -rn "DESTRUCTIVE_PLAN" orch/tools',
    "cat > tmp/notes.md <<'EOF'\nrun it as bash /w/plan.sh  # DESTRUCTIVE_PLAN sha256=" + _H + "\nEOF",
    "printf '%s\\n' 'bash /w/plan.sh  # DESTRUCTIVE_PLAN sha256=" + _H + "' > tmp/form.txt",
    "git commit -qm \"gate: refuse launches like bash p.sh > out  # DESTRUCTIVE_PLAN sha256=<hex>\"",
)
# Lines a shell does not read as a comment or a plain path, though Python's str.strip() or \\S would:
# each wraps an approved exact-form line, so reaching allow would run text the hash does not cover.
_NOT_SHELL_BLANK = ("\xa0", "\x0b", "\x0c", "\r", "\x1c", "\u2003")


with open(os.path.join(os.path.dirname(os.path.realpath(__file__)), "fixtures", "destructive_probes.json")) as _fh:
    _PROBES = json.load(_fh)


def _probe_decision(entry):
    """The hook's decision on a probe, with the SQL files it reads given from the fixture (absent: unreadable)."""
    files = {os.path.realpath(p): text for p, text in entry.get("files", {}).items()}
    texts = {p: files.get(p) for p in gate_policy.sql_file_paths(entry["cmd"])}
    return gate_policy.destructive_decision(entry["cmd"], "", None, None, None, "", texts, False, None)


class DestructiveProbeTest(unittest.TestCase):
    """fixtures/destructive_probes.json: "hole" are commands 3814e3e allowed that reach a production deletion
    (a comment quote, an unterminated heredoc operator, SQL on an exec'd client's stdin, a psql shell escape,
    a quote inside $( ) within double quotes); "blocker" the probes the design reviews required refused;
    "read" reads that must stay allowed; "gap" the known gaps of
    decisions/destructive-gate-threat-model-is-agent-mistakes.md, pinned as allow so that closing one is seen."""

    def _decisions(self, group):
        return {label: _probe_decision(e)["decision"] for label, e in _PROBES["destructive"].items()
                if e["group"] == group}

    def test_hole_shapes_are_refused(self):
        for label, decision in self._decisions("hole").items():
            with self.subTest(label):
                self.assertIn(decision, ("ask", "deny"))

    def test_review_blocker_probes_are_refused(self):
        for label, decision in self._decisions("blocker").items():
            with self.subTest(label):
                self.assertIn(decision, ("ask", "deny"))

    def test_reads_stay_allowed(self):
        for label, decision in self._decisions("read").items():
            with self.subTest(label):
                self.assertEqual(decision, "allow")

    def test_known_gaps_still_pass(self):
        # Known gaps, not wanted behaviour: a script written and run in one command (A2, A3, A5), argv
        # through xargs (P17), $(cat file) as an argument (T10, T14), a file rewritten under another
        # spelling (T17, T18, X11, X12), a client file path read on the far side of a carrier (G1, G2, G4),
        # a client whose stdin a group, subshell, function or exec redirect feeds (F07, F08, F09, F24), a heredoc
        # delimiter `<<1`, `<<"END SQL"` or `<<$X` feeding an exec'd client (T4a-T4c).
        for label, decision in self._decisions("gap").items():
            with self.subTest(label):
                self.assertEqual(decision, "allow")

    def test_new_refusals_name_one_remedy(self):
        cases = {
            "echo 'it; helm uninstall prod":
                "the hook cannot pair the quotes of this command, so what it runs is not known; put the quoted"
                " text in a file or a quoted heredoc",
            "( echo 'SELECT 1;' ) | kubectl --context prod-cluster exec -i pg-0 -- psql":
                "SQL on the client's stdin is not visible; pass the SQL as a literal heredoc attached to the client",
            "echo 'SELECT 1;' > /tmp/q.sql; cat /tmp/q.sql | kubectl --context prod-cluster exec -i pg-0 -- psql":
                "SQL from a file this command also names elsewhere: the text the client will read is not known;"
                " write the file in one Bash call and run the client in the next",
        }
        for command, reason in cases.items():
            with self.subTest(command):
                result = _probe_decision({"cmd": command, "files": {"/tmp/q.sql": "SELECT 1;\n"}})
                self.assertEqual((result["decision"], result["reason"]), ("ask", reason))

    def test_boundary_rows_decide_as_the_contract_says(self):
        for label, entry in _PROBES["destructive"].items():
            if entry["group"] != "boundary":
                continue
            with self.subTest(label):
                result = _probe_decision(entry)
                self.assertEqual(result["decision"], entry["expect"])
                if "reason" in entry:
                    self.assertEqual(result["reason"], entry["reason"])

    def test_review_probes_decide_as_pinned(self):
        # Every probe and fuzz row of the fix-round reviews, with its 3814e3e decision ("base") and the
        # decision it is pinned to ("expect").
        for label, entry in _PROBES["review_probes"].items():
            with self.subTest(label):
                self.assertEqual(_probe_decision(entry)["decision"], entry["expect"])

    def test_review_probes_pinned_as_allowed_are_only_gaps_reads_and_unrun_commands(self):
        # A row pinned "allow" is one of: a known gap named in the decision file's "Known gaps" (closing one
        # is seen as a failure above); a read control that reaches no deletion (h01v9, a read through an
        # unterminated heredoc, is a read control pinned "ask"); a command the shell does not run
        # (not_run_by_the_shell). Any other shape pinned "allow" would be a hole pinned open.
        known_gaps = {"h01e", "h03g", "h09a", "h09b", "h09c", "h11b", "h11l", "h11m", "h12a", "h12f", "h12g",
                      "h12h", "h12i", "h12l", "h12m", "p12"}
        read_controls = {"c12", "c15", "p21"}
        probes = _PROBES["review_probes"]
        unrun = {label for label, entry in probes.items() if "not_run_by_the_shell" in entry}
        allowed = {label for label, entry in probes.items() if entry["expect"] == "allow"}
        self.assertEqual(allowed, known_gaps | read_controls | unrun)
        self.assertEqual(probes["h01v9"]["expect"], "ask")

    def test_client_here_string_and_prefix_redirect_are_read_as_its_stdin(self):
        # Allowed at 3814e3e: an expanding here-string asks with the stdin remedy, a prefix ``< file``
        # is read like the suffix form (the file holds a DROP).
        probes = _PROBES["review_probes"]
        for label in ("h12b", "p01", "p02", "p03", "p04", "p18"):
            with self.subTest(label):
                result = _probe_decision(probes[label])
                self.assertEqual((result["decision"], result["reason"]), ("ask", gate_policy._UNREAD_STDIN_HIT[1]))
        for label in ("h12e", "p05", "p06"):
            with self.subTest(label):
                self.assertEqual(_probe_decision(probes[label])["decision"], "deny")
        reads = {
            "psql -h prod-db.internal <<< 'SELECT 1;'": {},
            "< /tmp/q.sql psql -h prod-db.internal": {"/tmp/q.sql": "SELECT 1;\n"},
        }
        for command, files in reads.items():
            with self.subTest(command):
                self.assertEqual(_probe_decision({"cmd": command, "files": files})["decision"], "allow")

    def test_heredoc_with_a_non_identifier_delimiter_feeds_the_client(self):
        # Allowed at 3814e3e: ``<<\SQL``, ``<<'END-SQL'``, ``<<'1'`` holding a DROP.
        probes = _PROBES["review_probes"]
        for label in ("h06a", "h06b", "h06d", "h06e"):
            with self.subTest(label):
                self.assertIn(_probe_decision(probes[label])["decision"], ("ask", "deny"))
        read = "psql -h prod-db.internal <<'END-SQL'\nSELECT 1;\nEND-SQL"
        self.assertEqual(_probe_decision({"cmd": read})["decision"], "allow")

    def test_own_remedy_refusals_in_bypass_mode_name_only_their_remedy(self):
        cases = {
            "echo 'it; helm uninstall prod":
                "the hook cannot pair the quotes of this command, so what it runs is not known; put the quoted"
                " text in a file or a quoted heredoc",
            "( echo 'SELECT 1;' ) | kubectl --context prod-cluster exec -i pg-0 -- psql":
                "SQL on the client's stdin is not visible; pass the SQL as a literal heredoc attached to the client",
            "echo 'SELECT 1;' > /tmp/q.sql; cat /tmp/q.sql | kubectl --context prod-cluster exec -i pg-0 -- psql":
                "SQL from a file this command also names elsewhere: the text the client will read is not known;"
                " write the file in one Bash call and run the client in the next",
        }
        for command, reason in cases.items():
            with self.subTest(command):
                texts = {p: "SELECT 1;\n" for p in gate_policy.sql_file_paths(command)}
                result = gate_policy.destructive_decision(
                    command, "", None, None, None, "bypassPermissions", texts, False, None)
                self.assertEqual((result["decision"], result["reason"]), ("deny", reason))

    def test_text_read_again_past_the_work_budget_is_denied_with_its_remedy(self):
        # Each open heredoc body or $( runs to the end of the text and its holder reads it again: a short
        # text costs its length to a high power (the hook has 5 s). The reading stops at the budget.
        self.assertRaises(gate_policy.OutOfWork, gate_policy._charge, {"work": gate_policy._WORK_BUDGET}, 1)
        drop = "\npsql -h prod-db.internal -c 'DROP TABLE users'"
        commands = {
            "unterminated operators": "<<a\n" * 8000,
            "shell holders of unterminated bodies": "\n".join(["bash <<a"] * 20) + drop,
            "remote shell holders": "\n".join(["ssh prod-db bash <<a"] * 20),
            "open substitutions": 'echo "' + "$(" * 500 + '"',
            "open substitutions in nested quotes": "echo " + '"$(' * 1500,
            "brace lists multiplying": "echo " + "{a,b}" * 2000,
        }
        for name, command in commands.items():
            with self.subTest(name):
                self.assertEqual(gate_policy.destructive_decision(command, "", None, None, None, "", {}, False, None),
                                 {"decision": "deny", "class": "impossible", "reason": gate_policy.OutOfWork.reason,
                                  "exec_targets": []})
                with self.assertRaises(gate_policy.OutOfWork):
                    gate_policy.sql_file_paths(command)

    def test_long_ordinary_commands_stay_within_the_work_budget(self):
        # The largest stored command is 34 KB; the gate reads up to 64 KB.
        writes = "cat > /tmp/notes.md <<'EOF'\n" + "a line of notes about $(the run) and `that`\n" * 1400 + "EOF"
        loop = "for f in a b; do\n  cat <<EOF > $f\n  line $f\nEOF\ndone\n" * 1000
        # A script held by a shell is read by the shell's reading and the body's own; behind ssh once more.
        script = ('echo "step: checking the service"\ncd /tmp/work && ls -la | head -5\nts=$(date +%s)\n'
                  'grep -n "error" logs/app.log | head -20 || true\nmkdir -p tmp/run\ngit -C repo log --oneline -3\n'
                  ) * 340
        held = [opener + script + "EOF" for opener in ("bash <<'EOF'\n", "bash <<EOF\n", "ssh devbox bash <<EOF\n")]
        for command in [writes, loop] + held:
            with self.subTest(command[:30]):
                self.assertEqual(_probe_decision({"cmd": command})["decision"], "allow")

    def test_carriers_nested_past_the_depth_bound_are_denied(self):
        # Three carriers hold a read; a fourth carrier inside them is not read and is denied.
        self.assertEqual(_probe_decision({"cmd": "ssh h " * 3 + "psql -c 'SELECT 1'"})["decision"], "allow")
        for count in (4, 2000):
            with self.subTest(count):
                result = _probe_decision({"cmd": "ssh h " * count + "psql -c 'SELECT 1'"})
                self.assertEqual((result["decision"], result["class"]), ("deny", "impossible"))
                self.assertTrue(result["reason"].startswith("command nesting too deep to classify;"))

    def test_other_gates_keep_the_3814e3e_result(self):
        e1, l3, x1 = (_PROBES["other_gates"][k] for k in ("E1", "L3", "X1"))
        self.assertEqual(gate_policy.builder_events("Bash", {"command": e1["cmd"]}, "/repo/x", None),
                         e1["builder_events"])
        self.assertEqual(gate_policy.landing_target(l3["cmd"], "/repo/x"), l3["landing_target"])
        self.assertEqual(gate_policy.merge_gate_decision(l3["cmd"]), l3["merge_gate_decision"])
        self.assertEqual(gate_policy.kubectl_exec_targets(x1["cmd"]), x1["kubectl_exec_targets"])
        self.assertEqual(gate_policy.destructive_decision(x1["cmd"], "", None, None)["exec_targets"],
                         x1["kubectl_exec_targets"])


class PlanFormTest(unittest.TestCase):
    def test_off_form_launch_is_refused_with_the_accepted_form(self):
        for command in _OFF_FORM_LAUNCHES:
            with self.subTest(command=command):
                result = _destructive(command)
                self.assertEqual((result["decision"], result["class"]), ("deny", "approval"))
                self.assertIn("bash <absolute plan path>  # DESTRUCTIVE_PLAN sha256=<64 hex>", result["reason"])

    def test_mention_of_the_marker_is_not_a_launch(self):
        for command in _PLAN_MENTIONS:
            with self.subTest(command=command):
                self.assertEqual(_destructive(command)["decision"], "allow")

    def test_exact_form_with_comment_lines_reaches_the_approval(self):
        command = ("# LOOP_COST: one-off approved run, no incremental form\n" + _PLAN_CMD
                   + "\n\t# OUTCOMES: exit 0 -> verify and report; non-zero -> read the log and undo")
        for background in (False, True):
            with self.subTest(background=background):
                self.assertEqual(gate_policy.outcomes_decision(command, background)["decision"], "allow")
                self.assertEqual(
                    gate_policy.destructive_decision(command, "", _PLAN, _PLAN_HASH, approved=True)["decision"],
                    "allow")
                self.assertEqual(
                    gate_policy.destructive_decision(command, "", _PLAN, "b" * 64, approved=True)["decision"],
                    "deny")

    def test_comment_line_led_by_a_non_shell_blank_is_not_a_comment(self):
        for lead in _NOT_SHELL_BLANK:
            command = _PLAN_CMD + "\n" + lead + "#x; rm -rf /srv/data"
            with self.subTest(lead=repr(lead)):
                self.assertNotEqual(
                    gate_policy.destructive_decision(command, "", _PLAN, _PLAN_HASH, approved=True)["decision"],
                    "allow")

    def test_shell_syntax_in_the_plan_path_is_not_the_form(self):
        for path in ("/tmp/plan.sh'", '/tmp/plan.sh"', "/tmp/pl`id`an.sh", "/tmp/plan.sh;id", "/tmp/plan.sh&id",
                     "/tmp/plan.sh|sh", "/tmp/plan.sh$(id)", "/tmp/plan.sh>out", "~/plan.sh;id"):
            command = "bash " + path + "  # DESTRUCTIVE_PLAN sha256=" + _PLAN_HASH + "\n# '\" ; rm -rf /srv/data"
            with self.subTest(path=path):
                self.assertNotEqual(
                    gate_policy.destructive_decision(command, "", _PLAN, _PLAN_HASH, approved=True)["decision"],
                    "allow")

    def test_home_relative_plan_path_reaches_the_hash_compare(self):
        command = "bash ~/plan.sh  # DESTRUCTIVE_PLAN sha256=" + _PLAN_HASH
        self.assertEqual(gate_policy.destructive_decision(command, "", _PLAN, _PLAN_HASH, approved=True)["decision"],
                         "allow")
        self.assertEqual(gate_policy.destructive_decision(command, "", _PLAN, "b" * 64, approved=True)["decision"],
                         "deny")

    def test_off_form_launch_from_a_subagent_gets_the_subagent_refusal(self):
        result = _destructive(_OFF_FORM_LAUNCHES[1], "builder")
        self.assertEqual(result["decision"], "deny")
        self.assertIn("only the main session runs a destructive plan", result["reason"])


class DestructiveWorkingDirectoryTest(unittest.TestCase):
    """A main checkout ``main`` (with ``src`` and ``tmp/sub``) and a linked
    worktree ``wt`` below its .worktrees whose ``link`` points at
    ``main/src``, outside the system temp dir (which is ours as a whole)."""

    def setUp(self):
        self.root = os.path.realpath(tempfile.mkdtemp(dir=os.path.expanduser("~")))
        self.main = os.path.join(self.root, "main")
        self.wt = os.path.join(self.main, ".worktrees", "task")
        os.makedirs(os.path.join(self.wt, "tmp"))
        os.makedirs(os.path.join(self.main, ".git"))
        os.makedirs(os.path.join(self.main, "src"))
        os.makedirs(os.path.join(self.main, "tmp", "sub"))
        with open(os.path.join(self.wt, ".git"), "w") as f:
            f.write("gitdir: " + os.path.join(self.main, ".git", "worktrees", "task") + "\n")
        os.symlink(os.path.join(self.main, "src"), os.path.join(self.wt, "link"))

    def tearDown(self):
        shutil.rmtree(self.root)

    def _table(self, cases):
        for (cmd, cwd), decision in cases.items():
            with self.subTest(cmd=cmd, cwd=cwd):
                command = cmd.format(main=self.main, wt=self.wt)
                self.assertEqual(_at(command, cwd.format(main=self.main, wt=self.wt)), decision)

    def test_relative_operands_resolve_through_cd(self):
        self._table({
            ("rm -rf src/probe", "{wt}"): "allow",
            ("rm -rf src", "{main}"): "deny",
            ("cd {main} && rm -rf .worktrees/x", "{wt}"): "allow",
            ("cd {wt}; rm -rf src", "{main}"): "allow",
            ("cd {main}/.worktrees/gone; rm -rf src", "{main}"): "deny",
            ("(cd {wt}); rm -rf b", "{main}"): "deny",
            ("(cd {wt} && rm -rf b)", "{main}"): "allow",
            ("X=$(cd {wt} && git rev-parse HEAD); rm -rf tmp/x", "{main}"): "allow",
            ("cd {wt}; (cd $X); rm -rf src", "{main}"): "allow",
            ("W={wt}; (W=/etc); rm -rf $W/x", "{main}"): "allow",
            ("rm -rf tmp", "{main}"): "allow",
            ('cd "$X" && rm -rf foo', "{wt}"): "deny",
            ("cd {wt} | true; rm -rf src", "{main}"): "deny",
            ("cd {wt} || rm -rf src", "{main}"): "deny",
            ("for d in a; do cd {wt}; done; rm -rf src", "{main}"): "deny",
            ("rm -rf ../other/x", "{wt}"): "allow",
            ("rm -rf ../../src", "{wt}"): "deny",
            ("chmod +x run.sh", "{wt}"): "allow",
            ("chmod +x run.sh", "{main}"): "deny",
        })

    def test_cd_that_may_not_run_makes_the_directory_unknown(self):
        self._table({
            ("cd {wt} && rm -rf src", "{main}"): "allow",
            ("true || cd {wt}; rm -rf src", "{main}"): "deny",
            ("false && cd {wt}; rm -rf src", "{main}"): "deny",
            ("cd {main}/.worktrees/gone && echo; rm -rf src", "{main}"): "deny",
            ("cd .worktrees/gone && echo\nrm -rf src", "{main}"): "deny",
            ("if true; then cd {wt}; fi; rm -rf src", "{main}"): "deny",
            ("cd {wt} && rm -rf tmp", "{main}"): "allow",
            ("true && cd {wt} && rm -rf x", "{main}"): "allow",
            ("true && cd {wt} && rm -rf x; rm -rf src", "{main}"): "deny",
            ("cd {main}/.worktrees/gone && rm -rf x; rm -rf y", "{main}"): "deny",
            ("false || cd {wt} && rm -rf x", "{main}"): "deny",
            ("true && cd {wt} && true || rm -rf src", "{main}"): "deny",
            ("mkdir -p {wt}/new && cd {wt}/new && rm -rf x", "{main}"): "allow",
            ("cd {wt}/new && rm -rf x", "{main}"): "allow",
            ("cd {wt}/new; rm -rf x", "{main}"): "deny",
            ("cd {wt} 2>/dev/null; rm -rf src", "{main}"): "allow",
            ("! cd {wt} && rm -rf src", "{main}"): "deny",
        })

    def test_symlinks_are_resolved_before_the_ours_check(self):
        self._table({
            ("rm -rf link/../main.rs", "{wt}"): "deny",
            ("rm -f link", "{wt}"): "allow",
            ("rm -rf link/", "{wt}"): "deny",
            ("rm -rf link/.", "{wt}"): "deny",
            ("cd link && rm -rf main.rs", "{wt}"): "deny",
            ("rm -rf tmp/../x", "{wt}"): "allow",
            ("rm -rf {wt}/{{../../src,}}", "{main}"): "deny",
            ("rm -rf {wt}/tmp/{{a,b}}", "{main}"): "allow",
            ("printf '{{\"a\":1,\"b\":2}}'; rm -rf src", "{main}"): "deny",
        })

    def test_variables_bind_literal_values_only(self):
        self._table({
            ("W={wt}; rm -rf $W/tmp ${{W}}/x", "{main}"): "allow",
            ("W={wt}; W=$(pwd); rm -rf $W/tmp", "{main}"): "deny",
            ("W={wt} rm -rf $W/x", "{main}"): "deny",
            ("W={wt}; sh -c 'rm -rf $W/tmp'", "{main}"): "deny",
            ("T=$(mktemp -d); rm -rf $T", "{main}"): "allow",
            ('W="tmp/x /etc"; rm -rf $W', "{main}"): "deny",
        })

    def test_any_other_write_to_a_name_unbinds_it(self):
        self._table({
            ("W=/etc; (W={wt}); rm -rf $W/x", "{main}"): "deny",
            ("W={wt}; if true; then W=/etc; fi; rm -rf $W/x", "{main}"): "deny",
            ("W={wt}; while true; do W=/etc; break; done; rm -rf $W/x", "{main}"): "deny",
            ("W={wt}; declare -x W={main}; rm -rf $W/src", "{main}"): "deny",
            ("W={wt}; local -r W={main}; rm -rf $W/src", "{main}"): "deny",
            ("W={wt}; typeset W={main}; rm -rf $W/src", "{main}"): "deny",
            ("W={wt}; W+=/../..; rm -rf $W/src", "{main}"): "deny",
            ("W={main}; f(){{ W={wt}; }}; rm -rf $W/src", "{main}"): "deny",
            ("W={wt}; unset W; rm -rf $W/x", "{main}"): "deny",
            ("true && W={wt}; rm -rf $W/x", "{main}"): "deny",
            ('W=/etc; x="$(rm -rf $W/a)"; W={wt}', "{main}"): "deny",
        })

    def test_shell_c_text_must_be_literal(self):
        self._table({
            ("CMD='rm -rf /'; sh -c \"$CMD\"", "{main}"): "deny",
            ("bash -c \"$(printf 'rm -rf /')\"", "{main}"): "deny",
            ("bash -c 'rm -rf tmp/x'", "{wt}"): "allow",
            ("sh -c 'echo \"v=[$V]\"; s=$(date +%s); rm -rf tmp/x'", "{wt}"): "allow",
            ("sh -c 'rm -rf $V/x'", "{wt}"): "deny",
            ("sh -c 'X=1 $CMD'", "{wt}"): "deny",
            ("sh -c 'echo a; `cat cmd.txt`'", "{wt}"): "deny",
            ("sh -c 'echo `date`'", "{wt}"): "allow",
        })

    def test_only_the_real_rm_own_is_exempt(self):
        tool = os.path.join(os.path.dirname(os.path.realpath(gate_policy.__file__)), "rm-own")
        self._table({
            (tool + " -r /etc", "{wt}"): "allow",
            ("./rm-own -r /etc", "{wt}"): "deny",
            ("./rm-own -r tmp/x", "{wt}"): "allow",
        })

    def test_only_the_real_kill_own_is_exempt(self):
        tool = os.path.join(os.path.dirname(os.path.realpath(gate_policy.__file__)), "kill-own")
        self._table({
            (tool + " 1234", "{wt}"): "allow",
            ("./kill-own 1234", "{wt}"): "deny",
            ("kill 1234", "{wt}"): "deny",
        })

    def test_kill_own_accepts_only_own_leftovers(self):
        me = 501
        rows = {
            "1": (0, "0", "??", "9", "/sbin/launchd", "/sbin/launchd"),
            # Terminal -> login -> -zsh -> claude (session) -> zsh -c -> caller
            "10": (me, "1", "??", "9", "/System/Applications/Utilities/Terminal.app/Contents/MacOS/Terminal", "Terminal"),
            "11": (me, "10", "ttys001", "9", "login", "login -pf u"),
            "12": (me, "11", "ttys001", "9", "-zsh", "-zsh"),
            "13": (me, "12", "ttys001", "9", "claude", "claude --dangerously-skip-permissions"),
            "14": (me, "13", "??", "9", "/bin/zsh", "/bin/zsh -c source /Users/u/.claude-accounts/x.sh"),
            "15": (me, "14", "??", "9", "python3", "python3 kill-own 20"),
            "16": (me, "14", "??", "9", "/usr/bin/node", "node /w/node_modules/.bin/vite"),
            # orphans in a work tree: a leftover, its child, a temp-dir leftover
            "20": (me, "1", "??", "9", "./target/debug/collect", "./target/debug/collect"),
            "21": (me, "20", "??", "9", "sleep", "sleep 9"),
            "25": (me, "1", "??", "9", "sleep", "sleep 9"),
            # refused: tty child, agent child (codex.js), another session's job (native path)
            "22": (me, "1", "??", "9", "/usr/bin/tmux", "tmux"),
            "23": (me, "22", "ttys009", "9", "-zsh", "-zsh"),
            "24": (me, "1", "??", "9", "/usr/bin/node", "node /x/@openai/codex/bin/codex.js"),
            "30": (me, "1", "??", "9", "/Users/u/.local/share/claude/versions/2.1.0", "claude"),
            "31": (me, "30", "??", "9", "sleep", "sleep 9"),
            # refused: launchd job, cwd outside git and temp, unknown cwd, other user
            "40": (me, "1", "??", "9", "/usr/local/bin/agentd", "agentd"),
            "41": (me, "1", "??", "9", "/opt/updater", "updater"),
            "42": (me, "1", "??", "9", "/opt/daemon", "daemon"),
            "43": (0, "1", "??", "9", "/opt/x", "x"),
            # not an agent although its path holds .codex
            "44": (me, "1", "??", "9", "/Users/u/.codex/plugins/helper", "/Users/u/.codex/plugins/helper"),
            # session markers: a shell whose child carries ours, none, another
            # live process's in a child, an unreadable env
            "26": (me, "1", "??", "9", "/bin/zsh", "/bin/zsh -c python3 x.py"),
            "27": (me, "26", "??", "9", "python3", "python3 x.py"),
            "50": (me, "1", "??", "9", "python3", "python3 y.py"),
            "51": (me, "1", "??", "9", "/bin/zsh", "/bin/zsh -c python3 z.py"),
            "52": (me, "51", "??", "9", "python3", "python3 z.py"),
            "53": (me, "1", "??", "9", "sleep", "sleep 9"),
            # one env with a gone session and a live process: refused
            "54": (me, "1", "??", "9", "python3", "python3 w.py"),
        }
        # 13 is the caller's session; 777 no longer exists; 10 is a live process
        markers = {"20": {"13"}, "21": {"13"}, "25": {"777"}, "26": set(), "27": {"13"},
                   "50": set(), "51": {"13"}, "52": {"10"}, "54": {"777", "10"}}
        markers.update({p: {"13"} for p in ("22", "24", "30", "40", "41", "42", "43", "44")})
        cwds = {"10": "/", "20": "/w/repo/sub", "22": "/w/repo", "24": "/w/repo", "30": "/w/repo",
                "25": "/private/tmp/x", "40": "/w/repo", "41": "/Users/u/Library", "43": "/w/repo",
                "44": "/w/repository", "26": "/private/tmp", "50": "/w/repo", "51": "/w/repo",
                "53": "/private/tmp", "54": "/w/repo"}
        why = gate_policy.own_kill_refusals(list(rows) + ["x1", "99"], rows, {"40"}, "15", me, "darwin",
                                            cwds, {"/w/repo"}, {"/private/tmp"}, markers, "13")
        self.assertEqual({p for p, r in why.items() if r is None}, {"16", "20", "21", "25", "26", "27", "53"})
        expected = {"x1": "no such process", "99": "no such process", "43": "another user",
                    "13": "caller or its ancestor", "10": "caller or its ancestor",
                    "22": "terminal ttys009", "24": "agent session", "31": "live agent session 30",
                    "40": "launchd job", "41": "outside a git work tree", "44": "outside a git work tree",
                    "42": "is unknown", "50": "no agent session marker", "52": "belongs to live session 10",
                    "51": "belongs to live session 10", "54": "belongs to live session 10"}
        for pid, reason in expected.items():
            with self.subTest(pid=pid):
                self.assertIn(reason, why[pid])
        linux = gate_policy.own_kill_refusals(["20"], rows, set(), "15", me, "linux",
                                              cwds, {"/w/repo"}, {"/private/tmp"}, markers, "13")
        self.assertIn("only on macOS", linux["20"])

    def test_git_discards_only_in_an_agent_worktree(self):
        self._table({
            ("git reset --hard", "{wt}"): "allow",
            ("git reset --hard", "{main}"): "deny",
            ("W={wt}; git -C $W reset --hard", "{main}"): "allow",
            ("git -C {wt} checkout HEAD~1 -- f", "{main}"): "allow",
            ("git clean -fd", "{main}"): "deny",
            ("git restore f", "{main}"): "deny",
            ("git restore f", "{wt}"): "allow",
            ("git restore --staged f", "{main}"): "allow",
            ("git checkout -q origin/master -- 2>/dev/null", "{main}"): "allow",
            ("git push --force", "{wt}"): "deny",
            ("cd {main}/tmp/sub && git reset --hard", "{main}"): "deny",
            ("git reset --hard", "{main}/tmp/sub"): "deny",
            ("git -C tmp/sub reset --hard", "{main}"): "deny",
            ("git checkout -- src/a", "{main}/tmp/sub"): "deny",
            ("git -C {wt}/.. reset --hard", "{main}"): "deny",
            ("git --work-tree={main} reset --hard", "{wt}"): "deny",
            ("git --git-dir {main}/.git reset --hard", "{wt}"): "deny",
            ("GIT_WORK_TREE={main} git reset --hard", "{wt}"): "deny",
            ("git -c core.worktree={main} reset --hard", "{wt}"): "deny",
        })

    def test_sql_file_redirected_into_exec_is_read_locally(self):
        path = os.path.join(self.main, "q.sql")
        for cmd in ("kubectl exec -i p -- psql < q.sql",
                    "kubectl exec -i p -- clickhouse-client --multiquery < q.sql",
                    "docker exec -i c psql <q.sql"):
            with self.subTest(cmd=cmd):
                self.assertEqual(gate_policy.sql_file_paths(cmd, cwd=self.main), [path])
                self.assertEqual(_at(cmd, self.main, {path: "DR" + "OP TABLE t;"}), "deny")
                self.assertEqual(_at(cmd, self.main, {path: "SELECT 1;"}), "allow")

    def test_sql_file_after_cd_is_read_from_its_resolved_path(self):
        cmd = "cd {wt} && psql -f q.sql".format(wt=self.wt)
        path = os.path.join(self.wt, "q.sql")
        self.assertEqual(gate_policy.sql_file_paths(cmd, cwd=self.main), [path])
        self.assertEqual(_at(cmd, self.main, {path: "SELECT 1;"}), "allow")
        self.assertEqual(_at(cmd, self.main, {path: "DR" + "OP TABLE t;"}), "deny")

    def test_own_removal_accepts_only_resolved_ours_paths(self):
        link = os.path.join(self.wt, "tmp", "up")
        os.symlink(self.main, link)
        cases = {
            os.path.join(self.wt, "tmp"): True,
            os.path.join(self.wt, "src"): True,
            link: True,
            os.path.join(link, "keep.txt"): False,
            os.path.join(self.wt, "..", "..", "keep.txt"): False,
            os.path.join(self.wt, "link", "..", "keep.txt"): False,
            os.path.join(self.main, "tmp"): True,
            os.path.join(self.main, ".worktrees"): False,
            self.main: False,
        }
        for path, allowed in cases.items():
            with self.subTest(path=path):
                self.assertEqual(gate_policy.own_removal(path)[1] is None, allowed)

    def test_rm_own_removes_the_checked_real_path(self):
        import subprocess
        tool = os.path.join(os.path.dirname(os.path.realpath(gate_policy.__file__)), "rm-own")
        os.symlink(self.main, os.path.join(self.wt, "tmp", "up"))
        open(os.path.join(self.main, "keep.txt"), "w").close()
        refused = subprocess.run([sys.executable, tool, "-r", "link/../keep.txt", "tmp/up/"],
                                 cwd=self.wt, stderr=subprocess.DEVNULL)
        self.assertEqual(refused.returncode, 2)
        self.assertTrue(os.path.exists(os.path.join(self.main, "keep.txt")))
        removed = subprocess.run([sys.executable, tool, "-r", "tmp/up/"], cwd=self.wt)
        self.assertEqual(removed.returncode, 0)
        self.assertEqual((os.path.lexists(os.path.join(self.wt, "tmp", "up")),
                          os.path.isdir(os.path.join(self.main, "src"))), (False, True))

    @unittest.skipUnless(sys.platform == "darwin", "kill-own decides only on macOS")
    def test_kill_own_stops_only_when_every_pid_is_eligible(self):
        import subprocess
        import time
        tool = os.path.join(os.path.dirname(os.path.realpath(gate_policy.__file__)), "kill-own")
        # a detached job of this session reparents to PID 1 without a terminal
        # (it lives until this test stops it: the finally block kills it)
        # and carries the session marker in its environment: a leftover; its
        # argument CLAUDE_PID=1 names a live process and must not count
        env = dict(os.environ, CLAUDE_PID=str(os.getpid()))
        tag = "kill-own-test-" + str(os.getpid())
        out = subprocess.run([sys.executable, "-c", "import subprocess,sys;print(subprocess.Popen("
                              "[sys.executable,'-c','import time;time.sleep(3600)','CLAUDE_PID=1','" + tag + "'],"
                              "stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).pid)"],
                             capture_output=True, text=True, start_new_session=True, env=env,
                             cwd=tempfile.gettempdir()).stdout.strip()

        def alive():
            return subprocess.run(["kill", "-0", out], stderr=subprocess.DEVNULL).returncode == 0
        try:
            listed = subprocess.run([sys.executable, tool, "--list"], capture_output=True, text=True,
                                    env=env)
            self.assertIn(out, [l.split("\t")[0] for l in listed.stdout.splitlines()])
            refused = subprocess.run([sys.executable, tool, out, "1"], stderr=subprocess.DEVNULL, env=env)
            self.assertEqual((refused.returncode, alive()), (2, True))
            other = subprocess.run([sys.executable, tool, out], capture_output=True, text=True,
                                   env=dict(env, CLAUDE_PID="1"))
            self.assertEqual((other.returncode, alive()), (2, True))
            self.assertIn("belongs to live session " + str(os.getpid()), other.stderr)
            # a platform binary hides its environment: an own detached
            # leftover of it passes on the rest of the evidence
            hidden = subprocess.run([sys.executable, "-c", "import subprocess,sys;print(subprocess.Popen("
                                     "['/bin/zsh','-c','sleep 10; true'],"
                                     "stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).pid)"],
                                    capture_output=True, text=True, start_new_session=True, env=env,
                                    cwd=tempfile.gettempdir()).stdout.strip()
            time.sleep(0.3)
            relisted = subprocess.run([sys.executable, tool, "--list"], capture_output=True, text=True,
                                      env=env)
            self.assertIn(hidden, [l.split("\t")[0] for l in relisted.stdout.splitlines()])
            self.assertEqual(subprocess.run([sys.executable, tool, hidden], env=env).returncode, 0)
            missing = subprocess.run([sys.executable, tool, "-f", tag + "-none"], capture_output=True,
                                     text=True, env=env)
            self.assertEqual((missing.returncode, missing.stdout), (2, ""))
            # the pattern also sits in this tool's own argv: its ancestors are not targets
            stopped = subprocess.run([sys.executable, tool, "-f", tag], env=env)
            self.assertEqual(stopped.returncode, 0)
            for _ in range(50):
                if not alive():
                    break
                time.sleep(0.1)
            self.assertFalse(alive())
        finally:
            if alive():
                os.kill(int(out), 15)

    def test_heredoc_sql_file_written_after_cd_is_classed(self):
        cmd = "cd {wt} && cat > q.sql <<'EOF'\nDR" "OP TABLE t;\nEOF\npsql -f q.sql"
        self.assertEqual(_at(cmd.format(wt=self.wt), self.main), "deny")


class DestructiveKubectlParsingTest(unittest.TestCase):
    def test_kubectl_verbs(self):
        cases = {
            'k(){ kubectl --context x "$@"; }; k get po': "allow",
            'k(){ rtk proxy kubectl --context x "$@"; }; k delete secret s': "deny",
            "kubectl get po 2>/dev/null": "allow",
            "kubectl -n x port-forward \\\n svc/a 8080:80": "allow",
            "kubectl apply --dry-run=server -f x.yaml": "allow",
            "kubectl $V delete po x": "ask",
            'C="--context x"; kubectl $C get po': "allow",
            "kubectl --cache-dir /x delete po y": "ask",
            "kubectl exec p -- sh -c 'rm -rf /data' --dry-run": "deny",
            "kubectl exec p -- psql -c 'DR" "OP TABLE t' --dry-run": "deny",
            "kubectl exec p -- sh -c 'rm -rf tmp/x'": "deny",
            "ssh host 'rm -rf tmp/x'": "deny",
        }
        for cmd, decision in cases.items():
            with self.subTest(cmd=cmd):
                self.assertEqual(_destructive(cmd)["decision"], decision)


class DestructiveOursPrefixTest(unittest.TestCase):
    def test_caches_and_system_temp_are_ours_below_their_root(self):
        temp = tempfile.gettempdir()
        cases = {
            "rm -rf ~/Library/Caches/example-tool": "allow",
            "rm -rf ~/Library": "deny",
            "rm -rf " + os.path.join(temp, "x"): "allow",
            "rm -rf " + temp: "deny",
        }
        for cmd, decision in cases.items():
            with self.subTest(cmd=cmd):
                self.assertEqual(_destructive(cmd)["decision"], decision)


def _plan_run(approved, mode):
    return gate_policy.destructive_decision(
        _PLAN_CMD, "", _PLAN, _PLAN_HASH, permission_mode=mode, approved=approved,
    )


def _declared(value, body):
    """_PLAN with its `# irreversible:` value replaced and its psql line replaced by ``body``."""
    head = _PLAN[:_PLAN.index("psql")]
    head = re.sub(r"(?m)^# irreversible:.*$", "# irreversible: " + value, head) if value is not None else re.sub(
        r"(?m)^# irreversible:.*\n", "", head)
    return head + body


def _declared_run(value, body, mode="default", approved=False, agent_type=""):
    return gate_policy.destructive_decision(_PLAN_CMD, agent_type, _declared(value, body), _PLAN_HASH, None, mode,
                                            None, approved)


class PlanRequirementTest(unittest.TestCase):
    """The owner approves a plan only for an effect with no way back: declared, seen by the floor, or pinned."""

    def test_declared_none_reversible_plan_runs_without_the_owner_in_every_mode(self):
        for mode in ("default", "bypassPermissions"):
            with self.subTest(mode=mode):
                result = _declared_run("none", "psql -c \"DELETE FROM orders WHERE tenant = 'acme'\"\n", mode)
                self.assertEqual((result["decision"], result["class"], result["owner"]),
                                 ("allow", "plan", "not_needed"))

    def test_none_is_read_case_insensitively_and_trimmed(self):
        self.assertEqual(_declared_run("  NONE  ", "psql -c \"UPDATE orders SET x = 1 WHERE id = 7\"\n")["owner"],
                         "not_needed")

    def test_any_value_other_than_none_needs_the_owner(self):
        for value in ("the Secret value is replaced", "no", "nothing a client sees"):
            with self.subTest(value=value):
                result = _declared_run(value, "psql -c \"UPDATE orders SET x = 1 WHERE id = 7\"\n")
                self.assertEqual((result["decision"], result["class"], result["owner"]),
                                 ("ask", "approval", "requested"))
                self.assertIn(value, result["reason"])

    def test_a_declared_ask_names_the_none_exit_and_a_floor_ask_does_not(self):
        declared = _declared_run("orders_old rows are deleted", "psql -c \"DELETE FROM orders_old WHERE id < 9\"\n")
        floor = _declared_run("none", "kubectl delete secret api-token -n prod\n")
        self.assertIn("the value is `none` and the plan runs without the owner", declared["reason"])
        self.assertIn("A way back is a step the plan states", declared["reason"])
        self.assertNotIn("the value is `none` and the plan runs without the owner", floor["reason"])

    def test_floor_raises_a_none_plan_with_a_visible_effect_without_undo(self):
        bodies = [
            "ssh app-1 rm -rf /var/lib/app/uploads\n",
            "kubectl exec -n prod deploy/api -- rm -rf /data/cache\n",
            "ssh app-1 <<'EOF'\ncd /srv/app && git reset --hard\nEOF\n",
            "kubectl delete secret api-token -n prod\n",
            "kubectl delete namespace staging\n",
            "git push --force origin master\n",
        ]
        for body in bodies:
            with self.subTest(body=body):
                result = _declared_run("none", body)
                self.assertEqual((result["decision"], result["owner"]), ("ask", "requested"))
                self.assertIn("owner needed: floor", result["reason"])

    def test_sql_drop_truncate_and_full_delete_are_left_to_the_declaration(self):
        bodies = [
            'CH=(clickhouse-client --host db)\n"${CH[@]}" --query "DROP TABLE events"\n',
            'psql -c "ALTER TABLE orders\n  DROP COLUMN note"\n',
            'psql -c "truncate orders"\n',
            "pg_dump -t orders_old -Fc > tmp/orders_old.dump\npsql -c 'DROP TABLE orders_old'\n",
            "psql -c 'DELETE FROM orders_old'\n",
        ]
        for body in bodies:
            with self.subTest(body=body):
                self.assertEqual(_declared_run("none", body)["owner"], "not_needed")
                result = _declared_run("the dropped table is read by the billing report", body)
                self.assertEqual((result["decision"], result["owner"]), ("ask", "requested"))

    def test_none_followed_by_text_is_refused_naming_the_test_and_carrying_the_value(self):
        for value in ("none (the backup copy stays on the box)", "none on our data; labels stay after undo",
                      "None; each step is undone by the listed command"):
            with self.subTest(value=value):
                result = _declared_run(value, "psql -c \"UPDATE orders SET x = 1 WHERE id = 7\"\n")
                self.assertEqual((result["decision"], result["owner"], result["declared"]),
                                 ("deny", "form_refused", value))
                self.assertIn("does not begin with the word `none`", result["reason"])
                self.assertIn("the value is exactly `none`", result["reason"])

    def test_value_opening_with_a_longer_word_than_none_asks_the_owner(self):
        self.assertEqual(_declared_run("nonexistent vendor rows are re-created", "echo done\n")["owner"],
                         "requested")

    def test_local_file_deletion_outside_owned_paths_needs_the_owner(self):
        for body in ("rm -rf ~/Documents/reports\n", "find ~/Documents/old -name '*.csv' -delete\n",
                     "git -C ~/src/app reset --hard\n",
                     "f() { rm -rf ~/Documents/reports; }\nssh app-1 \"$(declare -f f); f\"\n"):
            with self.subTest(body=body):
                self.assertEqual(_declared_run("none", body)["owner"], "requested")

    def test_body_the_hook_cannot_read_needs_the_owner(self):
        for body in ("sudo kubectl delete secret api-token -n prod\n", "echo 'it is\nkubectl get pods\n"):
            with self.subTest(body=body):
                self.assertEqual(_declared_run("none", body)["owner"], "requested")

    def test_reading_over_the_work_budget_needs_the_owner(self):
        self.assertEqual(gate_policy.plan_requirement("# irreversible: none\necho " + '"$(' * 1500 + "\n"),
                         (True, "floor: " + gate_policy.OutOfWork.reason))

    def test_plan_with_two_irreversible_lines_is_refused_by_the_form(self):
        result = _declared_run("none", "# irreversible: the vendor record is deleted\necho done\n")
        self.assertEqual((result["decision"], result["owner"]), ("deny", "form_refused"))

    def test_hook_tells_the_agent_to_report_a_plan_that_ran_without_the_owner(self):
        with tempfile.TemporaryDirectory() as home:
            plan = os.path.join(home, "plan.sh")
            text = _declared("none", "echo done\n")
            with open(plan, "w") as fh:
                fh.write(text)
            command = "bash " + plan + "  # DESTRUCTIVE_PLAN sha256=" + hashlib.sha256(text.encode()).hexdigest()
            out = subprocess.run(
                [sys.executable, os.path.join(os.path.dirname(__file__), "..", "..", "hooks", "deny-destructive.py")],
                env=dict(os.environ, HOME=home), capture_output=True, universal_newlines=True,
                input=json.dumps({"session_id": PlanApprovalHookTest.SESSION, "tool_name": "Bash", "cwd": home,
                                  "tool_input": {"command": command}}))
        hook = json.loads(out.stdout)["hookSpecificOutput"]
        self.assertEqual(hook["permissionDecision"], "allow")
        self.assertIn("ran without the owner", hook["additionalContext"])
        self.assertIn("report its scope, count and undo", hook["additionalContext"])

    def test_drop_in_a_whole_line_comment_is_not_read_by_the_floor(self):
        body = "# DROP TABLE orders was considered and rejected\npsql -c \"DELETE FROM orders WHERE id = 7\"\n"
        self.assertEqual(_declared_run("none", body)["owner"], "not_needed")

    def test_pinned_plan_declared_none_runs_without_the_owner_and_reports_the_pinned_version(self):
        result = _pinned(_PINNED_PLAN.replace(
            "# irreversible: the delete trigger notifies the billing service", "# irreversible: none"),
            dict(_PIN_FACTS, text="psql -c 'DROP TABLE orders_old'\n"))
        self.assertEqual((result["decision"], result["owner"]), ("allow", "not_needed"))
        self.assertIn("origin/main at " + "c" * 40 + ", committed 2026-11-17T10:00:00+00:00", result["context"])

    def test_floor_reads_the_pinned_file(self):
        plan = _PINNED_PLAN.replace("# irreversible: the delete trigger notifies the billing service",
                                    "# irreversible: none")
        for text in ("kubectl delete secret api-token -n prod\n", "rm -rf ~/Documents/reports\n",
                     "git push --force origin master\n", "sudo systemctl stop app\n", "echo 'it is\n"):
            with self.subTest(text=text):
                result = _pinned(plan, dict(_PIN_FACTS, text=text))
                self.assertEqual((result["decision"], result["owner"]), ("ask", "requested"))
                self.assertIn("floor: pinned file /repo/run.sh: ", result["reason"])

    def test_floor_names_the_pinned_file_that_raised_it(self):
        plan = (_PINNED_HEAD.replace("# irreversible: the delete trigger notifies the billing service",
                                     "# irreversible: none")
                + "# pin: /repo/b.sh sha256=" + _PIN_HASH + "\nbash /repo/b.sh\nbash /repo/run.sh\n")
        result = gate_policy.destructive_decision(
            _PLAN_CMD, "", plan, _PLAN_HASH, None, "default", None, False, None,
            {"/repo/run.sh": _PIN_FACTS,
             "/repo/b.sh": dict(_PIN_FACTS, real_path="/repo/b.sh", text="git push --force origin master\n")})
        self.assertEqual(result["owner"], "requested")
        self.assertIn("floor: pinned file /repo/b.sh: ", result["reason"])
        self.assertLess(result["facts"].index("/repo/b.sh"), result["facts"].index("/repo/run.sh"))

    def test_plan_without_the_irreversible_header_is_refused_by_the_form(self):
        result = _declared_run(None, "psql -c \"DELETE FROM orders WHERE id = 7\"\n")
        self.assertEqual((result["decision"], result["owner"]), ("deny", "form_refused"))
        self.assertIn("# irreversible: ...` is missing", result["reason"])

    def test_approval_present_for_a_plan_that_needs_no_owner_is_not_spent(self):
        result = _declared_run("none", "psql -c \"DELETE FROM orders WHERE id = 7\"\n", approved=True)
        self.assertEqual((result["decision"], result["owner"]), ("allow", "not_needed"))

    def test_approved_owner_needed_plan_is_allowed_as_approved(self):
        result = _declared_run("none", "kubectl delete secret api-token -n prod\n", "bypassPermissions",
                               approved=True)
        self.assertEqual((result["decision"], result["class"], result["owner"]), ("allow", "approval", "approved"))

    def test_subagent_cannot_run_a_plan_that_needs_no_owner(self):
        result = _declared_run("none", "psql -c \"DELETE FROM orders WHERE id = 7\"\n", agent_type="builder")
        self.assertEqual(result["decision"], "deny")


class PlanApprovalTest(unittest.TestCase):
    def test_approved_plan_is_allowed(self):
        self.assertEqual(_plan_run(True, "bypassPermissions")["decision"], "allow")

    def test_unapproved_plan_in_bypass_names_the_approve_line(self):
        result = _plan_run(False, "bypassPermissions")
        self.assertEqual(result["decision"], "deny")
        self.assertIn("APPROVE " + _PLAN_HASH + "\n", result["reason"])

    def test_unapproved_plan_in_default_mode_asks(self):
        self.assertEqual(_plan_run(False, "default")["decision"], "ask")

    def test_approval_does_not_lift_an_invalid_plan(self):
        result = gate_policy.destructive_decision(
            _PLAN_CMD, "", _PLAN, "b" * 64, approved=True,
        )
        self.assertEqual(result["decision"], "deny")

    def test_bash_write_to_approvals_dir_is_denied(self):
        for cmd in ("echo x > ~/.claude/approvals/s/abc", "touch $HOME/.claude/approvals/s/abc",
                    "cp f ~/.claude/approvals/s/abc"):
            with self.subTest(cmd=cmd):
                self.assertEqual(_destructive(cmd)["decision"], "deny")

    def test_listing_approvals_dir_is_not_classed(self):
        self.assertEqual(_destructive("ls ~/.claude/approvals")["class"], "none")

    def test_file_tool_path_under_approvals_dir_is_denied(self):
        for path in ("~/.claude/approvals/s/abc", "/home/u/.claude/approvals/s/abc",
                     "/home/u/x/../.claude/approvals"):
            with self.subTest(path=path):
                self.assertEqual(
                    gate_policy.approval_path_decision(path, "/home/u")["decision"], "deny")

    def test_file_tool_path_elsewhere_is_not_classed(self):
        self.assertIsNone(gate_policy.approval_path_decision("/home/u/.claude/approvals-x", "/home/u"))


class FalsePositiveRulesTest(unittest.TestCase):
    """Reads and own-job signals pass; the nearest write or foreign target stays refused."""

    def test_table(self):
        for cmd, allowed in [
            ("sleep 30 & P=$!; kill $P", True),
            ("sleep 30 & kill $!", True),
            ("kill %1", True),
            ("kill -0 4242", True),
            ("kill -9 4242", False),
            ("P=4242; kill $P", False),
            ("sleep 30 & kill -- -$!", False),
            ("pkill -f worker", False),
            ('eval "rm -rf /tmp/scratch-x"', True),
            ('eval "rm -rf ~/Documents/x"', False),
            ('eval "$CMD"', False),
            ('clickhouse-client --readonly=1 -q "SELECT count() FROM orders"', True),
            ('clickhouse-client --readonly 1 --host db.example -q "DROP TABLE orders"', True),
            ('clickhouse-client -q "SET readonly=1; SELECT 1"', True),
            ('clickhouse-client --readonly=2 -q "DROP TABLE orders"', False),
            ('clickhouse-client -q "DROP TABLE orders SETTINGS readonly=1"', False),
            ("clickhouse-client --readonly=1 <<EOF\nDROP TABLE orders\nEOF", True),
            ("clickhouse-client <<EOF\nDROP TABLE orders\nEOF", False),
            ('psql -c "BEGIN READ ONLY; DELETE FROM orders WHERE id = 1"', True),
            ('psql -c "START TRANSACTION READ ONLY; SELECT 1"', True),
            ('psql -c "BEGIN READ ONLY; COMMIT; DELETE FROM orders WHERE id = 1"', False),
            ('psql -c "BEGIN READ ONLY; SET transaction_read_only = off; DELETE FROM orders WHERE id = 1"', False),
            ('psql -c "BEGIN READ ONLY; RESET ALL; DELETE FROM orders WHERE id = 1"', False),
            ('psql -1 -c "BEGIN READ ONLY; DELETE FROM orders WHERE id = 1"', False),
            ('psql -c "BEGIN READ ONLY; \\set x 1; DELETE FROM orders WHERE id = 1"', False),
            ('psql -c "BEGIN READ ONLY; SELECT dblink_exec(1)"', False),
            ('psql -c "SELECT pg_terminate_backend(4242)"', False),
            ('psql -c "DELETE FROM orders WHERE id = 1" -c "BEGIN READ ONLY;"', False),
            ('clickhouse local -q "SELECT 1; DROP TABLE t"', True),
            ("clickhouse local -q \"SELECT * FROM url('http://example.com/x')\"", False),
            ('clickhouse local --path /var/lib/clickhouse -q "DROP TABLE t"', False),
            ('docker -H tcp://db.example:2375 exec c psql -c "DROP TABLE orders"', False),
            ('docker --context remote compose -f a.yml exec db psql -c "DROP TABLE orders"', False),
            ("python3 -c 'import psycopg; c.execute(\"BEGIN READ ONLY; DELETE FROM orders\")'", False),
        ]:
            with self.subTest(cmd=cmd):
                self.assertEqual(_destructive(cmd)["decision"] == "allow", allowed)

    def test_clickhouse_readonly_flag_covers_a_query_file(self):
        decision = gate_policy.destructive_decision(
            "clickhouse-client --readonly=1 -f /tmp/q.sql", "", None, None,
            sql_file_texts={"/tmp/q.sql": "DR" + "OP TABLE orders"})
        self.assertEqual(decision["decision"], "allow")

    def test_read_only_file_with_a_side_effect_call_is_asked(self):
        decision = gate_policy.destructive_decision(
            "psql -f /tmp/q.sql", "", None, None, sql_file_texts={"/tmp/q.sql": "SELECT pg_reload_conf();"})
        self.assertEqual(decision["decision"], "ask")

    def test_every_refusal_says_not_to_work_around_it(self):
        reason = _destructive("pkill -f worker")["reason"]
        self.assertTrue(reason.endswith("use the named tool or a plan."), reason)
        self.assertIn("kill-own -f", reason)

    def test_sql_refusal_names_both_read_markers(self):
        reason = _destructive('psql -c "DELETE FROM orders WHERE id = 1"')["reason"]
        self.assertTrue("--readonly=1" in reason and "BEGIN READ ONLY" in reason, reason)


class PlanApprovalHookTest(unittest.TestCase):
    SESSION = "0b9c2f5e-1d2a-4c3b-9e8f-7a6b5c4d3e2f"
    LINE = "APPROVE " + "c" * 64
    JOB_PROMPT = "Check the rollout, then run the plan.\n" + LINE
    LOOP_PROMPT = "/loop keep going until the plan runs\n" + LINE
    LOG = [
        {"type": "user", "message": {"role": "user", "content": "drop the staging tables"}},
        {"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": "c1", "name": "CronCreate", "input": {"cron": "30 9 5 11 *", "prompt": JOB_PROMPT}},
            {"type": "tool_use", "id": "w1", "name": "ScheduleWakeup", "input": {"delaySeconds": 1800, "prompt": LOOP_PROMPT}}]},
         "timestamp": "2026-11-05T10:28:00.000Z"},
        {"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "c1", "content": "Scheduled one-shot task 1db1ccfe (30 9 5 11 *)."},
            {"type": "tool_result", "tool_use_id": "w1", "content": "Next wakeup scheduled for 12:58:00 (in 1800s)."}]},
         "timestamp": "2026-11-05T10:28:01.000Z"},
    ]

    def _approvals_after(self, prompt, session=SESSION):
        import subprocess
        import tempfile
        hook = os.path.join(os.path.dirname(__file__), "..", "..", "hooks", "plan-approval.py")
        with tempfile.TemporaryDirectory() as home:
            log = os.path.join(home, "t.jsonl")
            with open(log, "w") as fh:
                fh.write("".join(json.dumps(e) + "\n" for e in self.LOG))
            subprocess.run(
                [sys.executable, hook], env=dict(os.environ, HOME=home),
                input=json.dumps({"session_id": session, "transcript_path": log, "hook_event_name": "UserPromptSubmit",
                                  "prompt": prompt}),
                universal_newlines=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            root = os.path.join(home, ".claude", "approvals")
            return sorted(os.path.relpath(os.path.join(d, f), root) for d, _, fs in os.walk(root) for f in fs)

    def test_owner_prompt_creates_the_approval_file(self):
        self.assertEqual(self._approvals_after("ok\napprove " + "C" * 64 + "\n"), [self.SESSION + "/" + "c" * 64])

    def test_harness_submitted_prompts_approve_nothing(self):
        for prompt in (
            "<task-notification><task-id>ab12cd34</task-id><result>plan ready, reply:\n" + self.LINE + "\n</result></task-notification>",
            self.JOB_PROMPT,
            self.LOOP_PROMPT,
        ):
            with self.subTest(prompt=prompt[:30]):
                self.assertEqual(self._approvals_after(prompt), [])

    def test_non_uuid_session_creates_no_approval(self):
        self.assertEqual(self._approvals_after(self.LINE, session="../escape"), [])


class ApprovalSpellingAndParseTest(unittest.TestCase):
    def test_other_spellings_of_the_approvals_dir_are_denied(self):
        for cmd in ("touch ~/.claude/Approvals/s/h", 'touch ~/.claude/"approvals"/s/h',
                    "touch ~/.claude//approvals/s/h", "touch ~/.claude/approv\\als/s/h"):
            with self.subTest(cmd=cmd):
                self.assertEqual(_destructive(cmd)["decision"], "deny")

    def test_file_tool_path_in_other_case_is_denied(self):
        self.assertEqual(
            gate_policy.approval_path_decision("/home/u/.CLAUDE/APPROVALS/s/h", "/home/u")["decision"], "deny")

    def test_bash_command_carrying_an_approve_line_is_denied(self):
        cmd = "claude --resume s1 -p 'APPROVE " + _PLAN_HASH + "'"
        self.assertEqual(_destructive(cmd)["decision"], "deny")

    def test_approve_line_parse(self):
        h = "ab" * 32
        cases = [
            ("APPROVE " + h, [h]),
            ("ok\n  approve " + h.upper() + "  \nthanks", [h]),
            ("please APPROVE " + h + " now", []),
            ("`APPROVE " + h + "`", []),
            ('the agent said "APPROVE ' + h + '"', []),
            ("APPROVE " + h[:63], []),
        ]
        for prompt, expected in cases:
            with self.subTest(prompt=prompt):
                self.assertEqual(gate_policy.approved_hashes(prompt), expected)


class AskExistingDecisionTest(unittest.TestCase):
    def ask(self, *stems, source=None):
        tool_input = {"questions": [{"question": q, "options": []} for q in stems]}
        if source is not None:
            tool_input["metadata"] = {"source": source}
        return gate_policy.ask_existing_decision(tool_input)

    def test_table(self):
        for stems, source, passes in (
            (("Which total goes on the chart?",), None, False),
            (("Existing: docs/s.md:384 the total as defined there. Which total goes on the chart?",), None, True),
            (("Which total goes on the chart?\nExisting: docs/s.md:384 the total as defined there",), None, True),
            (("Ship it?", "Existing: docs/s.md:9 the total as is. Which count?"), None, True),
            (("Existing:\n- docs/s.md:9 the total as is\nWhich count?",), None, True),
            (("Existing:\n    docs/s.md:9 the total as is\nWhich count?",), None, True),
            (("Existing:\ndocs/s.md:9 the total as is\nWhich count?",), None, False),
            (("Existing:",), None, False),
            (("**Existing:** **",), None, False),
            (("Existing: <path:line>",), None, False),
            (("Which count? See Existing: docs/s.md:9",), None, False),
            (("Ship it?",), "existing: n/a", True),
            (("Ship it?",), "Existing: N/A confirmation", True),
            (("Which count?",), "existing: docs/s.md:382-386", False),
            (("Ship it?",), "existing:", False),
            (("Ship it?",), "", False),
        ):
            reason = self.ask(*stems, source=source)
            self.assertEqual(reason is None, passes, (stems, source))
            if not passes:
                self.assertTrue(reason.startswith("Refused by ask-existing-gate:"))

    def test_input_without_a_stem_passes(self):
        for tool_input, passes in (
            ({}, True),
            ({"questions": []}, True),
            ({"questions": [{"text": "Which count?"}]}, True),
            ({"questions": [{"question": None}]}, True),
            ({"questions": [{"question": ""}]}, True),
            ({"questions": [{"question": ""}, {"question": "Which count?"}]}, False),
        ):
            reason = gate_policy.ask_existing_decision(tool_input)
            self.assertEqual(reason is None, passes, tool_input)


SPEND_LINE = ("Spend: 467 USD, ceiling 507 USD; account: company registrar, left 1200 USD;"
              " later: renewal 12 USD per domain per year")


class AskSpendRuleTest(unittest.TestCase):
    """Money is spent only on the owner's answer to a question whose subject is the spend."""

    def call(self, *stems, options=(), source="existing: n/a"):
        return {"questions": [{"question": s, "options": list(options)} for s in stems],
                "metadata": {"source": source}}

    def decide(self, tool_input, spend):
        return gate_policy.ask_existing_decision(tool_input, {"spend": spend, "sentence": ""})

    def test_judged_spend_needs_one_question_with_a_full_spend_line(self):
        for stems, refused in (
            (("Buy the inboxes again?",), True),
            (("Buy 39 domains?\n" + SPEND_LINE,), False),
            (("**Spend:** 467 USD, ceiling 507 USD; account: registrar, left 1200 USD; later: none\nBuy?",), False),
            (("Buy 39 domains?\n" + SPEND_LINE, "And which workspace?"), True),
            (("Buy 39 domains?\nSpend: 467 USD, ceiling 507 USD",), True),
            (("Buy 39 domains?\nSpend: 467 USD, ceiling 507 USD; account: registrar, left 1200 USD",), True),
            (("Buy?\nSpend: some money, ceiling high; account: registrar, left a lot; later: none",), True),
            (("Buy? Spend is left to the account later: ceiling 5",), True),
            (("Buy?\nWe noted " + SPEND_LINE,), True),
            (("Buy?\nSpend: unknown, ceiling 5; account: registrar, left 40 USD; later: none",), True),
            (("Buy?\nSpend: ceiling is not known 5; account: ?, left ?; later: ?",), True),
            (("Buy?\nSpend: 467 USD, ceiling <max>; account: <where it is paid from>, left <balance after>;"
              " later: <renewals, monthly fees, or none>",), True),
            (("Buy?\nSpend: 467 USD, ceiling 507 USD; account: registrar, left ?; later: none",), True),
            (("Buy?\nSpend: 467 USD, ceiling 507 USD; account: none, left 40 USD; later: none",), True),
            (("Buy?\nSpend: 467 USD, ceiling 507 USD, account: registrar, left 40 USD, later: none",), False),
            (("Buy?\nSpend: 467 USD, ceiling 507 USD; account: registrar; card 2, left 40 USD;"
              " later: renewal 12 USD a year; no monthly fee",), False),
        ):
            reason = self.decide(self.call(*stems), True)
            self.assertEqual(reason is not None and "(spend rule)" in reason, refused, stems)

    def test_spend_rule_goes_first_and_the_existing_rule_still_runs(self):
        bare = self.call("Buy 39 domains?", source="")
        self.assertIn("(spend rule)", self.decide(bare, True))
        stated = self.call("Buy 39 domains?\n" + SPEND_LINE, source="")
        self.assertTrue(self.decide(stated, True).startswith("Refused by ask-existing-gate: a question"))

    def test_no_spend_verdict_leaves_the_existing_rule_alone(self):
        tool_input = self.call("Which count?", source="")
        for verdict in (None, {"spend": False, "sentence": ""}):
            self.assertEqual(gate_policy.ask_existing_decision(tool_input, verdict),
                             gate_policy.ask_existing_decision(tool_input))
        self.assertIsNone(self.decide(self.call("Recreate the inboxes?"), False))

    def test_prefilter_reads_stems_and_options_in_both_languages(self):
        for stem, options, hit in (
            ("Recreate the inboxes?", [{"label": "Yes", "description": "покупаем 39 доменов"}], True),
            ("Что дальше?", [{"label": "Заново", "description": "повторная покупка 39 доменов"}], True),
            ("Что дальше?", [{"label": "Сами", "description": "мы оплачиваем сами"}], True),
            ("Label the rest?", [{"label": "Yes", "description": "about $80 of model runs"}], True),
            ("Label the rest?", [{"label": "Yes", "description": "around 80 EUR"}], True),
            ("Order mailboxes?", [{"label": "Yes", "description": "we pay for each order"}], True),
            ("Закупаем 39 доменов заново?", [], True),
            ("Что дальше?", [{"label": "Докупить", "description": "докупить 20 ящиков"}], True),
            ("Приобретаем лицензию?", [], True),
            ("Арендуем второй сервер?", [], True),
            ("Что дальше?", [{"label": "Pro", "description": "берём тариф Pro"}], True),
            ("Расход на домены до 500, ок?", [], True),
            ("Плата за домены раз в год, продолжаем?", [], True),
            ("Переходим на хост базы побольше (примерно втрое дороже)?",
             [{"label": "Да, хост побольше", "description": "переезд в выходные"}], True),
            ("Register 39 domains again?", [], True),
            ("Заказать 10 прокси?", [], True),
            ("Кто заказчик отчёта?", [{"label": "Отдел продаж", "description": "регистрируем хук в настройках"}],
             False),
            ("Register the hook in settings?", [{"label": "Yes", "description": "расход памяти вырастет на 5 %"}],
             False),
            ("Order 39 domains at the registrar?", [], True),
            ("Go?", [{"label": "Yes", "description": "rent one more server"}], True),
            ("Go?", [{"label": "Yes", "description": "a fee of 12 per domain"}], True),
            ("It is 500 dollars, go?", [], True),
            ("USD 507 for the domains, go?", [], True),
            ("£400 for the licence?", [], True),
            ("Which total goes on the chart?", [{"label": "Sent", "description": "rows of 3 days"}], False),
            ("Перенести 13 доменов?", [{"label": "Да", "description": "переносим без ящиков"}], False),
            ("Почему числа расходятся?", [{"label": "Сверить", "description": "сначала доказываю причину"}], False),
            ("Keep the order of rows?", [{"label": "Yes", "description": "at least in order to compare, please"}],
             False),
        ):
            got = gate_policy.spend_candidate({"questions": [{"question": stem, "options": options}]})
            self.assertEqual(got, hit, (stem, options))

    def test_spend_verdict_parse(self):
        def envelope(result, is_error=False):
            return json.dumps({"is_error": is_error, "result": result})
        for stdout, verdict in (
            (envelope('{"spend": true, "sentence": "buy domains"}'),
             {"spend": True, "sentence": "buy domains", "duration": False, "duration_sentence": ""}),
            (envelope('```json\n{"spend": false}\n```'),
             {"spend": False, "sentence": "", "duration": False, "duration_sentence": ""}),
            (envelope('{"spend": false, "sentence": "", "duration": true, "duration_sentence": "3 дня"}'),
             {"spend": False, "sentence": "", "duration": True, "duration_sentence": "3 дня"}),
            (envelope('{"spend": "yes"}'), None),
            (envelope('{"handoff": true, "deferral": false, "next_step": false}'), None),
            (envelope('{"spend": true}', is_error=True), None),
            ("not json", None),
        ):
            self.assertEqual(gate_policy.parse_spend_verdict(stdout), verdict, stdout)


if __name__ == "__main__":
    unittest.main()
