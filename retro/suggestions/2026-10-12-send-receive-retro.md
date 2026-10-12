# Suggestions for the `retro` skill

Source: retro of the send/receive optimization sessions, 2026-10-12.
Target file: `~/.claude-accounts/work/skills/retro/SKILL.md`.
Not applied; the owner of the skill decides.

## 1. Scope by topic, not only by project

Observed: work on a product's send/receive ran from three working directories.
Two main sessions ran in a backend repository, one in the product repository.
A project-scoped retro misses two of the three main sessions.

Add to **Scope**:

> A topic in the arguments: index every session in every project directory
> and in Codex by human-message keywords and touched repositories, then
> select by content. List the selected sessions with one line each and the
> candidates you rejected, so the user can correct the scope.

## 2. Rank by leverage with rough cost proxies

The skill forbids invented cost but gives no cheap proxy, so ranking by
leverage stalls. Add to **Analysis**:

> Rough cost proxies are allowed and sufficient for ordering: wall-clock
> from the first wrong decision to the user correction that ended it,
> number of compactions inside that span, number of subagent transcripts
> spawned in it, and the work later discarded. Give an order of magnitude
> (hours, not minutes) and say which proxy you used.

## 3. Let the arguments override the change count

The report allows zero to three changes. The user asked for five problems.
Add: "The arguments may set the number of findings; they override the
default limit."

## 4. Check when and where a rule was loaded, not only whether it exists

Observed: the "count operations per unit, fix the largest gap" lesson was
saved on 2026-10-07 in the product's project memory and in `user-rules.md`. The
receive session started next day in the backend repository and repeated
the same mistake for about 9 hours.

Add to **Analysis**, after "search the instructions that were loaded":

> For each candidate rule, compare its creation date with the episode time
> and check whether it was loaded in that session's working directory.
> Classify as: absent; present but not loaded (wrong project, wrong
> memory); loaded but not applied. Each class needs a different fix: write
> the rule; move it to the owner that every affected session loads; move it
> to the point of execution (the skill or hook that runs at that moment)
> with a mandatory artifact a reviewer can check.

## 4a. Distinguish "rule exists" from "rule reached the decision"

Second pass (27 sessions): in six sessions the needed rule was loaded at
session start and not applied hours later, while the same instruction
placed in the prompt was followed every time.
Add to **Analysis**: "When a loaded rule was not applied, the fix is a
mechanism at the decision point (a prompt-time hook, a skill step, a
mandatory first artifact), never a reworded rule. Check whether the user
had to repeat the rule in chat; each repeat is evidence for this class."

## 5. Generalize every finding before reporting it

The owner's test for a finding: an agent on a different domain must not
hit the same problem. Add to **Analysis**:

> A finding is complete only when it states the general property (the
> class of task, the invariant, the first artifact to produce) and one
> worked example from a different domain than the sessions reviewed. "Agents
> did not count operations per email" is a symptom; "any numeric goal starts
> with a unit of work and a per-unit cost table" is the finding.

## 6. High-signal markers for agent mistakes

Add to **Sources** (highest-signal events):

> - user pushback phrases such as "это не то", "почему так", "неправильно",
>   "откуда", "почему так долго", "я не понимаю", "сколько можно";
> - agent self-admissions such as "моя ошибка", "вы правы", "я ввёл в
>   заблуждение", "неверное объяснение";
> - user status questions during autonomous runs ("какой статус", "чего
>   ждём") — they often mark a stall;
> - the same correction repeated in a later session — a multiplier on cost.

## 7. Filter harness noise in long autonomous sessions

`/loop` re-invocations, Stop-hook feedback, goal check-ins, cross-session
messages, and continuation summaries make up most "human" records in long
sessions. Add: "Exclude these from human-message extraction; read them only
when the question is about the loop or the coordination itself."

## 8. Ship the extraction helpers with the skill

The retro needed two small scripts: one prints human messages with JSONL
line numbers and timestamps, one prints assistant text for a line range.
Add them as `scripts/humans.py` and `scripts/assistant.py` and reference
them from **Sources**. Working copies: `/tmp/retro-sendrecv/humans.py`,
`/tmp/retro-sendrecv/asst.py`.
