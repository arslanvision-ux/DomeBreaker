# DomeBreaker - Installation Guide (Windows & Linux)

This guide covers both **1-Click Automatic Installation** and **Manual Installation** for **Houdini 20.0, 20.5, and 21.0+** on Windows and Linux.

---

## ⚡ Option 1: 1-Click Automatic Installation (Recommended)

### Windows
1. Open the downloaded or cloned `DomeBreaker` folder.
2. Double-click **`install.bat`** (or run `python scripts/install.py` in a terminal).
3. The installer will automatically scan your `Documents` folder, detect all installed Houdini versions, and create the package descriptor in `packages/domebreaker.json`.
4. Launch or restart Houdini.

### Linux
1. Open a terminal in the `DomeBreaker` directory.
2. Make the installer executable and run it:
   ```bash
   chmod +x install.sh
   ./install.sh
   ```
3. The installer detects your Houdini preference directories (`~/houdiniX.Y/` or `~/.houdiniX.Y/`) and creates `packages/domebreaker.json`.
4. Launch or restart Houdini.

---

## 🛠️ Option 2: Manual Installation (For TDs & Custom Studio Pipelines)

If you prefer manual setup or are deploying via studio environment modules, follow these steps:

### 1. Locate your Houdini preferences `packages` directory:
* **Windows**: `C:\Users\<username>\Documents\houdiniX.Y\packages\`
* **Linux**: `/home/<username>/houdiniX.Y/packages/` (or `~/.houdiniX.Y/packages/`)
* **macOS**: `~/Library/Preferences/houdini/X.Y/packages/`

*(If the `packages` folder does not exist, create it).*

### 2. Create `domebreaker.json`:
Create a file named `domebreaker.json` inside the `packages` directory with the following contents:

```json
{
    "env": [
        {
            "DOMEBREAKER_ROOT": "C:/path/to/DomeBreaker"
        },
        {
            "HDRI_MATCH_SOLARIS_ROOT": "$DOMEBREAKER_ROOT"
        },
        {
            "PYTHONPATH": {
                "value": [
                    "$DOMEBREAKER_ROOT/python"
                ],
                "method": "append"
            }
        }
    ],
    "path": [
        "$DOMEBREAKER_ROOT/houdini"
    ],
    "houdini_version": ">= 20.0",
    "description": "DomeBreaker - Solaris USD Lighting & Environment Suite"
}
```
> **Note for Windows Users**: Always use forward slashes (`/`) in JSON filepaths (e.g., `D:/Tools/DomeBreaker`, not `D:\Tools\DomeBreaker`).

---

## 🔄 Updating DomeBreaker (Command-Line Auto-Updater)

Whenever a new version or fix is released on GitHub:

### Windows:
Double-click **`update.bat`** (or run `python scripts/update.py`).

### Linux:
Run in terminal:
```bash
./update.sh
```

The updater automatically checks the repository (`https://github.com/arslanvision-ux/DomeBreaker`), pulls updates via Git (or downloads the latest release archive if not using Git), and replaces the core files in-place without touching any of your custom scenes or local preferences.

---

## 🗑️ Uninstallation

If DomeBreaker is already installed, running the installer (`install.bat` / `install.sh`) automatically detects the existing installation and offers an option to **Uninstall** or **Reinstall/Update**.

Alternatively, you can uninstall directly with a single click:

### Windows:
* Double-click **`uninstall.bat`** (or run `python scripts/install.py --uninstall`).

### Linux:
* Run in terminal:
  ```bash
  chmod +x uninstall.sh
  ./uninstall.sh
  ```
  *(or `python3 scripts/install.py --uninstall`)*

### Manual Uninstallation:
Simply delete `domebreaker.json` from your Houdini `packages` directory:
* **Windows**: `C:\Users\<username>\Documents\houdiniX.Y\packages\domebreaker.json`
* **Linux**: `~/.houdiniX.Y/packages/domebreaker.json` or `~/houdiniX.Y/packages/domebreaker.json`

Your custom scenes, plates, and Houdini preferences remain 100% untouched.

---

## 🚀 Verifying Installation in Houdini

1. Launch **Houdini** (Solaris / LOPS desktop recommended).
2. Look at the top shelf tabs: you will see the **DomeBreaker** shelf tab.
3. Click the **DomeBreaker** shelf tool to launch the UI panel.
4. Alternatively, you can open it anytime from the menu bar:
   **Windows** $\rightarrow$ **Python Panel** $\rightarrow$ **DomeBreaker**.

