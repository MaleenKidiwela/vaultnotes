# vaultnotes

Publish selected folders of an Obsidian vault as a browsable GitHub Pages site — with wikilinks, math, a daily-notes calendar, and a daily auto-sync. macOS, one command.

## Prerequisites

1. Install [Obsidian](https://obsidian.md) and create a vault (e.g. `~/Documents/Obsidian Vault/`).
2. Create one subfolder per project you want to publish (e.g. `Research/`, `SideQuest/`).

## Install

```bash
curl -fsSL https://raw.githubusercontent.com/MaleenKidiwela/vaultnotes/main/install.sh | bash
```

The installer handles Homebrew, Python, `pipx`, `git`, `gh`, vaultnotes itself, and the GitHub login — then drops you into the interactive setup.

Manual equivalent:

```bash
brew install python pipx git gh
pipx install git+https://github.com/MaleenKidiwela/vaultnotes.git
gh auth login
vaultnotes init
```

## What `vaultnotes init` does

- Asks which vault folders to publish.
- Asks for a GitHub repo name (typically `<you>/<you>.github.io`).
- Creates the repo via `gh` if it doesn't exist, enables Pages.
- Builds `notes.html` and pushes the first sync.
- Offers to install a daily launchd job so syncs run automatically.

## Commands

| Command | Purpose |
|---|---|
| `vaultnotes init` | Interactive first-time setup |
| `vaultnotes sync` | Sync vault → pages repo → push |
| `vaultnotes build` | Rebuild `notes.html` only |
| `vaultnotes schedule install` | Install daily launchd job |
| `vaultnotes schedule uninstall` | Remove daily job |
| `vaultnotes schedule status` | Show job + log tail |
| `vaultnotes rag enable` | Add a password-gated chat that answers questions over your notes |
| `vaultnotes rag set-worker-url <url>` | Save the deployed Worker URL into the chat |
| `vaultnotes rag disable` | Stop emitting the chat link in `notes.html` |
| `vaultnotes doctor` | Validate config and dependencies |

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

You will need:
- A free Google AI Studio API key (https://aistudio.google.com/apikey).
- A Cloudflare account (free plan is fine).
- Node.js installed locally (Homebrew: `brew install node`).

### Enable

```bash
vaultnotes rag enable
```

This copies `chat/`, `worker/`, `scripts/`, a `.github/workflows/build-index.yml`, and a `rag-config.json` into your pages repo. It also flips `rag.enabled: true` in `~/.config/vaultnotes/config.yaml` so future syncs keep `rag-config.json` in step with your project list and add an "Ask the notes" link to `notes.html`.

### Add the secrets

**1. GitHub repo secret** — used by the indexing Action.

Go to `https://github.com/<owner>/<repo>/settings/secrets/actions` → "New repository secret" → name `GEMINI_API_KEY` → paste your Google key. Use the **Secrets** tab, not Variables.

While you're there, set `Settings → Actions → General → Workflow permissions` to **Read and write permissions** so the Action can commit the rebuilt index back to `main`.

**2. Cloudflare Worker secrets** — used at chat time.

```bash
cd <local pages repo>/worker
npm install
npx wrangler login
npx wrangler secret put GEMINI_API_KEY      # paste the same Google key
npx wrangler secret put CHAT_PASSWORD       # any string you'll share
npx wrangler deploy
```

Wrangler prints a URL like `https://rag-<your-repo-slug>.<your-account>.workers.dev`. Save it back into vaultnotes:

```bash
vaultnotes rag set-worker-url https://rag-<your-repo-slug>.<your-account>.workers.dev
```

### Push and use

```bash
vaultnotes sync
```

The Action runs (~1–2 min), embeds your notes, commits `public/`, and Pages redeploys. Open `https://<your-pages-domain>/chat/`, enter the chat password, and ask a question.

### How updates flow

Every `vaultnotes sync` (manual or via the daily launchd job) refreshes the notes in the pages repo and rewrites `rag-config.json`. The Action re-embeds and republishes within a few minutes. New notes are answerable right after.

Worker code changes (anything under `worker/`) require running `npx wrangler deploy` again — pushing to GitHub does not redeploy the Worker.

### Costs

Embedding ~hundreds of chunks runs comfortably inside the Gemini free tier. If your vault is bigger or you index frequently and start hitting `429 RESOURCE_EXHAUSTED`, enable billing on the Cloud project linked to your AI Studio key — the dollar cost on small vaults is effectively zero.

## Updates

```bash
pipx install --force git+https://github.com/MaleenKidiwela/vaultnotes.git
```

Site template improvements ship with the package; the next `sync` regenerates `notes.html` automatically.
