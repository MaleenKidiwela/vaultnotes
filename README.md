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

## Updates

```bash
pipx install --force git+https://github.com/MaleenKidiwela/vaultnotes.git
```

Site template improvements ship with the package; the next `sync` regenerates `notes.html` automatically.
