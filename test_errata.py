"""
test_errata.py — scores_detail.jsonl is append-only; runs found to be wrong
are listed in scores_detail_errata.json and excluded from analyses by
tools/detail_runs.py (Ray, 2026-11-17: the five empty-bar-window runs).

No pytest in this environment — plain script. Run: python test_errata.py
"""

import json
import tempfile
from pathlib import Path

from tools.detail_runs import load_runs, errata_run_ids


def test_errata_runs_are_excluded_and_exist():
    with tempfile.TemporaryDirectory() as tmp:
        detail, errata = Path(tmp) / "d.jsonl", Path(tmp) / "e.json"
        detail.write_text("\n".join(json.dumps({"run_utc": f"2026-11-1{i}T00:00:00Z", "session": f"2026-11-1{i}"})
                                    for i in range(3)) + "\n")
        errata.write_text(json.dumps({"runs": [{"run_utc": "2026-11-11T00:00:00Z"}]}))
        kept = load_runs(str(detail), str(errata))
        assert [r["session"] for r in kept] == ["2026-11-10", "2026-11-12"], kept
        assert len(load_runs(str(detail), str(Path(tmp) / "none.json"))) == 3, "no errata file: nothing excluded"
    stored = {json.loads(l)["run_utc"] for l in open("scores_detail.jsonl", encoding="utf-8") if l.strip()}
    listed = errata_run_ids()
    assert len(listed) == 5 and listed <= stored, f"errata runs not in scores_detail.jsonl: {listed - stored}"
    print("PASS: errata runs are excluded; all 5 listed runs exist in scores_detail.jsonl")


if __name__ == "__main__":
    test_errata_runs_are_excluded_and_exist()
    print("\nAll errata tests passed.")
