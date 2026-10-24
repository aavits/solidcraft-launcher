# SolidCraft Launcher

> [!WARNING]
> **Certified 100% artisanal AI slop.** Not a single line of this was typed by a human. An AI read
> someone else's launcher, borrowed the layout, borrowed SolidCraft's colours, fonts and icons,
> and stitched it all together while a human said "push it" at regular intervals.
> Everything borrowed is credited and MIT/OFL-licensed, so it's technically *legal* slop.
> It works on my machine. Your mileage may vary. No warranty, no refunds, no shame.

Download, update and launch SolidCraft **stable** and **nightly** AppImages, test GitHub pull
requests by building them with cargo, and open your projects with a chosen version.
Modelled on [FreeCAD Smart Launcher](https://github.com/deltahedra3d/freecad-launcher) by Deltahedra,
dressed in SolidCraft's own design language: its colours, IBM Plex fonts and icons (see LICENSE).
Unofficial; not affiliated with the SolidCraft project.

## Download

Grab `SolidCraft_Launcher-<version>-x86_64.AppImage` from the
[latest release](https://github.com/aavits/solidcraft-launcher/releases/latest), then:

    chmod +x SolidCraft_Launcher-*-x86_64.AppImage
    ./SolidCraft_Launcher-*-x86_64.AppImage

Linux x86_64 only. Everything it needs (Python, Qt, fonts) is inside. If it complains about FUSE,
install `fuse2`/`libfuse2`, or run it with `--appimage-extract-and-run`.

## Use

`~/Applications/SolidCraft_Launcher-<version>-x86_64.AppImage` (menu: *SolidCraft Launcher*). Versions, `launcher_config.json`
and `time_tracker.json` live in `~/Applications/SolidCraft` (changeable in the Download dialog).

- **Versions**: stable and nightly from GitHub releases, plus your system install, the fork in
  `~/Applications/solidcraft-fork` and PR builds under *Other builds*. Launched AppImages get
  `DESKTOPINTEGRATION` set, so they don't add their own menu entries; *Desktop entry* does that.
- **Other builds → Add build…**: add any SolidCraft program or AppImage on disk (e.g. a fork you
  build yourself; *Remove* only takes it off the list), or a **fork on GitHub** (`owner/repo` +
  branch, with *List branches*): its branch is fetched into the source folder and built like a PR.
  *Rebuild* fetches the latest commits of the selected fork (or main) build and builds it again.
- **Pull requests**: browse, search, star, read the conversation. *Build* fetches the PR into the
  source folder (stashing local changes), runs `cargo build --release -p solidcraft` and keeps the
  binary in `~/Applications/SolidCraft/builds/`. It uses the first Rust toolchain it finds
  (`~/.cargo`, `~/Applications/solidcraft-fork/cargo`, …) or installs one into the install folder.
  *Free build space* deletes the source's `target/` (several GB). Building a PR runs its code.
- **Project library**: files under `~/CAD` plus SolidCraft's recent files; previews come from
  the selected version's `solidcraft-cli`; *3D view* uses F3D if installed. *Open in running
  instance* sends the file to the last SolidCraft started from the launcher (control channel).
- **Stats**: time and launches per version, sessions over 5 s.

## Build

    python3 -m venv --system-site-packages .build-venv
    .build-venv/bin/pip install pyinstaller
    # tools/appimagetool-x86_64.AppImage from github.com/AppImage/appimagetool/releases
    ./build.sh            # -> dist/SolidCraft_Launcher-<version>-x86_64.AppImage (~115 MB)

Run from source: `python3 solidcraft_launcher.py` (needs PySide6).
