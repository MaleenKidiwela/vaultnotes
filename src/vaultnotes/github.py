from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


def has_gh() -> bool:
    return shutil.which("gh") is not None


def gh_authed() -> bool:
    if not has_gh():
        return False
    r = subprocess.run(["gh", "auth", "status"], capture_output=True)
    return r.returncode == 0


def ensure_repo(repo: str, local_clone: Path, branch: str = "main") -> None:
    """Clone the repo locally. Creates via `gh repo create` if it doesn't exist
    on GitHub and `gh` is authenticated."""
    local_clone.parent.mkdir(parents=True, exist_ok=True)
    if (local_clone / ".git").exists():
        return

    if gh_authed():
        # Try to view it first; if not found, create remote (no --clone, since
        # `gh repo create` only accepts a name and would clone into ./<name>).
        view = subprocess.run(["gh", "repo", "view", repo], capture_output=True)
        if view.returncode != 0:
            subprocess.check_call(["gh", "repo", "create", repo, "--public"])
        subprocess.check_call(["gh", "repo", "clone", repo, str(local_clone)])
        _set_default_branch(local_clone, branch)
        return

    # Fallback: assume user already created the repo; plain git clone over HTTPS.
    url = f"https://github.com/{repo}.git"
    subprocess.check_call(["git", "clone", url, str(local_clone)])
    _set_default_branch(local_clone, branch)


def _set_default_branch(repo_dir: Path, branch: str) -> None:
    # If repo is empty, create the branch.
    r = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=repo_dir, capture_output=True, text=True,
    )
    current = r.stdout.strip()
    if current and current != branch:
        subprocess.check_call(["git", "branch", "-M", branch], cwd=repo_dir)


def enable_pages(repo: str, branch: str = "main") -> bool:
    if not gh_authed():
        return False
    r = subprocess.run([
        "gh", "api", f"repos/{repo}/pages", "-X", "POST",
        "-f", f"source[branch]={branch}", "-f", "source[path]=/",
    ], capture_output=True)
    return r.returncode == 0


def commit_and_push(
    repo_dir: Path,
    message: str,
    branch: str = "main",
    paths: list[str] | None = None,
) -> bool:
    candidates = paths or ["notes", "notes.html"]
    existing = [p for p in candidates if (repo_dir / p).exists()]
    if not existing:
        return False
    subprocess.check_call(["git", "add", *existing], cwd=repo_dir)
    staged = subprocess.run(
        ["git", "diff", "--cached", "--quiet"], cwd=repo_dir,
    )
    if staged.returncode == 0:
        return False
    subprocess.check_call(["git", "commit", "-m", message], cwd=repo_dir)
    subprocess.check_call(["git", "push", "origin", branch], cwd=repo_dir)
    return True


def pull(repo_dir: Path, branch: str = "main") -> None:
    r = subprocess.run(
        ["git", "pull", "origin", branch],
        cwd=repo_dir, capture_output=True, text=True,
    )
    # First push to an empty repo won't have a remote branch yet; swallow.
    if r.returncode != 0 and "couldn't find remote ref" not in (r.stderr or ""):
        pass
