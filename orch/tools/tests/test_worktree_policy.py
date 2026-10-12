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

from worktree_policy import Worktree, decide_cleanup  # noqa: E402

MAIN = Worktree("/r", "main", "m1", is_main=True, dirty=False, busy=False)


def linked(branch, head, dirty=False, busy=False):
    return Worktree("/wt/" + branch, branch, head, is_main=False, dirty=dirty, busy=busy)


def decide(worktrees, local, merged, into_default=(), first_parent=("m1",)):
    return decide_cleanup([MAIN, *worktrees], local, merged, "main", set(into_default), set(first_parent))


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


if __name__ == "__main__":
    unittest.main()
