# Changelog

Each version is a git tag; `v1` points at the latest 1.x release. `git log` has the full story.

## 1.6.1 — 2026-11-23

- The focus project modal lists the pick's `(items: …)` as **Items · n of m done**, above History:
  one row per ID with its title, state, check date and (for a waiting item) `on:`. Closed items are
  struck through with `done <date>`, and IDs that never existed show as `unknown ID`. This replaces
  the bare row of IDs.

## 1.6.0 — 2026-11-23

- Focus project cards read their progress from git history: an **activity chip** (`active today`,
  `active 1 d ago`, `quiet 3 d`), a **progress bar** `n of m done` for the pick's `(items: …)` list
  (done = the line was deleted), and **closed this week** with linked IDs right under the bar. The
  project modal shows the same.
- The *Working* header counts focus projects (`3 focus projects · 2 active`) instead of items. The
  item counter and its amber over-limit state are gone; `wip_limit` in `tasker.yaml` is allowed and
  ignored. `#working` items still show under the cards.
- *This week* shows `n of m done` next to project picks that have an items list.

## 1.5.1 — 2026-11-22

- A focus outcome may end with `(items: WEB-2, WEB-12)`. The list leaves the outcome text and shows
  as item links on the card, in its modal and in *This week*.
- A `FOCUS.md` note starting `Next up:` also shows, muted, under the focus project cards.

## 1.5.0 — 2026-11-22

- A `FOCUS.md` pick may be a project ID (`PLAN`) as well as an item ID (`WEB-3`), and one file may
  mix both. An ID that is not in `projects.yaml` shows in *Format problems*. A project pick is done
  only when the project has `status: done`.
- Each open project pick gets a **focus project card** at the top of *Working*, three at most: the
  outcome and *done when*, open items by state, due count, the project's docs and its items closed
  this week. Clicking a card filters the board to the project and opens a project modal with the
  latest history of its items (also `…/#PLAN`).

## 1.4.1 — 2026-11-21

- Only IDs whose prefix is a project in `projects.yaml` count in `on:`. Ticket keys from other tools
  (`on:SUP-4412`) are plain text: they never make an item unblocked and are never struck through.
- A `#waiting` item with no `check:` date whose `on:` names only such a ticket shows *no check date*.

## 1.4.0 — 2026-11-21

- **Opened vs closed** per week: *opened this week · closed this week* above the board, and an
  eight-week chart with tooltips and a table view in *This week*. Opened = the week an item's line
  was first added; closed = the week its line was deleted and the ID is gone everywhere. Weeks
  without git data are left empty, not drawn as zero.

## 1.3.0 — 2026-11-21

- **Kanban board**: *Someday · Next · Working · Blocked · Done*, with `#watch` items in *Blocked*
  marked **verify**. Optional *Group by project* lanes, a project menu, a *needs you now* toggle, and
  area chips remembered per browser (`default_area` sets the first one).
- New `#working` state, with a `wip_limit` for the *Working* column (dropped in 1.6.0).
- **Card details** with every field and the item's history: the commits that changed its line in
  the last 90 days, read in one `git log -p` pass.
- **Done drawer** (lines deleted in the last 7 days), a light/dark theme toggle, and the
  **Routines** view from `routines.yaml`.

## 1.2.0 — 2026-11-20

- Git output with bytes that are not UTF-8 no longer breaks the build.
- `stale_minutes`: a red bar when the page is older than that, so a stopped build is visible.
- *This week*: the `FOCUS.md` picks with each item's live state, next to items closed per week.
- Search (`/` to focus, `Esc` clears); the area chips also filter *Needs you now*.

## 1.1.0 — 2026-10-23

- Claude Code setup: the `tasker` skill, a session-start hook and [docs/claude-code.md](docs/claude-code.md).
- A bigger example, with a week of finished items in the demo (`demo/history.sh`).
- *Done in the last 7 days* counts back from `--today`, not from the wall clock.

## 1.0.0 — 2026-10-23

- First release: `tasker.py` reads `projects.yaml` and `projects/*.md` and builds a static dashboard
  and `TASKER.md`, configured by `tasker.yaml`. It runs as a GitHub Action or from cron, with
  example data, a GitHub Pages demo and instructions for agents.
