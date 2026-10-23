# Tasker

**One plain-text list of everything on your plate, kept up to date by you and your AI agents,
shown as a dashboard.**

Each open item is one line of Markdown in a git repo:

```
- WEB-1 #waiting Legal sign-off on the cookie banner — sent Monday · check:2026-11-02 · on:Legal (Dana)
```

Tasker reads those files and builds a static dashboard and a `TASKER.md` overview. It shows
what you can act on, what you're waiting on and since when, and which check dates have come due.
It also flags waiting items whose blockers are finished, and lists what you finished this week,
read from git history.

**[Live demo →](https://xlysoft85.github.io/tasker-app/)** (built from [`example/`](example/))

## Why plain text

- **Agents can maintain it.** Claude Code or any coding agent with a checkout updates an item in the
  same session that did the work, with no API, MCP server or extra tool. See [docs/agents.md](docs/agents.md).
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
| `#waiting` | Waiting on a thing, a person or another item | `on:`, `since:`, `check:` |
| `#watch` | Done, but verify when a condition holds | `check:` |
| `#someday` | Wanted, not planned | — |

- **`check:`** is the date to look again. On that date the item moves to *Needs you now*.
- **`on:`** names what it waits on. When it names other item IDs (`on:WEB-1, WEB-3`) and all of them
  are gone, the item shows as **unblocked**.
- **IDs** are `<PROJECT>-<n>`. Take the highest number in the file + 1 and never renumber.
- **Finished → delete the line.**

Projects are registered in `projects.yaml` (id, name, area, status, docs). Each active project has
a `projects/<ID>.md`. Typos, unknown states, duplicate IDs and orphan files show in a red box at the
top of the dashboard.

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
git clone --branch v1.0.0 https://github.com/xlysoft85/tasker-app /opt/tasker
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
| `commit` | git's own identity | `{name, email}` for the `TASKER.md` commits |
| `repos` | — | `name: url` or `name: {url, branch}`. Turns `doc:<name>/<path>` into a link |

## Contributing

Issues and pull requests are welcome. Tasker is deliberately one file with one dependency, so please
keep it that way. Preview a change against the example with
`python3 tasker.py --repo example --site /tmp/site --no-git --today 2026-11-02`.

## Licence

MIT
