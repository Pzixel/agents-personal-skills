"""Backtest of the fresh-reader Stop hook on stored turns, with the shipped trigger, packet, flags and prompt.

usage: python3 fresh_reader_backtest.py packets <list>... [--names a,b] [--expect name=text]...
       python3 fresh_reader_backtest.py rerun <out_dir> <list>... [--names a,b]

A list file has one turn per line: `<name> <transcript path> <reply line | owner-message timestamp>` (a line number
is the 1-based JSONL line of the reply; a timestamp prefix names the owner message, and the turn's reply is the last
main-thread text before the next owner message). `packets` builds every packet with no model call, prints per turn
the trigger result and the section sizes, and exits 1 when an `--expect` text is missing from its packet, when a
turn with an `--expect` builds no packet or is absent from the lists, or when no packet is built at all; a missing transcript prints
"skipped: transcript not found". With no list it prints the usage and exits 2. The CASE-84 list is
retro-evals `cases/CASE-84/judged-turns.txt`. `rerun` also runs the hook's judge and writes
<name>.prompt.txt and <name>.verdict.txt (the judge's stdout or its error) into out_dir."""
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "hooks"))

import importlib.util  # noqa: E402

import fresh_reader_policy as policy  # noqa: E402
import gate_policy  # noqa: E402
import model_judge  # noqa: E402

CLIENTS = gate_policy._merged_params(gate_policy.load_params())["fresh_reader"]["query_clients"]


def hook():
    spec = importlib.util.spec_from_file_location("fresh_reader_hook", os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "..", "hooks", "fresh-reader.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def turns(paths, names):
    for path in paths:
        with open(path) as fh:
            for line in fh:
                parts = line.split()
                if len(parts) >= 3 and (not names or parts[0] in names):
                    yield parts[0], parts[1], parts[2]


def load(path, key):
    """(entries up to and including the reply, reply text) or None when the reply is not found."""
    rows = []
    with open(path, errors="replace") as fh:
        for number, line in enumerate(fh, 1):
            try:
                rows.append((number, json.loads(line)))
            except ValueError:
                continue
    entries = [entry for _n, entry in rows]

    def text(entry):
        if entry.get("type") != "assistant" or entry.get("isSidechain"):
            return ""
        return "\n".join(policy.texts((entry.get("message") or {}).get("content"))).strip()

    end = None
    if key.isdigit():
        end = next((i for i, (n, _e) in enumerate(rows) if n == int(key) and text(entries[i])), None)
    else:
        first = next((i for i, (_n, e) in enumerate(rows) if str(e.get("timestamp", "")).startswith(key)), None)
        for i in range(first + 1 if first is not None else len(rows), len(rows)):
            if policy.owner_text(entries[i]) is not None:
                break
            if text(entries[i]):
                end = i
    return None if end is None else (entries[:end + 1], text(entries[end]))


def packet(path, key):
    loaded = load(path, key)
    if loaded is None:
        return None
    entries, reply = loaded
    found = policy.trigger(entries, reply, False, None, None, CLIENTS)
    prompt, sizes = policy.build_packet(entries, found["start"], len(entries) - 1, reply, found["queries"])
    return found, prompt, sizes


def main(argv):
    if not argv or argv[0] not in ("packets", "rerun") or len(argv) < (3 if argv[0] == "rerun" else 2):
        print(__doc__.split("\n\n")[1], file=sys.stderr)
        return 2
    mode, rest = argv[0], argv[1:]
    out_dir = rest.pop(0) if mode == "rerun" else None
    names, expects, paths = set(), {}, []
    while rest:
        arg = rest.pop(0)
        if arg == "--names":
            names = set(rest.pop(0).split(","))
        elif arg == "--expect":
            name, _, text = rest.pop(0).partition("=")
            expects.setdefault(name, []).append(text)
        else:
            paths.append(arg)
    if not paths:
        print("no turn list given", file=sys.stderr)
        return 2
    judge = hook() if mode == "rerun" else None
    failed, built_count = 0, 0
    for name, path, key in turns(paths, names):
        found_file = os.path.isfile(path)
        built = packet(path, key) if found_file else None
        if built is None:
            print(name, "skipped: transcript not found" if not found_file else "skipped: reply not found at " + key,
                  "(expectation not checked)" if name in expects else "")
            failed += name in expects
            continue
        built_count += 1
        found, prompt, sizes = built
        missing = [t for t in expects.get(name, []) if t not in prompt]
        failed += bool(missing)
        line = "%s fire=%s queries=%d prompt=%d %s" % (name, found["fire"], len(found["queries"]), len(prompt),
                                                      json.dumps(sizes))
        if expects.get(name):
            line += " expect=" + ("MISSING %r" % missing if missing else "present")
        if judge:
            os.makedirs(out_dir, exist_ok=True)
            with open(os.path.join(out_dir, name + ".prompt.txt"), "w") as fh:
                fh.write(prompt)
            started = time.monotonic()
            verdict, error = model_judge.run(judge.JUDGE_ARGS, prompt, judge.JUDGE_TIMEOUT_S, judge.JUDGE_ENV, str)
            with open(os.path.join(out_dir, name + ".verdict.txt"), "w") as fh:
                fh.write(verdict or "ERROR " + str(error))
            parsed = policy.parse_verdict(verdict or "")
            line += " wall=%.0fs verdict=%s" % (time.monotonic() - started,
                                                (parsed or {}).get("verdict") or "unparsable " + str(error))
        print(line)
    for name in sorted(set(expects) - {n for n, _p, _k in turns(paths, ())}):
        print(name, "expectation for a turn absent from the lists")
        failed += 1
    if not built_count:
        print("no packet built", file=sys.stderr)
        return 1
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
