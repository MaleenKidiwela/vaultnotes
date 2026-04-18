from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

SCHEMA_VERSION = 1
CONFIG_PATH = Path.home() / ".config" / "vaultnotes" / "config.yaml"
HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
REPO_RE = re.compile(r"^[\w.-]+/[\w.-]+$")
TIME_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")

THEMES = {
    "midnight": {
        "bg": "#06101c", "bg2": "#0b1828", "bg3": "#101f32", "bg4": "#162840",
        "border": "#1c3050", "border_2": "#2c4870",
        "accent": "#18cdd8", "accent_dim": "#0b5a62",
        "accent_glow": "rgba(24, 205, 216, 0.10)",
        "text": "#ddeef8", "text_2": "#7a9ab8", "text_3": "#405870",
        "text_muted": "#628098",
    },
    "paper": {
        "bg": "#f7f3ea", "bg2": "#efe8d8", "bg3": "#e8dfc9", "bg4": "#d9cdb0",
        "border": "#c9bfa5", "border_2": "#a89e84",
        "accent": "#1a6b73", "accent_dim": "#8fb8bc",
        "accent_glow": "rgba(26, 107, 115, 0.10)",
        "text": "#1a2530", "text_2": "#45566a", "text_3": "#6a7a8c",
        "text_muted": "#556877",
    },
}


@dataclass
class Project:
    folder: str
    label: str
    color: str
    description: str = ""
    daily_pattern: str = r"(\d{2})-(\d{2})-(\d{2})"  # MM-DD-YY Notes.md


@dataclass
class Config:
    site_title: str
    wordmark: str
    theme: str
    accent: str | None
    vault_path: Path
    projects: list[Project]
    github_repo: str
    github_branch: str
    local_clone: Path
    schedule_enabled: bool
    schedule_time: str
    schema_version: int = SCHEMA_VERSION

    @property
    def theme_colors(self) -> dict[str, str]:
        colors = dict(THEMES[self.theme])
        if self.accent:
            colors["accent"] = self.accent
        return colors

    def hour_minute(self) -> tuple[int, int]:
        h, m = self.schedule_time.split(":")
        return int(h), int(m)


def _expand(p: str | Path) -> Path:
    return Path(os.path.expanduser(str(p))).resolve()


def load(path: Path = CONFIG_PATH) -> Config:
    if not path.exists():
        raise FileNotFoundError(f"No config at {path}. Run `vaultnotes init`.")
    with path.open() as f:
        data = yaml.safe_load(f) or {}
    return _from_dict(data)


def _from_dict(data: dict[str, Any]) -> Config:
    site = data.get("site", {})
    vault = data.get("vault", {})
    gh = data.get("github", {})
    sched = data.get("schedule", {})
    projects = [
        Project(
            folder=p["folder"],
            label=p.get("label", p["folder"]),
            color=p["color"],
            description=p.get("description", "").strip(),
            daily_pattern=p.get("daily_pattern", r"(\d{2})-(\d{2})-(\d{2})"),
        )
        for p in data.get("projects", [])
    ]
    return Config(
        site_title=site.get("title", "Notes"),
        wordmark=site.get("wordmark", "Notes"),
        theme=site.get("theme", "midnight"),
        accent=site.get("accent"),
        vault_path=_expand(vault.get("path", "~/Documents/Obsidian Vault")),
        projects=projects,
        github_repo=gh.get("repo", ""),
        github_branch=gh.get("branch", "main"),
        local_clone=_expand(gh.get("local_clone", "~/.local/share/vaultnotes/pages-repo")),
        schedule_enabled=sched.get("enabled", True),
        schedule_time=sched.get("time", "17:00"),
        schema_version=data.get("schema_version", SCHEMA_VERSION),
    )


def validate(cfg: Config) -> list[str]:
    """Return a list of error strings. Empty list = valid."""
    errs: list[str] = []

    if not cfg.vault_path.exists():
        errs.append(f"vault.path does not exist: {cfg.vault_path}")
    if not cfg.projects:
        errs.append("projects: at least one project required")

    seen = set()
    for p in cfg.projects:
        if p.folder in seen:
            errs.append(f"projects: duplicate folder '{p.folder}'")
        seen.add(p.folder)
        if not HEX_RE.match(p.color):
            errs.append(f"projects['{p.folder}'].color must be 6-digit hex, got {p.color}")
        folder_path = cfg.vault_path / p.folder
        if cfg.vault_path.exists() and not folder_path.exists():
            errs.append(f"projects['{p.folder}']: folder not found in vault: {folder_path}")

    if cfg.theme not in THEMES:
        errs.append(f"site.theme must be one of {list(THEMES)}, got {cfg.theme}")
    if cfg.accent and not HEX_RE.match(cfg.accent):
        errs.append(f"site.accent must be 6-digit hex, got {cfg.accent}")
    if not REPO_RE.match(cfg.github_repo):
        errs.append(f"github.repo must be 'owner/name', got '{cfg.github_repo}'")
    if not TIME_RE.match(cfg.schedule_time):
        errs.append(f"schedule.time must be HH:MM (24h), got '{cfg.schedule_time}'")

    return errs


def write(cfg_dict: dict[str, Any], path: Path = CONFIG_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        yaml.safe_dump(cfg_dict, f, sort_keys=False, width=80)
