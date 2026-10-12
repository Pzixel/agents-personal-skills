#!/usr/bin/env python3
"""Tests for the pure scout decision rules.

Expectations come from the phase-2 contract:
  - a quote is accepted only on the reported line or within two lines of it;
  - a quote that occurs exactly once elsewhere corrects the line number;
  - selector agreement needs a threshold AND a unique leader (a tie never accepts);
  - a negative answer is accepted only from the top tier.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import scout_policy as sp  # noqa: E402


class VerifyPointerTest(unittest.TestCase):
    def test_quote_on_a_far_line_is_rejected_when_it_also_occurs_elsewhere(self):
        lines = [
            "def handler():\n",
            "    pass\n",
            "\n",
            "\n",
            "    return 1\n",
            "    return 1\n",
            "    return 1\n",
        ]
        verdict = sp.verify_pointer(lines, line=1, quote="return 1")
        self.assertEqual(verdict.outcome, sp.REJECTED)
        self.assertIsNone(verdict.line)

    def test_single_occurrence_elsewhere_corrects_the_line(self):
        lines = ["alpha\n", "beta\n", "gamma\n", "delta\n", "epsilon\n", "zeta\n"]
        verdict = sp.verify_pointer(lines, line=1, quote="  zeta  ")
        self.assertEqual(verdict.outcome, sp.CORRECTED)
        self.assertEqual(verdict.line, 6)

    def test_out_of_range_line_falls_back_to_the_single_occurrence_rule(self):
        lines = ["alpha\n", "beta\n"]
        verdict = sp.verify_pointer(lines, line=900, quote="beta")
        self.assertEqual(verdict.outcome, sp.CORRECTED)
        self.assertEqual(verdict.line, 2)

    def test_whitespace_quote_is_rejected_rather_than_matching_every_line(self):
        lines = ["alpha\n", "beta\n"]
        verdict = sp.verify_pointer(lines, line=1, quote="   ")
        self.assertEqual(verdict.outcome, sp.REJECTED)


class AgreementTest(unittest.TestCase):
    def test_three_way_split_does_not_accept(self):
        verdict = sp.decide_agreement(["4", "9", "17"], threshold=2)
        self.assertEqual(verdict.outcome, sp.NO_AGREEMENT)
        self.assertIsNone(verdict.candidate_id)

    def test_two_of_three_accepts_the_agreed_candidate(self):
        verdict = sp.decide_agreement(["9", "4", "9"], threshold=2)
        self.assertEqual(verdict.outcome, sp.ACCEPTED)
        self.assertEqual(verdict.candidate_id, "9")

    def test_majority_of_none_votes_is_a_negative_not_an_acceptance(self):
        verdict = sp.decide_agreement(["none", "none", "7"], threshold=2)
        self.assertEqual(verdict.outcome, sp.AGREED_NONE)
        self.assertIsNone(verdict.candidate_id)


class EscalationTest(unittest.TestCase):
    kinds = ["selector", "selector", "agentic_finder"]

    def test_negative_below_the_top_tier_escalates(self):
        verdict = sp.decide_escalation(sp.AGREED_NONE, 0, self.kinds)
        self.assertEqual(verdict.action, sp.RUN_STAGE)
        self.assertEqual(verdict.stage_index, 1)
        self.assertFalse(verdict.negative)

    def test_negative_from_the_top_tier_is_accepted_and_marked_negative(self):
        verdict = sp.decide_escalation(sp.AGREED_NONE, 2, self.kinds)
        self.assertEqual(verdict.action, sp.ACCEPT)
        self.assertTrue(verdict.negative)

    def test_confirm_stage_does_not_count_as_a_tier_above_the_finder(self):
        kinds = ["selector", "agentic_finder", "confirm"]
        verdict = sp.decide_escalation(sp.AGREED_NONE, 1, kinds)
        self.assertEqual(verdict.action, sp.ACCEPT)
        self.assertTrue(verdict.negative)


if __name__ == "__main__":
    unittest.main()
