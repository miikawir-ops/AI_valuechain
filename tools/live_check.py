"""tools/live_check.py — checks a rendered RayDar page (the live site or a
local index.html) for the things a deploy must get right: no NaN, card
price = last completed close, strip text, earnings badges, methodology note
and heat-trail marker, card delta text, radar header, layer colours/notes.

Runs the page's own JavaScript (buildBottleneckStrip, the card delta line)
in Node rather than re-implementing it. Prices are re-fetched from yfinance
to compare with the last completed close of the page's session.

Usage (repo root): python tools/live_check.py [url-or-path]   (default: the live site)
"""
import re
import sys
import json
import datetime
import subprocess
import urllib.request

import yfinance as yf

SRC = sys.argv[1] if len(sys.argv) > 1 else "https://miikawir-ops.github.io/AI_valuechain/"
page = (urllib.request.urlopen(SRC, timeout=30).read().decode("utf-8") if SRC.startswith("http")
        else open(SRC, encoding="utf-8").read())
grab = lambda p, f=re.S: re.search(p, page, f)

layers = json.loads(grab(r"const LAYERS\s*= (\[.*?\]);\n").group(1))
heat   = {d["date"]: d for d in json.loads(grab(r"const HISTORY\s*= (\{.*?\});\n").group(1))["days"]}
fetch  = grab(r'Data: close of ([A-Za-z]+, [A-Za-z]+ \d+) · fetched ([^<·]+)').groups()
print(f"page: {SRC}\n  {fetch[0]} · fetched {fetch[1].strip()}")
print("  NaN on page:", "NaN" in page)

# Card price vs the last completed close of that session.
session = datetime.datetime.strptime(fetch[0] + " " + str(datetime.date.today().year), "%A, %B %d %Y").date()
bad = []
for l in layers:
    for t in l["tickers"]:
        h = yf.Ticker(t["sym"]).history(start=session - datetime.timedelta(days=7), end=session + datetime.timedelta(days=1))
        close = round(float(h["Close"].iloc[-1]), 2) if not h.empty else None
        if close is None or t["price"] is None or abs(t["price"] - close) > 0.011:
            bad.append((t["sym"], t["price"], close))
print(f"  card price = {session} close: {'all match' if not bad else bad}")

js = "\n".join(grab(p).group(0) for p in (r"const LAYERS\s*= \[.*?\];\n", r"const HAS_YESTERDAY=.*?;\n",
                                          r"const CC = \{.*?\n\};", r"function buildBottleneckStrip\(\) \{.*?\n\}"))
mb = grab(r"const METHOD_BREAK = (true|false);")
js += f"\nconst METHOD_BREAK = {mb.group(1) if mb else 'false'};\n"
line = grab(r"\n\s*(\$\{HAS_YESTERDAY && l\.delta_score !== null.*?first run</span>\"\})")
js += ("const strip = {innerHTML: ''};\nconst document = {getElementById: () => strip};\nbuildBottleneckStrip();\n"
       "function deltaArrow(d){return (d>0?'▲ ':d<0?'▼ ':'— ')+Math.abs(d).toFixed(1);}\n"
       "const t = s => s.replace(/<[^>]+>/g, ' ').replace(/\\s+/g, ' ').trim();\n"
       "console.log('  strip: ' + t(strip.innerHTML));\n"
       + (f"for (const l of LAYERS) console.log('  ' + l.n1 + ' delta line: ' + t(`{line.group(1)}`));\n" if line else ""))
out = subprocess.run(["node", "-e", js], capture_output=True, text=True, encoding="utf-8")
print(out.stdout.rstrip() or out.stderr[:400])

for l in layers:
    e = ", ".join(f"{x['sym']} {x['date']}" for x in l.get("earnings") or []) or "none"
    u = l.get("unmeasured")
    print(f"  {l['n1']:12} {l['color']:6} {l['score']:5.1f} | earnings ≤14d: {e}"
          + (f" | note: Acceleration not measured: {', '.join(u['syms'])} ({round(u['share']*100)}% of layer)" if u else ""))
note = grab(r"<strong>(Methodology change[^<]*)</strong>")
print("  methodology note:", note.group(1) if note else None)
marks = [d for d, v in heat.items() if v.get("method_boundary")]
print("  heat-trail marker on:", marks, "| colours that day:", heat[marks[0]]["layers"] if marks else None)
print("  'first run' possible:", "METHOD_BREAK" not in page and "first run" in page)
print("  radar header:", grab(r'id="radar-count">([^<]*)<').group(1), "| overwrite in JS:", "count.textContent" in page)
