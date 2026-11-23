#!/usr/bin/env bash
# Build a throwaway git repo from example/ for the demo, with two months of history. The first
# commit (60 days before the demo date) holds the open items of example/ plus the finished items
# that were already open then. After it, one commit each and in date order: items are added
# (demo/opened.txt, and the opened column of demo/done.txt), the #working item is started, and
# the finished items of demo/done.txt are deleted. That gives the opened vs closed chart, the
# Done drawer, card history and the focus project cards something to show.
# Usage: history.sh <out-dir> <YYYY-MM-DD>
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
out=$1 today=$2
rm -rf "$out" && cp -r "$here/../example" "$out" && cd "$out"
git init -q -b main
export GIT_AUTHOR_NAME=demo GIT_AUTHOR_EMAIL=demo@example.com
export GIT_COMMITTER_NAME=demo GIT_COMMITTER_EMAIL=demo@example.com

# A project file is its header plus the items present at that point, by ID number. Each item
# (its line and indented sub-lines) is kept in $blk/<PROJECT>/<n>; $blk/<PROJECT>/on/<n> marks
# the present ones.
blk=.git/demo
for f in projects/*.md; do
  p=$(basename "$f" .md)
  mkdir -p "$blk/$p/on"
  awk -v d="$blk/$p" '
    /^- [A-Z][A-Z0-9]*-[0-9]+ #/ { split($2, a, "-"); cur = d "/" a[2]; print > cur; print "" > (d "/on/" a[2]); next }
    cur && /^[ \t]/ { print > cur; next }
    !cur { print > (d "/header") }' "$f"
done
while read -r _ id; do rm "$blk/${id%-*}/on/${id##*-}"; done < <(grep -v '^#' "$here/opened.txt")
while read -r opened _ _ id rest; do
  p=${id%-*} n=${id##*-}
  mkdir -p "$blk/$p/on"
  echo "- $id $rest" > "$blk/$p/$n"
  if [ "$opened" = - ]; then : > "$blk/$p/on/$n"; fi
done < <(grep -v '^#' "$here/done.txt")
# The #working item starts as #next, so its card history shows the move.
working=$(grep -ho '^- [A-Z0-9]*-[0-9]* #working ' projects/*.md | cut -d' ' -f2 || true)
for id in $working; do sed -i "s/^- $id #working /- $id #next /" "$blk/${id%-*}/${id##*-}"; done

render() {
  for d in "$blk"/*/; do
    p=$(basename "$d")
    { if [ -f "$d/header" ]; then cat "$d/header"; fi
      for n in $(ls "$d/on" | sort -n); do cat "$d/$n"; done; } > "projects/$p.md"
  done
}

day= k=0
commit() {  # <days before the demo date> <message>; commits of one day are 10 minutes apart
  local d t
  d=$(date -d "$today -$1 days" +%F)
  if [ "$d" = "$day" ]; then k=$((k + 1)); else day=$d k=0; fi
  t=$(printf '%sT%02d:%02d:00' "$d" $((9 + k / 6)) $((k % 6 * 10)))
  GIT_AUTHOR_DATE=$t GIT_COMMITTER_DATE=$t git commit -qm "$2"
}

render
git add -A
commit 60 "Start Tasker"

# <days before> <order: add, start, done> <line no> <kind> <ID>, oldest first.
events=$(
  { grep -v '^#' "$here/opened.txt" | awk '{print $1, 0, NR, "add", $2}'
    grep -v '^#' "$here/done.txt" | awk '$1 != "-" {print $1, 0, NR, "add", $4}'
    for id in $working; do echo "3 1 0 start $id"; done
    grep -v '^#' "$here/done.txt" | awk '{print $2, 2, NR, "done", $4}'
  } | sort -k1,1nr -k2,2n -k3,3n
)
while read -r ago _ _ kind id; do
  p=${id%-*} n=${id##*-}
  case $kind in
    add) : > "$blk/$p/on/$n"; msg=added ;;
    start) sed -i "s/^- $id #next /- $id #working /" "$blk/$p/$n"; msg=started ;;
    done) rm "$blk/$p/on/$n"; msg=done ;;
  esac
  render
  git add projects
  commit "$ago" "$id: $msg"
done <<< "$events"

# The history must end exactly at example/.
diff -r -x .git "$here/../example" . >&2
