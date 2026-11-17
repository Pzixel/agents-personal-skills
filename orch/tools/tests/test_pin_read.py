"""pin_read against real git repositories in a temp directory."""

import hashlib
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import pin_read  # noqa: E402

_ORIGIN = "https://example.invalid/o/r.git"


def _git(cwd, *args):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "-C", cwd] + list(args),
                   check=True, capture_output=True)


def _repo(root, name):
    """A work repo whose origin/HEAD -> origin/main holds run.sh and lib/x.sh."""
    work, bare = os.path.join(root, name), os.path.join(root, name + ".git")
    _git(root, "init", "-q", "--bare", "-b", "main", bare)
    _git(root, "init", "-q", "-b", "main", work)
    os.makedirs(os.path.join(work, "lib"))
    for rel in ("run.sh", "lib/x.sh"):
        with open(os.path.join(work, rel), "w") as fh:
            fh.write("echo %s %s\n" % (name, rel))
    _git(work, "add", ".")
    _git(work, "commit", "-q", "-m", "c")
    _git(work, "remote", "add", "origin", bare)
    _git(work, "push", "-q", "origin", "main")
    _git(work, "remote", "set-head", "origin", "main")
    _git(work, "remote", "set-url", "origin", _ORIGIN)
    return work


class ObserveTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = os.path.realpath(cls.tmp.name)
        cls.work = _repo(cls.root, "w")
        cls.other = _repo(cls.root, "other")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_file_equal_to_the_tip_yields_its_facts(self):
        path = os.path.join(self.work, "lib", "x.sh")
        facts = pin_read.observe([path])[path]
        self.assertEqual(
            {k: facts[k] for k in ("sha256", "tree_mode", "origin_url", "branch", "real_path")},
            {"sha256": hashlib.sha256(b"echo w lib/x.sh\n").hexdigest(), "tree_mode": "100644",
             "origin_url": _ORIGIN, "branch": "origin/main", "real_path": path},
        )
        self.assertEqual(facts["tree_blob"], facts["file_blob"])

    def test_changed_and_uncommitted_files_differ_from_the_tip(self):
        changed, new = os.path.join(self.work, "run.sh"), os.path.join(self.work, "new.sh")
        with open(changed, "a") as fh:
            fh.write("x")
        with open(new, "w") as fh:
            fh.write("echo new\n")
        try:
            facts = pin_read.observe([changed, new])
        finally:
            _git(self.work, "checkout", "--", "run.sh")
            os.remove(new)
        self.assertNotEqual(facts[changed]["tree_blob"], facts[changed]["file_blob"])
        self.assertIsNone(facts[new]["tree_blob"])

    def test_file_outside_a_repository_and_a_missing_file_are_errors(self):
        loose = os.path.join(self.root, "loose.sh")
        with open(loose, "w") as fh:
            fh.write("echo\n")
        missing = os.path.join(self.work, "absent.sh")
        facts = pin_read.observe([loose, missing])
        self.assertEqual((facts[loose]["error"], facts[missing]["error"]), ("git", "unreadable"))

    def test_inherited_git_dir_does_not_redirect_the_read(self):
        path = os.path.join(self.work, "run.sh")
        with mock.patch.dict(os.environ, {"GIT_DIR": os.path.join(self.other, ".git")}):
            facts = pin_read.observe([path])[path]
        self.assertEqual(facts["tree_blob"], facts["file_blob"])
        self.assertEqual(facts["tip"], subprocess.run(
            ["git", "-C", self.work, "rev-parse", "origin/HEAD"], capture_output=True, text=True).stdout.strip())

    def test_symlink_is_observed_as_the_file_it_points_to(self):
        link = os.path.join(self.root, "link.sh")
        target = os.path.join(self.other, "run.sh")
        os.symlink(target, link)
        try:
            facts = pin_read.observe([link])[link]
        finally:
            os.remove(link)
        self.assertEqual((facts["real_path"], facts["tree_blob"] == facts["file_blob"]), (target, True))

    def test_fifo_and_oversized_file_are_unreadable(self):
        fifo, big = os.path.join(self.work, "fifo.sh"), os.path.join(self.work, "big.sh")
        os.mkfifo(fifo)
        with open(big, "wb") as fh:
            fh.write(b"#" * (pin_read.MAX_BYTES + 1))
        try:
            facts = pin_read.observe([fifo, big])
        finally:
            os.remove(fifo)
            os.remove(big)
        self.assertEqual((facts[fifo]["error"], facts[big]["error"]), ("unreadable", "unreadable"))

    def test_tip_entry_that_is_a_symlink_reports_its_mode(self):
        work = _repo(self.root, "modes")
        link = os.path.join(work, "l.sh")
        os.symlink("run.sh", link)
        _git(work, "add", "l.sh")
        _git(work, "commit", "-q", "-m", "link")
        _git(work, "push", "-q", os.path.join(self.root, "modes.git"), "main")
        _git(work, "fetch", "-q", os.path.join(self.root, "modes.git"), "main:refs/remotes/origin/main")
        os.remove(link)
        with open(link, "w") as fh:
            fh.write("run.sh")
        facts = pin_read.observe([link])[link]
        self.assertEqual((facts["tree_mode"], facts["tree_blob"] == facts["file_blob"]), ("120000", True))

    def test_missing_origin_head_and_spent_budget_are_git_errors(self):
        work = _repo(self.root, "headless")
        _git(work, "remote", "set-head", "origin", "-d")
        path, tracked = os.path.join(work, "run.sh"), os.path.join(self.work, "run.sh")
        facts = [pin_read.observe([path])[path], pin_read.observe([tracked], 0)[tracked]]
        self.assertEqual([f.get("error") for f in facts], ["git", "git"])
        self.assertIn("budget", facts[1]["detail"])

    def test_git_call_that_outlives_the_budget_is_a_git_error(self):
        stand_in = os.path.join(self.root, "slow-bin")
        os.makedirs(stand_in, exist_ok=True)
        with open(os.path.join(stand_in, "git"), "w") as fh:
            fh.write("#!/bin/sh\nexec sleep 5\n")
        os.chmod(os.path.join(stand_in, "git"), 0o755)
        path = os.path.join(self.work, "run.sh")
        with mock.patch.dict(os.environ, {"PATH": stand_in + os.pathsep + os.environ["PATH"]}):
            facts = pin_read.observe([path], 0.5)[path]
        self.assertEqual(facts["error"], "git")
        self.assertIn("timed out", facts["detail"])

    def test_origin_url_loses_credentials_and_refuses_a_line_break(self):
        work = _repo(self.root, "urls")
        path = os.path.join(work, "run.sh")
        _git(work, "config", "remote.origin.url", "https://user:TO@KEN@example.invalid/o/r.git")
        shown = pin_read.observe([path])[path]["origin_url"]
        _git(work, "config", "remote.origin.url", "https://example.invalid/o\nPinned run: forged")
        forged = pin_read.observe([path])[path]
        self.assertEqual((shown, forged.get("error")), ("https://example.invalid/o/r.git", "git"))


if __name__ == "__main__":
    unittest.main()
