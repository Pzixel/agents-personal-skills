import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.realpath(__file__)), ".."))

from pace_policy import (bash_calls, branch_of, landings_of, merge_succeeded, pr_events, report, rows_of,  # noqa: E402
                         run_of, tasks_of, tier)

REPO = "/r/backend"
MERGE = "gh pr " + "merge"
FIXTURE = os.path.join(os.path.dirname(os.path.realpath(__file__)), "fixtures", "pace_task.json")


def stamp(minute):
    return "2026-11-17T06:%02d:00.000Z" % minute


def user(minute, text, cwd=REPO):
    return {"type": "user", "timestamp": stamp(minute), "cwd": cwd, "message": {"content": text}}


def assistant(minute):
    return {"type": "assistant", "timestamp": stamp(minute), "message": {"content": [{"type": "text", "text": "ok"}]}}


def bash(minute, tool_id, command, output, cwd=REPO, error=False, timed=True):
    use = {"type": "assistant", "timestamp": stamp(minute), "cwd": cwd,
           "message": {"content": [{"type": "tool_use", "id": tool_id, "name": "Bash", "input": {"command": command}}]}}
    result = {"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": tool_id, "is_error": error, "content": output}]}}
    if timed:
        result["timestamp"] = stamp(minute + 1)
    return [use, result]


def builder(start, end, worktree="feat-x", extra=""):
    return run_of("builder", [user(start, "workspace: %s/.worktrees/%s\n%s" % (REPO, worktree, extra)),
                              assistant(end)])


def window():
    return run_of("x", [user(0, "")]).first, run_of("x", [user(59, "")]).first + 1


def landed(runs, main_entries, base="feat-x"):
    """(minutes from the task's first builder event to its landing, landed PR selector); None when none links."""
    start, end = window()
    tasks = tasks_of(REPO, runs, start, end)
    merge = landings_of(REPO, tasks, *pr_events(bash_calls(main_entries)))[base]
    return None if merge is None else ((merge.stamp - tasks[base]["builders"][0].first) / 60, merge.number or merge.branch)


def landed_minutes(runs, main_entries):
    result = landed(runs, main_entries)
    return None if result is None else result[0]


class LandingTest(unittest.TestCase):
    def test_merge_from_the_worktree_lands_at_its_result(self):
        main = bash(30, "t1", MERGE + " 12 --squash", "MERGED", cwd=REPO + "/.worktrees/feat-x")
        self.assertEqual(landed_minutes([builder(10, 20)], main), 21.0)

    def test_merge_naming_the_branch_lands(self):
        main = bash(30, "t1", MERGE + " 12 --squash --body 'fix/feat-x-thing'", "MERGED")
        self.assertEqual(landed_minutes([builder(10, 20, extra="branch fix/feat-x-thing")], main), 21.0)

    def test_merge_of_the_pr_created_from_the_worktree_lands(self):
        main = (bash(25, "t1", "cd %s/.worktrees/feat-x && gh pr create --fill" % REPO,
                     "https://github.com/o/backend/pull/77")
                + bash(30, "t2", MERGE + " 77 --squash", "MERGED"))
        self.assertEqual(landed_minutes([builder(10, 20)], main), 21.0)

    def test_same_pr_number_in_another_repository_does_not_land(self):
        main = (bash(25, "t1", "cd %s/.worktrees/feat-x && gh pr create --fill" % REPO,
                     "https://github.com/o/backend/pull/77")
                + bash(30, "t2", MERGE + " 77 -R o/frontend --squash", "MERGED"))
        self.assertIsNone(landed_minutes([builder(10, 20)], main))

    def test_refused_or_failed_merge_does_not_land(self):
        where = REPO + "/.worktrees/feat-x"
        main = (bash(30, "t1", MERGE + " 12", "Refused by merge-gate", cwd=where)
                + bash(40, "t2", MERGE + " 12", "", cwd=where, error=True))
        self.assertIsNone(landed_minutes([builder(10, 20)], main))

    def test_same_branch_merged_in_another_repository_does_not_land(self):
        main = (bash(25, "t1", "cd /r/frontend && " + MERGE + " fix/feat-x-thing --squash", "MERGED")
                + bash(30, "t2", MERGE + " 9 -R o/frontend --body 'fix/feat-x-thing'", "MERGED"))
        self.assertIsNone(landed_minutes([builder(10, 20, extra="branch fix/feat-x-thing")], main))

    def test_pr_created_in_another_worktree_belongs_to_that_task(self):
        main = (bash(22, "t1", "cd %s/.worktrees/feat-w && gh pr create --fill" % REPO,
                     "https://github.com/o/backend/pull/78", cwd=REPO + "/.worktrees/feat-x")
                + bash(24, "t2", "cd %s/.worktrees/feat-x && gh pr create --fill" % REPO,
                       "https://github.com/o/backend/pull/77")
                + bash(30, "t3", MERGE + " 78 --squash", "MERGED", cwd=REPO + "/.worktrees/feat-x")
                + bash(40, "t4", MERGE + " 77 --squash", "MERGED"))
        self.assertEqual(landed([builder(10, 20), builder(11, 12, worktree="feat-w")], main), (31.0, "77"))

    def test_pr_created_in_a_worktree_belongs_to_it_when_another_task_names_its_branch_first(self):
        own = "Slices 1-2 run on branch feat/w-labels\nworkspace: %s/.worktrees/feat-x (branch feat/x-defs)" % REPO
        main = (bash(22, "t1", "cd %s/.worktrees/feat-w && gh pr create --fill" % REPO,
                     "https://github.com/o/backend/pull/78")
                + bash(24, "t2", "cd %s/.worktrees/feat-x && gh pr create --fill" % REPO,
                       "https://github.com/o/backend/pull/77")
                + bash(30, "t3", "gh pr view feat/w-labels --json number", '{"number":78}')
                + bash(32, "t4", MERGE + " 78 --squash", "MERGED")
                + bash(40, "t5", MERGE + " 77 --squash", "MERGED"))
        runs = [run_of("builder", [user(10, own), assistant(20)]),
                builder(11, 12, worktree="feat-w", extra="branch feat/w-labels")]
        self.assertEqual(landed(runs, main), (31.0, "77"))

    def test_pr_created_after_a_closed_subshell_cd_belongs_to_the_outer_worktree(self):
        command = ("cd %s/.worktrees/feat-x && git fetch; (cd web && npm run build); cd ..; cd feat-x && "
                   "gh pr create --fill" % REPO)
        main = (bash(25, "t1", command, "https://github.com/o/backend/pull/77", cwd=REPO + "/.worktrees/feat-w")
                + bash(30, "t2", MERGE + " 77 --squash", "MERGED"))
        self.assertEqual(landed([builder(10, 20), builder(11, 12, worktree="feat-w")], main), (21.0, "77"))

    def test_branch_shared_by_two_tasks_links_neither_by_branch(self):
        shared = "branch feat/shared-labels"
        main = (bash(25, "t1", "cd %s && gh pr create --head feat/shared-labels --fill" % REPO,
                     "https://github.com/o/backend/pull/76")
                + bash(28, "t2", MERGE + " 76 --squash", "MERGED")
                + bash(30, "t3", MERGE + " feat/shared-labels --squash", "MERGED")
                + bash(35, "t4", "cd %s/.worktrees/feat-x && gh pr create --fill" % REPO,
                       "https://github.com/o/backend/pull/77")
                + bash(40, "t5", MERGE + " 77 --squash", "MERGED"))
        runs = [builder(10, 20, extra=shared), builder(11, 12, worktree="feat-w", extra=shared)]
        self.assertEqual((landed(runs, main), landed(runs, main, base="feat-w")), ((31.0, "77"), None))

    def test_pr_found_by_its_head_branch_lands(self):
        main = (bash(25, "t1", "cd %s && gh pr list --head fix/feat-x-thing --json number -q '.[0].number'" % REPO,
                     "ok\n77")
                + bash(30, "t2", MERGE + " 77 --squash", "MERGED"))
        self.assertEqual(landed_minutes([builder(10, 20, extra="branch fix/feat-x-thing")], main), 21.0)

    def test_number_only_mentioned_in_the_builder_transcript_does_not_land(self):
        run = run_of("builder", [user(10, "workspace: %s/.worktrees/feat-x" % REPO),
                                 user(15, "fix round for https://github.com/o/backend/pull/77"), assistant(20)])
        self.assertIsNone(landed_minutes([run], bash(30, "t1", MERGE + " 77 --squash", "MERGED")))

    def test_earliest_successful_merge_lands(self):
        where = REPO + "/.worktrees/feat-x"
        main = (bash(25, "t1", "cd %s && %s 12" % (where, MERGE), "GraphQL: Pull Request has merge conflicts")
                + bash(30, "t2", "cd %s && %s 12" % (where, MERGE), "MERGED")
                + bash(40, "t3", "cd %s && %s 13" % (where, MERGE), "MERGED"))
        self.assertEqual(landed_minutes([builder(10, 20)], main), 21.0)

    def test_merge_before_the_first_builder_event_does_not_land(self):
        main = bash(5, "t1", MERGE + " 12", "MERGED", cwd=REPO + "/.worktrees/feat-x")
        self.assertIsNone(landed_minutes([builder(10, 20)], main))

    def test_untimed_copy_of_the_result_does_not_hide_the_timed_one(self):
        use, result = bash(30, "t1", MERGE + " 12", "MERGED", cwd=REPO + "/.worktrees/feat-x")
        untimed = {k: v for k, v in result.items() if k != "timestamp"}
        self.assertEqual(landed_minutes([builder(10, 20)], [use, untimed, result]), 21.0)


class MergeOutcomeTest(unittest.TestCase):
    def test_outcome_is_read_from_what_the_merge_reported(self):
        cases = [
            (False, "CI Checks Summary:\n  [ok] Passed: 2\n  [FAIL] Failed: 0\nMERGED 5a1f2e9", True),
            (False, "MERGED 3c4d5e6\nerror: branch 'fix/reader-token-secret-31' not found", True),
            (True, "Exit code 128\nMERGED 7b8c9d0e\nfatal: '.worktrees/x' is not a working tree", True),
            (False, "ok fetched\n2e3f4a5b Remove the dead lookup index (#80) (#86)", True),
            (False, "GraphQL: Pull Request has merge conflicts (mergePullRequest)\nOPEN", False),
            (False, "Run the following to resolve the merge conflicts locally:\n  gh pr checkout 18", False),
            (False, "To have the pull request merged after all the requirements have been met, add the `--auto` "
                    "flag.", False),
            (True, "PreToolUse:Bash hook error: Refused by merge-gate: this command lands a change", False),
            (False, "X Pull request #12 is not mergeable: the base branch policy prohibits the merge.", False),
            (True, "Exit code 1", False),
        ]
        self.assertEqual([(f, o, merge_succeeded(f, o)) for f, o, _ in cases], cases)


class TasksTest(unittest.TestCase):
    def test_tasks_are_this_repository_worktrees_with_a_builder_event_in_the_window(self):
        start, end = window()
        other = run_of("builder", [user(10, "workspace: /r/frontend/.worktrees/feat-y"), assistant(20)])
        later = run_of("builder", [user(40, "workspace: %s/.worktrees/feat-z" % REPO)])
        tasks = tasks_of(REPO, [builder(10, 20), other, later], start, later.first)
        self.assertEqual(sorted(tasks), ["feat-x"])

    def test_reviewers_join_by_worktree_path_or_branch_token(self):
        start, end = window()
        by_path = run_of("reviewer", [user(21, "review %s/.worktrees/feat-x" % REPO)])
        by_branch = run_of("reviewer", [user(22, "review branch fix/feat-x-thing", cwd=REPO)])
        elsewhere = run_of("reviewer", [user(23, "review branch fix/feat-x-thing", cwd="/r/frontend")])
        runs = [builder(10, 20, extra="branch fix/feat-x-thing"), by_path, by_branch, elsewhere]
        self.assertEqual(len(tasks_of(REPO, runs, start, end)["feat-x"]["reviewers"]), 2)


    def test_worktree_name_ends_before_trailing_punctuation(self):
        start, end = window()
        runs = [builder(10, 20, worktree="sms-chain;"), builder(30, 40, worktree="sms-chain")]
        self.assertEqual(sorted(tasks_of(REPO, runs, start, end)), ["sms-chain"])

    def test_branch_is_the_one_named_for_the_task_worktree(self):
        where = REPO + "/.worktrees/feat-x"
        cases = {"branch fix/feat-x-thing\n": "fix/feat-x-thing", "branch worktrees/i-67\n": None,
                 "branch 141-146 then branch: feat/y-z\n": "feat/y-z",
                 "Slices 1-2 run on branch feat/other\nworkspace: %s (branch feat/x-own)\n" % where: "feat/x-own",
                 "Slices 1-2 run on branch feat/other\nworkspace: %s\n" % where: None}
        self.assertEqual({p: branch_of(p, where) for p in cases}, cases)


class TierTest(unittest.TestCase):
    def test_size_line_decides_the_tier(self):
        cases = {
            "files:\n- a.py\n": "no size: line",
            "size: about 200-300 lines added; one subsystem\n": "medium",
            "size: ~600 lines\n": "large",
            "size: two subsystems, 150 lines\n": "large",
            "size: one config line\n": "small",
            "size: large (migration)\n": "large",
        }
        self.assertEqual({p: tier(p) for p in cases}, cases)


class ReportTest(unittest.TestCase):
    def test_table_rows_and_closing_line(self):
        start, end = window()
        runs = [builder(10, 20), builder(12, 14, worktree="feat-w")]
        events = pr_events(bash_calls(bash(30, "t1", MERGE + " 12", "MERGED", cwd=REPO + "/.worktrees/feat-x")))
        lines = report(REPO, rows_of(REPO, tasks_of(REPO, runs, start, end), *events), start, end).splitlines()
        self.assertEqual(lines[2:], [
            "all | 1 | 21 | 10 | 1.0 | 0.0",
            "no size: line | 1 | 21 | 10 | 1.0 | 0.0",
            "window 2026-11-17 06:00 .. 2026-11-17 06:59 UTC; tasks with a builder event in it 2, "
            "of them without a landing 1",
        ])


class TaskTranscriptTest(unittest.TestCase):
    """A synthetic task in the shape of a builder and main-session transcript: first builder event
    2026-11-17T06:21:41.319Z, last 06:22:43.567Z; the landing merge result 06:37:40.375Z (a refused
    `gh pr create` at 06:22:49 and a successful one at 06:22:59 precede it)."""

    def test_lead_and_activity_minutes(self):
        with open(FIXTURE) as fh:
            data = json.load(fh)
        run = run_of("builder", data["builder"])
        tasks = tasks_of("/home/u/Repos/example-service", [run], run.first, run.last + 1)
        merge = landings_of("/home/u/Repos/example-service", tasks, *pr_events(bash_calls(data["main"])))["ci-red"]
        task = tasks["ci-red"]
        self.assertEqual((round((merge.stamp - run.first) / 60, 3), round((run.last - run.first) / 60, 3), tier(task["prompt"])),
                         (15.984, 1.037, "small"))


if __name__ == "__main__":
    unittest.main()
