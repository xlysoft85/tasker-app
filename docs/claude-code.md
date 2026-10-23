# Claude Code setup

Three pieces, each optional. With all three, every Claude Code session starts knowing what is due,
reads and updates your items when the work changes them, and `/tasker` answers "what's on my plate?".

The examples assume your data repo is cloned at **`~/my-tasker`**. Replace that path everywhere if yours
differs, including in the skill.

## 1. The skill: how to read and edit items

```bash
git clone https://github.com/xlysoft85/tasker-app ~/src/tasker-app
mkdir -p ~/.claude/skills && cp -r ~/src/tasker-app/claude/skills/tasker ~/.claude/skills/
```

It holds the item format and rules. Claude loads it when a task touches your items, and you can call
it directly with `/tasker` ("/tasker what's waiting on other people?", "/tasker add: renew the domain by Friday").

## 2. Your `CLAUDE.md`: make updating a habit

The skill teaches *how*; this makes Claude do it unasked. Add to `~/.claude/CLAUDE.md`, which every
session loads:

```markdown
## Tasker
My open items live in `~/my-tasker` (use the `tasker` skill). When this session changes an item's
state (an answer arrived, a PR merged, something finished), update the item, commit and push before ending.
```

For a project with its own items, name the Tasker ID in that project's `CLAUDE.md`, so a session
reads one file instead of the whole list:

```markdown
Open items: `projects/WEB.md` in ~/my-tasker (Tasker project WEB).
```

## 3. The session-start hook: see what's due

```bash
mkdir -p ~/.claude/hooks && cp ~/src/tasker-app/claude/hooks/tasker-status.sh ~/.claude/hooks/
```

Then add this to `~/.claude/settings.json`. If the file already has `hooks` or `permissions`, merge
these entries into them instead:

```json
{
  "hooks": {
    "SessionStart": [
      { "hooks": [ { "type": "command",
                     "command": "bash ~/.claude/hooks/tasker-status.sh ~/my-tasker https://you.github.io/my-tasker/",
                     "timeout": 30 } ] }
    ]
  },
  "permissions": {
    "allow": [
      "Bash(git -C ~/my-tasker pull:*)",
      "Edit(~/my-tasker/projects/**)",
      "Edit(~/my-tasker/projects.yaml)"
    ]
  }
}
```

Each new session pulls the repo and starts with a block like this:

```
Tasker — needs you now (https://you.github.io/my-tasker/):
- ONB-2 waiting Laptop for Sam — ordered, delivery expected Thursday — on: IT parcel — check 2026-10-31 (2 d overdue)
- WEB-6 waiting New header photos from the design agency — on: WEB-2 — unblocked
```

The `permissions` entries let Claude pull and edit items without asking each time. Commits and pushes
still ask, unless you also allow `Bash(git commit:*)` and `Bash(git push:*)`.

The dashboard's *Needs you now* comes from `TASKER.md`, which your workflow regenerates after each push.
Locally edited items show up there once that build has run and the next session pulls.

## Other agents

Codex, Cursor and other agents with a checkout: see [agents.md](agents.md) for a block to paste into
their instructions.
