"""
tools/publish_site.py — push a curated site folder to the public site repo.

The RayDar sites use a split pattern on free GitHub Pages (Ray, 2026-10-25):
a private source repo (<name>-src) builds the pages, and a public repo with
the old name holds only the published pages, served by Pages from main/root.
This script is the only thing that writes to the public repo.

    python tools/publish_site.py SRC_DIR OWNER/REPO [--remote URL]

  - In CI it authenticates with the SSH deploy key in $SITE_DEPLOY_KEY (write
    access to that one site repo). Without the secret it skips cleanly and
    exits 0, so the step is harmless before the cutover.
  - --remote URL (e.g. https://github.com/OWNER/REPO.git, or a local path in
    tests) pushes with the caller's own git credentials instead — used for
    the first push at cutover, always from a temporary folder.
  - Guard: refuses (exit 2) if the content contains code, docs, data or
    workflow files (.py, .md except a root README.md, .json/.jsonl, .yml,
    .github/, ...), before and after the sync. Code must never reach the
    public repo.
  - Sync: the site repo's tree becomes exactly SRC_DIR, except CNAME (written
    by GitHub when a custom domain is set) and .nojekyll, which are never
    deleted; .nojekyll is always present.
  - Pushes only when the content changed. Any clone or push failure exits 1,
    so the workflow run fails loudly instead of leaving a stale site silently.

Identical copies live in RayDar-DataCenter and RayDar-Vice; this repo's
test_publish_site.py is the test.
"""

import os
import sys
import shutil
import argparse
import datetime
import subprocess
import tempfile
from pathlib import Path

PRESERVE = {"CNAME", ".nojekyll"}
FORBIDDEN_SUFFIXES = {".py", ".pyc", ".md", ".json", ".jsonl", ".yml", ".yaml", ".env", ".ipynb",
                      ".sh", ".ps1", ".toml", ".cfg", ".ini", ".lock", ".db", ".sqlite", ".pem", ".key"}
FORBIDDEN_PARTS = {".github", ".git", "__pycache__", ".venv", "tools", "tests"}
# GitHub's SSH host keys, from https://api.github.com/meta ("ssh_keys"), fetched
# 2026-10-25. Pinned so the push can't be intercepted; update if GitHub rotates.
GITHUB_HOST_KEYS = """\
github.com ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl
github.com ecdsa-sha2-nistp256 AAAAE2VjZHNhLXNoYTItbmlzdHAyNTYAAAAIbmlzdHAyNTYAAABBBEmKSENjQEezOmxkZMy7opKgwFB9nkt5YRrYMjNuG5N87uRgg6CLrbo5wAdT/y6v0mKV0U2w0WZ2YB/++Tpockg=
github.com ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABgQCj7ndNxQowgcQnjshcLrqPEiiphnt+VTTvDP6mHBL9j1aNUkY4Ue1gvwnGLVlOhGeYrnZaMgRK6+PKCUXaDbC7qtbW8gIkhL7aGCsOr/C56SJMy/BCZfxd1nWzAOxSDPgVsmerOBYfNqltV9/hWCqBywINIR+5dIg6JTJ72pcEpEjcYgXkE2YEFXV1JHnsKgbLWNlhScqb2UmyRkQyytRLtL+38TGxkxCflmO+5Z8CSSNY7GidjMIZ7Q4zMjA2n1nGrlTDkzwDCsw+wqFPGQA179cnfGWOWRVruj16z6XyvxvjJwbz0wQZ75XK5tKSb7FNyeIEs4TT4jk+S4dhPeAUC5y+bDYirYgM4GC7uEnztnZyaVWQ7B381AK4Qdrwt51ZqExKbQpTUNn+EjqoTwvqNj4kqx5QUCI0ThS/YkOxJCXmPUWZbhjpCg56i+2aB6CmK2JGhn57K5mj0MNdBXA4/WnwH6XoPWJzK5Nyu2zB3nAZp+S5hpQs+p1vN1/wsjk=
"""


def forbidden_files(root: Path) -> list:
    """Paths under root (outside .git) that must never be published."""
    bad = []
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root)
        if rel.parts and rel.parts[0] == ".git":
            continue
        if not p.is_file():
            continue
        name = p.name
        if any(part in FORBIDDEN_PARTS for part in rel.parts[:-1]) or name in FORBIDDEN_PARTS:
            bad.append(str(rel))
        elif name.startswith(".env"):
            bad.append(str(rel))
        elif p.suffix.lower() in FORBIDDEN_SUFFIXES and not (str(rel) == "README.md"):
            bad.append(str(rel))
    return bad


def _run(cmd, cwd=None, env=None, check=True):
    r = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True)
    if check and r.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd)} failed ({r.returncode}): {r.stderr.strip() or r.stdout.strip()}")
    return r


def _sync(src: Path, dst: Path) -> None:
    """Make dst's tree (outside .git and PRESERVE) exactly src's."""
    for p in list(dst.iterdir()):
        if p.name == ".git" or p.name in PRESERVE:
            continue
        shutil.rmtree(p) if p.is_dir() else p.unlink()
    for p in src.rglob("*"):
        rel = p.relative_to(src)
        if rel.parts[0] in PRESERVE and len(rel.parts) == 1:
            continue                                   # never overwritten from the source either
        target = dst / rel
        if p.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, target)
    (dst / ".nojekyll").touch(exist_ok=True)


def publish(src_dir: str, repo: str, remote: str = None, key: str = None) -> str:
    """Returns "skipped", "unchanged" or "published"; raises on any failure."""
    src = Path(src_dir).resolve()
    if not (src / "index.html").is_file():
        raise SystemExit(f"publish_site: {src} has no index.html - refusing to publish an incomplete site")
    bad = forbidden_files(src)
    if bad:
        print("publish_site: REFUSED - source contains files that must never be published:", *bad, sep="\n  ")
        raise SystemExit(2)

    env = dict(os.environ)
    with tempfile.TemporaryDirectory(prefix="site-publish-") as tmp:
        tmp = Path(tmp)
        if remote is None:
            if not key:
                print("publish_site: SITE_DEPLOY_KEY not set - publish skipped (site repo not in use yet)")
                return "skipped"
            key_file, known = tmp / "deploy_key", tmp / "known_hosts"
            key_file.write_text(key.replace("\r", "").strip() + "\n")
            os.chmod(key_file, 0o600)
            known.write_text(GITHUB_HOST_KEYS)
            env["GIT_SSH_COMMAND"] = (f'ssh -i "{key_file}" -o IdentitiesOnly=yes '
                                      f'-o UserKnownHostsFile="{known}" -o StrictHostKeyChecking=yes')
            remote = f"git@github.com:{repo}.git"
        work = tmp / "site"
        r = _run(["git", "clone", "--depth", "1", remote, str(work)], env=env, check=False)
        if r.returncode != 0:
            raise RuntimeError(f"clone of {repo} failed: {r.stderr.strip()}")
        if _run(["git", "rev-parse", "--verify", "HEAD"], cwd=work, env=env, check=False).returncode != 0:
            _run(["git", "checkout", "-b", "main"], cwd=work, env=env)   # empty new repo
        _sync(src, work)
        bad = forbidden_files(work)
        if bad:
            print(f"publish_site: REFUSED - the site repo would contain:", *bad, sep="\n  ")
            raise SystemExit(2)
        _run(["git", "add", "-A"], cwd=work, env=env)
        if _run(["git", "diff", "--cached", "--quiet"], cwd=work, env=env, check=False).returncode == 0:
            print(f"publish_site: {repo} unchanged - nothing pushed")
            return "unchanged"
        source = os.getenv("GITHUB_SHA") or "local"
        when = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        _run(["git", "-c", "user.name=github-actions[bot]",
              "-c", "user.email=41898282+github-actions[bot]@users.noreply.github.com",
              "commit", "-q", "-m", f"site: publish from {source[:7]} ({when})"], cwd=work, env=env)
        r = _run(["git", "push", "origin", "HEAD:main"], cwd=work, env=env, check=False)
        if r.returncode != 0:
            raise RuntimeError(f"push to {repo} failed: {r.stderr.strip()}")
        print(f"publish_site: published to {repo} ({source[:7]}, {when})")
        return "published"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("src_dir")
    ap.add_argument("repo", help="OWNER/REPO of the public site repo")
    ap.add_argument("--remote", help="push with your own credentials to this URL instead of the deploy key")
    a = ap.parse_args(argv)
    try:
        publish(a.src_dir, a.repo, remote=a.remote, key=os.getenv("SITE_DEPLOY_KEY", ""))
    except RuntimeError as e:
        print(f"publish_site: FAILED - {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
