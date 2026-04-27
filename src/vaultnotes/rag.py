from __future__ import annotations

import json
import re
from importlib import resources
from pathlib import Path

import yaml

from vaultnotes.config import CONFIG_PATH, Config

_BINARY_SUFFIXES = {".svg", ".bin", ".png", ".jpg", ".jpeg", ".gif", ".ico"}

# Files copied verbatim from templates/rag into the pages repo.
_VERBATIM_FILES: dict[str, str] = {
    "chat/index.html": "chat/index.html",
    "chat/chat.css": "chat/chat.css",
    "chat/chat.js": "chat/chat.js",
    "chat/fox.svg": "chat/fox.svg",
    "scripts/index-notes.mjs": "scripts/index-notes.mjs",
    "package.json": "package.json",
    "worker/package.json": "worker/package.json",
    "worker/src/index.js": "worker/src/index.js",
    # Path mappings where source has a leading-dot or template name change.
    "worker/.dev.vars.example": "worker/dev.vars.example",
    ".github/workflows/build-index.yml": "github_workflows/build-index.yml",
}

_TEMPLATED_FILES: dict[str, str] = {
    "chat/config.json": "chat/config.json.tmpl",
    "worker/wrangler.toml": "worker/wrangler.toml.tmpl",
}


def _template(rel: str):
    """Return a Traversable resource under templates/rag/<rel>."""
    parts = rel.split("/")
    node = resources.files("vaultnotes.templates").joinpath("rag")
    for p in parts:
        node = node.joinpath(p)
    return node


def _read_template_text(rel: str) -> str:
    return _template(rel).read_text()


def _read_template_bytes(rel: str) -> bytes:
    return _template(rel).read_bytes()


def _write(dst: Path, content: str | bytes) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        dst.write_bytes(content)
    else:
        dst.write_text(content)


def _slug_repo(repo: str) -> str:
    """Cloudflare worker names: lowercase, alphanumerics + dashes, max 63 chars."""
    name = re.sub(r"[^a-z0-9-]+", "-", repo.lower())
    name = re.sub(r"-{2,}", "-", name).strip("-")
    return f"rag-{name}"[:63]


def _allowed_origin(repo: str) -> str:
    """Best-guess Pages origin from owner/name. Users can edit later."""
    if "/" not in repo:
        return "*"
    owner, name = repo.split("/", 1)
    # `<user>/<user>.github.io` → `https://<user>.github.io`
    if name.lower() == f"{owner.lower()}.github.io":
        return f"https://{owner.lower()}.github.io"
    # Project pages: `<user>.github.io/<name>` — origin is still `<user>.github.io`.
    return f"https://{owner.lower()}.github.io"


def _render_template(rel: str, mapping: dict[str, str]) -> str:
    body = _read_template_text(rel)
    for k, v in mapping.items():
        body = body.replace("{{" + k + "}}", v)
    return body


def write_rag_config_json(cfg: Config, pages_repo: Path) -> Path:
    """Write rag-config.json (folders list etc.) at the repo root.

    Called by `vaultnotes rag enable` and on every `vaultnotes sync` so the
    indexer always sees the current project list.
    """
    if not cfg.rag.enabled:
        return pages_repo / "rag-config.json"
    data = {
        "folders": [p.folder for p in cfg.projects],
        "embedDim": 768,
        "chunkWords": 375,
        "overlapWords": 75,
        "batchSize": 25,
    }
    target = pages_repo / "rag-config.json"
    _write(target, json.dumps(data, indent=2) + "\n")
    return target


def write_chat_config(cfg: Config, pages_repo: Path) -> Path:
    target = pages_repo / "chat" / "config.json"
    body = _render_template(
        "chat/config.json.tmpl",
        {"WORKER_URL": cfg.rag.worker_url or ""},
    )
    _write(target, body)
    return target


def _ensure_gitignore(pages_repo: Path) -> None:
    gi = pages_repo / ".gitignore"
    needed = ["node_modules/", "worker/.dev.vars", "worker/.wrangler/", "worker/node_modules/"]
    existing = gi.read_text().splitlines() if gi.exists() else []
    additions = [n for n in needed if n not in existing]
    if not additions:
        return
    out = "\n".join(existing + additions).strip() + "\n"
    gi.write_text(out)


def enable(cfg: Config, pages_repo: Path, *, password: str | None = None) -> dict[str, Path]:
    """Copy RAG templates into the pages repo and write per-user config files.

    Does NOT touch git (no commit/push). Returns a map of label -> path written.
    """
    written: dict[str, Path] = {}

    for dst_rel, src_rel in _VERBATIM_FILES.items():
        dst = pages_repo / dst_rel
        if Path(src_rel).suffix in _BINARY_SUFFIXES:
            _write(dst, _read_template_bytes(src_rel))
        else:
            _write(dst, _read_template_text(src_rel))
        written[dst_rel] = dst

    worker_name = _slug_repo(cfg.github_repo)
    allowed_origin = _allowed_origin(cfg.github_repo)
    wrangler = _render_template(
        "worker/wrangler.toml.tmpl",
        {"WORKER_NAME": worker_name, "ALLOWED_ORIGIN": allowed_origin},
    )
    target = pages_repo / "worker" / "wrangler.toml"
    _write(target, wrangler)
    written["worker/wrangler.toml"] = target

    written["chat/config.json"] = write_chat_config(cfg, pages_repo)
    written["rag-config.json"] = write_rag_config_json(cfg, pages_repo)

    _ensure_gitignore(pages_repo)
    return written


def update_user_config(
    cfg_path: Path = CONFIG_PATH,
    *,
    enabled: bool | None = None,
    worker_url: str | None = None,
) -> None:
    """Patch the user's YAML config to add/update the rag block."""
    if not cfg_path.exists():
        raise FileNotFoundError(f"No config at {cfg_path}. Run `vaultnotes init` first.")
    raw = cfg_path.read_text()
    data = yaml.safe_load(raw) or {}
    rag = data.get("rag", {}) or {}
    if enabled is not None:
        rag["enabled"] = bool(enabled)
    if worker_url is not None:
        rag["worker_url"] = worker_url
    data["rag"] = rag
    cfg_path.write_text(yaml.safe_dump(data, sort_keys=False, width=80))


def next_steps_message(cfg: Config, pages_repo: Path) -> str:
    repo = cfg.github_repo
    worker_name = _slug_repo(repo)
    return _NEXT_STEPS.format(
        repo=repo,
        worker=worker_name,
        pages=pages_repo,
    ).strip()


_NEXT_STEPS = """
RAG files added to {pages}.

To finish, you need a Google AI Studio API key and a Cloudflare account:

  1. Create the Gemini API key at https://aistudio.google.com/apikey

  2. Add it as a Repository secret named GEMINI_API_KEY:
       https://github.com/{repo}/settings/secrets/actions/new
     Use the "Secrets" tab (not "Variables"). Workflow permissions also need
     "Read and write" enabled at:
       https://github.com/{repo}/settings/actions

  3. Deploy the Cloudflare Worker (only needed once, plus on each worker code change):
       cd worker
       npm install
       npx wrangler login
       npx wrangler secret put GEMINI_API_KEY     # paste the same key
       npx wrangler secret put CHAT_PASSWORD      # any string you'll share
       npx wrangler deploy
     Wrangler will print a URL like:
       https://{worker}.<your-account>.workers.dev

  4. Save the URL into vaultnotes:
       vaultnotes rag set-worker-url https://{worker}.<your-account>.workers.dev

  5. Commit and push the new files (vaultnotes sync will do it on the next run):
       vaultnotes sync

After step 5, the GitHub Action 'Build RAG index' will run, embed your notes,
and publish /public/. The chat will be live at:
  https://<your-pages-domain>/chat/
"""
