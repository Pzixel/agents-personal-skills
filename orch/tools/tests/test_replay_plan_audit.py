import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.realpath(__file__)), "..", "backtest"))

import replay  # noqa: E402


def _row(ts, owner, plan_hash, path="/tmp/plan.sh", **extra):
    return dict(ts=ts, owner=owner, plan_hash=plan_hash, plan_path=path, **extra)


class NoneRewritesTest(unittest.TestCase):
    def test_a_refusal_or_a_request_followed_by_a_run_without_the_owner_is_listed(self):
        rows = [_row("2026-01-01T10:00:00", "form_refused", "a", declared="none; labels stay"),
                _row("2026-01-01T10:05:00", "not_needed", "a2"),
                _row("2026-01-01T11:00:00", "requested", "b", path="/tmp/other.sh"),
                _row("2026-01-01T11:00:09", "not_needed", "c", path="/tmp/other.sh")]
        self.assertEqual([(f["ts"], r["ts"]) for f, r in replay._none_rewrites(rows)],
                         [("2026-01-01T10:00:00", "2026-01-01T10:05:00"),
                          ("2026-01-01T11:00:00", "2026-01-01T11:00:09")])

    def test_a_request_is_not_paired_with_a_run_of_another_path_or_of_its_own_text(self):
        rows = [_row("2026-01-01T11:00:00", "requested", "b"),
                _row("2026-01-01T11:00:05", "not_needed", "c", path="/tmp/other.sh"),
                _row("2026-01-01T11:00:09", "not_needed", "b")]
        self.assertEqual(replay._none_rewrites(rows), [])


if __name__ == "__main__":
    unittest.main()
