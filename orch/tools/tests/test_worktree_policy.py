"""Cases for worktree_policy.decide_cleanup.

Contract: a branch is finished when a merged pull request's head equals its
current tip. Finished branches lose their clean, idle linked worktree, the
local branch. Dirty or busy worktrees keep the branch;
the default branch and unmerged or advanced branches are never deleted.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.realpath(__file__))))

from gate_policy import bash_effects  # noqa: E402
from worktree_policy import (  # noqa: E402
    DELEGATE_SILENT_S, Worktree, decide_cleanup, delegate_facts, delegate_finished, delegate_held, own_writes,
    uncommitted_work_decision, written_worktrees)

MAIN = Worktree("/r", "main", "m1", is_main=True, dirty=False, busy=False)


def linked(branch, head, dirty=False, busy=False):
    return Worktree("/wt/" + branch, branch, head, is_main=False, dirty=dirty, busy=busy)


def decide(worktrees, local, merged, into_default=(), first_parent=("m1",), content_merged=(), detached_in=()):
    return decide_cleanup([MAIN, *worktrees], local, merged, "main", set(into_default), set(first_parent),
                          set(content_merged), dict(detached_in))


def detached(name, head):
    return Worktree("/wt/" + name, None, head, is_main=False, dirty=False, busy=False)


class DecideCleanupTest(unittest.TestCase):
    def test_finished_clean_worktree_is_removed_with_local_branch(self):
        c = decide([linked("f", "a")], {"main": "m1", "f": "a"}, {"f": {"a"}})
        self.assertEqual(
            (c.remove_worktrees, c.delete_local_branches),
            (("/wt/f",), ("f",)),
        )

    def test_tip_advanced_after_merged_head_is_kept(self):
        c = decide([linked("f", "b")], {"main": "m1", "f": "b"}, {"f": {"a"}})
        self.assertEqual((c.remove_worktrees, c.delete_local_branches), ((), ()))

    def test_dirty_worktree_keeps_worktree_and_branches(self):
        c = decide([linked("f", "a", dirty=True)], {"main": "m1", "f": "a"}, {"f": {"a"}})
        self.assertEqual((c.remove_worktrees, c.delete_local_branches), ((), ()))

    def test_busy_worktree_keeps_worktree_and_branches(self):
        c = decide([linked("f", "a", busy=True)], {"main": "m1", "f": "a"}, {"f": {"a"}})
        self.assertEqual((c.remove_worktrees, c.delete_local_branches), ((), ()))

    def test_default_branch_matching_a_merged_head_is_never_deleted(self):
        c = decide([], {"main": "m1"}, {"main": {"m1"}})
        self.assertEqual(c.delete_local_branches, ())

    def test_fresh_branch_without_merged_pr_is_kept(self):
        c = decide([linked("g", "m1")], {"main": "m1", "g": "m1"}, {"f": {"m1"}})
        self.assertEqual((c.remove_worktrees, c.delete_local_branches), ((), ()))


    def test_tip_merged_into_default_through_a_merge_commit_is_finished(self):
        c = decide([linked("h", "x")], {"main": "m1", "h": "x"}, {}, into_default={"h"})
        self.assertEqual((c.remove_worktrees, c.delete_local_branches), (("/wt/h",), ("h",)))

    def test_fresh_branch_at_first_parent_default_commit_is_kept(self):
        c = decide([linked("g", "m1")], {"main": "m1", "g": "m1"}, {}, into_default={"g"})
        self.assertEqual((c.remove_worktrees, c.delete_local_branches), ((), ()))

    def test_tip_merged_as_a_differently_named_pr_head_is_finished(self):
        c = decide([linked("feat", "x")], {"main": "m1", "feat": "x"}, {"fix": {"x"}})
        self.assertEqual((c.remove_worktrees, c.delete_local_branches), (("/wt/feat",), ("feat",)))

    def test_branch_whose_content_is_in_default_is_finished(self):
        c = decide([linked("h", "y")], {"main": "m1", "h": "y"}, {}, content_merged={"h"})
        self.assertEqual((c.remove_worktrees, c.delete_local_branches), (("/wt/h",), ("h",)))

    def test_branch_with_an_unmerged_commit_is_kept(self):
        c = decide([linked("h", "y")], {"main": "m1", "h": "y"}, {})
        self.assertEqual((c.remove_worktrees, c.delete_local_branches), ((), ()))

    def test_detached_worktree_in_default_is_removed_without_branch(self):
        c = decide([detached("d", "m0")], {"main": "m1"}, {}, detached_in={"/wt/d": 0})
        self.assertEqual((c.remove_worktrees, c.delete_local_branches), (("/wt/d",), ()))

    def test_detached_worktree_with_own_commit_is_kept(self):
        c = decide([detached("d", "z")], {"main": "m1"}, {})
        self.assertEqual(c.remove_worktrees, ())

    def test_fresh_detached_worktree_at_first_parent_commit_is_kept(self):
        c = decide([detached("d", "m1")], {"main": "m1"}, {}, detached_in={"/wt/d": 3600})
        self.assertEqual(c.remove_worktrees, ())

    def test_old_detached_worktree_at_first_parent_commit_is_removed(self):
        c = decide([detached("d", "m1")], {"main": "m1"}, {}, detached_in={"/wt/d": 3 * 86400})
        self.assertEqual(c.remove_worktrees, ("/wt/d",))

    def test_branch_at_first_parent_commit_another_pr_merged_is_kept(self):
        c = decide([linked("g", "m1")], {"main": "m1", "g": "m1"}, {"other": {"m1"}})
        self.assertEqual((c.remove_worktrees, c.delete_local_branches), ((), ()))


class UncommittedWorkDecisionTest(unittest.TestCase):
    def test_file_edited_after_session_start_is_reported(self):
        report, prints = uncommitted_work_decision(100.0, {"/wt/x": [("a", 150.0)]}, set())
        self.assertEqual((report, len(prints)), (["/wt/x"], 1))

    def test_same_dirty_set_is_reported_once(self):
        _, prints = uncommitted_work_decision(100.0, {"/wt/x": [("a", 150.0)]}, set())
        report, _ = uncommitted_work_decision(100.0, {"/wt/x": [("a", 150.0)]}, set(prints))
        self.assertEqual(report, [])

    def test_files_older_than_session_are_not_reported(self):
        report, _ = uncommitted_work_decision(100.0, {"/wt/x": [("a", 50.0)]}, set())
        self.assertEqual(report, [])

    def test_fresh_handoff_inside_the_worktree_settles_it(self):
        report, _ = uncommitted_work_decision(100.0, {"/wt/x": [("a", 150.0), ("HANDOFF.md", 160.0)]}, set())
        self.assertEqual(report, [])

    def test_handoff_outside_naming_the_worktree_settles_it(self):
        report, _ = uncommitted_work_decision(100.0, {"/wt/x": [("a", 150.0)]}, set(), {"/wt/x"})
        self.assertEqual(report, [])


class DelegateHeldTest(unittest.TestCase):
    """A dirty worktree is a live delegate's work in progress unless the session itself wrote there."""

    def held(self, own, delegates):
        return delegate_held(["/r/.worktrees/x"], ["fp"], own, delegates)

    def test_worktree_named_by_a_live_builder_is_not_reported(self):
        self.assertEqual(self.held(set(), [("orch:builder", {"/r/.worktrees/x"}, True)]), ([], []))

    def test_own_write_keeps_the_report_beside_a_live_builder(self):
        self.assertEqual(self.held({"/r/.worktrees/x"}, [("orch:builder", {"/r/.worktrees/x"}, True)]),
                         (["/r/.worktrees/x"], ["fp"]))

    def test_finished_builder_holds_nothing(self):
        self.assertEqual(self.held(set(), [("orch:builder", {"/r/.worktrees/x"}, False)]),
                         (["/r/.worktrees/x"], ["fp"]))

    def test_live_read_only_delegate_holds_nothing(self):
        self.assertEqual(self.held(set(), [("orch:prod-probe", {"/r/.worktrees/x"}, True)]),
                         (["/r/.worktrees/x"], ["fp"]))


class OwnWritesTest(unittest.TestCase):
    def bash(self, command, cwd):
        return own_writes("Bash", {"command": command}, cwd, bash_effects(command, cwd)["writes"])

    def test_interpreter_heredoc_patching_a_relative_file_inside_the_worktree_is_own(self):
        command = "python3 - <<'EOF'\np='crates/t/a.rs'\ns=open(p).read()\nopen(p,'w').write(s)\nEOF"
        self.assertEqual(self.bash(command, "/r/.worktrees/x"), {"/r/.worktrees/x"})

    def test_notes_handoff_inside_the_worktree_is_not_own_work(self):
        self.assertEqual(own_writes("Write", {"file_path": "/r/.worktrees/x/orch-notes/HANDOFF.md"}, "/r", ()), set())

    def test_read_command_naming_the_worktree_is_not_own_work(self):
        self.assertEqual(self.bash("git -C /r/.worktrees/x status --short", "/r"), set())


    def test_handoff_the_gate_asks_for_written_by_relative_path_is_not_own_work(self):
        self.assertEqual(self.bash("echo 'builder still coding' >> HANDOFF.md", "/r/.worktrees/x"), set())

    def test_a_document_named_like_a_handoff_is_own_work(self):
        self.assertEqual(own_writes("Write", {"file_path": "/r/.worktrees/x/docs/handoff-format.md"}, "/r", ()),
                         {"/r/.worktrees/x"})


WT = "/r/.worktrees/x"


def _main(content):
    return (None, {"type": "assistant" if content[0]["type"] == "tool_use" else "user", "cwd": "/r",
                   "message": {"content": content}})


def _spawn(call, kind, prompt):
    return _main([{"type": "tool_use", "id": call, "name": "Agent",
                   "input": {"subagent_type": kind, "prompt": prompt, "run_in_background": True}}])


def _launched(call, agent):
    return _main([{"type": "tool_result", "tool_use_id": call,
                   "content": [{"type": "text", "text": "Async agent launched successfully.\nagentId: %s (...)" % agent}]}])


def _notified(agent):
    return _main([{"type": "text", "text": "<task-notification>\n<task-id>%s</task-id>\n<status>completed</status>\n"
                                           "</task-notification>" % agent}])


class DelegateFactsTest(unittest.TestCase):
    """Which async agent names which worktree, and whether it is still open, as the main transcript records it."""

    def test_builder_spawned_on_a_worktree_is_open_and_names_it(self):
        entries = [_spawn("toolu_1", "orch:builder", "workspace: %s, fix it" % WT), _launched("toolu_1", "a1")]
        self.assertEqual(delegate_facts(entries), (set(), [("a1", "orch:builder", {WT}, True)]))

    def test_send_message_adds_the_worktree_it_names(self):
        entries = [_spawn("toolu_1", "orch:builder", "no path here"), _launched("toolu_1", "a1"),
                   _main([{"type": "tool_use", "id": "toolu_2", "name": "SendMessage",
                           "input": {"to": "a1", "message": "continue in %s" % WT}}])]
        self.assertEqual(delegate_facts(entries)[1], [("a1", "orch:builder", {WT}, True)])

    def test_completion_notification_closes_the_agent(self):
        entries = [_spawn("toolu_1", "orch:builder", WT), _launched("toolu_1", "a1"), _notified("a1")]
        self.assertEqual(delegate_facts(entries)[1], [("a1", "orch:builder", {WT}, False)])

    def test_session_edit_inside_the_worktree_is_own(self):
        entries = [_main([{"type": "tool_use", "id": "toolu_3", "name": "Edit",
                           "input": {"file_path": WT + "/src/a.py", "old_string": "a", "new_string": "b"}}])]
        self.assertEqual(delegate_facts(entries), ({WT}, []))


class DelegateFinishedTest(unittest.TestCase):
    """A delegate with no notification is finished by its meta.json or by a silent transcript."""

    NOW = 1_000_000.0

    def test_meta_finished_after_the_last_transcript_write(self):
        self.assertTrue(delegate_finished({"finished": True}, self.NOW - 60, self.NOW - 61, self.NOW))

    def test_transcript_written_after_the_finished_meta_is_a_resumed_run(self):
        self.assertFalse(delegate_finished({"finished": True}, self.NOW - 600, self.NOW - 30, self.NOW))

    def test_running_delegate_without_the_key_writing_recently_is_live(self):
        self.assertFalse(delegate_finished({}, self.NOW - 3600, self.NOW - 590, self.NOW))

    def test_delegate_without_the_key_silent_past_the_bound_is_finished(self):
        self.assertTrue(delegate_finished({}, self.NOW - 7200, self.NOW - DELEGATE_SILENT_S - 1, self.NOW))

    def test_missing_transcript_is_not_finished(self):
        self.assertFalse(delegate_finished({}, None, None, self.NOW))


class WrittenWorktreesTest(unittest.TestCase):
    def test_agent_prompt_naming_a_worktree_is_not_a_write(self):
        self.assertEqual(written_worktrees("Agent", {"prompt": "workspace: /r/.worktrees/x"}), set())

    def test_read_inside_a_worktree_is_not_a_write(self):
        self.assertEqual(written_worktrees("Read", {"file_path": "/r/.worktrees/x/a.py"}), set())

    def test_edit_inside_a_worktree_is_a_write(self):
        self.assertEqual(written_worktrees("Edit", {"file_path": "/r/.worktrees/y/a.py"}), {"/r/.worktrees/y"})

    def test_bash_command_naming_a_worktree_is_a_write(self):
        self.assertEqual(written_worktrees("Bash", {"command": "cd /r/.worktrees/z && git add -A"}),
                         {"/r/.worktrees/z"})

    def test_notebook_edit_by_notebook_path_is_a_write(self):
        self.assertEqual(written_worktrees("NotebookEdit", {"notebook_path": "/r/.worktrees/n/a.ipynb"}),
                         {"/r/.worktrees/n"})

    def test_relative_bash_path_resolves_against_cwd(self):
        self.assertEqual(written_worktrees("Bash", {"command": "git -C .worktrees/x add -A"}, "/r"),
                         {"/r/.worktrees/x"})

    def test_relative_bash_path_without_cwd_is_not_a_candidate(self):
        self.assertEqual(written_worktrees("Bash", {"command": "cd .worktrees/x && touch f"}), set())


if __name__ == "__main__":
    unittest.main()
