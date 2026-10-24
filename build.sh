#!/bin/sh
# Builds dist/SolidCraft-Launcher: one self-contained executable (Python + PySide6 inside).
# Needs the project venv: python3 -m venv --system-site-packages .build-venv && .build-venv/bin/pip install pyinstaller
set -eu
cd "$(dirname "$0")"
VERSION=$(sed -n 's/^APP_VERSION = "\(.*\)"/\1/p' solidcraft_launcher.py)
.build-venv/bin/pyinstaller --noconfirm --clean --onefile --windowed \
    --name SolidCraft-Launcher \
    --add-data "$PWD/icon.png:." --icon "$PWD/icon.png" \
    --exclude-module tkinter \
    --workpath .build --distpath dist --specpath .build \
    solidcraft_launcher.py
echo "built dist/SolidCraft-Launcher ($VERSION)"
