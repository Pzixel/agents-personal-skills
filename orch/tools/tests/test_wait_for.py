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


class WaitForFailOn(unittest.TestCase):
    EXIT_7 = ("python3", "-c", "raise SystemExit(7)")

    def wait(self, *options):
        return subprocess.run(
            [str(WAIT_FOR), *options, "--", *self.EXIT_7],
            capture_output=True, text=True, timeout=20,
        )

    def test_listed_code_exits_3_at_once(self):
        proc = self.wait("--every", "30", "--max", "60", "--fail-on", "1,7")
        self.assertEqual(proc.returncode, 3)
        self.assertIn("FAILED (terminal condition exit 7)", proc.stdout)

    def test_unlisted_code_keeps_waiting(self):
        self.assertEqual(self.wait("--every", "0.1", "--max", "0.3").returncode, 124)


if __name__ == "__main__":
    unittest.main()
