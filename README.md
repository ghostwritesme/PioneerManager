# Pioneer Manager

A mod manager for **Watch Dogs 2**. Handles mod archive imports, load order,
XML conflict detection with a 3-way merge, deployment into the game's data
folder, Easy Anti-Cheat bypass toggling, and Nexus Mods integration
(identify installed mods, check for updates, `nxm://` download links).

## Features

- Drag-and-drop mod import from `.zip` / `.rar` / `.7z` / `.fat` archives
- Reorderable load order with drag-and-drop
- Conflict scan across queued mods, with automatic XML merging where safe
- One-click deploy/merge into the game's `data_win64` folder
- EAC bypass toggle for running with mods active
- Nexus Mods: identify installed mods by archive hash, check for updates,
  register as the "Mod Manager Download" handler for `nxm://` links
- Archive cache / deploy-state views for troubleshooting missing tools or
  a misconfigured game path

## Requirements

- Windows
- Python 3.10+ (only needed if running from source; the packaged release
  is a standalone `.exe`)
- [PyQt6](https://pypi.org/project/PyQt6/)

## Running from source

```bash
pip install -r requirements.txt
python main.py
```

On first launch, set your Watch Dogs 2 executable path and the `tools`
folder path (bundled in this repo under `tools/`) from the Settings tab.

Nexus Mods features use browser-based OAuth2 sign-in with PKCE. After Nexus
registers the application and issues its public client ID, set
`OAUTH_CLIENT_ID` in `nexus.py` and register
`http://127.0.0.1:8089/callback` as its redirect URI. For local development,
`PIONEER_NEXUS_CLIENT_ID` can override the ID. Access and refresh tokens are
encrypted for the current Windows user in the ignored `nexus_auth.dat` file;
personal API keys are not used. The Nexus sign-in button stays disabled until
an application ID is configured.

## Building a standalone release

`build.bat` compiles the app with [Nuitka](https://nuitka.net/) into a
standalone folder, copies in the bundled tools, and zips the result:

```bash
build.bat
```

Output lands in `build/main.dist/` (unzipped) and
`build/Pioneer_Manager_Release.zip`.

## Project layout

- `main.py` — UI and application entry point (PyQt6)
- `pipeline.py` — mod extraction/deployment pipeline
- `merger.py` — XML 3-way merge logic for conflicting mod files
- `gibbed.py` — wrapper around the bundled Gibbed unpack/pack/convert tools
- `nexus.py` — Nexus Mods API client, `nxm://` link handling, single-instance guard
- `tools/` — third-party WD2 archive tools (see `tools/license.txt`)
- `assets/` — icons, fonts, loading GIFs

## Credits & licensing

- **Archive tools** (`tools/`): Gibbed.Disrupt tools by Rick (`rick@gibbed.us`),
  with community modifications — see `tools/license.txt` (zlib-style license).
- **Fonts** (`assets/fonts/`): Rajdhani, Inter, IBM Plex Sans, Archivo, and
  Pixeloid Mono are distributed under the SIL Open Font License 1.1. See
  `assets/fonts/LICENSES.md` and `assets/fonts/OFL.txt` for copyright notices,
  source information, and the license text.
- This project (source code in this repository) is licensed under the
  **GNU General Public License v3.0** — see `LICENSE`.

Not affiliated with Ubisoft. Watch Dogs is a trademark of Ubisoft
Entertainment.
