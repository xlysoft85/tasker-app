#!/usr/bin/env python3
"""Tasker: read a data repo (tasker.yaml, projects.yaml, projects/*.md), write a static
index.html and the generated TASKER.md (committed back if changed).

Run it from cron, CI or by hand. Keep this script out of the data repo: anyone who can push
items there should not be able to change the code that runs on the build host.
"""
import argparse
import datetime as dt
import html
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

VERSION = "1.5.1"
STATES = ["working", "next", "waiting", "watch", "someday"]
ITEM_RE = re.compile(r"^- ([A-Z][A-Z0-9]*-\d+) #(\w+) (.+)$")
ID_RE = re.compile(r"\b[A-Z][A-Z0-9]*-\d+\b")
KEYS = ("since", "check", "on", "doc")
AREA_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
# Area chip colours, light and dark, in the order tasker.yaml lists the areas.
AREA_COLORS = [("#e2f3e5", "#243d2a"), ("#e3ecfb", "#24334c"), ("#fbe3e3", "#4a2b2b"),
               ("#f7edd8", "#463a20"), ("#ece4f7", "#3a2d4c"), ("#dcf1ef", "#1f3d3a")]
CFG = {}  # tasker.yaml, set by main()


def load_config(repo):
    """tasker.yaml → settings; every key is optional."""
    problems, raw = [], {}
    path = repo / "tasker.yaml"
    if path.exists():
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception as e:  # noqa: BLE001 — show any config error on the page
            problems.append(f"tasker.yaml: {e}")
    if not isinstance(raw, dict):
        problems.append("tasker.yaml: expected key: value settings")
        raw = {}
    repos = {}
    for name, r in (raw.get("repos") or {}).items():
        r = {"url": r} if isinstance(r, str) else (r or {})
        repos[str(name)] = (str(r.get("url", "")).rstrip("/"), str(r.get("branch", "main")))
    areas = [str(a) for a in raw.get("areas") or []]
    problems += [f"tasker.yaml: area '{a}' — use lowercase letters, digits, - and _"
                 for a in areas if not AREA_RE.match(a)]
    source = raw.get("source") or {}
    if isinstance(source, str):
        source = {"name": source.removeprefix("https://github.com/"), "url": source}
    wip = raw.get("wip_limit", 5)
    if isinstance(wip, bool) or not isinstance(wip, int) or wip < 1:
        problems.append(f"tasker.yaml: wip_limit '{wip}' — use a whole number, 1 or more")
        wip = 5
    default_area = str(raw.get("default_area") or "all")
    if default_area != "all" and areas and default_area not in areas:
        problems.append(f"tasker.yaml: default_area '{default_area}' — use all or one of the areas")
        default_area = "all"
    return {"title": str(raw.get("title") or "Tasker"), "areas": areas, "repos": repos,
            "source": source, "schedule": str(raw.get("schedule") or ""),
            "refresh": int(raw.get("refresh_minutes", 15) or 0),
            "stale": int(raw.get("stale_minutes", 0) or 0),
            "focus": str(raw.get("focus") or "FOCUS.md"),
            "routines": str(raw.get("routines") or "routines.yaml"),
            "wip": wip, "default_area": default_area,
            "commit": raw.get("commit") or {}}, problems


def area_list(projects):
    """Configured areas, or — with none configured — the areas projects use, in order."""
    if CFG["areas"]:
        return CFG["areas"]
    seen = []
    for p in projects:
        if p["area"] not in seen:
            seen.append(p["area"])
    return seen


def git(repo, *args, check=True):
    # errors="replace": one commit with a non-UTF-8 byte must not stop the build.
    return subprocess.run(["git", "-C", str(repo), *args], check=check, capture_output=True,
                          text=True, encoding="utf-8", errors="replace").stdout


def parse_date(s, problems, where):
    try:
        return dt.date.fromisoformat(s.strip())
    except ValueError:
        problems.append(f"{where}: bad date '{s}'")
        return None


def parse_item(m, problems, where):
    iid, state, rest = m.groups()
    parts = rest.split(" · ")
    head, fields = parts[0], parts[1:]
    title, _, note = head.partition(" — ")
    item = {"id": iid, "state": state, "title": title.strip(), "note": note.strip(),
            "extra": [], "since": None, "check": None, "on": "", "doc": ""}
    if state not in STATES:
        problems.append(f"{where}: {iid} unknown state #{state}")
    for f in fields:
        key, sep, val = f.partition(":")
        key = key.strip()
        if sep and key in KEYS:
            if key in ("since", "check"):
                item[key] = parse_date(val, problems, f"{where} {iid}")
            else:
                item[key] = val.strip()
        else:
            item["note"] = (item["note"] + " · " + f).strip(" ·")
    return item


def blame_dates(repo, rel):
    """line number → author date of the commit that last touched it."""
    out = git(repo, "blame", "--line-porcelain", "--", rel, check=False)
    dates, cur = {}, None
    for line in out.splitlines():
        if re.match(r"^[0-9a-f]{40} ", line):
            cur = int(line.split()[2])
        elif line.startswith("author-time ") and cur is not None:
            dates[cur] = dt.date.fromtimestamp(int(line.split()[1]))
    return dates


def load(repo):
    problems = []
    try:
        reg = yaml.safe_load((repo / "projects.yaml").read_text(encoding="utf-8")) or []
    except Exception as e:  # noqa: BLE001 — show any registry error on the page
        reg, problems = [], [f"projects.yaml: {e}"]
    projects, by_id = [], {}
    for p in reg:
        pid = str(p.get("id", "")).strip()
        if not pid:
            problems.append(f"projects.yaml: entry without id: {p}")
            continue
        if CFG["areas"] and p.get("area") not in CFG["areas"]:
            problems.append(f"projects.yaml: {pid} unknown area '{p.get('area')}'")
        proj = {"id": pid, "name": p.get("name", pid), "area": p.get("area", "?"),
                "status": p.get("status", "active"), "docs": p.get("docs") or [], "items": [],
                "registered": True}
        projects.append(proj)
        by_id[pid] = proj
    files = sorted((repo / "projects").glob("*.md"))
    for f in files:
        pid, rel = f.stem, f"projects/{f.name}"
        proj = by_id.get(pid)
        if proj is None:
            problems.append(f"{rel}: no '{pid}' entry in projects.yaml")
            proj = {"id": pid, "name": pid, "area": "?", "status": "active", "docs": [], "items": []}
            projects.append(proj)
            by_id[pid] = proj
        touched = blame_dates(repo, rel)
        last = None
        for n, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            m = ITEM_RE.match(line)
            if m:
                last = parse_item(m, problems, rel)
                last["touched"] = touched.get(n)
                if not last["id"].startswith(pid + "-"):
                    problems.append(f"{rel}: {last['id']} does not belong to project {pid}")
                proj["items"].append(last)
            elif line.startswith("- "):
                problems.append(f"{rel}:{n}: not in item format: {line[:80]}")
                last = None
            elif line.startswith((" ", "\t")) and line.strip() and last:
                last["extra"].append(line.strip().lstrip("-* ").strip())
    for p in projects:
        if p["status"] != "done" and not (repo / "projects" / f"{p['id']}.md").exists():
            problems.append(f"projects.yaml: {p['id']} has no projects/{p['id']}.md")
    seen = {}
    for p in projects:
        for it in p["items"]:
            if it["id"] in seen:
                problems.append(f"duplicate id {it['id']}")
            seen[it["id"]] = it
    return projects, seen, problems


WEEKS = 8  # opened-vs-closed chart: this week and the seven before
HISTORY_DAYS = 90  # card modal: commits that touched the item's line in this many days
HISTORY_MAX = 40  # … at most this many per item


def scan_history(repo, current_ids, today):
    """One `git log -p` pass over projects/ → (history, closed, opened).

    history: id → [(date, short hash, subject, change)], newest first, last HISTORY_DAYS days.
    closed: [(date, id, item)] of items whose line was deleted (the ID is gone everywhere) since
    the Monday WEEKS-1 weeks back, newest first; item is the deleted line, parsed.
    opened: id → date its line was first added, for IDs first added since that Monday. "First
    added" = the oldest commit in the scan that touches the ID adds its line without removing
    one, so an edit or a move between files is not an opening.
    Outside a git repo all three are empty.
    """
    weeks_since = today - dt.timedelta(days=today.weekday() + 7 * (WEEKS - 1))
    since = min(today - dt.timedelta(days=HISTORY_DAYS), weeks_since)
    out = git(repo, "log", f"--since={since} 00:00", f"--until={today} 23:59:59", "-p",
              "--no-renames", "--format=@@C %h %as %s", "--", "projects/", check=False)
    history, done, first = {}, {}, {}
    commit, old, new = None, {}, {}

    def flush():
        if commit is None:
            return
        for iid in list(old) + [i for i in new if i not in old]:
            o, n = old.get(iid), new.get(iid)
            change = (("edited" if o == n else f"#{o} → #{n}") if o and n
                      else (f"added #{n}" if n else "line deleted"))
            hist = history.setdefault(iid, [])
            if len(hist) < HISTORY_MAX:
                hist.append((commit[1], commit[0], commit[2], change))
            first[iid] = (commit[1], bool(n and not o))  # newest first: the last write is the oldest

    for line in out.splitlines():
        if line.startswith("@@C "):
            flush()
            sha, _, rest = line[4:].partition(" ")
            date, _, subject = rest.partition(" ")
            commit, old, new = (sha, date, subject), {}, {}
        elif line.startswith(("-- ", "+- ")) and commit:
            m = ITEM_RE.match(line[1:])
            if not m:
                continue
            iid, state = m.group(1), m.group(2)
            (old if line[0] == "-" else new).setdefault(iid, state)
            if (line[0] == "-" and iid not in current_ids and iid not in done
                    and commit[1] >= str(weeks_since)):
                done[iid] = (commit[1], parse_item(m, [], ""))
    flush()
    closed = sorted(((d, i, it) for i, (d, it) in done.items()), key=lambda c: (c[0], c[1]),
                    reverse=True)
    opened = {i: d for i, (d, added) in first.items() if added and d >= str(weeks_since)}
    return history, closed, opened


def repo_start(repo):
    """Date of the repo's first commit, or None outside a git repo."""
    out = git(repo, "rev-list", "--max-parents=0", "--format=%as", "HEAD", check=False)
    dates = [line for line in out.splitlines() if not line.startswith("commit ")]
    return dt.date.fromisoformat(min(dates)) if dates else None


def recently_done(closed, today, days=7):
    """The closed items of the `days` days up to and including `today`."""
    since = str(today - dt.timedelta(days=days - 1))
    return [c for c in closed if c[0] >= since]


def weekly_flow(closed, opened, today, start):
    """[(label, monday, opened, closed)] for the last WEEKS ISO weeks (Mon–Sun), oldest first;
    the last is this week. A week that ends before `start` (the first commit; None = no git) has
    no data: opened and closed are None there, not 0."""
    out = []
    for k in range(WEEKS - 1, -1, -1):
        mon = today - dt.timedelta(days=today.weekday() + 7 * k)
        lo, hi = str(mon), str(mon + dt.timedelta(days=6))
        label = f"W{mon.isocalendar().week:02d}"
        if start is None or str(start) > hi:
            out.append((label, mon, None, None))
            continue
        out.append((label, mon, sum(1 for d in opened.values() if lo <= d <= hi),
                    sum(1 for c in closed if lo <= c[0] <= hi)))
    return out


# A pick is an item ID (WEB-3) or a project ID (WEB).
FOCUS_RE = re.compile(r"^\d+\.\s+([A-Z][A-Z0-9]*(?:-\d+)?)\s+—\s+(.+)$")
FOCUS_MAX = 3  # focus project cards on the board
FOCUS_ITEMS_RE = re.compile(r"\s*\(items:\s*([^)]*)\)")  # optional "(items: FDB-11, FDB-15)" in an outcome
FOCUS_HISTORY = 10  # project modal: this many latest history entries of the project's items


def load_focus(repo):
    """FOCUS.md → (heading, [(id, outcome, done_when)], [other lines]); None if absent."""
    path = repo / CFG["focus"]
    if not path.is_file():
        return None
    head, picks, notes = "", [], []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        m = FOCUS_RE.match(line)
        if m:
            outcome, _, when = m.group(2).partition(" · done when: ")
            picks.append((m.group(1), outcome.strip(), when.strip()))
        elif line.startswith("# ") and not head:
            head = line[2:].removeprefix("Focus").strip(" —-")
        elif line and not line.startswith("#"):
            notes.append(line)
    return head, picks, notes


def resolve_focus(focus, projects, items, closed):
    """FOCUS.md picks → (heading, [pick], notes), problems.

    A pick is a dict: id, kind (item | project), outcome, when, done, item / proj.
    An item pick is done when its line is gone (as before 1.5.0). A project pick is done when the
    project has status: done, or is gone from projects.yaml while closed items of it are in the
    git window (so it existed). Any other bare ID that is no registered project is a problem.
    """
    if focus is None:
        return None, []
    head, raw, notes = focus
    reg = {p["id"]: p for p in projects if p.get("registered")}
    existed = {c[1].rpartition("-")[0] for c in closed}
    picks, problems = [], []
    for pid, outcome, when in raw:
        m = FOCUS_ITEMS_RE.search(outcome)
        pick = {"id": pid, "outcome": FOCUS_ITEMS_RE.sub("", outcome, count=1).strip(), "when": when,
                "items": ID_RE.findall(m.group(1)) if m else []}
        if "-" in pid:
            picks.append(dict(pick, kind="item", item=items.get(pid), done=pid not in items))
            continue
        p = reg.get(pid)
        if p is None and pid not in existed:
            problems.append(f"{CFG['focus']}: {pid} is not a project in projects.yaml "
                            f"(a pick is an item ID like {pid}-1 or a project ID)")
            continue
        picks.append(dict(pick, kind="project", proj=p, done=p is None or p["status"] == "done"))
    n = sum(1 for pk in picks if pk["kind"] == "project" and not pk["done"])
    if n > FOCUS_MAX:
        problems.append(f"{CFG['focus']}: {n} open project picks — the board shows the first {FOCUS_MAX}")
    return (head, picks, notes), problems


def doc_label(d):
    """A doc's file name for a short link label: work:GTM/PLAN.md → PLAN.md."""
    d = str(d).partition("#")[0].rstrip("/")
    return d.rsplit("/", 1)[-1].rpartition(":")[2].strip() or d


ROUTINE_KEYS = ("name", "description", "schedule", "runs_on", "account", "status", "doc")
ROUTINE_STATUS = ("active", "paused", "planned")


def load_routines(repo):
    """routines.yaml → ([routine], problems); (None, []) when the file is absent."""
    name = CFG["routines"]
    path = repo / name
    if not path.is_file():
        return None, []
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or []
    except Exception as e:  # noqa: BLE001 — show any error on the page
        return [], [f"{name}: {e}"]
    if not isinstance(raw, list):
        return [], [f"{name}: expected a list of routines (- name: …)"]
    routines, problems = [], []
    for n, r in enumerate(raw, 1):
        if not isinstance(r, dict) or not str(r.get("name") or "").strip():
            problems.append(f"{name}: entry {n} has no name")
            continue
        rt = {k: str(r.get(k) if r.get(k) is not None else "").strip() for k in ROUTINE_KEYS}
        problems += [f"{name}: {rt['name']}: unknown key '{k}'" for k in r if k not in ROUTINE_KEYS]
        rt["status"] = rt["status"] or "active"
        if rt["status"] not in ROUTINE_STATUS:
            problems.append(f"{name}: {rt['name']}: status '{rt['status']}' — use "
                            + " | ".join(ROUTINE_STATUS))
        routines.append(rt)
    return routines, problems


def registered(projects):
    """Project IDs from projects.yaml. Only IDs with one of these prefixes are Tasker items."""
    return {p["id"] for p in projects if p.get("registered")}


def item_refs(text, known):
    """Tasker item IDs in a text. Jira keys and other ABC-123 strings whose prefix is not a
    registered project are not items, so they never count as done."""
    return [r for r in ID_RE.findall(text) if r.rpartition("-")[0] in known]


def annotate(projects, items, today):
    """Flags per item: due (check date reached), unblocked (every on: item ID is gone)."""
    known = registered(projects)
    for p in projects:
        for it in p["items"]:
            it["due"] = bool(it["check"] and it["check"] <= today)
            refs = item_refs(it["on"], known)
            it["refs"] = refs
            it["unblocked"] = bool(it["state"] == "waiting" and refs
                                   and all(r not in items for r in refs))
            it["now"] = it["due"] or it["unblocked"]
            it["nocheck"] = it["state"] == "waiting" and not it["check"] and not refs


def sort_key(it):
    return (not it["now"], STATES.index(it["state"]) if it["state"] in STATES else 9,
            it["check"] or dt.date.max)


# ---------------------------------------------------------------- HTML

def esc(s):
    return html.escape(str(s), quote=True)


def doc_href(d):
    """doc:<repo>/<path#anchor> → that file in the repo's web view; "" for a repo not in tasker.yaml."""
    repo, _, path = str(d).partition("/")
    url, branch = CFG["repos"].get(repo, ("", ""))
    if not (url and path):
        return ""
    p, _, anchor = path.partition("#")
    return f"{url}/blob/{branch}/{p}" + (f"#{anchor}" if anchor else "")


def doc_link(d):
    href = doc_href(d)
    return f'<a href="{esc(href)}" target="_blank" rel="noopener">{esc(d)}</a>' if href else esc(d)


def link_ids(text, items, known):
    """Item IDs become links; gone ones are struck. Other ABC-123 strings (Jira keys) stay text."""
    out, pos = [], 0
    for m in ID_RE.finditer(text):
        out.append(esc(text[pos:m.start()]))
        i = m.group(0)
        out.append(f'<a href="#{esc(i)}">{esc(i)}</a>' if i in items else
                   f'<s title="done">{esc(i)}</s>' if i.rpartition("-")[0] in known else esc(i))
        pos = m.end()
    out.append(esc(text[pos:]))
    return "".join(out)


def days_txt(n):
    return "today" if n == 0 else (f"in {n} d" if n > 0 else f"{-n} d overdue")


def week_html(focus, weeks, items, today, start=None, known=frozenset()):
    """The 'This week' strip: FOCUS.md picks with their live state, and opened vs closed per week."""
    parts = []
    if focus:
        head, picks, notes = focus
        cards = {f["id"] for f in focus_cards(picks)}
        lis = []
        for pk in picks:
            iid, outcome, when, got = pk["id"], pk["outcome"], pk["when"], pk["done"]
            name = ""
            if pk["kind"] == "project":
                p = pk["proj"]
                name = f"<div class='pn'>{esc(p['name'] if p else iid)}</div>"
                st, label = ("done", "✓") if got else ("working", "project")
                if p is None:
                    info = "project is gone from projects.yaml — done"
                elif got:
                    info = "project status: done"
                else:
                    n = len(p["items"])
                    info = f"project · {n} open item{'' if n == 1 else 's'}"
                    if p["status"] != "active":
                        info += f" · {p['status']}"
                ref = (f'<a href="#{esc(iid)}">{esc(iid)}</a>' if iid in cards else
                       f"<s>{esc(iid)}</s>" if got else esc(iid))
            else:
                it = pk["item"]
                if it is None:
                    st, label, info = "done", "✓", "line is gone — done"
                else:
                    st = label = it["state"]
                    info = f"#{st}"
                    if it["check"]:
                        n = (it["check"] - today).days
                        info += f" · check {days_txt(n)}"
                ref = f'<a href="#{esc(iid)}">{esc(iid)}</a>' if it else f"<s>{esc(iid)}</s>"
            when_html = f"<div class='meta'>done when: {link_ids(when, items, known)}</div>" if when else ""
            if pk["items"]:
                info_html = esc(info) + " · items: " + link_ids(", ".join(pk["items"]), items, known)
            else:
                info_html = esc(info)
            lis.append(f"<li class='pick{' got' if got else ''}'><div class='st {esc(st)}'>"
                       f"{esc(label)}</div><div>{name}<div class='t'>{link_ids(outcome, items, known)}</div>"
                       f"{when_html}<div class='src'>{ref} · {info_html}</div></div></li>")
        note_html = "".join(f"<div class='meta'>{link_ids(n, items, known)}</div>" for n in notes)
        got = sum(1 for p in picks if p["done"])
        parts.append(f"<div class='focus'><h2>This week <span class='meta'>{esc(head)}</span>"
                     f"<span class='pstatus'>{got} of {len(picks)} done</span></h2>"
                     f"<ul>{''.join(lis) or '<li><div></div><div class=meta>No picks.</div></li>'}</ul>{note_html}</div>")
    parts.append(flow_html(weeks, start))
    return f"<section id='week' class='week{' solo' if not focus else ''}'>{''.join(parts)}</section>"


def nice_top(n):
    """Smallest 'round' axis maximum ≥ n: 1 2 3 4 5 6 8 10 20 30 …"""
    scale = 1
    while True:
        for m in (1, 2, 3, 4, 5, 6, 8):
            if m * scale >= n:
                return m * scale
        scale *= 10


def fmt_week(mon):
    sun = mon + dt.timedelta(days=6)
    return f"{mon:%d %b}–{sun:%d %b}".lstrip("0").replace("–0", "–")


def flow_html(weeks, start):
    """Opened vs closed per week (inline SVG, grouped columns), legend, and the same numbers as a
    table. A week without git data draws no bars (empty, not zero)."""
    W, H, ax, top_y, base = 272, 122, 22, 16, 94
    gw = (W - ax) / len(weeks)
    bw, gap = 11, 2
    vals = [v for _, _, o, c in weeks for v in (o, c) if v is not None]
    top = nice_top(max(vals + [1]))
    ph = base - top_y

    def y(v):
        return base - ph * v / top

    def bar(x, v, cls):
        if v == 0:  # zero is data: a flat stub on the baseline
            return f"<rect class='{cls}' x='{x:.1f}' y='{base - 1.5}' width='{bw}' height='1.5'/>"
        h = base - y(v)
        r = min(3.0, h, bw / 2)
        return (f"<path class='{cls}' d='M{x:.1f},{base}V{base - h + r:.1f}Q{x:.1f},{base - h:.1f} "
                f"{x + r:.1f},{base - h:.1f}H{x + bw - r:.1f}Q{x + bw:.1f},{base - h:.1f} "
                f"{x + bw:.1f},{base - h + r:.1f}V{base}Z'/>")

    svg = []
    for v in ([0, top // 2, top] if top % 2 == 0 else [0, top]):
        svg.append(f"<line class='gl' x1='{ax}' x2='{W}' y1='{y(v):.1f}' y2='{y(v):.1f}'/>"
                   f"<text class='tk' x='{ax - 4}' y='{y(v) + 3:.1f}'>{v}</text>")
    for i, (lbl, mon, o, c) in enumerate(weeks):
        gx = ax + i * gw
        cur = i == len(weeks) - 1
        x0 = gx + (gw - 2 * bw - gap) / 2
        if o is None:
            tip = f"{lbl} ({fmt_week(mon)}): no git data"
            marks = ""
        else:
            tip = f"{lbl} ({fmt_week(mon)}): opened {o} · closed {c}"
            if start and mon <= start <= mon + dt.timedelta(days=6):
                tip += f" · history starts {start}, so opened includes the first import"
            marks = bar(x0, o, "fo") + bar(x0 + bw + gap, c, "fc")
        svg.append(f"<g class='wkg{' cur' if cur else ''}{' nodata' if o is None else ''}'><title>{esc(tip)}</title>"
                   f"<rect class='hit' x='{gx:.1f}' y='0' width='{gw:.1f}' height='{H}'/>{marks}"
                   f"<text class='wl' x='{gx + gw / 2:.1f}' y='{base + 14}'>{lbl}</text></g>")
    empty = all(o is None for _, _, o, _ in weeks)
    if empty:
        svg.append(f"<text class='tk na' x='{ax + (W - ax) / 2:.1f}' y='{top_y + ph / 2:.1f}'>no git history</text>")
    summary = "; ".join(f"{lbl} " + ("no data" if o is None else f"opened {o}, closed {c}")
                        for lbl, _, o, c in weeks)
    rows = "".join(f"<tr><td>{lbl}</td><td>{esc(fmt_week(mon))}</td>"
                   f"<td>{'—' if o is None else o}</td><td>{'—' if c is None else c}</td></tr>"
                   for lbl, mon, o, c in weeks)
    lbl, _, o, c = weeks[-1]
    now = f"<b>{lbl} so far: opened {o} · closed {c}</b> · " if o is not None else ""
    note = (now + "per week, Mon–Sun · opened = item line first added · closed = line deleted, ID gone · "
            + ("from git" if not empty else "needs a git checkout"))
    if start and start > weeks[0][1]:
        note += f" · history starts {start}"
    return (f"<div class='flow'><h2>Opened vs closed <span class='lg'><span><i class='sw fo'></i>opened</span>"
            f"<span><i class='sw fc'></i>closed</span></span></h2>"
            f"<svg viewBox='0 0 {W} {H}' role='img' aria-label='Opened and closed items per week: {esc(summary)}'>"
            f"{''.join(svg)}</svg><div class='meta'>{note}</div>"
            f"<details class='meta'><summary>as a table</summary><table><thead><tr><th>Week</th><th>Mon–Sun</th>"
            f"<th>Opened</th><th>Closed</th></tr></thead><tbody>{rows}</tbody></table></details></div>")


def item_json(it, proj, today):
    """One card of the board: the item's fields, dates as text, day counts relative to today."""
    since, check = it["since"], it["check"]
    return {"id": it["id"], "st": it["state"], "title": it["title"], "note": it["note"],
            "extra": it["extra"], "on": it["on"], "doc": it["doc"],
            "docUrl": doc_href(it["doc"]) if it["doc"] else "",
            "since": str(since) if since else None, "age": (today - since).days if since else None,
            "check": str(check) if check else None, "days": (check - today).days if check else None,
            "touched": str(it["touched"]) if it.get("touched") else None,
            "proj": proj["id"], "pname": proj["name"], "area": proj["area"],
            "paused": proj["status"] != "active", "now": bool(it.get("now")),
            "unblocked": bool(it.get("unblocked")), "nocheck": bool(it.get("nocheck"))}


def focus_cards(picks):
    """The project picks that get a card on the board: open ones, the first FOCUS_MAX."""
    return [pk["proj"] for pk in picks if pk["kind"] == "project" and not pk["done"]][:FOCUS_MAX]


def focus_json(picks, done, history, today):
    """One focus project card each: the week's outcome, open items by state, due count, project
    docs, items closed this week (Mon–today) and the latest history of the project's items."""
    by_pick = {pk["id"]: pk for pk in picks}
    monday = str(today - dt.timedelta(days=today.weekday()))
    out = []
    for p in focus_cards(picks):
        pk, pre = by_pick[p["id"]], p["id"] + "-"
        hist = sorted(((*e, iid) for iid, h in history.items() if iid.startswith(pre) for e in h),
                      key=lambda e: e[0], reverse=True)[:FOCUS_HISTORY]
        out.append({"id": p["id"], "name": p["name"], "area": p["area"], "status": p["status"],
                    "outcome": pk["outcome"], "when": pk["when"], "items": pk["items"],
                    "counts": {s: sum(1 for it in p["items"] if it["state"] == s) for s in STATES},
                    "due": sum(1 for it in p["items"] if it.get("due")),
                    "docs": [[doc_label(d), doc_href(d), str(d)] for d in p["docs"]],
                    "closed": [i for d, i, _ in done if d >= monday and i.startswith(pre)],
                    "hist": hist})
    return out


def board_json(projects, done, history, today, areas, has_git, focus=None):
    """Everything the board, the card modal and the Done drawer need, as one object."""
    by_id = {p["id"]: p for p in projects}
    items = [(p, it) for p in projects for it in p["items"]]
    # Within a column: needs-you-now first, then by check date; file order breaks ties.
    items.sort(key=lambda pi: (not pi[1]["now"], pi[1]["check"] or dt.date.max))
    done_rows = []
    for d, iid, it in done:
        pid = iid.rpartition("-")[0]
        proj = by_id.get(pid) or {"id": pid, "name": pid, "area": "?", "status": "done"}
        done_rows.append(dict(item_json(it, proj, today), date=d))
    cards = focus_json(focus[1], done, history, today) if focus else []
    used = {p["id"] for p, _ in items} | {r["proj"] for r in done_rows} | {f["id"] for f in cards}
    order = {a: i for i, a in enumerate(areas)}
    lanes = sorted((p["id"] for p in projects if p["id"] in used),
                   key=lambda pid: order.get(by_id[pid]["area"], len(order)))
    lanes += sorted(pid for pid in used if pid not in by_id)
    ids = {it["id"] for _, it in items} | {r["id"] for r in done_rows}
    src = CFG["source"].get("url", "") if isinstance(CFG["source"], dict) else ""
    return {"version": VERSION, "today": str(today), "wip": CFG["wip"],
            "defaultArea": CFG["default_area"], "areas": areas, "git": has_git,
            "historyDays": HISTORY_DAYS,
            "projects": {p["id"]: {"name": p["name"], "area": p["area"], "status": p["status"],
                                   "docs": [[str(d), doc_href(d)] for d in p["docs"]]}
                         for p in projects},
            "known": sorted(registered(projects)),
            "lanes": lanes,
            "items": [item_json(it, p, today) for p, it in items],
            "done": done_rows,
            "focus": cards,
            "nextUp": [n for n in (focus[2] if focus else []) if n.lower().startswith("next up:")],
            "history": {i: h for i, h in history.items() if i in ids},
            "commitUrl": f"{str(src).rstrip('/')}/commit/" if src else ""}


def script_json(data):
    """JSON that is safe inside <script type="application/json">: no raw < > or &."""
    s = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return s.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


def routines_html(routines):
    """The Routines view: one row per routine in routines.yaml, active first."""
    if routines is None:
        return ""
    order = {s: i for i, s in enumerate(ROUTINE_STATUS)}
    rows = []
    for r in sorted(routines, key=lambda r: order.get(r["status"], len(order))):
        desc = f"<div class='meta'>{esc(r['description'])}</div>" if r["description"] else ""
        doc = f"<div class='src'>{doc_link(r['doc'])}</div>" if r["doc"] else ""
        cells = "".join(f"<div><span class='rl'>{lbl}</span>{esc(r[k]) or '—'}</div>"
                        for k, lbl in (("schedule", "Schedule"), ("runs_on", "Runs on"),
                                       ("account", "Account")))
        rows.append(f"<div class='rrow' data-status='{esc(r['status'])}'><div class='rmain'>"
                    f"<div class='t'>{esc(r['name'])}</div>{desc}{doc}</div>{cells}"
                    f"<div><span class='rs {esc(r['status'])}'>{esc(r['status'])}</span></div></div>")
    counts = " · ".join(f"{n} {s}" for s in ROUTINE_STATUS
                        if (n := sum(1 for r in routines if r["status"] == s)))
    head = ("<div class='rrow rhead'><div>Routine</div><div>Schedule</div><div>Runs on</div>"
            "<div>Account</div><div>Status</div></div>") if rows else ""
    empty = "" if rows else f"<p class='meta'>No routines in {esc(CFG['routines'])} yet.</p>"
    return (f"<div id='v-routines' class='hidden'><section class='routines'><h2>Routines "
            f"<span class='meta'>— what runs on its own: when, where and as whom · from "
            f"<code>{esc(CFG['routines'])}</code></span><span class='pstatus'>{counts}</span></h2>"
            f"{head}{''.join(rows)}{empty}<p id='rnomatch' class='meta hidden'>No routine matches.</p>"
            f"</section></div>")


def render_html(projects, items, problems, done, today, generated, pull_error,
                focus=None, weeks=(), built=0, stale=0, history=None, routines=None, has_git=True,
                start=None):
    weeks = list(weeks)
    chips = ["all"] + [a for a in area_list(projects) if any(p["area"] == a for p in projects)]
    default = CFG["default_area"] if CFG["default_area"] in chips else "all"
    filt = "".join(f'<button type="button" data-a="{esc(a)}" class="{"" if a == "all" else "ac"}'
                   f'{" on" if a == default else ""}">{esc(a)}</button>' for a in chips)
    views = ('<div class="seg" id="views"><button type="button" data-v="projects" class="on">Projects</button>'
             '<button type="button" data-v="routines">Routines</button></div>') if routines is not None else ""
    warn = []
    if pull_error:
        warn.append(f"git pull failed — page shows the last pulled state: {pull_error}")
    warn += problems
    warn_html = ("<section class='warn'><h2>Format problems</h2><ul>" +
                 "".join(f"<li class='w'>{esc(w)}</li>" for w in warn) + "</ul></section>") if warn else ""
    src = CFG["source"]
    source = (f' from <a href="{esc(src["url"])}" target="_blank" rel="noopener">'
              f'{esc(src.get("name") or src["url"])}</a>') if src.get("url") else ""
    refresh = f'<meta http-equiv="refresh" content="{CFG["refresh"] * 60}">' if CFG["refresh"] else ""
    data = board_json(projects, done, history or {}, today, chips[1:], has_git, focus)
    subs = {"TITLE": esc(CFG["title"]), "VERSION": VERSION, "REFRESH": refresh,
            "DARK": DARK_VARS, "AREACSS": area_css(area_list(projects)), "SOURCE": source,
            "SCHEDULE": f" · {esc(CFG['schedule'])}" if CFG["schedule"] else "",
            "GENERATED": esc(generated), "BUILT": str(int(built)), "STALE": str(int(stale)),
            "WEEK": week_html(focus, weeks, items, today, start, registered(projects)) if weeks else "",
            "WARN": warn_html, "VIEWS": views, "FILTERS": filt,
            "OPENEDNOW": "—" if not weeks or weeks[-1][2] is None else str(weeks[-1][2]),
            "CLOSEDNOW": "—" if not weeks or weeks[-1][3] is None else str(weeks[-1][3]),
            "ROUTINES": routines_html(routines), "DATA": script_json(data)}
    # One pass, so a %NAME% inside item text is never replaced.
    return re.sub(r"%([A-Z]+)%", lambda m: subs.get(m.group(1), m.group(0)), TEMPLATE)


def area_css(areas):
    named = [(i, a) for i, a in enumerate(a for a in areas if AREA_RE.match(a))]
    light = "".join(f"--a-{i}:{AREA_COLORS[i % len(AREA_COLORS)][0]};" for i, _ in named)
    dark = "".join(f"--a-{i}:{AREA_COLORS[i % len(AREA_COLORS)][1]};" for i, _ in named)
    rules = "".join(f'.ac[data-a="{a}"]{{background:var(--a-{i})}}\n' for i, a in named)
    return (f":root{{{light}}}\n@media (prefers-color-scheme: dark){{"
            f':root:not([data-theme="light"]){{{dark}}}}}\n:root[data-theme="dark"]{{{dark}}}\n{rules}')


# ---------------------------------------------------------------- Markdown

def render_md(projects, items, done, today, generated):
    def line(it):
        bits = [f"**{it['id']}** `{it['state']}` {it['title']}"]
        if it["note"]:
            bits.append(it["note"])
        if it["on"]:
            bits.append(f"on: {it['on']}")
        if it["check"]:
            bits.append(f"check {it['check']} ({days_txt((it['check'] - today).days)})")
        if it["unblocked"]:
            bits.append("**unblocked**")
        return "- " + " — ".join(bits)

    out = ["<!-- Generated by tasker.py. Do not edit; edit projects/*.md. -->",
           f"# {CFG['title']}", "", f"_Generated {generated}_", ""]
    now = sorted((it for p in projects for it in p["items"] if it["now"]), key=sort_key)
    out += ["## Needs you now", ""] + ([line(it) for it in now] or ["Nothing due today."]) + [""]
    for p in projects:
        if not p["items"]:
            continue
        out += [f"## {p['name']} · {p['area']} · {p['status']}", ""]
        out += [line(it) for it in sorted(p["items"], key=sort_key)] + [""]
    if done:
        out += ["## Done in the last 7 days", ""] + [f"- {d} **{i}** {it['title']}" for d, i, it in done] + [""]
    return "\n".join(out)


def write_atomic(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", default=os.path.expanduser("~/tasker"))
    ap.add_argument("--site", default=os.path.expanduser("~/tasker-site"))
    ap.add_argument("--no-git", action="store_true", help="no pull, no commit/push (local test)")
    ap.add_argument("--no-pull", action="store_true", help="commit/push but do not pull (CI checkout)")
    ap.add_argument("--today", help="override today's date (YYYY-MM-DD), for testing")
    ap.add_argument("--version", action="version", version=f"tasker {VERSION}")
    a = ap.parse_args()
    repo = Path(a.repo)

    pull_error = ""
    if not a.no_git and not a.no_pull:
        r = subprocess.run(["git", "-C", str(repo), "pull", "-q", "--rebase"],
                           capture_output=True, text=True)
        if r.returncode:
            pull_error = (r.stderr.strip().splitlines() or ["unknown error"])[-1]

    global CFG
    CFG, cfg_problems = load_config(repo)
    today = dt.date.fromisoformat(a.today) if a.today else dt.date.today()
    generated = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    projects, items, problems = load(repo)
    routines, routine_problems = load_routines(repo)
    problems = cfg_problems + problems + routine_problems
    annotate(projects, items, today)
    # History is read even with --no-git (it only reads); outside a git repo it is empty.
    has_git = bool(git(repo, "rev-parse", "--git-dir", check=False).strip())
    history, closed, opened = scan_history(repo, items, today)
    start = repo_start(repo) if has_git else None
    done = recently_done(closed, today)
    focus, focus_problems = resolve_focus(load_focus(repo), projects, items, closed)
    problems += focus_problems
    # The stale warning compares the build time with the viewer's clock, so it is meaningless
    # for a page built with a fixed --today (the demo).
    stale = 0 if a.today else CFG["stale"]

    write_atomic(Path(a.site) / "index.html",
                 render_html(projects, items, problems, done, today, generated, pull_error,
                             focus, weekly_flow(closed, opened, today, start),
                             dt.datetime.now().timestamp(), stale, history, routines, has_git, start))

    # TASKER.md carries the generation time only in its header; compare the rest so a
    # rebuild with no item changes does not create a commit.
    md = render_md(projects, items, done, today, generated)
    target = repo / "TASKER.md"
    old = target.read_text(encoding="utf-8") if target.exists() else ""
    strip = lambda s: re.sub(r"^_Generated .*_$", "", s, flags=re.M)  # noqa: E731
    if strip(old) != strip(md):
        write_atomic(target, md)
        if not a.no_git and not pull_error:
            git(repo, "add", "TASKER.md")
            who = CFG["commit"]
            ident = [x for k in ("name", "email") if who.get(k) for x in ("-c", f"user.{k}={who[k]}")]
            git(repo, *ident, "commit", "-q", "-m", "TASKER.md: regenerate")
            r = subprocess.run(["git", "-C", str(repo), "push", "-q"], capture_output=True, text=True)
            if r.returncode:
                print(f"{generated} push failed: {r.stderr.strip()}", file=sys.stderr)
    if pull_error:
        print(f"{generated} pull failed: {pull_error}", file=sys.stderr)


DARK_VARS = ("color-scheme:dark;--bg:#151514;--panel:#1e1e1c;--card:#252523;--col:#1a1a18;"
             "--ink:#ecebe6;--muted:#9c9b95;--line:#33322f;--next:#58a6ff;--working:#3fc1b0;"
             "--waiting:#e3b341;--watch:#b392f0;--someday:#8b8b86;--done:#56d364;--alert:#f47067;"
             "--chip:#2a2a27;--a-ink:#ecebe6;--amber-bg:#4a3814;--amber-ink:#f5d58a;--shadow:none;"
             "--c-open:#388bfd;--c-closed:#2ea043;")

TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="generator" content="tasker %VERSION%">
%REFRESH%
<title>%TITLE%</title>
<link rel="icon" type="image/svg+xml" href="data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjAgMCA2NCA2NCI+PGVsbGlwc2UgY3g9IjMyIiBjeT0iMzIiIHJ4PSIzMCIgcnk9IjE5IiBmaWxsPSIjZjVjNTE4IiBzdHJva2U9IiMxMTEiIHN0cm9rZS13aWR0aD0iMi41Ii8+PHBhdGggZmlsbD0iIzExMSIgZD0iTTcgMjdRMTQgMjEgMjIgMjIuNVEyNiAyMy41IDI3IDI3TDI4LjYgMTkuNUwzMC42IDI1LjJMMzMuNCAyNS4yTDM1LjQgMTkuNUwzNyAyN1EzOCAyMy41IDQyIDIyLjVRNTAgMjEgNTcgMjdRNTEuNSAyOSA1MS41IDM1UTQ3LjUgMzEuNSA0MyAzNC41UTM5LjUgMzQgMzcuNSAzOS41UTM0IDM4LjUgMzIgNDVRMzAgMzguNSAyNi41IDM5LjVRMjQuNSAzNCAyMSAzNC41UTE2LjUgMzEuNSAxMi41IDM1UTEyLjUgMjkgNyAyN1oiLz48L3N2Zz4=">
<script>try{var t=localStorage.getItem("tasker-theme");if(t==="light"||t==="dark")document.documentElement.setAttribute("data-theme",t);}catch(e){}</script>
<style>
:root{
  color-scheme:light dark;
  --bg:#f6f5f2; --panel:#ffffff; --card:#ffffff; --col:#edebe6; --ink:#1d1d1b; --muted:#6b6a66; --line:#e1dfd9;
  --next:#1f6feb; --working:#0b7a6f; --waiting:#b7791f; --watch:#6f42c1; --someday:#8a8a85; --done:#2e8b57;
  --alert:#c0392b; --chip:#efeee9; --a-ink:#3a3936; --amber-bg:#fbe7c2; --amber-ink:#7a4a00;
  --c-open:#1f6feb; --c-closed:#2e8b57;
  --shadow:0 1px 2px rgba(0,0,0,.06);
}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){%DARK%}}
:root[data-theme="dark"]{%DARK%}
:root[data-theme="light"]{color-scheme:light}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif}
a{color:inherit;text-decoration-color:var(--muted)}
button,select,input{font:inherit;color:inherit}
header,.stalebar,.bar,main{max-width:1440px;margin-left:auto;margin-right:auto;padding-left:16px;padding-right:16px}
header{padding-top:20px;display:flex;gap:12px;align-items:flex-start;justify-content:space-between}
h1{font-size:20px;margin:0 0 2px}
.sub{color:var(--muted);font-size:12px}
.tbtn{flex:none;border:1px solid var(--line);background:var(--panel);border-radius:999px;padding:4px 12px;font-size:12px;cursor:pointer;white-space:nowrap}
.tbtn:hover,.chips button:hover,.tog:hover,.seg button:hover{border-color:var(--muted)}
.stalebar{margin-top:12px}
.stalebar div{background:var(--alert);color:#fff;border-radius:10px;padding:8px 12px;font-weight:600}
.bar{display:flex;flex-wrap:wrap;gap:8px 10px;align-items:center;margin-top:14px}
.seg{display:inline-flex;border:1px solid var(--line);border-radius:999px;overflow:hidden;background:var(--panel)}
.seg button{border:0;background:none;padding:4px 14px;font-size:13px;cursor:pointer}
.seg button.on{background:var(--ink);color:var(--bg)}
.chips{display:flex;flex-wrap:wrap;gap:6px}
.chips button,.tog{border:1px solid var(--line);background:var(--panel);border-radius:999px;padding:4px 12px;font-size:12px;cursor:pointer}
.chips button.ac{color:var(--a-ink)}
.chips button.on{border-color:var(--ink);box-shadow:0 0 0 1px var(--ink);font-weight:600}
.chips button[data-a=all].on,.tog.on{background:var(--ink);color:var(--bg);border-color:var(--ink)}
#q{margin-left:auto;width:240px;max-width:100%;border:1px solid var(--line);background:var(--panel);border-radius:999px;padding:5px 12px;font-size:13px}
#q:focus,select:focus-visible,button:focus-visible{outline:2px solid var(--next);outline-offset:1px}
@media (max-width:620px){#q{margin-left:0;width:100%;order:-1}}
%AREACSS%
main{margin-top:14px;margin-bottom:40px}
section{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:12px 14px;margin-bottom:16px}
section.warn{border-color:var(--alert)}
section h2{font-size:14px;margin:0 0 8px;display:flex;gap:8px;align-items:baseline;flex-wrap:wrap}
section h2 .meta{font-weight:400}
ul{list-style:none;margin:0;padding:0}
li.w{color:var(--alert);font-size:12px;padding:2px 0}
.st{font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:.04em;padding-top:1px}
.st.next{color:var(--next)} .st.working{color:var(--working)} .st.waiting{color:var(--waiting)} .st.watch{color:var(--watch)} .st.someday{color:var(--someday)} .st.done{color:var(--done)}
.t{font-weight:500}
.meta{font-size:12px;color:var(--muted);overflow-wrap:anywhere}
.due{color:var(--alert);font-weight:600}
.soon{color:var(--waiting);font-weight:600}
.src{font-family:ui-monospace,Consolas,monospace;font-size:11px;color:var(--muted);overflow-wrap:anywhere}
.pstatus{margin-left:auto;font-size:11px;color:var(--muted)}
.hidden{display:none !important}
/* This week */
.week{display:grid;grid-template-columns:minmax(0,1fr) 300px;gap:0;padding:0}
.week>div{padding:12px 14px}
.week .flow{border-left:1px solid var(--line)}
.week.solo{grid-template-columns:300px}
.week.solo .flow{border-left:0}
.week li{display:grid;grid-template-columns:70px 1fr;gap:8px;padding:6px 0;border-top:1px solid var(--line)}
.week li:first-child{border-top:0}
.pick .st{font-size:12px}
.pick.got .t{text-decoration:line-through;text-decoration-color:var(--muted);color:var(--muted)}
.pick .pn{font-size:11px;font-weight:600;color:var(--muted)}
.flow svg{display:block;width:100%;height:auto;margin:2px 0 4px;overflow:visible}
.flow .fo{fill:var(--c-open)} .flow .fc{fill:var(--c-closed)}
.flow .gl{stroke:var(--line);stroke-width:1}
.flow text{font:9.5px ui-monospace,Consolas,monospace;fill:var(--muted)}
.flow .tk{text-anchor:end}
.flow .tk.na{text-anchor:middle;font-size:11px}
.flow .wl{text-anchor:middle}
.flow .cur .wl{fill:var(--ink);font-weight:700}
.flow .nodata .wl{opacity:.45}
.flow .hit{fill:transparent}
.flow .wkg:hover .hit{fill:var(--chip)}
.lg{margin-left:auto;display:inline-flex;gap:10px;font-size:11px;font-weight:400;color:var(--muted)}
.lg span{display:inline-flex;gap:4px;align-items:center}
.sw{display:inline-block;width:9px;height:9px;border-radius:2px}
.sw.fo{background:var(--c-open)} .sw.fc{background:var(--c-closed)}
.flow .meta b{color:var(--ink);font-weight:600}
.flow details{margin-top:4px}
.flow summary{cursor:pointer}
.flow table{border-collapse:collapse;margin-top:4px;font-variant-numeric:tabular-nums}
.flow th,.flow td{text-align:left;padding:1px 10px 1px 0;font-weight:400}
.flow th{color:var(--muted)}
.flow td:nth-child(n+3),.flow th:nth-child(n+3){text-align:right}
@media (max-width:700px){.week{grid-template-columns:1fr}.week .flow{border-left:0;border-top:1px solid var(--line)}}
/* Board toolbar */
.tools{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin-bottom:10px}
.tools select{border:1px solid var(--line);background:var(--panel);border-radius:999px;padding:4px 10px;font-size:12px;max-width:240px}
#nowbtn b{color:var(--alert);font-variant-numeric:tabular-nums}
#nowbtn.on b{color:inherit}
.cw{margin-left:auto;font-size:12px;color:var(--muted)}
.cw b{font-size:15px;color:var(--ink);font-variant-numeric:tabular-nums}
#nomatch{text-align:center;padding:16px 0}
/* Board */
.board{display:grid;grid-template-columns:repeat(4,minmax(0,1fr)) 44px;gap:10px;align-items:start}
.board.open{grid-template-columns:repeat(5,minmax(0,1fr))}
.col{background:var(--col);border:1px solid var(--line);border-radius:12px;padding:0 8px 8px;min-width:0}
.colh{position:sticky;top:0;z-index:2;display:flex;flex-wrap:wrap;gap:2px 8px;align-items:baseline;width:100%;margin:0;padding:10px 4px 6px;border:0;border-radius:12px 12px 0 0;background:var(--col);font-size:13px;font-weight:600;text-align:left}
.colh i{flex:none;width:8px;height:8px;border-radius:50%;background:var(--someday);align-self:center}
.h-next i{background:var(--next)} .h-working i{background:var(--working)} .h-blocked i{background:var(--waiting)} .h-done i{background:var(--done)}
.colh .n{font-weight:500;color:var(--muted);font-variant-numeric:tabular-nums}
.colh .sub{flex-basis:100%;font-size:11px;font-weight:400;color:var(--muted)}
.colh.over{background:var(--amber-bg);color:var(--amber-ink);border-radius:8px}
.colh.over .n{color:inherit;font-weight:700}
.col.over{border-color:var(--waiting)}
button.colh{cursor:pointer}
button.colh:hover{color:var(--done)}
.col.shut{display:flex;flex-direction:column;align-self:stretch;padding:0}
.colh.shut{position:static;flex:1;flex-wrap:nowrap;writing-mode:vertical-rl;align-items:center;gap:8px;padding:12px 0;min-height:140px;border-radius:12px}
.empty{color:var(--muted);font-size:12px;text-align:center;padding:10px 0 4px}
.card{display:block;background:var(--card);border:1px solid var(--line);border-left:3px solid var(--line);border-radius:8px;padding:6px 9px 7px;margin-top:6px;cursor:pointer;box-shadow:var(--shadow)}
.card:hover{border-color:var(--muted)}
.card:focus-visible{outline:2px solid var(--next);outline-offset:1px}
.card.now{border-left-color:var(--alert)}
.card.paused{opacity:.65}
.card.done .ct{color:var(--muted)}
.c1{display:flex;gap:6px;align-items:center;font-size:11px;min-width:0}
.cid{flex:none;font-family:ui-monospace,Consolas,monospace;font-size:11px;font-weight:600;color:var(--muted);white-space:nowrap}
.cproj{min-width:0;padding:0 7px;border-radius:999px;background:var(--chip);color:var(--a-ink);font-size:11px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.tag{flex:none;font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.04em;line-height:1.4;padding:0 4px;border:1px solid currentColor;border-radius:4px}
.tag.verify{color:var(--watch)} .tag.unb,.tag.bad{color:var(--alert)}
.ct{margin-top:3px;font-weight:500;display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden;overflow-wrap:anywhere}
.c3{margin-top:3px;font-size:11.5px;color:var(--muted);overflow-wrap:anywhere}
.c3>span+span::before{content:" · ";color:var(--muted);font-weight:400}
/* Focus project cards: the week's project picks, at the top of Working */
.fcard{border-left-color:var(--working);overflow:hidden;padding-top:0}
.fcard.on{box-shadow:0 0 0 2px var(--working)}
.fband{display:flex;gap:6px;align-items:center;margin:0 -9px 5px;padding:3px 9px;background:var(--chip);color:var(--a-ink);font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.05em}
.fband .ftag{color:var(--working)}
.fband .fa{margin-left:auto;font-weight:500}
.fname{min-width:0;font-size:12px;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.fcard .ct{-webkit-line-clamp:4}
.fcard a{position:relative;z-index:1}
.fs.next{color:var(--next)} .fs.working{color:var(--working)} .fs.waiting{color:var(--waiting)} .fs.watch{color:var(--watch)}
.fcard+.card:not(.fcard),.fcard+.empty,.fnext+.card,.fnext+.empty{margin-top:12px}
.fnext{margin:6px 4px 0;font-size:11.5px;color:var(--muted);overflow-wrap:anywhere}
/* Group by project: one lane per project across the columns */
.board.lanes{gap:0 10px}
.board.lanes>.colh{border-bottom:2px solid var(--line);border-radius:0;background:var(--bg);padding:8px 2px 6px}
.board.lanes>.colh.over{background:var(--amber-bg);border-radius:8px 8px 0 0}
.board.lanes>.colh.shut{min-height:90px;border-bottom:0}
.lane{grid-column:1/-1;padding:14px 2px 2px;border-bottom:1px solid var(--line);font-size:13px;font-weight:600}
.lane .lt{position:sticky;left:2px;display:inline-flex;gap:6px;align-items:baseline}
.lane .n{font-weight:400;color:var(--muted);font-size:12px}
.cell{min-width:0;padding:0 0 6px}
.lanes .card .cproj{display:none}
@media (max-width:900px){
  .board{grid-template-columns:repeat(4,minmax(240px,80vw)) 44px;overflow-x:auto;scroll-snap-type:x proximity;padding-bottom:8px}
  .board.open{grid-template-columns:repeat(5,minmax(240px,80vw))}
  .col,.board>.colh{scroll-snap-align:start}
  .colh,.board.lanes>.colh{position:static}
}
/* Card modal */
dialog{width:min(700px,calc(100vw - 24px));max-height:calc(100vh - 40px);padding:0;border:1px solid var(--line);border-radius:14px;background:var(--panel);color:var(--ink);box-shadow:0 12px 40px rgba(0,0,0,.3)}
dialog::backdrop{background:rgba(0,0,0,.5)}
.mb{padding:14px 18px 18px}
.mh{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.mh .x{margin-left:auto;border:0;background:none;color:var(--muted);font-size:22px;line-height:1;padding:0 6px 2px;border-radius:6px;cursor:pointer}
.mh .x:hover{background:var(--chip);color:var(--ink)}
.pill{padding:1px 8px;border:1px solid currentColor;border-radius:999px}
.mb h2{font-size:18px;line-height:1.3;margin:10px 0 6px}
.mnote{margin:0 0 8px;overflow-wrap:anywhere}
.mextra{list-style:disc;margin:0 0 10px;padding-left:1.2em;font-size:13px}
.mextra li{margin:2px 0}
.mb dl{display:grid;grid-template-columns:100px minmax(0,1fr);gap:5px 12px;margin:10px 0 0;font-size:13px}
.mb dt{color:var(--muted)}
.mb dd{margin:0;overflow-wrap:anywhere}
.mb h3{font-size:12px;font-weight:600;color:var(--muted);text-transform:uppercase;letter-spacing:.04em;margin:18px 0 4px}
.hist li{display:grid;grid-template-columns:88px minmax(0,1fr);gap:8px;padding:5px 0;border-top:1px solid var(--line);font-size:13px}
.hist li:first-child{border-top:0}
.hist .hd{font-family:ui-monospace,Consolas,monospace;font-size:12px;color:var(--muted);padding-top:1px}
a.gone{text-decoration-style:dotted}
/* Routines */
.routines .rrow{display:grid;grid-template-columns:minmax(0,2.4fr) minmax(0,1.2fr) minmax(0,1.2fr) minmax(0,.8fr) 86px;gap:12px;padding:9px 0;border-top:1px solid var(--line);align-items:start;font-size:13px}
.routines .rhead{border-top:0;padding:2px 0 6px;font-size:11px;text-transform:uppercase;letter-spacing:.05em;color:var(--muted)}
.rl{display:none}
.rs{display:inline-block;padding:1px 8px;border:1px solid currentColor;border-radius:999px;font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:.04em}
.rs.active{color:var(--done)} .rs.paused{color:var(--waiting)} .rs.planned{color:var(--someday);border-style:dashed}
.rrow[data-status=paused] .t,.rrow[data-status=planned] .t{color:var(--muted)}
@media (max-width:760px){
  .routines .rrow{grid-template-columns:1fr 1fr;gap:6px 12px}
  .routines .rmain{grid-column:1/-1}
  .routines .rhead{display:none}
  .rl{display:block;font-size:11px;color:var(--muted)}
}
</style>
</head>
<body data-built="%BUILT%" data-stale="%STALE%">
<header>
  <div>
    <h1>%TITLE%</h1>
    <div class="sub">Generated %GENERATED%<span id="age"></span>%SOURCE% · read-only%SCHEDULE% · edit items in <code>projects/*.md</code></div>
  </div>
  <button type="button" class="tbtn" id="theme" title="Theme: follows the system — click to change">◐ Auto</button>
</header>
<div class="stalebar hidden" id="stale"><div></div></div>
<div class="bar" id="bar">%VIEWS%<div class="chips" id="areas">%FILTERS%</div><input type="search" id="q" placeholder="Search  ( / )" aria-label="Search items and routines" autocomplete="off"></div>
<main>
%WARN%
<div id="v-projects">
%WEEK%
<div class="tools" id="tools">
  <button type="button" class="tog" id="nowbtn" aria-pressed="false" title="Only items whose check date has come, and waiting items whose blockers are done"><b id="nowcount">0</b> needs you now</button>
  <button type="button" class="tog" id="lanes" aria-pressed="false" title="One row per project across the columns">Group by project</button>
  <select id="proj" aria-label="Project"><option value="">All projects</option></select>
  <span class="cw" title="This week, Monday to Sunday, from git: opened = item lines first added, closed = item lines deleted (ID gone everywhere)">opened this week: <b>%OPENEDNOW%</b> · closed this week: <b>%CLOSEDNOW%</b></span>
</div>
<div id="board" class="board cols" data-tasker-board="%VERSION%"><noscript><p class="meta">The board needs JavaScript; <code>TASKER.md</code> has the same items as text.</p></noscript></div>
<p id="nomatch" class="meta hidden">No item matches these filters.</p>
</div>
%ROUTINES%
</main>
<dialog id="card" aria-label="Item"><div class="mb" id="cardbody"></div></dialog>
<script type="application/json" id="data">%DATA%</script>
<script>
(function(){
  var D=JSON.parse(document.getElementById("data").textContent);
  var COLS=[{k:"someday",label:"Someday",st:["someday"]},{k:"next",label:"Next",st:["next"]},
            {k:"working",label:"Working",st:["working"]},{k:"blocked",label:"Blocked",st:["waiting","watch"]},
            {k:"done",label:"Done",st:[]}];
  var KNOWN=["working","next","waiting","watch","someday"], byId={}, doneById={};
  function prep(it,done){  // column, search text
    it.isDone=done; it.col=done?"done":"next";  // an unknown state (a typo) lands in Next, flagged
    if(!done) COLS.forEach(function(c){if(c.st.indexOf(it.st)>=0) it.col=c.k;});
    it.text=[it.id,it.title,it.note,it.on,it.pname,it.proj,it.doc].concat(it.extra).join(" ").toLowerCase();
  }
  D.items.forEach(function(it){prep(it,false);byId[it.id]=it;});
  D.done.forEach(function(it){prep(it,true);doneById[it.id]=it;});
  var FOC=D.focus||[], focById={};
  FOC.forEach(function(f){focById[f.id]=f;});
  function $(id){return document.getElementById(id);}
  var board=$("board"), box=$("q"), sel=$("proj"), dlg=$("card"), body=$("cardbody");
  var chips=[].slice.call(document.querySelectorAll("#areas button"));
  function areaOk(a){return chips.some(function(b){return b.dataset.a===a;});}
  var S={view:"projects",area:D.defaultArea,proj:"",lanes:false,now:false}, q="", doneOpen=false;
  try{
    var s=JSON.parse(localStorage.getItem("tasker")||"{}");
    if(areaOk(s.area)) S.area=s.area;
    if(s.view==="routines"&&$("v-routines")) S.view="routines";
    if(D.projects[s.proj]) S.proj=s.proj;
    S.lanes=s.lanes===true; S.now=s.now===true;
  }catch(e){}
  if(!areaOk(S.area)) S.area="all";
  function save(){try{localStorage.setItem("tasker",JSON.stringify(S));}catch(e){}}

  function el(tag,cls,txt){var e=document.createElement(tag);if(cls)e.className=cls;if(txt!=null)e.textContent=txt;return e;}
  function daysTxt(n){return n===0?"today":(n>0?"in "+n+" d":(-n)+" d overdue");}
  function link(text,href){
    if(!href) return document.createTextNode(text);
    var a=el("a",null,text); a.href=href; a.target="_blank"; a.rel="noopener"; return a;
  }
  function idLinks(parent,text){  // item IDs in a text become links to their card; gone ones are struck
    var re=/\b[A-Z][A-Z0-9]*-\d+\b/g, pos=0, m;  // an ID whose prefix is no registered project (a Jira key) stays text
    while((m=re.exec(text))){
      parent.appendChild(document.createTextNode(text.slice(pos,m.index)));
      var id=m[0], a;
      if(byId[id]||doneById[id]){a=el("a",doneById[id]?"gone":null,id);a.href="#"+id;}
      else if(D.known.indexOf(id.replace(/-\d+$/,""))>=0){a=el("s",null,id);a.title="done";}
      else a=document.createTextNode(id);
      parent.appendChild(a); pos=m.index+id.length;
    }
    parent.appendChild(document.createTextNode(text.slice(pos)));
    return parent;
  }
  function base(it){return (S.area==="all"||it.area===S.area)&&(!S.proj||it.proj===S.proj)&&(!q||it.text.indexOf(q)>=0);}
  function match(it){return base(it)&&(!S.now||it.now);}

  function card(it){
    var c=el("div","card"+(it.now?" now":"")+(it.paused?" paused":"")+(it.isDone?" done":""));
    c.tabIndex=0; c.setAttribute("role","button"); c.dataset.id=it.id;
    var top=c.appendChild(el("div","c1"));
    top.appendChild(el("span","cid",it.id));
    var pj=top.appendChild(el("span","cproj ac",it.pname)); pj.dataset.a=it.area; pj.title=it.area+" · "+it.pname;
    if(it.st==="watch"&&!it.isDone) top.appendChild(el("span","tag verify","verify")).title="#watch: done, verify when the check comes";
    if(it.unblocked) top.appendChild(el("span","tag unb","unblocked")).title="everything it waited on is done";
    if(!it.isDone&&KNOWN.indexOf(it.st)<0) top.appendChild(el("span","tag bad","#"+it.st));
    c.appendChild(el("div","ct",it.title));
    var m=el("div","c3");
    if(it.isDone) m.appendChild(el("span",null,"closed "+it.date));
    else if(it.check) m.appendChild(el("span",it.days<=0?"due":(it.days<=3?"soon":null),"check "+it.check.slice(5)+" · "+daysTxt(it.days)));
    if(it.nocheck) m.appendChild(el("span","soon","no check date"));
    if(it.on&&!it.isDone) m.appendChild(el("span",null,"on: "+it.on));
    if(m.childNodes.length) c.appendChild(m);
    return c;
  }
  // Focus project card: one per project pick in FOCUS.md, at the top of Working; follows the area chips.
  function focusShown(){return FOC.filter(function(f){return S.area==="all"||f.area===S.area;});}
  function countsLine(f,cls){
    var m=el("div",cls), k=0;
    KNOWN.slice(0,4).concat(["someday"]).forEach(function(s){
      if(f.counts[s]){m.appendChild(el("span","fs "+s,f.counts[s]+" "+s)); k++;}
    });
    if(!k) m.appendChild(el("span",null,"no open items"));
    if(f.due) m.appendChild(el("span","due",f.due+" due")).title="check date reached (today or overdue)";
    return m;
  }
  function docLinks(f,cls){
    var m=el("div",cls);
    f.docs.forEach(function(d){var s=m.appendChild(el("span")); s.title=d[2]; s.appendChild(link(d[0],d[1]));});
    return m;
  }
  function closedLine(f,cls){
    var m=el("div",cls,"closed this week: ");
    if(f.closed.length) idLinks(m,f.closed.join(", ")); else m.appendChild(document.createTextNode("none yet"));
    return m;
  }
  function fcard(f){
    var c=el("div","card fcard"+(S.proj===f.id?" on":"")+(f.status!=="active"?" paused":""));
    c.tabIndex=0; c.setAttribute("role","button"); c.dataset.proj=f.id;
    c.title="Focus project this week: show only its items, with the project's details";
    var b=c.appendChild(el("div","fband ac")); b.dataset.a=f.area;
    b.appendChild(el("span","ftag","Focus project"));
    b.appendChild(el("span","fa",f.area));
    var top=c.appendChild(el("div","c1"));
    top.appendChild(el("span","cid",f.id));
    top.appendChild(el("span","fname",f.name)).title=f.name;
    idLinks(c.appendChild(el("div","ct")),f.outcome);
    if(f.when) idLinks(c.appendChild(el("div","c3")),"done when: "+f.when);
    if(f.items.length) idLinks(c.appendChild(el("div","c3 fitems")),"items: "+f.items.join(", "));
    c.appendChild(countsLine(f,"c3"));
    if(f.docs.length) c.appendChild(docLinks(f,"c3"));
    c.appendChild(closedLine(f,"c3"));
    return c;
  }
  function nextUp(parent){  // "Next up: …" note lines of FOCUS.md, muted, under the focus cards
    (D.nextUp||[]).forEach(function(n){idLinks(parent.appendChild(el("div","fnext")),n);});
  }
  function count(list,total){return list.length===total?String(total):list.length+" of "+total;}
  function head(c,list,total,fs){
    var h=el(c.k==="done"?"button":"div","colh h-"+c.k);
    h.appendChild(el("i"));
    if(c.k==="done"){
      h.type="button"; h.setAttribute("aria-expanded",String(doneOpen));
      h.title=doneOpen?"Hide the Done drawer":"Show what closed in the last 7 days";
      if(!doneOpen) h.classList.add("shut");
      h.appendChild(el("span",null,doneOpen?"Done · last 7 days":"Done"));
      h.appendChild(el("span","n",count(list,total)));
      h.onclick=function(){doneOpen=!doneOpen;render();};
      return h;
    }
    h.appendChild(el("span",null,c.label));
    if(c.k==="working"){
      h.appendChild(el("span","n",total+" / "+D.wip));
      h.title="Work in progress: "+total+", limit "+D.wip+" (wip_limit in tasker.yaml)";
      if(total>D.wip) h.classList.add("over");
      if(list.length<total) h.appendChild(el("span","n","· "+list.length+" shown"));
      if(FOC.length){
        var fp=h.appendChild(el("span","sub",count(fs,FOC.length)+" focus project"+(FOC.length===1?"":"s")+" · not counted in WIP"));
        fp.title="Project picks from FOCUS.md; the count above is items only";
      }
    } else h.appendChild(el("span","n",count(list,total)));
    if(c.k==="blocked"){
      var w=list.filter(function(it){return it.st==="waiting";}).length;
      h.appendChild(el("span","sub",w+" waiting · "+(list.length-w)+" to verify"));
    }
    return h;
  }
  function render(){
    var shown={}, total={}, n=0, fs=focusShown();
    COLS.forEach(function(c){shown[c.k]=[];total[c.k]=0;});
    D.items.concat(D.done).forEach(function(it){total[it.col]++; if(match(it)) shown[it.col].push(it);});
    function live(c){return c.k!=="done"||doneOpen;}
    COLS.forEach(function(c){if(live(c)) n+=shown[c.k].length;});
    board.textContent="";
    board.className="board "+(S.lanes?"lanes":"cols")+(doneOpen?" open":"");
    var heads=COLS.map(function(c){return head(c,shown[c.k],total[c.k],fs);});
    if(!S.lanes){
      COLS.forEach(function(c,i){
        var col=board.appendChild(el("div","col c-"+c.k+(live(c)?"":" shut")+(heads[i].classList.contains("over")?" over":"")));
        col.appendChild(heads[i]);
        if(!live(c)) return;
        if(c.k==="working"&&fs.length){fs.forEach(function(f){col.appendChild(fcard(f));}); nextUp(col);}
        shown[c.k].forEach(function(it){col.appendChild(card(it));});
        if(!shown[c.k].length) col.appendChild(el("div","empty",c.k==="done"?"Nothing closed in the last 7 days.":"—"));
      });
    }else{
      heads.forEach(function(h){board.appendChild(h);});
      if(fs.length){
        var fl=board.appendChild(el("div","lane")).appendChild(el("span","lt"));
        fl.appendChild(el("span",null,"Focus projects"));
        fl.appendChild(el("span","n","this week · from FOCUS.md"));
        COLS.forEach(function(c){
          var cell=board.appendChild(el("div","cell c-"+c.k));
          if(c.k==="working"){fs.forEach(function(f){cell.appendChild(fcard(f));}); nextUp(cell);}
        });
      }
      D.lanes.forEach(function(pid){
        var per=COLS.map(function(c){return live(c)?shown[c.k].filter(function(it){return it.proj===pid;}):[];});
        var k=per.reduce(function(a,l){return a+l.length;},0);
        if(!k) return;
        var p=D.projects[pid]||{name:pid,area:"?",status:""};
        var lt=board.appendChild(el("div","lane")).appendChild(el("span","lt"));
        var ap=lt.appendChild(el("span","cproj ac",p.area)); ap.dataset.a=p.area;
        lt.appendChild(el("span",null,p.name));
        lt.appendChild(el("span","n",pid+" · "+k+(p.status&&p.status!=="active"?" · "+p.status:"")));
        COLS.forEach(function(c,i){
          var cell=board.appendChild(el("div","cell c-"+c.k));
          per[i].forEach(function(it){cell.appendChild(card(it));});
        });
      });
    }
    $("nomatch").classList.toggle("hidden",n>0);
    $("nowcount").textContent=D.items.filter(function(it){return it.now&&base(it);}).length;
  }
  function fillProj(){  // projects of the chosen area, grouped by area
    sel.textContent="";
    sel.appendChild(el("option",null,"All projects")).value="";
    var groups={};
    D.lanes.forEach(function(pid){
      var p=D.projects[pid];
      if(!p||(S.area!=="all"&&p.area!==S.area)) return;
      var g=groups[p.area]; if(!g){g=groups[p.area]=sel.appendChild(el("optgroup")); g.label=p.area;}
      g.appendChild(el("option",null,p.name+" · "+pid)).value=pid;
    });
    var p=D.projects[S.proj];
    if(!p||(S.area!=="all"&&p.area!==S.area)) S.proj="";
    sel.value=S.proj;
  }
  function filterRoutines(){
    var rows=document.querySelectorAll("#v-routines .rrow[data-status]"), k=0;
    rows.forEach(function(r){var ok=!q||r.textContent.toLowerCase().indexOf(q)>=0; r.classList.toggle("hidden",!ok); if(ok) k++;});
    var nm=$("rnomatch"); if(nm) nm.classList.toggle("hidden",k>0||!rows.length);
  }
  function apply(){
    chips.forEach(function(b){b.classList.toggle("on",b.dataset.a===S.area);});
    document.querySelectorAll("#views button").forEach(function(b){b.classList.toggle("on",b.dataset.v===S.view);});
    var vr=$("v-routines");
    $("v-projects").classList.toggle("hidden",S.view!=="projects");
    if(vr) vr.classList.toggle("hidden",S.view!=="routines");
    $("areas").classList.toggle("hidden",S.view!=="projects");
    $("nowbtn").classList.toggle("on",S.now); $("nowbtn").setAttribute("aria-pressed",String(S.now));
    $("lanes").classList.toggle("on",S.lanes); $("lanes").setAttribute("aria-pressed",String(S.lanes));
    fillProj(); render(); filterRoutines(); save();
  }

  // Card modal: every field, sub-lines, doc link, and the commits that touched the line.
  function row(dl,label,node){
    dl.appendChild(el("dt",null,label));
    var dd=dl.appendChild(el("dd"));
    if(typeof node==="string") dd.textContent=node; else dd.appendChild(node);
  }
  function open(id){
    var it=byId[id]||doneById[id]; if(!it) return;
    var p=D.projects[it.proj]||{name:it.pname,area:it.area,status:"",docs:[]};
    body.textContent="";
    var mh=body.appendChild(el("div","mh"));
    mh.appendChild(el("span","cid",it.id));
    mh.appendChild(el("span","pill st "+(it.isDone?"done":it.st),it.isDone?"done":it.st));
    if(it.st==="watch"&&!it.isDone) mh.appendChild(el("span","tag verify","verify"));
    if(it.unblocked) mh.appendChild(el("span","tag unb","unblocked"));
    var ap=mh.appendChild(el("span","cproj ac",it.area)); ap.dataset.a=it.area;
    var x=mh.appendChild(el("button","x","×")); x.type="button"; x.title="Close (Esc)"; x.setAttribute("aria-label","Close");
    x.onclick=function(){dlg.close();};
    body.appendChild(el("h2",null,it.title));
    if(it.note) idLinks(body.appendChild(el("p","mnote")),it.note);
    if(it.extra.length){var ul=body.appendChild(el("ul","mextra")); it.extra.forEach(function(e){idLinks(ul.appendChild(el("li")),e);});}
    var dl=body.appendChild(el("dl"));
    row(dl,"State",it.isDone?"closed "+it.date+" — line deleted (it was #"+it.st+")"
      :"#"+it.st+(it.st==="watch"?" — done, verify when the check comes":""));
    row(dl,"Project",p.name+" · "+it.proj+" · "+(p.area||it.area)+(p.status?" · "+p.status:""));
    if(it.on) row(dl,"On",idLinks(el("span"),it.on));
    if(it.since) row(dl,"Since",it.since+" · "+it.age+" d ago");
    if(it.check) row(dl,"Check",it.isDone?it.check:el("span",it.days<=0?"due":(it.days<=3?"soon":null),it.check+" · "+daysTxt(it.days)));
    if(it.unblocked) row(dl,"Unblocked",el("span","due","everything it waited on is done"));
    if(it.nocheck) row(dl,"Check",el("span","soon","no check date"));
    if(it.doc) row(dl,"Doc",link(it.doc,it.docUrl));
    if(it.touched) row(dl,"Updated",it.touched);
    if(p.docs&&p.docs.length){
      var ds=el("span");
      p.docs.forEach(function(d,i){if(i) ds.appendChild(document.createTextNode(" · ")); ds.appendChild(link(d[0],d[1]));});
      row(dl,"Project docs",ds);
    }
    body.appendChild(el("h3",null,"History · last "+D.historyDays+" days"));
    var hs=D.history[id]||[];
    if(!hs.length) body.appendChild(el("p","meta",D.git?"No commit changed this line in the last "+D.historyDays+" days.":"No history: the data folder is not a git checkout."));
    else{
      var ol=body.appendChild(el("ul","hist"));
      hs.forEach(function(e){  // [date, commit, subject, change]
        var li=ol.appendChild(el("li"));
        li.appendChild(el("span","hd",e[0]));
        var d=li.appendChild(el("div"));
        d.appendChild(el("div",null,e[2]));
        var m=d.appendChild(el("div","meta",e[3]+" · "));
        m.appendChild(link(e[1],D.commitUrl&&e[1]?D.commitUrl+e[1]:""));
      });
    }
    if(!dlg.open) dlg.showModal();
    dlg.scrollTop=0;
    if(decodeURIComponent(location.hash.slice(1))!==id) history.replaceState(null,"","#"+id);
  }
  // Project modal (from a focus card): the card's details plus the latest history of its items.
  function openProj(pid){
    var f=focById[pid]; if(!f) return;
    body.textContent="";
    var mh=body.appendChild(el("div","mh"));
    mh.appendChild(el("span","cid",f.id));
    mh.appendChild(el("span","pill st working","focus project"));
    var ap=mh.appendChild(el("span","cproj ac",f.area)); ap.dataset.a=f.area;
    var x=mh.appendChild(el("button","x","×")); x.type="button"; x.title="Close (Esc)"; x.setAttribute("aria-label","Close");
    x.onclick=function(){dlg.close();};
    body.appendChild(el("h2",null,f.name));
    idLinks(body.appendChild(el("p","mnote")),f.outcome);
    var dl=body.appendChild(el("dl"));
    if(f.when) row(dl,"Done when",idLinks(el("span"),f.when));
    if(f.items.length) row(dl,"Items",idLinks(el("span"),f.items.join(", ")));
    row(dl,"Open items",countsLine(f,"c3"));
    row(dl,"Closed",closedLine(f,null));
    if(f.status!=="active") row(dl,"Status",f.status);
    if(f.docs.length) row(dl,"Project docs",docLinks(f,"c3"));
    row(dl,"Board",S.proj===f.id?"filtered to "+f.id+" (project menu: All projects to clear)":"not filtered");
    body.appendChild(el("h3",null,"History · latest changes to "+f.id+" items"));
    if(!f.hist.length) body.appendChild(el("p","meta",D.git?"No commit changed a "+f.id+" item in the last "+D.historyDays+" days.":"No history: the data folder is not a git checkout."));
    else{
      var ol=body.appendChild(el("ul","hist"));
      f.hist.forEach(function(e){  // [date, commit, subject, change, item id]
        var li=ol.appendChild(el("li"));
        li.appendChild(el("span","hd",e[0]));
        var d=li.appendChild(el("div"));
        d.appendChild(el("div",null,e[2]));
        var m=d.appendChild(el("div","meta"));
        idLinks(m,e[4]); m.appendChild(document.createTextNode(" · "+e[3]+" · "));
        m.appendChild(link(e[1],D.commitUrl&&e[1]?D.commitUrl+e[1]:""));
      });
    }
    if(!dlg.open) dlg.showModal();
    dlg.scrollTop=0;
    if(decodeURIComponent(location.hash.slice(1))!==pid) history.replaceState(null,"","#"+pid);
  }
  function pickProj(pid){S.proj=pid; apply(); openProj(pid);}
  dlg.addEventListener("close",function(){if(location.hash) history.replaceState(null,"",location.pathname+location.search);});
  dlg.addEventListener("click",function(e){  // a click on the backdrop closes it
    if(e.target!==dlg) return;
    var r=dlg.getBoundingClientRect();
    if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom) dlg.close();
  });
  function fromHash(){var id=decodeURIComponent(location.hash.slice(1)); if(byId[id]||doneById[id]) open(id); else if(focById[id]) openProj(id);}
  window.addEventListener("hashchange",fromHash);
  function act(c){if(c.dataset.proj) pickProj(c.dataset.proj); else open(c.dataset.id);}
  board.addEventListener("click",function(e){
    if(e.target.closest("a")) return;  // a link inside a focus card goes where it points
    var c=e.target.closest(".card"); if(c) act(c);
  });
  board.addEventListener("keydown",function(e){
    var c=e.target.closest&&e.target.closest(".card");
    if(c&&e.target===c&&(e.key==="Enter"||e.key===" ")){e.preventDefault();act(c);}
  });

  chips.forEach(function(b){b.onclick=function(){S.area=b.dataset.a;apply();};});
  document.querySelectorAll("#views button").forEach(function(b){b.onclick=function(){S.view=b.dataset.v;apply();};});
  sel.onchange=function(){S.proj=sel.value;apply();};
  $("nowbtn").onclick=function(){S.now=!S.now;apply();};
  $("lanes").onclick=function(){S.lanes=!S.lanes;apply();};
  box.oninput=function(){q=box.value.trim().toLowerCase();render();filterRoutines();};
  box.onkeydown=function(e){if(e.key==="Escape"){box.value="";q="";render();filterRoutines();box.blur();}};
  document.addEventListener("keydown",function(e){
    var t=document.activeElement&&document.activeElement.tagName;
    if(e.key==="/"&&!dlg.open&&!/^(INPUT|SELECT|TEXTAREA)$/.test(t)&&!e.ctrlKey&&!e.metaKey){e.preventDefault();box.focus();}
  });
  apply();
  fromHash();

  // Theme: follows the system until the button picks light or dark (remembered per browser).
  var tb=$("theme"), MODES=["auto","light","dark"], LABEL={auto:"◐ Auto",light:"☀ Light",dark:"☾ Dark"}, tm="auto";
  function theme(m){
    tm=m;
    if(m==="auto") document.documentElement.removeAttribute("data-theme"); else document.documentElement.setAttribute("data-theme",m);
    tb.textContent=LABEL[m];
    tb.title="Theme: "+(m==="auto"?"follows the system":m)+" — click to change";
  }
  try{tm=localStorage.getItem("tasker-theme")||"auto";}catch(e){}
  theme(MODES.indexOf(tm)<0?"auto":tm);
  tb.onclick=function(){
    theme(MODES[(MODES.indexOf(tm)+1)%MODES.length]);
    try{if(tm==="auto") localStorage.removeItem("tasker-theme"); else localStorage.setItem("tasker-theme",tm);}catch(e){}
  };

  // Age of the page, and a red bar once it is older than stale_minutes (the build stopped).
  var built=+document.body.dataset.built, lim=+document.body.dataset.stale;
  function age(){
    var m=Math.max(0,Math.round((Date.now()/1000-built)/60));
    var txt=m<1?"just now":m<120?m+" min ago":m<2880?Math.round(m/60)+" h ago":Math.round(m/1440)+" days ago";
    $("age").textContent=" ("+txt+")";
    var bar=$("stale"), bad=lim>0&&m>lim;
    bar.classList.toggle("hidden",!bad);
    if(bad) bar.firstChild.textContent="Stale: this page was last built "+txt+" — the build has stopped. The items below may be out of date.";
  }
  if(built){age();setInterval(age,60000);}
})();
</script>
</body>
</html>
"""

if __name__ == "__main__":
    main()
