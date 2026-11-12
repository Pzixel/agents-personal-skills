import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SKILL = ROOT / "orch" / "skills" / "worker-rules"
sys.path.insert(0, str(SKILL))

import assemble  # noqa: E402

B, E = "<!-- BEGIN owner only -->", "<!-- END owner only -->"
AGENTS = ["builder", "reviewer", "scout", "prod-probe", "watcher"]


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def body_lines(parts):
    """The delivered lines: parts joined, begin and closing lines removed."""
    out = []
    for p in parts:
        out.extend(p.split("\n")[:-1])
    return out[1:]


class OwnerOnlyRegions(unittest.TestCase):
    def test_well_formed_regions_are_removed(self):
        lines = ["keep 1", B, "owner 1", E, "keep 2", B, "owner 2", "owner 3", E, "keep 3"]
        self.assertEqual(assemble.drop_owner_only(lines), ["keep 1", "keep 2", "keep 3"])

    def test_malformed_marker_set_delivers_the_text_whole(self):
        for lines in (["a", B, "b"], ["a", E, "b", B, "c"], ["a", B, B, "b", E, E], ["a", B, "b", E, E]):
            with self.subTest(lines=lines):
                self.assertEqual(assemble.drop_owner_only(lines), lines)

    def test_installer_block_is_judged_apart_from_the_text_around_it(self):
        text = "\n".join(["private", B, "private tail", assemble.BLOCK_BEGIN,
                          "public worker", B, "public owner", E, assemble.BLOCK_END, "after"])
        self.assertEqual(assemble.worker_lines(text),
                         ["private", B, "private tail", assemble.BLOCK_BEGIN, "public worker",
                          assemble.BLOCK_END, "after"])


class Sources(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.tmp = Path(temp.name)
        self.home = self.tmp / "home"
        self.config = self.home / ".claude"
        self.repo = self.tmp / "repo"
        (self.repo / ".git").mkdir(parents=True)

    def lines(self, cwd=None):
        return assemble.rules_lines(str(self.config), str(cwd or self.repo), str(self.home))

    def run_assembler(self, cwd, **env):
        return subprocess.run([sys.executable, str(SKILL / "assemble.py"), "1"], cwd=cwd,
                              env=dict(os.environ, CLAUDE_CONFIG_DIR=str(self.config), HOME=str(self.home), **env),
                              capture_output=True)

    def test_user_imports_are_inlined_one_level_once(self):
        write(self.home / "x y" / "A.md", "a1\n@deeper.md\n@no-such.md\na2\n")
        write(self.home / "x y" / "deeper.md", "never\n")
        write(self.config / "CLAUDE.md", "top\n@~/x\\ y/A.md\n@%s\nend\n" % str(self.home / "x y" / "A.md").replace(" ", "\\ "))
        self.assertEqual(self.lines(), ["top", "a1", "@no-such.md", "a2", "end"])

    def test_an_at_line_that_names_no_file_or_is_more_than_a_path_stays_text(self):
        write(self.home / "A.md", "a1\n")
        write(self.config / "CLAUDE.md", "@~/missing.md\n@~/A.md is the file\n@team, read this\n")
        self.assertEqual(self.lines(), ["@~/missing.md", "@~/A.md is the file", "@team, read this"])

    def test_project_imports_resolve_inside_the_repository_only(self):
        write(self.repo / "AGENTS.md", "project rule\n")
        write(self.home / "secret", "token\n")
        write(self.repo / "CLAUDE.md", "@AGENTS.md\n@~/secret\n")
        sub = self.repo / "src"
        sub.mkdir()
        self.assertEqual(self.lines(sub),
                         ["project rule", "orch worker rules: import skipped, outside the repository: @~/secret"])

    def test_a_root_file_linked_outside_the_repository_is_skipped_with_a_notice(self):
        write(self.home / "secret.md", "token\n")
        (self.repo / "CLAUDE.md").symlink_to(self.home / "secret.md")
        self.assertEqual(self.lines(), ["orch worker rules: file skipped, outside the repository: %s"
                                        % Path(os.path.realpath(self.repo), "CLAUDE.md")])

    def test_undecodable_bytes_arrive_as_replacement_characters(self):
        write(self.home / "A.md", "").write_bytes(b"a1 \xff\n")
        write(self.config / "CLAUDE.md", "").write_bytes(b"top \xfe\n@~/A.md\nend\n")
        self.assertEqual(self.lines(), ["top �", "a1 �", "end"])

    @unittest.skipIf(os.geteuid() == 0, "root opens any file")
    def test_an_unreadable_source_leaves_a_notice_in_the_text(self):
        user = write(self.config / "CLAUDE.md", "top\n")
        user.chmod(0)
        self.addCleanup(user.chmod, 0o600)
        self.assertEqual(self.lines(), ["orch worker rules: file not readable: %s" % user])

    def test_no_sources_still_prints_the_sentinels(self):
        outside = self.tmp / "plain"
        outside.mkdir()
        run = self.run_assembler(outside)
        self.assertEqual((run.returncode, run.stdout),
                         (0, b"orch worker rules: begin\norch worker rules: part 1 of 1 end\n"))

    def test_text_is_written_as_utf8_under_a_non_utf8_locale(self):
        write(self.config / "CLAUDE.md", "rule → правило\n")
        run = self.run_assembler(self.repo, LC_ALL="C", LANG="C", PYTHONUTF8="0", PYTHONIOENCODING="ascii")
        self.assertEqual((run.returncode, run.stdout.decode("utf-8")),
                         (0, "orch worker rules: begin\nrule → правило\n"
                             "orch worker rules: part 1 of 1 end\n"))

    def test_a_failed_assembly_exits_zero_without_a_closing_line(self):
        (self.tmp / "gone").mkdir()
        run = subprocess.run(["sh", "-c", 'cd gone && rmdir ../gone && exec "$0" "$1" 1',
                              sys.executable, str(SKILL / "assemble.py")], cwd=self.tmp, capture_output=True)
        out = run.stdout.decode("utf-8")
        self.assertEqual((run.returncode, out.startswith("orch worker rules: assembly failed: "), " end\n" in out),
                         (0, True, False))


class Parts(unittest.TestCase):
    TEXT = ["ascii line %d %s" % (i, "x" * 60) for i in range(600)] + \
           ["кириллица %d %s" % (i, "ж" * 50) for i in range(600)] + ["ü" * 40000]

    def test_parts_stay_under_the_byte_cap_and_carry_the_text_in_order(self):
        ps = assemble.parts(self.TEXT)
        self.assertTrue(all(len(p.encode("utf-8")) + 1 < 28000 for p in ps))
        self.assertEqual(ps[0].split("\n")[0], "orch worker rules: begin")
        self.assertEqual([p.split("\n")[-1] for p in ps],
                         ["orch worker rules: part %d of %d end" % (i, len(ps)) for i in range(1, len(ps) + 1)])
        lines = body_lines(ps)
        self.assertEqual(lines[:1200], self.TEXT[:1200])
        self.assertEqual("".join(lines[1200:]), self.TEXT[1200])

    def test_a_command_past_the_last_part_says_so(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(assemble.output(2, d, d, d), "orch worker rules: command 2 unused (1 parts)")


class Pins(unittest.TestCase):
    def test_installer_block_markers_match(self):
        sh = (ROOT / "install.sh").read_text()
        self.assertEqual(re.search(r'^begin="(.*)"$', sh, re.M).group(1), assemble.BLOCK_BEGIN)
        self.assertEqual(re.search(r'^end="(.*)"$', sh, re.M).group(1), assemble.BLOCK_END)

    def test_installer_and_assembler_resolve_an_import_line_alike(self):
        with tempfile.TemporaryDirectory() as d:
            home, config = Path(d) / "home", Path(d) / "config"
            target = write(home / "x y" / "AGENTS.md", assemble.BLOCK_BEGIN + "\n" + assemble.BLOCK_END + "\n")
            line = "@~/x\\ y/AGENTS.md"
            write(config / "CLAUDE.md", line + "\n")
            env = dict(os.environ, HOME=str(home), CLAUDE_CONFIG_DIR=str(config))
            run = subprocess.run(["sh", str(ROOT / "install.sh"), "claude"], env=env, capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stderr)
            via = re.search(r"already imports the rules block via (.*)$", run.stdout, re.M).group(1)
            self.assertEqual(via, str(target))
            self.assertEqual(assemble.import_target(line, str(config), str(home)), str(target))

    def test_agents_preload_the_rules_and_guard_on_the_assembler_sentinels(self):
        closing = assemble.CLOSING.format(n="N", m="M")
        for name in AGENTS:
            with self.subTest(agent=name):
                text = (ROOT / "orch" / "agents" / (name + ".md")).read_text()
                head = text.split("\n---\n", 1)[0]
                self.assertIn("\nomitClaudeMd: true", head)
                self.assertIn("\nskills: [worker-rules]", head)
                self.assertIn("`%s`" % assemble.BEGIN, text)
                self.assertIn("`%s`" % closing, text)
                self.assertIn("`WORKER RULES MISSING`", text)

    def test_the_skill_commands_deliver_a_four_part_text_whole(self):
        commands = re.findall(r"^!`(.*)`$", (SKILL / "SKILL.md").read_text(), re.M)
        text = ["rule %d %s" % (i, "ж" * 60) for i in range(700)]
        with tempfile.TemporaryDirectory() as d:
            write(Path(d) / "CLAUDE.md", "\n".join(text) + "\n")
            env = dict(os.environ, CLAUDE_CONFIG_DIR=d, HOME=d, CLAUDE_PLUGIN_ROOT=str(ROOT / "orch"))
            outs = [subprocess.run(["sh", "-c", c], cwd=d, env=env, capture_output=True, text=True).stdout[:-1]
                    for c in commands]
        self.assertEqual([o.split("\n")[-1] for o in outs],
                         ["orch worker rules: part %d of 4 end" % n for n in range(1, 5)])
        self.assertEqual(body_lines(outs), text)

    def test_public_rules_files_hold_well_formed_owner_only_regions(self):
        for name in ("always-on.md", "always-on-claude.md"):
            with self.subTest(file=name):
                lines = (ROOT / "rules" / name).read_text().splitlines()
                marks = [l for l in lines if l in (B, E)]
                self.assertTrue(marks)
                self.assertEqual(marks, [B, E] * (len(marks) // 2))
                self.assertEqual(assemble.worker_lines("\n".join(lines)).count(B), 0)


class RealRulesFiles(unittest.TestCase):
    """The filter on the marked public files as they are in the tree."""

    PROBES = {
        "always-on.md": ["- **The opening is the bottom line:**",
                         "- **A figure or verdict the reader already holds from me**",
                         "- An owner statement that contradicts a conclusion",
                         "- A question to the owner is raised when its decision point"],
        "always-on-claude.md": ["- A `user` or `feedback` memory",
                                "- A plan goes to the owner only when",
                                "- An owner question is raised with one `PushNotification`",
                                "- One monitor per subject"],
    }

    DELIVERED = {
        "always-on.md": {"## Working contract": "**Progressive disclosure",
                         "## Replies": "- A reply answers every part of the message",
                         "## Authority and evidence": "- A tunable (retention, threshold",
                         "## Measurement": "- A wait or window that delays a result",
                         "## Scope": "- A task is accepted by its purpose"},
        "always-on-claude.md": {"## Past sessions": "- A past Claude Code session named by ID",
                                "## Hooks and gates": "- Hooks enforce the gates"},
    }

    def test_each_delivered_section_keeps_its_worker_rule(self):
        for name, probes in self.DELIVERED.items():
            with self.subTest(file=name):
                found, section = {}, None
                for line in assemble.worker_lines((ROOT / "rules" / name).read_text()):
                    if line.startswith("## "):
                        section = line
                        found[section] = []
                    elif section:
                        found[section].append(line)
                self.assertEqual({s: any(l.startswith(p) for l in found.get(s, [])) for s, p in probes.items()},
                                 {s: True for s in probes})
                self.assertEqual(sorted(found), sorted(probes))

    def test_owner_only_bullets_are_dropped_and_every_other_bullet_delivered(self):
        for name, probes in self.PROBES.items():
            with self.subTest(file=name):
                text = (ROOT / "rules" / name).read_text()
                regions = re.findall(re.escape(B) + r"\n(.*?)\n" + re.escape(E), text, re.S)
                owner = [l for r in regions for l in r.split("\n") if l.startswith("- ")]
                kept = [l for l in text.split("\n") if l.startswith("- ") and l not in owner]
                delivered = [l for l in assemble.worker_lines(text) if l.startswith("- ")]
                self.assertEqual([[p for p in probes if any(l.startswith(p) for l in r.split("\n"))]
                                  for r in regions], [[p] for p in probes])
                self.assertEqual(delivered, kept)

    def test_past_sessions_reaches_the_agents(self):
        text = (ROOT / "rules" / "always-on-claude.md").read_text()
        self.assertIn("## Past sessions", assemble.worker_lines(text))


if __name__ == "__main__":
    unittest.main()
