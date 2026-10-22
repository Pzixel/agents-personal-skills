"""Unit tests for the scout-bounds PreToolUse clamp decision."""
import importlib.util
import os
import unittest

_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "..", "hooks", "scout-bounds.py"
)
_spec = importlib.util.spec_from_file_location("scout_bounds", _PATH)
scout_bounds = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(scout_bounds)
decide = scout_bounds.decide


class DecideTest(unittest.TestCase):
    def test_smaller_explicit_grep_limit_is_preserved(self):
        self.assertIsNone(decide(True, None, "Grep", {"pattern": "x", "head_limit": 5}))

    def test_smaller_explicit_read_limit_is_preserved(self):
        self.assertIsNone(decide(False, "scout", "Read", {"file_path": "/a", "limit": 50}))

    def test_missing_grep_limit_gets_the_clamp(self):
        self.assertEqual(
            decide(True, None, "Grep", {"pattern": "x", "output_mode": "content"}),
            {"pattern": "x", "output_mode": "content", "head_limit": 40},
        )

    def test_missing_read_limit_gets_the_clamp(self):
        self.assertEqual(
            decide(False, "scout", "Read", {"file_path": "/a"}),
            {"file_path": "/a", "limit": 150},
        )

    def test_oversized_read_limit_is_clamped_keeping_other_fields(self):
        self.assertEqual(
            decide(False, "scout", "Read", {"file_path": "/a", "offset": 900, "limit": 2000}),
            {"file_path": "/a", "offset": 900, "limit": 150},
        )

    def test_other_agent_without_env_flag_is_untouched(self):
        self.assertIsNone(decide(False, "builder", "Read", {"file_path": "/a"}))

    def test_main_session_without_agent_type_is_untouched(self):
        self.assertIsNone(decide(False, None, "Grep", {"pattern": "x"}))

    def test_other_tools_are_untouched(self):
        self.assertIsNone(decide(True, "scout", "Bash", {"command": "rg -n x"}))

    def test_unparseable_limit_falls_back_to_the_cap(self):
        self.assertEqual(
            decide(True, None, "Read", {"file_path": "/a", "limit": "all"}),
            {"file_path": "/a", "limit": 150},
        )


if __name__ == "__main__":
    unittest.main()
