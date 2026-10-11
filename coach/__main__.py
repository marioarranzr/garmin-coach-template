"""Usage:
    python -m coach setup                 # guided setup: writes config.yaml
    python -m coach login                 # first time: saves your Garmin session tokens
    python -m coach run                   # today's workout → Garmin calendar
    python -m coach run --dry-run         # same, but uploads nothing
    python -m coach run --date 2026-10-14
    python -m coach week                  # preview of the next 7 days
    python -m coach export-tokens         # prepares the encrypted tokens for GitHub
    python -m coach update                # brings in the latest template code, keeps your settings
"""
from __future__ import annotations

import argparse
import datetime as dt
import logging
import os
import sys
from pathlib import Path

import yaml

from . import garmin_api as G
from .engine import ROOT, run_day, week_target
from .planner import history_start, learn_long_day, plan_day, phase_for


def load_cfg(path: str | None) -> dict:
    p = Path(path) if path else ROOT / "config.yaml"
    return yaml.safe_load(p.read_text(encoding="utf-8"))


def main(argv=None):
    ap = argparse.ArgumentParser(prog="coach")
    ap.add_argument("cmd", choices=["setup", "login", "run", "week", "semana", "export-tokens", "update"])
    ap.add_argument("--date", help="YYYY-MM-DD (default: today)")
    ap.add_argument("--dry-run", action="store_true", help="don't upload anything to Garmin")
    ap.add_argument("--wait-for-sleep", action="store_true",
                    help="scheduled runs: skip if today is planned, wait until last night's sleep reaches Garmin")
    ap.add_argument("--config")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args(argv)
    if a.cmd == "semana":  # old alias
        a.cmd = "week"

    logging.basicConfig(level=logging.INFO if a.verbose else logging.WARNING,
                        format="%(levelname)s %(message)s")
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    if a.cmd == "update":
        from .update import run as update
        raise SystemExit(update(ROOT))
    if a.cmd == "setup":
        from .setup_wizard import run as setup
        setup(ROOT)
        return
    cfg_file = Path(a.config) if a.config else ROOT / "config.yaml"
    if not cfg_file.exists():
        raise SystemExit("No config.yaml yet: run 'python -m coach setup' first.")
    cfg = load_cfg(a.config)
    tokens = cfg.get("garmin", {}).get("tokenstore", "~/.garminconnect")
    d = dt.date.fromisoformat(a.date) if a.date else dt.date.today()

    if a.cmd == "login":
        api = G.connect(tokens, interactive=True)
        print(f"✅ Logged in as {api.get_full_name()}. Tokens saved in {tokens}.")
        return

    if a.cmd == "export-tokens":
        key = G.export_tokens(tokens)
        print("✅ Tokens encrypted in data/garmin_tokens.enc (commit it to the repository).\n")
        print("Create the GitHub secret GARMIN_TOKENS_KEY with this value (keep it private):\n")
        print(key)
        return

    api = G.connect(tokens)
    try:
        if a.cmd == "run":
            upload = cfg.get("garmin", {}).get("upload", True) and not a.dry_run
            res = run_day(api, cfg, d, upload=upload, wait_for_sleep=a.wait_for_sleep)
            print(res["report"])
            summary = os.environ.get("GITHUB_STEP_SUMMARY")
            if summary:
                with open(summary, "a", encoding="utf-8") as f:
                    f.write(res["report"])
            return
    finally:
        G.save_cloud_tokens(api)

    if a.cmd == "week":
        acts = G.fetch_activities(api, history_start(d), d - dt.timedelta(days=1))
        learned = {s: day for s in ("run", "bike") if (day := learn_long_day(acts, d, s))}
        cfg = dict(cfg, _learned_days=learned)
        wt = week_target(acts, d, cfg)
        print(f"Phase: {phase_for(d, cfg).describe()}")
        print(f"Running target this week: {wt['target_km']} km "
              f"(4-week average: {wt['chronic_km']} km)\n")
        if wt.get("return_mode"):
            print("Return-to-running mode: easy runs only, volume grows ~20 %/week.\n")
        for i in range(7):
            day = d + dt.timedelta(days=i)
            _, sess = plan_day(day, cfg, wt)
            print(f"{day:%a %d/%m}: " + " + ".join(s.title for s in sess))
        print("\n(Base plan: each morning it is adjusted to your recovery.)")


if __name__ == "__main__":
    main()
