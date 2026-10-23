# Image Hunt

Local image search for artists: illustration, design, scrapbooking, textures, drawing reference, line-art SVGs and
woodcut-style matches. It fans out over public collections (Openverse, Wikimedia Commons, Iconify, Art Institute of
Chicago, The Met, Cleveland, Library of Congress, NASA, Smithsonian) and optional keyed sources (Unsplash, Pexels,
Pixabay), with a linework scorer for ink drawings. An optional AI helper (via
[ag](https://github.com/Segzman/ag)) plans the searches, curates the results and brainstorms with you in chat.

## Modes and sources

Pick a mode in the top bar: **Anything**, **Photo reference**, **Illustration & art**, **Sketch & etching**,
**Line art SVG**, **Texture & pattern**, **Scrapbook & ephemera**, or **Style match**. Each mode chooses suitable
sources and may turn on ink-linework scoring; saved searches keep working with the older Images / Line art / Style
match names.

The **Sources** panel lists every source. Untick one to leave it out of the next search; keyed sources stay greyed
until you click **add key** and paste the key (saved to `~/.config/image-hunt/keys.json`, mode 0600). Custom sources
are declarative JSON files in `sources/custom/<name>.json` and can be added at runtime; the brainstorm chat can also
propose one (it is validated with a test search and only saved when you click **Add source**).

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

## License

MIT
