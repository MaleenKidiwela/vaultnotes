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
        "bg": "#0c0c0f", "bg2": "#131318", "bg3": "#191920", "bg4": "#22222b",
        "border": "#26262f", "border_2": "#363642",
        "accent": "#4bb8c0", "accent_dim": "#1f4a4e",
        "accent_glow": "rgba(75, 184, 192, 0.08)",
        "text": "#e8e8ea", "text_2": "#a0a0a8", "text_3": "#5c5c66",
        "text_muted": "#7a7a84",
    },
    "paper": {
        "bg": "#f5f2eb", "bg2": "#ece7dc", "bg3": "#e3ddcd", "bg4": "#d6ceba",
        "border": "#cabfa8", "border_2": "#a89d84",
        "accent": "#2a7a80", "accent_dim": "#a8c8cb",
        "accent_glow": "rgba(42, 122, 128, 0.08)",
        "text": "#1a1a1f", "text_2": "#5a5a63", "text_3": "#8a8a93",
        "text_muted": "#6a6a73",
    },
}


@dataclass
class Rag:
    enabled: bool = False
    worker_url: str = ""


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
    rag: Rag = field(default_factory=Rag)
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
    rag_block = data.get("rag", {}) or {}
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
        rag=Rag(
            enabled=bool(rag_block.get("enabled", False)),
            worker_url=str(rag_block.get("worker_url", "") or ""),
        ),
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
