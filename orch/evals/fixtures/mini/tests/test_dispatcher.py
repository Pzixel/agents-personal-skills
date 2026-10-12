import unittest

from stats_svc import Store, handle


class DispatcherTest(unittest.TestCase):
    def test_add_then_summary_by_tag(self):
        store = Store()
        handle(store, {"op": "add", "record": {"id": "a", "value": 2, "tags": ["x"]}})
        handle(store, {"op": "add", "record": {"id": "b", "value": 9, "tags": []}})
        response = handle(store, {"op": "summary", "tag": "x"})
        self.assertEqual((response["status"], response["body"]["total"]), (200, 2.0))

    def test_unknown_id_and_op(self):
        store = Store()
        self.assertEqual(handle(store, {"op": "get", "id": "nope"})["status"], 404)
        self.assertEqual(handle(store, {"op": "frobnicate"})["status"], 404)

    def test_duplicate_add_is_400(self):
        store = Store()
        request = {"op": "add", "record": {"id": "a", "value": 1, "tags": []}}
        handle(store, request)
        self.assertEqual(handle(store, request)["status"], 400)
