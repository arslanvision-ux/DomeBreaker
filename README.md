# DomeBreaker

> **Solaris USD Lighting, HDRI Dissection & Environment Reconstruction Suite for SideFX Houdini**

[![Version](https://img.shields.io/badge/version-v1.0.0--beta.1-blue.svg)](VERSION)
[![Status](https://img.shields.io/badge/status-Active%20Beta%20Testing-brightgreen.svg)](#beta-testing--continuous-updates)
[![Houdini](https://img.shields.io/badge/Houdini-19.5%20%7C%2020.0%20%7C%2020.5%20%7C%2021.0%2B-orange.svg)](https://www.sidefx.com/)
[![Renderers](https://img.shields.io/badge/Renderers-Karma%20%7C%20Arnold%20%7C%20Redshift-red.svg)](#multi-renderer-usd-support)
[![OS](https://img.shields.io/badge/OS-Windows%20%7C%20Linux-green.svg)](#installation)
[![License](https://img.shields.io/badge/license-MIT-lightgrey.svg)](LICENSE)

DomeBreaker is a production-grade Houdini Solaris (LOPs) USD suite designed for VFX lookdev artists, lighters, and lighting TDs. It solves the classic limitations of traditional panoramic dome lights by breaking 360-degree HDRIs into physically plausible CG lighting rigs: extracting physical suns, detecting practical light sources, inpainting textures, solving interior room dimensions, projecting seamless parallax box rooms, and verifying calibration against ground-truth plates.

---

## 🧪 Beta Testing & Continuous Updates

DomeBreaker is currently in **Active Beta Testing (`v1.0.0-beta.1`)**. 

We are continuously updating this tool with performance optimizations, feature refinements, and bug fixes based on real-world production feedback from lighting and lookdev artists. 

> **⚡ Seamless In-Place Updates**: You never have to redownload the whole package manually! Whenever a new update is released, simply run:
> * **Windows**: Double-click `update.bat`
> * **Linux**: Run `./update.sh`
> 
> The tool will pull the latest updates directly from GitHub in seconds without touching any of your local scenes or preferences.

---

## Key Features

- **Physical HDRI Calibration**:
  - Radiometrically calibrate panoramic environments with EV, exposure offset, and Kelvin color temperature controls.
  - Native OCIO ACEScg and scene-linear workflow compliance.

- **Analytical Sun Extraction**:
  - Automatically detect the primary solar direction, angular diameter, solid angle, and spectral irradiance.
  - Generates a physically paired `DistantLight` in USD with corresponding dome inpainting to prevent double illumination.

- **Practical Light Extraction & Diffuse Inpainting**:
  - Extract localized practical lights (ceiling panels, lamps, bounce sources) as native USD Rect/Disk/Sphere lights.
  - Interactive multi-pass OpenCV / OpenImageIO diffuse texture inpainting that cleanly paints out emissive sources from the dome.

- **Seamless Interior Room Reconstruction (Parallax Box Projection)**:
  - **Step 1: Analyze Room Boundaries**: Solves the physical ceiling height, floor plane, and perimeter wall dimensions from the panoramic projection.
  - **Step 2: Auto-Align Room to HDRI**: Solves the optimal rotation offset ($\Delta\text{Yaw}$) to align the room box with coordinate axes and practical light positions.
  - **Step 3: Apply Solved Dimensions & Align Room Box**: Builds the calibrated USD box mesh, projects seamless textures onto walls/floor/ceiling, and positions practical lights at their exact 3D ceiling coordinates with true camera parallax.

- **3D Gaussian Splat Ingestion & Lookdev**:
  - Load and render `.ply` Gaussian Splats inside Solaris alongside USD geometry and materials.
  - Native lookdev turntable rigs with Macbeth color checkers, chrome spheres, and 18% neutral grey balls.

- **Multi-Renderer USD Support**:
  - Production-ready USD material & lighting setup for:
    * **SideFX Karma** (CPU & XPU)
    * **Autodesk Arnold** (HtoA)
    * **Maxon Redshift**

---

## Installation (Windows & Linux)

DomeBreaker supports **Windows** and **Linux** with 1-click automated installers and manual package setups.

### Option 1: 1-Click Automated Installation (Recommended)

#### Windows
1. Download or clone this repository to your preferred location (e.g. `C:\Tools\DomeBreaker`).
2. Double-click **`install.bat`** (or open a terminal and run `python scripts/install.py`).
3. The installer scans your user documents, detects all installed Houdini versions (`19.5`, `20.0`, `20.5`, `21.0+`), and registers `domebreaker.json`.
4. Launch or restart Houdini.

#### Linux
1. Clone or extract the repository to your tools directory (e.g. `~/tools/DomeBreaker`).
2. Open a terminal, make the script executable, and run:
   ```bash
   chmod +x install.sh
   ./install.sh
   ```
3. The installer registers the package inside `~/houdiniX.Y/packages/`.
4. Launch or restart Houdini.

---

### Option 2: Manual Installation (For Studio Pipelines & TDs)

If you manage environments via studio modules or custom package paths, create `domebreaker.json` inside your user `packages` directory:
- **Windows**: `%USERPROFILE%\Documents\houdiniX.Y\packages\domebreaker.json`
- **Linux**: `~/houdiniX.Y/packages/domebreaker.json`

```json
{
    "env": [
        {
            "DOMEBREAKER_ROOT": "/path/to/DomeBreaker"
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
    "houdini_version": ">= 19.5",
    "description": "DomeBreaker - Solaris USD Lighting & Environment Suite"
}
```
*(On Windows, replace `/path/to/DomeBreaker` with your directory using forward slashes, e.g. `C:/Tools/DomeBreaker`).*

---

## Updating DomeBreaker (In-Place Auto-Updater)

Because DomeBreaker is in active beta testing, updates will be pushed frequently:

### Windows:
Double-click **`update.bat`** (or run `python scripts/update.py`).

### Linux:
Run in your terminal:
```bash
./update.sh
```

- **Git clones**: Runs `git fetch` and `git pull --ff-only` from `origin main`.
- **Standalone ZIP downloads**: Connects to the GitHub API, queries the latest commit, downloads the updated files, and updates your installation in-place.
- **Scene Safety**: The updater only replaces tool scripts; your custom scenes, project files, and preferences are never touched.

---

## Verifying in Houdini

1. Launch **Houdini** (Solaris / LOPS desktop).
2. Look at the shelf toolbar: click the **DomeBreaker** shelf tab.
3. Click the **DomeBreaker** tool to open the floating UI.
4. You can also access it at any time via:
   **Windows menu** -> **Python Panel** -> **DomeBreaker**.

---

## Support & Beta Feedback

If you encounter any edge cases, bugs, or have feature requests during your testing:
- **Submit an Issue on GitHub**: https://github.com/arslanvision-ux/DomeBreaker/issues
- **Discussions & Feedback**: Share your test renders and suggestions!