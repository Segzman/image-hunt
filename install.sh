#!/bin/sh
# Image Hunt installer for macOS and Linux. Installs in place: put this folder where it should live first.
#   sh install.sh            install (asks before downloading ag / opencode)
#   sh install.sh --yes      install, say yes to every question
#   sh install.sh --uninstall  remove the app icon + imagehunt:// link (keeps this folder and saved searches)
# Windows: double-click install.cmd instead.
set -eu
cd "$(dirname "$0")"
DIR=$(pwd)
YES=0
OS=$(uname -s)
APP="$HOME/Applications/Image Hunt.app"
DESKTOP="${XDG_DATA_HOME:-$HOME/.local/share}/applications/image-hunt.desktop"
ICON="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor/256x256/apps/image-hunt.png"

say() { printf '\033[1;31m▶\033[0m %s\n' "$*"; }
ask() {  # ask "question" -> 0 for yes
    [ "$YES" = 1 ] && return 0
    [ -t 0 ] || return 1
    printf '%s [Y/n] ' "$1"; read -r a; case "$a" in [nN]*) return 1;; *) return 0;; esac
}

uninstall() {
    rm -rf "$APP"
    rm -f "$DESKTOP" "$ICON"
    command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$(dirname "$DESKTOP")" || true
    say "Removed the app icon and imagehunt:// link. $DIR (and saved/) left alone."
}

for a in "$@"; do
    case "$a" in
        --yes|-y) YES=1;;
        --uninstall) uninstall; exit 0;;
        *) echo "usage: sh install.sh [--yes] [--uninstall]" >&2; exit 2;;
    esac
done

# 1. Python + Pillow in a private venv
PY=""
for c in python3.13 python3.12 python3.11 python3.10 python3; do
    if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(sys.version_info < (3, 9))' 2>/dev/null; then PY=$c; break; fi
done
[ -n "$PY" ] || { echo "Need Python 3.9+. macOS: xcode-select --install (or brew install python). Linux: install python3 + python3-venv." >&2; exit 1; }
if [ ! -x .venv/bin/python ]; then
    say "Creating .venv with $PY"
    "$PY" -m venv .venv || { echo "venv failed. Debian/Ubuntu: sudo apt install python3-venv" >&2; exit 1; }
fi
.venv/bin/python -c 'import PIL' 2>/dev/null || { say "Installing Pillow"; .venv/bin/python -m pip install -q --upgrade pip pillow; }

# 2. AI helpers (optional): ag discovers the installed model harnesses. Without them search runs scripts-only.
AG=$(command -v ag 2>/dev/null || true)
[ -n "$AG" ] || { [ -x "$HOME/.local/bin/ag" ] && AG="$HOME/.local/bin/ag"; } || true
[ -n "$AG" ] || { [ -x "$HOME/agent-cli/ag" ] && AG="$HOME/agent-cli/ag"; } || true
if [ -z "$AG" ]; then
    if command -v git >/dev/null 2>&1 && ask "Install ag (runs the AI helpers) from github.com/Segzman/ag into ~/.local/bin?"; then
        src="$HOME/.local/share/ag-src"
        [ -d "$src/.git" ] && git -C "$src" pull -q --ff-only || git clone -q --depth 1 https://github.com/Segzman/ag "$src"
        sh "$src/install.sh" --force
        AG="$HOME/.local/bin/ag"
    else
        say "Skipping ag: AI model picker will only offer 'No AI'."
    fi
fi
PATH="$HOME/.local/bin:$HOME/.opencode/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
if [ -n "$AG" ]; then
    DOCTOR=$("$AG" --dir "$DIR/.agent" agents doctor --json 2>/dev/null || true)
    DOCTOR_INFO=$(printf '%s\n' "$DOCTOR" | "$PY" -c '
import json, sys
try:
    result = json.load(sys.stdin)
    data = result.get("data", result)
    rows = data.get("backends", [])
    if not isinstance(rows, list):
        rows = []
except (ValueError, AttributeError):
    rows = []
rows = [row for row in rows if isinstance(row, dict)]
allowed = {"claude", "codex", "opencode", "gemini", "cursor"}
usable = any(r.get("backend") in allowed and r.get("installed") and r.get("auth") != "no" for r in rows)
print("usable=" + ("yes" if usable else "no"))
for row in [r for r in rows if r.get("backend") in allowed and r.get("installed")]:
    version = ", version=" + str(row["version"]) if row.get("version") else ""
    print("%s: %s, auth=%s%s" % (row.get("backend", "unknown"), "installed" if row.get("installed") else "not installed", row.get("auth", "unknown"), version))
')
    say "Detected AI tools:"
    printf '%s\n' "$DOCTOR_INFO" | sed -n '2,$p' | while IFS= read -r row; do
        [ -n "$row" ] && say "$row"
    done
    if ! printf '%s\n' "$DOCTOR_INFO" | sed -n '1p' | grep -q '^usable=yes$'; then
        if ask "No logged-in AI harness found. Install opencode (free models) via its official script, https://opencode.ai/install ?"; then
            curl -fsSL https://opencode.ai/install | bash
        else
            say "No usable AI harness found: install and log in to Claude, Codex, opencode, Gemini, or Cursor later."
        fi
    fi
fi

# 3. App icon + imagehunt:// link (bookmark it in any browser: opening it starts Image Hunt)
q() { printf '%s' "$1" | sed "s/'/'\\\\''/g"; }  # escape for a single-quoted shell string
RUN="'$(q "$DIR/.venv/bin/python")' '$(q "$DIR/launch.py")'"
if [ "$OS" = Darwin ]; then
    say "Building $APP"
    tmp=$(mktemp -d)
    cat > "$tmp/main.applescript" <<EOF
on run
    do shell script "$RUN >/dev/null 2>&1 &"
end run
on open location u
    do shell script "$RUN " & quoted form of u & " >/dev/null 2>&1 &"
end open location
EOF
    mkdir -p "$HOME/Applications"
    rm -rf "$APP"
    osacompile -o "$APP" "$tmp/main.applescript"
    rm -rf "$tmp"
    cp assets/icon.icns "$APP/Contents/Resources/applet.icns"
    pl="$APP/Contents/Info.plist"
    /usr/libexec/PlistBuddy -c "Set :CFBundleIdentifier org.imagehunt.launcher" "$pl" 2>/dev/null \
        || /usr/libexec/PlistBuddy -c "Add :CFBundleIdentifier string org.imagehunt.launcher" "$pl"
    /usr/libexec/PlistBuddy -c "Add :CFBundleURLTypes array" \
        -c "Add :CFBundleURLTypes:0 dict" \
        -c "Add :CFBundleURLTypes:0:CFBundleURLName string Image Hunt" \
        -c "Add :CFBundleURLTypes:0:CFBundleURLSchemes array" \
        -c "Add :CFBundleURLTypes:0:CFBundleURLSchemes:0 string imagehunt" "$pl"
    codesign -s - --force --deep "$APP" >/dev/null 2>&1 || true
    /System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister -f "$APP"
    touch "$APP"
else
    say "Adding menu entry + imagehunt:// handler"
    mkdir -p "$(dirname "$DESKTOP")" "$(dirname "$ICON")"
    cp assets/icon.png "$ICON"
    dq() { printf '"%s"' "$(printf '%s' "$1" | sed 's/[\\"`$]/\\&/g')"; }  # desktop-entry Exec quoting
    cat > "$DESKTOP" <<EOF
[Desktop Entry]
Type=Application
Name=Image Hunt
Comment=Hunt high-res images for posters
Exec=$(dq "$DIR/.venv/bin/python") $(dq "$DIR/launch.py") %u
Icon=image-hunt
Terminal=false
Categories=Graphics;
MimeType=x-scheme-handler/imagehunt;
EOF
    command -v xdg-mime >/dev/null 2>&1 && xdg-mime default image-hunt.desktop x-scheme-handler/imagehunt || true
    command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$(dirname "$DESKTOP")" || true
fi

say "Done. Starting Image Hunt…"
.venv/bin/python launch.py || true
cat <<EOF

  Open it:      the Image Hunt app icon$( [ "$OS" = Darwin ] && echo " (~/Applications; drag it to the Dock)" || echo " (app menu)")
  Bookmark:     imagehunt://open   ← starts the server if it's off, then opens the page
                (or bookmark http://127.0.0.1:8666/ — only works while it's running)
  AI helpers:   $( [ -n "$AG" ] && echo "on (ag: $AG)" || echo "off — rerun sh install.sh to add ag" )
  Uninstall:    sh install.sh --uninstall
EOF
