import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.realpath(__file__)), ".."))

import gate_policy  # noqa: E402
import transcript  # noqa: E402

with open(os.path.join(os.path.dirname(os.path.realpath(__file__)), "fixtures", "next_step_witnesses.json")) as _fh:
    WITNESSES = json.load(_fh)


def tool_use(use_id, name, args):
    return {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": use_id, "name": name, "input": args}]}}


def tool_result(use_id, text):
    return {"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": use_id, "content": text}]}}


def notification(task_id, status="completed"):
    return {"type": "user", "message": {"content":
            "<task-notification>\n<task-id>%s</task-id>\n<status>%s</status>\n</task-notification>" % (task_id, status)}}


def owner(text):
    return {"type": "user", "message": {"role": "user", "content": text}}


def turn_end():
    return {"type": "system", "subtype": "turn_duration"}


def live(entries):
    facts = None
    for facts in transcript.background_watch_scan([("", e) for e in entries], "\x00"):
        pass
    return gate_policy.live_mechanisms(facts)


BASH_LAUNCH = [
    tool_use("b1", "Bash", {"command": "make test", "run_in_background": True}),
    tool_result("b1", "Command running in background with ID: bx1. Output is being written to: /tmp/tasks/bx1.output"),
]
AGENT_LAUNCH = [
    tool_use("g1", "Agent", {"prompt": "build"}),
    tool_result("g1", "Async agent launched successfully.\nagentId: ag1 (internal)"),
]
CRON_ONE_SHOT = [
    tool_use("c1", "CronCreate", {"cron": "37 19 29 10 *", "recurring": False, "prompt": "measure"}),
    tool_result("c1", "Scheduled one-shot task 1db1ccfe (37 19 29 10 *). Next run: Oct 29 7:37pm."),
]


class NextStepTriggerTest(unittest.TestCase):
    def test_witness_replies_trigger_with_their_step_sentence(self):
        for name, needle in (
            ("c56ffb38-1020", "Следующий шаг: найти, почему на пуле A"),
            ("c56ffb38-1044", "Что сделаю дальше:"),
            ("29acce51-1718", "Следующий шаг — Kafka"),
        ):
            self.assertIn(needle, gate_policy.next_step_sentence(WITNESSES[name]), name)

    def test_held_step_in_nominal_check_list_triggers(self):
        sentence = gate_policy.next_step_sentence(WITNESSES["5b6476f0-2338"])
        self.assertIn("Что решит вопрос", sentence)
        self.assertIn("Запрос к провайдеру", sentence)
        self.assertIn("пока не запускал", gate_policy.next_step_sentence("Итог ясен.\nПроверку пока не запускал."))
        self.assertIn("Not run yet", gate_policy.next_step_sentence("Checks that decide: a lookup by name. Not run yet."))

    def test_past_negation_about_the_owner_or_inside_a_quote_does_not_trigger(self):
        self.assertEqual(gate_policy.next_step_sentence("Владелец ответил: «пока не запускал».\nЖду."), "")

    def test_reply_without_label_or_future_verb_does_not_trigger(self):
        self.assertEqual(gate_policy.next_step_sentence("Сделано: тесты зелёные, PR влит."), "")

    def test_verb_inside_code_quote_or_path_does_not_trigger(self):
        for text in (
            "Итог в отчёте.\n```\nпроверю потом\n```",
            "Владелец написал «проверю завтра».",
            "Отчёт: /tmp/проверю/out.txt",
        ):
            self.assertEqual(gate_policy.next_step_sentence(text), "", text)

    def test_step_before_the_closing_part_does_not_trigger(self):
        self.assertEqual(gate_policy.next_step_sentence("Проверю логи. " + "Факт. " * 200), "")


class LiveMechanismsTest(unittest.TestCase):
    def test_session_with_no_mechanism_has_none_live(self):
        self.assertEqual(live([owner("go"), turn_end(), owner("next")]), [])

    def test_background_task_from_an_earlier_turn_is_live_until_its_notification(self):
        self.assertEqual(live(BASH_LAUNCH + [turn_end(), owner("status?")]), ["bx1"])
        self.assertEqual(live(BASH_LAUNCH + [notification("bx1")]), [])

    def test_monitor_is_live_until_its_end_notice(self):
        armed = [tool_use("m1", "Monitor", {"command": "x"}), tool_result("m1", "Monitor started (task mo1, timeout 300s)")]
        self.assertEqual(live(armed + [turn_end()]), ["mo1"])
        self.assertEqual(live(armed + [notification("mo1")]), [])

    def test_one_shot_cron_is_live_until_it_fires(self):
        self.assertEqual(live(CRON_ONE_SHOT + [turn_end()]), ["1db1ccfe"])
        fired = {"type": "system", "subtype": "scheduled_task_fire", "taskId": "1db1ccfe"}
        self.assertEqual(live(CRON_ONE_SHOT + [fired]), [])

    def test_recurring_job_is_not_a_wait(self):
        created = [tool_use("c2", "CronCreate", {"cron": "*/5 * * * *", "recurring": True}),
                   tool_result("c2", "Scheduled recurring job 2f63e881 (*/5 * * * *).")]
        self.assertEqual(live(created + [turn_end()]), [])

    def test_cron_delete_closes_a_one_shot_job(self):
        self.assertEqual(live(CRON_ONE_SHOT + [tool_use("d1", "CronDelete", {"id": "1db1ccfe"})]), [])

    def test_async_agent_is_live_until_notification_or_task_stop(self):
        self.assertEqual(live(AGENT_LAUNCH + [turn_end(), owner("status?")]), ["ag1"])
        self.assertEqual(live(AGENT_LAUNCH + [notification("ag1")]), [])
        self.assertEqual(live(AGENT_LAUNCH + [tool_use("t1", "TaskStop", {"task_id": "ag1"})]), [])

    def test_send_message_reopens_a_finished_agent(self):
        self.assertEqual(live(AGENT_LAUNCH + [notification("ag1"), tool_use("s1", "SendMessage", {"to": "ag1"})]), ["ag1"])

    def test_agent_call_without_result_is_live(self):
        self.assertEqual(live([tool_use("g2", "Agent", {"prompt": "x"})]), ["Agent call"])


class NextStepDecisionTest(unittest.TestCase):
    @staticmethod
    def _verdict(next_step, deferral=False, handoff=False):
        return {"handoff": handoff, "sentence": "Решение за вами.", "deferral": deferral,
                "deferral_sentence": "Проверю потом.", "next_step": next_step, "next_step_sentence": "Проверю логи."}

    def test_candidate_the_judge_finds_startable_is_returned(self):
        decision = gate_policy.reply_lint_decision(WITNESSES["c56ffb38-1020"], False, self._verdict(True), "Первым проверю.")
        self.assertEqual((decision["decision"], decision["rules"]), ("block", ["next-step"]))
        self.assertIn("Run it now, in this turn", decision["reason"])

    def test_candidate_the_judge_finds_waiting_passes(self):
        decision = gate_policy.reply_lint_decision("Проверю логи, когда сборщик допишет.", False, self._verdict(False), "Проверю логи.")
        self.assertEqual(decision["decision"], "allow")

    def test_without_a_candidate_the_judge_cannot_fire_next_step(self):
        decision = gate_policy.reply_lint_decision("Проверю логи.", False, self._verdict(True), "")
        self.assertEqual(decision["decision"], "allow")

    def test_next_step_replaces_deferral_and_keeps_handoff(self):
        decision = gate_policy.reply_lint_decision("Проверю логи.", False, self._verdict(True, True, True), "Проверю логи.")
        self.assertEqual(decision["rules"], ["handoff", "next-step"])

    def test_second_stop_passes(self):
        decision = gate_policy.reply_lint_decision(WITNESSES["c56ffb38-1020"], True, self._verdict(True), "Первым проверю.")
        self.assertEqual(decision["decision"], "allow")

    def test_question_c_is_asked_only_for_a_candidate(self):
        self.assertIn('"Проверю логи."', gate_policy.judge_prompt("x", "Проверю логи."))
        self.assertNotEqual(gate_policy.judge_prompt("x", "Проверю логи."), gate_policy.judge_prompt("x"))


class LeverTriggerTest(unittest.TestCase):
    def test_lever_lists_with_no_consumer_read_are_candidates(self):
        for text, sentence in (
            ("Рычаги на ожидании сборки: флаг пропуска прогрева и параллельный прогон двух стадий.",
             "Рычаги на ожидании сборки: флаг пропуска прогрева и параллельный прогон двух стадий"),
            ("Levers: raise the poll interval, then merge the two waits into one.",
             "Levers: raise the poll interval, then merge the two waits into one"),
            ("Стадия длится 9 минут. Рычаг: кэш артефакта, webhook не нужен.",
             "Рычаг: кэш артефакта, webhook не нужен"),
            ("Lever #2: done. Рычаг: таймаут ожидания раннера снизить.", "Рычаг: таймаут ожидания раннера снизить"),
            ("Рычаг: убираем таймаут ожидания и ставим параллельный прогон.",
             "Рычаг: убираем таймаут ожидания и ставим параллельный прогон"),
            ("Рычаг: кэш, чтобы не делать лишних запросов к API.", "Рычаг: кэш, чтобы не делать лишних запросов к API"),
            ("Levers: cache the status poll; the webhook is not needed.",
             "Levers: cache the status poll; the webhook is not needed"),
            ("Lever: raise Kafka consumer parallelism for the poll stage.",
             "Lever: raise Kafka consumer parallelism for the poll stage"),
            ("Lever: cache to cut CPU consumption of the poll loop.",
             "Lever: cache to cut CPU consumption of the poll loop"),
        ):
            self.assertEqual(gate_policy.lever_candidate(text), sentence, text)

    def test_status_labels_and_consumer_reads_are_not_candidates(self):
        for text in (
            "Сборка зелёная, PR влит, кэш прогрет.",
            "lever #1: p95 438 ms; blocker: none; doing: review of the cache before the PR.",
            "We leverage the cache already; PR merged, CI green.",
            "Рычаг первый: прочитать, кто читает результат шага; флаг потом.",
            "Шаг не нужен, убираем: это и есть рычаг, быстрее не станет ничего другого.",
            "Рычаг: убираем шаг прогрева целиком, кэш не нужен.",
            "Рычаг: никто не потребляет результат прогрева, параллельный прогон не нужен.",
            "Who reads the warm-up output? Nobody, so the lever is its removal, not a parallel run.",
            "Главный рычаг здесь — цена письма.",
            "Рычаг понятен, остался один вопрос по цене.",
        ):
            self.assertEqual(gate_policy.lever_candidate(text), "", text)


class LeverDecisionTest(unittest.TestCase):
    TEXT = "Рычаги: флаг пропуска прогрева."

    @staticmethod
    def _verdict(lever):
        return {"handoff": False, "sentence": "", "deferral": False, "deferral_sentence": "",
                "next_step": False, "next_step_sentence": "", "lever": lever, "lever_sentence": ""}

    def test_judged_true_returns_the_lever_reason(self):
        lever = gate_policy.lever_candidate(self.TEXT)
        decision = gate_policy.reply_lint_decision(self.TEXT, False, self._verdict(True), "", lever)
        self.assertEqual((decision["decision"], decision["rules"]), ("block", ["lever"]))
        self.assertIn("who consumes that step's result", decision["reason"])

    def test_judged_false_or_no_verdict_passes(self):
        lever = gate_policy.lever_candidate(self.TEXT)
        for verdict in (self._verdict(False), None):
            self.assertEqual(gate_policy.reply_lint_decision(self.TEXT, False, verdict, "", lever)["decision"], "allow")

    def test_lever_and_next_step_fire_together(self):
        verdict = dict(self._verdict(True), next_step=True, next_step_sentence="Проверю логи.")
        decision = gate_policy.reply_lint_decision(self.TEXT, False, verdict, "Проверю логи.", "Рычаги: флаг пропуска прогрева")
        self.assertEqual(decision["rules"], ["next-step", "lever"])

    def test_question_d_is_asked_only_for_a_candidate(self):
        self.assertIn("QUESTION D, LEVER", gate_policy.judge_prompt("x", "", "Рычаг: кэш."))
        self.assertNotIn("QUESTION D, LEVER", gate_policy.judge_prompt("x"))

    def test_verdict_without_the_lever_key_reads_false(self):
        out = json.dumps({"result": json.dumps({"handoff": False, "deferral": False, "next_step": False})})
        self.assertFalse(gate_policy.parse_judge_verdict(out)["lever"])


if __name__ == "__main__":
    unittest.main()
