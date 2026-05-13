# vaultnotes

Publish selected folders of an Obsidian vault as a browsable GitHub Pages site — with wikilinks, math, a daily-notes calendar, and a daily auto-sync. Optional password-gated RAG chat answers questions from the synced notes. macOS, one command.

## Prerequisites

1. Install [Obsidian](https://obsidian.md) and create a vault (e.g. `~/Documents/Obsidian Vault/`).
2. Create one subfolder per project you want to publish (e.g. `Research/`, `SideQuest/`).

## Install

```bash
curl -fsSL https://raw.githubusercontent.com/MaleenKidiwela/vaultnotes/main/install.sh | bash
```

The installer handles Homebrew, Python, `pipx`, `git`, `gh`, vaultnotes itself, and the GitHub login — then drops you into the interactive setup.
It checks whether the core tools are already installed, skips them when present, and proceeds directly to installing or upgrading vaultnotes.

Faster manual path when Homebrew, `pipx`, `git`, and `gh` are already installed:

```bash
pipx install git+https://github.com/MaleenKidiwela/vaultnotes.git
gh auth login
vaultnotes init
```

Full manual equivalent:

```bash
brew install python pipx git gh
pipx install git+https://github.com/MaleenKidiwela/vaultnotes.git
gh auth login
vaultnotes init
```

## What `vaultnotes init` does

- Opens with Setup and Update choices.
- Setup uses a tabbed terminal form for vault path, folders, project details, site settings, GitHub, and review.
- Update shows command tabs for maintenance commands such as sync, doctor, RAG setup, where, and upgrade. Each tab includes editable fields where needed and a `Run command` action.
- Asks which vault folders to publish.
- Asks for your GitHub username and derives `<you>/<you>.github.io`.
- Shows a review screen so you can edit answers before anything is written.
- Creates the repo via `gh` if it doesn't exist, enables Pages.
- Builds `notes.html` and pushes the first sync.
- Offers to install a daily launchd job so syncs run automatically.

## Setup UI preview

```text
╭────────────────────────────────────────────╮
│  ▗▖      ▗▖   Vaultnotes                  │
│  ▜█▙    ▟█▛   Publish your notes,         │
│   ▜█▙▄▄▟█▛    keep control.               │
│    ▐█▌▐█▌     Guided setup                │
│     ▜██▛      GitHub Pages sync           │
╰────────────────────────────────────────────╯

Choose what you want to do

  › Setup   Configure publishing for the first time
    Update  Browse maintenance and update commands

Setup

  [Vault]  [Folders]  [Projects]  [Site]  [GitHub]  [Review]

  Path to your Obsidian vault
  ~/Documents/Obsidian Vault

Folders

  Use ↑/↓ to move and Space to select

  › ● Research
    ● SideQuest
    ○ Archive

Review

  Vault:    ~/Documents/Obsidian Vault
  Site:     Research Notes / JD / midnight
  GitHub:   janedoe/janedoe.github.io
  Schedule: 17:00

  › Continue
    Edit vault path
    Edit projects
    Edit site settings
    Edit GitHub repo
    Abort
```

## Commands

| Command | Purpose |
|---|---|
| `vaultnotes init` | Interactive first-time setup |
| `vaultnotes sync` | Sync vault → pages repo → push |
| `vaultnotes build` | Rebuild `notes.html` only |
| `vaultnotes add <folder>` | Add a vault folder to publish (auto-picks color, syncs by default) |
| `vaultnotes schedule install` | Install daily launchd job |
| `vaultnotes schedule uninstall` | Remove daily job |
| `vaultnotes schedule status` | Show job + log tail |
| `vaultnotes rag enable` | Add a password-gated chat that answers questions over your notes |
| `vaultnotes rag secret <NAME>` | Run `wrangler secret put` from the worker dir |
| `vaultnotes rag deploy-worker` | Run `wrangler deploy` from the worker dir |
| `vaultnotes rag set-worker-url <url>` | Save the deployed Worker URL into the chat |
| `vaultnotes rag disable` | Stop emitting the chat link in `notes.html` |
| `vaultnotes where [--open]` | Print (or reveal in Finder) the local pages-repo path |
| `vaultnotes doctor` | Validate config and dependencies |
| `vaultnotes upgrade` | Reinstall the latest vaultnotes package through `pipx` |

## Config

Lives at `~/.config/vaultnotes/config.yaml`. Edit freely, then `vaultnotes sync` to apply.

```yaml
site:
  title: "Research Notes"
  wordmark: "JD"
  theme: midnight       # midnight | paper
projects:
  - folder: "Research"
    label: "Research"
    color: "#f5a833"
github:
  repo: "janedoe/janedoe.github.io"
schedule:
  enabled: true
  time: "17:00"
```

Only listed folders are synced — everything else in the vault stays private.

## Optional: chat over your notes (RAG)

Add a password-gated chat at `/chat/` that answers questions grounded in the notes you publish. Anonymous visitors still browse the rest of the site normally; only the chat panel is behind the password.

Architecture: a GitHub Action embeds your notes with Gemini and writes a small index to `public/`; a tiny Cloudflare Worker proxies queries to Gemini using your API key; the browser does the retrieval locally and streams the answer back.

The RAG indexer is section-aware: it parses Markdown headings, keeps heading paths, splits large sections, folds tiny same-section fragments together, and stores note title, project, file path, section path, and filename date metadata with each chunk. The chat uses local hybrid retrieval over BM25, lexical matching, and embeddings, with same-section neighbor expansion for context.

Date questions are normalized before retrieval. Queries such as "what did we do yesterday", `04-04-26`, `2026-04-04`, `April 4th`, `Apr 4`, and `4th of April` are expanded to the indexed `YYYY-MM-DD` date when possible.

The Worker has a Gemini chat model fallback chain. If a chat model is unavailable or quota-limited, it tries the next configured model before returning an error.

### Claude (bring-your-own-key)

The chat model dropdown also offers two **bring-your-own-key (BYOK)** Claude options — `claude-sonnet-4-6` and `claude-opus-4-7`. Selecting one prompts for an Anthropic API key (`sk-ant-...`) that is stored only in the browser's `localStorage` and sent straight to `api.anthropic.com` from the page using the `anthropic-dangerous-direct-browser-access` header. The Worker is bypassed for those calls, so no Anthropic key needs to live on Cloudflare or in GitHub. Embeddings still go through the Worker (Gemini). A "Change API key" button next to the dropdown lets you re-enter or clear the saved key.

You will need:
- A free Google AI Studio API key (https://aistudio.google.com/apikey).
- A Cloudflare account (free plan is fine).
- Node.js installed locally (Homebrew: `brew install node`).

### Enable

```bash
vaultnotes rag enable
```

This copies `chat/`, `worker/`, `scripts/`, a `.github/workflows/build-index.yml`, and a `rag-config.json` into your pages repo. It also flips `rag.enabled: true` in `~/.config/vaultnotes/config.yaml` so future syncs refresh the RAG templates, keep `rag-config.json` in step with your project list, and add an "Ask the notes" link to `notes.html`.

### Add the secrets

**1. GitHub repo secret** — used by the indexing Action.

Go to `https://github.com/<owner>/<repo>/settings/secrets/actions` → "New repository secret" → name `GEMINI_API_KEY` → paste your Google key. Use the **Secrets** tab, not Variables.

While you're there, set `Settings → Actions → General → Workflow permissions` to **Read and write permissions** so the Action can commit the rebuilt index back to `main`.

**2. Cloudflare Worker secrets** — used at chat time.

You don't need to find the worker folder yourself; vaultnotes drives wrangler from the right directory.

```bash
npx wrangler login                              # one-time browser auth
vaultnotes rag secret GEMINI_API_KEY            # paste the same Google key
vaultnotes rag secret CHAT_PASSWORD             # any string you'll share
vaultnotes rag deploy-worker
```

The deploy step prints a URL like `https://rag-<your-repo-slug>.<your-account>.workers.dev`. Save it back into vaultnotes:

```bash
vaultnotes rag set-worker-url https://rag-<your-repo-slug>.<your-account>.workers.dev
```

The local pages-repo lives at `~/.local/share/vaultnotes/pages-repo` by default. Run `vaultnotes where --open` to reveal it in Finder if you ever need to poke at the files directly.

### Push and use

```bash
vaultnotes sync
```

The Action runs (~1–2 min), embeds your notes, commits `public/`, and Pages redeploys. Open `https://<your-pages-domain>/chat/`, enter the chat password, and ask a question.

### How updates flow

Every `vaultnotes sync` (manual or via the daily launchd job) refreshes the notes in the pages repo, refreshes the RAG template files when RAG is enabled, and rewrites `rag-config.json`. If the sync pushes note or RAG-file changes, the Action rebuilds and republishes within a few minutes. New notes are answerable right after.

The indexer reuses embeddings for unchanged chunks by hashing each chunk's embedding input against the model, dimension, and task type. The first rebuild after a chunking/indexer upgrade may re-embed many chunks; later rebuilds should only embed new or changed chunks.

Worker code changes (anything under `worker/`) require running `vaultnotes rag deploy-worker` again — pushing to GitHub does not redeploy the Worker.

### Existing RAG users

If you already had RAG enabled, upgrade and sync:

```bash
vaultnotes upgrade
vaultnotes sync
vaultnotes rag deploy-worker
```

`vaultnotes sync` now refreshes the RAG templates automatically when `rag.enabled: true`, so existing users do not need to run `vaultnotes rag enable` again just to get updated indexer or chat files. Redeploying the Worker is needed for Worker-side changes such as model fallbacks.

### Costs

Embedding ~hundreds of chunks runs comfortably inside the Gemini free tier. The content-hash cache avoids re-embedding unchanged chunks after the first build with the current indexer. If your vault is bigger or you index frequently and start hitting `429 RESOURCE_EXHAUSTED`, enable billing on the Cloud project linked to your AI Studio key — the dollar cost on small vaults is effectively zero.

## Updates

```bash
vaultnotes upgrade
```

Re-fetches the latest version from GitHub via pipx. Equivalent to `pipx install --force git+https://github.com/MaleenKidiwela/vaultnotes.git`. Pin a specific version with `vaultnotes upgrade --ref v0.2.0`.

Upgrading does **not** touch your config, notes, pages repo, scheduled job, or RAG secrets — only the package code in `~/.local/pipx/venvs/vaultnotes/`. Site and RAG template improvements take effect on the next `vaultnotes sync`. If the update includes Worker changes, run `vaultnotes rag deploy-worker` after syncing.
