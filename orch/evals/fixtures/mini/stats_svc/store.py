"""In-memory record store."""


def validate(record):
    if not isinstance(record, dict):
        raise ValueError("record must be a dict")
    if not isinstance(record.get("id"), str) or not record["id"]:
        raise ValueError("record id must be a non-empty string")
    value = record.get("value")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("record value must be a number")
    tags = record.get("tags", [])
    if not isinstance(tags, list) or not all(isinstance(t, str) for t in tags):
        raise ValueError("record tags must be a list of strings")
    return {"id": record["id"], "value": float(value), "tags": list(tags)}


class Store:
    def __init__(self):
        self._records = {}

    def add(self, record):
        clean = validate(record)
        if clean["id"] in self._records:
            raise KeyError(clean["id"])
        self._records[clean["id"]] = clean
        return clean

    def get(self, record_id):
        return self._records.get(record_id)

    def remove(self, record_id):
        return self._records.pop(record_id, None) is not None

    def all(self):
        return list(self._records.values())

    def by_tag(self, tag):
        return [r for r in self._records.values() if tag in r["tags"]]
