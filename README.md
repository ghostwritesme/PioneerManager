# Pioneer Manager

A standalone mod manager for **Watch Dogs 2** designed to kill the headache of manual mod installs. Instead of extracting `.fat`/`.dat` archives by hand and praying two mods don't overwrite each other's files, Pioneer Manager handles your load order, runs 3-way merges on conflicting XMLs, and deploys everything straight to your game folder.

Covers absolutely everything needed for the importing process of mods including ordering, checking for conflicts, merging, and deployment—all without needing to touch patch3 or handling any XML differences personally.

## Features

- **Drag and Drop**: Import mods directly from .zip, .rar, .7z, and .fat archives.
- **Load Order**: Customize mod loading priority easily.
- **Conflict Detection & Auto-Merge**: Checks for potential conflicts among imported mods with automatic 3-way merging of safe XML files.
- **One-Click Deployment**: Directly deploys imported and merged mods into the game's data_win64 folder.
- **EAC Bypass**: Built-in option to easily bypass Easy Anti-Cheat for modded play.
- **Nexus Mods Integration (WIP)**: Match installed mods via hash comparison, check for mod updates, and support for the "Mod Manager Download" button directly from Nexus.

## Installation & Setup

1. Download the latest release .zip archive and extract it anywhere outside of your game directory.
2. Launch the application and open the Settings menu.
3. Select your WatchDogs2.exe file location.
4. Set the path to the bundled tools folder.
5. You're ready to start importing and managing mods.

## Credits

Developed using the Gibbed.Disrupt framework tools (WD2Extract, WD2Pack, and ConvertBinaryObject).

## License

GNU General Public License v3.0 (GPL-3.0). See [LICENSE](LICENSE) for more details.

## Development & Authenticity Disclaimer

This project is 100% human-crafted. No generative AI or LLMs were used to write the application code, logic, or structure.

