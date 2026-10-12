import unittest

from stats_svc import summary


def recs(*values):
    return [{"id": str(i), "value": float(v), "tags": []} for i, v in enumerate(values)]


class SummaryTest(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(
            summary([]),
            {"count": 0, "total": 0.0, "mean": None, "min": None, "max": None, "median": None},
        )

    def test_odd_count(self):
        result = summary(recs(3, 1, 2))
        self.assertEqual((result["count"], result["total"], result["mean"]), (3, 6.0, 2.0))
        self.assertEqual((result["min"], result["max"], result["median"]), (1.0, 3.0, 2.0))

    def test_even_count_median_is_mean_of_middle_values(self):
        self.assertEqual(summary(recs(4, 1, 3, 2))["median"], 2.5)
