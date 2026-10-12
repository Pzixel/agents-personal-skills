"""deny-destructive refuses kubectl mutations; task-type-checklist fires the incident checklist on live failures."""

import json
import pathlib
import subprocess
import unittest

HOOKS = pathlib.Path(__file__).resolve().parents[2] / "hooks"


def run(hook, payload):
    return subprocess.run(
        ["python3", str(HOOKS / hook)], input=json.dumps(payload), capture_output=True, text=True
    )


def deny(command):
    return run("deny-destructive.py", {"tool_input": {"command": command}}).returncode


def incident(prompt):
    return "Incident checklist" in run("task-type-checklist.py", {"prompt": prompt}).stdout


class KubectlVerbs(unittest.TestCase):
    def test_table(self):
        for cmd, code in [
            ("kubectl rollout restart deploy/x", 2),
            ("kubectl -n app patch deploy/x -p '{}'", 2),
            ("kubectl rollout undo deploy/x", 2),
            ("kubectl --context prod scale deploy/x --replicas=0", 2),
            ("kubectl drain node-3", 2),
            ("kubectl -v 6 apply -f m.yaml", 2),
            ("kubectl delete pod x", 2),
            ("kubectl delete pod -l app=x", 0),
            ("kubectl rollout status deploy/x", 0),
            ("kubectl get pods -A", 0),
            ("kubectl get pods -n set", 0),
            ("kubectl -n app exec pod -- redis-cli set k v", 0),
            ("kubectl get pods # then patch it", 0),
        ]:
            with self.subTest(cmd=cmd):
                self.assertEqual(deny(cmd), code)


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
