import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.realpath(__file__)), ".."))

from chat_policy import Session, match, parse_query  # noqa: E402

A = Session("25caeb48-d171-43a4-87dd-256d48fa958b", "a", ("measure-api-latency", "delivery-fixes"), 2.0)
B = Session("25ca0000-0000-4000-8000-000000000000", "b", ("Delivery-Review",), 3.0)
C = Session("9c06f3d5-b788-4d78-b11f-0e029bed5846", "c", ("workspace-1596",), 1.0)


def resolve(query):
    """Mirror of `chat find`: the first kind with a match wins."""
    kinds, value = parse_query(query)
    for kind in kinds:
        hits = [s.path for s in match(kind, value, [A, B, C])]
        if hits:
            return hits
    return []


class ChatMatchTest(unittest.TestCase):
    def test_id_prefix_resolves_one_session(self):
        self.assertEqual(resolve("25caeb48"), ["a"])

    def test_pasted_block_resolves_by_its_session_id(self):
        self.assertEqual(resolve("Session name: delivery\nSession ID: 9c06f3d5-b788-4d78-b11f-0e029bed5846"), ["c"])

    def test_fragment_of_an_old_title_resolves_the_renamed_session(self):
        self.assertEqual(resolve("measure-api"), ["a"])

    def test_ambiguous_fragment_lists_all_case_insensitively_most_recent_first(self):
        self.assertEqual(resolve("delivery"), ["b", "a"])

    def test_hex_looking_title_fragment_falls_back_to_name_when_no_id_matches(self):
        self.assertEqual(parse_query("ace-1596"), (("id", "name"), "ace-1596"))
        self.assertEqual(resolve("ace-1596"), ["c"])


if __name__ == "__main__":
    unittest.main()
