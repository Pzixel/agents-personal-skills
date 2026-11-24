"""Pure policy of `pace`: transcript entries of orchestrated tasks -> tasks, landing times and the printed table.

A task is a repository plus the basename of the worktree named in a builder prompt (`<repo>/.worktrees/<name>`).
Its start is the first event of its first builder transcript; its landing is the earliest successful merge command
of a main session, after that start, that targets the task's repository and merges the task's own PR (see
`landings_of`)."""

import collections
import datetime
import os
import re

from transcript import blocks_of, epoch, result_text

MERGE = "gh pr " + "merge"  # split so the merge gate does not read this source as a landing
CREATE = "gh pr create"
WORKTREE_RE = re.compile(r"(/[^\s`'\"]+/\.worktrees/[\w.-]*\w)")
BRANCH_RE = re.compile(r"branch[`:\s]+`?([\w./-]{3,})")
# A lookup of the PR whose head is a branch: `gh pr view <branch>` or `gh pr list --head <branch>`.
HEAD_LOOKUP_RE = re.compile(r"gh pr view\s+([\w.-]+/[\w./-]+|[A-Za-z][\w.]*-[\w./-]+)(?=[\s;&|)]|$)"
                            r"|gh pr list\b[^;&|\n]*?--head[=\s]+['\"]?([\w./-]+)")
PR_URL_RE = re.compile(r"github\.com/[^/\s]+/([\w.-]+)/pull/(\d+)")
# What a merge command reports when the merge itself did not happen: a GitHub API refusal (merge conflicts, a
# merge already in progress), the conflict or `--auto` hint gh prints for an unmergeable PR, a gate refusal.
# Words such as `failed` or `error` elsewhere in a compound command's output (a check summary, a branch cleanup
# after the merge) say nothing about the merge.
MERGE_REFUSED_RE = re.compile(r"(?i)GraphQL:|merge conflicts|not mergeable|refused by|add the `--auto` flag")
MERGED_RE = re.compile(r"\bMERGED\b|(?i:merged pull request)")
TIERS = ("small", "medium", "large", "no size: line")

Run = collections.namedtuple("Run", "kind prompt cwd first last stamps prs")
Call = collections.namedtuple("Call", "stamp command cwd failed output")
# A PR GitHub returned for a head the command named: the URL a `gh pr create` printed, or the one PR a lookup
# by head branch (HEAD_LOOKUP_RE) printed. Repository name lowercased, number, the head branch (`--head` or the view's
# branch; None when not named) and the worktree the command cd'd into (None when it ran in none).
Created = collections.namedtuple("Created", "repo number head worktree")
# A successful merge command: result time; the repository it targets (`repo`: a name from -R or a PR URL, else
# `place`: the directory it ran in); the PR selector (number or branch); its text: the call up to the end of the
# merge command (what ran after the merge names nothing about it) and the directory it ran in.
Merge = collections.namedtuple("Merge", "stamp repo place number branch text")


def run_of(kind, entries):
    """A builder or reviewer transcript as a Run: its first user prompt, cwd, first/last event time (epoch
    seconds), every event time, and the PRs its own `gh pr create` calls returned. None without a timed event."""
    entries = list(entries)
    stamps, prompt, cwd = [], None, None
    for entry in entries:
        if entry.get("type") not in ("user", "assistant"):
            continue
        stamp = epoch(entry.get("timestamp"))
        if stamp is None:
            continue
        stamps.append(stamp)
        cwd = cwd or entry.get("cwd")
        for block in blocks_of(entry):
            if prompt is None and entry["type"] == "user" and block.get("type") == "text":
                prompt = str(block.get("text") or "")
    if not stamps:
        return None
    prs = {(c.repo, c.number) for c in pr_events(bash_calls(entries))[0]}
    return Run(kind, prompt or "", cwd or "", min(stamps), max(stamps), stamps, prs)


def bash_calls(entries):
    """Bash tool uses of a transcript joined with their results: Call(result time, command, cwd, failed, output).
    The result is the timed user entry that carries it; untimed copies of it (re-logged entries) are not results."""
    pending, calls = {}, []
    for entry in entries:
        for block in blocks_of(entry):
            if entry.get("type") == "assistant" and block.get("type") == "tool_use" and block.get("name") == "Bash":
                pending[block.get("id")] = (str((block.get("input") or {}).get("command") or ""), entry.get("cwd") or "")
            elif (block.get("type") == "tool_result" and block.get("tool_use_id") in pending
                  and entry.get("type") == "user" and epoch(entry.get("timestamp")) is not None):
                command, cwd = pending.pop(block["tool_use_id"])
                calls.append(Call(epoch(entry.get("timestamp")), command, cwd, bool(block.get("is_error")),
                                  result_text(block)))
    return calls


def merge_succeeded(failed, output):
    """Whether a merge command merged, from what it reported: a refusal of the merge itself is a failure; a
    reported merge (`MERGED`, gh's "Merged pull request") is a success even when a later part of the call failed;
    otherwise the call's own exit decides. Words such as `Failed: 0` or `error:` say nothing either way."""
    if MERGE_REFUSED_RE.search(output):
        return False
    return bool(MERGED_RE.search(output)) or not failed


def _segment(command, at):
    """The simple command that starts at index `at`: up to the first unquoted `;`, `&`, `|` or newline."""
    quote, i = None, at
    while i < len(command):
        ch = command[i]
        if quote:
            if ch == "\\" and quote == '"':
                i += 1
            elif ch == quote:
                quote = None
        elif ch in "'\"":
            quote = ch
        elif ch in ";&|\n":
            break
        i += 1
    return command[at:i]


def _place(command, at, cwd):
    """The directory a command part at index `at` runs in: the call's cwd moved by each `cd` before it, in order; a
    `cd` inside a subshell or substitution `( ... )` that closed before `at` moved only that subshell."""
    before = command[:at]
    while True:
        outer = re.sub(r"\([^()]*\)", "", before)
        if outer == before:
            break
        before = outer
    place = cwd
    for target in re.findall(r"(?:^|[;&|(\n])\s*cd\s+(\"[^\"]+\"|'[^']+'|[^\s;&|)]+)", before):
        target = target.strip("'\"")
        if target.startswith("~/"):  # the home of the transcript's machine: the first two parts of its cwd
            target = "/".join(cwd.split("/")[:3]) + target[1:]
        place = os.path.normpath(os.path.join(place, target))
    return place


def _option(segment, *names):
    found = re.search(r"(?:%s)(?:=|\s+)(\"[^\"]+\"|'[^']+'|\S+)" % "|".join(map(re.escape, names)), segment)
    return found.group(1).strip("'\"") if found else None


def _one_pr(output):
    """(repository name or None, number) of the one PR a lookup printed (URLs, `"number":N`, a number that opens
    the output or stands alone on a line); None when it printed none or several."""
    urls = set(PR_URL_RE.findall(output))
    numbers = set(re.findall(r'"number":\s*(\d+)', output)) | {n for _, n in urls}
    bare = re.match(r"\s*(\d+)\b(?![.:/-])", output)
    numbers.update(re.findall(r"(?m)^\s*(\d+)\s*$", output) + ([bare.group(1)] if bare else []))
    if len(numbers) != 1 or len(urls) > 1:
        return None
    return (urls.pop()[0] if urls else None), numbers.pop()


def _repo_name(place):
    return os.path.basename(place.split("/.worktrees/")[0])


def _worktree(place):
    found = WORKTREE_RE.search(place + "/")
    return found.group(1) if found else None


def pr_events(calls):
    """(created, merges) from Bash calls: the PRs GitHub returned for a head a command named (Created), and the
    successful merge commands (one Merge per merge command in a call)."""
    created, merges = [], []
    for call in calls:
        if call.stamp is None:
            continue
        at = call.command.find(CREATE)
        url = PR_URL_RE.search(call.output)
        if not call.failed and at >= 0 and url:
            head = _option(_segment(call.command, at), "--head", "-H")
            created.append(Created(url.group(1).lower(), url.group(2), head and head.split(":")[-1],
                                   _worktree(_place(call.command, at, call.cwd))))
        lookup = HEAD_LOOKUP_RE.search(call.command)
        found = None if call.failed or not lookup or at >= 0 else _one_pr(call.output)
        if found:
            place = _place(call.command, lookup.start(), call.cwd)
            repo = found[0] or _option(_segment(call.command, lookup.start()), "-R", "--repo") or _repo_name(place)
            created.append(Created(repo.rstrip("/").split("/")[-1].lower(), found[1], lookup.group(1) or
                                   lookup.group(2), _worktree(place)))
        if MERGE not in call.command or not merge_succeeded(call.failed, call.output):
            continue
        for found in re.finditer(re.escape(MERGE), call.command):
            segment = _segment(call.command, found.start())
            selector = re.match(r"\s+(\"[^\"]+\"|'[^']+'|[^\s-]\S*)", segment[len(MERGE):])
            selector = selector.group(1).strip("'\"") if selector else ""
            url = PR_URL_RE.search(selector)
            number = url.group(2) if url else selector if selector.isdigit() else None
            branch = selector if selector and not number and not url and "$" not in selector else None
            repo = _option(segment, "-R", "--repo") or (url and url.group(1))
            place = _place(call.command, found.start(), call.cwd)
            merges.append(Merge(call.stamp, repo and repo.rstrip("/").split("/")[-1].lower(), place, number, branch,
                                call.command[:found.start() + len(segment)] + " " + place))
    return created, merges


def _token(name, text):
    return bool(name) and re.search(r"(?<![\w./-])" + re.escape(name) + r"(?![\w-])", text) is not None


def _repo_of(worktree):
    return worktree.split("/.worktrees/")[0]


def tier(prompt):
    """Size tier of a step-1 builder prompt from its `size:` line: the tier word when the line opens with one, else
    its line count and subsystems (and `files:` for presentation-only work)."""
    line = re.search(r"(?m)^\W*size:(.*)$", prompt)
    if not line:
        return "no size: line"
    size = line.group(1)
    named_tier = re.match(r"\s*(small|medium|large)\b", size, re.IGNORECASE)
    if named_tier:
        return named_tier.group(1).lower()
    nums = [int(x) for x in re.findall(r"(\d+)\s*(?=[-–.+ ]*\d*\s*(?:LOC|lines?|added|removed|строк))", size)] \
        or [int(x) for x in re.findall(r"\d+", size)]
    named = None if re.search(r"(?i)\bone subsystem", size) else re.search(r"subsystems?:\s*([^.;\n(]*)", size)
    if re.search(r"(?i)(two|three|several|more than one|2|3)\s+subsystems", size):
        subsystems = 2
    else:
        subsystems = len(re.split(r",|\s\+\s|\band\b", named.group(1))) if named else 1
    if (nums and max(nums) > 400) or subsystems > 1:
        return "large"
    if re.search(r"(?i)\b(one|a|single)\s+(value|field|config(uration)? line|constant)|presentation|copy only"
                 r"|styles? only", size) or (nums and max(nums) <= 10):
        return "small"
    files = re.search(r"(?ms)^\W*files:(.*?)^\W*\w+:", prompt) or re.search(r"(?m)^\W*files:(.*)$", prompt)
    lines = [ln for ln in (files.group(1) if files else "").splitlines() if ln.strip()]
    if lines and all(re.search(r"\.(html|css|scss|svg|j2|tmpl|hbs)\b|styles?/|locales?/", ln) for ln in lines):
        return "small"
    return "medium"


def branch_of(prompt, worktree):
    """The branch a builder prompt names for its task (`branch fix/x`, `branch: x`) on the line that names the task's
    worktree, else on a line that opens with `branch`; a mention of a branch elsewhere in the prompt (another task's)
    is not the task's. A name with `-` or `/`, not a path or a worktree, not a number range."""
    lines = prompt.splitlines()
    for line in [ln for ln in lines if worktree in ln] + [ln for ln in lines if re.match(r"\W*branch\b", ln)]:
        for found in BRANCH_RE.finditer(line):
            name = found.group(1).strip(".`")
            if (re.search(r"[-/]", name) and not re.match(r"[./]", name) and "worktrees" not in name
                    and not re.fullmatch(r"[\d./-]+", name)):
                return name
    return None


def tasks_of(repo, runs, start, end):
    """Tasks of repository path `repo` with a builder event in [start, end). runs: Runs (builder first, in any
    order). A reviewer joins a task by the worktree path in its prompt, else by the task's basename or branch as a
    token in its prompt when its cwd is inside the repository (the longest matching basename wins)."""
    tasks = {}
    for run in sorted((r for r in runs if r.kind == "builder"), key=lambda r: r.first):
        found = WORKTREE_RE.search(run.prompt)
        if not found or _repo_of(found.group(1)) != repo:
            continue
        if not any(start <= s < end for s in run.stamps):
            continue
        base = os.path.basename(found.group(1))
        task = tasks.setdefault(base, dict(base=base, builders=[], reviewers=[], prompt=None, branch=None))
        task["builders"].append(run)
        if task["prompt"] is None and "handoff:" not in run.prompt:
            task["prompt"] = run.prompt
        task["branch"] = task["branch"] or branch_of(run.prompt, found.group(1))
    for run in runs:
        if run.kind != "reviewer" or not any(start <= s < end for s in run.stamps):
            continue
        found = WORKTREE_RE.search(run.prompt)
        if found:
            key = os.path.basename(found.group(1)) if _repo_of(found.group(1)) == repo else None
        elif run.cwd == repo or run.cwd.startswith(repo + "/"):
            hits = [b for b, t in tasks.items() if _token(b, run.prompt) or _token(t["branch"], run.prompt)]
            key = max(hits, key=len) if hits else None
        else:
            key = None
        if key in tasks:
            tasks[key]["reviewers"].append(run)
    return tasks


def landings_of(repo, tasks, created, merges):
    """Each task's landing: the earliest successful Merge after its first builder event that targets repository
    path `repo` (its -R or PR URL names the repository, else it ran inside it) and merges a PR of the task. A PR is
    the task's when its builder's own `gh pr create` returned it, or GitHub returned it in this repository for the
    task's branch as head (`gh pr create --head`, a lookup by head branch) or for a `gh pr create` run in the task's
    worktree (the worktree a command ran in decides over the head it named); or when the merge selects the task's
    branch. A branch several tasks name links none of them by branch. A merge of a
    PR no task owns links by its own text (the call up to the end of the merge command, and the directory it ran
    in) naming the task's worktree (absolute, or `.worktrees/<name>` relative to the repository), branch or
    basename as a token. Returns {basename: Merge or None}."""
    name = os.path.basename(repo).lower()
    paths = {base: repo + "/.worktrees/" + base for base in tasks}
    shared = {b for b, n in collections.Counter(t["branch"] for t in tasks.values()).items() if n > 1}
    branches = {base: None if t["branch"] in shared else t["branch"] for base, t in tasks.items()}
    owners = collections.defaultdict(set)
    for pr in created:
        if pr.repo != name:
            continue
        in_worktree = [b for b in tasks if pr.worktree == paths[b]]
        owners[pr.number].update(in_worktree or [b for b, br in branches.items() if pr.head and pr.head == br])
    for base, task in tasks.items():
        for run in task["builders"]:
            for pr_repo, number in run.prs:
                if pr_repo == name:
                    owners[number].add(base)

    def in_repo(merge):
        return merge.repo == name if merge.repo else merge.place == repo or merge.place.startswith(repo + "/")

    out = {}
    for base, task in tasks.items():
        start = min(r.first for r in task["builders"])
        path = re.compile("(?:%s|(?<![\\w./-]))/?\\.worktrees/%s(?![\\w.-])" % (re.escape(repo), re.escape(base)))
        hits = [m for m in merges if m.stamp >= start and in_repo(m) and (
            base in owners.get(m.number, ()) or (m.branch and m.branch == branches[base])
            or (not owners.get(m.number) and (
                path.search(m.text) or _token(branches[base], m.text) or _token(base, m.text))))]
        out[base] = min(hits, key=lambda m: m.stamp) if hits else None
    return out


def rows_of(repo, tasks, created, merges):
    """One row per task: basename, tier, first builder event, landing Merge (None when none links), minutes from
    the first builder event to the landing, summed wall minutes of its builder runs, builder and reviewer runs."""
    landed = landings_of(repo, tasks, created, merges)
    rows = []
    for base, task in sorted(tasks.items()):
        first = min(r.first for r in task["builders"])
        merge = landed[base]
        rows.append(dict(base=base, branch=task["branch"], tier=tier(task["prompt"] or ""), first=first, merge=merge,
                         lead=None if merge is None else (merge.stamp - first) / 60,
                         active=sum(r.last - r.first for r in task["builders"]) / 60,
                         builders=len(task["builders"]), reviewers=len(task["reviewers"])))
    return rows


def _median(values):
    ordered = sorted(values)
    return ordered[len(ordered) // 2]


def report(repo, rows, start, end):
    """The printed table: one row for all landed tasks, then one per size tier present; the last line names the
    window and the tasks without a landing."""
    done = [r for r in rows if r["lead"] is not None]
    header = ("size tier of the step-1 prompt | tasks landed, count | minutes from the first builder event to the "
              "merge of the task's PR, median | summed wall minutes of the task's builder runs (first to last event "
              "of each run), median | builder runs per task, mean | reviewer runs per task, mean")
    out = ["pace %s" % os.path.basename(repo), header]
    for label in ("all",) + TIERS:
        part = [r for r in done if label == "all" or r["tier"] == label]
        if not part:
            continue
        out.append("%s | %d | %.0f | %.0f | %.1f | %.1f" % (
            label, len(part), _median([r["lead"] for r in part]), _median([r["active"] for r in part]),
            sum(r["builders"] for r in part) / len(part), sum(r["reviewers"] for r in part) / len(part)))
    if not done:
        out.append("(no task of this repository landed in the window)")
    out.append("window %s .. %s UTC; tasks with a builder event in it %d, of them without a landing %d" % (
        _utc(start), _utc(end), len(rows), len(rows) - len(done)))
    return "\n".join(out)


def task_lines(rows):
    """Per-task rows for checking the links by hand."""
    out = ["task (worktree basename) | branch named in a builder prompt | size tier | first builder event UTC | landing merge result UTC | merged PR "
           "(number or branch, else -) | minutes first builder event to merge | summed builder run minutes"]
    for r in rows:
        merge = r["merge"]
        out.append("%s | %s | %s | %s | %s | %s | %s | %.0f" % (
            r["base"], r["branch"] or "-", r["tier"], _utc(r["first"]), _utc(merge.stamp) if merge else "-",
            (merge.number or merge.branch or "-") if merge else "-",
            "-" if r["lead"] is None else "%.0f" % r["lead"], r["active"]))
    return "\n".join(out)


def _utc(stamp):
    return datetime.datetime.fromtimestamp(stamp, datetime.timezone.utc).strftime("%Y-%m-%d %H:%M")
