"""Contract tests for usage-audit pricing and its read-like narrowing."""
import importlib.util
import os
import unittest

_USAGE_AUDIT = os.path.expanduser("~/.claude/usage-audit.py")
# The script lives outside this repository; CI has no copy.
_PRESENT = os.path.isfile(_USAGE_AUDIT)
_NEEDS_SCRIPT = unittest.skipUnless(_PRESENT, "usage-audit.py is not installed: %s" % _USAGE_AUDIT)
if _PRESENT:
    _SPEC = importlib.util.spec_from_file_location("usage_audit", _USAGE_AUDIT)
    ua = importlib.util.module_from_spec(_SPEC)
    _SPEC.loader.exec_module(ua)


@_NEEDS_SCRIPT
class CacheWritePricingTest(unittest.TestCase):
    def test_five_minute_and_one_hour_writes_are_priced_apart(self):
        cost = ua.turn_cost({"input_tokens": 0, "output_tokens": 0,
                             "cache_read_input_tokens": 0,
                             "cache_creation_input_tokens": 3_000_000,
                             "cache_creation": {"ephemeral_5m_input_tokens": 2_000_000,
                                                "ephemeral_1h_input_tokens": 1_000_000}},
                            ua.RATES["opus"])
        self.assertAlmostEqual(cost["usd"], 2 * 5.0 + 1 * 8.0, places=6)
        self.assertAlmostEqual(cost["usd_cw"], 18.0, places=6)

    def test_flat_cache_creation_is_billed_at_the_five_minute_price(self):
        cost = ua.turn_cost({"input_tokens": 1_000_000, "output_tokens": 1_000_000,
                             "cache_read_input_tokens": 1_000_000,
                             "cache_creation_input_tokens": 1_000_000}, ua.RATES["fable"])
        self.assertAlmostEqual(cost["usd"], 10.0 + 50.0 + 0.25 + 12.5, places=6)


@_NEEDS_SCRIPT
class ReadLikeNarrowingTest(unittest.TestCase):
    """Read-like = observing only; gate_policy.is_read_call is the wider gate."""

    def test_observing_programs_are_read_like(self):
        for command in ("rtk proxy cat /tmp/f.py", "cd /tmp/x && sed -n '1,40p' f.py",
                        "git show HEAD:src/app.py", "awk '{print $1}' /tmp/f",
                        "sort /tmp/a | uniq -c", "stat /tmp/f", "diff a.txt b.txt"):
            with self.subTest(command=command):
                self.assertTrue(ua.is_readlike_call("Bash", command))

    def test_non_observing_programs_are_not_read_like(self):
        for command in ("rtk proxy poetry run pytest tests/x.py -q",
                        "cargo check 2>&1 | tail -40", "git log --oneline -5",
                        "kubectl get pods", "python3 /tmp/probe.py"):
            with self.subTest(command=command):
                self.assertFalse(ua.is_readlike_call("Bash", command))

    def test_git_log_with_a_patch_is_read_like(self):
        self.assertTrue(ua.is_readlike_call("Bash", "git log -p -3"))

    def test_writing_commands_are_not_read_like(self):
        for command in ("cat > /tmp/out.py <<'EOF'\nx = 1\nEOF",
                        "rg foo src > /tmp/hits.txt", "cat a.txt | tee /tmp/copy.txt",
                        "sed -i '' 's/a/b/' /tmp/f.py"):
            with self.subTest(command=command):
                self.assertFalse(ua.is_readlike_call("Bash", command))

    def test_read_tools_are_read_like_and_edits_are_not(self):
        self.assertTrue(ua.is_readlike_call("Read"))
        self.assertTrue(ua.is_readlike_call("Grep"))
        self.assertFalse(ua.is_readlike_call("Write"))
        self.assertFalse(ua.is_readlike_call("Agent"))


if __name__ == "__main__":
    unittest.main()
