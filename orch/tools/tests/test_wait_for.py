"""wait-for runs the condition argv with the caller's quoting intact."""

import pathlib
import subprocess
import unittest

WAIT_FOR = pathlib.Path(__file__).resolve().parents[1] / "wait-for"


def run(*condition):
    return subprocess.run(
        [str(WAIT_FOR), "--every", "1", "--max", "1", "--", *condition],
        capture_output=True,
        text=True,
    ).returncode


class WaitForQuoting(unittest.TestCase):
    def test_quoted_inner_script_runs_as_one_word(self):
        # Joined with plain spaces this became `sh -c echo a | grep -q a`:
        # the pipe left the inner shell and the condition could never pass.
        self.assertEqual(run("sh", "-c", "echo a | grep -q a"), 0)

    def test_bare_condition_still_works(self):
        self.assertEqual(run("test", "-d", str(WAIT_FOR.parent)), 0)

    def test_failing_condition_times_out(self):
        self.assertEqual(run("sh", "-c", "false"), 124)


class WaitForBrokenCondition(unittest.TestCase):
    def test_wrong_flag_exits_2_at_once(self):
        # A wrong flag looked like "still pending" for the whole --max in
        # seven waits of one session; now it is reported as broken.
        self.assertEqual(run("sh", "-c", "ls --no-such-flag-xyz"), 2)

    def test_missing_command_exits_2(self):
        self.assertEqual(run("no-such-command-xyz"), 2)


if __name__ == "__main__":
    unittest.main()
