# ⚡ DomeBreaker

**Production-Grade Solaris USD Lighting, Interior Room Projection & Lookdev Suite for SideFX Houdini**

[![Houdini](https://img.shields.io/badge/Houdini-19.5%20|%2020.0%20|%2020.5%20|%2021.0-orange.svg)](https://www.sidefx.com/)
[![USD](https://img.shields.io/badge/OpenUSD-22.11+-blue.svg)](https://openusd.org/)
[![Renderers](https://img.shields.io/badge/Renderers-Karma%20|%20Arnold%20|%20Redshift-9cf.svg)](#universal-multi-renderer-shader-pipeline)
[![Release](https://img.shields.io/badge/Release-v1.0.0--beta.1-purple.svg)](https://github.com/arslanvision-ux/DomeBreaker/releases)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Platform](https://img.shields.io/badge/Platform-Windows%20|%20Linux-lightgrey.svg)](#-quick-installation)

<div align="center">
  <img src="screenshots/Small_Room4.jpg" alt="DomeBreaker Main Presentation - Interactive Lookdev & Room Dimension Analyzer" width="100%">
  <p><em>Interactive Karma Lookdev with Chrome & 18% Grey calibration spheres, real-time 360° Room Dimension Analyzer, and live USD stage synchronization.</em></p>
</div>

---

DomeBreaker is a high-performance Houdini Solaris / OpenUSD suite engineered to break the traditional limitations of infinite flat environment domes in feature film and episodic VFX pipelines. By transforming 2D equirectangular panoramas into parallax-accurate 3D room boxes, extracting practical lights with automatic inpainting, and constructing native multi-renderer USD networks, DomeBreaker gives lighters and lookdev artists physically plausible ground reflections, localized occlusion, and real-time interactive feedback.

---

## 📸 Visual Showcase

### 1. 📐 3D Interior Room Box & Practical Light Extraction
Extract physical room geometry and practical light sources directly into native OpenUSD `RectLight` primitives with clean inpainting to avoid energy doubling:

| Wireframe Room Box & Inpainting | Live Solaris Lights & Multi-Light Tuning |
| :---: | :---: |
| <img src="screenshots/Small_Room6.jpg" alt="Wireframe Room Box & RectLights" width="100%"> | <img src="screenshots/Small_Room2.jpg" alt="Solaris Lights & HDRI Library" width="100%"> |
| *RectLights flush-snapped to ceiling/walls with background inpainting* | *Interactive multi-light adjustments with live scene graph updates* |

| Lookdev Room Integration | Office Lookdev Setup | Bell Tower Lookdev |
| :---: | :---: | :---: |
| <img src="screenshots/Small_Room5.jpg" alt="Lookdev Room Integration" width="100%"> | <img src="screenshots/Small_Room3.jpg" alt="Office Lookdev Setup" width="100%"> | <img src="screenshots/Small_Room.jpg" alt="Bell Tower Lookdev" width="100%"> |
| *True local reflection & ground shadow contact* | *Accurate local light reflections on chrome ball* | *Complex architectural environment alignment* |

---

### 2. ☀️ Outdoor Ground Projection & Physical Sun Relighting
Ground disc projection with shadow catcher integration and sub-pixel sun extraction for outdoor environments:

| Direct Sun Extraction & Contact Shadows | Outdoor Lawn Lookdev Test |
| :---: | :---: |
| <img src="screenshots/outside.jpg" alt="Direct Sun Extraction & Contact Shadows" width="100%"> | <img src="screenshots/outside2.jpg" alt="Outdoor Lawn Lookdev Test" width="100%"> |
| *Ground disc projection with shadow catcher and physical sun ray casting* | *Seamless ground integration with true environmental occlusion* |

---

### 3. 🎛️ Unified Production Interface & Control Panels
A modern, artist-friendly PySide UI built for 60 FPS real-time parameter synchronization:

| Main DomeBreaker Panel (Tab 1) | HDRI Room Dimension Analyzer (Tab 3) |
| :---: | :---: |
| <img src="screenshots/ui1.jpg" alt="Main DomeBreaker Panel" width="100%"> | <img src="screenshots/ui3.jpg" alt="HDRI Room Dimension Analyzer" width="100%"> |
| *Lookdev rig controls, sun extraction & live viewport sync* | *Automated boundary extraction with visual overlay & large screen view* |

| 3D Gaussian Splatting (Tab 2) | HDRI & Light Asset Browser |
| :---: | :---: |
| <img src="screenshots/ui2.jpg" alt="3D Gaussian Splatting" width="100%"> | <img src="screenshots/u43.jpg" alt="HDRI Asset Browser" width="100%"> |
| *PLY point cloud ingestion & room architecture generation* | *Organized HDRI asset library with thumbnail browsing* |

| Studio Gobos & Light Textures | Procedural Gradient & Scrim Builder |
| :---: | :---: |
| <img src="screenshots/u5.jpg" alt="Studio Gobos" width="100%"> | <img src="screenshots/u6.jpg" alt="Procedural Scrim Builder" width="100%"> |
| *Curated studio gobo textures & projection masks* | *Interactive softbox, ramp, and procedural gradient generator* |

---

## 🌟 Core Capabilities

### 1. 📐 3D Interior Room Box Projection & Planar Baking
* **Multi-Surface Planar Baking**: Projects equirectangular HDRIs onto 6 clean rectilinear planar textures at up to 8K resolution (Floor, Ceiling, Wall North, Wall South, Wall East, Wall West).
* **Parallax-Correct Lookdev**: CG assets receive physically accurate local reflections and occlusion instead of distorted, distant environment maps.
* **Manhattan Room Architecture Analyzer**: Automatically detects room bounds, floor-to-ceiling heights, and camera tripod offsets with a single click.

### 2. 💡 Practical Light Extraction & Non-Destructive Inpainting
* **Automated Hotspot Extraction**: Scans HDRIs for bright practical light emitters (fluorescents, lamps, windows, downlights) and extracts them into native OpenUSD `RectLight` and `DiskLight` primitives.
* **Non-Destructive Inpainting**: Automatically paints out extracted light sources from the diffuse room textures, preventing double-illumination and energy conservation violations.
* **1:1 Surface Snapping**: Snaps extracted RectLights flush onto the ceiling and wall geometry matching the exact inpaint positions.

### 3. ☀️ Physical Sun Relighting
* Detects solar position, elevation, and azimuth with sub-pixel centroid accuracy.
* Creates directional distant sun lights calibrated to true physical sky models with live contact shadows and ground interaction.

### 4. 🔮 Real-Time Lookdev Calibration Rig
* **Industry Standard Verification**: Chrome mirror, 18% Scene-Linear Neutral Grey, and optional 90% Matte White spheres on a slender anodized metal tripod stand.
* **Scale Presets**: Instant one-click switching between VFX Standard (30cm / 12in diameter on 1m stand), Tabletop (12cm diameter), and Floor (resting on ground).
* **Interactive 60 FPS Viewport Sync**: Ball radius, ball spacing, stand height, and ground positions synchronize live with zero cook delays.

### 5. 🎨 Universal Multi-Renderer Shader Pipeline
Native automated shader network generation across major production render delegates:
* **SideFX Karma CPU / XPU** (MaterialX `open_pbr_surface` and `standard_surface`)
* **Autodesk Arnold** (`aiStandardSurface` and `aiFlat` with camera ray visibility flags)
* **Maxon Redshift** (`StandardMaterial` with USD primvar and projection bindings)
* **UsdPreviewSurface** fallbacks for universal Hydra viewport fidelity.

---

## 🚀 Quick Installation

### Windows (1-Click)
1. Clone or download this repository.
2. Double-click **`install.bat`** (or run `python scripts/install.py`).
3. Launch Houdini.

### Linux (1-Click)
1. Open a terminal in the DomeBreaker folder:
   ```bash
   chmod +x install.sh
   ./install.sh
   ```
2. Launch Houdini.

*For detailed manual installation instructions or studio TD module deployments, see [INSTALL.md](INSTALL.md).*

---

## 🔄 Instant Updates (Command-Line)

When a new version or fix is published, you do **not** need to re-download or reinstall:

* **Windows**: Double-click **`update.bat`**
* **Linux**: Run `./update.sh`

The updater automatically pulls changes from GitHub (`https://github.com/arslanvision-ux/DomeBreaker`), seamlessly updates all code and shelf tools, and preserves your custom scenes.

---

## 🛠️ Typical Lookdev Workflow

1. **Load HDRI**: Drop or select your calibrated 360° HDR panorama in the DomeBreaker panel.
2. **Analyze Room Boundaries**:
   * Under **Room Boundary Analyzer**, click **`🔍 Analyze Room Boundaries`** to solve room dimensions.
   * Click **`📐 Auto-Align Room to HDRI`** to calculate camera offsets.
   * Click **`👉 Apply Solved Dimensions & Align Room Box`** to generate the 3D room box.
3. **Extract Practicals**:
   * Under **Light Extraction**, click **`🔍 Analyze Hotspots`**.
   * Click **`Extract to Solaris RectLights`** — practical lights are created and their diffuse textures are automatically inpainted.
4. **Calibrate Lookdev Rig**: Click **`🔮 Add / Update Lookdev Spheres`** to place calibrated Chrome and Grey reference balls into your scene.
5. **Bake Planar Textures**: Click **`Bake Planar Textures (4K/8K)`** for ultra-sharp rectilinear wall projections with zero spherical distortion.

---

## 📄 License
This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
