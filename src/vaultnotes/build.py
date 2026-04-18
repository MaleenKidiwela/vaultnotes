from __future__ import annotations

import json
import re
import textwrap
from importlib import resources
from pathlib import Path

from vaultnotes.config import Config, Project


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


def _root_vars(cfg: Config) -> str:
    t = cfg.theme_colors
    accent = t["accent"]
    lines = [
        f"  --bg:             {t['bg']};",
        f"  --bg2:            {t['bg2']};",
        f"  --bg3:            {t['bg3']};",
        f"  --bg4:            {t['bg4']};",
        f"  --border:         {t['border']};",
        f"  --border-2:       {t['border_2']};",
        "",
        f"  --accent:         {accent};",
        f"  --accent-dim:     {t['accent_dim']};",
        f"  --accent-glow:    {glow(accent)};",
        "",
    ]
    for p in cfg.projects:
        key = _slug(p.folder)
        lines += [
            f"  --{key}:          {p.color};",
            f"  --{key}-dim:      {dim(p.color)};",
            f"  --{key}-glow:     {glow(p.color)};",
        ]
    lines += [
        "",
        f"  --text:       {t['text']};",
        f"  --text-2:     {t['text_2']};",
        f"  --text-3:     {t['text_3']};",
        f"  --text-muted: {t['text_muted']};",
    ]
    return "\n".join(lines)


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
            f".dot-{key} {{ background: var(--{key}); box-shadow: 0 0 7px var(--{key}); }}"
        )
    return "\n".join(lines)


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
    lines = ["{"]
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
    lines.append("}")
    return "\n".join(lines)


def _wordmark_html(cfg: Config) -> str:
    return f'{cfg.wordmark}<span class="accent-dot"> · </span>Notes'


def _load_template() -> str:
    return resources.files("vaultnotes.templates").joinpath("notes.html.tmpl").read_text()


def render(cfg: Config, pages_repo: Path) -> str:
    tmpl = _load_template()
    subs = {
        "SITE_TITLE": cfg.site_title,
        "WORDMARK_HTML": _wordmark_html(cfg),
        "ROOT_VARS": _root_vars(cfg),
        "PROJECT_TAB_CSS": _project_tab_css(cfg),
        "PROJECT_DOT_CSS": _project_dot_css(cfg),
        "PROJECT_TABS": _project_tabs_html(cfg),
        "PROJECTS_JS": _projects_js(cfg, pages_repo),
        "FIRST_PROJECT_JS": json.dumps(cfg.projects[0].folder if cfg.projects else "Dashboard"),
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
