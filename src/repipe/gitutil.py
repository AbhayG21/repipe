"""git discovery: turn the working tree's origin remote into provider inputs."""

import re
import subprocess
from typing import Optional

from .errors import RepipeError, EXIT_CONFIG


def run_git(args, cwd=None) -> Optional[str]:
    """Run a git command; return stdout stripped, or None on non-zero exit."""
    try:
        out = subprocess.run(
            ["git", *args], cwd=cwd, capture_output=True, text=True
        )
    except FileNotFoundError:
        raise RepipeError("git was not found on PATH.", EXIT_CONFIG)
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
