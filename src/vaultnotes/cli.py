from __future__ import annotations

import argparse
import datetime as dt
import os
import random
import sys
from importlib import resources
from pathlib import Path

from vaultnotes import __version__, build, config as cfgmod, github, integrity, rag, sync

# macOS-only guard for scheduling.
IS_MACOS = sys.platform == "darwin"


def _log(msg: str) -> None:
    print(msg, flush=True)


# ── init ────────────────────────────────────────────────────────────────────
DEFAULT_THEME = "midnight"
DEFAULT_ACCENT = "#18cdd8"
PALETTE = ["#f5a833", "#a37cff", "#38dba0", "#ff7a8a", "#7abaff", "#e7c94f"]


def _prompt(question: str, default: str = "") -> str:
    hint = f" [{default}]" if default else ""
    ans = input(f"{question}{hint}: ").strip()
    return ans or default


def _prompt_bool(question: str, default: bool = True) -> bool:
    hint = "[Y/n]" if default else "[y/N]"
    ans = input(f"{question} {hint}: ").strip().lower()
    if not ans:
        return default
    return ans.startswith("y")


def cmd_init(args: argparse.Namespace) -> int:
    _log("vaultnotes init — interactive setup")

    # Vault
    vault = _prompt("Path to your Obsidian vault", "~/Documents/Obsidian Vault")
    vault_path = Path(os.path.expanduser(vault))
    if not vault_path.exists():
        _log(f"  vault not found: {vault_path}")
        _log("  Install Obsidian (obsidian.md) and create the vault, then rerun.")
        return 1

    # Project folders
    subdirs = sorted(
        d.name for d in vault_path.iterdir()
        if d.is_dir() and not d.name.startswith(".")
    )
    _log("\nTop-level folders in your vault:")
    for i, name in enumerate(subdirs, 1):
        _log(f"  {i:2d}. {name}")
    picks = _prompt(
        "Which folders to publish? Comma-separated numbers or names",
        ",".join(str(i) for i in range(1, min(len(subdirs), 3) + 1)),
    )
    selected: list[str] = []
    for tok in [t.strip() for t in picks.split(",") if t.strip()]:
        if tok.isdigit():
            idx = int(tok) - 1
            if 0 <= idx < len(subdirs):
                selected.append(subdirs[idx])
        elif tok in subdirs:
            selected.append(tok)
    if not selected:
        _log("No folders selected. Aborting.")
        return 1

    # Colors for each project
    project_blocks: list[str] = []
    for i, folder in enumerate(selected):
        label = _prompt(f"  Display label for '{folder}'", folder)
        color = _prompt(f"  Color for '{folder}' (hex)", PALETTE[i % len(PALETTE)])
        desc = _prompt(f"  One-line description for '{folder}'", "")
        project_blocks.append(
            f'  - folder: "{folder}"\n'
            f'    label: "{label}"\n'
            f'    color: "{color}"\n'
            f'    description: "{desc}"'
        )

    # Site metadata
    title = _prompt("Site title", "Research Notes")
    wordmark = _prompt("Short wordmark (2–3 chars)", "JD")
    theme = _prompt("Theme (midnight | paper)", DEFAULT_THEME)
    accent = _prompt("Global accent hex (blank to keep theme default)", "")

    # GitHub
    default_repo = ""
    repo = _prompt("GitHub repo (owner/name, e.g. you/you.github.io)", default_repo)
    sched_time = _prompt("Daily sync time (HH:MM)", "17:00")

    # Render config
    tmpl = resources.files("vaultnotes.templates").joinpath("config.yaml.tmpl").read_text()
    body = (
        tmpl
        .replace("{{SITE_TITLE}}", title)
        .replace("{{WORDMARK}}", wordmark)
        .replace("{{THEME}}", theme)
        .replace("{{ACCENT}}", accent)
        .replace("{{VAULT_PATH}}", str(vault_path))
        .replace("{{PROJECTS_BLOCK}}", "\n".join(project_blocks))
        .replace("{{GITHUB_REPO}}", repo)
        .replace("{{SCHEDULE_TIME}}", sched_time)
    )
    cfgmod.CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    cfgmod.CONFIG_PATH.write_text(body)
    _log(f"\nConfig written: {cfgmod.CONFIG_PATH}")

    # Validate
    cfg = cfgmod.load()
    errs = cfgmod.validate(cfg)
    if errs:
        _log("Config errors:")
        for e in errs:
            _log(f"  - {e}")
        _log("Edit the file and run `vaultnotes doctor` to re-check.")
        return 1

    # Repo setup
    _log(f"\nSetting up GitHub repo {repo}")
    github.ensure_repo(cfg.github_repo, cfg.local_clone, cfg.github_branch)
    if github.gh_authed():
        if github.enable_pages(cfg.github_repo, cfg.github_branch):
            _log("  GitHub Pages enabled.")
        else:
            _log("  Could not auto-enable Pages. Enable manually in repo settings.")

    # First sync
    if _prompt_bool("Run first sync now?", True):
        return cmd_sync(args)

    if IS_MACOS and _prompt_bool(f"Install daily launchd job at {sched_time}?", True):
        from vaultnotes import schedule_macos
        schedule_macos.install(cfg)
        _log(f"  Installed: {schedule_macos.PLIST_PATH}")

    return 0


# ── sync ────────────────────────────────────────────────────────────────────
def cmd_sync(args: argparse.Namespace) -> int:
    cfg = cfgmod.load()
    errs = cfgmod.validate(cfg)
    if errs:
        for e in errs:
            _log(f"config error: {e}")
        return 1

    _log(f"[{dt.datetime.now():%Y-%m-%d %H:%M:%S}] Syncing vault → pages repo")

    if not (cfg.local_clone / ".git").exists():
        github.ensure_repo(cfg.github_repo, cfg.local_clone, cfg.github_branch)
    github.pull(cfg.local_clone, cfg.github_branch)

    counts = sync.sync_all(cfg)
    for proj, files in counts.items():
        _log(f"  {proj}: {len(files)} files")

    if cfg.rag.enabled:
        rag.write_rag_config_json(cfg, cfg.local_clone)
        rag.write_chat_config(cfg, cfg.local_clone)

    build.build(cfg, cfg.local_clone)
    _log("  notes.html built")

    errs = integrity.check(cfg, cfg.local_clone)
    if errs:
        _log("Integrity check FAILED — not pushing:")
        for e in errs:
            _log(f"  {e}")
        return 2

    msg = f"vaultnotes sync — {dt.datetime.now():%Y-%m-%d %H:%M:%S}"
    pushed = github.commit_and_push(cfg.local_clone, msg, cfg.github_branch)
    _log("  pushed" if pushed else "  no changes")
    return 0


# ── build ───────────────────────────────────────────────────────────────────
def cmd_build(args: argparse.Namespace) -> int:
    cfg = cfgmod.load()
    errs = cfgmod.validate(cfg)
    if errs:
        for e in errs:
            _log(f"config error: {e}")
        return 1
    build.build(cfg, cfg.local_clone)
    _log(f"built: {cfg.local_clone / 'notes.html'}")
    return 0


# ── schedule ────────────────────────────────────────────────────────────────
def cmd_schedule(args: argparse.Namespace) -> int:
    if not IS_MACOS:
        _log("Scheduling is macOS-only in v1.")
        return 1
    from vaultnotes import schedule_macos

    cfg = cfgmod.load()
    if args.action == "install":
        path = schedule_macos.install(cfg)
        _log(f"Installed: {path}")
        return 0
    if args.action == "uninstall":
        if schedule_macos.uninstall():
            _log("Uninstalled.")
        else:
            _log("No plist found.")
        return 0
    if args.action == "status":
        info = schedule_macos.status()
        for k, v in info.items():
            _log(f"{k}: {v}")
        return 0
    return 1


# ── rag ─────────────────────────────────────────────────────────────────────
def cmd_rag(args: argparse.Namespace) -> int:
    cfg = cfgmod.load()
    errs = cfgmod.validate(cfg)
    if errs:
        for e in errs:
            _log(f"config error: {e}")
        return 1

    if args.action == "enable":
        if not (cfg.local_clone / ".git").exists():
            _log(
                f"Pages repo not found at {cfg.local_clone}. "
                "Run `vaultnotes init` or `vaultnotes sync` first to clone it."
            )
            return 1
        rag.update_user_config(enabled=True)
        cfg = cfgmod.load()
        written = rag.enable(cfg, cfg.local_clone)
        _log(f"Wrote {len(written)} files into {cfg.local_clone}:")
        for label in written:
            _log(f"  + {label}")
        _log("")
        _log(rag.next_steps_message(cfg, cfg.local_clone))
        return 0

    if args.action == "set-worker-url":
        url = (args.url or "").strip().rstrip("/")
        if not url.startswith("https://"):
            _log("worker URL must start with https://")
            return 1
        rag.update_user_config(worker_url=url)
        cfg = cfgmod.load()
        if not cfg.rag.enabled:
            _log("RAG is disabled in config. Run `vaultnotes rag enable` first.")
            return 1
        target = rag.write_chat_config(cfg, cfg.local_clone)
        _log(f"Saved worker URL: {url}")
        _log(f"Updated: {target}")
        _log("Run `vaultnotes sync` to push the change.")
        return 0

    if args.action == "disable":
        rag.update_user_config(enabled=False)
        _log("RAG disabled in config. Generated files in the pages repo are left in place;")
        _log("delete chat/, worker/, scripts/, public/, .github/workflows/build-index.yml,")
        _log("rag-config.json, and root package.json by hand if you want them gone.")
        return 0

    return 1


# ── doctor ──────────────────────────────────────────────────────────────────
def cmd_doctor(args: argparse.Namespace) -> int:
    _log(f"vaultnotes {__version__}")
    try:
        cfg = cfgmod.load()
    except FileNotFoundError as e:
        _log(str(e))
        return 1
    errs = cfgmod.validate(cfg)
    if errs:
        _log("Config:")
        for e in errs:
            _log(f"  FAIL {e}")
    else:
        _log("Config: OK")

    _log(f"gh installed: {github.has_gh()}")
    _log(f"gh authenticated: {github.gh_authed()}")
    _log(f"local clone exists: {(cfg.local_clone / '.git').exists()}")
    if IS_MACOS:
        from vaultnotes import schedule_macos
        _log(f"schedule installed: {schedule_macos.PLIST_PATH.exists()}")
    return 0 if not errs else 1


# ── main ────────────────────────────────────────────────────────────────────
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="vaultnotes")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init", help="Interactive setup").set_defaults(func=cmd_init)
    sub.add_parser("sync", help="Sync vault → pages repo, build, push").set_defaults(func=cmd_sync)
    sub.add_parser("build", help="Rebuild notes.html only").set_defaults(func=cmd_build)
    sub.add_parser("doctor", help="Diagnose configuration").set_defaults(func=cmd_doctor)

    sch = sub.add_parser("schedule", help="Manage daily launchd job")
    sch.add_argument("action", choices=["install", "uninstall", "status"])
    sch.set_defaults(func=cmd_schedule)

    rg = sub.add_parser("rag", help="Manage the RAG chat add-on")
    rg.add_argument("action", choices=["enable", "set-worker-url", "disable"])
    rg.add_argument("url", nargs="?", help="Worker URL (for set-worker-url)")
    rg.set_defaults(func=cmd_rag)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
