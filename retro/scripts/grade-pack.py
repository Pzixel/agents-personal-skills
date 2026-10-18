#!/usr/bin/env python3
"""Build a blind grader pack for one two-arm comparison and map the grader reply back.

`pack` copies the rubric, the packet and run-1..k of each arm into a fresh
directory under anonymous shuffled ids, strips a first-line heading naming
the case and a run, aborts on arm-identity strings, writes the id key
`ARMS.tsv` outside the pack,
and prints the diversity line per arm and the grader command. `unpack` reads
the grader reply and `ARMS.tsv`, writes `A-verdict-k.md` / `B-verdict-k.md`
and prints the tally. Usage:

    grade-pack.py pack --case CASE-n --variant V --rubric R.md --packet input.md \\
        --arm-a runs/<baseline>/CASE-n --arm-b runs/<variant>/CASE-n --k 3 \\
        --key-dir grading/CASE-n/V [--pack-dir DIR]
    grade-pack.py unpack --reply grading/CASE-n/V/grader-reply.txt \\
        --arms grading/CASE-n/V/ARMS.tsv [--out-dir grading/CASE-n/V]
"""

from __future__ import annotations

import argparse
import hashlib
import itertools
import os
import random
import re
import shlex
import sys
import tempfile

TITLE = re.compile(r"^#\s+CASE-\d+[A-Z]?\b.*\brun[- ]?\d+\b.*$")
REPLY_OPEN = re.compile(r"^## (\S+)\s*$")
REPLY_VERDICT = re.compile(r"^VERDICT (\S+): (PASS|FAIL) — (.+)$")
PROMPT = """You are a blind grader for a text decision test. The candidate ids are arbitrary and shuffled. Some candidates come from an unmodified configuration and some from a modified one; you are not told which, and you must not try to find out. A PASS for a candidate from the unmodified configuration is a completely valid and expected outcome. Near-identical candidates are normal; judge each on its own text.

Files, all in the current directory: rubric.md (the only criterion), input.md (the situation the candidates answered), and the candidates {files}. A candidate's id is its file name without `.md`: {ids}. Open nothing else.

Procedure for each candidate: read the rubric, the packet and the candidate (message and its tool-call list). If the rubric carries a grader procedure, follow it. Go through Answer form, Minimum and Fail line by line, and for each write present / absent / applies with a phrase quoted verbatim from that candidate (non-English quotes as written). PASS means the Minimum is met and no Fail line applies. Judge only against the rubric; add no criteria of your own; do not compare candidates with each other.

Write no file. Reply with one block per candidate id, in the order listed. A block opens with the line `## <id>` (for example `## {first}`) and closes with exactly one line in this shape:
VERDICT <id>: PASS|FAIL — <one sentence reason>
Write no other line that starts with `##` and no other line that starts with `VERDICT`.
"""


def read(path: str) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read()


def write(path: str, text: str) -> None:
    with open(path, "x", encoding="utf-8") as f:
        f.write(text)


def strip_title(text: str) -> tuple[str, bool]:
    lines = text.split("\n")
    if not lines or not TITLE.match(lines[0]):
        return text, False
    rest = lines[1:]
    while rest and not rest[0].strip():
        rest = rest[1:]
    return "\n".join(rest), True


def diversity(bodies: list[str]) -> str:
    sets = [{ln.strip() for ln in b.split("\n") if ln.strip()} for b in bodies]
    jac = [len(x & y) / len(x | y) if x | y else 1.0 for x, y in itertools.combinations(sets, 2)]
    return f"n_distinct={len(set(bodies))}/{len(bodies)} max_line_jaccard={max(jac, default=0.0):.2f}"


def leak_patterns(arms: list[str], candidate: bool) -> list[re.Pattern]:
    """Candidates get every arm-identity shape; the owner-written packet and
    rubric predate the arms and get only `VERDICT ` and `runs/<arm>/`."""
    pats = [re.compile(r"VERDICT ")]
    for arm in arms:
        a = re.escape(arm)
        pats.append(re.compile(f"runs/{a}/"))
        if candidate:
            pats += [re.compile(rf"\({a}"), re.compile(rf"CASE-\d+ {a} run")]
            if "-" in arm:
                pats.append(re.compile(a))
    return pats


def bare_word_counts(files: dict[str, str], arms: list[str]) -> list[str]:
    words = sorted(set(arms + ["baseline", "runs/", "verdict"]))
    out = []
    for name in sorted(files):
        for w in words:
            n = len(re.findall(re.escape(w), files[name], re.IGNORECASE))
            if n:
                out.append(f"{name}: {w!r} x{n}")
    return out


def anon_order(case: str, variant: str, k: int, arm_a: str, arm_b: str) -> list[tuple[str, int]]:
    seed = hashlib.sha256(f"{case}\0{variant}\0{k}\0{arm_a}\0{arm_b}".encode()).hexdigest()
    slots = [(role, run) for role in "AB" for run in range(1, k + 1)]
    random.Random(seed).shuffle(slots)
    return slots


def arm_name(arm_dir: str) -> str:
    return os.path.basename(os.path.dirname(os.path.abspath(arm_dir).rstrip("/")))


def pack(args: argparse.Namespace) -> int:
    arm_dirs = {"A": args.arm_a, "B": args.arm_b}
    names = {r: arm_name(d) for r, d in arm_dirs.items()}
    if names["A"] == names["B"]:
        sys.exit(f"both arms resolve to the same arm name {names['A']!r}")
    bodies: dict[tuple[str, int], str] = {}
    strips = {"A": 0, "B": 0}
    for role, d in arm_dirs.items():
        for run in range(1, args.k + 1):
            body, stripped = strip_title(read(os.path.join(d, f"run-{run}.md")))
            bodies[(role, run)] = body
            strips[role] += stripped
    slots = anon_order(args.case, args.variant, args.k, names["A"], names["B"])
    ids = {slot: f"cand-{i}" for i, slot in enumerate(slots, 1)}
    files = {"rubric.md": read(args.rubric), "input.md": read(args.packet),
             **{f"{ids[s]}.md": bodies[s] for s in slots}}
    arm_words = list(names.values())
    leaks = [
        f"{name}:{n}: {p.pattern!r}: {line.strip()[:120]}"
        for name, text in files.items()
        for p in leak_patterns(arm_words, name.startswith("cand-"))
        for n, line in enumerate(text.split("\n"), 1)
        if p.search(line)
    ]
    if leaks:
        print("LEAK: arm identity reaches the grader input. Trace each hit to the path that wrote it "
              "and fix that path; do not widen or narrow the scan patterns around it "
              "(the first-line title strip is a separate rule).", file=sys.stderr)
        print("\n".join(leaks), file=sys.stderr)
        return 2
    order = [ids[s] for s in slots]
    files["prompt.md"] = PROMPT.format(files=", ".join(f"{i}.md" for i in order),
                                       ids=", ".join(order), first=order[0])
    key = "\n".join(["id\trole\tarm\trun\tsource"] + [
        f"{ids[(r, n)]}\t{r}\t{names[r]}\t{n}\t{os.path.join(arm_dirs[r], f'run-{n}.md')}"
        for r, n in sorted(ids)
    ]) + "\n"
    arms_path = os.path.join(args.key_dir, "ARMS.tsv")
    if os.path.exists(arms_path) and read(arms_path) != key:
        sys.exit(f"{arms_path} exists with a different key; it may belong to a graded reply, "
                 "pick another --key-dir")
    pack_dir = args.pack_dir or tempfile.mkdtemp(prefix="grade-pack-")
    os.makedirs(pack_dir, exist_ok=True)
    if os.listdir(pack_dir):
        sys.exit(f"pack dir {pack_dir} is not empty")
    for name, text in files.items():
        write(os.path.join(pack_dir, name), text)
    if not os.path.exists(arms_path):
        os.makedirs(args.key_dir, exist_ok=True)
        write(arms_path, key)
    reply = os.path.abspath(os.path.join(args.key_dir, "grader-reply.txt"))
    print(f"pack: {pack_dir}")
    print(f"key: {arms_path}")
    for r in "AB":
        group = [bodies[(r, n)] for n in range(1, args.k + 1)]
        print(f"arm {r} ({names[r]}): title_strips={strips[r]}/{args.k} {diversity(group)}")
    print("bare-word hits (counted, not leaks):")
    for line in bare_word_counts(files, arm_words) or ["none"]:
        print(f"  {line}")
    print("grader command:")
    print(f"  cd {shlex.quote(pack_dir)} && claude -p --restricted --strict-mcp-config "
          f"--output-format text < prompt.md > {shlex.quote(reply)}")
    return 0


def parse_reply(text: str, known: set[str]) -> dict[str, tuple[list[str], str]]:
    """Only `## <known id>` opens a block; any other `##` line is body text.
    A block closes at its one VERDICT line; text between blocks is ignored."""
    blocks: dict[str, tuple[list[str], str]] = {}
    current, body = None, []
    for line in text.split("\n"):
        m = REPLY_OPEN.match(line)
        if m and m.group(1).endswith(".md") and m.group(1)[:-3] in known:
            sys.exit(f"block opener ## {m.group(1)}: the id is the file stem, expected ## {m.group(1)[:-3]}")
        if m and m.group(1) in known:
            if current is not None:
                sys.exit(f"block {current} has no VERDICT line before ## {m.group(1)}")
            current, body = m.group(1), []
            if current in blocks:
                sys.exit(f"duplicated id {current} in reply")
            continue
        v = REPLY_VERDICT.match(line)
        if v:
            vid = v.group(1)
            if vid.endswith(".md") and vid[:-3] in known:
                sys.exit(f"VERDICT {vid}: the id is the file stem, expected VERDICT {vid[:-3]}:")
            if current is None:
                sys.exit(f"VERDICT {vid} outside a block: a second VERDICT line or a missing ## opener")
            if vid != current:
                sys.exit(f"block {current} closes with VERDICT {vid}")
            blocks[current] = (body, f"{v.group(2)} — {v.group(3)}")
            current = None
        elif current is not None:
            body.append(line)
    if current is not None:
        sys.exit(f"block {current} has no VERDICT line")
    missing = sorted(known - set(blocks))
    if missing:
        sys.exit(f"missing ids in reply: {', '.join(missing)}")
    return blocks


def unpack(args: argparse.Namespace) -> int:
    rows = [ln.split("\t") for ln in read(args.arms).strip().split("\n")[1:]]
    key = {r[0]: (r[1], int(r[3]), r[4]) for r in rows}
    blocks = parse_reply(read(args.reply), set(key))
    seen: dict[str, str] = {}
    for cid, (body, _) in blocks.items():
        text = "\n".join(body).strip()
        if text in seen:
            sys.exit(f"identical verdict bodies for {seen[text]} and {cid}: templated reply, discard it")
        seen[text] = cid
    out_dir = args.out_dir or os.path.dirname(os.path.abspath(args.arms))
    passes = {"A": 0, "B": 0}
    runs: dict[str, list[str]] = {"A": [], "B": []}
    outputs: dict[str, str] = {}
    for cid, (role, run, source) in sorted(key.items(), key=lambda x: (x[1][0], x[1][1])):
        body, verdict = blocks[cid]
        while body and not body[0].strip():
            body = body[1:]
        while body and not body[-1].strip():
            body = body[:-1]
        outputs[os.path.join(out_dir, f"{role}-verdict-{run}.md")] = (
            "\n".join(body + [f"VERDICT run-{run}: {verdict}"]) + "\n")
        passes[role] += verdict.startswith("PASS")
        if not os.path.isfile(source):
            sys.exit(f"source {source} not found from {os.getcwd()}: run unpack from the directory "
                     "pack ran in (ARMS.tsv sources are relative to it)")
        runs[role].append(strip_title(read(source))[0])
    existing = [p for p in outputs if os.path.exists(p)]
    if existing:
        sys.exit(f"refusing to overwrite: {', '.join(existing)}")
    os.makedirs(out_dir, exist_ok=True)
    for path, text in outputs.items():
        write(path, text)
    print(", ".join(f"{r} {passes[r]}/{len(runs[r])}" for r in "AB"))
    for r in "AB":
        print(f"arm {r}: {diversity(runs[r])}")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("pack")
    for name in ("--case", "--variant", "--rubric", "--packet", "--arm-a", "--arm-b", "--key-dir"):
        p.add_argument(name, required=True)
    p.add_argument("--k", type=int, required=True)
    p.add_argument("--pack-dir", help="empty or new directory; default a fresh temp dir")
    u = sub.add_parser("unpack")
    u.add_argument("--reply", required=True)
    u.add_argument("--arms", required=True)
    u.add_argument("--out-dir", help="default: the directory of ARMS.tsv")
    args = parser.parse_args()
    sys.exit(pack(args) if args.cmd == "pack" else unpack(args))


if __name__ == "__main__":
    main()
