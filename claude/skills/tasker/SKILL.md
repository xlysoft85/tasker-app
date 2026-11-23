---
name: tasker
description: Read or update my Tasker list of open items (git repo ~/my-tasker) — what's on my plate, what's due, this week's focus, add an item, mark one done, record what it waits on. Use whenever the work in this session changes an item's state.
---

# Tasker

My open items live in **`~/my-tasker`**, a git repo. Change that path here if yours differs.

## Reading

- `git -C ~/my-tasker pull --ff-only` first.
- `TASKER.md` is the overview: *Needs you now*, then every project. It is **generated**, so never edit it.
- One project's items are in `projects/<ID>.md`. Projects are registered in `projects.yaml`.
- `FOCUS.md` is this week's focus: up to three outcomes, usually project picks (see below).

## Item format

One line per item, nothing else on the line:

    - <ID> #<state> <Title> — <note> · since:YYYY-MM-DD · check:YYYY-MM-DD · on:<what or whom> · doc:<repo/path#section>

Only ID, `#state` and Title are required. Optional fields go in any order, each starting with ` · `.

| State | Use when |
|-------|----------|
| `#next` | I (or you) can act on it now. Most items go from here straight to done |
| `#working` | Someone is on it right now. Rarely used: the board shows progress from git history, not from this state |
| `#waiting` | It waits on a thing, a person or other items. Always give `on:` and a `check:` date; `since:` when known |
| `#watch` | Done, but needs a check later (`check:` or a condition in the note) |
| `#someday` | Wanted, not planned |

## Changing items

1. Edit the line in `projects/<ID>.md`:
   - **finished → delete the line.** Git keeps the history and the dashboard lists it under *Done*.
   - new item → the next free number in that file (highest + 1). Never renumber.
   - `on:` may name other item IDs (`on:WEB-1, WEB-3`). The item shows as unblocked once those are gone.
   - **parking work on someone** (Legal, a vendor, another team): `#waiting` with `on:` and `check:`,
     and end the note with `then: <first step when it clears>`:

         - VEND-6 #waiting CRM renewal quote — asked for a two-year price; then: compare it with the one-year price · since:2026-10-24 · check:2026-11-05 · on:vendor (Alex)

   - **ticket keys from another tracker** (`SUP-4412`) go in the note or in `on:`. Only IDs whose
     prefix is a project in `projects.yaml` are items, so a ticket key never counts as done and never
     unblocks anything. Never register a project whose ID is another tracker's prefix.
2. New project: add `{id, name, area, status: active}` to `projects.yaml` and create `projects/<ID>.md`.
   Areas are listed in `tasker.yaml`.
3. Commit with the item IDs in the message (`WEB-4: waiting on legal`) and push.
   If the push is rejected, `git pull --rebase` and push again.

## This week's focus (`FOCUS.md`)

Written by the weekly review; change it only when I ask. Format:

    # Focus — week of 2026-11-02 (W45)

    1. WEB — New site running on staging (items: WEB-2, WEB-12, WEB-14) · done when: staging passes the smoke test
    2. PLAN — Q1 goals draft shared (items: PLAN-1, PLAN-5) · done when: three outcomes, one owner each

    Next up: ONB — first-week checklist (ONB-1)
    Last week (W44): TALK-2 done · PLAN-1 not done (waits on PLAN-5)

- Three picks at most. A pick is normally a **project ID** with an `(items: …)` list: the board gives
  it a focus project card with progress (`n of m done`, from deleted lines), activity and what closed
  this week, all read from git. An **item ID** pick is the fallback for a one-item outcome.
- Other lines are notes; `Next up:` also shows under the focus cards.

## Rules

- Update in the **same session** that changed the state: got an answer, merged a PR, received a part.
- Items hold state. Context (why, how, logs) stays in the project's own docs, and the item links to it with `doc:`.
- No secrets, tokens or passwords in items.
