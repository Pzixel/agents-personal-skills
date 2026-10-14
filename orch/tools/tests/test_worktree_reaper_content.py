"""content_in_default against real git: patch ids must not stand in for content.

Contract: a branch is finished only when its content is in the default branch.
"""

import importlib.util
import os
import subprocess
import tempfile
import unittest

HOOK = os.path.join(os.path.dirname(os.path.realpath(__file__)), "..", "..", "hooks", "worktree-reaper.py")
spec = importlib.util.spec_from_file_location("worktree_reaper", HOOK)
reaper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reaper)


def git(repo, *args):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=repo,
                   check=True, capture_output=True)


def commit(repo, name, text, msg):
    with open(os.path.join(repo, name), "w") as fh:
        fh.write(text)
    git(repo, "add", name)
    git(repo, "commit", "-qm", msg)


class ContentInDefaultTest(unittest.TestCase):
    def setUp(self):
        self.repo = tempfile.mkdtemp()
        git(self.repo, "init", "-q", "-b", "main")
        commit(self.repo, "a", "base\n", "base")
        git(self.repo, "branch", "work")
        git(self.repo, "checkout", "-q", "work")
        commit(self.repo, "a", "base\nfeature\n", "feature")
        git(self.repo, "checkout", "-q", "main")

    def test_squash_merged_branch_is_in_default(self):
        commit(self.repo, "a", "base\nfeature\n", "squash of work")
        self.assertTrue(reaper.content_in_default(self.repo, "main", "work", "work"))

    def test_patch_reverted_on_default_is_not_in_default(self):
        commit(self.repo, "a", "base\nfeature\n", "squash of work")
        git(self.repo, "revert", "--no-edit", "HEAD")
        self.assertFalse(reaper.content_in_default(self.repo, "main", "work", "work"))


if __name__ == "__main__":
    unittest.main()
