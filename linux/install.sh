#!/bin/sh
# Adds DDP Streamer to the applications menu (with its icon) for this user.
# Run it from the unpacked folder:   ./install.sh          (./install.sh --uninstall removes it)
# The program stays where it is; move the folder first if you want it somewhere else.
set -e
HERE=$(cd "$(dirname "$0")" && pwd)
APPS="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
ICONS="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor/256x256/apps"

if [ "$1" = "--uninstall" ]; then
    rm -f "$APPS/ddp-streamer.desktop" "$ICONS/ddp-streamer.png"
    echo "DDP Streamer removed from the menu (the program folder and your settings are kept)."
    exit 0
fi

mkdir -p "$APPS" "$ICONS"
cp "$HERE/_internal/ddp-streamer.png" "$ICONS/ddp-streamer.png"
# The menu entry, pointing at this folder (the path may contain spaces)
sed "s|^Exec=.*|Exec=\"$HERE/DDP Streamer\" %F|" "$HERE/_internal/ddp-streamer.desktop" > "$APPS/ddp-streamer.desktop"
chmod +x "$APPS/ddp-streamer.desktop"
# Let the menu and dock notice (both tools are optional)
gtk-update-icon-cache -q -t "${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor" 2>/dev/null || true
update-desktop-database -q "$APPS" 2>/dev/null || true
echo "DDP Streamer added to the applications menu."
