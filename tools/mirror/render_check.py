"""render_check.py — load every mirrored page in headless Chromium and fail
on any request that doesn't return 2xx/3xx on the mirror, or any request to a
RayDar domain. Catches references built at runtime that a static scan can't
see. Run: python render_check.py site https://miikawir-ops.github.io/raydar-mirror/"""
import os, re, sys
from playwright.sync_api import sync_playwright
out, base = sys.argv[1], sys.argv[2]
RAYDAR = re.compile(r"^https?://(www\.|datacenter\.|vice\.)?raydar\.fi(?![\w.-])", re.I)
pages = sorted(os.path.relpath(os.path.join(r, n), out).replace(os.sep, "/")
               for r, _, ns in os.walk(out) for n in ns if n.endswith(".html"))
bad = []
with sync_playwright() as p:
    b = p.chromium.launch()
    for rel in pages:
        ctx = b.new_context(); pg = ctx.new_page()
        pg.on("request", lambda r, rel=rel: bad.append(f"{rel}: request to {r.url}") if RAYDAR.match(r.url) else None)
        pg.on("requestfailed", lambda r, rel=rel: bad.append(f"{rel}: failed {r.url} ({r.failure})") if r.url.startswith(base) else None)
        pg.on("response", lambda r, rel=rel: bad.append(f"{rel}: {r.status} {r.url}") if r.url.startswith(base) and r.status >= 400 else None)
        print(rel, flush=True); pg.goto(base + rel, wait_until="load", timeout=30000)
        pg.wait_for_timeout(1500)
        ctx.close()
    b.close()
for x in bad: print("FAIL", x)
print(f"rendered {len(pages)} pages, {len(bad)} problem(s)")
sys.exit(1 if bad else 0)
