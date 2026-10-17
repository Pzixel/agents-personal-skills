#!/bin/sh
# Link this checkout's skills and orch directories into the Codex and Claude Code
# config directories on macOS and import rules/always-on.md into each client's
# user-level rules file. Idempotent: rerun after every pull.
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

common=$(cd "$root" && echo superpowers-*-psilon dragonfly-scripting test-audit pareto-design-selection retro)
for s in $common; do
  [ -d "$root/$s" ] || { echo "Missing skill directory: $root/$s" >&2; exit 1; }
done

# One "<destination> <source>" pair per line; names contain no spaces.
plan() {
  for t in $targets; do
    if [ "$t" = codex ]; then
      for s in $common codex-simplify; do echo "$codex/skills/$s $s"; done
    else
      for s in $common orch; do echo "$claude/skills/$s $s"; done
      for d in agents hooks tools; do
        for e in $(cd "$root/orch/$d" && echo *); do [ "$e" = __pycache__ ] || echo "$claude/$d/$e orch/$d/$e"; done
      done
    fi
  done
}

rules="$root/rules/always-on.md"
begin="<!-- BEGIN agents-personal-skills rules/always-on.md -->"
end="<!-- END agents-personal-skills rules/always-on.md -->"
[ -f "$rules" ] || { echo "Missing rules file: $rules" >&2; exit 1; }
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

# Check every destination before changing anything.
plan | while read -r dest src; do
  if [ -e "$dest" ] || [ -L "$dest" ]; then
    [ "$(realpath "$dest" 2>/dev/null)" = "$root/$src" ] && continue
    echo "Conflicting destination: $dest" >&2
    exit 1
  fi
done

# Dangling links into this checkout are retired skills, agents, hooks or tools.
# A directory that is itself a link (legacy whole-directory install) is the checkout; skip it.
for t in $targets; do
  if [ "$t" = codex ]; then dirs="$codex/skills"; else dirs="$claude/skills $claude/agents $claude/hooks $claude/tools"; fi
  for dir in $dirs; do
    [ -d "$dir" ] && [ ! -L "$dir" ] || continue
    for l in "$dir"/*; do
      [ -L "$l" ] && [ ! -e "$l" ] || continue
      case $(readlink "$l") in
        "$root"/*) rm "$l"; echo "removed $l" ;;
      esac
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

real_path() {
  if [ -e "$1" ]; then realpath "$1"
  elif [ -L "$1" ]; then
    lt=$(readlink "$1")
    case $lt in /*) echo "$lt" ;; *) echo "$(dirname "$1")/$lt" ;; esac
  else mkdir -p "$(dirname "$1")"; echo "$(realpath "$(dirname "$1")")/$(basename "$1")"
  fi
}

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

# Claude Code: one import line in the user CLAUDE.md, unless a line-start @-import
# already loads a file holding the Codex block; then a direct line is removed.
# Codex has no import: one marked block holding the file, replaced on every run
# (targets list codex first, so with `both` the block exists before the check).
for t in $targets; do
  if [ "$t" = claude ]; then
    line="@$rules"
    cm=$(real_path "$claude/CLAUDE.md")
    mkdir -p "$(dirname "$cm")"
    touch "$cm"
    via=""
    for ref in $(sed -n 's/^@//p' "$cm"); do
      case $ref in "~/"*) ref="$HOME/${ref#"~/"}" ;; esac
      if [ -f "$ref" ] && grep -qxF "$begin" "$ref"; then via=$ref; break; fi
    done
    if [ -n "$via" ]; then
      if grep -qxF "$line" "$cm"; then
        start_tmp "$cm"
        grep -vxF "$line" "$cm" > "$tmp" || true
        finish_tmp "$cm" "removed $line from $cm: rules arrive via $via"
      else
        echo "skipped $line: $cm already imports the rules block via $via"
      fi
    elif ! grep -qxF "$line" "$cm"; then
      if [ -s "$cm" ] && [ -n "$(tail -c 1 "$cm")" ]; then echo >> "$cm"; fi
      echo "$line" >> "$cm"
      echo "added $line to $cm"
    fi
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
