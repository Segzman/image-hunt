# Image Hunt

Local image search for poster making: memes, line-art SVGs and woodcut-style matches from Openverse, Wikimedia Commons
and Iconify, ranked by a linework scorer. An optional AI helper (via [ag](https://github.com/Segzman/ag)) plans the
searches, curates the results and brainstorms with you in chat.

## Install

Download **[image-hunt.zip](https://github.com/Segzman/image-hunt/releases/latest/download/image-hunt.zip)**,
unzip it where it should live (the install points at that folder), then:

| OS | Run |
|---|---|
| macOS / Linux | `sh install.sh` |
| Windows | double-click `install.cmd` |

You need Python 3.9+. The installer creates a private `.venv` and installs Pillow into it. It then adds:
- an **app icon**: `~/Applications/Image Hunt.app` on macOS, the app menu on Linux, Start menu + Desktop on Windows;
- an **`imagehunt://open` link**: opening it starts the server if it's off, then opens the page. To bookmark it,
  drag the 🔖 button at the top of the page onto your bookmarks bar.

**AI helpers** are optional; without them the model picker shows only "No AI" and search runs on scripts alone.
The picker shows models from AI CLIs you already have logged in to (Claude Code, Codex, opencode, Gemini, or Cursor);
use the ↻ button to re-detect installed tools. On macOS/Linux the installer offers to install `ag` and, if no usable
harness is found, `opencode` (free models). ag is POSIX-only, so on Windows search is scripts-only unless you install
inside WSL. Model notes: models marked "trains on prompts" keep your search text for training; pick a different one if
that matters.

Everything runs on `http://127.0.0.1:8666` and only accepts local requests. Log: `~/Library/Logs/image-hunt.log` (macOS),
`~/.local/state/image-hunt/image-hunt.log` (Linux), `%LOCALAPPDATA%\Image Hunt\image-hunt.log` (Windows).

Uninstall: `sh install.sh --uninstall` / `install.cmd -Uninstall`, then delete the folder.

## Share it

`sh make_dist.sh` → `dist/image-hunt.zip` (committed files only; your saved searches and AI state stay out).
