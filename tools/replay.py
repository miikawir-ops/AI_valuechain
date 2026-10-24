"""tools/replay.py — the before/after replay: score and render a saved fetch
with the code in the current directory, in a temp dir with a copy of
scores_history.json (the repo's files are never touched). Writes a JSON
snapshot (layer/company scores, colours, deltas, card ratings and forecasts)
and the rendered page.

Before/after a change: save a live fetch once ({"market", "macro",
"sessions"} from main.stage_fetch + fetch_trading_sessions), run this from a
`git worktree` of the old commit and from the new one on the same file, and
diff the snapshots. Same input on both sides means every difference comes
from the code.

Usage (repo root): python tools/replay.py <fetch.json> <snap_out.json> <page_out.html>"""
import os, sys, json, shutil, tempfile, datetime, logging, re
logging.disable(50)
sys.path.insert(0, os.getcwd())
import main, render

d = json.load(open(sys.argv[1]))
sessions = [datetime.date.fromisoformat(s) for s in d["sessions"]]
repo = os.getcwd()
with tempfile.TemporaryDirectory() as tmp:
    shutil.copy("scores_history.json", tmp); os.chdir(tmp)
    try:
        scored = main.stage_score(d["market"], d["macro"], sessions)
        render.generate_dashboard(scored, "(Replay: Gemini analysis not run.)", d["macro"], sessions[-1], d["market"], [])
        page = open("index.html", encoding="utf-8").read()
        hist = json.load(open("scores_history.json"))
    finally:
        os.chdir(repo)
open(sys.argv[3], "w", encoding="utf-8").write(page)
layers = json.loads(re.search(r"const LAYERS\s*= (\[.*?\]);\n", page).group(1))
snap = {"layers": {}, "last_entry": {k: v for k, v in hist[-1].items() if k != "scores"},
        "has_yesterday": "const HAS_YESTERDAY= true;" in page,
        "method_note": bool(re.search(r"Methodology change, \w+ \d+:", page))}
for lid, r in scored.items():
    l = next(x for x in layers if x["id"] == lid)
    snap["layers"][lid] = {
        "score": r["best"]["score"], "color": r["best"]["color"], "status": r["best"]["status"],
        "unmeasured": l.get("unmeasured"), "earnings": l.get("earnings"),
        "tickers": {t["ticker"]: {"score": t["score"], "color": t["color"], "delta": t.get("fund_delta")}
                    for t in r["all_tickers"]},
        "cards": {t["sym"]: {"rating": t["rating"], "band": t["delta_band"], "forecast": t["forecast"]["label"],
                             "yoy": t.get("growth_yoy"), "q": t.get("growth_q")} for t in l["tickers"]},
    }
open(sys.argv[2], "w").write(json.dumps(snap, indent=1, sort_keys=True))
