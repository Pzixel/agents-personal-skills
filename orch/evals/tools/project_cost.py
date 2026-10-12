#!/usr/bin/env python3
"""Projected cost of a suite run: cases x runs x arms x mean $/run.

Cases are the case dirs under evals/ whose prompt.md `tags:` line holds one of
the given tags. The mean $/run of a case is the mean `costUsd` of its with-arm
runs over every result JSON in evals/results (iterations included); a case
with no recorded run is listed as unknown and left out of the sum. The
without arm is costed like the with arm, so the figure is an upper estimate.
Usage: project_cost.py [--runs N] [--arms N] <tag> [...]
"""
import argparse
import glob
import json
import os
import re
from collections import defaultdict

EVALS = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))


def cases_with_tags(tags):
    found = {}
    for prompt in glob.glob(os.path.join(EVALS, "**", "prompt.md"), recursive=True):
        with open(prompt) as fh:
            head = fh.read(2000)
        m = re.search(r"^tags:\s*\[([^\]]*)\]", head, re.M)
        name = re.search(r"^name:\s*(\S+)", head, re.M)
        case_tags = {t.strip() for t in m.group(1).split(",")} if m else set()
        if case_tags & set(tags) and name:
            found[name.group(1)] = sorted(case_tags)
    return found


def mean_cost_per_case():
    costs = defaultdict(list)
    for path in glob.glob(os.path.join(EVALS, "results", "*.json")):
        try:
            with open(path) as fh:
                data = json.load(fh)
        except (ValueError, OSError):
            continue
        for case in data.get("cases", []) if isinstance(data, dict) else []:
            for run in (case.get("arms") or {}).get("with", []):
                if run.get("costUsd"):
                    costs[case["name"]].append(run["costUsd"])
    return {name: sum(v) / len(v) for name, v in costs.items()}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--arms", type=int, default=2)
    ap.add_argument("tags", nargs="+")
    args = ap.parse_args()
    means = mean_cost_per_case()
    total = 0.0
    for name, tags in sorted(cases_with_tags(args.tags).items()):
        mean = means.get(name)
        if mean is None:
            print("  %-28s unknown $/run  %s" % (name, ",".join(tags)))
            continue
        cost = mean * args.runs * args.arms
        total += cost
        print("  %-28s %6.2f $/run x %d x %d = %7.2f $" % (name, mean, args.runs, args.arms, cost))
    print("projected: %.2f $ (harness costUsd; runs x arms x mean with-arm $/run)" % total)


if __name__ == "__main__":
    main()
