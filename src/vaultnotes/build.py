from __future__ import annotations

import json
import re
import textwrap
from importlib import resources
from pathlib import Path

from vaultnotes.config import THEMES, Config, Project


def _hex_to_rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _rgb_to_hex(rgb: tuple[int, int, int]) -> str:
    r, g, b = (max(0, min(255, int(x))) for x in rgb)
    return f"#{r:02x}{g:02x}{b:02x}"


def dim(hex_color: str, factor: float = 0.4) -> str:
    r, g, b = _hex_to_rgb(hex_color)
    return _rgb_to_hex((r * factor, g * factor, b * factor))


def glow(hex_color: str, alpha: float = 0.10) -> str:
    r, g, b = _hex_to_rgb(hex_color)
    return f"rgba({r}, {g}, {b}, {alpha:.2f})"


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", s.lower())


def _theme_vars(t: dict[str, str], accent_override: str | None = None) -> str:
    accent = accent_override or t["accent"]
    accent_glow = glow(accent) if accent_override else t["accent_glow"]
    return "\n".join([
        f"  --bg:             {t['bg']};",
        f"  --bg2:            {t['bg2']};",
        f"  --bg3:            {t['bg3']};",
        f"  --bg4:            {t['bg4']};",
        f"  --border:         {t['border']};",
        f"  --border-2:       {t['border_2']};",
        f"  --accent:         {accent};",
        f"  --accent-dim:     {t['accent_dim']};",
        f"  --accent-glow:    {accent_glow};",
        f"  --text:           {t['text']};",
        f"  --text-2:         {t['text_2']};",
        f"  --text-3:         {t['text_3']};",
        f"  --text-muted:     {t['text_muted']};",
    ])


def _theme_css(cfg: Config) -> str:
    default_theme = cfg.theme
    other_theme = "paper" if default_theme == "midnight" else "midnight"

    project_lines = []
    for p in cfg.projects:
        key = _slug(p.folder)
        project_lines += [
            f"  --{key}:          {p.color};",
            f"  --{key}-dim:      {dim(p.color)};",
            f"  --{key}-glow:     {glow(p.color)};",
        ]
    project_block = "\n".join(project_lines)

    statics = "\n".join([
        "  --sidebar-w: 304px;",
        "  --header-h:  58px;",
        "  --font-sans: 'Geist', -apple-system, BlinkMacSystemFont, sans-serif;",
        "  --font-mono: 'Geist Mono', 'SF Mono', Menlo, monospace;",
        "  --font-serif: 'Fraunces', Georgia, serif;",
        "  --radius-sm: 6px;",
        "  --radius-md: 10px;",
        "  --radius-lg: 14px;",
        "  --shadow-diffuse: 0 20px 40px -18px rgba(0,0,0,0.55), 0 1px 0 rgba(255,255,255,0.03) inset;",
    ])

    default_vars = _theme_vars(THEMES[default_theme], cfg.accent)
    other_vars = _theme_vars(THEMES[other_theme])

    return (
        f":root, :root[data-theme=\"{default_theme}\"] {{\n"
        f"{project_block}\n\n"
        f"{statics}\n\n"
        f"{default_vars}\n"
        f"}}\n\n"
        f":root[data-theme=\"{other_theme}\"] {{\n"
        f"{other_vars}\n"
        f"}}"
    )


def _project_tab_css(cfg: Config) -> str:
    blocks = []
    for p in cfg.projects:
        key = _slug(p.folder)
        blocks.append(
            f'.tab[data-project="{p.folder}"].active {{\n'
            f"  color: var(--{key});\n"
            f"  background: var(--{key}-glow);\n"
            f"  border-color: var(--{key}-dim);\n"
            "}"
        )
    return "\n\n".join(blocks)


def _project_dot_css(cfg: Config) -> str:
    lines = []
    for p in cfg.projects:
        key = _slug(p.folder)
        lines.append(
            f".dot-{key} {{ background: var(--{key}); "
            f"box-shadow: 0 0 0 2px var(--{key}-glow); }}"
        )
    return "\n".join(lines)


def _project_hero_css(cfg: Config) -> str:
    lines = []
    for p in cfg.projects:
        key = _slug(p.folder)
        lines.append(
            f".project-hero.{key}::before {{ "
            f"background: linear-gradient(90deg, transparent, var(--{key}) 40%, "
            f"var(--{key}) 60%, transparent); opacity: 0.7; }}"
        )
    return "\n".join(lines)


def _project_card_label_css(cfg: Config) -> str:
    return "\n".join(
        f".card-label.{_slug(p.folder)} {{ color: var(--{_slug(p.folder)}); }}"
        for p in cfg.projects
    )


def _project_landing_file_css(cfg: Config) -> str:
    return "\n".join(
        f".landing-file.{_slug(p.folder)}:hover .landing-file-arrow "
        f"{{ color: var(--{_slug(p.folder)}); }}"
        for p in cfg.projects
    )


def _project_tabs_html(cfg: Config) -> str:
    parts = []
    for p in cfg.projects:
        key = _slug(p.folder)
        parts.append(
            f'    <button class="tab" data-project="{p.folder}" '
            f"onclick=\"selectProject('{p.folder}', this)\">\n"
            f'      <span class="tab-dot dot-{key}"></span>{p.label}\n'
            "    </button>"
        )
    return "\n".join(parts)


def _scan_project_files(pages_repo: Path, project_folder: str) -> list[str]:
    d = pages_repo / "notes" / project_folder
    if not d.is_dir():
        return []
    return sorted(f.name for f in d.iterdir() if f.suffix in {".md", ".html"})


def _projects_js(cfg: Config, pages_repo: Path) -> str:
    obj = {}
    for p in cfg.projects:
        files = _scan_project_files(pages_repo, p.folder)
        obj[p.folder] = {
            "label": p.label,
            "color": _slug(p.folder),
            "accent": p.color,
            "desc": p.description,
            "files": files,
        }
    # Pretty print for readability + preserve AUTO-FILES markers for integrity check.
    lines = []
    for proj, data in obj.items():
        lines.append(f"  {json.dumps(proj)}: {{")
        lines.append(f"    label:  {json.dumps(data['label'])},")
        lines.append(f"    color:  {json.dumps(data['color'])},")
        lines.append(f"    accent: {json.dumps(data['accent'])},")
        lines.append(f"    desc:   {json.dumps(data['desc'])},")
        lines.append(f"    files: [ // AUTO-FILES:{proj}:START")
        for fn in data["files"]:
            lines.append(f"      {json.dumps(fn)},")
        lines.append(f"    ] // AUTO-FILES:{proj}:END")
        lines.append("  },")
    return "\n".join(lines)


def _wordmark_html(cfg: Config) -> str:
    return (
        f'<a href="index.html" class="wordmark">{cfg.wordmark}'
        f'<span class="accent-dot"> · </span>Notes</a>'
    )


def _load_template() -> str:
    return resources.files("vaultnotes.templates").joinpath("notes.html.tmpl").read_text()


def render(cfg: Config, pages_repo: Path) -> str:
    tmpl = _load_template()
    subs = {
        "SITE_TITLE": cfg.site_title,
        "WORDMARK_HTML": _wordmark_html(cfg),
        "THEME_CSS": _theme_css(cfg),
        "DEFAULT_THEME": cfg.theme,
        "PROJECT_TAB_CSS": _project_tab_css(cfg),
        "PROJECT_DOT_CSS": _project_dot_css(cfg),
        "PROJECT_HERO_CSS": _project_hero_css(cfg),
        "PROJECT_CARD_LABEL_CSS": _project_card_label_css(cfg),
        "PROJECT_LANDING_FILE_CSS": _project_landing_file_css(cfg),
        "PROJECT_TABS": _project_tabs_html(cfg),
        "PROJECTS_JS": _projects_js(cfg, pages_repo),
        "FIRST_PROJECT_JS": cfg.projects[0].folder if cfg.projects else "Dashboard",
    }
    out = tmpl
    for k, v in subs.items():
        out = out.replace("{{" + k + "}}", v)
    return out


def build(cfg: Config, pages_repo: Path) -> Path:
    html = render(cfg, pages_repo)
    target = pages_repo / "notes.html"
    target.write_text(html)
    return target
