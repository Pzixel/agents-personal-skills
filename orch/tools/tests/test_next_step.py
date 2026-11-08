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
            ("Ожидание после job 66-198 с, причина не найдена. Рычаг на эту стадию: перенести публикацию в job сборки.",
             "Рычаг на эту стадию: перенести публикацию в job сборки"),
            ("Пауза 66-198 с, причина паузы не найдена. Рычаг: перенести публикацию в job сборки.",
             "Рычаг: перенести публикацию в job сборки"),
            ("The cause of the wait is not found. Lever: move publishing into the build job.",
             "Lever: move publishing into the build job"),
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
            "Причина не найдена; на эту стадию рычаг не ставлю, пока не прочитан механизм.",
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


def scan(entries):
    facts = None
    for facts in transcript.background_watch_scan([("", e) for e in entries], "\x00"):
        pass
    return facts


# Scrubbed paraphrases of the two CASE-71 witnesses: a heading with the night and its window, the own
# steps as list items; a closing promise for the next night.
TIMED_HEADING = (
    "Команды лежат в описании ночных шагов.\n\n"
    "**Суббота ночью**, окно примерно 04:15-05:45 UTC:\n"
    "- проверяю, что фоновая задача не идёт;\n"
    "- присылаю точный план команд;\n"
    "- после твоего «давай» иду по шагам."
)
TIMED_CLOSING = "Если сегодня не начинаем, на следующую ночь поставлю будильник и назову его id."


class TimedStepTriggerTest(unittest.TestCase):
    def test_present_tense_steps_under_a_time_heading_trigger_with_the_heading_and_first_item(self):
        self.assertEqual(gate_policy.timed_step_sentence(TIMED_HEADING),
                         "**Суббота ночью**, окно примерно 04:15-05:45 UTC: - проверяю, что фоновая задача не идёт;")

    def test_own_step_after_a_relative_time_triggers_with_its_sentence(self):
        self.assertEqual(gate_policy.timed_step_sentence("Тесты зелёные. " + TIMED_CLOSING), TIMED_CLOSING)

    def test_weekday_after_vo_triggers(self):
        self.assertEqual(gate_policy.timed_step_sentence("Во вторник проверю отправки."), "Во вторник проверю отправки.")

    def test_english_future_with_a_forward_clock_time_triggers(self):
        self.assertEqual(gate_policy.timed_step_sentence("Build is green. I will merge it after 18:00."),
                         "I will merge it after 18:00.")

    def test_a_bare_past_timestamp_or_date_does_not_trigger(self):
        for reply in ("Проверю логи: сбой был 20.10 около 03:12 UTC.", "Сверю данные с 2026-10-20 по сегодня."):
            self.assertEqual(gate_policy.timed_step_sentence(reply), "", reply)

    def test_time_in_a_report_sentence_does_not_borrow_a_verb_from_another_sentence(self):
        self.assertEqual(gate_policy.timed_step_sentence("PR влит в 18:44. Проверю логи."), "")

    def test_time_without_an_own_step_does_not_trigger(self):
        self.assertEqual(gate_policy.timed_step_sentence("Задача системы запускается завтра ночью сама."), "")

    def test_time_marker_inside_code_does_not_trigger(self):
        self.assertEqual(gate_policy.timed_step_sentence("Проверю это. Пример: `завтра ночью`."), "")


class TimedStepConditionTest(unittest.TestCase):
    def test_live_one_shot_cron_exempts(self):
        self.assertEqual(gate_policy.live_clocks(scan(CRON_ONE_SHOT)), ["1db1ccfe"])

    def test_live_recurring_cron_exempts_timed_step_and_not_next_step(self):
        recurring = [tool_use("c2", "CronCreate", {"cron": "23 * * * *", "recurring": True, "prompt": "tick"}),
                     tool_result("c2", "Scheduled recurring job 5e0a11c3 (Every hour at :23). Next run: Oct 28 12:23pm.")]
        facts = scan(recurring)
        self.assertEqual((gate_policy.live_clocks(facts), gate_policy.live_mechanisms(facts)), (["5e0a11c3"], []))

    # A /loop wakeup armed at 10:29:00 for 1859 s (due 10:59:59), as in a real /loop session (a1ddb115).
    WAKEUP = [dict(tool_use("w1", "ScheduleWakeup", {"delaySeconds": 1800}), timestamp="2026-11-05T10:28:58.100Z"),
              dict(tool_result("w1", "Next wakeup scheduled for 13:00:00 (in 1859s)."), timestamp="2026-11-05T10:29:00.200Z")]

    @staticmethod
    def _reply_at(timestamp):
        return {"type": "assistant", "timestamp": timestamp, "message": {"content": [{"type": "text", "text": "x"}]}}

    def test_pending_loop_wakeup_exempts_until_it_fires(self):
        fired = {"type": "system", "subtype": "scheduled_task_fire", "timestamp": "2026-11-05T10:59:01.000Z",
                 "content": "Claude resuming /loop wakeup (Nov 5 12:59pm)"}
        before_due = self.WAKEUP + [self._reply_at("2026-11-05T10:45:00.000Z")]
        self.assertEqual((gate_policy.live_clocks(scan(before_due)), gate_policy.live_clocks(scan(self.WAKEUP + [fired]))),
                         (["wakeup"], []))

    def test_stopped_loop_wakeup_does_not_exempt(self):
        stop = [dict(tool_use("w2", "ScheduleWakeup", {"stop": True}), timestamp="2026-11-05T10:31:00.000Z"),
                dict(tool_result("w2", "Loop stopped \u2014 cancelled 1 pending wakeup(s);"), timestamp="2026-11-05T10:31:01.000Z"),
                self._reply_at("2026-11-05T10:32:00.000Z")]
        self.assertEqual(gate_policy.live_clocks(scan(self.WAKEUP + stop)), [])

    def test_wakeup_past_its_due_time_does_not_exempt(self):
        self.assertEqual(gate_policy.live_clocks(scan(self.WAKEUP + [self._reply_at("2026-11-05T11:00:30.000Z")])), [])

    def test_monitor_or_agent_alone_does_not_exempt(self):
        monitor = [tool_use("m1", "Monitor", {"command": "x"}), tool_result("m1", "Monitor started (task mo1, timeout 300s)")]
        self.assertEqual(gate_policy.live_clocks(scan(monitor + AGENT_LAUNCH + BASH_LAUNCH)), [])


class TimedStepDecisionTest(unittest.TestCase):
    @staticmethod
    def _verdict(timed_step, **extra):
        verdict = {"handoff": False, "sentence": "", "deferral": False, "deferral_sentence": "", "next_step": False,
                   "next_step_sentence": "", "lever": False, "lever_sentence": "", "timed_step": timed_step,
                   "timed_step_sentence": "на следующую ночь поставлю будильник" if timed_step else ""}
        verdict.update(extra)
        return verdict

    def test_candidate_judged_true_is_returned_with_the_step(self):
        decision = gate_policy.reply_lint_decision(TIMED_CLOSING, False, self._verdict(True), timed_step=TIMED_CLOSING)
        self.assertEqual(decision["rules"], ["timed-step"])
        self.assertIn("на следующую ночь поставлю будильник", decision["reason"])
        self.assertIn("CronCreate", decision["reason"])

    def test_candidate_judged_false_passes(self):
        decision = gate_policy.reply_lint_decision(TIMED_CLOSING, False, self._verdict(False), timed_step=TIMED_CLOSING)
        self.assertEqual(decision, {"decision": "allow", "rules": []})

    def test_without_a_candidate_the_verdict_cannot_fire(self):
        decision = gate_policy.reply_lint_decision(TIMED_CLOSING, False, self._verdict(True))
        self.assertEqual(decision["rules"], [])

    def test_rules_add_up_with_deferral(self):
        verdict = self._verdict(True, deferral=True, deferral_sentence="поставлю будильник")
        decision = gate_policy.reply_lint_decision(TIMED_CLOSING, False, verdict, timed_step=TIMED_CLOSING)
        self.assertEqual(decision["rules"], ["deferral", "timed-step"])

    def test_question_e_is_asked_only_for_a_candidate(self):
        self.assertIn("QUESTION E, OWN STEP", gate_policy.judge_prompt(TIMED_CLOSING, timed_step=TIMED_CLOSING))
        self.assertIn("answer timed_step false", gate_policy.judge_prompt(TIMED_CLOSING))

    def test_verdict_field_is_parsed_and_old_verdicts_read_false(self):
        new = {"handoff": False, "deferral": False, "next_step": False, "timed_step": True, "timed_step_sentence": "x"}
        old = {"handoff": False, "deferral": False, "next_step": False}
        parsed_new = gate_policy.parse_judge_verdict(json.dumps({"result": json.dumps(new)}))
        parsed_old = gate_policy.parse_judge_verdict(json.dumps({"result": json.dumps(old)}))
        self.assertEqual((parsed_new["timed_step"], parsed_new["timed_step_sentence"]), (True, "x"))
        self.assertEqual((parsed_old["timed_step"], parsed_old["timed_step_sentence"]), (False, ""))


if __name__ == "__main__":
    unittest.main()
