"""
test_publish_site.py — tools/publish_site.py, the only writer to the public
site repo in the split-repo setup (Ray, 2026-10-25). Uses a local bare repo
as the "site repo", so no network or keys are involved.

  - no SITE_DEPLOY_KEY → skips cleanly (exit 0), touches nothing;
  - code, docs, data or workflow files in the content → refused (exit 2);
  - first publish into an empty repo works and adds .nojekyll;
  - a removed page is deleted, but CNAME and .nojekyll are never deleted;
  - unchanged content → no new commit;
  - a failed push → exit 1 (the workflow run fails loudly).

No pytest in this environment — plain script. Run: python test_publish_site.py
"""

import os
import sys
import subprocess
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "tools"))
import publish_site


def git(*args, cwd=None):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()


def files_in(bare: Path) -> set:
    r = subprocess.run(["git", "--git-dir", str(bare), "ls-tree", "-r", "--name-only", "main"],
                       capture_output=True, text=True)
    return set(r.stdout.split()) if r.returncode == 0 else set()      # no main yet → empty


def page_dir(root: Path, **files) -> Path:
    for rel, text in files.items():
        p = root / rel.replace("__", "/")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return root


def run(*argv, key=""):
    env_before = os.environ.get("SITE_DEPLOY_KEY")
    os.environ["SITE_DEPLOY_KEY"] = key
    try:
        return publish_site.main(list(argv))
    except SystemExit as e:
        return e.code if isinstance(e.code, int) else 1
    finally:
        if env_before is None:
            os.environ.pop("SITE_DEPLOY_KEY", None)
        else:
            os.environ["SITE_DEPLOY_KEY"] = env_before


def test_publish_site():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        bare = tmp / "site.git"
        git("init", "-q", "--bare", "-b", "main", str(bare))
        src = page_dir(tmp / "src", **{"index.html": "<h1>v1</h1>", "raydar_vice.html": "brief",
                                        "business-models__index.html": "hub",
                                        "business-models__assets__fonts__OFL.txt": "licence"})

        assert run(str(src), "o/site") == 0 and files_in(bare) == set(), "no key: must skip and push nothing"
        print("PASS: no SITE_DEPLOY_KEY → skipped, nothing pushed")

        for bad in ("main.py", "PLAN.md", "scores_detail.jsonl", "growth_history.json",
                    ".github__workflows__daily.yml", "sub__notes.md", ".env"):
            leak = page_dir(tmp / f"leak_{bad.replace('.', '_')}", **{"index.html": "x", bad: "secret"})
            assert run(str(leak), "o/site", "--remote", str(bare)) == 2, f"{bad} was not refused"
        assert files_in(bare) == set()
        assert run(str(page_dir(tmp / "readme", **{"index.html": "x", "README.md": "ok"})), "o/site", "--remote", str(bare)) == 0
        git("--git-dir", str(bare), "update-ref", "-d", "refs/heads/main")      # reset the bare repo
        print("PASS: .py/.md/.json/.jsonl/workflow/.env refused (exit 2); a root README.md is allowed")

        assert run(str(src), "o/site", "--remote", str(bare)) == 0
        assert files_in(bare) == {"index.html", "raydar_vice.html", "business-models/index.html",
                                  "business-models/assets/fonts/OFL.txt", ".nojekyll"}, files_in(bare)
        print("PASS: first publish into an empty repo, with .nojekyll added")

        # GitHub writes CNAME into the site repo when a custom domain is set.
        clone = tmp / "clone"
        git("clone", "-q", str(bare), str(clone))
        (clone / "CNAME").write_text("vice.raydar.fi\n")
        git("add", "CNAME", cwd=clone)
        git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "custom domain", cwd=clone)
        git("push", "-q", "origin", "main", cwd=clone)
        (src / "raydar_vice.html").unlink()
        (src / "index.html").write_text("<h1>v2</h1>")
        assert run(str(src), "o/site", "--remote", str(bare)) == 0
        assert files_in(bare) == {"index.html", "business-models/index.html", "business-models/assets/fonts/OFL.txt",
                                  ".nojekyll", "CNAME"}, files_in(bare)
        assert git("--git-dir", str(bare), "show", "main:CNAME") == "vice.raydar.fi"
        print("PASS: removed page deleted; CNAME and .nojekyll kept")

        head = git("--git-dir", str(bare), "rev-parse", "main")
        assert run(str(src), "o/site", "--remote", str(bare)) == 0
        assert git("--git-dir", str(bare), "rev-parse", "main") == head, "unchanged content made a commit"
        print("PASS: unchanged content → no new commit")

        assert run(str(src), "o/site", "--remote", str(tmp / "no-such-repo.git")) == 1
        hook = bare / "hooks" / "pre-receive"                      # the site repo rejects pushes
        hook.write_text("#!/bin/sh\necho rejected >&2\nexit 1\n", newline="\n")
        os.chmod(hook, 0o755)
        (src / "index.html").write_text("<h1>v3</h1>")
        assert run(str(src), "o/site", "--remote", str(bare)) == 1, "a rejected push didn't fail"
        assert git("--git-dir", str(bare), "rev-parse", "main") == head, "rejected push changed the site repo"
        print("PASS: clone failure and rejected push → exit 1 (fails loudly), site repo untouched")


if __name__ == "__main__":
    test_publish_site()
    print("\nAll publish-site tests passed.")
