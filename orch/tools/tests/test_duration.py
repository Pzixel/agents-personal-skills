"""Rule `duration`: a work duration in days or weeks with no run basis is returned once, from a reply at its first
stop or from an AskUserQuestion input.

The decision is two pure steps: the broad trigger ``duration_candidate`` (native rows) and ``duration_return`` over
the candidate and the judge's question-F verdict, a value argument here. Inputs: three synthetic replies in the
shape of the CASE-88 defect replies (fixtures/duration_replies.json) and every adversarial reply quoted in the two
round-1 reviews (fixtures/duration_adversarial.json), each with the verdict the reviews expect."""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.realpath(__file__)), ".."))

import gate_policy  # noqa: E402

_FIXTURES = os.path.join(os.path.dirname(os.path.realpath(__file__)), "fixtures")
with open(os.path.join(_FIXTURES, "duration_replies.json"), encoding="utf-8") as _fh:
    REPLIES = json.load(_fh)
with open(os.path.join(_FIXTURES, "duration_adversarial.json"), encoding="utf-8") as _fh:
    ADVERSARIAL = json.load(_fh)

CASE_88_RETURN = "Derive it from measured runs"
CORRECTION_ORDER = "Open the correction with the re-derived hours"
PACE_SOURCE = "`~/.claude/tools/pace <repository>` prints the measured minutes"
ESTIMATES = list(REPLIES["fire"].values()) + ADVERSARIAL["estimate"]


def _verdict(duration):
    return {"handoff": False, "sentence": "", "deferral": False, "deferral_sentence": "", "next_step": False,
            "next_step_sentence": "", "lever": False, "timed_step": False, "duration": duration,
            "duration_sentence": ""}


def _first_stop(text, verdict):
    return gate_policy.reply_lint_decision(text, False, verdict, duration=gate_policy.duration_candidate(text))


class DurationTriggerTest(unittest.TestCase):
    def test_every_estimate_reaches_the_judge(self):
        for text in ESTIMATES:
            with self.subTest(text=text[:60]):
                self.assertTrue(gate_policy.duration_candidate(text))

    def test_text_without_days_or_weeks_does_not_trigger(self):
        for text in ("Займёт 5 часов: прогон билдера 40 минут.", "Отчёт раз в день? Нет, per day: 6 GiB.",
                     "Weekly report sent.", "Code in `retention_days = 30`."):
            with self.subTest(text=text):
                self.assertEqual(gate_policy.duration_candidate(text), "")


class DurationDecisionTest(unittest.TestCase):
    def test_estimates_judged_true_return_the_case_88_text(self):
        for text in ESTIMATES:
            with self.subTest(text=text[:60]):
                decision = _first_stop(text, _verdict(True))
                reason = decision.get("reason", "")
                self.assertEqual((decision["rules"], CASE_88_RETURN in reason, CORRECTION_ORDER in reason,
                                  PACE_SOURCE in reason), (["duration"], True, True, True))

    def test_non_estimates_judged_false_pass(self):
        for text in ADVERSARIAL["not_estimate"]:
            with self.subTest(text=text[:60]):
                self.assertNotIn("duration", _first_stop(text, _verdict(False))["rules"])

    def test_judge_error_fails_open(self):
        self.assertNotIn("duration", _first_stop(REPLIES["fire"]["correction-two-weeks"], None)["rules"])

    def test_verdict_without_a_candidate_does_not_return(self):
        decision = gate_policy.reply_lint_decision("Таблица ниже.", False, _verdict(True))
        self.assertNotIn("duration", decision["rules"])

    def test_later_stop_does_not_return_it_again(self):
        text = REPLIES["fire"]["correction-two-weeks"]
        decision = gate_policy.reply_lint_decision(text, True, _verdict(True), later_deferral_open=True,
                                                   first_cause_open=True,
                                                   duration=gate_policy.duration_candidate(text))
        self.assertNotIn("duration", decision["rules"])

    def test_candidate_alone_calls_the_judge(self):
        self.assertTrue(gate_policy.reply_lint_judge_needed("Таблица ниже.", False, False, duration="Займёт неделю."))


class AskDurationTest(unittest.TestCase):
    """The AskUserQuestion entry: the same decision over the stem and option texts, the same return text."""

    @staticmethod
    def _ask(description):
        return {"questions": [{"question": "Existing: none.\nКакой объём берём?",
                               "options": [{"label": "Всё", "description": description}]}]}

    def test_estimate_in_an_option_is_refused_with_the_shared_text_naming_the_question(self):
        tool_input = self._ask("интерфейс следующим этапом, ещё 3–4 дня")
        refusal = gate_policy.ask_existing_decision(tool_input, {"spend": False, "duration": True})
        reply_reason = gate_policy.reply_lint_decision("x", False, _verdict(True), duration="x")["reason"]
        self.assertEqual((refusal.split("(duration) the question ", 1)[1], "the reply " in refusal),
                         (reply_reason.split("(duration) the reply ", 1)[1], False))

    def test_dialog_judged_for_days_alone_is_not_refused_on_spend(self):
        tool_input = self._ask("прогрев ящиков займёт 2 недели")
        self.assertIsNone(gate_policy.ask_existing_decision(tool_input, {"spend": True, "duration": False}))

    def test_option_judged_false_or_unjudged_passes(self):
        tool_input = self._ask("прогрев ящиков займёт 2 недели")
        for verdict in ({"spend": False, "duration": False}, None):
            with self.subTest(verdict=verdict):
                self.assertIsNone(gate_policy.ask_existing_decision(tool_input, verdict))

    def test_question_with_days_reaches_the_judge_with_question_2(self):
        prompt = gate_policy.spend_judge_prompt(self._ask("ещё 3–4 дня"))
        self.assertIn("WORK DURATION WITHOUT A RUN BASIS", prompt)


if __name__ == "__main__":
    unittest.main()
