from __future__ import annotations

import shutil
from pathlib import Path

from vaultnotes.config import Config

SYNCED_SUFFIXES = {".md", ".html"}


def sync_project(vault_path: Path, pages_repo: Path, project_folder: str) -> list[str]:
    """Mirror a project folder's .md/.html files into pages_repo/notes/<project>/.
    Returns list of relative paths that now exist in the destination."""
    src = vault_path / project_folder
    dst = pages_repo / "notes" / project_folder
    dst.mkdir(parents=True, exist_ok=True)

    kept: set[str] = set()
    if src.is_dir():
        for f in src.iterdir():
            if f.is_file() and f.suffix in SYNCED_SUFFIXES:
                shutil.copy2(f, dst / f.name)
                kept.add(f.name)

    # Delete stale files in destination that are no longer in source.
    for existing in dst.iterdir():
        if existing.is_file() and existing.name not in kept:
            existing.unlink()

    return sorted(kept)


def sync_all(cfg: Config) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for p in cfg.projects:
        result[p.folder] = sync_project(cfg.vault_path, cfg.local_clone, p.folder)
    return result
