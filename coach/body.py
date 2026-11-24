"""Body composition from the scale (weight, fat, muscle...): a small series the coach reads for trends.

Entries come from the athlete's notes ("scale 75.2 kg, fat 8.9, muscle 62.9": typed or dictated into Todoist,
read by the schedule-notes call) or are added by hand to `data/coach_log/body.json`. Bio-impedance scales are
noisy (hydration), so the coach uses trends over weeks, not one weigh-in.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

FILE = "data/coach_log/body.json"
FIELDS = ("weight_kg", "fat_kg", "fat_pct", "muscle_kg", "lean_kg", "water_kg", "bone_kg", "visceral", "bmr_kcal")


def path(root: Path) -> Path:
    return Path(root) / FILE


def read(root: Path) -> list[dict]:
    try:
        rows = json.loads(path(root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return sorted((r for r in rows if isinstance(r, dict) and r.get("date")), key=lambda r: r["date"])


def add(root: Path, entries: list[dict]) -> int:
    """Adds weigh-ins (one per date: a new one for a date replaces the old). Returns how many were new/changed."""
    rows = {r["date"]: r for r in read(root)}
    changed = 0
    for e in entries:
        clean = {k: e[k] for k in FIELDS if isinstance(e.get(k), (int, float))}
        if not e.get("date") or not clean:
            continue
        row = {"date": str(e["date"])[:10], **clean}
        if rows.get(row["date"]) != row:
            rows[row["date"]] = row
            changed += 1
    if changed:
        p = path(root)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps([rows[k] for k in sorted(rows)], indent=1) + "\n", encoding="utf-8")
    return changed


def summary(root: Path, d: dt.date, weeks: int = 4) -> dict | None:
    """Latest weigh-in and the change over the last `weeks` weeks (None without data)."""
    rows = [r for r in read(root) if r["date"] <= d.isoformat()]
    if not rows:
        return None
    last = rows[-1]
    cutoff = (d - dt.timedelta(weeks=weeks)).isoformat()
    before = [r for r in rows if r["date"] <= cutoff]
    ref = before[-1] if before else (rows[0] if len(rows) > 1 else None)
    out = {"latest": last, "weigh_ins": len(rows)}
    if ref and ref is not last:
        change = {k: round(last[k] - ref[k], 1) for k in FIELDS if k in last and k in ref}
        out["change_since"] = {"date": ref["date"], **change}
        flags = []
        if change.get("muscle_kg", 0) <= -1.0:
            flags.append("muscle mass down 1 kg or more: keep strength training and check protein and energy intake")
        if change.get("weight_kg", 0) <= -2.0:
            flags.append("weight down 2 kg or more")
        if flags:
            out["flags"] = flags
    out["note"] = "bio-impedance scale: noisy (hydration); judge trends over weeks, not one weigh-in"
    return out
