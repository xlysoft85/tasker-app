#!/usr/bin/env python3
"""Tasker: read a data repo (tasker.yaml, projects.yaml, projects/*.md), write a static
index.html and the generated TASKER.md (committed back if changed).

Run it from cron, CI or by hand. Keep this script out of the data repo: anyone who can push
items there should not be able to change the code that runs on the build host.
"""
import argparse
import datetime as dt
import html
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

STATES = ["next", "waiting", "watch", "someday"]
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
    return {"title": str(raw.get("title") or "Tasker"), "areas": areas, "repos": repos,
            "source": source, "schedule": str(raw.get("schedule") or ""),
            "refresh": int(raw.get("refresh_minutes", 15) or 0),
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
    return subprocess.run(["git", "-C", str(repo), *args], check=check,
                          capture_output=True, text=True).stdout


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
                "status": p.get("status", "active"), "docs": p.get("docs") or [], "items": []}
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


def recently_done(repo, current_ids, days=7):
    out = git(repo, "log", f"--since={days}.days", "-p", "--format=@@C %as", "--",
              "projects/", check=False)
    done, date = {}, None
    for line in out.splitlines():
        if line.startswith("@@C "):
            date = line[4:]
        elif line.startswith("-- "):
            m = ITEM_RE.match(line[1:])
            if m and m.group(1) not in current_ids and m.group(1) not in done:
                done[m.group(1)] = (date, m.group(3).split(" · ")[0].partition(" — ")[0])
    return sorted(((d, i, t) for i, (d, t) in done.items()), reverse=True)


def annotate(projects, items, today):
    """Flags per item: due (check date reached), unblocked (every on: ID is gone)."""
    for p in projects:
        for it in p["items"]:
            it["due"] = bool(it["check"] and it["check"] <= today)
            refs = ID_RE.findall(it["on"])
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


def doc_link(d):
    repo, _, path = d.partition("/")
    if repo in CFG["repos"] and path:
        url, branch = CFG["repos"][repo]
        p, _, anchor = path.partition("#")
        href = f"{url}/blob/{branch}/{p}" + (f"#{anchor}" if anchor else "")
        return f'<a href="{esc(href)}" target="_blank" rel="noopener">{esc(d)}</a>'
    return esc(d)


def link_ids(text, items):
    out, pos = [], 0
    for m in ID_RE.finditer(text):
        out.append(esc(text[pos:m.start()]))
        i = m.group(0)
        out.append(f'<a href="#{esc(i)}">{esc(i)}</a>' if i in items else f'<s title="done">{esc(i)}</s>')
        pos = m.end()
    out.append(esc(text[pos:]))
    return "".join(out)


def days_txt(n):
    return "today" if n == 0 else (f"in {n} d" if n > 0 else f"{-n} d overdue")


def item_html(it, items, today, show_project=None):
    meta = []
    if it["note"]:
        meta.append(link_ids(it["note"], items))
    if it["on"]:
        meta.append("on: " + link_ids(it["on"], items))
    if it["since"]:
        meta.append(f"since {it['since']} ({(today - it['since']).days} d)")
    if it["check"]:
        n = (it["check"] - today).days
        cls = "due" if n <= 0 else ("soon" if n <= 3 else "")
        meta.append(f'<span class="{cls}">check {it["check"]} · {days_txt(n)}</span>')
    if it["unblocked"]:
        meta.append('<span class="due">unblocked — everything it waited on is done</span>')
    if it["nocheck"]:
        meta.append('<span class="soon">no check date</span>')
    if it["doc"]:
        meta.append(doc_link(it["doc"]))
    extra = "".join(f"<div class='meta'>· {link_ids(e, items)}</div>" for e in it["extra"])
    touched = f" · updated {it['touched']}" if it.get("touched") else ""
    proj = f"{esc(show_project)} · " if show_project else ""
    return (f'<li data-st="{esc(it["state"])}" data-now="{int(it["now"])}">'
            f'<div class="st {esc(it["state"])}">{esc(it["state"])}</div><div>'
            f'<div class="t">{esc(it["title"])}</div>'
            f'<div class="meta">{" · ".join(meta)}</div>{extra}'
            f'<div class="src" id="{"now-" if show_project else ""}{esc(it["id"])}">{proj}{esc(it["id"])}{touched}</div>'
            f'</div></li>')


def render_html(projects, items, problems, done, today, generated, pull_error):
    allitems = [it for p in projects for it in p["items"]]
    now = [(p, it) for p in projects for it in p["items"] if it["now"]]
    now.sort(key=lambda pi: sort_key(pi[1]))
    count = {s: sum(1 for it in allitems if it["state"] == s) for s in STATES}
    stats = [("next", count["next"], "you can act"),
             ("now", len(now), "checks due / unblocked"),
             ("waiting", count["waiting"], "on things, people, items"),
             ("watch", count["watch"], "verify when…"),
             ("someday", count["someday"], "wanted, not planned")]
    stats_html = "".join(
        f'<div class="stat" data-k="{k}"><b>{n}</b><span>{k} — {esc(lbl)}</span></div>'
        for k, n, lbl in stats)
    areas = ["all"] + [a for a in area_list(projects) if any(p["area"] == a for p in projects)]
    filt = "".join(f'<button data-a="{a}" class="{"on" if a == "all" else ""}">{a}</button>' for a in areas)

    now_html = "".join(item_html(it, items, today, p["name"]) for p, it in now) or \
        '<li><div class="st"></div><div class="meta">Nothing due today.</div></li>'

    order = {"active": 0, "paused": 1, "done": 2}
    cards = []
    for p in sorted(projects, key=lambda p: order.get(p["status"], 3)):
        if not p["items"] and p["status"] == "done":
            continue
        lis = "".join(item_html(it, items, today) for it in sorted(p["items"], key=sort_key))
        docs = " · ".join(doc_link(d) for d in p["docs"])
        cards.append(
            f'<div class="proj {"paused" if p["status"] != "active" else ""}" data-area="{esc(p["area"])}">'
            f'<h2><span class="area">{esc(p["area"])}</span>{esc(p["name"])}'
            f'<span class="pstatus">{esc(p["status"])} · {len(p["items"])}</span></h2>'
            f'<ul>{lis}</ul><div class="foot"><div class="src docs">{docs}</div>'
            f'<span class="pid">{esc(p["id"])}</span></div></div>')

    area_of = {p["id"]: p["area"] for p in projects}
    done_html = "".join(
        f"<li data-area='{esc(a)}'><div class='st done'>done</div>"
        f"<div><span class='badge' data-a='{esc(a)}'>{esc(a.capitalize())}</span></div><div><div class='t'>{esc(t)}</div>"
        f"<div class='src'>{esc(i)} · {esc(d)}</div></div></li>"
        for d, i, t in done for a in [area_of.get(i.rpartition("-")[0], "?")])
    if done_html:
        done_html += "<li class='empty hidden'><div class='meta'>Nothing finished in this area in the last 7 days.</div></li>"
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
    return (TEMPLATE
            .replace("%TITLE%", esc(CFG["title"]))
            .replace("%REFRESH%", refresh)
            .replace("%AREACSS%", area_css(area_list(projects)))
            .replace("%SOURCE%", source)
            .replace("%SCHEDULE%", f" · {esc(CFG['schedule'])}" if CFG["schedule"] else "")
            .replace("%GENERATED%", esc(generated))
            .replace("%WARN%", warn_html)
            .replace("%STATS%", stats_html)
            .replace("%FILTERS%", filt)
            .replace("%NOW%", now_html)
            .replace("%CARDS%", "".join(cards))
            .replace("%DONE%", done_html or "<li><div class='meta'>Nothing finished in the last 7 days.</div></li>"))


def area_css(areas):
    named = [(i, a) for i, a in enumerate(a for a in areas if AREA_RE.match(a))]
    light = "".join(f"--a-{i}:{AREA_COLORS[i % len(AREA_COLORS)][0]};" for i, _ in named)
    dark = "".join(f"--a-{i}:{AREA_COLORS[i % len(AREA_COLORS)][1]};" for i, _ in named)
    rules = "".join(f'.filters button[data-a="{a}"],.badge[data-a="{a}"]{{background:var(--a-{i})}}\n'
                    for i, a in named)
    return (f":root{{{light}}}\n@media (prefers-color-scheme: dark){{"
            f':root:not([data-theme="light"]){{{dark}}}}}\n{rules}')


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
        out += ["## Done in the last 7 days", ""] + [f"- {d} **{i}** {t}" for d, i, t in done] + [""]
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
    problems = cfg_problems + problems
    annotate(projects, items, today)
    done = recently_done(repo, items)

    write_atomic(Path(a.site) / "index.html",
                 render_html(projects, items, problems, done, today, generated, pull_error))

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


TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
%REFRESH%
<title>%TITLE%</title>
<link rel="icon" type="image/svg+xml" href="data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjAgMCA2NCA2NCI+PGVsbGlwc2UgY3g9IjMyIiBjeT0iMzIiIHJ4PSIzMCIgcnk9IjE5IiBmaWxsPSIjZjVjNTE4IiBzdHJva2U9IiMxMTEiIHN0cm9rZS13aWR0aD0iMi41Ii8+PHBhdGggZmlsbD0iIzExMSIgZD0iTTcgMjdRMTQgMjEgMjIgMjIuNVEyNiAyMy41IDI3IDI3TDI4LjYgMTkuNUwzMC42IDI1LjJMMzMuNCAyNS4yTDM1LjQgMTkuNUwzNyAyN1EzOCAyMy41IDQyIDIyLjVRNTAgMjEgNTcgMjdRNTEuNSAyOSA1MS41IDM1UTQ3LjUgMzEuNSA0MyAzNC41UTM5LjUgMzQgMzcuNSAzOS41UTM0IDM4LjUgMzIgNDVRMzAgMzguNSAyNi41IDM5LjVRMjQuNSAzNCAyMSAzNC41UTE2LjUgMzEuNSAxMi41IDM1UTEyLjUgMjkgNyAyN1oiLz48L3N2Zz4=">
<style>
:root{
  --bg:#f6f5f2; --panel:#ffffff; --ink:#1d1d1b; --muted:#6b6a66; --line:#e4e2dc;
  --next:#1f6feb; --waiting:#b7791f; --watch:#6f42c1; --someday:#8a8a85; --done:#2e8b57;
  --alert:#c0392b; --chip:#efeee9; --a-ink:#3a3936;
}
@media (prefers-color-scheme: dark){
  :root:not([data-theme="light"]){
    --bg:#151514; --panel:#1e1e1c; --ink:#ecebe6; --muted:#9c9b95; --line:#2f2e2b;
    --next:#58a6ff; --waiting:#e3b341; --watch:#b392f0; --someday:#8b8b86; --done:#56d364;
    --alert:#f47067; --chip:#2a2a27; --a-ink:#ecebe6;
  }
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif}
a{color:inherit;text-decoration-color:var(--line)}
header,.stats,.filters,main{max-width:1180px;margin-left:auto;margin-right:auto;padding-left:16px;padding-right:16px}
header{padding-top:20px}
h1{font-size:20px;margin:0 0 2px}
.sub{color:var(--muted);font-size:12px}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin-top:14px;margin-bottom:14px}
.stat{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:10px 12px;cursor:pointer;user-select:none}
.stat b{display:block;font-size:22px}
.stat span{font-size:12px;color:var(--muted)}
.stat.on{outline:2px solid var(--ink)}
.filters{display:flex;flex-wrap:wrap;gap:6px}
.filters button{border:1px solid var(--line);background:var(--panel);color:var(--ink);border-radius:999px;padding:4px 12px;font:inherit;font-size:12px;cursor:pointer}
.filters button.on{background:var(--ink);color:var(--bg);border-color:var(--ink)}
%AREACSS%.filters button[data-a]:not([data-a=all]){color:var(--a-ink)}
.filters button[data-a]:not([data-a=all]).on{border-color:var(--ink);box-shadow:0 0 0 1px var(--ink);font-weight:600}
main{margin-top:14px;margin-bottom:40px}
section{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:12px 14px;margin-bottom:16px}
section.warn{border-color:var(--alert)}
section h2,.proj h2{font-size:14px;margin:0 0 8px;display:flex;gap:8px;align-items:baseline;flex-wrap:wrap}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(340px,1fr));gap:14px;margin-bottom:16px}
.proj{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:12px 14px;max-height:360px;display:flex;flex-direction:column}
.proj.paused{opacity:.7}
.proj ul{flex:1;min-height:0;overflow-y:auto;scrollbar-width:thin}
.area{font-size:11px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);font-weight:500}
.pstatus{margin-left:auto;font-size:11px;color:var(--muted)}
ul{list-style:none;margin:0;padding:0}
li{display:grid;grid-template-columns:70px 1fr;gap:8px;padding:6px 0;border-top:1px solid var(--line)}
li:first-child{border-top:0}
li.w{display:block;color:var(--alert);font-size:12px}
.st{font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:.04em;padding-top:1px}
.st.next{color:var(--next)} .st.waiting{color:var(--waiting)} .st.watch{color:var(--watch)} .st.someday{color:var(--someday)} .st.done{color:var(--done)}
.t{font-weight:500}
.meta{font-size:12px;color:var(--muted);overflow-wrap:anywhere}
.due{color:var(--alert);font-weight:600}
.soon{color:var(--waiting);font-weight:600}
.src{font-family:ui-monospace,Consolas,monospace;font-size:11px;color:var(--muted);overflow-wrap:anywhere}
.foot{display:flex;justify-content:space-between;align-items:flex-end;gap:8px;margin-top:6px;padding-top:6px;border-top:1px solid var(--line)}
.docs{width:65%}
.pid{font-family:ui-monospace,Consolas,monospace;font-size:11px;font-weight:600;color:var(--muted)}
.badge{display:block;padding:0 4px;border-radius:999px;background:var(--chip);color:var(--a-ink);font-size:11px;font-weight:500;text-align:center}
#done ul{max-height:380px;overflow-y:auto;scrollbar-width:thin}
#done li[data-area]{grid-template-columns:43px 54px 1fr;column-gap:14px}
.hidden{display:none}
:target{background:var(--chip);border-radius:4px}
@media (max-width:420px){.grid{grid-template-columns:1fr} li{grid-template-columns:60px 1fr} #done li[data-area]{grid-template-columns:52px 64px 1fr;column-gap:8px}}
</style>
</head>
<body>
<header>
  <h1>%TITLE%</h1>
  <div class="sub">Generated %GENERATED%%SOURCE% · read-only%SCHEDULE% · edit items in <code>projects/*.md</code></div>
</header>
<div class="stats" id="stats">%STATS%</div>
<div class="filters" id="filters">%FILTERS%</div>
<main>
%WARN%
<section id="now"><h2>Needs you now <span class="meta">— check dates reached, and waiting items whose blockers are done</span></h2><ul>%NOW%</ul></section>
<div class="grid" id="grid">%CARDS%</div>
<section id="done"><h2>Done in the last 7 days</h2><ul>%DONE%</ul></section>
</main>
<script>
(function(){
  var area="all", state=null;
  function apply(){
    document.querySelectorAll(".proj").forEach(function(c){
      var show=area==="all"||c.dataset.area===area, any=false;
      c.querySelectorAll("li").forEach(function(li){
        var ok=!state||(state==="now"?li.dataset.now==="1":li.dataset.st===state);
        li.classList.toggle("hidden",!ok); if(ok) any=true;
      });
      c.classList.toggle("hidden",!show||!any);
    });
    var shown=0;
    document.querySelectorAll("#done li[data-area]").forEach(function(li){
      var ok=area==="all"||li.dataset.area===area;
      li.classList.toggle("hidden",!ok); if(ok) shown++;
    });
    var empty=document.querySelector("#done li.empty");
    if(empty) empty.classList.toggle("hidden",shown>0);
    document.querySelectorAll(".stat").forEach(function(s){s.classList.toggle("on",s.dataset.k===state);});
    document.querySelectorAll("#filters button").forEach(function(b){b.classList.toggle("on",b.dataset.a===area);});
    try{localStorage.setItem("tasker",JSON.stringify({area:area,state:state}));}catch(e){}
  }
  try{var s=JSON.parse(localStorage.getItem("tasker")||"{}"); area=s.area||"all"; state=s.state||null;}catch(e){}
  document.querySelectorAll(".stat").forEach(function(s){s.onclick=function(){state=state===s.dataset.k?null:s.dataset.k;apply();};});
  document.querySelectorAll("#filters button").forEach(function(b){b.onclick=function(){area=b.dataset.a;apply();};});
  apply();
})();
</script>
</body>
</html>
"""

if __name__ == "__main__":
    main()
