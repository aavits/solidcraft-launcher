# SolidCraft Launcher

Download, update and launch SolidCraft **stable** and **nightly** AppImages, test GitHub pull
requests by building them with cargo, and open your projects with a chosen version.
Modelled on [FreeCAD Smart Launcher](https://github.com/deltahedra3d/freecad-launcher) by Deltahedra.
Unofficial; not affiliated with the SolidCraft project.

## Use

`~/Applications/SolidCraft-Launcher` (menu: *SolidCraft Launcher*). Versions, `launcher_config.json`
and `time_tracker.json` live in `~/Applications/SolidCraft` (changeable in the Download dialog).

- **Versions**: stable and nightly from GitHub releases, plus your system install, the fork in
  `~/solidcraft-fork` and PR builds under *Other builds*. Launched AppImages get
  `DESKTOPINTEGRATION` set, so they don't add their own menu entries; *Desktop entry* does that.
- **Pull requests**: browse, search, star, read the conversation. *Build* fetches the PR into the
  source folder (stashing local changes), runs `cargo build --release -p solidcraft` and keeps the
  binary in `~/Applications/SolidCraft/builds/`. It uses the first Rust toolchain it finds
  (`~/.cargo`, `~/solidcraft-fork/cargo`, …) or installs one into the install folder.
  *Free build space* deletes the source's `target/` (several GB). Building a PR runs its code.
- **Project library**: files under `~/CAD` plus SolidCraft's recent files; previews come from
  the selected version's `solidcraft-cli`; *3D view* uses F3D if installed. *Open in running
  instance* sends the file to the last SolidCraft started from the launcher (control channel).
- **Stats**: time and launches per version, sessions over 5 s.

## Build

    python3 -m venv --system-site-packages .build-venv
    .build-venv/bin/pip install pyinstaller
    ./build.sh            # -> dist/SolidCraft-Launcher (one self-contained file, ~125 MB)

Run from source: `python3 solidcraft_launcher.py` (needs PySide6).
