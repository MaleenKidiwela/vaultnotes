from __future__ import annotations

import shutil
import subprocess
from importlib import resources
from pathlib import Path

from vaultnotes.config import Config

LABEL = "com.vaultnotes.sync"
PLIST_PATH = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"
LOG_PATH = Path.home() / "Library" / "Logs" / "vaultnotes.log"


def _plist_body(cfg: Config) -> str:
    tmpl = resources.files("vaultnotes.templates").joinpath("launchd.plist.tmpl").read_text()
    bin_path = shutil.which("vaultnotes") or "/usr/local/bin/vaultnotes"
    h, m = cfg.hour_minute()
    return (
        tmpl
        .replace("{{BIN}}", bin_path)
        .replace("{{HOUR}}", str(h))
        .replace("{{MINUTE}}", str(m))
        .replace("{{LOG}}", str(LOG_PATH))
    )


def install(cfg: Config) -> Path:
    PLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    if PLIST_PATH.exists():
        subprocess.run(["launchctl", "unload", str(PLIST_PATH)], capture_output=True)
    PLIST_PATH.write_text(_plist_body(cfg))
    subprocess.check_call(["launchctl", "load", str(PLIST_PATH)])
    return PLIST_PATH


def uninstall() -> bool:
    if not PLIST_PATH.exists():
        return False
    subprocess.run(["launchctl", "unload", str(PLIST_PATH)], capture_output=True)
    PLIST_PATH.unlink()
    return True


def status() -> dict:
    info = {"plist": str(PLIST_PATH), "installed": PLIST_PATH.exists()}
    if PLIST_PATH.exists():
        r = subprocess.run(
            ["launchctl", "list", LABEL], capture_output=True, text=True,
        )
        info["launchctl"] = r.stdout.strip() or r.stderr.strip()
    if LOG_PATH.exists():
        info["log_tail"] = "\n".join(LOG_PATH.read_text().splitlines()[-20:])
    return info
