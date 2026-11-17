"""tools/detail_runs.py — the runs in scores_detail.jsonl that analyses may use.

scores_detail.jsonl is append-only and never edited. Runs found to be wrong
are listed in scores_detail_errata.json (run_utc, session, reason) and left
out here, so every analysis (the January review's noise floor, the Red
replay's market caps) reads the same corrected set.

    from tools.detail_runs import load_runs
    runs = load_runs()            # list of records, errata runs excluded, file order
"""
import json
from pathlib import Path

DETAIL = "scores_detail.jsonl"
ERRATA = "scores_detail_errata.json"


def errata_run_ids(path: str = ERRATA) -> set:
    """run_utc of every errata run. A missing file means no errata; an
    unreadable one raises, so an analysis never silently includes bad runs."""
    p = Path(path)
    if not p.exists():
        return set()
    return {r["run_utc"] for r in json.loads(p.read_text(encoding="utf-8"))["runs"]}


def load_runs(path: str = DETAIL, errata_path: str = ERRATA) -> list:
    bad = errata_run_ids(errata_path)
    runs = []
    for line in open(path, encoding="utf-8"):
        if line.strip():
            rec = json.loads(line)
            if rec.get("run_utc") not in bad:
                runs.append(rec)
    return runs


if __name__ == "__main__":
    all_runs = sum(1 for line in open(DETAIL, encoding="utf-8") if line.strip())
    kept = load_runs()
    print(f"{all_runs} runs in {DETAIL}, {all_runs - len(kept)} excluded by {ERRATA}, {len(kept)} kept")
