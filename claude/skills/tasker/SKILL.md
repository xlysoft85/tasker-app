---
name: tasker
description: Read or update my Tasker list of open items (git repo ~/my-tasker) — what's on my plate, what's due, add an item, mark one done, record what it waits on. Use whenever the work in this session changes an item's state.
---

# Tasker

My open items live in **`~/my-tasker`**, a git repo. Change that path here if yours differs.

## Reading

- `git -C ~/my-tasker pull --ff-only` first.
- `TASKER.md` is the overview: *Needs you now*, then every project. It is **generated**, so never edit it.
- One project's items are in `projects/<ID>.md`. Projects are registered in `projects.yaml`.

## Item format

One line per item, nothing else on the line:

    - <ID> #<state> <Title> — <note> · since:YYYY-MM-DD · check:YYYY-MM-DD · on:<what or whom> · doc:<repo/path#section>

Only ID, `#state` and Title are required. Optional fields go in any order, each starting with ` · `.

| State | Use when |
|-------|----------|
| `#next` | I (or you) can act on it now |
| `#waiting` | It waits on a thing, a person or other items. Always give `on:` and a `check:` date; `since:` when known |
| `#watch` | Done, but needs a check later (`check:` or a condition in the note) |
| `#someday` | Wanted, not planned |

## Changing items

1. Edit the line in `projects/<ID>.md`:
   - **finished → delete the line.** Git keeps the history and the dashboard lists it under *Done*.
   - new item → the next free number in that file (highest + 1). Never renumber.
   - `on:` may name other item IDs (`on:WEB-1, WEB-3`). The item shows as unblocked once those are gone.
2. New project: add `{id, name, area, status: active}` to `projects.yaml` and create `projects/<ID>.md`.
   Areas are listed in `tasker.yaml`.
3. Commit with the item IDs in the message (`WEB-4: waiting on legal`) and push.
   If the push is rejected, `git pull --rebase` and push again.

## Rules

- Update in the **same session** that changed the state: got an answer, merged a PR, received a part.
- Items hold state. Context (why, how, logs) stays in the project's own docs, and the item links to it with `doc:`.
- No secrets, tokens or passwords in items.
