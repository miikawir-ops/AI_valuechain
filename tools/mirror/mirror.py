"""
mirror.py — github.io mirror of the three RayDar sites, for networks that
block raydar.fi (Ray, 2026-10-28).

build OUT     Fetch the live sites over HTTPS, discover every page and asset
              they reference (HTML attributes, CSS url()/@import, path
              strings in JS), rewrite links to the RayDar domains to the
              mirror's paths, mark each page, and write the tree to OUT.
              Any fetch failure fails the build: never a partial mirror.
verify OUT    Every file in OUT returns 200 on the mirror; no file holds a URL
              on a RayDar domain; every page has noindex and the note.
stubs OUT     Removal: OUT gets a redirect stub to the main domain for every
              page in the current mirror (run after the schedule is off).

Standard library only. Run: python mirror.py build site
"""

import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser

MIRROR = "https://miikawir-ops.github.io/raydar-mirror/"
# Main host → mirror subfolder. www is served by the apex.
HOSTS = {"raydar.fi": "", "www.raydar.fi": "", "datacenter.raydar.fi": "datacenter/", "vice.raydar.fi": "vice/"}
SITES = {   # host → seed pages; everything else is discovered from these
    "raydar.fi": ["index.html", "ai_chain_report.html"],
    "datacenter.raydar.fi": ["index.html", "ecosystem.html", "business-models/index.html"],
    "vice.raydar.fi": ["index.html", "raydar_vice.html", "quantum.html"],
}
# Folders published whole whose pages are linked only by JS at runtime
# (chrome.js builds "<slug>.html"), so a crawl can't find them: seeded from
# the public source repo's tree. Non-strict: a file pushed but not yet
# deployed (DataCenter has no push trigger) is skipped, not a failure.
TREE_SEEDS = [("datacenter.raydar.fi", "miikawir-ops/RayDar-DataCenter", "business-models/")]
TEXT = (".html", ".js", ".css")
ASSET_EXT = r"html|js|css|webp|png|jpe?g|svg|gif|ico|woff2?|ttf|otf"
RAYDAR_URL = re.compile(r"(?:https?:)?//(?:www\.)?((?:datacenter\.|vice\.)?raydar\.fi)(?![\w.-])/?", re.I)
JS_PATH = re.compile(r"""["'`]([^"'`\s{}$<>]+?\.(?:%s))(?:[?#][^"'`\s]*)?["'`]""" % ASSET_EXT, re.I)
CSS_URL = re.compile(r"""url\(\s*["']?([^"')]+?)["']?\s*\)|@import\s+["']([^"']+)["']""", re.I)
CANONICAL = re.compile(r"<link\b[^>]*\brel=[\"']?canonical[\"']?[^>]*>\s*", re.I)


def note(host):
    return (f'<div class="raydar-mirror-note" style="font:12px/1.4 system-ui,sans-serif;background:#FFF4D6;'
            f'color:#1A1A1A;padding:6px 12px;text-align:center;border-bottom:1px solid #E8D9A8">'
            f'Mirror for networks that block {host} — main site: {host}</div>')


class Refs(HTMLParser):
    """href/src/srcset/poster attributes, meta refresh targets, inline
    <style> and <script> bodies (scanned like CSS and JS files)."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.refs, self.inline, self._in = [], [], None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        for k in ("href", "src", "poster", "data-src"):
            if a.get(k):
                self.refs.append(a[k])
        for k in ("srcset", "imagesrcset"):
            if a.get(k):
                self.refs += [p.strip().split()[0] for p in a[k].split(",") if p.strip()]
        if tag == "meta" and (a.get("http-equiv") or "").lower() == "refresh":
            m = re.search(r"url\s*=\s*['\"]?([^'\";]+)", a.get("content") or "", re.I)
            if m:
                self.refs.append(m.group(1))
        self._in = tag if tag in ("style", "script") else None

    def handle_data(self, data):
        if self._in:
            self.inline.append((self._in, data))

    def handle_endtag(self, tag):
        self._in = None


def refs_in(path, text):
    """Candidate references in one file: (ref, strict). Strict refs (HTML
    attributes, CSS) must exist; JS path strings are heuristic."""
    out = []
    def css(t):
        out.extend((m.group(1) or m.group(2), True) for m in CSS_URL.finditer(t))
    def js(t):
        out.extend((m.group(1), False) for m in JS_PATH.finditer(t))
    if path.endswith(".html"):
        p = Refs(); p.feed(text)
        out += [(r, True) for r in p.refs]
        for kind, body in p.inline:
            (css if kind == "style" else js)(body)
    elif path.endswith(".css"):
        css(text)
    elif path.endswith(".js"):
        js(text)
    return out


def resolve(host, path, ref):
    """A reference → (host, site path) when it's on a RayDar site, else None."""
    ref = ref.strip()
    if not ref or ref.startswith(("#", "data:", "mailto:", "javascript:", "tel:")):
        return None
    if ref.startswith("/") and not ref.startswith("//"):
        # Root-relative breaks in a subfolder; none exist today (2026-10-28).
        raise SystemExit(f"root-relative reference {ref!r} in {host}/{path}: mirror can't place it")
    u = urllib.parse.urlsplit(urllib.parse.urljoin(f"https://{host}/{path}", ref))
    h = (u.hostname or "").lower()
    if h not in HOSTS:
        return None
    h = "raydar.fi" if h == "www.raydar.fi" else h
    p = urllib.parse.unquote(u.path.lstrip("/"))
    return h, (p + "index.html" if p == "" or p.endswith("/") else p)


def tree_seeds():
    import json
    seeds = []
    for host, repo, folder in TREE_SEEDS:
        req = urllib.request.Request(f"https://api.github.com/repos/{repo}/git/trees/main?recursive=1",
                                     headers={"User-Agent": "raydar-mirror", "Accept": "application/vnd.github+json"})
        if os.environ.get("GITHUB_TOKEN"):
            req.add_header("Authorization", "Bearer " + os.environ["GITHUB_TOKEN"])
        try:
            tree = json.load(urllib.request.urlopen(req, timeout=30))["tree"]
        except Exception as e:
            raise SystemExit(f"{repo} tree unavailable ({e}): no partial mirror")
        seeds += [(host, t["path"], False, f"{repo} tree") for t in tree
                  if t["type"] == "blob" and t["path"].startswith(folder)]
    return seeds


def fetch(url, tries=3):
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "raydar-mirror"}), timeout=30) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            if e.code < 500:
                return e.code, b""
        except (urllib.error.URLError, TimeoutError):
            pass
        time.sleep(2 * (i + 1))
    return None, b""


def rewrite(text):
    return RAYDAR_URL.sub(lambda m: MIRROR + HOSTS[m.group(1).lower()], text)


def mark(host, html):
    html = CANONICAL.sub("", html)
    head = re.search(r"<head\b[^>]*>", html, re.I)
    body = re.search(r"<body\b[^>]*>", html, re.I)
    if not head or not body:
        raise SystemExit(f"{host}: page without <head> or <body>, can't mark it")
    html = html[:head.end()] + '\n<meta name="robots" content="noindex">' + html[head.end():]
    body = re.search(r"<body\b[^>]*>", html, re.I)
    return html[:body.end()] + "\n" + note(host) + html[body.end():]


def build(out):
    queue = [(h, p, True, "seed") for h, ps in SITES.items() for p in ps] + tree_seeds()
    seen, files, skipped = set(), {}, []
    loaders = {}    # JS file → pages that load it: its path strings are relative to those pages
    while queue:
        host, path, strict, src = queue.pop(0)
        if (host, path) in seen:
            continue
        seen.add((host, path))
        status, body = fetch(f"https://{host}/{path}")
        if status != 200:
            if strict:
                raise SystemExit(f"{host}/{path} returned {status} (referenced from {src}): no partial mirror")
            skipped.append(f"{host}/{path} ({status}, JS string in {src})")
            continue
        files[(host, path)] = body
        if path.endswith(TEXT):
            bases = sorted(loaders.get((host, path), {path})) if path.endswith(".js") else [path]
            for ref, s in refs_in(path, body.decode("utf-8")):
                for base in bases:
                    r = resolve(host, base, ref)
                    if r and path.endswith(".html") and r[1].endswith(".js"):
                        loaders.setdefault(r, set()).add(path)
                    if r and r not in seen:
                        queue.append((*r, s, f"{host}/{path}"))
    for (host, path), body in sorted(files.items()):
        if path.endswith(TEXT):
            text = rewrite(body.decode("utf-8"))
            body = (mark(host, text) if path.endswith(".html") else text).encode("utf-8")
        dest = os.path.join(out, HOSTS[host], *path.split("/"))
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        open(dest, "wb").write(body)
    open(os.path.join(out, ".nojekyll"), "w").close()
    print(f"built {len(files)} files: " + ", ".join(f"{h} {sum(1 for k in files if k[0] == h)}" for h in SITES))
    for s in skipped:
        print(f"  not mirrored: {s}")


def site_files(out):
    for root, _, names in os.walk(out):
        for n in names:
            if n != ".nojekyll":
                yield os.path.relpath(os.path.join(root, n), out).replace(os.sep, "/")


def verify(out, base=MIRROR):
    bad = []
    for rel in sorted(site_files(out)):
        if rel.endswith(TEXT):
            text = open(os.path.join(out, rel), encoding="utf-8").read()
            if RAYDAR_URL.search(text):
                bad.append(f"{rel}: URL on a RayDar domain: {RAYDAR_URL.search(text).group(0)}")
            if rel.endswith(".html") and ('name="robots" content="noindex"' not in text or "raydar-mirror-note" not in text):
                bad.append(f"{rel}: missing noindex or the mirror note")
            if rel.endswith(".html") and CANONICAL.search(text):
                bad.append(f"{rel}: canonical tag left in")
        url = base + urllib.parse.quote(rel)
        status, _ = fetch(url)
        if status != 200:
            bad.append(f"{rel}: {url} returned {status}")
    for b in bad:
        print("FAIL", b)
    if bad:
        raise SystemExit(f"{len(bad)} problem(s)")
    print(f"verified {len(list(site_files(out)))} files on {base}")


def stubs(out):
    for rel in list(site_files(out)):
        if not rel.endswith(".html"):
            continue
        sub, _, path = rel.partition("/") if rel.split("/")[0] in ("datacenter", "vice") else ("", "", rel)
        host = {"": "raydar.fi", "datacenter": "datacenter.raydar.fi", "vice": "vice.raydar.fi"}[sub]
        target = f"https://{host}/" + ("" if path == "index.html" else path)
        open(os.path.join(out, rel), "w", encoding="utf-8").write(
            f'<!DOCTYPE html>\n<html lang="en"><head><meta charset="UTF-8">\n<meta name="robots" content="noindex">\n'
            f'<meta http-equiv="refresh" content="0; url={target}"><title>RayDar mirror closed</title></head>\n'
            f'<body><p>This mirror is closed. The site is at {host}.</p></body></html>\n')
    print("stubs written")


if __name__ == "__main__":
    cmd, out = sys.argv[1], sys.argv[2]
    {"build": build, "verify": verify, "stubs": stubs}[cmd](out)
