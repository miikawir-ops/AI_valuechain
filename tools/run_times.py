"""tools/run_times.py — when the pipeline actually ran, from run_utc in
scores_detail.jsonl (errata runs excluded), grouped by which run it was:
the morning cron (06:00 UTC), the evening cron (18:00 UTC since 2026-11-18;
20:30 before) or a push/dispatch. GitHub starts scheduled runs hours late,
so the January review re-checks the cron times against these real starts
(Ray, 2026-11-17).

Without the Actions event per run, a run is attributed to the cron slot it
started 0-10 hours after; anything else is listed as "other" (pushes and
manual dispatches land there or inside a slot's range, so treat counts as
approximate and check the Actions history for exact events).

Usage (repo root): python tools/run_times.py
"""
import os
import sys
import datetime
import statistics

sys.path.insert(0, os.getcwd())
from tools.detail_runs import load_runs

EVENING_CHANGE = datetime.date(2026, 11, 18)   # first day of the 18:00 UTC evening cron


def slot(t: datetime.datetime):
    """(slot name, minutes after the slot's cron time) or ("other", None)."""
    evening = (18, 0) if t.date() >= EVENING_CHANGE else (20, 30)
    for name, (h, m) in (("morning 06:00", (6, 0)), (f"evening {evening[0]:02d}:{evening[1]:02d}", evening)):
        for day in (t.date(), t.date() - datetime.timedelta(days=1)):
            start = datetime.datetime(day.year, day.month, day.day, h, m, tzinfo=datetime.timezone.utc)
            delay = (t - start).total_seconds() / 60
            if 0 <= delay <= 600:
                return name, delay
    return "other", None


groups = {}
for r in load_runs():
    t = datetime.datetime.strptime(r["run_utc"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc)
    name, delay = slot(t)
    groups.setdefault(name, []).append((r["run_utc"], r["session"], delay))

for name, rows in sorted(groups.items()):
    delays = [d for _, _, d in rows if d is not None]
    summary = (f"delay min {min(delays):.0f}, median {statistics.median(delays):.0f}, max {max(delays):.0f} min"
               if delays else "")
    print(f"\n{name}: {len(rows)} runs  {summary}")
    for run_utc, session, d in rows:
        print(f"  {run_utc}  session {session}" + (f"  +{d:.0f} min" if d is not None else ""))
