#!/usr/bin/env python3
"""Build a blind grader pack for one two-arm comparison and map the grader reply back.

`pack` copies the rubric, the packet and run-1..k of each arm into a fresh
directory under anonymous shuffled ids, strips a `# CASE-n run-k` title line,
aborts on arm-identity strings, writes the id key `ARMS.tsv` outside the pack,
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

TITLE = re.compile(r"^#\s+CASE-\d+\s+run-\d+\b.*$")
REPLY_OPEN = re.compile(r"^## (\S+)\s*$")
REPLY_VERDICT = re.compile(r"^VERDICT (\S+): (PASS|FAIL) — (.+)$")
PROMPT = """You are a blind grader for a text decision test. The candidate ids are arbitrary and shuffled. Some candidates come from an unmodified configuration and some from a modified one; you are not told which, and you must not try to find out. A PASS for a candidate from the unmodified configuration is a completely valid and expected outcome. Near-identical candidates are normal; judge each on its own text.

Files, all in the current directory: rubric.md (the only criterion), input.md (the situation the candidates answered), and the candidates {files}. Open nothing else.

Procedure for each candidate: read the rubric, the packet and the candidate (message and its tool-call list). If the rubric carries a grader procedure, follow it. Go through Answer form, Minimum and Fail line by line, and for each write present / absent / applies with a phrase quoted verbatim from that candidate (non-English quotes as written). PASS means the Minimum is met and no Fail line applies. Judge only against the rubric; add no criteria of your own; do not compare candidates with each other.

Write no file. Reply with one block per candidate, in the order listed. A block opens with the line `## <ID>` and closes with exactly one line in this shape:
VERDICT <ID>: PASS|FAIL — <one sentence reason>
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


def leak_patterns(arms: list[str]) -> list[re.Pattern]:
    pats = [re.compile(r"VERDICT ")]
    for arm in arms:
        a = re.escape(arm)
        pats += [re.compile(f"runs/{a}/"), re.compile(rf"\({a}"), re.compile(rf"CASE-\d+ {a} run-")]
        if "-" in arm:
            pats.append(re.compile(a))
    return pats


def bare_word_counts(files: dict[str, str], arms: list[str]) -> list[str]:
    words = sorted(set(arms + ["runs/", "verdict"]))
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
    packet = read(args.packet)
    scanned = {"input.md": packet, **{f"{ids[s]}.md": bodies[s] for s in slots}}
    pats = leak_patterns(list(names.values()))
    leaks = [
        f"{name}:{n}: {p.pattern!r}: {line.strip()[:120]}"
        for name, text in scanned.items()
        for n, line in enumerate(text.split("\n"), 1)
        for p in pats
        if p.search(line)
    ]
    if leaks:
        print("LEAK: arm identity reaches the grader input. Trace each hit to the path that wrote it "
              "and fix that path; do not widen or narrow the pattern around it.", file=sys.stderr)
        print("\n".join(leaks), file=sys.stderr)
        return 2
    pack_dir = args.pack_dir or tempfile.mkdtemp(prefix="grade-pack-")
    os.makedirs(pack_dir, exist_ok=True)
    if os.listdir(pack_dir):
        sys.exit(f"pack dir {pack_dir} is not empty")
    os.makedirs(args.key_dir, exist_ok=True)
    files = {"rubric.md": read(args.rubric), **scanned}
    files["prompt.md"] = PROMPT.format(files=", ".join(f"{ids[s]}.md" for s in slots))
    for name, text in files.items():
        write(os.path.join(pack_dir, name), text)
    key = ["id\trole\tarm\trun\tsource"] + [
        f"{ids[(r, n)]}\t{r}\t{names[r]}\t{n}\t{os.path.join(arm_dirs[r], f'run-{n}.md')}"
        for r, n in sorted(ids)
    ]
    arms_path = os.path.join(args.key_dir, "ARMS.tsv")
    with open(arms_path, "w", encoding="utf-8") as f:
        f.write("\n".join(key) + "\n")
    reply = os.path.abspath(os.path.join(args.key_dir, "grader-reply.txt"))
    print(f"pack: {pack_dir}")
    print(f"key: {arms_path}")
    for r in "AB":
        group = [bodies[(r, n)] for n in range(1, args.k + 1)]
        print(f"arm {r} ({names[r]}): title_strips={strips[r]}/{args.k} {diversity(group)}")
    print("bare-word hits (counted, not leaks):")
    for line in bare_word_counts(files, list(names.values())) or ["none"]:
        print(f"  {line}")
    print("grader command:")
    print(f"  cd {shlex.quote(pack_dir)} && claude -p --restricted --strict-mcp-config "
          f"--output-format text < prompt.md > {shlex.quote(reply)}")
    return 0


def parse_reply(text: str, known: set[str]) -> dict[str, tuple[list[str], str]]:
    blocks: dict[str, tuple[list[str], str]] = {}
    current, body = None, []
    for line in text.split("\n"):
        m = REPLY_OPEN.match(line)
        if m:
            if current is not None:
                sys.exit(f"block {current} has no VERDICT line before ## {m.group(1)}")
            current, body = m.group(1), []
            if current not in known:
                sys.exit(f"unknown id {current} in reply")
            if current in blocks:
                sys.exit(f"duplicated id {current} in reply")
            continue
        v = REPLY_VERDICT.match(line)
        if v and current is not None:
            if v.group(1) != current:
                sys.exit(f"block {current} closes with VERDICT {v.group(1)}")
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
    runs = {"A": [], "B": []}
    for cid, (role, run, source) in sorted(key.items(), key=lambda x: (x[1][0], x[1][1])):
        body, verdict = blocks[cid]
        while body and not body[0].strip():
            body = body[1:]
        while body and not body[-1].strip():
            body = body[:-1]
        write(os.path.join(out_dir, f"{role}-verdict-{run}.md"),
              "\n".join(body + [f"VERDICT run-{run}: {verdict}"]) + "\n")
        passes[role] += verdict.startswith("PASS")
        runs[role].append(strip_title(read(source))[0])
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
