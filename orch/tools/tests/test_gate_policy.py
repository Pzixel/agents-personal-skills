import json
import os
import sys
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


class MergeGateDecisionTest(unittest.TestCase):
    def test_merge_without_token_is_denied(self):
        self.assertEqual(gate_policy.merge_gate_decision("gh pr merge 12 --squash")["decision"], "deny")

    def test_push_to_master_without_token_is_denied(self):
        self.assertEqual(
            gate_policy.merge_gate_decision("git push origin HEAD:master")["decision"], "deny"
        )

    def test_merge_with_token_is_allowed(self):
        self.assertEqual(
            gate_policy.merge_gate_decision(
                "echo 'CHECK_RAN: ring r10 on prod, 3635/s'; gh pr merge 12 --squash"
            )["decision"],
            "allow",
        )

    def test_branch_push_and_pr_create_are_allowed(self):
        for cmd in ("git push -u origin retro-fixes-2", "gh pr create --fill"):
            self.assertEqual(gate_policy.merge_gate_decision(cmd)["decision"], "allow", cmd)


class ReplyLintDecisionTest(unittest.TestCase):
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

    def test_answer_ending_on_a_question_passes(self):
        text = "Пауза 2 с стоит в PAGE_PAUSE_SECONDS. Это единственное место. Убрать её вместе со следующим шагом?"
        self.assertEqual(gate_policy.reply_lint_decision(text, False)["decision"], "allow")

    def test_owner_decision_list_is_returned(self):
        text = "Статус: план готов, замер 12:00Z 1.5 cores. Решения за вами: 1) retention 2) порог."
        self.assertEqual(gate_policy.reply_lint_decision(text, False)["decision"], "block")

    def test_owner_domain_question_passes(self):
        text = "Удаление 664 аккаунтов у стороннего провайдера необратимо. Удалять? Рекомендую нет."
        self.assertEqual(gate_policy.reply_lint_decision(text, False)["decision"], "allow")

    def test_quoted_marker_is_not_a_question(self):
        text = "Добавил в хук паттерн \"решения за вами\"; проверено 12:00Z."
        self.assertEqual(gate_policy.reply_lint_decision(text, False)["decision"], "allow")

    def test_second_stop_passes(self):
        self.assertEqual(gate_policy.reply_lint_decision("Готово.", True)["decision"], "allow")


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
