"""Request dispatcher: {"op": ...} -> {"status": int, "body": ...}."""

from .summary import summary


def _ok(body):
    return {"status": 200, "body": body}


def _error(status, message):
    return {"status": status, "body": {"error": message}}


def _add(store, request):
    try:
        return _ok(store.add(request.get("record")))
    except ValueError as exc:
        return _error(400, str(exc))
    except KeyError:
        return _error(400, "duplicate id")


def _get(store, request):
    record = store.get(request.get("id"))
    return _ok(record) if record is not None else _error(404, "unknown id")


def _remove(store, request):
    return _ok({"removed": True}) if store.remove(request.get("id")) else _error(404, "unknown id")


def _list(store, request):
    tag = request.get("tag")
    return _ok(store.by_tag(tag) if tag is not None else store.all())


def _summary(store, request):
    tag = request.get("tag")
    return _ok(summary(store.by_tag(tag) if tag is not None else store.all()))


OPS = {"add": _add, "get": _get, "remove": _remove, "list": _list, "summary": _summary}


def handle(store, request):
    if not isinstance(request, dict) or not isinstance(request.get("op"), str):
        return _error(400, "request must be a dict with an op")
    op = OPS.get(request["op"])
    if op is None:
        return _error(404, "unknown op")
    return op(store, request)
