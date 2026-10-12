# stats_svc

A small in-memory statistics service. Stdlib only.

Contract:
- A record is `{"id": str, "value": float, "tags": list[str]}`; ids are unique.
- `Store` keeps records in memory: `add` rejects a duplicate id (`KeyError`),
  `get` returns `None` for an unknown id, `remove` returns whether a record was removed,
  `all()` returns records in insertion order, `by_tag(tag)` filters them.
- `summary(records)` is pure: `count`, `total`, `mean`, `min`, `max`, `median`.
  An empty list gives `count` 0, `total` 0.0 and `None` for every other field.
  `median` of an even count is the mean of the two middle values.
- `handle(store, request)` dispatches `{"op": ..., ...}` and returns
  `{"status": int, "body": ...}`: 200 on success, 400 on a bad request or a duplicate id,
  404 for an unknown id or an unknown op.

Check: `bash check.sh` (prints `check: passed` or `check: failed` as its last line).
