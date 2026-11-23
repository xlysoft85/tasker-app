# Letting agents keep Tasker up to date

Tasker works best when whoever does the work — you, Claude Code, Codex, any agent with a checkout —
updates the item in the same session. Paste this into the agent's instructions (`CLAUDE.md`,
`AGENTS.md`, a system prompt) and adjust the path.

```markdown
## Tasker — open items

My open items live in `~/my-tasker` (git). One line per item in `projects/<ID>.md`:

    - <ID> #next|working|waiting|watch|someday <Title> — <note> · since:YYYY-MM-DD · check:YYYY-MM-DD · on:<what/IDs> · doc:<repo/path>

- Pull before reading. `TASKER.md` is generated — never edit it.
- When this session changes an item's state (got an answer, merged a PR, received a part), edit its line,
  commit and push before ending.
- Finished → delete the line (git keeps the history). New item → next free number in that file; never renumber.
- Items normally go from `#next` straight to done. `#working` is rarely needed: the board reads
  progress from git history, not from this state.
- Parking work on someone (Legal, a vendor, another team): `#waiting` with `on:` and a `check:` date,
  and end the note with `then: <first step when it clears>`.
- Ticket keys from another tracker (`SUP-4412`) go in the note or in `on:`. They are not items: they
  never count as done and never unblock anything. Never register a project whose ID is such a prefix.
- `FOCUS.md` holds this week's picks, three at most: normally a project ID with `(items: …)`, or an
  item ID. Read it to know what matters this week; only the weekly review writes it.
- Context (why, how, logs) stays in the project's own docs; the item links to it with `doc:`.
- No secrets in items.
```

For a work project, also name its Tasker ID in that project's own instructions ("open items:
`projects/WEB.md` in my-tasker"), so the agent reads one file instead of the whole list.

If an agent runs your weekly review, give it the `FOCUS.md` format from the
[README](../README.md#the-weeks-focus-focusmd): up to three lines
`N. <PROJECT> — <outcome> (items: <IDs>) · done when: <test>`, then optional `Next up:` and
`Last week` notes.
