# Pioneer Manager

A standalone mod manager for **Watch Dogs 2** designed to kill the headache of manual mod installs. Instead of extracting `.fat`/`.dat` archives by hand and praying two mods don't overwrite each other's files, Pioneer Manager handles your load order, runs 3-way merges on conflicting XMLs, and deploys everything straight to your game folder.

## What it does

- **Drop-in installs**: Drag and drop `.zip`, `.rar`, `.7z`, or raw `.fat` files directly into the window.
- **Visual load order**: Reorder your mod priority via drag and drop without touching config files.
- **Smart XML merging**: Automatic conflict scanner that performs 3-way merges on overlapping XML tables so you don't lose tweaks from competing mods.
- **One-click deploy**: Packages and pushes your active load order directly into `data_win64`.
- **Built-in EAC toggle**: Turn Easy Anti-Cheat off with a single click so your mods actually load without game crashes.
- **Native Nexus Mods support**: Log in via official OAuth2 PKCE (no sketchy raw API keys to paste, tokens stay encrypted locally in `nexus_auth.dat`). Supports one-click `nxm://` downloads, automatic update checks, and mod identification.
- **Health check tab**: Instantly flags broken game paths, missing unpacker binaries, or permission snags before you hit launch.

## Requirements

- **OS**: Windows 10 / 11 (x64)
- **Running the release**: Just grab the latest zip from [Releases](https://github.com/ghostwritesme/PioneerManager/releases), extract it anywhere, and run `Pioneer_Manager.exe`.
- **Running from source**: Python 3.10+ with [PyQt6](https://pypi.org/project/PyQt6/).

## Running from source

```bash
git clone https://github.com/ghostwritesme/PioneerManager.git
cd PioneerManager
pip install -r requirements.txt
python main.py
```

### First launch setup

1. Open the **Settings** tab.
2. Point the game path to your `WatchDogs2.exe` (or your root install directory).
3. Confirm the `tools/` path points to the bundled unpacker directory.
4. *(Optional)* Click **Connect to Nexus Mods** to authenticate via your default browser.

## Building a standalone binary

To compile the standalone `.exe` using [Nuitka](https://nuitka.net/):

```bash
build.bat
```

This packages the entire Python environment, embeds the bundled tools, and drops a portable zip into `build/Pioneer_Manager_Release.zip`.

> **Build requirement**: Needs **Visual Studio 2022 Build Tools** with the **Desktop development with C++** workload installed. The script targets MSVC specifically to avoid AVX instruction crashes on older or budget x64 CPUs, ensuring broad compatibility.

## Architecture

```plaintext
PioneerManager/
├── main.py        # PyQt6 GUI and main loop
├── pipeline.py    # Unpacking, mod staging, and patch repacking
├── merger.py      # 3-way XML conflict resolution and diff merging
├── gibbed.py      # Wrapper around Rick's Gibbed.Disrupt tools
├── nexus.py       # Nexus Mods API client, OAuth2 PKCE, and nxm:// handler
├── tools/         # Gibbed unpack/pack binaries (see tools/license.txt)
└── assets/        # Stylesheets, icons, and bundled typography
```

## Credits & License

- **Archive tools** (`tools/`): Built on Rick's Gibbed.Disrupt suite (`rick@gibbed.us`) with community updates.
- **Fonts** (`assets/fonts/`): Rajdhani, Inter, IBM Plex Sans, Archivo, and Pixeloid Mono (SIL Open Font License 1.1).
- **License**: Open-source under the GNU General Public License v3.0 (see `LICENSE`).

**Disclaimer**: Not affiliated with Ubisoft. Watch Dogs 2 is a registered trademark of Ubisoft Entertainment.
