import json
import os
import shutil
import sys
import tempfile
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
            "deny",
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

    def test_sixteenth_hands_on_call_is_denied(self):
        self.assertEqual(
            gate_policy.budget_decision("", 200000, 16, "cat src/app.py")["decision"], "deny"
        )

    def test_fifteenth_hands_on_call_is_reminded_not_denied(self):
        self.assertEqual(
            gate_policy.budget_decision("", 200000, 15, "cat src/app.py")["decision"], "remind"
        )

    def test_fourth_hands_on_call_reminds(self):
        self.assertEqual(
            gate_policy.budget_decision("", 200000, 4, "cat src/app.py")["decision"], "remind"
        )

    def test_third_hands_on_call_is_allowed(self):
        self.assertEqual(
            gate_policy.budget_decision("", 200000, 3, "cat src/app.py")["decision"], "allow"
        )

    def test_override_marker_line_bypasses_the_budget_with_its_reason(self):
        self.assertEqual(
            gate_policy.budget_decision(
                "", 200000, 20, "# MAIN_BUDGET_OVERRIDE: delivery setup for slices\ncat src/app.py"
            ),
            {"decision": "override", "reason": "delivery setup for slices"},
        )


class OverrideMarkerTest(unittest.TestCase):
    CASES = [
        ("colon form", "MAIN_BUDGET_OVERRIDE: reads for the retro", "reads for the retro"),
        ("equals with quotes", 'MAIN_BUDGET_OVERRIDE="reads for the retro report"\nls', "reads for the retro report"),
        ("equals with quotes then a command", 'MAIN_BUDGET_OVERRIDE="reads for the retro report" cat a', "reads for the retro report"),
        ("unquoted equals form", "MAIN_BUDGET_OVERRIDE=reads cat src/app.py", None),
        ("trailing comment after a command", "cat x # MAIN_BUDGET_OVERRIDE: reason text", None),
        ("leading hash", "cat a\n  # MAIN_BUDGET_OVERRIDE: delivery setup for slices", "delivery setup for slices"),
        ("inside a grep argument", "rtk grep MAIN_BUDGET_OVERRIDE ~/.claude/x.jsonl", None),
        ("inside an echo string", 'echo "MAIN_BUDGET_OVERRIDE: delivery setup for slices"', None),
        ("reason under the minimum", "# MAIN_BUDGET_OVERRIDE: ok", None),
        ("other marker name", "NO_POLL_OVERRIDE: waiting on a deploy", None),
    ]

    def test_cases(self):
        for label, text, expected in self.CASES:
            with self.subTest(label):
                self.assertEqual(
                    gate_policy.override_marker(text, "MAIN_BUDGET_OVERRIDE"), expected
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
            gate_policy.agent_budget_decision(9000, 0.1, 12, "Explore")["decision"],
            "remind",
        )

    def test_call_capped_agent_is_stopped_at_its_hard_cap(self):
        self.assertEqual(
            gate_policy.agent_budget_decision(9000, 0.1, 18, "Explore")["decision"], "stop"
        )

    def test_call_capped_agent_below_its_soft_cap_is_allowed(self):
        self.assertEqual(
            gate_policy.agent_budget_decision(9000, 0.1, 11, "Explore")["decision"],
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

    def test_a_design_stop_after_writing_only_the_notes_is_let_through(self):
        events = [
            {"kind": "edit", "path": "/repo/orch-notes/SPEC.md"},
            {"kind": "edit", "path": "/repo/orch-notes/DESIGN.md"},
        ]
        self.assertEqual(gate_policy.checks_decision("pytest", events)["decision"], "allow")

    def test_a_code_edit_after_the_notes_still_needs_the_check(self):
        events = [
            {"kind": "edit", "path": "/repo/src/app.py"},
            {"kind": "edit", "path": "/repo/orch-notes/SPEC.md"},
        ]
        self.assertEqual(gate_policy.checks_decision("pytest", events)["decision"], "return")


class IsReadCallTest(unittest.TestCase):
    def test_read_tool_is_a_read(self):
        self.assertTrue(gate_policy.is_read_call("Read"))

    def test_edit_and_write_tools_are_not_reads(self):
        self.assertFalse(gate_policy.is_read_call("Edit"))
        self.assertFalse(gate_policy.is_read_call("Write"))

    def test_wrapped_inspection_command_is_a_read(self):
        self.assertTrue(
            gate_policy.is_read_call("Bash", "rtk proxy poetry run pytest tests/x.py -q")
        )

    def test_cd_prefix_does_not_hide_a_read(self):
        self.assertTrue(gate_policy.is_read_call("Bash", "cd /srv/app && rg -n needle src/"))

    def test_cd_prefix_does_not_hide_a_write(self):
        self.assertFalse(gate_policy.is_read_call("Bash", "cd /srv/app && echo x > out.txt"))

    def test_heredoc_is_a_write(self):
        self.assertFalse(
            gate_policy.is_read_call("Bash", "cat <<'EOF' > /tmp/f\nbody\nEOF")
        )

    def test_appending_redirect_is_a_write(self):
        self.assertFalse(gate_policy.is_read_call("Bash", "printf 'x' >> /tmp/log"))

    def test_tee_is_a_write(self):
        self.assertFalse(gate_policy.is_read_call("Bash", "rtk cat a.txt | tee /tmp/copy"))

    def test_sed_in_place_is_a_write(self):
        self.assertFalse(gate_policy.is_read_call("Bash", "sed -i '' s/a/b/ file.txt"))

    def test_sed_printing_a_range_is_a_read(self):
        self.assertTrue(gate_policy.is_read_call("Bash", "sed -n '10,40p' file.txt"))

    def test_stderr_redirection_is_not_a_file_write(self):
        self.assertTrue(gate_policy.is_read_call("Bash", "cargo check 2>&1 | tail -40"))

    def test_discarding_output_to_devnull_is_not_a_file_write(self):
        self.assertTrue(gate_policy.is_read_call("Bash", "grep -q x file > /dev/null"))

    def test_a_quoted_redirect_inside_a_search_pattern_is_still_a_read(self):
        self.assertTrue(gate_policy.is_read_call("Bash", "rg -n 'x > y' src/"))

    def test_an_inspection_program_redirected_into_a_file_is_a_write(self):
        self.assertFalse(gate_policy.is_read_call("Bash", "rg foo src > /tmp/hits.txt"))


class SpawnDecisionTest(unittest.TestCase):
    def test_main_session_may_spawn_the_flow_types(self):
        params = json.load(open(PARAMS_FILE))
        for p in (None, params):
            for requested in ("builder", "reviewer", "Explore", "prod-probe", "watcher"):
                with self.subTest(params="file" if p else "defaults", requested=requested):
                    self.assertEqual(
                        gate_policy.spawn_decision("", requested, p)["decision"], "allow"
                    )

    def test_main_session_may_not_spawn_a_type_outside_the_flow(self):
        params = json.load(open(PARAMS_FILE))
        for p in (None, params):
            with self.subTest(params="file" if p else "defaults"):
                self.assertEqual(
                    gate_policy.spawn_decision("", "Plan", p)["decision"], "deny"
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
                    gate_policy.spawn_decision("builder", "Explore", p)["decision"],
                    "allow",
                )
                for requested in ("builder", "reviewer", "prod-probe"):
                    self.assertEqual(
                        gate_policy.spawn_decision("builder", requested, p)["decision"],
                        "deny",
                    )

    def test_reviewer_may_spawn_a_search_agent(self):
        self.assertEqual(
            gate_policy.spawn_decision("reviewer", "Explore")["decision"], "allow"
        )

    def test_a_type_without_rights_may_spawn_nothing(self):
        self.assertEqual(
            gate_policy.spawn_decision("watcher", "Explore")["decision"], "deny"
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
            gate_policy.spawn_decision("reviewer", "Explore", params)["decision"],
            "deny",
        )


_SONNET_EXPLORE = {"model_override_rights": {"builder": {"Explore": ["sonnet"]}}}


class ModelOverrideDecisionTest(unittest.TestCase):
    def test_main_session_may_choose_the_model(self):
        self.assertEqual(
            gate_policy.model_override_decision("", "reviewer", "fable")["decision"],
            "allow",
        )

    def test_a_call_without_a_model_takes_it_from_the_definition(self):
        self.assertEqual(
            gate_policy.model_override_decision("builder", "Explore", None)["decision"],
            "allow",
        )

    def test_a_spawner_may_not_raise_the_model_of_the_type_it_spawns(self):
        self.assertEqual(
            gate_policy.model_override_decision("builder", "Explore", "opus")["decision"],
            "deny",
        )

    def test_a_listed_triple_is_allowed(self):
        self.assertEqual(
            gate_policy.model_override_decision(
                "builder", "Explore", "sonnet", _SONNET_EXPLORE
            )["decision"],
            "allow",
        )

    def test_a_right_for_one_requested_type_does_not_cover_another(self):
        self.assertEqual(
            gate_policy.model_override_decision(
                "builder", "prod-probe", "sonnet", _SONNET_EXPLORE
            )["decision"],
            "deny",
        )

    def test_a_right_for_one_model_does_not_cover_another(self):
        self.assertEqual(
            gate_policy.model_override_decision(
                "builder", "Explore", "opus", _SONNET_EXPLORE
            )["decision"],
            "deny",
        )


class PluginPrefixedTypeTest(unittest.TestCase):
    """A plugin agent arrives as `<plugin>:<type>` in both the spawner's
    agent_type and the requested subagent_type; it keeps the bare type's rules."""

    def test_prefixed_builder_keeps_the_builder_spawn_rights(self):
        self.assertEqual(
            gate_policy.spawn_decision("orch:builder", "orch:Explore")["decision"], "allow"
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
                "orch:builder", "orch:Explore", "sonnet", _SONNET_EXPLORE
            )["decision"],
            "allow",
        )


if __name__ == "__main__":
    unittest.main()


_MERGE_FIELDS = (
    "CHECK_RAN: ring r10 on prod, 3635/s",
    "PROD_POSITION: mailboxes touched, prod 187k, example-db count",
    "WAY_BACK: revert PR, 10 min",
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

    def test_branch_push_and_pr_create_are_allowed(self):
        for cmd in ("git push -u origin retro-fixes-2", "gh pr create --fill"):
            self.assertEqual(gate_policy.merge_gate_decision(cmd)["decision"], "allow", cmd)


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
        for field in ("`CHECK_RAN", "`PROD_POSITION", "`WAY_BACK", "`ALWAYS_ON"):
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

    def test_done_word_before_colon_is_a_label(self):
        for text in ("Что сделано: ничего не записано.", "What was done: nothing yet.",
                     "Блокер: нет прав.\n- **Что сделано:** ничего не записано."):
            self.assertEqual(self._d(text), "allow", text)
        for text in ("Прогоны готовы: 27 сессий.", "Сделано: задеплоил.", "Done: merged and deployed.", "Что сделано: PR влит.",
                     "Вижу, что готово.", "Подтверждаю, что сделано.", "Сообщаю, что исправлено.", "I confirm what is done."):
            self.assertEqual(self._d(text), "block", text)

    def test_done_word_with_manner_complement_describes_shape(self):
        for text in ("Фильтр сделан allowlist'ом.", "Фильтр сделан `allowlist`ом.", "The filter is done as a denylist.", "Он сделан через хук."):
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
        self.assertEqual(gate_policy.reply_lint_decision("Ещё не готово, CI идёт.", False)["decision"], "allow")

    def test_unbacked_promise_is_returned(self):
        for text in (
            "The alert text is misleading. Поправлю текст отдельным PR.",
            "The label is wrong; I'll fix it in a follow-up PR.",
        ):
            decision = gate_policy.reply_lint_decision(text, False)
            self.assertEqual(decision["decision"], "block", text)
            self.assertIn("promises future work", decision["reason"], text)

    def test_backed_or_offered_promise_passes(self):
        for text in (
            "Текст алерта неверный. Поправлю отдельным PR, открыт как #42.",
            "Текст алерта неверный. Не исправлено, записано в handoff-файле tmp/todo.md.",
            "Причина в пороге. Поправлю по твоей команде.",
            "The cause is the threshold. I'll fix it on your go.",
            "Hook now matches \"I'll fix\" phrases.",
        ):
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
        ):
            self.assertEqual(self._decide(text), "allow", text)

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

    def test_offer_waiting_on_others_is_not_a_promise(self):
        for text in (
            "Пришлите URL, и я проверю API.",
            "Once you allow writes, I'll write it.",
        ):
            self.assertEqual(self._decide(text), "allow", text)

    def test_promise_waiting_on_own_work_is_returned(self):
        for text in (
            "Потом поправлю тесты.",
            "Займусь этим позже.",
            "Когда выкачу, сам проверю.",
            "Ревьюер нашёл баг. Поправлю потом.",
            "Жду ревью.\n\nПотом поправлю тесты.",
            "Когда закончу миграцию, поправлю линтер.",
            "Когда прогоню тесты, добавлю индекс.",
            "After I run the suite, I'll open the PR.",
            "After reviewing the code, I'll fix it.",
            "After my review of the diff, I'll fix it.",
            "После моего ревью поправлю тесты.",
            "Я пока жду. Агент упал, потом поправлю.",
        ):
            self.assertEqual(self._decide(text), "block", text)

    def test_promise_conditioned_on_own_finding_is_returned(self):
        for text in (
            "Если нормально отработает, открою PR.",
            "Если ок, поправлю.",
            "If it works, I'll open the PR.",
        ):
            self.assertEqual(self._decide(text), "block", text)

    def test_promise_waiting_on_a_background_event_passes(self):
        for text in (
            "Когда агент закончит, проверю.",
            "Жду ревьюера A, затем исправлю.",
            "Жду ревью A. Затем исправлю.",
            "Когда CI пройдёт, открою PR.",
            "Работа продолжится по уведомлению, тогда допишу.",
            "Once the reviewer finishes, I'll fix it.",
            "Поправлю, когда агент закончит.",
            "I'll fix it once the reviewer finishes.",
            "Буду ждать ревьюера, затем исправлю.",
            "Ожидаю ревью, потом исправлю.",
        ):
            self.assertEqual(self._decide(text), "allow", text)

    def test_offer_waiting_on_owner_consent_passes(self):
        for text in ("Если подходит, сделаю так.", "Если согласен, сделаю.", "If that works for you, I'll add it."):
            self.assertEqual(self._decide(text), "allow", text)

    def test_judged_hand_off_is_returned_with_its_sentence(self):
        decision = gate_policy.reply_lint_decision(
            "Есть два варианта.", False, {"handoff": True, "sentence": "Решение за вами."}
        )
        self.assertEqual(decision["decision"], "block")
        self.assertIn("Решение за вами.", decision["reason"])
        self.assertIn("AskUserQuestion", decision["reason"])

    def test_no_hand_off_or_no_verdict_passes(self):
        for verdict in (None, {"handoff": False, "sentence": "none"}):
            self.assertEqual(
                gate_policy.reply_lint_decision("Есть два варианта, решение за вами.", False, verdict)["decision"],
                "allow",
                verdict,
            )

    def test_second_stop_passes_a_judged_hand_off(self):
        decision = gate_policy.reply_lint_decision("Your call.", True, {"handoff": True, "sentence": "Your call."})
        self.assertEqual(decision["decision"], "allow")


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

    @staticmethod
    def _cli(result, is_error=False):
        return json.dumps({"type": "result", "is_error": is_error, "result": result})

    def test_reply_reaches_the_prompt_verbatim(self):
        reply = 'Pick {a} or "b"?\nЖду ответа.'
        prompt = gate_policy.handoff_judge_prompt(reply)
        self.assertIn(json.dumps(reply, ensure_ascii=False), prompt)

    def test_verdict_object_is_parsed(self):
        for result in (
            '{"sentence": "Your call.", "handoff": true}',
            '```json\n{"sentence": "Your call.", "handoff": true}\n```',
        ):
            self.assertEqual(
                gate_policy.parse_handoff_verdict(self._cli(result)),
                {"handoff": True, "sentence": "Your call."},
                result,
            )

    def test_anything_but_one_verdict_object_is_no_verdict(self):
        for stdout in (
            "",
            "not json",
            self._cli('{"sentence": "x", "handoff": true}', is_error=True),
            self._cli('Verdict: {"sentence": "x", "handoff": true}'),
            self._cli('{"sentence": "x", "handoff": "false"}'),
            self._cli('{"sentence": "x"} {"handoff": true}'),
        ):
            self.assertIsNone(gate_policy.parse_handoff_verdict(stdout), stdout)


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
        prompt = "task: fix it\nfiles: a.py:10, b.py:3\ncheck: cargo test -p x"
        self.assertEqual(gate_policy.brief_decision("builder", prompt)["decision"], "allow")

    def test_probe_without_access_is_denied(self):
        self.assertEqual(gate_policy.brief_decision("prod-probe", "Measure x")["decision"], "deny")

    def test_other_types_are_not_gated(self):
        self.assertEqual(gate_policy.brief_decision("Explore", "where is x")["decision"], "allow")


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
    BRIEF = "files: a\ncheck: b\n"

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
    "# count: 120 rows\n"
    "# contradiction: none\n"
    "# prod_position: rows deleted, acme has 120 of 9.1M, SELECT count(*) 10:02Z\n"
    "# way_back: COPY from tmp/orders-acme.csv, 2 min\n"
    "psql -c \"DELETE FROM orders WHERE tenant = 'acme'\"\n"
)
_PLAN_HASH = "a" * 64
_PLAN_CMD = "bash /tmp/plan.sh  # DESTRUCTIVE_PLAN sha256=" + _PLAN_HASH


def _destructive(command, agent_type="", plan_text=None, plan_hash=None):
    return gate_policy.destructive_decision(command, agent_type, plan_text, plan_hash)


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

    def test_kubectl_delete_secret_is_denied(self):
        self.assertEqual(_destructive("kubectl delete secret/db-creds -n prod")["decision"], "deny")

    def test_kubectl_scale_asks(self):
        self.assertEqual(
            _destructive("kubectl scale deploy/api --replicas=0")["decision"], "ask"
        )

    def test_kubectl_verbs_moved_from_the_hook_table(self):
        for cmd, decision in [
            ("kubectl rollout restart deploy/x", "ask"),
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
            "kubectl scale deploy/api --replicas=0", "", None, None,
            permission_mode="bypassPermissions",
        )
        self.assertEqual(result["decision"], "deny")

    def test_sql_delete_without_where_is_denied(self):
        self.assertEqual(_destructive('psql -c "DELETE FROM orders"')["decision"], "deny")

    def test_plan_with_matching_hash_asks_and_shows_the_script(self):
        result = _destructive(_PLAN_CMD, plan_text=_PLAN, plan_hash=_PLAN_HASH)
        self.assertEqual(result["decision"], "ask")
        self.assertIn(_PLAN, result["reason"])

    def test_plan_with_tampered_hash_is_denied(self):
        result = _destructive(_PLAN_CMD, plan_text=_PLAN, plan_hash="b" * 64)
        self.assertEqual(
            (result["decision"], result["reason"]),
            ("deny", "plan hash mismatch: rewrite the marker after editing the plan"),
        )

    def test_plan_missing_any_header_is_denied_naming_it(self):
        for line in _PLAN.splitlines()[:6]:
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
            "echo bash x.sh  # DESTRUCTIVE_PLAN sha256=" + _PLAN_HASH,
        ):
            with self.subTest(command=command):
                self.assertEqual(_destructive(command)["class"], "none")

    def test_ask_in_bypass_true_keeps_ask(self):
        result = gate_policy.destructive_decision(
            "kubectl scale deploy/api --replicas=0", "", None, None,
            {"destructive": {"ask_in_bypass": True}}, "bypassPermissions",
        )
        self.assertEqual(result["decision"], "ask")

    def test_privileged_commands_are_denied(self):
        for cmd in (
            "sudo ls", "apt-get install x", "brew uninstall x", "kill -9 1", "pkill -f x",
            "killall x", "chmod +x /etc/x", "chmod 600 ~/.ssh/config", "chown me tmp/x",
            "chown me /tmp/q.sh", "eval ls",
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

    def test_sql_file_written_by_heredoc_is_classified_by_its_text(self):
        read = "cat > q.sql <<'EOF'\nSELECT 1;\nEOF\npsql -f q.sql"
        drop = "cat > q.sql <<'EOF'\nDROP TABLE t;\nEOF\npsql -f q.sql"
        self.assertEqual(
            (_destructive(read)["class"], _destructive(drop)["class"]), ("none", "impossible")
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
        self.assertEqual((result["decision"], result["reason"]), ("deny", "plan path must be absolute"))

    def test_plan_running_another_file_is_denied(self):
        for line in ("source other.sh\n", ". other.sh\n", "exec bash evil.sh\n", "bash other.sh\n"):
            with self.subTest(line=line):
                result = _destructive(_PLAN_CMD, "", _PLAN + line, _PLAN_HASH)
                self.assertEqual(result["decision"], "deny")

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
            _destructive(cmd)["reason"], "command too long for the destructive gate"
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

    def test_only_the_real_kill_forward_is_exempt(self):
        tool = os.path.join(os.path.dirname(os.path.realpath(gate_policy.__file__)), "kill-forward")
        self._table({
            (tool + " 1234", "{wt}"): "allow",
            ("./kill-forward 1234", "{wt}"): "deny",
            ("kill 1234", "{wt}"): "deny",
        })

    def test_kill_forward_accepts_only_own_kubectl_port_forwards(self):
        uid = 501
        cases = {
            ("10", (uid, "1", "01:00", "kubectl -n x port-forward svc/a 0:80")): True,
            ("11", (uid, "1", "01:00", "/usr/local/bin/kubectl port-forward pod/p 5432")): True,
            ("12", (0, "1", "01:00", "kubectl port-forward svc/a 0:80")): False,
            ("13", (uid, "1", "01:00", "kubectl -n x get pods")): False,
            ("14", (uid, "1", "01:00", "sleep port-forward")): False,
            ("15", None): False,
            ("-1", (uid, "1", "01:00", "kubectl port-forward svc/a 0:80")): False,
        }
        for (pid, row), allowed in cases.items():
            with self.subTest(pid=pid):
                self.assertEqual(gate_policy.forward_refusal(pid, row, uid) is None, allowed)

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

    def test_kill_forward_stops_only_when_every_pid_is_a_forward(self):
        import shutil
        import subprocess
        tool = os.path.join(os.path.dirname(os.path.realpath(gate_policy.__file__)), "kill-forward")
        # sleep ignores argv[0], so ps shows it as a kubectl port-forward
        forward = subprocess.Popen(["kubectl -n x port-forward svc/a 0:80", "30"],
                                   executable=shutil.which("sleep"))
        other = subprocess.Popen(["sleep", "30"])
        try:
            listed = subprocess.run([sys.executable, tool, "--list"], capture_output=True, text=True)
            self.assertIn(str(forward.pid), [l.split("\t")[0] for l in listed.stdout.splitlines()])
            refused = subprocess.run([sys.executable, tool, str(forward.pid), str(other.pid)],
                                     stderr=subprocess.DEVNULL)
            self.assertEqual((refused.returncode, forward.poll(), other.poll()), (2, None, None))
            stopped = subprocess.run([sys.executable, tool, str(forward.pid)])
            self.assertEqual((stopped.returncode, forward.wait(5), other.poll()), (0, -15, None))
        finally:
            for p in (forward, other):
                if p.poll() is None:
                    p.terminate()
                p.wait()

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


class PlanApprovalHookTest(unittest.TestCase):
    def test_owner_prompt_creates_the_approval_file(self):
        import subprocess
        import tempfile
        hook = os.path.join(os.path.dirname(__file__), "..", "..", "hooks", "plan-approval.py")
        with tempfile.TemporaryDirectory() as home:
            subprocess.run(
                [sys.executable, hook], check=True, env=dict(os.environ, HOME=home),
                input=json.dumps({"session_id": "s1", "prompt": "ok\napprove " + "C" * 64 + "\n"}),
                universal_newlines=True, stdout=subprocess.DEVNULL,
            )
            self.assertTrue(os.path.isfile(os.path.join(home, ".claude", "approvals", "s1", "c" * 64)))


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

