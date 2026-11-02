"""
changes.py — "What changed" since the previous session, generated from
scores_detail.jsonl, no manual curation (Ray, 2026-11-02). Display-only.

Each session's last run (the track record's rule) is compared with the
previous session's. Listed:
- layer colour changes and changes of confirmation state ("Confirmed
  Orange" → "Holding Orange"), as they are: they're already confirmed. The
  stored status's day counts ("2/3 recent days") aren't compared, or every
  session would list them;
- company rating or colour changes, only when the score moved at least
  MIN_SCORE_MOVE points (above the 1-2 points of run-to-run news noise
  measured in PLAN.md) or the company reported a new quarter;
- new or cleared hype and divergence flags;
- companies with a newly reported quarter (growth_quarter moved).
Nothing is compared across a scoring-method change.
"""

import json
import datetime

MIN_SCORE_MOVE = 2.0


def _by_session(path: str) -> dict:
    out = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                rec = json.loads(line)
                out[rec["session"]] = rec          # the session's last run wins
    return out


def _state(status: str) -> str:
    """The confirmation state without its day counts: "Confirmed Orange — 3/3
    recent days above 40" -> "Confirmed Orange"."""
    return (status or "").split(" — ")[0].split(" (")[0].strip()


def _qd(iso: str) -> str:
    d = datetime.date.fromisoformat(iso)
    return f"{d:%b} {d.day}, {d.year}"


def compare(prev: dict, cur: dict, names: dict = None) -> list:
    """Changes from one session's record to the next, as display lines."""
    names = names or {}
    items = []
    for lid, c in cur["layers"].items():
        p = prev["layers"].get(lid)
        if not p:
            continue
        n = names.get(lid, lid)
        if p["color"] != c["color"]:
            items.append(f"{n}: {p['color']} → {c['color']} (score {p['score']:.1f} → {c['score']:.1f})")
        elif _state(p["status"]) != _state(c["status"]):
            items.append(f"{n}: {_state(p['status'])} → {_state(c['status'])}")
    pc = {x["ticker"]: x for x in prev["companies"]}
    flags, ratings, quarters = [], [], []
    for x in cur["companies"]:
        y = pc.get(x["ticker"])
        if not y:
            continue
        t = x["ticker"]
        q0, q1 = y["inputs"].get("growth_quarter"), x["inputs"].get("growth_quarter")
        new_q = bool(q0 and q1 and q0 != q1)
        moved = x["score"] is not None and y["score"] is not None and abs(x["score"] - y["score"]) >= MIN_SCORE_MOVE
        parts = []
        if x.get("rating") != y.get("rating") and (moved or new_q):
            parts.append(f"rating {y.get('rating') or '—'} → {x.get('rating') or '—'}")
        if x.get("color") != y.get("color") and (moved or new_q):
            parts.append(f"{y.get('color')} → {x.get('color')}")
        if parts:
            ratings.append(f"{t}: {', '.join(parts)} (score {y['score']:.1f} → {x['score']:.1f})")
        if x.get("is_hype") != y.get("is_hype"):
            flags.append(f"{t}: hype flag {'new' if x.get('is_hype') else 'cleared'}")
        dx, dy = (x.get("divergence") or {}).get("flag"), (y.get("divergence") or {}).get("flag")
        if bool(dx) != bool(dy):
            flags.append(f"{t}: divergence flag {'new' if dx else 'cleared'}")
        if new_q:
            quarters.append(f"{t}: new quarter ({_qd(q1)})")
    return items + flags + ratings + quarters


def compute(path: str, session, names: dict = None) -> dict:
    """{"available", "prev_session", "method_break", "items"} for the record
    of `session` (the current run's, already appended) against the previous
    session's. Never raises: an unreadable file is "not available"."""
    try:
        recs = _by_session(path)
    except Exception as e:
        return {"available": False, "note": f"scores_detail.jsonl unreadable ({type(e).__name__})"}
    key = session.isoformat() if hasattr(session, "isoformat") else str(session)
    if key not in recs:
        return {"available": False, "note": "this session isn't recorded yet"}
    earlier = sorted(s for s in recs if s < key)
    if not earlier:
        return {"available": False, "note": "no earlier session recorded"}
    prev, cur = recs[earlier[-1]], recs[key]
    out = {"available": True, "prev_session": earlier[-1], "method_break": prev.get("method") != cur.get("method")}
    out["items"] = [] if out["method_break"] else compare(prev, cur, names)
    return out
