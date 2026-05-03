from __future__ import annotations

import argparse
import datetime as dt
import os
import shutil
import subprocess
import sys
import tempfile
from importlib import resources
from importlib.util import find_spec
from pathlib import Path

from vaultnotes import __version__, build, config as cfgmod, github, integrity, rag, sync

try:
    import questionary
except ImportError:  # pragma: no cover - exercised only in dev environments.
    questionary = None

# macOS-only guard for scheduling.
IS_MACOS = sys.platform == "darwin"


def _log(msg: str) -> None:
    print(msg, flush=True)


# ── init ────────────────────────────────────────────────────────────────────
DEFAULT_THEME = "midnight"
DEFAULT_ACCENT = "#18cdd8"
PALETTE = ["#f5a833", "#a37cff", "#38dba0", "#ff7a8a", "#7abaff", "#e7c94f"]


def _print_init_banner() -> None:
    for line in _plain_block_fox():
        _log(line)
    _log("")


def _print_init_preview() -> None:
    _print_init_banner()
    _log("Interactive setup preview")
    _log("")
    _log("Choose what you want to do.")
    _log("")
    _log(" › Setup  - configure publishing for the first time")
    _log("   Update - browse maintenance and update commands")
    _log("")
    _log("Setup tabs:")
    _log("  1. Vault  2. Folders  3. Projects  4. Site  5. GitHub  6. Review")
    _log("")
    _log("? Path to your Obsidian vault  ~/Documents/Obsidian Vault")
    _log("")
    _log("? Which folders should be published? (Use arrow keys to move, <space> to select)")
    _log(" » ● Research")
    _log("   ● SideQuest")
    _log("   ○ Archive")
    _log("")
    _log("? Theme  midnight")
    _log("")
    _log("Review setup:")
    _log("  Vault:    ~/Documents/Obsidian Vault")
    _log("  Projects:")
    _log("    - Research as Research (#f5a833)")
    _log("    - SideQuest as SideQuest (#a37cff)")
    _log("  Site:     Research Notes / JD / midnight")
    _log("  GitHub:   you/you.github.io")
    _log("  Schedule: 17:00")
    _log("")
    _log("? Continue, edit a section, or abort (Use arrow keys)")
    _log(" » Continue")
    _log("   Edit vault path")
    _log("   Edit projects")
    _log("   Edit site settings")
    _log("   Edit GitHub repo")
    _log("   Edit schedule time")
    _log("   Restart all answers")
    _log("   Abort")
    _log("")
    _log("Update command tabs:")
    _log("  sync  build  add  schedule  rag enable  rag deploy-worker  rag secret")
    _log("  rag set-worker-url  where  doctor  upgrade")
    _log("")
    _log("Example command tab:")
    _log("  vaultnotes upgrade [--ref <branch|tag|commit>]")
    _log("  Reinstall vaultnotes from GitHub through pipx.")
    _log("  Fields can be edited in place, then Run command executes the selected command.")
    _log("")
    _log("Preview only. No config, repo, notes, or schedule was touched.")


def _create_mock_vault() -> tuple[tempfile.TemporaryDirectory, Path]:
    tmp = tempfile.TemporaryDirectory(prefix="vaultnotes-mock-")
    vault_path = Path(tmp.name) / "Obsidian Vault"
    samples = {
        "Research": "# Research\n\nA sample research note.\n",
        "SideQuest": "# SideQuest\n\nA sample side project note.\n",
        "Archive": "# Archive\n\nA sample archived note.\n",
    }
    for folder, body in samples.items():
        project_dir = vault_path / folder
        project_dir.mkdir(parents=True, exist_ok=True)
        (project_dir / "index.md").write_text(body)
    return tmp, vault_path


def _plain_block_fox() -> list[str]:
    return [
        "╭────────────────────────────────────────────╮",
        "│  ▗▖      ▗▖   Vaultnotes                  │",
        "│  ▜█▙    ▟█▛   Publish your notes,         │",
        "│   ▜█▙▄▄▟█▛    keep control.               │",
        "│    ▐█▌▐█▌     Guided setup                │",
        "│     ▜██▛      GitHub Pages sync           │",
        "╰────────────────────────────────────────────╯",
    ]


def _use_tui() -> bool:
    return find_spec("prompt_toolkit") is not None and sys.stdin.isatty() and sys.stdout.isatty()


def _tui_unavailable_reasons() -> list[str]:
    reasons = []
    if find_spec("prompt_toolkit") is None:
        reasons.append("prompt_toolkit is not installed in this Python environment")
    if not sys.stdin.isatty():
        reasons.append("stdin is not an interactive terminal")
    if not sys.stdout.isatty():
        reasons.append("stdout is not an interactive terminal")
    return reasons


def _prompt(question: str, default: str = "") -> str:
    if _use_tui() and questionary is not None:
        ans = questionary.text(question, default=default).ask()
        if ans is None:
            raise KeyboardInterrupt
        return ans.strip() or default
    hint = f" [{default}]" if default else ""
    ans = input(f"{question}{hint}: ").strip()
    return ans or default


def _prompt_bool(question: str, default: bool = True) -> bool:
    if _use_tui() and questionary is not None:
        ans = questionary.confirm(question, default=default).ask()
        if ans is None:
            raise KeyboardInterrupt
        return bool(ans)
    hint = "[Y/n]" if default else "[y/N]"
    ans = input(f"{question} {hint}: ").strip().lower()
    if not ans:
        return default
    return ans.startswith("y")


def _select_one(question: str, choices: list[tuple[str, str]], default: str) -> str:
    if _use_tui() and questionary is not None:
        choice_objs = [
            questionary.Choice(title=title, value=value, checked=value == default)
            for title, value in choices
        ]
        ans = questionary.select(question, choices=choice_objs, default=default).ask()
        if ans is None:
            raise KeyboardInterrupt
        return str(ans)
    default_title = next((title for title, value in choices if value == default), default)
    ans = _prompt(question, default_title).strip().lower()
    for title, value in choices:
        if ans in {title.lower(), value.lower()}:
            return value
    return default


def _select_many(
    question: str,
    choices: list[str],
    default: list[str] | None = None,
) -> list[str]:
    default = default or []
    if _use_tui() and questionary is not None:
        choice_objs = [
            questionary.Choice(title=choice, value=choice, checked=choice in default)
            for choice in choices
        ]
        ans = questionary.checkbox(question, choices=choice_objs).ask()
        if ans is None:
            raise KeyboardInterrupt
        return list(ans)

    _log("\nTop-level folders in your vault:")
    for i, name in enumerate(choices, 1):
        _log(f"  {i:2d}. {name}")
    default_nums = ",".join(
        str(choices.index(name) + 1) for name in default if name in choices
    )
    picks = _prompt(
        "Which folders to publish? Comma-separated numbers or names",
        default_nums,
    )
    selected: list[str] = []
    for tok in [t.strip() for t in picks.split(",") if t.strip()]:
        if tok.isdigit():
            idx = int(tok) - 1
            if 0 <= idx < len(choices):
                selected.append(choices[idx])
        elif tok in choices:
            selected.append(tok)
    return list(dict.fromkeys(selected))


def _prompt_vault_path(default: str = "~/Documents/Obsidian Vault") -> Path | None:
    vault = _prompt("Path to your Obsidian vault", default)
    vault_path = Path(os.path.expanduser(vault))
    if not vault_path.exists():
        _log(f"  vault not found: {vault_path}")
        _log("  Install Obsidian (obsidian.md) and create the vault, then rerun.")
        return None
    return vault_path


def _prompt_selected_folders(vault_path: Path) -> list[str]:
    subdirs = sorted(
        d.name for d in vault_path.iterdir()
        if d.is_dir() and not d.name.startswith(".")
    )
    default = subdirs[:3]
    return _select_many("Which folders should be published?", subdirs, default)


def _prompt_project_details(selected: list[str]) -> list[dict[str, str]]:
    projects: list[dict[str, str]] = []
    for i, folder in enumerate(selected):
        label = _prompt(f"  Display label for '{folder}'", folder)
        color = _prompt(f"  Color for '{folder}' (hex)", PALETTE[i % len(PALETTE)])
        desc = _prompt(f"  One-line description for '{folder}'", "")
        projects.append({
            "folder": folder,
            "label": label,
            "color": color,
            "description": desc,
        })
    return projects


def _prompt_projects(vault_path: Path) -> list[dict[str, str]] | None:
    selected = _prompt_selected_folders(vault_path)
    if not selected:
        _log("No folders selected.")
        return None
    return _prompt_project_details(selected)


def _prompt_site_settings() -> dict[str, str]:
    return {
        "title": _prompt("Site title", "Research Notes"),
        "wordmark": _prompt("Short wordmark (2–3 chars)", "JD"),
        "theme": _select_one(
            "Theme",
            [("midnight", "midnight"), ("paper", "paper")],
            DEFAULT_THEME,
        ),
        "accent": _prompt("Global accent hex (blank to keep theme default)", ""),
    }


def _prompt_github_repo() -> str:
    return _prompt("GitHub repo (owner/name, e.g. you/you.github.io)", "")


def _prompt_schedule_time() -> str:
    return _prompt("Daily sync time (HH:MM)", "17:00")


def _project_blocks(projects: list[dict[str, str]]) -> list[str]:
    return [
        f'  - folder: "{p["folder"]}"\n'
        f'    label: "{p["label"]}"\n'
        f'    color: "{p["color"]}"\n'
        f'    description: "{p["description"]}"'
        for p in projects
    ]


def _review_setup(
    vault_path: Path,
    projects: list[dict[str, str]],
    site: dict[str, str],
    repo: str,
    sched_time: str,
) -> str:
    _log("\nReview setup:")
    _log(f"  Vault:    {vault_path}")
    _log("  Projects:")
    for p in projects:
        desc = f" — {p['description']}" if p["description"] else ""
        _log(f"    - {p['folder']} as {p['label']} ({p['color']}){desc}")
    _log(f"  Site:     {site['title']} / {site['wordmark']} / {site['theme']}")
    if site["accent"]:
        _log(f"  Accent:   {site['accent']}")
    _log(f"  GitHub:   {repo}")
    _log(f"  Schedule: {sched_time}")
    _log("")
    return _select_one(
        "Continue, edit a section, or abort",
        [
            ("Continue", "continue"),
            ("Edit vault path", "vault"),
            ("Edit projects", "projects"),
            ("Edit site settings", "site"),
            ("Edit GitHub repo", "github"),
            ("Edit schedule time", "schedule"),
            ("Restart all answers", "all"),
            ("Abort", "abort"),
        ],
        "continue",
    ).strip().lower()


def _run_tabbed_init(default_vault: str) -> dict[str, object] | None:
    from prompt_toolkit.application import Application
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.keys import Keys
    from prompt_toolkit.layout import Layout
    from prompt_toolkit.layout.containers import HSplit, Window
    from prompt_toolkit.layout.controls import FormattedTextControl
    from prompt_toolkit.styles import Style

    setup_tabs = ["Vault", "Folders", "Projects", "Site", "GitHub", "Review"]
    command_tabs = [
        "sync", "build", "add", "schedule", "rag enable", "rag deploy-worker",
        "rag secret", "rag set-worker-url", "where", "doctor", "upgrade",
    ]
    command_help = {
        "sync": (
            "vaultnotes sync",
            "Sync your configured vault folders into the local Pages repo, rebuild notes.html, "
            "run integrity checks, commit, and push.",
        ),
        "build": (
            "vaultnotes build",
            "Rebuild notes.html from the current local pages repo contents without syncing or pushing.",
        ),
        "add": (
            "vaultnotes add <folder>",
            "Add another top-level vault folder to the published project list, then sync by default.",
        ),
        "schedule": (
            "vaultnotes schedule install|uninstall|status",
            "Manage the macOS launchd job that runs daily sync automatically.",
        ),
        "rag enable": (
            "vaultnotes rag enable",
            "Add the password-gated chat app, Worker template, indexer script, and GitHub Action.",
        ),
        "rag deploy-worker": (
            "vaultnotes rag deploy-worker",
            "Run wrangler deploy from the generated Worker directory.",
        ),
        "rag secret": (
            "vaultnotes rag secret <NAME>",
            "Set a Cloudflare Worker secret such as GEMINI_API_KEY or CHAT_PASSWORD.",
        ),
        "rag set-worker-url": (
            "vaultnotes rag set-worker-url <url>",
            "Save the deployed Worker URL into chat/config.json so the browser can query it.",
        ),
        "where": (
            "vaultnotes where [--open]",
            "Print the local Pages repo path, or reveal it in Finder on macOS.",
        ),
        "doctor": (
            "vaultnotes doctor",
            "Check config validity, gh availability/authentication, local clone status, and schedule status.",
        ),
        "upgrade": (
            "vaultnotes upgrade [--ref <branch|tag|commit>]",
            "Reinstall vaultnotes from GitHub through pipx. Config, notes, and generated repos are left alone.",
        ),
    }
    command_fields = {
        "sync": [],
        "build": [],
        "add": [
            ("folder", "Folder", ""),
            ("label", "Label", ""),
            ("color", "Color", ""),
            ("description", "Description", ""),
            ("no_sync", "No sync? y/N", "n"),
        ],
        "schedule": [("action", "Action install|uninstall|status", "status")],
        "rag enable": [],
        "rag deploy-worker": [],
        "rag secret": [("name", "Secret name", "GEMINI_API_KEY")],
        "rag set-worker-url": [("url", "Worker URL", "https://")],
        "where": [("open", "Open in Finder? y/N", "n")],
        "doctor": [],
        "upgrade": [("ref", "Ref branch|tag|commit (blank = main)", "")],
    }
    state = {
        "screen": "home",
        "tab": 0,
        "command_tab": 0,
        "row": 0,
        "error": "",
    }
    data: dict[str, object] = {
        "vault": default_vault,
        "selected": [],
        "projects": {},
        "site": {
            "title": "Research Notes",
            "wordmark": "JD",
            "theme": DEFAULT_THEME,
            "accent": "",
        },
        "github_username": "",
        "schedule_time": "17:00",
        "commands": {
            name: {key: default for key, _label, default in fields}
            for name, fields in command_fields.items()
        },
    }

    def vault_path() -> Path:
        return Path(os.path.expanduser(str(data["vault"])))

    def github_username() -> str:
        raw = str(data["github_username"]).strip().lstrip("@")
        return raw.split("/", 1)[0].strip()

    def github_repo() -> str:
        user = github_username()
        return f"{user}/{user}.github.io" if user else ""

    def vault_dirs() -> list[str]:
        path = vault_path()
        if not path.is_dir():
            return []
        return sorted(d.name for d in path.iterdir() if d.is_dir() and not d.name.startswith("."))

    def selected_folders() -> list[str]:
        dirs = vault_dirs()
        selected = [f for f in data["selected"] if f in dirs]  # type: ignore[index]
        if not data["selected"] and dirs:
            selected = dirs[:3]
        data["selected"] = selected
        return selected

    def project_details() -> dict[str, dict[str, str]]:
        selected = selected_folders()
        projects: dict[str, dict[str, str]] = data["projects"]  # type: ignore[assignment]
        for i, folder in enumerate(selected):
            projects.setdefault(
                folder,
                {
                    "label": folder,
                    "description": "",
                    "color": PALETTE[i % len(PALETTE)],
                },
            )
        for folder in list(projects):
            if folder not in selected:
                del projects[folder]
        return projects

    def project_rows() -> list[tuple[str, str, str]]:
        rows: list[tuple[str, str, str]] = []
        for folder in selected_folders():
            rows.append((folder, "label", "Label"))
            rows.append((folder, "description", "Description"))
            rows.append((folder, "color", "Color"))
        return rows

    def site_rows() -> list[tuple[str, str]]:
        return [
            ("title", "Site title"),
            ("wordmark", "Wordmark"),
            ("theme", "Theme"),
            ("accent", "Global accent hex"),
        ]

    def github_rows() -> list[tuple[str, str]]:
        return [
            ("github_username", "GitHub username"),
            ("schedule_time", "Daily sync time"),
        ]

    def command_rows() -> list[tuple[str, str]]:
        name = command_tabs[state["command_tab"]]
        return [(key, label) for key, label, _default in command_fields[name]]

    def review_rows() -> list[str]:
        return [
            "Continue setup",
            "Edit vault path",
            "Edit folders",
            "Edit project details",
            "Edit site settings",
            "Edit GitHub username",
            "Abort",
        ]

    def active_tabs() -> list[str]:
        return command_tabs if state["screen"] == "commands" else setup_tabs

    def active_tab_index() -> int:
        return state["command_tab"] if state["screen"] == "commands" else state["tab"]

    def active_tab() -> str:
        return active_tabs()[active_tab_index()]

    def row_count(tab: str) -> int:
        if state["screen"] == "home":
            return 2
        if state["screen"] == "commands":
            return len(command_rows()) + 2
        if tab == "Vault":
            return 1
        if tab == "Folders":
            return max(1, len(vault_dirs()))
        if tab == "Projects":
            return max(1, len(project_rows()))
        if tab == "Site":
            return len(site_rows())
        if tab == "GitHub":
            return len(github_rows())
        return len(review_rows())

    def clamp_row() -> None:
        state["row"] = max(0, min(state["row"], row_count(active_tab()) - 1))

    def switch_tab(idx: int) -> None:
        tabs = active_tabs()
        if state["screen"] == "commands":
            state["command_tab"] = idx % len(tabs)
        else:
            state["tab"] = idx % len(tabs)
        state["row"] = 0
        state["error"] = ""

    def current_text_ref() -> tuple[dict[str, str], str] | None:
        if state["screen"] != "setup":
            if state["screen"] == "commands":
                rows = command_rows()
                if state["row"] < len(rows):
                    key, _label = rows[state["row"]]
                    name = command_tabs[state["command_tab"]]
                    commands: dict[str, dict[str, str]] = data["commands"]  # type: ignore[assignment]
                    return commands[name], key
            return None
        tab = active_tab()
        row = state["row"]
        if tab == "Vault":
            return data, "vault"  # type: ignore[return-value]
        if tab == "Projects":
            rows = project_rows()
            if not rows:
                return None
            folder, key, _label = rows[row]
            return project_details()[folder], key
        if tab == "Site":
            key, _label = site_rows()[row]
            if key == "theme":
                return None
            return data["site"], key  # type: ignore[return-value]
        if tab == "GitHub":
            key, _label = github_rows()[row]
            return data, key  # type: ignore[return-value]
        return None

    def validate_for_continue() -> bool:
        path = vault_path()
        if not path.is_dir():
            state["error"] = f"Vault path does not exist: {path}"
            state["screen"] = "setup"
            switch_tab(0)
            return False
        if not selected_folders():
            state["error"] = "Select at least one folder."
            state["screen"] = "setup"
            switch_tab(1)
            return False
        for folder, details in project_details().items():
            color = details["color"]
            if not cfgmod.HEX_RE.match(color):
                state["error"] = f"{folder} color must be a 6-digit hex value like #f5a833."
                state["screen"] = "setup"
                switch_tab(2)
                return False
        site: dict[str, str] = data["site"]  # type: ignore[assignment]
        if site["accent"] and not cfgmod.HEX_RE.match(site["accent"]):
            state["error"] = "Global accent must be blank or a 6-digit hex value like #18cdd8."
            state["screen"] = "setup"
            switch_tab(3)
            return False
        user = github_username()
        if not user:
            state["error"] = "Enter your GitHub username."
            state["screen"] = "setup"
            switch_tab(4)
            return False
        if not cfgmod.TIME_RE.match(str(data["schedule_time"])):
            state["error"] = "Daily sync time must be HH:MM, for example 17:00."
            state["screen"] = "setup"
            switch_tab(4)
            state["row"] = 1
            return False
        state["error"] = ""
        return True

    def payload() -> tuple[Path, list[dict[str, str]], dict[str, str], str, str]:
        projects = []
        details = project_details()
        for folder in selected_folders():
            entry = details[folder]
            projects.append({
                "folder": folder,
                "label": entry["label"],
                "description": entry["description"],
                "color": entry["color"],
            })
        return (
            vault_path(),
            projects,
            data["site"],  # type: ignore[return-value]
            github_repo(),
            str(data["schedule_time"]),
        )

    def command_argv(name: str) -> list[str] | None:
        commands: dict[str, dict[str, str]] = data["commands"]  # type: ignore[assignment]
        params = commands[name]
        if name == "sync":
            return ["sync"]
        if name == "build":
            return ["build"]
        if name == "doctor":
            return ["doctor"]
        if name == "where":
            argv = ["where"]
            if params.get("open", "").strip().lower() in {"y", "yes", "true", "1"}:
                argv.append("--open")
            return argv
        if name == "upgrade":
            argv = ["upgrade"]
            ref = params.get("ref", "").strip()
            if ref:
                argv += ["--ref", ref]
            return argv
        if name == "add":
            folder = params.get("folder", "").strip()
            if not folder:
                state["error"] = "Add needs a folder name."
                return None
            argv = ["add", folder]
            for key, flag in [
                ("label", "--label"),
                ("color", "--color"),
                ("description", "--description"),
            ]:
                value = params.get(key, "").strip()
                if value:
                    argv += [flag, value]
            if params.get("no_sync", "").strip().lower() in {"y", "yes", "true", "1"}:
                argv.append("--no-sync")
            return argv
        if name == "schedule":
            action = params.get("action", "status").strip().lower()
            if action not in {"install", "uninstall", "status"}:
                state["error"] = "Schedule action must be install, uninstall, or status."
                return None
            return ["schedule", action]
        if name == "rag enable":
            return ["rag", "enable"]
        if name == "rag deploy-worker":
            return ["rag", "deploy-worker"]
        if name == "rag secret":
            secret = params.get("name", "").strip()
            if not secret:
                state["error"] = "Secret needs a name, for example GEMINI_API_KEY."
                return None
            return ["rag", "secret", secret]
        if name == "rag set-worker-url":
            url = params.get("url", "").strip()
            if not url or url == "https://":
                state["error"] = "Worker URL is required."
                return None
            return ["rag", "set-worker-url", url]
        return None

    def add_line(parts: list[tuple[str, str]], text: str = "", style: str = "") -> None:
        parts.append((style, text + "\n"))

    def add_selectable(parts: list[tuple[str, str]], idx: int, text: str) -> None:
        prefix = "› " if idx == state["row"] else "  "
        style = "class:selected" if idx == state["row"] else ""
        add_line(parts, prefix + text, style)

    def fragments() -> list[tuple[str, str]]:
        clamp_row()
        parts: list[tuple[str, str]] = []
        for banner_line in _plain_block_fox():
            add_line(parts, banner_line, "class:title")

        if state["screen"] == "home":
            add_line(parts, "Choose what you want to do.", "class:section")
            add_line(parts)
            add_selectable(parts, 0, "Setup  - configure publishing for the first time")
            add_selectable(parts, 1, "Update - browse maintenance and update commands")
            add_line(parts)
            add_line(parts, "↑/↓ choose  enter opens  ctrl-c exits", "class:help")
            return parts

        tab_line = []
        tabs = active_tabs()
        active_idx = active_tab_index()
        for i, tab in enumerate(tabs):
            label = f" {i + 1}. {tab} "
            style = "class:tab.current" if i == active_idx else "class:tab"
            tab_line.append((style, label))
            tab_line.append(("", " "))
        parts.extend(tab_line)
        add_line(parts)
        if state["screen"] == "commands":
            add_line(parts, "←/→ command tabs  esc returns home  ctrl-c exits", "class:help")
        else:
            add_line(parts, "←/→ tabs  ↑/↓ fields  type to edit  space toggles  enter advances  esc home  ctrl-c exits", "class:help")
        if state["error"]:
            add_line(parts, state["error"], "class:error")
        add_line(parts)

        if state["screen"] == "commands":
            name = command_tabs[state["command_tab"]]
            command, desc = command_help[name]
            add_line(parts, command, "class:section")
            add_line(parts)
            for line in desc.split(". "):
                suffix = "" if line.endswith(".") else "."
                add_line(parts, f"  {line}{suffix}")
            add_line(parts)
            commands: dict[str, dict[str, str]] = data["commands"]  # type: ignore[assignment]
            params = commands[name]
            for i, (key, label) in enumerate(command_rows()):
                add_selectable(parts, i, f"{label}: {params[key]}")
            if command_rows():
                add_line(parts)
            add_selectable(parts, len(command_rows()), "Run command")
            add_selectable(parts, len(command_rows()) + 1, "Back to start")
            argv = command_argv(name)
            if argv:
                add_line(parts)
                add_line(parts, "Will run: vaultnotes " + " ".join(argv), "class:muted")
            return parts

        tab = active_tab()
        if tab == "Vault":
            add_selectable(parts, 0, f"Vault path: {data['vault']}")
            path = vault_path()
            add_line(parts)
            add_line(parts, "This is your Obsidian vault folder.", "class:muted")
            add_line(parts, f"Status: {'found' if path.is_dir() else 'not found'}", "class:muted")
        elif tab == "Folders":
            dirs = vault_dirs()
            selected = selected_folders()
            if not dirs:
                add_line(parts, "No top-level folders found at this path.", "class:error")
            for i, folder in enumerate(dirs):
                mark = "●" if folder in selected else "○"
                add_selectable(parts, i, f"{mark} {folder}")
        elif tab == "Projects":
            rows = project_rows()
            if not rows:
                add_line(parts, "Select folders first.", "class:error")
            details = project_details()
            last_folder = ""
            for i, (folder, key, label) in enumerate(rows):
                if folder != last_folder:
                    if i:
                        add_line(parts)
                    add_line(parts, folder, "class:section")
                    last_folder = folder
                add_selectable(parts, i, f"{label}: {details[folder][key]}")
        elif tab == "Site":
            site: dict[str, str] = data["site"]  # type: ignore[assignment]
            for i, (key, label) in enumerate(site_rows()):
                value = site[key]
                if key == "theme":
                    value = f"{value}  (space toggles)"
                add_selectable(parts, i, f"{label}: {value}")
        elif tab == "GitHub":
            add_selectable(parts, 0, f"GitHub username: {data['github_username']}")
            add_selectable(parts, 1, f"Daily sync time: {data['schedule_time']}")
            add_line(parts)
            add_line(parts, f"Repo will be: {github_repo() or '<username>/<username>.github.io'}", "class:muted")
        else:
            add_line(parts, "Review setup", "class:section")
            add_line(parts, f"Vault:    {vault_path()}")
            add_line(parts, "Projects:")
            for p in payload()[1]:
                desc = f" - {p['description']}" if p["description"] else ""
                add_line(parts, f"  - {p['folder']} as {p['label']} ({p['color']}){desc}")
            site: dict[str, str] = data["site"]  # type: ignore[assignment]
            add_line(parts, f"Site:     {site['title']} / {site['wordmark']} / {site['theme']}")
            add_line(parts, f"GitHub:   {github_repo() or '<missing username>'}")
            add_line(parts, f"Schedule: {data['schedule_time']}")
            add_line(parts)
            for i, action in enumerate(review_rows()):
                add_selectable(parts, i, action)
        return parts

    kb = KeyBindings()

    @kb.add("c-c")
    def _(event) -> None:
        event.app.exit(result=None)

    @kb.add("escape")
    def _(event) -> None:
        if state["screen"] in {"setup", "commands"}:
            state["screen"] = "home"
            state["row"] = 0
            state["error"] = ""
        event.app.invalidate()

    @kb.add("left")
    def _(event) -> None:
        if state["screen"] in {"setup", "commands"}:
            switch_tab(active_tab_index() - 1)
        event.app.invalidate()

    @kb.add("right")
    def _(event) -> None:
        if state["screen"] in {"setup", "commands"}:
            switch_tab(active_tab_index() + 1)
        event.app.invalidate()

    @kb.add("tab")
    def _(event) -> None:
        if state["screen"] in {"setup", "commands"}:
            switch_tab(active_tab_index() + 1)
        event.app.invalidate()

    @kb.add("s-tab")
    def _(event) -> None:
        if state["screen"] in {"setup", "commands"}:
            switch_tab(active_tab_index() - 1)
        event.app.invalidate()

    @kb.add("up")
    def _(event) -> None:
        state["row"] -= 1
        clamp_row()
        event.app.invalidate()

    @kb.add("down")
    def _(event) -> None:
        state["row"] += 1
        clamp_row()
        event.app.invalidate()

    def activate(event) -> None:
        row = state["row"]
        if state["screen"] == "home":
            state["screen"] = "setup" if state["row"] == 0 else "commands"
            state["row"] = 0
            state["error"] = ""
            event.app.invalidate()
            return
        if state["screen"] == "commands":
            rows = command_rows()
            if row < len(rows):
                state["row"] += 1
                clamp_row()
            elif row == len(rows):
                name = command_tabs[state["command_tab"]]
                argv = command_argv(name)
                if argv:
                    event.app.exit(result={"mode": "command", "argv": argv})
                    return
            else:
                state["screen"] = "home"
                state["row"] = 0
                state["error"] = ""
            event.app.invalidate()
            return

        tab = active_tab()
        if tab == "Folders":
            dirs = vault_dirs()
            if dirs:
                selected = selected_folders()
                folder = dirs[row]
                if folder in selected:
                    selected.remove(folder)
                else:
                    selected.append(folder)
                data["selected"] = selected
        elif tab == "Site":
            key, _label = site_rows()[row]
            if key == "theme":
                site: dict[str, str] = data["site"]  # type: ignore[assignment]
                site["theme"] = "paper" if site["theme"] == "midnight" else "midnight"
            else:
                state["row"] += 1
                clamp_row()
        elif tab == "Review":
            action = review_rows()[row]
            if action == "Continue setup":
                if validate_for_continue():
                    event.app.exit(result={"mode": "setup", "payload": payload()})
                    return
            elif action == "Abort":
                event.app.exit(result=None)
                return
            else:
                switch_tab(row - 1)
        else:
            state["row"] += 1
            clamp_row()
        event.app.invalidate()

    @kb.add("enter")
    def _(event) -> None:
        activate(event)

    @kb.add(" ")
    def _(event) -> None:
        activate(event)

    @kb.add("backspace")
    def _(event) -> None:
        ref = current_text_ref()
        if ref is not None:
            obj, key = ref
            obj[key] = obj[key][:-1]
        event.app.invalidate()

    @kb.add("c-u")
    def _(event) -> None:
        ref = current_text_ref()
        if ref is not None:
            obj, key = ref
            obj[key] = ""
        event.app.invalidate()

    @kb.add(Keys.Any)
    def _(event) -> None:
        text = event.data
        if text and text.isprintable():
            ref = current_text_ref()
            if ref is not None:
                obj, key = ref
                obj[key] += text
        event.app.invalidate()

    control = FormattedTextControl(fragments, focusable=True)
    root = HSplit([Window(content=control, always_hide_cursor=True)])
    style = Style.from_dict({
        "title": "bold",
        "tab": "fg:#888888",
        "tab.current": "reverse bold",
        "selected": "reverse",
        "help": "fg:#888888",
        "muted": "fg:#888888",
        "section": "bold",
        "error": "fg:#ff5f5f bold",
    })
    app = Application(
        layout=Layout(root),
        key_bindings=kb,
        style=style,
        full_screen=False,
        mouse_support=False,
    )
    return app.run()


def cmd_init(args: argparse.Namespace) -> int:
    if getattr(args, "preview_ui", False):
        _print_init_preview()
        return 0

    mock_tmp: tempfile.TemporaryDirectory | None = None
    default_vault = "~/Documents/Obsidian Vault"
    if getattr(args, "mock_run", False):
        if not _use_tui():
            _log("Mock run needs the interactive terminal UI.")
            for reason in _tui_unavailable_reasons():
                _log(f"  - {reason}")
            _log("")
            _log("From a normal terminal in this repo, run:")
            _log("  .venv/bin/python -m vaultnotes init --mock-run")
            return 1
        mock_tmp, mock_vault = _create_mock_vault()
        default_vault = str(mock_vault)

    try:
        use_tui = _use_tui()
        if not use_tui:
            _print_init_banner()
        _log("Interactive setup" + (" (mock run)" if getattr(args, "mock_run", False) else ""))
        if getattr(args, "mock_run", False):
            _log("Using a temporary demo vault. No config, repo, notes, or schedule will be changed.")
            _log("")

        if use_tui:
            result = _run_tabbed_init(default_vault)
            if result is None:
                _log("Setup aborted. No config was written.")
                return 1
            if result.get("mode") == "command":
                argv = result.get("argv", [])
                if not isinstance(argv, list):
                    _log("Could not run command: invalid command payload.")
                    return 1
                _log("")
                _log("Selected: vaultnotes " + " ".join(str(x) for x in argv))
                if getattr(args, "mock_run", False):
                    _log("Mock run complete. Command was not executed.")
                    return 0
                return main([str(x) for x in argv])
            if result.get("mode") != "setup":
                _log("Setup aborted. No config was written.")
                return 1
            vault_path, projects, site, repo, sched_time = result["payload"]  # type: ignore[misc]
        else:
            vault_path = _prompt_vault_path(default_vault)
            if vault_path is None:
                return 1

            projects = _prompt_projects(vault_path)
            if projects is None:
                _log("No folders selected. Aborting.")
                return 1

            site = _prompt_site_settings()
            repo = _prompt_github_repo()
            sched_time = _prompt_schedule_time()

            while True:
                action = _review_setup(vault_path, projects, site, repo, sched_time)
                if action in {"", "c", "continue", "y", "yes"}:
                    break
                if action in {"a", "abort", "q", "quit", "n", "no"}:
                    _log("Setup aborted. No config was written.")
                    return 1
                if action in {"v", "vault"}:
                    new_vault_path = _prompt_vault_path(str(vault_path))
                    if new_vault_path is not None:
                        vault_path = new_vault_path
                        new_projects = _prompt_projects(vault_path)
                        if new_projects is not None:
                            projects = new_projects
                    continue
                if action in {"p", "project", "projects", "folders"}:
                    new_projects = _prompt_projects(vault_path)
                    if new_projects is not None:
                        projects = new_projects
                    continue
                if action in {"s", "site"}:
                    site = _prompt_site_settings()
                    continue
                if action in {"g", "github", "repo"}:
                    repo = _prompt_github_repo()
                    continue
                if action in {"t", "time", "schedule"}:
                    sched_time = _prompt_schedule_time()
                    continue
                if action in {"all", "restart"}:
                    new_vault_path = _prompt_vault_path(str(vault_path))
                    if new_vault_path is None:
                        continue
                    new_projects = _prompt_projects(new_vault_path)
                    if new_projects is None:
                        continue
                    vault_path = new_vault_path
                    projects = new_projects
                    site = _prompt_site_settings()
                    repo = _prompt_github_repo()
                    sched_time = _prompt_schedule_time()
                    continue
                _log("Choose continue, vault, projects, site, github, schedule, all, or abort.")

        if getattr(args, "mock_run", False):
            _log("")
            _log("Mock run complete. No config, repo, notes, or schedule was touched.")
            return 0

        # Render config
        tmpl = resources.files("vaultnotes.templates").joinpath("config.yaml.tmpl").read_text()
        body = (
            tmpl
            .replace("{{SITE_TITLE}}", site["title"])
            .replace("{{WORDMARK}}", site["wordmark"])
            .replace("{{THEME}}", site["theme"])
            .replace("{{ACCENT}}", site["accent"])
            .replace("{{VAULT_PATH}}", str(vault_path))
            .replace("{{PROJECTS_BLOCK}}", "\n".join(_project_blocks(projects)))
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
    finally:
        if mock_tmp is not None:
            mock_tmp.cleanup()


# ── add ─────────────────────────────────────────────────────────────────────
def cmd_add(args: argparse.Namespace) -> int:
    import yaml

    folder = args.folder.strip()
    if not folder:
        _log("folder name required")
        return 1

    cfg_path = cfgmod.CONFIG_PATH
    if not cfg_path.exists():
        _log(f"No config at {cfg_path}. Run `vaultnotes init` first.")
        return 1

    raw = cfg_path.read_text()
    data = yaml.safe_load(raw) or {}
    projects = data.setdefault("projects", [])

    if any(p.get("folder") == folder for p in projects):
        _log(f"Project '{folder}' already in config.")
        return 1

    cfg = cfgmod.load()
    folder_path = cfg.vault_path / folder
    if not folder_path.is_dir():
        _log(f"Folder not found in vault: {folder_path}")
        _log("Create it in Obsidian first, then rerun.")
        return 1

    color = args.color or PALETTE[len(projects) % len(PALETTE)]
    if not cfgmod.HEX_RE.match(color):
        _log(f"color must be 6-digit hex (e.g. #f5a833), got {color}")
        return 1
    label = args.label or folder
    description = args.description or ""

    entry = {"folder": folder, "label": label, "color": color}
    if description:
        entry["description"] = description
    projects.append(entry)

    cfg_path.write_text(yaml.safe_dump(data, sort_keys=False, width=80))
    _log(f"Added project '{folder}' (label={label}, color={color}).")

    cfg = cfgmod.load()
    errs = cfgmod.validate(cfg)
    if errs:
        _log("Config validation:")
        for e in errs:
            _log(f"  - {e}")
        return 1

    if args.no_sync:
        _log("Run `vaultnotes sync` to publish the new folder.")
        return 0

    return cmd_sync(args)


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
        rag.enable(cfg, cfg.local_clone)

    build.build(cfg, cfg.local_clone)
    _log("  notes.html built")

    errs = integrity.check(cfg, cfg.local_clone)
    if errs:
        _log("Integrity check FAILED — not pushing:")
        for e in errs:
            _log(f"  {e}")
        return 2

    paths = ["notes", "notes.html"]
    if cfg.rag.enabled:
        paths += [
            "chat", "worker", "scripts", ".github",
            "package.json", "rag-config.json", ".gitignore",
        ]

    msg = f"vaultnotes sync — {dt.datetime.now():%Y-%m-%d %H:%M:%S}"
    pushed = github.commit_and_push(cfg.local_clone, msg, cfg.github_branch, paths=paths)
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

    if args.action == "deploy-worker":
        worker_dir = cfg.local_clone / "worker"
        if not worker_dir.is_dir():
            _log(f"worker/ not found in {cfg.local_clone}.")
            _log("Run `vaultnotes rag enable` first.")
            return 1
        if not shutil.which("npx"):
            _log("npx not found. Install Node.js first: brew install node")
            return 1
        if not (worker_dir / "node_modules").exists():
            _log("Installing wrangler (one-time)...")
            try:
                subprocess.check_call(["npm", "install"], cwd=worker_dir)
            except subprocess.CalledProcessError as e:
                _log(f"npm install failed (exit {e.returncode}).")
                return e.returncode
        _log(f"Deploying worker from {worker_dir}")
        try:
            subprocess.check_call(["npx", "wrangler", "deploy"], cwd=worker_dir)
        except subprocess.CalledProcessError as e:
            _log(f"wrangler deploy failed (exit {e.returncode}).")
            _log("If this is the first deploy, run these once first:")
            _log(f"  cd {worker_dir}")
            _log("  npx wrangler login")
            _log("  npx wrangler secret put GEMINI_API_KEY")
            _log("  npx wrangler secret put CHAT_PASSWORD")
            return e.returncode
        _log("")
        _log("Deployed. Copy the URL Wrangler printed, then run:")
        _log("  vaultnotes rag set-worker-url <https://...workers.dev>")
        return 0

    if args.action == "secret":
        worker_dir = cfg.local_clone / "worker"
        if not worker_dir.is_dir():
            _log(f"worker/ not found in {cfg.local_clone}. Run `vaultnotes rag enable` first.")
            return 1
        if not args.url:  # reused positional carries the secret name
            _log("Usage: vaultnotes rag secret <SECRET_NAME>")
            _log("Common names: GEMINI_API_KEY, CHAT_PASSWORD")
            return 1
        if not shutil.which("npx"):
            _log("npx not found. Install Node.js first: brew install node")
            return 1
        try:
            subprocess.check_call(["npx", "wrangler", "secret", "put", args.url], cwd=worker_dir)
        except subprocess.CalledProcessError as e:
            return e.returncode
        return 0

    if args.action == "disable":
        rag.update_user_config(enabled=False)
        _log("RAG disabled in config. Generated files in the pages repo are left in place;")
        _log("delete chat/, worker/, scripts/, public/, .github/workflows/build-index.yml,")
        _log("rag-config.json, and root package.json by hand if you want them gone.")
        return 0

    return 1


# ── where ───────────────────────────────────────────────────────────────────
def cmd_where(args: argparse.Namespace) -> int:
    cfg = cfgmod.load()
    path = cfg.local_clone
    _log(str(path))
    if args.open:
        if not IS_MACOS:
            _log("--open is macOS-only.")
            return 1
        if not path.exists():
            _log("Path does not exist yet. Run `vaultnotes sync` first.")
            return 1
        subprocess.check_call(["open", str(path)])
    return 0


# ── upgrade ─────────────────────────────────────────────────────────────────
PACKAGE_GIT_URL = "git+https://github.com/MaleenKidiwela/vaultnotes.git"


def cmd_upgrade(args: argparse.Namespace) -> int:
    ref = (args.ref or "").strip()
    target = f"{PACKAGE_GIT_URL}@{ref}" if ref else PACKAGE_GIT_URL

    if not shutil.which("pipx"):
        _log("pipx not found on PATH.")
        _log(f"Install pipx (brew install pipx), or upgrade manually with:")
        _log(f"  pip install --force-reinstall {target}")
        return 1

    cmd = ["pipx", "install", "--force", target]
    _log(f"Running: {' '.join(cmd)}")
    try:
        subprocess.check_call(cmd)
    except subprocess.CalledProcessError as e:
        _log(f"pipx install failed (exit {e.returncode}).")
        return e.returncode

    _log("")
    _log("Upgraded. Your config, notes, and pages repo were not touched.")
    _log("Run `vaultnotes sync` to pick up any template improvements in notes.html.")
    return 0


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

    init = sub.add_parser("init", help="Interactive setup")
    init.add_argument(
        "--preview-ui",
        action="store_true",
        help="Show a sample setup UI without writing config or running setup",
    )
    init.add_argument(
        "--mock-run",
        action="store_true",
        help="Run the full interactive setup against a temporary demo vault, then exit without side effects",
    )
    init.set_defaults(func=cmd_init)
    sub.add_parser("sync", help="Sync vault → pages repo, build, push").set_defaults(func=cmd_sync)
    sub.add_parser("build", help="Rebuild notes.html only").set_defaults(func=cmd_build)
    sub.add_parser("doctor", help="Diagnose configuration").set_defaults(func=cmd_doctor)

    add = sub.add_parser("add", help="Add a project folder to publish")
    add.add_argument("folder", help="Top-level folder name inside the vault")
    add.add_argument("--label", help="Display label (defaults to folder name)")
    add.add_argument("--color", help="6-digit hex color (auto-picked if omitted)")
    add.add_argument("--description", help="One-line description")
    add.add_argument("--no-sync", action="store_true", help="Don't run sync after adding")
    add.set_defaults(func=cmd_add)

    sch = sub.add_parser("schedule", help="Manage daily launchd job")
    sch.add_argument("action", choices=["install", "uninstall", "status"])
    sch.set_defaults(func=cmd_schedule)

    rg = sub.add_parser("rag", help="Manage the RAG chat add-on")
    rg.add_argument(
        "action",
        choices=["enable", "set-worker-url", "deploy-worker", "secret", "disable"],
    )
    rg.add_argument(
        "url",
        nargs="?",
        help="Worker URL (for set-worker-url) or secret name (for secret)",
    )
    rg.set_defaults(func=cmd_rag)

    wh = sub.add_parser("where", help="Print the local pages-repo path")
    wh.add_argument("--open", action="store_true", help="Reveal in Finder (macOS)")
    wh.set_defaults(func=cmd_where)

    up = sub.add_parser("upgrade", help="Reinstall vaultnotes from GitHub via pipx")
    up.add_argument("--ref", help="Branch, tag, or commit (default: main)")
    up.set_defaults(func=cmd_upgrade)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
