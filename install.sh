#!/bin/sh
# Link this checkout's skills and orch directories into the Codex and Claude Code
# config directories on macOS, import rules/always-on.md into each client's
# user-level rules file and rules/always-on-claude.md into the Claude Code one.
# Idempotent: rerun after every pull.
set -eu

[ $# -eq 1 ] || set -- ""
case "$1" in
  claude) targets=claude ;;
  codex) targets=codex ;;
  both) targets="codex claude" ;;
  *) echo "usage: $0 claude|codex|both" >&2; exit 2 ;;
esac

root=$(dirname "$(realpath "$0")")
codex="$HOME/.agents"
codex_rules="${CODEX_HOME:-$HOME/.codex}/AGENTS.md"
claude="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"

# Claude Code loads only the planning superpowers skill: the other four had 0 loads
# there while orch covers their jobs (ORIGINS.md); Codex gets all five.
shared="superpowers-writing-plans-psilon dragonfly-scripting test-audit pareto-design-selection retro"
codex_only="superpowers-dispatching-parallel-agents-psilon superpowers-receiving-code-review-psilon superpowers-requesting-code-review-psilon superpowers-verification-before-completion-psilon"
for s in $shared $codex_only; do
  [ -d "$root/$s" ] || { echo "Missing skill directory: $root/$s" >&2; exit 1; }
done

# One "<destination> <source>" pair per line; names contain no spaces.
plan() {
  for t in $targets; do
    if [ "$t" = codex ]; then
      for s in $shared $codex_only codex-simplify; do echo "$codex/skills/$s $s"; done
    else
      for s in $shared orch; do echo "$claude/skills/$s $s"; done
      # orch agents reach Claude Code through the orch plugin as `orch:<name>`.
      for d in hooks tools; do
        for e in $(cd "$root/orch/$d" && echo *); do [ "$e" = __pycache__ ] || echo "$claude/$d/$e orch/$d/$e"; done
      done
    fi
  done
}

rules="$root/rules/always-on.md"
begin="<!-- BEGIN agents-personal-skills rules/always-on.md -->"
end="<!-- END agents-personal-skills rules/always-on.md -->"
claude_rules="$root/rules/always-on-claude.md"
for f in "$rules" "$claude_rules"; do
  [ -f "$f" ] || { echo "Missing rules file: $f" >&2; exit 1; }
done
for f in "$claude/CLAUDE.md" "$codex_rules"; do
  if [ -e "$f" ] && [ ! -r "$f" ]; then echo "Cannot read $f" >&2; exit 1; fi
done

# A Codex rules file may hold at most one complete marked block, BEGIN before END.
case " $targets " in
  *" codex "*)
    if [ -f "$codex_rules" ]; then
      nb=$(grep -cxF "$begin" "$codex_rules" || true)
      ne=$(grep -cxF "$end" "$codex_rules" || true)
      bl=$(grep -nxF "$begin" "$codex_rules" | cut -d: -f1)
      el=$(grep -nxF "$end" "$codex_rules" | cut -d: -f1)
      if [ "$nb" -gt 1 ] || [ "$nb" != "$ne" ] || { [ "$nb" = 1 ] && [ "$bl" -gt "$el" ]; }; then
        echo "Malformed always-on block in $codex_rules (BEGIN=$nb END=$ne)" >&2
        exit 1
      fi
    fi ;;
esac

# The path a destination resolves to: its realpath, a link's own target when
# dangling, or (a new file) the resolved parent plus the name.
real_path() {
  if [ -e "$1" ]; then realpath "$1"
  elif [ -L "$1" ]; then
    lt=$(readlink "$1")
    case $lt in /*) echo "$lt" ;; *) echo "$(dirname "$1")/$lt" ;; esac
  else mkdir -p "$(dirname "$1")"; echo "$(realpath "$(dirname "$1")")/$(basename "$1")"
  fi
}

# Check every destination before changing anything.
plan | while read -r dest src; do
  if [ -e "$dest" ] || [ -L "$dest" ]; then
    [ "$(real_path "$dest")" = "$root/$src" ] && continue
    echo "Conflicting destination: $dest" >&2
    if [ -L "$dest" ]; then
      echo "It links to $(readlink "$dest"); if that is an old checkout of this repository, remove the links into it first." >&2
    fi
    exit 1
  fi
done

# Links into this checkout that the plan no longer names (retired or moved
# skills, agents, hooks, tools; the legacy whole-directory agents link) are removed.
planned=$(plan | cut -d' ' -f1)
unplanned() {
  case $(real_path "$1") in "$root"/*) ;; *) return 1 ;; esac
  ! printf '%s\n' "$planned" | grep -qxF "$1"
}
for t in $targets; do
  if [ "$t" = codex ]; then dirs="$codex/skills"; else dirs="$claude/skills $claude/agents $claude/hooks $claude/tools"; fi
  for dir in $dirs; do
    if [ -L "$dir" ]; then
      # A whole-directory link is the legacy install; only agents is retired.
      if [ "$dir" = "$claude/agents" ] && unplanned "$dir"; then rm "$dir"; echo "removed $dir"; fi
      continue
    fi
    [ -d "$dir" ] || continue
    for l in "$dir"/*; do
      [ -L "$l" ] && unplanned "$l" || continue
      rm "$l"; echo "removed $l"
    done
  done
done

plan | while read -r dest src; do
  [ -e "$dest" ] && continue
  mkdir -p "$(dirname "$dest")"
  ln -s "$root/$src" "$dest"
  echo "linked $dest -> $root/$src"
done

# Always-on rules. A rewritten file is written at its symlink target (dangling
# links included) through a sibling temp file: mode kept, hard links not.
tmp=""
trap '[ -z "$tmp" ] || rm -f "$tmp"' EXIT


# Start $tmp next to $1, carrying $1's mode when it exists (new file: 600).
start_tmp() {
  mkdir -p "$(dirname "$1")"
  tmp=$(mktemp "$1.XXXXXX")
  if [ -f "$1" ]; then cp -p "$1" "$tmp"; fi
}

# Replace $1 with $tmp unless the content is unchanged; $2 is the message.
finish_tmp() {
  if [ -f "$1" ] && cmp -s "$tmp" "$1"; then rm "$tmp"; else mv "$tmp" "$1"; echo "$2"; fi
  tmp=""
}

# Claude Code import lines escape spaces as `\ `: an unescaped spaced path is not loaded.
esc() { printf '%s' "$1" | sed 's/ /\\ /g'; }

# Drop line $2 from CLAUDE.md $1; $3 is the message.
drop_line() {
  start_tmp "$1"
  grep -vxF "$2" "$1" > "$tmp" || true
  finish_tmp "$1" "$3"
}

# Append import line @$2 (escaped) to $1 unless present; an older unescaped line is replaced.
add_import() {
  new="@$(esc "$2")"
  if [ "$new" != "@$2" ] && grep -qxF "@$2" "$1"; then drop_line "$1" "@$2" "removed unescaped @$2 from $1"; fi
  if ! grep -qxF "$new" "$1"; then
    if [ -s "$1" ] && [ -n "$(tail -c 1 "$1")" ]; then echo >> "$1"; fi
    echo "$new" >> "$1"
    echo "added $new to $1"
  fi
}

# Claude Code: one import line per rules file in the user CLAUDE.md. For
# always-on.md the line is skipped when a line-start @-import
# already loads a file holding the Codex block; then a direct line is removed.
# Codex has no import: one marked block holding the file, replaced on every run
# (targets list codex first, so with `both` the block exists before the check).
for t in $targets; do
  if [ "$t" = claude ]; then
    line="@$(esc "$rules")"
    cm=$(real_path "$claude/CLAUDE.md")
    mkdir -p "$(dirname "$cm")"
    touch "$cm"
    via=""
    while IFS= read -r ref; do
      [ -n "$ref" ] || continue
      ref=$(printf '%s' "$ref" | sed 's/\\ / /g')
      case $ref in "~/"*) ref="$HOME/${ref#"~/"}" ;; esac
      if [ -f "$ref" ] && grep -qxF "$begin" "$ref"; then via=$ref; break; fi
    done <<EOF
$(sed -n 's/^@//p' "$cm")
EOF
    if [ -n "$via" ]; then
      found=no
      for l in "$line" "@$rules"; do
        if grep -qxF "$l" "$cm"; then found=yes; drop_line "$cm" "$l" "removed $l from $cm: rules arrive via $via"; fi
      done
      [ $found = yes ] || echo "skipped $line: $cm already imports the rules block via $via"
    else
      add_import "$cm" "$rules"
    fi
    add_import "$cm" "$claude_rules"
  else
    real=$(real_path "$codex_rules")
    has_block=no
    if [ -f "$real" ] && grep -qxF "$begin" "$real"; then has_block=yes; fi
    start_tmp "$real"
    {
      if [ $has_block = yes ]; then
        awk -v b="$begin" '$0 == b { exit } { print }' "$real"
      elif [ -s "$real" ]; then
        cat "$real"
        [ -z "$(tail -c 1 "$real")" ] || echo
      fi
      echo "$begin"
      cat "$rules"
      echo "$end"
      if [ $has_block = yes ]; then
        awk -v e="$end" 'after { print } $0 == e { after = 1 }' "$real"
      fi
    } > "$tmp"
    finish_tmp "$real" "updated always-on block in $real"
  fi
done

# A skill or agent name visible from two sources (an old link, a plugin
# install) is reported by the orch duplicate check; any line fails the run.
case " $targets " in
  *" claude "*)
    dups=$(CLAUDE_CONFIG_DIR="$claude" python3 "$root/orch/tools/dup-skills")
    if [ -n "$dups" ]; then
      echo "$dups" >&2
      echo "Remove one copy of each name above and rerun." >&2
      exit 1
    fi ;;
esac
