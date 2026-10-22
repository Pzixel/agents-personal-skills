import json
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import dup_policy  # noqa: E402

HOOKS_JSON = Path(__file__).resolve().parents[2] / "hooks" / "hooks.json"


class DuplicatesTest(unittest.TestCase):
    def test_a_plugin_name_and_a_bare_name_are_one_name(self):
        lines = dup_policy.duplicates([("agent", "orch:builder", "plugin"), ("agent", "builder", "user")])
        self.assertEqual(lines, ["duplicate agent `builder`: plugin | user"])

    def test_a_skill_and_an_agent_of_one_name_do_not_collide(self):
        self.assertEqual(dup_policy.duplicates([("skill", "x", "a"), ("agent", "x", "b")]), [])

    def test_one_source_listing_a_name_twice_is_not_a_duplicate(self):
        self.assertEqual(dup_policy.duplicates([("skill", "x", "a"), ("skill", "x", "a")]), [])


class ScanTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.config = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, rel, text="---\nname: %s\n---\n"):
        path = self.config / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    def settings(self, **values):
        self.write("settings.json", json.dumps(values))

    def names(self):
        return sorted((k, n) for k, n, _ in dup_policy.scan(str(self.config)))

    def test_the_frontmatter_name_wins_over_the_directory_name(self):
        self.write("skills/dir/SKILL.md", "---\nname: real\n---\n")
        self.assertEqual(self.names(), [("skill", "real")])

    def test_a_skills_dir_plugin_exposes_its_skills_and_agents(self):
        self.write("skills/p/.claude-plugin/plugin.json", "{}")
        self.write("skills/p/skills/s/SKILL.md", "---\nname: s\n---\n")
        self.write("skills/p/agents/a.md", "---\nname: a\n---\n")
        self.assertEqual(self.names(), [("agent", "a"), ("skill", "s")])

    def test_only_enabled_plugins_count(self):
        for plugin in ("on", "off"):
            self.write("cache/%s/skills/%s/SKILL.md" % (plugin, plugin), "---\nname: %s\n---\n" % plugin)
        installed = {p + "@m": [{"installPath": str(self.config / "cache" / p)}] for p in ("on", "off")}
        self.write("plugins/installed_plugins.json", json.dumps({"plugins": installed}))
        self.settings(enabledPlugins={"on@m": True, "off@m": False})
        self.assertEqual(self.names(), [("skill", "on")])

    def test_synced_skills_count_unless_sync_is_off(self):
        self.write("skills/synced/x/manifest.json", json.dumps({"skills": [{"name": "s"}]}))
        self.assertEqual(self.names(), [("skill", "s")])
        self.settings(syncClaudeAiSkills=False)
        self.assertEqual(self.names(), [])

    def test_a_dangling_agent_link_is_skipped(self):
        (self.config / "agents").mkdir()
        os.symlink(self.config / "missing.md", self.config / "agents" / "gone.md")
        self.assertEqual(self.names(), [])


class HookWiringTest(unittest.TestCase):
    def test_session_start_runs_the_duplicate_hook(self):
        with open(HOOKS_JSON) as fh:
            entries = json.load(fh)["hooks"]["SessionStart"]
        commands = [h["command"] for e in entries for h in e["hooks"]]
        self.assertTrue(any(re.search(r"hooks/dup-skills-hook\.py", c) for c in commands))


if __name__ == "__main__":
    unittest.main()
