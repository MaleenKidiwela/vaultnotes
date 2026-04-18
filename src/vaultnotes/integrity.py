from __future__ import annotations

import json
import re
from pathlib import Path

from vaultnotes.config import Config

REQUIRED_JS_FNS = [
    "loadFile", "renderCalendar", "showDashboard",
    "selectProject", "selectCalDate",
]


def check(cfg: Config, pages_repo: Path) -> list[str]:
    errors: list[str] = []
    notes_html = pages_repo / "notes.html"

    if not notes_html.exists():
        errors.append("FAIL: notes.html missing")
        return errors

    size = notes_html.stat().st_size
    if size < 10_000:
        errors.append(f"FAIL: notes.html suspiciously small ({size} bytes)")
        return errors

    html = notes_html.read_text()

    for p in cfg.projects:
        start = f"// AUTO-FILES:{p.folder}:START"
        end = f"// AUTO-FILES:{p.folder}:END"
        if start not in html:
            errors.append(f"FAIL: missing marker {start}")
        if end not in html:
            errors.append(f"FAIL: missing marker {end}")

    for p in cfg.projects:
        disk_dir = pages_repo / "notes" / p.folder
        disk_files = (
            sorted(f.name for f in disk_dir.iterdir() if f.suffix in {".md", ".html"})
            if disk_dir.is_dir() else []
        )
        start = f"// AUTO-FILES:{p.folder}:START"
        end = f"// AUTO-FILES:{p.folder}:END"
        s, e = html.find(start), html.find(end)
        if s == -1 or e == -1:
            continue
        block = html[s:e]
        raw = re.findall(r'"([^"]+\.(?:md|html))"', block)
        html_files = []
        for r in raw:
            try:
                html_files.append(json.loads(f'"{r}"'))
            except Exception:
                html_files.append(r)
        if len(html_files) != len(disk_files):
            errors.append(
                f"FAIL: {p.folder} count mismatch — disk:{len(disk_files)} html:{len(html_files)}"
            )
        for fn in html_files:
            if not (disk_dir / fn).exists():
                errors.append(f"FAIL: listed file missing on disk — {p.folder}/{fn}")

    for fn_name in REQUIRED_JS_FNS:
        if fn_name not in html:
            errors.append(f"FAIL: JS function '{fn_name}' missing from notes.html")

    return errors
