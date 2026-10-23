#!/usr/bin/env bash
# Claude Code SessionStart hook: pull the Tasker data repo and print what needs you now.
# Whatever this prints goes into the session's context.
# Usage: tasker-status.sh <data-repo> [dashboard-url]
d=${1:-$HOME/my-tasker} url=${2:-}
[ -d "$d/.git" ] || exit 0
if ! out=$(git -C "$d" pull --ff-only --quiet 2>&1); then
  echo "Tasker: pull failed — ${out##*$'\n'}"
fi
t="$d/TASKER.md"
[ -f "$t" ] || exit 0
now=$(awk '/^## Needs you now/{f=1;next} /^## /{f=0} f && /^- /' "$t" | sed -E 's/\*\*//g; s/`//g' | cut -c1-160)
echo "Tasker — needs you now${url:+ ($url)}:"
echo "${now:-- nothing due}"
