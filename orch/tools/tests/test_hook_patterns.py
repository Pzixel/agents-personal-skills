"""deny-destructive fails closed; task-type-checklist fires the incident checklist on live failures."""

import json
import shutil
import tempfile
import pathlib
import subprocess
import unittest

HOOKS = pathlib.Path(__file__).resolve().parents[2] / "hooks"


def run(hook, payload):
    return subprocess.run(
        ["python3", str(HOOKS / hook)], input=json.dumps(payload), capture_output=True, text=True
    )


def incident(prompt):
    return "Incident checklist" in run("task-type-checklist.py", {"prompt": prompt}).stdout


def destructive_decision(hooks_dir, stdin):
    out = subprocess.run(
        ["python3", str(hooks_dir / "deny-destructive.py")], input=stdin, capture_output=True, text=True
    )
    return out.returncode, json.loads(out.stdout)["hookSpecificOutput"]["permissionDecision"]


class DenyDestructiveFailsClosed(unittest.TestCase):
    def test_bad_stdin_is_denied(self):
        self.assertEqual(destructive_decision(HOOKS, "not json"), (0, "deny"))

    def test_malformed_params_value_is_denied(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            shutil.copytree(HOOKS, root / "hooks")
            shutil.copytree(HOOKS.parent / "tools", root / "tools", ignore=shutil.ignore_patterns("tests"))
            (root / "tools" / "orchestrate-params.json").write_text(
                json.dumps({"destructive": {"ours_paths": 5}})
            )
            payload = json.dumps({"tool_input": {"command": "rm tmp/x"}})
            self.assertEqual(destructive_decision(root / "hooks", payload), (0, "deny"))


class IncidentChecklist(unittest.TestCase):
    def test_table(self):
        for prompt, fires in [
            ("прод упал, 503 на /statistics", True),
            ("сервис не отвечает с утра", True),
            ("pods in CrashLoopBackOff after deploy", True),
            ("the service is down.", True),
            ("страница лежит в src/pages", False),
            ("тест упал в tests/foo", False),
            ("спроси у коллег про сервис", False),
            ("почему медленно работает выборка", False),
            ("напиши простой скрипт для выгрузки", False),
            ("конфиг лежит в папке deploy", False),
            ("convert the notes to markdown please", False),
            ("leave more headroom on the zoom panel", False),
            ("port 15030 and PR #503 review", False),
            ("an incidental change in the parser", False),
            ("тест упал в CI на ветке", False),
            ("ответ не отвечает требованиям задачи", False),
        ]:
            with self.subTest(prompt=prompt):
                self.assertEqual(incident(prompt), fires)


if __name__ == "__main__":
    unittest.main()
