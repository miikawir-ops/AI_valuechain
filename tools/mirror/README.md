# tools/mirror — github.io mirror fallback (not wired)

A github.io copy of all three RayDar sites, for networks that block raydar.fi.
Nothing here runs: `mirror.yml` sits outside `.github/workflows/`, and the
`raydar-mirror` repo it targets doesn't exist.

## Why it exists

On 2026-10-28 Ray's organisation's web filter blocked raydar.fi and its
subdomains as a "security risk", most likely because the domain showed
Zoner's parking page in its first days. A mirror was planned and tested
locally. The block lifted on its own by 2026-10-29 (probably
re-categorisation), so the mirror was cancelled before the repo was created.
These files are kept in case the block returns.

## What it does

`mirror.yml` runs in its own public repo `miikawir-ops/raydar-mirror`, every
30 minutes and on manual dispatch. It doesn't touch the three source repos.
1. `mirror.py build site` fetches the live sites over HTTPS and writes them
   to `site/`: raydar.fi at `/`, datacenter.raydar.fi at `/datacenter/`,
   vice.raydar.fi at `/vice/`.
   - It discovers assets from HTML attributes, CSS `url()`/`@import`, and
     path strings in JS. JS paths are resolved against the pages that load
     the script.
   - DataCenter's `business-models/` is also seeded from that repo's tree,
     because chrome.js builds those page links at runtime.
   - A required file that doesn't return 200 fails the build, so a partial
     mirror is never published.
2. **Links:** raydar.fi, www.raydar.fi, datacenter.raydar.fi and
   vice.raydar.fi URLs (http and https) in `.html` and `.js` are rewritten to
   the matching mirror paths. TP_special_agent links stay as they are.
3. **Marking:**
   - each page gets `<meta name="robots" content="noindex">` and a plain-text
     note with its own site's domain;
   - canonical tags are removed: noindex alone is enough, and Google advises
     against mixing the two.
4. **Publish:** only when `site/` changed. Deploy first, then verify, then
   commit, so the repo only records states that were published and verified.
5. **Verify:**
   - `mirror.py verify site` checks that every file returns 200 on the mirror,
     that no file contains a raydar.fi URL, and that every page has noindex
     and the note;
   - `render_check.py` loads every page in headless Chromium and fails on any
     failed request or any request to a raydar.fi domain.

Tested on 2026-10-28 against the live sites, served locally under
`/raydar-mirror/`:
- 34 files, matching the three curated deploys;
- verify passed;
- a planted vice.raydar.fi link failed verify, as it should;
- 18 pages rendered with no failed requests.

## Setup if the block returns

1. **Ray:** create a public, empty repo `miikawir-ops/raydar-mirror`. Then
   Settings → Pages → Source: **GitHub Actions**, with no custom domain.
2. **In that repo:** `mirror.py`, `render_check.py` and `requirements.txt` go
   at the root, and `mirror.yml` goes in `.github/workflows/`.
   - First re-run the local test: `python mirror.py build site`, serve the
     parent folder of `site/` with `site/` named `raydar-mirror/`, then run
     `verify` and `render_check.py` against it.
   - The sites may have gained files or root-relative paths since 2026-10-28.
     `build` fails on a root-relative reference rather than guessing.
3. **Ray:** dispatch "Mirror RayDar sites" once, then open the three mirror
   URLs from the blocked network.
4. **Never** create a `miikawir-ops.github.io` account-level repo with a
   custom domain while the mirror exists. GitHub would then redirect every
   project site without its own domain to it, the mirror included. The rule
   is in each repo's CLAUDE.md.

## Removal

1. Disable the workflow.
2. Commit the output of `mirror.py stubs site`, which writes redirect stubs
   to the main domains, and deploy it.
3. Weeks later, unpublish Pages or archive the repo.

The main sites are never touched.
