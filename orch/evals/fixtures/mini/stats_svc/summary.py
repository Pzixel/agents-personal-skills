"""Pure summary statistics over records."""


def _median(values):
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2


def summary(records):
    values = [r["value"] for r in records]
    if not values:
        return {"count": 0, "total": 0.0, "mean": None, "min": None, "max": None, "median": None}
    total = sum(values)
    return {
        "count": len(values),
        "total": total,
        "mean": total / len(values),
        "min": min(values),
        "max": max(values),
        "median": _median(values),
    }
