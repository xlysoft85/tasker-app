#!/usr/bin/env bash
# Build a throwaway git repo from example/ for the demo: every item in demo/done.txt is added,
# then deleted in a commit dated that many days before the demo date, so "Done in the last
# 7 days" and the "updated" dates have something to show. Usage: history.sh <out-dir> <YYYY-MM-DD>
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
out=$1 today=$2
rm -rf "$out" && cp -r "$here/../example" "$out" && cd "$out"
git init -q -b main
export GIT_AUTHOR_NAME=demo GIT_AUTHOR_EMAIL=demo@example.com
export GIT_COMMITTER_NAME=demo GIT_COMMITTER_EMAIL=demo@example.com

commit() {  # <date> <message>
  GIT_AUTHOR_DATE="$1T10:00:00" GIT_COMMITTER_DATE="$1T10:00:00" git commit -qam "$2"
}

done_items=$(grep -v '^#' "$here/done.txt" | sort -rn)
while read -r _ line; do
  id=${line#- }; id=${id%% *}
  echo "$line" >> "projects/${id%-*}.md"
done <<< "$done_items"
git add -A
commit "$(date -d "$today -10 days" +%F)" "Start Tasker"

while read -r ago line; do
  id=${line#- }; id=${id%% *}
  f="projects/${id%-*}.md"
  grep -vxF -- "$line" "$f" > "$f.tmp"; mv "$f.tmp" "$f"
  commit "$(date -d "$today -$ago days" +%F)" "$id done"
done <<< "$done_items"
