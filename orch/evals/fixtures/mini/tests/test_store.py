import unittest

from stats_svc import Store


class StoreTest(unittest.TestCase):
    def test_duplicate_id_rejected(self):
        store = Store()
        store.add({"id": "a", "value": 1, "tags": []})
        with self.assertRaises(KeyError):
            store.add({"id": "a", "value": 2, "tags": []})

    def test_invalid_value_rejected(self):
        with self.assertRaises(ValueError):
            Store().add({"id": "a", "value": "1", "tags": []})

    def test_by_tag_keeps_insertion_order(self):
        store = Store()
        for rid, tags in (("a", ["x"]), ("b", []), ("c", ["x", "y"])):
            store.add({"id": rid, "value": 1, "tags": tags})
        self.assertEqual([r["id"] for r in store.by_tag("x")], ["a", "c"])
