"""Cases for tmp-evict: the pure eviction policy, reference extraction and its join to units, and the workflow on
real temporary trees.

Contract (orch/tools/tmp_evict.py docstring): a repository tmp entry idle longer than its size class allows is
moved to <tmp>/.evicted/ with a restore row, purged after the grace period unless used again, restored when
used; KEEP directories, entries holding git-tracked files, entries a tracked file names, symbolic link entries
and symbolic link tmp directories are never touched; no entry sits under .evicted/ without a row; the scheduled
form runs only when enabled, at most once per 24 hours, one pass at a time.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.realpath(__file__))))

import tmp_evict  # noqa: E402
import tmp_evict_policy as P  # noqa: E402

# Confinement while this module's tests run: HOME, CLAUDE_CONFIG_DIR and every temporary directory of the process
# (and of the CLI subprocesses, which inherit the environment) live under ROOT, and a transcript store or
# repository the tool resolves outside ROOT fails the test. tearDownModule restores all of it, so sibling modules
# of a single-process discover run see the environment they started with.
ROOT = None
_SAVED = {}
_GUARDED = ("stores", "main_checkouts")


def _confined(fn):
    def wrapper(*args, **kwargs):
        found = fn(*args, **kwargs)
        outside = [p for p in found if not os.path.realpath(p).startswith(ROOT + "/")]
        if outside:
            raise AssertionError("%s resolved paths outside the test tree: %s" % (fn.__name__, outside))
        return found
    return wrapper


def setUpModule():
    global ROOT
    ROOT = os.path.realpath(tempfile.mkdtemp(prefix="tmp-evict-test-"))
    os.makedirs(ROOT + "/home")
    for key, value in (("HOME", ROOT + "/home"), ("CLAUDE_CONFIG_DIR", ROOT + "/config"), ("TMPDIR", ROOT)):
        _SAVED[key] = os.environ.get(key)
        os.environ[key] = value
    _SAVED["tempdir"] = tempfile.tempdir
    tempfile.tempdir = ROOT
    for name in _GUARDED:
        _SAVED[name] = getattr(tmp_evict, name)
        setattr(tmp_evict, name, _confined(_SAVED[name]))


def tearDownModule():
    for name in _GUARDED:
        setattr(tmp_evict, name, _SAVED[name])
    tempfile.tempdir = _SAVED["tempdir"]
    for key in ("HOME", "CLAUDE_CONFIG_DIR", "TMPDIR"):
        if _SAVED[key] is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = _SAVED[key]
    for d, dirs, _ in os.walk(ROOT):
        for name in dirs:
            os.chmod(os.path.join(d, name), 0o755)
    shutil.rmtree(ROOT)

DAY = 86400.0
NOW = 1_800_000_000.0
CFG = {"big_bytes": 100, "big_idle_days": 14, "small_idle_days": 180, "grace_days": 14}


def unit(path="/r/tmp/zq", size=10, idle=1.0, ref_idle=None, keep=False, recent=False, tracked=False, link=False):
    return P.Unit(path, size, 1, None if idle is None else NOW - idle * DAY,
                  None if ref_idle is None else NOW - ref_idle * DAY, set(), keep, recent, tracked, link)


def entry(path="/r/tmp/.evicted/zq", age=15.0, used_after=False, ref_after=False, recent=False, free=True, row=True,
          state="evicted"):
    at = NOW - age * DAY
    return P.Evicted(path, "/r/tmp/zq", at if row else None, at + (60 if used_after else -60),
                     at + 60 if ref_after else None, recent, free, state if row else None)


def kinds(units=(), evicted=(), complete=True):
    return [(d.kind, d.path) for d in P.decide(list(units), list(evicted), NOW, CFG, complete)]


class DecideTest(unittest.TestCase):
    def test_size_classes_use_their_own_idle_limit(self):
        got = kinds([unit("/r/tmp/big", 100, 15), unit("/r/tmp/bigfresh", 100, 13),
                     unit("/r/tmp/small", 99, 181), unit("/r/tmp/smallmid", 99, 100)])
        self.assertEqual(got, [("evict", "/r/tmp/big"), ("evict", "/r/tmp/small")])

    def test_a_later_reference_keeps_an_old_unit(self):
        self.assertEqual(kinds([unit(size=100, idle=40, ref_idle=3)]), [])

    def test_keep_tracked_unread_and_recently_named_units_are_never_evicted(self):
        got = kinds([unit("/a", 100, 40, keep=True), unit("/b", 100, 40, tracked=True),
                     unit("/c", 100, None), unit("/d", 100, 40, recent=True)])
        self.assertEqual(got, [])

    def test_a_symbolic_link_unit_is_held_not_evicted(self):
        self.assertEqual(kinds([unit("/r/tmp/zqlink", 100, 400, link=True)]), [("hold", "/r/tmp/zqlink")])

    def test_evicted_entry_is_purged_after_grace_restored_when_used_held_when_blocked(self):
        got = kinds(evicted=[entry("/r/tmp/.evicted/old", 15), entry("/r/tmp/.evicted/young", 13),
                             entry("/r/tmp/.evicted/used", 20, used_after=True),
                             entry("/r/tmp/.evicted/read", 20, ref_after=True),
                             entry("/r/tmp/.evicted/taken", 20, used_after=True, free=False),
                             entry("/r/tmp/.evicted/norow", row=False),
                             entry("/r/tmp/.evicted/named", 20, recent=True)])
        self.assertEqual(got, [("purge", "/r/tmp/.evicted/old"), ("restore", "/r/tmp/.evicted/used"),
                               ("restore", "/r/tmp/.evicted/read"), ("held", "/r/tmp/.evicted/taken"),
                               ("hold", "/r/tmp/.evicted/norow")])

    def test_an_entry_held_for_good_is_never_purged(self):
        self.assertEqual(kinds(evicted=[entry(age=400, state="held")]), [])

    def test_a_deletion_stopped_part_way_is_retried_not_restored(self):
        self.assertEqual(kinds(evicted=[entry(age=15, used_after=True, state="purging")]),
                         [("purge", "/r/tmp/.evicted/zq")])

    def test_an_unconfirmed_eviction_moves_back(self):
        self.assertEqual(kinds(evicted=[entry(age=0.001, used_after=True, state="pending")]),
                         [("restore", "/r/tmp/.evicted/zq")])

    def test_unread_transcripts_stop_eviction_and_new_purge_but_not_restore(self):
        got = kinds([unit("/r/tmp/big", 100, 15)],
                    [entry("/r/tmp/.evicted/old", 15), entry("/r/tmp/.evicted/used", 20, used_after=True)],
                    complete=False)
        self.assertEqual(got, [("restore", "/r/tmp/.evicted/used")])

    def test_a_tracked_name_turns_only_an_eviction_into_a_hold(self):
        decisions = [P.Decision("evict", "/r/tmp/zqa", "idle"), P.Decision("evict", "/r/tmp/zqb", "idle"),
                     P.Decision("purge", "/r/tmp/.evicted/zqa", "grace")]
        got = P.exempt_named(decisions, {"/r/tmp/zqa": "file notes.md", "/r/tmp/.evicted/zqa": "file notes.md"})
        self.assertEqual([(d.kind, d.path) for d in got],
                         [("hold", "/r/tmp/zqa"), ("evict", "/r/tmp/zqb"), ("purge", "/r/tmp/.evicted/zqa")])


class RowTest(unittest.TestCase):
    def test_open_evictions_follow_the_row_states(self):
        rows = "".join([
            P.row(NOW, "pending", "/r/tmp/zqa", "/r/tmp/.evicted/zqa", 1, 1, [], set()),
            P.row(NOW + 1, "evicted", "/r/tmp/zqa", "/r/tmp/.evicted/zqa", 1, 1, [], set()),
            P.row(NOW, "pending", "/r/tmp/zqb", "/r/tmp/.evicted/zqb", 1, 1, [], set()),
            P.row(NOW, "pending", "/r/tmp/zqc", "/r/tmp/.evicted/zqc", 1, 1, [], set()),
            P.row(NOW, "failed", "/r/tmp/zqc", "/r/tmp/.evicted/zqc", "", "", [], set()),
            P.row(NOW, "evicted", "/r/tmp/zqd", "/r/tmp/.evicted/zqd", 1, 1, [], set()),
            P.row(NOW + 5, "held", "/r/tmp/zqd", "/r/tmp/.evicted/zqd", "", "", [], set()),
            P.row(NOW, "evicted", "/r/tmp/zqe", "/r/tmp/.evicted/zqe", 1, 1, [], set()),
            P.row(NOW + 4, "purging", "/r/tmp/zqe", "/r/tmp/.evicted/zqe", "", "", [], set()),
            P.row(NOW + 5, "purge-failed", "/r/tmp/zqe", "/r/tmp/.evicted/zqe", "", "", [], set())])
        self.assertEqual(P.open_evictions(rows), {
            "/r/tmp/.evicted/zqa": ("/r/tmp/zqa", NOW + 1, "evicted"),
            "/r/tmp/.evicted/zqb": ("/r/tmp/zqb", NOW, "pending"),
            "/r/tmp/.evicted/zqd": ("/r/tmp/zqd", NOW, "held"),
            "/r/tmp/.evicted/zqe": ("/r/tmp/zqe", NOW, "purge-failed")})

    def test_the_restore_line_survives_a_quote_in_the_name(self):
        line = P.row(NOW, "evicted", "/r/tmp/it's", "/r/tmp/.evicted/it's", 1, 1, [], set())
        self.assertIn("to restore: mv '/r/tmp/.evicted/it'\"'\"'s' '/r/tmp/it'\"'\"'s'", line)


class ReferenceTest(unittest.TestCase):
    TMPS = {"/r/tmp": "/r/tmp", "/r/.worktrees/w/tmp": "/r/.worktrees/w/tmp", "/r/vendor/n/tmp": "/r/vendor/n/tmp"}

    def keys(self, text, cwd="/r"):
        return sorted(k for kind, v in P.refs_of_text(text, cwd, "/h") if kind == "path"
                      for k in P.unit_keys(v, self.TMPS))

    def names(self, text, cwd="/r"):
        return sorted(v for kind, v in P.refs_of_text(text, cwd, "/h") if kind == "name")

    def test_absolute_relative_home_nested_and_evicted_paths_map_to_units(self):
        self.assertEqual(self.keys("cat /r/tmp/zqa/x.txt tmp/zqb ../r/.worktrees/w/tmp/zqc/d "
                                   "/r/vendor/n/tmp/zqe /r/tmp/.evicted/zqf/g", cwd="/r"),
                         ["/r/.worktrees/w/tmp/zqc", "/r/tmp/.evicted/zqf", "/r/tmp/zqa", "/r/tmp/zqb",
                          "/r/vendor/n/tmp/zqe"])
        self.assertEqual([v for kind, v in P.refs_of_text("ls ~/p/tmp/zqz", "/r", "/h") if kind == "path"],
                         ["/h/p/tmp/zqz"])

    def test_unresolvable_prefix_names_every_unit_of_that_name(self):
        line = "E=/o/p; cd /x && cp $E/tmp/zq-silver/f.bin /x/f.bin"
        self.assertEqual(list(P.refs_of_text(line, "/x", "/h")), [("name", "zq-silver")])
        self.assertEqual(self.names('ls $(pwd)/tmp/zqa "$D"/tmp/zqb "/tmp/zqc" /tmp/zqd'), ["zqa", "zqb"])

    def test_a_relative_token_names_its_unit_whatever_directory_the_command_moved_to(self):
        self.assertEqual(self.names("cd /o/repo2 && ls tmp/zqa/x; git -C /o/repo3 show HEAD:tmp/zqb"),
                         ["zqa", "zqb"])
        self.assertEqual(list(P.refs_of_text("cat tmp/zqc/y", "", "/h")), [("name", "zqc")])

    def test_a_quoted_absolute_path_with_a_space_is_one_reference(self):
        refs = list(P.refs_of_text('cat "/u/My Repos/p/tmp/zqa/x.csv"', "/r", "/h"))
        self.assertIn(("path", "/u/My Repos/p/tmp/zqa/x.csv"), refs)
        self.assertIn(("name", "zqa"), refs)

    def test_scan_reads_tool_inputs_and_cwd_of_a_subagent_transcript(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "s", "subagents", "agent-1.jsonl")
            os.makedirs(os.path.dirname(path))
            with open(path, "w") as fh:
                fh.write('{"type":"assistant","cwd":"/r","sessionId":"S","timestamp":"2026-11-01T00:00:00.250Z",'
                         '"message":{"content":[{"type":"tool_use","name":"Agent","input":{"prompt":"read /r/tmp/zqa"}}]}}\n'
                         '{"type":"assistant","cwd":"/r/tmp/zqb/sub","sessionId":"S","timestamp":"2026-11-02T00:00:00Z",'
                         '"message":{"content":[{"type":"tool_use","name":"Bash","input":{"command":"ls"}}]}}\n')
            _, _, paths, _, cwds, error = tmp_evict.scan_file(path)
        self.assertEqual((paths, cwds, error), ({"/r/tmp/zqa": [1793491200.25, {"S"}],
                                                 "/r/tmp/zqb/sub": [1793577600.0, {"S"}]},
                                                {"/r", "/r/tmp/zqb/sub"}, None))


class JoinTest(unittest.TestCase):
    TMPS = {"/alias/tmp": "/real/tmp", "/real/tmp": "/real/tmp", "/other/tmp": "/other/tmp"}

    def test_a_path_reference_reaches_its_unit_through_an_alias_and_only_that_unit(self):
        paths = P.canonical({"/alias/tmp/zqa/f": [5.0, {"S"}], "/elsewhere/x": [9.0, {"T"}]}, self.TMPS)
        self.assertEqual(P.references(paths, {}, [], ["/real/tmp/zqa"], "zqa"), (5.0, {"S"}, False))
        self.assertEqual(P.references(paths, {}, [], ["/other/tmp/zqa"], "zqa"), (None, set(), False))

    def test_a_name_reference_reaches_every_unit_of_the_name(self):
        names = {"zqb": [7.0, {"T"}]}
        self.assertEqual([P.references({}, names, [], [k], "zqb")[0] for k in ("/real/tmp/zqb", "/other/tmp/zqb")],
                         [7.0, 7.0])

    def test_a_reference_to_the_original_path_after_eviction_reaches_the_evicted_entry(self):
        paths = P.canonical({"/real/tmp/zqc/x": [8.0, {"S"}]}, self.TMPS)
        self.assertEqual(P.references(paths, {}, [], ["/real/tmp/.evicted/zqc", "/real/tmp/zqc"], "zqc")[0], 8.0)

    def test_a_recent_transcript_marks_the_units_it_names(self):
        recent = [(P.canonical({"/alias/tmp/zqd/x": [1.0, set()]}, self.TMPS), {"zqe": [1.0, set()]})]
        self.assertEqual([P.references({}, {}, recent, ["/real/tmp/" + n], n)[2] for n in ("zqd", "zqe", "zqf")],
                         [True, True, False])


def git(repo, *args):
    subprocess.run(["git", "-C", repo] + list(args), check=True, capture_output=True)


class WorkflowTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.root = os.path.realpath(self.dir.name)
        self.repo = self.root + "/repo"
        os.makedirs(self.repo + "/tmp")
        git(self.repo, "init", "-q")
        self.facts = {"paths": {}, "names": {}, "cwds": {self.repo}, "recent": []}

    def tearDown(self):
        for d, dirs, _ in os.walk(self.root):
            for name in dirs:
                os.chmod(os.path.join(d, name), 0o755)
        self.dir.cleanup()

    def make(self, rel, size=10, base=None):
        path = (base or self.repo + "/tmp") + "/" + rel
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as fh:
            fh.write("x" * size)
        return path

    def collect(self):
        units, evicted, walked, _ = tmp_evict.collect(self.facts)
        return {u.path: u for u in units}, {e.path: e for e in evicted}, walked

    def evict(self, name):
        units, evicted, walked = self.collect()
        return tmp_evict.act(P.Decision("evict", self.repo + "/tmp/" + name, "idle"), units, evicted, walked)

    def events(self):
        with open(self.repo + "/tmp/.evicted.tsv") as fh:
            return [line.split("\t")[1] for line in fh]

    def test_an_entry_holding_a_tracked_file_is_not_a_candidate(self):
        self.make("zqt/tracked.txt")
        self.make("zqt/scratch.txt")
        self.make("zqu/scratch.txt")
        git(self.repo, "add", "-f", "tmp/zqt/tracked.txt")
        units, _, _ = self.collect()
        self.assertEqual({p: u.tracked for p, u in units.items()},
                         {self.repo + "/tmp/zqt": True, self.repo + "/tmp/zqu": False})

    def test_a_tmp_directory_git_cannot_list_is_skipped_whole(self):
        os.makedirs(self.repo + "/.worktrees/w")
        with open(self.repo + "/.worktrees/w/.git", "w") as fh:
            fh.write("gitdir: %s/pruned\n" % self.root)
        self.make("zqv/a.txt", base=self.repo + "/.worktrees/w/tmp")
        self.make("zqu/a.txt")
        units, _, _ = self.collect()
        self.assertEqual(sorted(units), [self.repo + "/tmp/zqu"])

    def test_a_symbolic_link_tmp_directory_is_skipped_whole(self):
        os.rmdir(self.repo + "/tmp")
        self.make("zqu/a.txt", base=self.repo + "/data")
        os.symlink(self.repo + "/data", self.repo + "/tmp")
        self.make("zqv/a.txt", base=self.root + "/elsewhere/tmp")
        os.makedirs(self.repo + "/.worktrees")
        os.symlink(self.root + "/elsewhere", self.repo + "/.worktrees/w")
        units, evicted, _ = self.collect()
        self.assertEqual((units, evicted), ({}, {}))

    def test_a_symbolic_link_unit_is_held(self):
        os.makedirs(self.root + "/target")
        os.symlink(self.root + "/target", self.repo + "/tmp/zqln")
        units, evicted, _ = self.collect()
        got = P.decide(list(units.values()), list(evicted.values()), time.time() + 400 * DAY, CFG)
        self.assertEqual([(d.kind, d.path) for d in got], [("hold", self.repo + "/tmp/zqln")])

    def test_a_unit_named_by_a_tracked_file_is_held_and_others_evicted(self):
        self.make("zqkept/a.txt")
        self.make("zqfree/a.txt")
        with open(self.repo + "/notes.md", "w") as fh:
            fh.write("Evidence is retained under tmp/zqkept/ for the record.\n")
        git(self.repo, "add", "notes.md")
        units, evicted, _ = self.collect()
        decisions, named = tmp_evict.plan(list(units.values()), [], time.time() + 400 * DAY, CFG, True)
        self.assertEqual(([(d.kind, d.path) for d in decisions], named),
                         ([("evict", self.repo + "/tmp/zqfree"), ("hold", self.repo + "/tmp/zqkept")],
                          {self.repo + "/tmp/zqkept": "file notes.md"}))

    def test_a_failed_git_grep_keeps_the_unit(self):
        self.assertIsNotNone(tmp_evict.tracked_reference(self.root + "/missing", "zqa"))

    def test_evict_then_restore_round_trip_with_a_restore_line(self):
        self.make("zqu/a.txt")
        self.evict("zqu")
        u, moved = self.repo + "/tmp/zqu", self.repo + "/tmp/.evicted/zqu"
        with open(self.repo + "/tmp/.evicted.tsv") as fh:
            tsv = fh.read()
        self.assertEqual((os.path.lexists(u), os.path.isfile(moved + "/a.txt")), (False, True))
        self.assertIn("to restore: mv %s %s" % (moved, u), tsv)
        _, evicted, walked = self.collect()
        self.assertEqual((evicted[moved].original, evicted[moved].state), (u, "evicted"))
        tmp_evict.act(P.Decision("restore", moved, "used"), {}, evicted, walked)
        self.assertEqual((os.path.isfile(u + "/a.txt"), os.path.lexists(moved)), (True, False))

    def test_a_second_eviction_of_the_same_name_keeps_both_entries(self):
        self.make("zqu/a.txt")
        self.evict("zqu")
        self.make("zqu/b.txt")
        self.evict("zqu")
        held = {n: sorted(os.listdir(self.repo + "/tmp/.evicted/" + n))
                for n in os.listdir(self.repo + "/tmp/.evicted")}
        second = [n for n in held if n != "zqu"]
        self.assertEqual((held.get("zqu"), len(second), [held[n] for n in second]), (["a.txt"], 1, [["b.txt"]]))

    def test_restore_onto_a_taken_name_leaves_the_entry_in_place(self):
        self.make("zqu/a.txt")
        self.evict("zqu")
        _, evicted, walked = self.collect()
        self.make("zqu/new.txt")
        moved = self.repo + "/tmp/.evicted/zqu"
        line = tmp_evict.act(P.Decision("restore", moved, "used"), {}, evicted, walked)
        self.assertEqual((line.startswith("hold "), os.path.isfile(moved + "/a.txt"),
                          sorted(os.listdir(self.repo + "/tmp/zqu"))), (True, True, ["new.txt"]))

    def test_a_held_decision_is_recorded_and_read_back(self):
        self.make("zqu/a.txt")
        self.evict("zqu")
        _, evicted, walked = self.collect()
        moved = self.repo + "/tmp/.evicted/zqu"
        tmp_evict.act(P.Decision("held", moved, "used, original taken"), {}, evicted, walked)
        _, evicted, _ = self.collect()
        self.assertEqual(evicted[moved].state, "held")

    def test_purge_deletes_the_evicted_entry_and_records_it(self):
        self.make("zqu/a.txt")
        self.evict("zqu")
        _, evicted, walked = self.collect()
        moved = self.repo + "/tmp/.evicted/zqu"
        tmp_evict.act(P.Decision("purge", moved, "grace over"), {}, evicted, walked)
        self.assertEqual((os.path.lexists(moved), self.events()), (False, ["pending", "evicted", "purging", "purged"]))

    def test_a_purge_that_stops_part_way_is_retried_and_never_moved_back(self):
        self.make("zqu/a.txt")
        self.make("zqu/ro/b.txt")
        self.evict("zqu")
        moved = self.repo + "/tmp/.evicted/zqu"
        os.chmod(moved + "/ro", 0o555)
        _, evicted, walked = self.collect()
        with self.assertRaises(OSError):
            tmp_evict.act(P.Decision("purge", moved, "grace over"), {}, evicted, walked)
        _, evicted, _ = self.collect()
        got = P.decide([], list(evicted.values()), time.time(), CFG)
        self.assertEqual(([(d.kind, d.path) for d in got], os.path.lexists(self.repo + "/tmp/zqu")),
                         ([("purge", moved)], False))
        os.chmod(moved + "/ro", 0o755)
        _, evicted, walked = self.collect()
        tmp_evict.act(got[0], {}, evicted, walked)
        self.assertEqual((os.path.lexists(moved), self.events()[-1]), (False, "purged"))

    def test_activity_after_the_scan_refuses_eviction_and_purge(self):
        path = self.make("zqu/a.txt")
        units, evicted, walked = self.collect()
        time.sleep(0.01)
        self.make("zqu/b.txt")
        line = tmp_evict.act(P.Decision("evict", self.repo + "/tmp/zqu", "idle"), units, evicted, walked)
        self.assertEqual((line.startswith("keep "), os.path.isfile(path)), (True, True))
        self.evict("zqu")
        moved = self.repo + "/tmp/.evicted/zqu"
        _, evicted, walked = self.collect()
        time.sleep(0.01)
        self.make("c.txt", base=moved)
        line = tmp_evict.act(P.Decision("purge", moved, "grace over"), {}, evicted, walked)
        self.assertEqual((line.startswith("keep "), os.path.isfile(moved + "/a.txt")), (True, True))

    def test_a_tmp_directory_replaced_by_a_link_after_the_scan_refuses_eviction(self):
        self.make("zqu/a.txt", base=self.root + "/outside")  # older than the scanned unit: the re-walk passes
        time.sleep(0.01)
        self.make("zqu/a.txt")
        units, evicted, walked = self.collect()
        os.rename(self.repo + "/tmp", self.repo + "/tmp-old")
        os.symlink(self.root + "/outside", self.repo + "/tmp")
        line = tmp_evict.act(P.Decision("evict", self.repo + "/tmp/zqu", "idle"), units, evicted, walked)
        self.assertEqual((line.startswith("keep "), os.listdir(self.root + "/outside")), (True, ["zqu"]))

    def test_an_unwritable_row_file_leaves_the_unit_in_place(self):
        self.make("zqu/a.txt")
        open(self.repo + "/tmp/.evicted.tsv", "w").close()
        os.chmod(self.repo + "/tmp/.evicted.tsv", 0o444)
        with self.assertRaises(OSError):
            self.evict("zqu")
        self.assertEqual((os.path.isfile(self.repo + "/tmp/zqu/a.txt"),
                          os.listdir(self.repo + "/tmp/.evicted")), (True, []))

    def test_a_failed_rename_is_recorded_and_leaves_no_open_row(self):
        self.make("zqu/a.txt")
        os.makedirs(self.repo + "/tmp/.evicted")
        os.chmod(self.repo + "/tmp/.evicted", 0o555)
        with self.assertRaises(OSError):
            self.evict("zqu")
        _, evicted, _ = self.collect()
        self.assertEqual((self.events(), evicted, os.path.isfile(self.repo + "/tmp/zqu/a.txt")),
                         (["pending", "failed"], {}, True))

    def test_an_eviction_stopped_after_the_move_is_moved_back(self):
        self.make("zqu/a.txt")
        moved = self.repo + "/tmp/.evicted/zqu"
        with open(self.repo + "/tmp/.evicted.tsv", "w") as fh:
            fh.write(P.row(time.time() - 1, "pending", self.repo + "/tmp/zqu", moved, 10, 1, [], set()))
        os.makedirs(self.repo + "/tmp/.evicted")
        os.rename(self.repo + "/tmp/zqu", moved)
        _, evicted, _ = self.collect()
        got = P.decide([], list(evicted.values()), time.time(), CFG)
        self.assertEqual([(d.kind, d.path) for d in got], [("restore", moved)])

    def test_collect_joins_path_name_and_recent_references_to_units(self):
        for name in ("zqa", "zqb", "zqc", "zqd"):
            self.make(name + "/f.txt")
        self.facts["paths"] = {self.repo + "/tmp/zqa/f.txt": [5.0, {"S"}]}
        self.facts["names"] = {"zqb": [7.0, {"T"}]}
        self.facts["recent"] = [({self.repo + "/tmp/zqc/f.txt": [1.0, set()]}, {})]
        units, _, _ = self.collect()
        self.assertEqual({os.path.basename(p): (u.last_ref, u.sessions, u.recent_ref) for p, u in units.items()},
                         {"zqa": (5.0, {"S"}, False), "zqb": (7.0, {"T"}, False), "zqc": (None, set(), True),
                          "zqd": (None, set(), False)})

    def test_a_second_failed_purge_appends_no_second_failure_row(self):
        self.make("zqu/ro/b.txt")
        self.evict("zqu")
        moved = self.repo + "/tmp/.evicted/zqu"
        os.chmod(moved + "/ro", 0o555)
        for _ in range(2):
            _, evicted, walked = self.collect()
            with self.assertRaises(OSError):
                tmp_evict.act(P.Decision("purge", moved, "grace over"), {}, evicted, walked)
        self.assertEqual(self.events(), ["pending", "evicted", "purging", "purge-failed"])

    def test_list_mode_prints_entries_held_for_good(self):
        self.make("zqu/a.txt")
        self.evict("zqu")
        _, evicted, walked = self.collect()
        moved = self.repo + "/tmp/.evicted/zqu"
        tmp_evict.act(P.Decision("held", moved, "used, original taken"), {}, evicted, walked)
        store = os.environ["CLAUDE_CONFIG_DIR"] + "/projects/p"
        os.makedirs(store, exist_ok=True)
        with open(store + "/s.jsonl", "w") as fh:
            fh.write('{"type":"user","cwd":"%s","sessionId":"S"}\n' % self.repo)
        out = subprocess.run([sys.executable, os.path.join(os.path.dirname(os.path.dirname(
            os.path.realpath(__file__))), "tmp-evict"), "--list-keep"], capture_output=True, text=True, timeout=60)
        os.remove(store + "/s.jsonl")
        self.assertEqual([l.strip().split("  ")[1:] for l in out.stdout.splitlines() if "held" in l],
                         [["held for good (original %s/tmp/zqu)" % self.repo, moved]])


class GatherTest(unittest.TestCase):
    def test_gather_merges_transcripts_and_keeps_the_recent_ones_apart(self):
        store = tempfile.mkdtemp()
        line = ('{"type":"assistant","cwd":"/r","sessionId":"%s","timestamp":"2026-11-0%dT00:00:00Z",'
                '"message":{"content":[{"type":"tool_use","name":"Bash","input":{"command":"ls /r/tmp/%s"}}]}}\n')
        for sid, day, name, age in (("A", 1, "zqa", 3 * DAY), ("B", 2, "zqa", 3 * DAY), ("C", 3, "zqb", 0)):
            path = "%s/p/%s.jsonl" % (store, sid)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as fh:
                fh.write(line % (sid, day, name))
            os.utime(path, (time.time() - age, time.time() - age))
        facts, error = tmp_evict.gather([store])
        self.assertEqual((error, facts["paths"], [sorted(p) for p, _ in facts["recent"]]),
                         (None, {"/r/tmp/zqa": [1793577600.0, {"A", "B"}], "/r/tmp/zqb": [1793664000.0, {"C"}]},
                          [["/r/tmp/zqb"]]))


class ScheduledTest(unittest.TestCase):
    def test_second_start_within_a_day_and_a_concurrent_start_do_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            marker, state = d + "/enabled", d + "/state"
            self.assertIsNone(tmp_evict.scheduled_start(marker, state, time.time()))
            open(marker, "w").close()
            first = tmp_evict.scheduled_start(marker, state, time.time())
            concurrent = tmp_evict.scheduled_start(marker, state, time.time() + 2 * DAY)
            first.close()
            again = tmp_evict.scheduled_start(marker, state, time.time() + 3600)
            tomorrow = tmp_evict.scheduled_start(marker, state, time.time() + DAY + 1)
            self.assertEqual((first is not None, concurrent, again, tomorrow is not None), (True, None, None, True))
            tomorrow.close()

    def test_scheduled_call_without_the_marker_exits_at_once_and_writes_nothing(self):
        with tempfile.TemporaryDirectory() as home:
            tool = os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__file__))), "tmp-evict")
            env = dict(os.environ, HOME=home, CLAUDE_CONFIG_DIR=home + "/config")
            done = subprocess.run([sys.executable, tool, "--scheduled"], env=env, capture_output=True, timeout=30)
            self.assertEqual((done.returncode, done.stdout, os.path.lexists(home + "/.claude")), (0, b"", False))

    def test_dry_run_and_apply_together_are_refused(self):
        # An empty HOME and config dir: a regression that applies finds no transcripts, never real data.
        with tempfile.TemporaryDirectory() as home:
            tool = os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__file__))), "tmp-evict")
            env = dict(os.environ, HOME=home, CLAUDE_CONFIG_DIR=home + "/config")
            done = subprocess.run([sys.executable, tool, "--dry-run", "--apply"], env=env, capture_output=True,
                                  timeout=30)
        self.assertEqual(done.returncode, 2)


if __name__ == "__main__":
    unittest.main()
