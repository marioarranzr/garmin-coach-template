"""Offline tests: a fake Garmin with different scenarios.
Run:  python -m pytest -q tests   (or  python tests/test_scenarios.py)"""
import datetime as dt
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import yaml  # noqa: E402

from coach import engine  # noqa: E402
from coach.planner import plan_day, phase_for  # noqa: E402
from coach.workouts import to_garmin  # noqa: E402

CFG = yaml.safe_load((Path(__file__).resolve().parent / "test_config.yaml").read_text(encoding="utf-8"))


def act(date, typ, km, mins, aero=2.5, anae=0.5, load=None):
    return {"activityId": hash((date, typ, km)) % 10**8, "activityName": typ, "startTimeLocal": f"{date} 07:00:00",
            "activityType": {"typeKey": typ}, "distance": km * 1000, "duration": mins * 60,
            "averageHR": 140, "aerobicTrainingEffect": aero, "anaerobicTrainingEffect": anae,
            "activityTrainingLoad": load or mins * 1.2}


class FakeGarmin:
    def __init__(self, today, good=True, extra_yesterday=0.0, swims=True):
        self.today = today
        self.good = good
        self.uploaded, self.scheduled, self.deleted = [], [], []
        acts = []
        for k in range(1, 36):
            d = today - dt.timedelta(days=k)
            wd = d.weekday()
            if wd in (1, 3, 5):
                acts.append(act(d.isoformat(), "running", 7, 42))
            if wd == 6:
                acts.append(act(d.isoformat(), "running", 12, 72))
            if wd == 2:
                acts.append(act(d.isoformat(), "road_biking", 25, 60))
            if wd == 0 and swims and k > 14:
                acts.append(act(d.isoformat(), "lap_swimming", 1.5, 40))
        y = (today - dt.timedelta(days=1)).isoformat()
        if extra_yesterday:
            acts = [a for a in acts if not a["startTimeLocal"].startswith(y)]
            acts.append(act(y, "running", 6 + extra_yesterday, 70, aero=4.2, anae=2.8))
        self.acts = acts

    def get_activities_by_date(self, s, e):
        return [a for a in self.acts if s <= a["startTimeLocal"][:10] <= e]

    def get_sleep_data(self, d):
        return {"dailySleepDTO": {"sleepTimeSeconds": (7.8 if self.good else 5.2) * 3600,
                                  "sleepScores": {"overall": {"value": 85 if self.good else 48}}}}

    def get_hrv_data(self, d):
        return {"hrvSummary": {"lastNightAvg": 62 if self.good else 41, "weeklyAvg": 60,
                               "status": "BALANCED" if self.good else "LOW",
                               "baseline": {"balancedLow": 52, "balancedUpper": 70}}}

    def get_stats(self, d):
        return {"restingHeartRate": 48 if self.good else 57, "lastSevenDaysAvgRestingHeartRate": 49,
                "averageStressLevel": 24 if self.good else 48, "bodyBatteryHighestValue": 88 if self.good else 35}

    def get_body_battery(self, d):
        v = 88 if self.good else 35
        return [{"bodyBatteryValuesArray": [[1, v - 20], [2, v]]}]

    def get_training_readiness(self, d):
        return [{"score": 82 if self.good else 22, "level": "HIGH" if self.good else "LOW"}]

    def get_training_status(self, d):
        return {"mostRecentTrainingStatus": {"latestTrainingStatusData": {"123": {
            "trainingStatusFeedbackPhrase": "PRODUCTIVE",
            "acuteTrainingLoadDTO": {"dailyAcuteChronicWorkloadRatio": 1.1}}}}}

    def upload_workout(self, w):
        json.dumps(w)  # must be serializable
        self.uploaded.append(w)
        return {"workoutId": len(self.uploaded)}

    def schedule_workout(self, wid, date):
        self.scheduled.append((wid, date))
        return {"workoutScheduleId": 1000 + wid}

    def unschedule_workout(self, sid):
        self.deleted.append(("sched", sid))

    def delete_workout(self, wid):
        self.deleted.append(("w", wid))


def _run(day, **kw):
    api = FakeGarmin(day, **kw)
    with tempfile.TemporaryDirectory() as tmp:
        res = engine.run_day(api, CFG, day, upload=True, state_path=Path(tmp) / "s.json")
    return api, res


def test_good_day_follows_plan():
    day = dt.date(2026, 10, 13)  # Tuesday → quality
    api, res = _run(day)
    assert res["readiness"]["level"] == "green"
    assert any(s.kind == "run_quality" for s in res["final"])
    assert api.uploaded and api.scheduled[0][1] == "2026-10-13"


def test_bad_day_goes_to_rest():
    day = dt.date(2026, 10, 13)
    api, res = _run(day, good=False)
    assert res["readiness"]["level"] == "red"
    assert [s.kind for s in res["final"]] == ["rest"]


def test_overdid_yesterday_cuts_today():
    day = dt.date(2026, 10, 15)  # Thursday (Wednesday: bike, no run planned)
    api, res = _run(day, extra_yesterday=4)
    titles = " ".join(res["notes"])
    assert "more than planned" in titles or "intens" in titles.lower() or res["readiness"]["score"] < 80


def test_no_swims_marks_priority():
    day = dt.date(2026, 10, 16)  # Friday → swim
    api, res = _run(day, swims=False)
    assert any(s.priority for s in res["final"] if s.sport == "swim")


def test_phases():
    assert phase_for(dt.date(2026, 10, 12), CFG).name == "base"
    assert phase_for(dt.date(2026, 11, 20), CFG).name == "specific"
    assert phase_for(dt.date(2026, 12, 10), CFG).name == "race_week"
    assert phase_for(dt.date(2026, 12, 16), CFG).name == "transition"
    assert phase_for(dt.date(2027, 3, 20), CFG).name == "peak"
    assert phase_for(dt.date(2027, 4, 15), CFG).name == "transition"
    assert phase_for(dt.date(2027, 5, 10), CFG).focus == "tri"
    assert phase_for(dt.date(2026, 11, 20), CFG).label == "8.5 km specific"


def test_three_runs_and_one_swim_per_week():
    kinds = []
    for i in range(7):
        kinds += [s.kind for s in plan_day(dt.date(2026, 10, 12) + dt.timedelta(days=i), CFG, 25)[1]]
    assert sum(k.startswith("run_") for k in kinds) == 3
    assert sum(k.startswith("swim_") for k in kinds) == 1


def test_cloud_tokens_roundtrip(monkeypatch=None):
    import os
    from cryptography.fernet import Fernet
    from coach import garmin_api as G
    key = Fernet.generate_key().decode()
    os.environ[G.KEY_ENV] = key
    old = G.TOKENS_ENC
    with tempfile.TemporaryDirectory() as tmp:
        G.TOKENS_ENC = Path(tmp) / "t.enc"
        class C:  # fake client
            def dumps(self): return '{"di_token": "a", "di_refresh_token": "b", "di_client_id": "c"}'
        class A: client = C()
        G.save_cloud_tokens(A())
        assert b"di_token" not in G.TOKENS_ENC.read_bytes()
        assert "di_refresh_token" in G._fernet(key).decrypt(G.TOKENS_ENC.read_bytes()).decode()
    G.TOKENS_ENC = old
    del os.environ[G.KEY_ENV]


def test_all_sessions_build_json():
    for i in range(0, 300, 3):
        d = dt.date(2026, 10, 12) + dt.timedelta(days=i)
        _, sess = plan_day(d, CFG, 35)
        for s in sess:
            if s.steps:
                w = to_garmin(s, CFG, "x")
                assert w["workoutSegments"][0]["workoutSteps"]



def test_return_to_running_mode():
    """Mostly cycling, ~4 km/week of running: start at 10 km, no hard sessions."""
    from coach.planner import compute_week_target
    mon = dt.date(2026, 10, 12)
    acts = [{"date": (mon - dt.timedelta(days=d)).isoformat(), "sport": "run", "distance_km": 4.2,
             "duration_min": 25} for d in (8, 15, 22)] + \
           [{"date": (mon - dt.timedelta(days=d)).isoformat(), "sport": "run", "distance_km": 4.2,
             "duration_min": 25} for d in (25,)]
    wt = compute_week_target(acts, mon, CFG)
    assert wt["return_mode"] and wt["target_km"] == 10
    kinds, km = [], 0
    for i in range(7):
        sess = plan_day(mon + dt.timedelta(days=i), CFG, wt["target_km"], True)[1]
        kinds += [s.kind for s in sess]
        km += sum(s.distance_km or 0 for s in sess if s.sport == "run")
    assert "run_quality" not in kinds
    assert km <= 12.5
    # next week, after running 10 km: ~20 % more
    acts2 = acts + [{"date": (mon + dt.timedelta(days=d)).isoformat(), "sport": "run",
                     "distance_km": 10 / 3, "duration_min": 20} for d in (1, 3, 6)]
    wt2 = compute_week_target(acts2, mon + dt.timedelta(days=7), CFG)
    assert wt2["return_mode"] and 11.5 <= wt2["target_km"] <= 12.5


def test_setup_wizard_writes_a_working_config():
    """Scripted answers → config.yaml + workflow time zone, usable by the planner."""
    import shutil
    from coach import setup_wizard
    future = (dt.date.today() + dt.timedelta(days=120)).isoformat()
    later = (dt.date.today() + dt.timedelta(days=250)).isoformat()
    answers = iter([
        "3",                                   # triathlon
        "City half", future, "3", "A",         # race 1: half marathon
        "Lake sprint", later, "6", "",         # race 2: sprint tri, default priority A
        "",                                    # done with races
        "4", "sun", "mon", "5:30", "50", "America/New_York", "06:30",
    ])
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        wf = root / ".github" / "workflows"
        wf.mkdir(parents=True)
        shutil.copy(Path(__file__).resolve().parent.parent / ".github" / "workflows" / "coach.yml", wf)
        setup_wizard.run(root, ask=lambda prompt: next(answers))
        cfg = yaml.safe_load((root / "config.yaml").read_text())
        flow = (wf / "coach.yml").read_text()
    assert [r["kind"] for r in cfg["races"]] == ["half", "tri_sprint"]
    assert cfg["athlete"]["easy_pace_min_per_km"] == 5.5 and cfg["athlete"]["pool_length_m"] == 50
    runs = [k for day in cfg["weekly_templates"]["run_focus"].values() for k in day if k.startswith("run_")]
    assert len(runs) == 4
    assert "TZ: America/New_York" in flow and '"30 11 * * *"' in flow
    for i in range(0, 260, 5):   # the planner accepts it for the whole season
        plan_day(dt.date.today() + dt.timedelta(days=i), cfg, 30)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
