"""tools/og_image.py — renders og-image.png (1200x627, the link-preview image
for LinkedIn and others), favicon.svg and its PNG fallbacks (favicon.png
32x32, apple-touch-icon.png 180x180) in the dashboard hero's style.
Static by design: no scores, colours, layer states or company names, which
would go stale in a cached preview (Ray, 2026-10-30).

Needs Playwright with Chromium. Usage (repo root): python tools/og_image.py
"""
import base64
import pathlib
from playwright.sync_api import sync_playwright

ROOT = pathlib.Path(__file__).resolve().parent.parent

FAVICON = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">
<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">
<stop offset="0" stop-color="#08081A"/><stop offset=".5" stop-color="#0C1A3A"/><stop offset="1" stop-color="#1A0830"/></linearGradient></defs>
<rect width="64" height="64" rx="14" fill="url(#g)"/>
<path d="M14 50 A36 36 0 0 1 50 14" fill="none" stroke="#534AB7" stroke-width="3" opacity=".7"/>
<path d="M14 50 A24 24 0 0 1 38 26" fill="none" stroke="#85B7EB" stroke-width="3"/>
<path d="M14 50 A12 12 0 0 1 26 38" fill="none" stroke="#C8D4E8" stroke-width="3"/>
<circle cx="14" cy="50" r="4" fill="#85B7EB"/>
<circle cx="44" cy="22" r="5" fill="#E24B4A"/>
</svg>
"""

BARS = "".join(
    f'<div style="width:9px;height:{h}px;border-radius:2px;background:{c};opacity:{o}"></div>'
    for h, c, o in [(10, "#534AB7", .5), (18, "#534AB7", .6), (14, "#85B7EB", .6), (26, "#85B7EB", .7), (20, "#AFA9EC", .7),
                    (34, "#85B7EB", .8), (46, "#85B7EB", .9), (30, "#E24B4A", .85), (52, "#85B7EB", .95), (38, "#AFA9EC", .8),
                    (24, "#85B7EB", .7), (42, "#534AB7", .7), (16, "#85B7EB", .6), (28, "#AFA9EC", .6), (12, "#534AB7", .5)])

PAGE = f"""<!DOCTYPE html><html><head><meta charset="UTF-8"><style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{width:1200px;height:627px;overflow:hidden;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
  background:linear-gradient(135deg,#08081A 0%,#0C1A3A 30%,#1A0830 60%,#0A0818 100%);position:relative}}
.laser{{position:absolute;top:0;left:0;right:0;height:6px;
  background:linear-gradient(90deg,transparent 0%,transparent 10%,#534AB7 28%,#85B7EB 45%,#ffffff 50%,#85B7EB 55%,#534AB7 72%,transparent 90%,transparent 100%)}}
.glow{{position:absolute;top:0;left:10%;right:10%;height:120px;
  background:radial-gradient(ellipse at 50% 0%,rgba(133,183,235,0.40) 0%,rgba(83,74,183,0.18) 40%,transparent 75%)}}
.accent{{position:absolute;bottom:-160px;right:-120px;width:620px;height:620px;
  background:radial-gradient(circle,rgba(83,74,183,0.28) 0%,transparent 70%)}}
.arcs{{position:absolute;right:70px;top:120px;width:380px;height:380px}}
.main{{position:absolute;left:90px;top:170px}}
.title{{font-size:150px;font-weight:500;letter-spacing:-5px;color:#fff;line-height:1}}
.title span{{color:#85B7EB;font-weight:300}}
.tag{{margin-top:22px;font-size:40px;letter-spacing:0.08em;
  background:linear-gradient(90deg,#85B7EB,#AFA9EC,#85B7EB);-webkit-background-clip:text;-webkit-text-fill-color:transparent}}
.sub{{margin-top:26px;font-size:24px;color:#8A9AB8;max-width:640px;line-height:1.4}}
.dot{{display:inline-block;width:16px;height:16px;border-radius:50%;background:#E24B4A;margin-left:14px;vertical-align:top;margin-top:24px}}
.foot{{position:absolute;left:90px;bottom:56px;font-size:26px;color:#C8D4E8;font-weight:500;letter-spacing:.02em}}
.bars{{position:absolute;right:90px;bottom:56px;display:flex;align-items:flex-end;gap:6px}}
</style></head><body>
<div class="laser"></div><div class="glow"></div><div class="accent"></div>
<svg class="arcs" viewBox="0 0 380 380">
  <path d="M30 350 A320 320 0 0 1 350 30" fill="none" stroke="#534AB7" stroke-width="3" opacity=".45"/>
  <path d="M30 350 A240 240 0 0 1 270 110" fill="none" stroke="#85B7EB" stroke-width="3" opacity=".55"/>
  <path d="M30 350 A160 160 0 0 1 190 190" fill="none" stroke="#AFA9EC" stroke-width="3" opacity=".6"/>
  <path d="M30 350 A80 80 0 0 1 110 270" fill="none" stroke="#C8D4E8" stroke-width="3" opacity=".7"/>
  <line x1="30" y1="350" x2="300" y2="80" stroke="#85B7EB" stroke-width="2" opacity=".35"/>
  <circle cx="30" cy="350" r="7" fill="#85B7EB"/>
  <circle cx="232" cy="148" r="10" fill="#E24B4A"/>
</svg>
<div class="main">
  <div class="title">Ray<span>Dar</span><i class="dot"></i></div>
  <div class="tag">AI value chain intelligence</div>
  <div class="sub">Where revenue acceleration meets supply constraints, from power and chips to cloud and software.</div>
</div>
<div class="foot">raydar.fi</div>
<div class="bars">{BARS}</div>
</body></html>"""

(ROOT / "favicon.svg").write_text(FAVICON, encoding="utf-8")
with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width": 1200, "height": 627})
    pg.set_content(PAGE, wait_until="load")
    pg.screenshot(path=str(ROOT / "og-image.png"), clip={"x": 0, "y": 0, "width": 1200, "height": 627})
    # PNG fallbacks of the same icon: favicon.png for browsers without SVG
    # favicons, apple-touch-icon.png for iOS home screens (no transparency).
    for name, size in (("favicon.png", 32), ("apple-touch-icon.png", 180)):
        pg = b.new_page(viewport={"width": size, "height": size})
        pg.set_content(f'<html><body style="margin:0;background:#08081A">'
                       f'<img src="data:image/svg+xml;base64,{base64.b64encode(FAVICON.encode()).decode()}" '
                       f'width="{size}" height="{size}" style="display:block"></body></html>', wait_until="load")
        pg.screenshot(path=str(ROOT / name), clip={"x": 0, "y": 0, "width": size, "height": size})
    b.close()
print("wrote og-image.png, favicon.svg, favicon.png and apple-touch-icon.png")
