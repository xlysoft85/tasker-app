# Tasker

**One plain-text list of everything on your plate, kept up to date by you and your AI agents,
shown as a dashboard.**

Each open item is one line of Markdown in a git repo:

```
- WEB-1 #waiting Legal sign-off on the cookie banner — sent Monday · check:2026-11-02 · on:Legal (Dana)
```

Tasker reads those files and builds a static dashboard, a kanban board, and a `TASKER.md` overview.
It shows what you can act on, what you're working on, what you're waiting on and since when, and
which check dates have come due. It also flags waiting items whose blockers are finished, shows each
item's history, and lists what you finished this week. History and finished items come from git.

**[Live demo →](https://xlysoft85.github.io/tasker-app/)** (built from [`example/`](example/); the
*Done* list comes from [`demo/done.txt`](demo/done.txt))

## Why plain text

- **Agents can maintain it.** Claude Code or any coding agent with a checkout updates an item in the
  same session that did the work, with no API, MCP server or extra tool. Setup for Claude Code
  (skill, session-start hook, permissions): [docs/claude-code.md](docs/claude-code.md). Other agents: [docs/agents.md](docs/agents.md).
- **Git is the history.** A finished item is a deleted line, and `git log -p` shows when and why.
- **Nothing to host.** The dashboard is one static HTML file. Put it on GitHub Pages, serve it from
  any box, or just read `TASKER.md` on GitHub.

## Item format

```
- <ID> #<state> <Title> — <note> · since:YYYY-MM-DD · check:YYYY-MM-DD · on:<what or whom> · doc:<repo/path#section>
```

Only `ID`, `#state` and `Title` are required. The note and the ` · key:value` fields are optional and
can come in any order. Indented lines under an item make a short sub-list.

| State | Meaning | Usually has |
|-------|---------|-------------|
| `#next` | You (or an agent) can act on it now | — |
| `#working` | Someone is on it right now (optional: progress on the board comes from git history, not from this state) | — |
| `#waiting` | Waiting on a thing, a person or another item | `on:`, `since:`, `check:` |
| `#watch` | Done, but verify when a condition holds | `check:` |
| `#someday` | Wanted, not planned | — |

- **`check:`** is the date to look again. On that date the item moves to *Needs you now*.
- **`on:`** names what it waits on. When it names other item IDs (`on:WEB-1, WEB-3`) and all of them
  are gone, the item shows as **unblocked**. Only IDs whose prefix is a project in `projects.yaml`
  count: a ticket key from another tool (`on:SUP-4412`) is just text and never unblocks the item.
- **IDs** are `<PROJECT>-<n>`. Take the highest number in the file + 1 and never renumber.
- **Finished → delete the line.**

Projects are registered in `projects.yaml` (id, name, area, status, docs). Each active project has
a `projects/<ID>.md`. Typos, unknown states, duplicate IDs and orphan files show in a red box at the
top of the dashboard.

## The page

One static `index.html` with its CSS and JavaScript inline: no server, no CDN, no build step.

- **Board.** Columns *Someday* · *Next* · *Working* · *Blocked* · *Done*. *Blocked* holds `#waiting`
  items and `#watch` items, which carry a **verify** badge. *Working* holds the focus project cards
  (see *This week*); its header reads `3 focus projects · 2 active` and is empty without focus
  projects. There is no item counter: the limit is the focus projects, and their progress is read
  from git history, never from a state you have to set. `#working` items still show in *Working*,
  under the project cards. A card shows its ID, project (in its area's
  colour), title, `check:` date and `on:`. Cards that need you now have a red edge and sort first:
  their check date has come, or everything they wait on is done (**unblocked**). Check dates are red
  when due and amber within three days.
- **Done drawer.** The narrow column on the right; click it to open. It lists items whose line was
  deleted in the last 7 days (the ID is gone from every file), with the date.
- **Flow.** Above the board, *opened this week: N · closed this week: N* (Monday to Sunday). An
  item counts as opened in the week its ID's line was first added (an edit, a state change or a
  move to another file is not an opening). It counts as closed in the week its line was deleted,
  if the ID is gone from every file.
- **Filters.** Area chips: `default_area` sets the first one, then each browser remembers your last
  choice. Also a project menu, a *needs you now* toggle, and **Group by project**, which gives each
  project its own row across the columns. **Search** (press `/`) matches ID, title, note, `on:` and
  project; `Esc` clears it.
- **Card details.** Click a card, or open `…/#WEB-3`, to see every field: the note, the indented
  sub-lines, the `doc:` link, and the **history**. The history lists the commits that changed that
  item's line in the last 90 days, newest first, with the state change (`#next → #working`). Edits to
  the sub-lines alone are not counted. One `git log -p` pass over `projects/` reads history, Done and
  the flow numbers.
- **Routines** view: what runs on its own, from `routines.yaml` (see below). Without that file the
  view is hidden.
- **This week** shows the picks from `FOCUS.md` next to **opened vs closed** per week for the last
  eight weeks: paired columns, a tooltip per week, and the same numbers as a table. A week with no
  git data (before the repo's first commit, or no git checkout at all) is left empty rather than
  drawn as zero. The week of the first commit counts every item in it as opened.
  `FOCUS.md` format, three lines at most. A pick is an **item** ID or a **project** ID, and one file
  may mix both:

  ```
  # Focus — week of 2026-11-02
  1. WEB-3 — Redirect map ready · done when: all 140 old URLs map to a new page
  2. PLAN — Q1 goals draft shared · done when: three outcomes with one owner each are on the page
  ```

  - An **item pick** (`WEB-3`) shows its item's current state. It counts as done when its line is gone.
  - A **project pick** (`PLAN`) must be a project in `projects.yaml`; an unknown one shows in the red
    *Format problems* box. It shows the project's name and open item count, and counts as done only
    when the project has `status: done` (or is gone from `projects.yaml` after its items closed).
  - Each open project pick also gets a **focus project card** at the top of *Working* (three at
    most). Everything on it that moves is read from the same `git log -p` pass, so nobody has to
    update a state for the card to show progress:
    - an **activity chip**: `active today`, `active 1 d ago`, `active 2 d ago`, then `quiet 3 d`
      and up, from the last commit that added, changed or deleted a line of any of the project's
      items (any `PLAN-n`, listed or not). `quiet` alone: no such change in the last 90 days.
      The *Working* header counts the cards that are active (2 days or less).
    - a **progress bar**, `n of m done`, for the pick's `(items: …)` list (no list, no bar). An item
      counts as done when its line was deleted (the ID is gone from every file). A listed ID that
      is neither open nor in the last 90 days of history (a typo, or closed long ago) is left out
      of `m`.
    - **closed this week**: how many of the project's items closed Monday–today, with their IDs
      as links. This is the main progress signal, so it sits right under the bar.
    - then the outcome's *done when*, the listed items, open items by state, how many are due, and
      links to the project's `docs:` (labelled by file name, linked like `doc:`).

    The cards follow the area chips. Click one to filter the board to that project and open its
    details (progress, activity, closed, open items) with the latest history of its items (also
    `…/#PLAN`). Without a git checkout there is no history, so no activity chip and no bar.

    ```
    FOCUS PROJECT                                       work
    PLAN  Q1 planning                       active 1 d ago
    Q1 goals draft shared with the team
    ██████░░░░░░░░░░░░  1 of 3 done
    ✓ 2 closed this week: PLAN-2, PLAN-6
    ```
  - An outcome may end with `(items: PLAN-1, PLAN-3)`. The list is taken out of the outcome text and
    shown as item links on the card and in *This week*, where a project pick with a list also shows
    the same `n of m done`.
  - Lines that are not numbered picks are notes. A note starting `Next up:` also shows as a muted
    line under the focus project cards.
- **Theme** follows the system. The button in the corner cycles *Auto → Light → Dark*, and each
  browser remembers the choice.
- The header shows how old the page is; with `stale_minutes` set, a red bar says the build has stopped.

The page needs JavaScript. Without it, read `TASKER.md`.

## Routines

`routines.yaml` in the data repo lists the jobs that run on their own: a cron job, a scheduled
agent, a CI workflow. The Routines view shows the list; Tasker does not run them or check that they
ran.

```yaml
- name: Friday review
  description: What closed, what is stuck; writes next week's picks to FOCUS.md
  schedule: "Fri 16:00"          # free text; quote a value that starts with *, or contains ": " or " #"
  runs_on: laptop, scheduled task
  account: personal agent        # free text: who or what it runs as
  status: active                 # active | paused | planned
  doc: handbook/planning/weekly-review.md   # like an item's doc: — a repo from tasker.yaml becomes a link
```

Only `name` is required, and `status` defaults to `active`. An unknown key, a bad status or a YAML
error shows in the red *Format problems* box. YAML reads ` #` as the start of a comment, so an unquoted
`description: drafts nudges for #waiting items` ends at "for". Quote such values.

## Quick start (GitHub Pages)

1. Copy the example into a new repo of your own:

   ```bash
   git clone https://github.com/xlysoft85/tasker-app
   mkdir my-tasker && cp -r tasker-app/example/. my-tasker/
   cd my-tasker && git init -b main && git add . && git commit -m "Start Tasker"
   ```

2. Create the repo on GitHub and push it.
3. In the repo's **Settings → Pages**, set *Source* to **GitHub Actions**.
4. Edit `tasker.yaml` (title, areas, links), replace the example projects with your own, and push.

The included workflow ([`example/.github/workflows/tasker.yml`](example/.github/workflows/tasker.yml))
uses this repo as an action, pinned to `@v1`. It rebuilds on every push and once a day, publishes
the page and commits `TASKER.md` back when an item changed. To pick up a newer Tasker, change the tag.

> **Private items?** GitHub Pages on a private repo needs a paid plan, and the page is still public
> unless you're on Enterprise Cloud. If that doesn't suit you, keep the repo private, delete the two
> Pages steps from the workflow and read `TASKER.md` on GitHub, or run Tasker on your own machine (below).

## Run it yourself

Needs Python 3.11+ and PyYAML.

```bash
python3 tasker.py --repo ~/my-tasker --site ~/tasker-site            # pull, build, commit + push TASKER.md
python3 tasker.py --repo ~/my-tasker --site /tmp/site --no-git       # local preview, touches no git
```

From cron every 15 minutes, serving `~/tasker-site` with any static web server:

```
*/15 * * * *  python3 /opt/tasker/tasker.py --repo ~/.local/share/tasker/data >> ~/tasker-build.log 2>&1
```

Give the build its **own checkout** of the data repo, separate from the one you edit by hand, so a
half-done edit is never pulled over or committed.

**Keep the code out of the data repo.** Anyone (or any agent) who can push items should not be able
to change the script that runs on your build host. Install a tagged release of this repo and
update it on purpose:

```bash
git clone --branch v1.5.0 https://github.com/xlysoft85/tasker-app /opt/tasker
```

## Configuration

`tasker.yaml` in the data repo. Every key is optional.

| Key | Default | What |
|-----|---------|------|
| `title` | `Tasker` | Page and `TASKER.md` heading |
| `areas` | areas used in `projects.yaml` | Allowed areas, in display order. Sets the filter chips and their colours |
| `source` | — | `{name, url}` for the header link to the data repo |
| `schedule` | — | Text shown in the header, e.g. `rebuilds every 15 min` |
| `refresh_minutes` | `15` | Page auto-reload; `0` turns it off |
| `stale_minutes` | `0` (off) | Red *stale* bar when the page is older than this — the build has stopped. Set it to about 3× your build interval; leave it off if you build only on push |
| `default_area` | `all` | Area chip selected on a browser's first visit; after that the browser remembers the last chip |
| `wip_limit` | — | Unused since 1.6.0 (it was the *Working* item limit). Allowed and ignored, so an old `tasker.yaml` needs no change |
| `focus` | `FOCUS.md` | File with the week's picks, shown as *This week* (see above). Missing file → no picks, just the closed-per-week chart |
| `routines` | `routines.yaml` | File for the Routines view (see above). Missing file → no Routines view |
| `commit` | git's own identity | `{name, email}` for the `TASKER.md` commits |
| `repos` | — | `name: url` or `name: {url, branch}`. Turns `doc:<name>/<path>` into a link |

## Contributing

Issues and pull requests are welcome. Tasker is deliberately one file with one dependency, so please
keep it that way. Preview a change against the example with
`demo/history.sh /tmp/demo 2026-11-02 && python3 tasker.py --repo /tmp/demo --site /tmp/site --no-git --today 2026-11-02`.

## Licence

MIT
