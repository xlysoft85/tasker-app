# Letting agents keep Tasker up to date

Tasker works best when whoever does the work — you, Claude Code, Codex, any agent with a checkout —
updates the item in the same session. Paste this into the agent's instructions (`CLAUDE.md`,
`AGENTS.md`, a system prompt) and adjust the path.

```markdown
## Tasker — open items

My open items live in `~/my-tasker` (git). One line per item in `projects/<ID>.md`:

    - <ID> #next|waiting|watch|someday <Title> — <note> · since:YYYY-MM-DD · check:YYYY-MM-DD · on:<what/IDs> · doc:<repo/path>

- Pull before reading. `TASKER.md` is generated — never edit it.
- When this session changes an item's state (got an answer, merged a PR, received a part), edit its line,
  commit and push before ending.
- Finished → delete the line (git keeps the history). New item → next free number in that file; never renumber.
- `#waiting` items always get `on:` and a `check:` date.
- Context (why, how, logs) stays in the project's own docs; the item links to it with `doc:`.
- No secrets in items.
```

For a work project, also name its Tasker ID in that project's own instructions ("open items:
`projects/WEB.md` in my-tasker"), so the agent reads one file instead of the whole list.
