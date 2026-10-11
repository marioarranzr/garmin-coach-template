"""`python -m coach update`: brings the latest template code into your copy.

Keeps your config.yaml and data/, and re-applies your time zone and schedule to
the template's workflow file. The first time (a copy made with "Use this
template" starts with its own history) it joins the template's history; after
that it's a normal merge. It never pushes: review, then `git push`.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

TEMPLATE_URL = "https://github.com/marioarranzr/garmin-coach-template"
WORKFLOW = ".github/workflows/coach.yml"


def _git(*args: str, root: Path, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, check=check)


def _schedule(text: str) -> tuple[str | None, str | None]:
    tz = re.search(r"\n(\s*TZ:\s*)(\S+)", text)
    cron = re.search(r'- cron:\s*"([^"]*)"', text)
    return (tz.group(2) if tz else None), (cron.group(1) if cron else None)


def _apply_schedule(path: Path, tz: str | None, cron: str | None) -> None:
    if not path.exists():
        return
    s = path.read_text(encoding="utf-8")
    if tz:
        s = re.sub(r"(\n\s*TZ:\s*)\S+", rf"\g<1>{tz}", s, count=1)
    if cron:
        s = re.sub(r'(- cron:\s*)"[^"]*"', rf'\g<1>"{cron}"', s, count=1)
    path.write_text(s, encoding="utf-8")


def run(root: Path, url: str = TEMPLATE_URL, branch: str = "main") -> int:
    try:
        return _run(root, url, branch)
    except subprocess.CalledProcessError as e:
        cmd = " ".join(e.cmd)
        detail = (e.stderr or e.stdout or "").strip()
        print(f"\n❌ `{cmd}` failed:\n{detail}\n")
        if "fetch" in e.cmd:
            print(f"Check the template remote with `git remote -v` (it should be {url})\n"
                  "and that you're online. Fix it with:\n"
                  f"  git remote set-url template {url}")
        elif "Please tell me who you are" in detail:
            print("Git needs your name and email once:\n"
                  '  git config --global user.name "Your Name"\n'
                  '  git config --global user.email "you@example.com"')
        print("Nothing was changed. If a merge was started, `git merge --abort` undoes it.")
        return 1


def _run(root: Path, url: str, branch: str) -> int:
    if _git("status", "--porcelain", root=root).stdout.strip():
        print("You have uncommitted changes. Commit or stash them first, then run the update again.")
        return 1
    remotes = _git("remote", root=root).stdout.split()
    if "template" not in remotes:
        _git("remote", "add", "template", url, root=root)
    else:
        current = _git("remote", "get-url", "template", root=root).stdout.strip()
        if current.rstrip("/").removesuffix(".git") != url.rstrip("/").removesuffix(".git"):
            print(f"Note: your 'template' remote points to {current}, not {url}.")
    print(f"Fetching the template ({url})…")
    _git("fetch", "template", branch, root=root)
    ref = f"template/{branch}"

    wf = root / WORKFLOW
    tz, cron = _schedule(wf.read_text(encoding="utf-8")) if wf.exists() else (None, None)
    before = _git("rev-parse", "HEAD", root=root).stdout.strip()
    if _git("merge-base", "--is-ancestor", ref, "HEAD", root=root, check=False).returncode == 0:
        print("Already up to date with the template.")
        return 0

    linked = _git("merge-base", "HEAD", ref, root=root, check=False).returncode == 0
    if linked:
        merge = _git("merge", ref, "--no-edit", "-m", "Update from the template", root=root, check=False)
        if merge.returncode != 0:
            print(merge.stdout + merge.stderr)
            print("\nGit couldn't merge some lines automatically (see above). Open the files it lists, keep\n"
                  "your own values in config.yaml, remove the <<<<<<< ======= >>>>>>> markers, then run:\n"
                  "  git add -A && git commit\n"
                  "Or undo the whole update with:  git merge --abort")
            return 1
    else:
        # first update of a copy made with "Use this template": join the histories
        _git("merge", ref, "--allow-unrelated-histories", "-X", "theirs",
             "-m", "Join the template's history", root=root)
        _git("checkout", before, "--", "config.yaml", root=root)
        if (root / "data").exists() or _git("ls-tree", before, "data", root=root).stdout.strip():
            _git("checkout", before, "--", "data", root=root, check=False)

    _apply_schedule(wf, tz, cron)
    if _git("status", "--porcelain", root=root).stdout.strip():
        _git("commit", "-am", "Keep my settings after the template update", root=root)

    changes = _git("log", "--oneline", f"{before}..{ref}", root=root).stdout.strip().splitlines()
    print(f"\n✅ Updated with {len(changes)} template change(s):")
    for line in changes[:15]:
        print("  " + line)
    print("\nYour config.yaml, data and schedule were kept. Run the tests if you like:\n"
          "  .venv/bin/python tests/test_scenarios.py\n"
          "then publish it with:  git push")
    return 0
