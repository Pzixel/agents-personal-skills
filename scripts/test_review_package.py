"""Exercise review-range admission against real Git objects in isolated repos."""

import os
from pathlib import Path
import subprocess
import tempfile
import unittest


SCRIPT = (Path(__file__).resolve().parents[1]
          / "superpowers-subagent-driven-development-psilon/scripts/review-package")


class ReviewRangeTests(unittest.TestCase):
    def setUp(self):
        workspace = tempfile.TemporaryDirectory()
        self.addCleanup(workspace.cleanup)
        self.root = Path(workspace.name)
        self.env = {
            **os.environ,
            "GIT_AUTHOR_NAME": "Range test",
            "GIT_AUTHOR_EMAIL": "range@example.invalid",
            "GIT_COMMITTER_NAME": "Range test",
            "GIT_COMMITTER_EMAIL": "range@example.invalid",
        }
        self.git("init", "-q")
        self.plan = self.root / "plan.md"
        self.plan.write_text("# Range admission example\n")
        self.output = self.root / "review.diff"

    def git(self, *args, text=None):
        return subprocess.run(
            ["git", *args], cwd=self.root, env=self.env,
            input=text, text=True, capture_output=True, check=True,
        ).stdout.strip()

    def tree(self, content):
        blob = self.git("hash-object", "-w", "--stdin", text=content)
        return self.git("mktree", text=f"100644 blob {blob}\tvalue.txt\n")

    def commit(self, content, parent=None):
        args = ["commit-tree", self.tree(content), "-m", "fixture"]
        if parent is not None:
            args.extend(["-p", parent])
        return self.git(*args)

    def package(self, base, head):
        return subprocess.run(
            ["bash", str(SCRIPT), str(self.plan), base, head, str(self.output)],
            cwd=self.root, env=self.env, text=True, capture_output=True,
        )

    def test_admits_descendant_across_multiple_commits(self):
        base = self.commit("before\n")
        middle = self.commit("middle\n", base)
        head = self.commit("after\n", middle)

        result = self.package(base, head)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.output.is_file())

    def test_rejects_empty_commit_range_before_creating_workspace(self):
        base = self.commit("unchanged\n")

        result = subprocess.run(
            ["bash", str(SCRIPT), str(self.plan), base, base],
            cwd=self.root, env=self.env, text=True, capture_output=True,
        )

        self.assertEqual(result.returncode, 3, result.stderr)
        self.assertFalse((self.root / ".superpowers").exists())

    def test_rejects_other_branch_without_overwriting_package(self):
        common = self.commit("common\n")
        base = self.commit("base branch\n", common)
        head = self.commit("other branch\n", common)
        self.output.write_text("existing review\n")

        result = self.package(base, head)

        self.assertEqual(result.returncode, 3, result.stderr)
        self.assertEqual(self.output.read_text(), "existing review\n")

    def test_admits_tree_snapshots_without_commit_ancestry(self):
        base = self.tree("before\n")
        head = self.tree("after\n")

        result = self.package(base, head)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertTrue(self.output.is_file())


if __name__ == "__main__":
    unittest.main()
