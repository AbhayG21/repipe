"""git discovery: turn the working tree's origin remote into provider inputs."""

import os
import re
import subprocess
from typing import Optional

from .errors import RepipeError, EXIT_CONFIG


def run_git(args, cwd=None, timeout=None, env=None) -> Optional[str]:
    """Run a git command; return stdout stripped, or None on non-zero exit.

    `timeout` (seconds) bounds commands that touch the network (e.g. ls-remote);
    a timeout is treated like any other failure and returns None. `env` merges
    over the current environment (used to disable git's credential prompt).
    """
    run_env = {**os.environ, **env} if env else None
    try:
        out = subprocess.run(
            ["git", *args], cwd=cwd, capture_output=True, text=True,
            timeout=timeout, env=run_env,
        )
    except FileNotFoundError:
        raise RepipeError("git was not found on PATH.", EXIT_CONFIG)
    except subprocess.TimeoutExpired:
        return None
    if out.returncode != 0:
        return None
    return out.stdout.strip()


def parse_remote(url: str):
    """Parse a git remote URL into (host, workspace, repo).

    Handles the three common forms:
      git@host:workspace/repo.git
      ssh://git@host/workspace/repo.git
      https://host/workspace/repo.git   (optionally with user@)
    """
    host = path = None
    m = re.match(r"^[\w.+-]+@([^:]+):(.+?)(?:\.git)?/?$", url)
    if m:
        host, path = m.group(1), m.group(2)
    if host is None:
        m = re.match(r"^ssh://[\w.+-]+@([^/]+)/(.+?)(?:\.git)?/?$", url)
        if m:
            host, path = m.group(1), m.group(2)
    if host is None:
        m = re.match(r"^https?://(?:[^@/]+@)?([^/]+)/(.+?)(?:\.git)?/?$", url)
        if m:
            host, path = m.group(1), m.group(2)
    if host is None:
        raise RepipeError(f"could not parse git remote URL: {url}", EXIT_CONFIG)

    parts = [p for p in path.split("/") if p]
    if len(parts) < 2:
        raise RepipeError(
            f"git remote '{url}' has no workspace/repo path.", EXIT_CONFIG
        )
    # Bitbucket: workspace/repo. (Nested groups, e.g. GitLab, handled per-adapter later.)
    workspace = parts[0]
    repo = parts[-1]
    return host, workspace, repo


def list_branches(cwd=".", prefix=None):
    """Branch short-names (local + origin/…), newest first by committer date,
    de-duplicated. Optionally filtered to those starting with `prefix`.
    """
    out = run_git(
        [
            "for-each-ref",
            "--sort=-committerdate",
            "--format=%(refname:short)",
            "refs/heads",
            "refs/remotes",
        ],
        cwd,
    )
    names = []
    for line in (out or "").splitlines():
        n = line.strip()
        if n.startswith("origin/"):
            n = n[len("origin/"):]
        if not n or n == "HEAD" or n in names:
            continue
        names.append(n)
    if prefix:
        names = [n for n in names if n.startswith(prefix)]
    return names


def branch_candidates(cwd, current, prefix, limit=4):
    """Ordered branch suggestions for a prompt: newest release-prefixed branch
    first, the current branch always offered, then a few more recent matches.
    """
    matching = list_branches(cwd, prefix)
    candidates = []
    if matching:
        candidates.append(matching[0])
    if current and current not in candidates:
        candidates.append(current)
    for b in matching[1:]:
        if b not in candidates:
            candidates.append(b)
        if len(candidates) >= limit:
            break
    return candidates


def remote_has_branch(ref, cwd="."):
    """Whether `ref` exists as a branch on the 'origin' remote.

    Returns True/False, or None when we genuinely can't tell — no network, no
    'origin', auth failure, etc. Callers MUST treat None as "unknown" and never
    block on it: this is a best-effort pre-flight, not a gate.

    Uses `git ls-remote`, which exits 0 with empty output when the ref is simply
    absent (→ False) and non-zero on any real failure (→ None).
    """
    # GIT_TERMINAL_PROMPT=0: never block on a credential prompt during this
    # best-effort check — a repo we can't authenticate to just reads as unknown.
    out = run_git(
        ["ls-remote", "--heads", "origin", ref], cwd,
        timeout=10, env={"GIT_TERMINAL_PROMPT": "0"},
    )
    if out is None:
        return None
    wanted = f"refs/heads/{ref}"
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) == 2 and parts[1] == wanted:   # exact match, not a suffix
            return True
    return False


def detect_repo(cwd="."):
    """Return (host, workspace, repo, current_branch) from git in cwd."""
    url = run_git(["remote", "get-url", "origin"], cwd)
    if not url:
        raise RepipeError(
            "no git 'origin' remote here — run repipe inside a repo clone "
            "(or pass --path).",
            EXIT_CONFIG,
        )
    host, workspace, repo = parse_remote(url)
    branch = run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd)
    return host, workspace, repo, branch
