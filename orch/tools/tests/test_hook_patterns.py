"""deny-destructive fails closed; task-type-checklist fires the incident checklist on live failures."""

import json
import os
import shutil
import tempfile
import pathlib
import subprocess
import time
import unittest

HOOKS = pathlib.Path(__file__).resolve().parents[2] / "hooks"


def run(hook, payload):
    return subprocess.run(
        ["python3", str(HOOKS / hook)], input=json.dumps(payload), capture_output=True, text=True
    )


def incident(prompt):
    return "Incident checklist" in run("task-type-checklist.py", {"prompt": prompt}).stdout


def destructive_decision(hooks_dir, stdin, home=None):
    with tempfile.TemporaryDirectory() as tmp:
        out = subprocess.run(
            ["python3", str(hooks_dir / "deny-destructive.py")], input=stdin, capture_output=True, text=True,
            env=dict(os.environ, HOME=home or tmp),
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


class DenyDestructiveLog(unittest.TestCase):
    def test_only_a_claude_code_session_is_logged(self):
        for session, logged in [("0b9c2f5e-1d2a-4c3b-9e8f-7a6b5c4d3e2f", True), ("probe", False), ("", False)]:
            with self.subTest(session=session), tempfile.TemporaryDirectory() as home:
                os.makedirs(os.path.join(home, ".claude"))
                payload = json.dumps({"session_id": session, "tool_input": {"command": "rm -rf /etc/x"}})
                self.assertEqual(destructive_decision(HOOKS, payload, home), (0, "deny"))
                self.assertEqual(os.path.exists(os.path.join(home, ".claude", "destructive-log.jsonl")), logged)


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


class OptimizationChecklist(unittest.TestCase):
    def test_lever_with_target_or_speed_word(self):
        for prompt, fires in [
            ("Посмотри какие есть рычаги чтобы сделать деплой 1-2 минуты", True),
            ("какие рычаги для 500req/s", True),
            ("за последние 72 часа что-то упало, посмотри", False),
            ("поясни каждый рычаг чуть подробнее (50-100 слов)", False),
            ("какие рычаги есть у 5 секций отчёта", False),
        ]:
            with self.subTest(prompt=prompt):
                out = run("task-type-checklist.py", {"prompt": prompt}).stdout
                self.assertEqual("Optimization checklist" in out, fires)

    def test_long_prompt_with_lever_word_and_no_unit_is_fast(self):
        prompt = "build 12345 step 67890 " * 2200 + "рычаг"
        started = time.monotonic()
        out = run("task-type-checklist.py", {"prompt": prompt}).stdout
        self.assertLess(time.monotonic() - started, 1.0)
        self.assertNotIn("Optimization checklist", out)


if __name__ == "__main__":
    unittest.main()
