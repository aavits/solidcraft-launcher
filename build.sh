#!/bin/sh
# Builds dist/SolidCraft_Launcher-<version>-x86_64.AppImage (Python + PySide6 inside).
# Needs: python3 -m venv --system-site-packages .build-venv && .build-venv/bin/pip install pyinstaller
# and tools/appimagetool-x86_64.AppImage (github.com/AppImage/appimagetool releases).
set -eu
cd "$(dirname "$0")"
VERSION=$(sed -n 's/^APP_VERSION = "\(.*\)"/\1/p' solidcraft_launcher.py)
APPDIR=.build/SolidCraft_Launcher.AppDir

.build-venv/bin/python -m PyInstaller --noconfirm --clean --onedir --windowed \
    --name solidcraft-launcher \
    --add-data "$PWD/icon.png:." --add-data "$PWD/assets:assets" \
    --exclude-module tkinter \
    --workpath .build/pyinstaller --distpath .build/dist --specpath .build \
    solidcraft_launcher.py

rm -rf "$APPDIR"
mkdir -p "$APPDIR/usr/lib"
cp -a .build/dist/solidcraft-launcher "$APPDIR/usr/lib/"
# GNOME draws no title bars for Wayland apps; without this Qt falls back to its plain "bradient" one.
# Comes from qt6-qtwayland-adwaita-decoration and must match the bundled Qt (both from the system).
ADW=/usr/lib64/qt6/plugins/wayland-decoration-client/libadwaita.so
if [ -f "$ADW" ]; then
    cp "$ADW" "$APPDIR/usr/lib/solidcraft-launcher/_internal/PySide6/Qt/plugins/wayland-decoration-client/"
else
    echo "warning: $ADW missing (install qt6-qtwayland-adwaita-decoration); GNOME gets Qt's plain title bar" >&2
fi
cp icon.png "$APPDIR/solidcraft-launcher.png"
cat > "$APPDIR/solidcraft-launcher.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=SolidCraft Launcher
GenericName=SolidCraft version manager
Comment=Download, update and launch SolidCraft versions, test pull requests
Exec=solidcraft-launcher
Icon=solidcraft-launcher
Terminal=false
StartupWMClass=solidcraft-launcher
Categories=Graphics;3DGraphics;Engineering;
EOF
cat > "$APPDIR/AppRun" <<'EOF'
#!/bin/sh
APPDIR="$(dirname "$(readlink -f "$0")")"
exec "$APPDIR/usr/lib/solidcraft-launcher/solidcraft-launcher" "$@"
EOF
chmod +x "$APPDIR/AppRun"

mkdir -p dist
OUT="dist/SolidCraft_Launcher-$VERSION-x86_64.AppImage"
ARCH=x86_64 tools/appimagetool-x86_64.AppImage --appimage-extract-and-run --no-appstream "$APPDIR" "$OUT"
echo "built $OUT"
