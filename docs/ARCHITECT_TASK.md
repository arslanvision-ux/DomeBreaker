# 🏛️ Architectural Blueprint from Gemini 4 Argon Web Chat

> **Live Gemini Web Session URL:** [https://gemini.google.com/app/a8ab3921b33faca2](https://gemini.google.com/app/a8ab3921b33faca2)

> **Generated:** 2026-11-12 03:49:44 UTC

Technical Architecture, Stability & Safety Audit: DomeBreaker v2.0.4 (f5bbe36)

Auditor: Argon (Principal Lead Quantitative Systems & Software Architect)

Target Environments: Windows 10/11 & Rocky Linux 8 (mlk-ws448) via HP Anyware / Teradici PCoIP Virtual X11 (DISPLAY=:100)

Host Runtime: SideFX Houdini 20.0 / 20.5 / 21.0+ Solaris (pxr USD, hou, OpenImageIO, PySide2 / PySide6) under ShotGrid tk-houdini

1. Quantitative Safety & State Machine Invariants
1.1 Validation of Section 3 Audit Findings (Confirmed + Deepened Analysis)
ID	Subsystem	Verdict	Architectural & PCoIP :100 Invariant Analysis
3.A.1	CrucibleLightStudio (ThumbnailWorker)	CONFIRMED (P0 SIGSEGV)	ThumbnailWorker.run() executes a synchronous for loop spawning subprocess.run(..., timeout=4) with no Qt event loop (exec_()). Calling self._worker.quit() is a strict no-op on threads without an event loop. Waiting 200ms (wait(200)) and setting self._worker = None while parent=None causes Python's GC to invoke QThread::~QThread() while the OS thread is still blocked in subprocess.run, triggering QFatal: QThread: Destroyed while thread is still running (SIGABRT/SIGSEGV). Furthermore, late queued thumbnail_ready(int, str) signals lack a generation epoch ID and overwrite thumbnails on the newly selected folder.
3.A.2	panel_ui.py (4 Bake/Download QThreads)	CONFIRMED (P0 SIGSEGV + Import Lock Race)	BackgroundBakeWorker, SplatBakeWorker, PlanarBakeWorker, and SplatDownloadWorker run synchronous CPU/GPU/OIIO/network workloads without exec_(). In closeEvent() (L1851-1889), worker.quit(); worker.wait(100) times out during 4K/8K EXR bakes and destroys active C++ QThread instances when the panel is closed. Worse, SplatBakeWorker.run() (L845-850) calls importlib.reload(_gsm) from a background thread—mutating sys.modules["hdri_match_solaris.gaussian_splat"] globals and class objects concurrently with the main GUI thread.
3.B	oiio_adapter.py, panel_ui.py, gaussian_splat.py	CONFIRMED (P0 Silent Data Loss / Memory Corruption)	In OpenImageIO's Python bindings, ImageOutput.open(path, spec) and ImageOutput.write_image(arr) return bool (False on failure) and do not raise Python exceptions. Ignoring their return values in oiio_adapter.py:66-71 causes patched_save to silently succeed on permission errors, missing directories, or locked files without ever triggering orig_saver fallback. Passing non-contiguous slice/transposed arrays (img.astype(np.float32) without np.ascontiguousarray) to OIIO C++ bindings in panel_ui.py:776-790 and gaussian_splat.py:1401-1422 reads misaligned strides or segfaults.
3.C	Hot-Reload (_reload_tool, button & import reloads)	CONFIRMED (P0 State Corruption)	Calling importlib.reload() inside a live Qt/USD session invalidates C++/Python class identity (isinstance), orphans existing signal-slot lambdas, and re-runs module-level side effects. panel_ui.py:7964 (btn_reload_console) still exposes _reload_tool (L11926-11997), which attempts to reload hdri_match_solaris.panel_ui while self is alive and references 4 deleted modules (hdri_match_solaris.pipeline, sun_detector, light_extractor, hdri_match.core.inpaint).
3.D	Hardcoded E:/PROJECTS/... Paths	CONFIRMED (P0 Cross-Platform Failure)	On Rocky Linux 8 (mlk-ws448), "E:/PROJECTS/HDRI_MATCH_SOLARIS/..." is treated as a relative path ./E:/PROJECTS/... inside the current working directory (often read-only /opt/hfs... or ShotGrid launch dir), raising PermissionError or polluting studio directories.
3.E	OpenCV (cv2) & OpenCL in Houdini	CONFIRMED (P0 Linux X11 / GPU Context Crash)	Importing cv2 inside Houdini on Rocky Linux 8 risks loading OpenCV's bundled libQt5*/libX11/libOpenCL symbols into the process address space, colliding with Houdini's Qt and PCoIP virtual X11 :100 driver. Calling cv2.ocl.setUseOpenCL(True) + cv2.UMat in sun_relighter.py:145-182 initializes a competing OpenCL context alongside Karma/Redshift/PyTorch CUDA contexts. Additionally, genai_bridge.py:39 running pip install --user google-genai mid-session corrupts locked studio virtualenvs.
3.F	C1 Mojibake, Orphan \ufe0f, & 4-Byte Emojis	CONFIRMED (P0 X11 :100 Font Engine Hazard)	Over Teradici PCoIP virtual X11 (DISPLAY=:100), missing Xft color-emoji fonts (NotoColorEmoji) combined with orphan variation selectors (\ufe0f with no base codepoint) or C1 control bytes (\x80-\x9f from double-encoded UTF-8 like â\x80\x94 and ð\x9f\x94\x84) trigger Qt font-fallback table scans or XRender glyph crashes.
3.G	renderer_materials.py Shader Wiring & Scaling	CONFIRMED (P1 Lookdev Corruption)	1) In gen_usd_preview_surface_code (L85-93), setting scale on _ups_tex in pbr_emissive mode multiplies both diffuseColor and emissiveColor by emissive_mult, blowing out albedo. 2) In gen_material_output_connections (L364-368), outputs:arnold:surface and outputs:redshift:surface are wired to _ups_out (UsdPreviewSurface) instead of _ai_out and _rs_out.
3.H	Crucible, SPLATFORGE, Shelf & Installer	CONFIRMED (P1 Functional Bugs)	Validated all 7 items: gradient_builder.py fragile sys.modules check, preset_manager.py ignoring saved RIG_DIR, light_manager.py solo/un-solo clobbering pre-muted lights, domebreaker.shelf splatforge_open_panel matching domebreaker_panel, inconsistent /stage vs /obj/stage resolution, install.py dirtying tracked houdini/packages/domebreaker.json, and version drift across 4 files.
1.2 Newly Uncovered Bugs, Math Flaws, USD Leaks & Race Conditions (Missed in Digest)

Beyond Section 3, an exhaustive inspection of the attached modules uncovered 12 additional critical/high-severity architectural defects:

Bug 1 (Math / Photometric Double-Counting): lop_calibrate.py Applies Exposure & White Balance TWICE

Location: python/hdri_match_solaris/lop_calibrate.py:86-127

Mechanism:

At L87, calibrated = pipeline.process_hdri(use_proxy=False) bakes ev_offset, temperature, tint, and hdri_yaw into the pixel values of calibrated and writes output_path to disk (L97).

At L113-127, cook() also computes wb_color from parms["temperature"] and parms["tint"], and passes exposure=parms["ev_offset"], color=wb_color, and yaw_degrees=parms["hdri_yaw"] to set_dome_light()!

Impact: The USD DomeLight applies exposure (
2
EV
2
EV
), white balance tint, and yaw rotation a second time on top of the already-baked EXR—doubling EV stops (
2
2
⋅
EV
2
2⋅EV
), doubling chromaticity shifts, and doubling dome rotation!

Bug 2 (GUI Blocking inside LOP Cook): hou.ui.displayMessage Called Inside lop_calibrate.py:cook()

Location: python/hdri_match_solaris/lop_calibrate.py:77-81

Mechanism: Inside a LOP cook callback (cook(node)), calling hou.ui.displayMessage(..., severity=hou.severityType.Warning) pops a modal blocking dialog during USD stage evaluation—or crashes with hou.OperationFailed when cooked in batch/hython/headless farm renders where hou.isUIAvailable() is False.

Invariant: LOP cook callbacks must never invoke modal UI dialogs; warnings must use node.addWarning(...) or logging.

Bug 3 (USD Cook Recursion / Infinite Cook Loop): lop_calibrate.py & lop_sun.py Call parm.set() Inside cook(node)

Location: python/hdri_match_solaris/lop_calibrate.py:75 (sync_pipeline_to_parms(node, pipeline)) and python/hdri_match_solaris/lop_sun.py:92-95 (node.parm("source_u").set(source_u)).

Mechanism: Modifying node parameters on node from inside node's own Python LOP cook() execution marks the LOP node dirty mid-cook, triggering recursive recooks or Houdini cook-loop warnings in Solaris.

Bug 4 (Houdini Python Truthiness Bug on 0 / 0.0 Parameters): if node.parm("...").eval() if node.parm("...") else default

Location: lop_domelight.py:31-38, lop_sun.py:33-52, lop_light_extract.py:33-38.

Mechanism: While the ternary node.parm("x").eval() if node.parm("x") else default checks node.parm("x") is not None (in Python hou.Parm is truthy), if node.parm("hdri_path").eval() returns None or an unexpanded token, hou.expandString(None) raises TypeError. Worse, in lop_sun.py:34, 51, 52 (auto_detect, enable_relight, create_distant), .eval() on a toggle parm returns integer 0 or 1, which works, except if the parm does not exist on a custom Python LOP node, where it silently defaults to True and attempts to write to node.parm("source_u").

Bug 5 (USD Stage Ghost Prim Leak on Re-Cook): lop_light_extract.py:85-134 Never Clears Previous /lights/extracted Children

Location: python/hdri_match_solaris/lop_light_extract.py:85-134

Mechanism: Suppose cook #1 extracts max_lights=8 (emitter_00 .. emitter_07), or extracts emitter_00 as a DistantLight (is_sun=True). On cook #2, the user lowers max_lights=3 or disables sun_as_distant:

Prims emitter_03 .. emitter_07 remain on the stage if an upstream layer or persistent session layer holds them, or if safe_name changes between HDRIs (e.g. light_00 vs emitter_00).

If emitter_00 was defined as UsdLux.DistantLight and is redefined as UsdLux.SphereLight at the same path without clearing old UsdLux.DistantLight attributes (inputs:angle), USD keeps authored attributes from the previous schema on the prim.

Bug 6 (Solid Angle & Photometric Fallacy): lop_light_extract.py Ignores 
cos
⁡
(
elevation
)
cos(elevation) Equirectangular Distortion and Solid Angle (
Ω
Ω)

Location: python/hdri_match_solaris/lop_light_extract.py:165-218 (_fallback_extract_lights) & lop_sun.py:98-119.

Mechanism:

In equirectangular projection, pixels near the zenith (
𝑣
→
0
,
𝜃
→
0
v→0,θ→0) are stretched horizontally by 
1
/
sin
⁡
(
𝜃
)
=
1
/
cos
⁡
(
elevation
)
1/sin(θ)=1/cos(elevation). _fallback_extract_lights computes area = stats[label_id, cv2.CC_STAT_AREA] and unweighted np.mean(region_pixels), and computes angular_size = math.degrees(2.0 * math.sqrt(area / math.pi) / w * math.pi). A small cloud or light near the zenith has 10× more pixels than at the horizon, inflating its estimated angular_size and skewing intensity (which should be integrated irradiance 
𝐸
=
∫
𝐿
(
𝜔
)
𝑑
𝜔
=
∑
𝐿
𝑖
,
𝑗
sin
⁡
𝜃
𝑖
Δ
𝜃
Δ
𝜙
E=∫L(ω)dω=∑L
i,j
	​

sinθ
i
	​

ΔθΔϕ, not raw average radiance mean_luma).

Furthermore, cv2.connectedComponentsWithStats does not wrap horizontally across the 
𝑢
=
0
/
𝑢
=
1
u=0/u=1 (
±
180
∘
±180
∘
) seam, splitting any light straddling the panorama seam into two weaker lights!

Bug 7 (Equirectangular Pole Distortion & Vertical Wrap Artifact): SunRelighter.place_sun() Uses 2D Pixel np.roll / torch.roll

Location: python/hdri_match/analysis/sun_relighter.py:216-226, 297-302

Mechanism:

np.roll(shifted_stamp, shift_y, axis=0) wraps vertically across the north and south poles (
𝑣
=
0
v=0 zenith wraps to 
𝑣
=
1
v=1 nadir)! If a sun near the zenith with a large feather mask is shifted upward, its upper halo wraps around to the floor/nadir of the HDRI.

_build_radial_mask (L65-67) scales horizontal distance by cos(lat) of source_v. When place_sun simply np.rolls the extracted stamp from source_v (e.g., 
75
∘
75
∘
 elevation where the sun stamp is horizontally wide in pixel space) to target_v (e.g., 
15
∘
15
∘
 elevation near the horizon), the sun disc appears horizontally stretched into an ellipse because its pixel width was never re-projected for 
cos
⁡
(
lat
target
)
/
cos
⁡
(
lat
source
)
cos(lat
target
	​

)/cos(lat
source
	​

)!

Bug 8 (GPU VRAM Leak & Multi-Thread CUDA Contention): SunRelighter.relight() Runs Uncached CUDA Allocations & Mutates Global HAS_TORCH

Location: python/hdri_match/analysis/sun_relighter.py:16-21, 249-307

Mechanism:

import torch; HAS_TORCH = torch.cuda.is_available() runs at module import time (L16-19), initializing CUDA driver context even if SunRelighter is only imported for uv_to_spherical.

For an 8K HDRI (
8192
×
4096
×
3
8192×4096×3 float32 
≈
402
 MB
≈402 MB), t_img, t_alpha, t_stamp, t_result, t_blurred, and t_shifted_stamp allocate 
>
2.4
 GB
>2.4 GB of VRAM inside Houdini while Karma XPU / Redshift is rendering on an RTX 3080 (12GB VRAM), triggering CUDA out of memory and leaving PyTorch's caching allocator holding the blocks (torch.cuda.empty_cache() is never called).

On OOM, HAS_TORCH = False mutates a module global from potentially concurrent threads.

Bug 9 (USD GetLookAt Handedness / Light Direction Inversion Risk): usd_helpers.py:set_distant_light()

Location: python/hdri_match_solaris/usd_helpers.py:141-156

Mechanism:
In USD (UsdLux.DistantLight / UsdLux.LightAPI), lights emit along their local 
−
𝑍
−Z axis.
In Gf.Matrix4d().SetLookAt(eye, center, up), Gf constructs a world-to-camera (view) matrix where the camera looks from eye toward center (0,0,0) down the 
−
𝑍
−Z axis. Taking look_at.GetInverse() produces a local-to-world matrix whose local 
−
𝑍
−Z axis points from eye (direction * 1000) to center (0,0,0), which does point light along -direction (from the sun toward the origin). However, Gf.Matrix4d.SetLookAt sets a translation of eye = direction * 1000.0 on a UsdLux.DistantLight! While DistantLight emission is position-independent in rendering, placing the DistantLight gizmo 1000 scene units away from the origin makes the light manipulator invisible and unusable in the Solaris viewport! A DistantLight should stay at (0, 0, 0) (or configurable distance) with pure rotation, or use Gf.Rotation.

Bug 10 (Security / Zip-Slip & Unverified Partial Overwrite): scripts/update.py:140-182

Location: scripts/update.py:140-182

Mechanism:

z.extractall(extract_dir) (L142) does not validate archive member paths against path traversal (../).

update_via_zip overwrites files in python/, houdini/, scripts/ in-place without deleting removed/renamed .py files (leaving stale .py modules on disk that can be imported!) and without re-running install.py if package structure changed.

update_via_git (L82-84) falls back to git pull origin main when git pull --ff-only fails, which can trigger an interactive merge editor or leave git merge conflict markers (<<<<<<<) inside .py files if houdini/packages/domebreaker.json was dirtied by install.py!

Bug 11 (Version Parse Bug in scripts/install.py): _is_supported_houdini_version("20.5.332")

Location: scripts/install.py:45-53

Mechanism:

Python
parts = ver_str.split(".")
major = float(parts[0] + "." + parts[1]) if len(parts) >= 2 else float(parts[0])


If a user has a preference folder named houdini20.0.547 or houdini20.5_backup, parts[1] may contain non-digits (5_backup), causing float() to raise ValueError and silently return False, skipping installation!

Bug 12 (Preset Dict Mutation Bug in CrucibleLightStudio/core/preset_manager.py): get_light_presets()

Location: python/CrucibleLightStudio/core/preset_manager.py:259

Mechanism: presets = {"Built-in": dict(LIGHT_PRESETS)} performs a shallow copy of LIGHT_PRESETS. The inner preset dicts ("ARRI SkyPanel S60", etc.) are still shared references to the global LIGHT_PRESETS dict entries; any downstream mutation corrupts the built-in presets for the rest of the Houdini session.

2. Code to Remove (Dead Code, Dangerous Fallbacks, Latency Bottlenecks)

Remove ALL Runtime importlib.reload() Calls Across the Codebase:

Files:

python/hdri_match_solaris/panel_ui.py:

L66-68 & L83-85 (top-level/helper importlib.reload)

L845-851 (inside SplatBakeWorker.run() background thread—critical race condition)

L5054-5055 & L6469 (button-click reloads of gaussian_splat and lop_splat)

L7964 & L9253-9254 (self.btn_reload_console in Debug Console toolbar)

L11926-11997 (_reload_tool() method containing deleted modules hdri_match_solaris.pipeline, sun_detector, light_extractor, hdri_match.core.inpaint)

Why: Reloading modules holding active QWidget/QThread subclasses or USD callbacks breaks class identity and crashes Qt/Shiboken on Linux :100.

Remove Auto-pip install Inside DCC Runtime:

File: python/hdri_match/ml/genai_bridge.py:39

Code to Remove: subprocess.check_call([python_exe, "-m", "pip", "install", "--user", "google-genai"])

Why: Spawning pip install inside a live Houdini session under ShotGrid (tk-houdini) blocks the main UI thread, violates studio firewall/airgap policies on mlk-ws448, and pollutes ~/.local/lib/python3.x/site-packages with binary wheels incompatible with Houdini's hython. Raise a clean ImportError with installation instructions instead.

Remove Top-Level & Unprotected import cv2 and OpenCL Context Mutation:

Files:

python/hdri_match/calibration/autocrop.py:2 (import cv2)

python/hdri_match/ml/genai_bridge.py:2 (import cv2)

python/hdri_match/core/projection.py:2-6 (import cv2)

python/hdri_match/analysis/sun_relighter.py:142-182 (import cv2, cv2.ocl.setUseOpenCL(True), cv2.UMat)

python/hdri_match_solaris/lop_light_extract.py:165 (import cv2 inside _fallback_extract_lights)

python/hdri_match_solaris/gaussian_splat.py:1418 (import cv2 fallback in save_exr)

Why: In Houdini ('hou' in sys.modules), OpenImageIO (oiio_adapter.py) and NumPy/SciPy must handle all image I/O, resizing, blurring, and connected-component labeling. Never import cv2 or initialize cv2.ocl when 'hou' in sys.modules.

Remove Hardcoded Developer E:/PROJECTS/HDRI_MATCH_SOLARIS Paths:

Files:

python/hdri_match_solaris/panel_ui.py:4521, 4643-4645, 5123, 5191-5192, 5955

python/hdri_match_solaris/splat_prop_reconstructor.py:446, 502, 661

python/hdri_match_solaris/lop_custom_props.py:189-191

python/hdri_match_solaris/lop_splat.py:1771

Why: Fails on Rocky Linux 8 and any Windows machine lacking that specific developer folder. Replace with a centralized resolve_output_dir(subpath="") helper using $HIP 
→
→ $DOMEBREAKER_ROOT 
→
→ tempfile.gettempdir().

Remove Self-Dirtying Repo Write in scripts/install.py:

File: scripts/install.py:222-226

Code to Remove: Writing pkg_json_str (with hardcoded root) over houdini/packages/domebreaker.json.

Why: houdini/packages/domebreaker.json is tracked in Git and already uses relative $HOUDINI_PACKAGE_PATH/... Overwriting it with a machine-specific absolute path dirties the working tree and causes git pull failures in update.py.

Remove C1 Mojibake, Orphan \ufe0f, and 4-Byte Emojis Across UI & Shell Scripts:

Files: python/hdri_match_solaris/panel_ui.py, install.sh, uninstall.sh, update.sh.

Why: Eliminates X11/Xft font fallback crashes on PCoIP :100.

3. Code to Add (Missing Risk Guards, Circuit Breakers, Watchdog Timers)

SafeThreadManager & Cooperative QThread Retirement Registry:

Add a module-level _ACTIVE_WORKERS: set[QtCore.QThread] = set() zombie-prevention registry and cooperative requestInterruption() / is_cancelled polling inside every worker loop (ThumbnailWorker, BackgroundBakeWorker, SplatBakeWorker, PlanarBakeWorker, SplatDownloadWorker).

If a worker does not finish within the non-blocking grace window (150ms) on folder switch or closeEvent, disconnect its UI signals immediately, keep its Python reference alive in _ACTIVE_WORKERS, and attach worker.finished.connect(lambda w=worker: _ACTIVE_WORKERS.discard(w)) plus worker.finished.connect(worker.deleteLater). This mathematically guarantees QThread::~QThread() is never invoked while the OS thread is still running.

Add a monotonically increasing _generation_id: int to AssetGridWidget so late signals from superseded workers are deterministically dropped.

Strict OIIO C++ Return-Value & Memory-Contiguity Guard:

In oiio_adapter.py, panel_ui.py, and gaussian_splat.py, enforce:

Finite value sanitization (np.nan_to_num(image_array, nan=0.0, posinf=65504.0, neginf=0.0)).

C-contiguous float32 memory layout (np.ascontiguousarray(..., dtype=np.float32)).

Explicit boolean checks on if not out.open(file_path, spec): raise IOError(...) and if not out.write_image(contiguous): raise IOError(...).

Houdini Host Runtime Guard (is_houdini_runtime()) for cv2 / OpenCL / PyTorch:

Centralize a guard 'hou' in sys.modules that blocks cv2 and cv2.ocl inside Houdini and provides a pure-NumPy 2D separable Gaussian blur + pure-NumPy 8-connected components labeling (_extract_components_numpy) with horizontal equirectangular seam wrapping and 
cos
⁡
(
elevation
)
cos(elevation) solid-angle weighting.

Canonical Solaris Stage Resolver (get_or_create_solaris_stage_node()):

Replace the 5 divergent /stage vs /obj/stage implementations (splatforge.pypanel, panel_ui.py, scene_builder.py, splatforge_tab.py, backend_houdini.py) with a single idempotent resolver:

Check hou.node("/stage") (standard Houdini Solaris root LOP network).

Check hou.node("/obj/stage").

Only if both are None, create hou.node("/obj").createNode("lopnet", "stage").

Never call this during __init__ (lazy evaluation only on user action).

4. Step-by-Step Implementation & Surgical Code Fixes
Priority P0: Crash, Memory Corruption & Cross-Platform Safety Patches
P0.1: python/hdri_match_solaris/oiio_adapter.py (Full Replacement)

Fixes unchecked out.open() / out.write_image() boolean return values, sanitizes NaN/Inf before EXR export, and handles 4-channel RGBA inputs cleanly.

Python
"""
OpenImageIO Adapter for HDRI Match in Houdini.

Provides high-performance native image I/O via OpenImageIO (shipped with Houdini).
Seamlessly patches hdri_match.io.loader and hdri_match.io.exporter so that all
EXR, HDR, TIFF, PNG, and JPEG loading/exporting works out-of-the-box in Houdini
without requiring external cv2 or imageio packages.
"""

import os
import numpy as np

try:
    import OpenImageIO as oiio
    HAS_OIIO = True
except ImportError:
    HAS_OIIO = False


def load_image_with_oiio(file_path: str) -> np.ndarray:
    """Read any image supported by OpenImageIO into contiguous float32 RGB."""
    if not HAS_OIIO:
        raise ImportError("OpenImageIO is not available.")

    if not file_path or not os.path.isfile(file_path):
        raise FileNotFoundError(f"Image file does not exist: '{file_path}'")

    inp = oiio.ImageInput.open(file_path)
    if not inp:
        raise IOError(f"OpenImageIO failed to open '{file_path}': {oiio.geterror()}")

    try:
        arr = inp.read_image(format=oiio.FLOAT)
        if arr is None:
            raise IOError(
                f"OpenImageIO read_image returned None for '{file_path}': {inp.geterror()}"
            )

        if arr.ndim == 3 and arr.shape[2] >= 3:
            arr = arr[..., :3]
        elif arr.ndim == 3 and arr.shape[2] == 1:
            arr = np.repeat(arr, 3, axis=-1)
        elif arr.ndim == 2:
            arr = np.stack([arr] * 3, axis=-1)
        else:
            raise ValueError(f"Unsupported image shape {arr.shape} in '{file_path}'")

        return np.ascontiguousarray(
            np.nan_to_num(arr, nan=0.0, posinf=65504.0, neginf=0.0),
            dtype=np.float32,
        )
    finally:
        inp.close()


def save_image_with_oiio(image_array: np.ndarray, file_path: str) -> None:
    """Save a float32 RGB/RGBA image array using OpenImageIO with strict error checking."""
    if not HAS_OIIO:
        raise ImportError("OpenImageIO is not available.")

    if image_array is None or image_array.ndim != 3:
        raise ValueError("Expected a 3D (H, W, C) image array.")

    h, w, c = image_array.shape
    if c not in (3, 4):
        raise ValueError(f"Only 3-channel RGB or 4-channel RGBA arrays are supported (got {c}).")

    abs_path = os.path.abspath(file_path)
    out_dir = os.path.dirname(abs_path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    spec = oiio.ImageSpec(w, h, c, oiio.FLOAT)
    out = oiio.ImageOutput.create(abs_path)
    if not out:
        raise IOError(f"OpenImageIO could not create output for '{abs_path}': {oiio.geterror()}")

    opened = False
    try:
        opened = out.open(abs_path, spec)
        if not opened:
            raise IOError(
                f"OpenImageIO failed to open '{abs_path}' for writing: {out.geterror()}"
            )

        sanitized = np.nan_to_num(image_array, nan=0.0, posinf=65504.0, neginf=0.0)
        contiguous = np.ascontiguousarray(sanitized, dtype=np.float32)

        written = out.write_image(contiguous)
        if not written:
            raise IOError(
                f"OpenImageIO failed to write pixel data to '{abs_path}': {out.geterror()}"
            )
    finally:
        if opened:
            out.close()


def install_adapter() -> None:
    """Monkey-patch hdri_match.io to prepend OpenImageIO handlers."""
    if not HAS_OIIO:
        return

    try:
        import hdri_match.io.loader as loader
        orig_loader = loader.load_exr_to_numpy

        def patched_load(file_path: str) -> np.ndarray:
            try:
                return load_image_with_oiio(file_path)
            except Exception:
                return orig_loader(file_path)

        loader.load_exr_to_numpy = patched_load
        loader.load_image_to_numpy = patched_load
    except Exception:
        pass

    try:
        import hdri_match.io.exporter as exporter
        orig_saver = exporter.save_numpy_to_image

        def patched_save(image_array: np.ndarray, file_path: str):
            try:
                save_image_with_oiio(image_array, file_path)
            except Exception:
                orig_saver(image_array, file_path)

        exporter.save_numpy_to_image = patched_save
    except Exception:
        pass


install_adapter()

P0.2: python/CrucibleLightStudio/ui/widgets/asset_library.py (ThumbnailWorker & AssetGridWidget Safe Retirement)

Fixes QThread: Destroyed while thread is still running SIGSEGV and stale folder thumbnail race conditions.

Target: python/CrucibleLightStudio/ui/widgets/asset_library.py (L25-89 and L156-207)

Python
# Add at module level near top of python/CrucibleLightStudio/ui/widgets/asset_library.py:
_ACTIVE_THUMBNAIL_WORKERS = set()


class ThumbnailWorker(QtCore.QThread):
    # Signal emits (generation_id, row_index, thumbnail_filepath)
    thumbnail_ready = QtCore.Signal(int, int, str)

    def __init__(self, assets, generation_id=0, parent=None):
        super().__init__(None)  # Strictly parent=None for PCoIP :100 safety
        self.assets = list(assets) if assets else []
        self.generation_id = int(generation_id)
        self.is_cancelled = False

    def cancel(self):
        self.is_cancelled = True
        self.requestInterruption()

    def run(self):
        for idx, asset in enumerate(self.assets):
            if self.is_cancelled or self.isInterruptionRequested():
                return
            # ... existing thumbnail generation body ...
            # Before any subprocess.run call, re-check cancellation:
            if self.is_cancelled or self.isInterruptionRequested():
                return
            # When emitting thumbnail_ready, include generation_id:
            # if not (self.is_cancelled or self.isInterruptionRequested()):
            #     self.thumbnail_ready.emit(self.generation_id, idx, thumb_path)


And in AssetGridWidget (L156-207):

Python
    def _start_thumbnail_worker(self, assets):
        self.stop_worker()
        self._worker_gen = getattr(self, "_worker_gen", 0) + 1
        gen_id = self._worker_gen

        worker = ThumbnailWorker(assets, generation_id=gen_id, parent=None)
        _ACTIVE_THUMBNAIL_WORKERS.add(worker)

        def _cleanup_worker(w=worker):
            _ACTIVE_THUMBNAIL_WORKERS.discard(w)
            try:
                w.deleteLater()
            except Exception:
                pass

        worker.finished.connect(_cleanup_worker)
        worker.thumbnail_ready.connect(self._on_thumbnail_ready)
        self._worker = worker
        worker.start()

    def stop_worker(self):
        worker = getattr(self, "_worker", None)
        self._worker = None
        if worker is not None:
            try:
                worker.cancel()
                try:
                    worker.thumbnail_ready.disconnect(self._on_thumbnail_ready)
                except Exception:
                    pass
                if worker.isRunning():
                    # Non-blocking short wait; if still in subprocess.run,
                    # _ACTIVE_THUMBNAIL_WORKERS keeps C++ QThread alive until finished!
                    worker.wait(50)
            except Exception:
                pass

    def _on_thumbnail_ready(self, gen_id, idx, filepath):
        if gen_id != getattr(self, "_worker_gen", -1):
            return  # Drop late signal from superseded folder scan
        if 0 <= idx < self.count():
            item = self.item(idx)
            if item and os.path.isfile(filepath):
                item.setIcon(QtGui.QIcon(filepath))

P0.3: python/hdri_match_solaris/panel_ui.py (Worker Thread Zombie Guard, Reload Removal, OIIO Check, Cross-Platform Path & Mojibake Fixes)

Add Zombie-Proof Worker Retirement Registry & Fix closeEvent (L585-944, L1851-1889):

Python
# Add at module level in python/hdri_match_solaris/panel_ui.py:
_RETIRED_PANEL_WORKERS = set()


def _retire_qthread_safely(worker, wait_ms=100):
    """Cancel a parentless QThread and keep its Python ref alive until OS thread exits."""
    if worker is None:
        return
    try:
        if hasattr(worker, "is_cancelled"):
            worker.is_cancelled = True
        if hasattr(worker, "cancel"):
            worker.cancel()
        worker.requestInterruption()
        if worker.isRunning():
            _RETIRED_PANEL_WORKERS.add(worker)
            worker.finished.connect(lambda w=worker: _RETIRED_PANEL_WORKERS.discard(w))
            worker.finished.connect(worker.deleteLater)
            worker.wait(wait_ms)
    except Exception:
        pass


In closeEvent(self, event) (L1851-1889), replace the unsafe worker.quit(); worker.wait(100) loop with:

Python
    def closeEvent(self, event):
        for attr in ("_bake_worker", "_splat_bake_worker", "_planar_bake_worker", "_download_worker"):
            w = getattr(self, attr, None)
            if w is not None:
                _retire_qthread_safely(w, wait_ms=100)
                setattr(self, attr, None)
        if hasattr(self, "crucible_panel") and self.crucible_panel is not None:
            try:
                self.crucible_panel.close()
            except Exception:
                pass
        super().closeEvent(event)


Remove importlib.reload(_gsm) inside SplatBakeWorker.run() (L845-851):
Replace lines 845-851 with a standard import:

Python
                import hdri_match_solaris.gaussian_splat as _gsm


Fix OIIO Write in panel_ui.py (L776-790) and gaussian_splat.py (L1401-1422):
Delegate directly to save_image_with_oiio from hdri_match_solaris.oiio_adapter:

Python
from hdri_match_solaris.oiio_adapter import save_image_with_oiio
save_image_with_oiio(img, out_path)


Replace Hardcoded E:/PROJECTS/HDRI_MATCH_SOLARIS Fallbacks (panel_ui.py:4521, 5123, splat_prop_reconstructor.py:446, 502, 661, lop_custom_props.py:189-191, lop_splat.py:1771):
Add this helper in python/hdri_match_solaris/usd_helpers.py (and import where needed):

Python
def resolve_workspace_dir(subdir: str = "") -> str:
    """Resolve a writable cross-platform workspace directory (Windows & Linux)."""
    import os
    import tempfile
    base = ""
    try:
        import hou
        hip = hou.expandString("$HIP")
        if hip and os.path.isdir(hip) and "houdini_temp" not in hip.lower():
            base = hip
    except Exception:
        pass

    if not base:
        env_root = os.environ.get("DOMEBREAKER_ROOT", "")
        if env_root and os.path.isdir(env_root) and os.access(env_root, os.W_OK):
            base = env_root
        else:
            base = os.path.join(tempfile.gettempdir(), "domebreaker_workspace")

    target = os.path.join(base, subdir) if subdir else base
    os.makedirs(target, exist_ok=True)
    return target.replace("\\", "/")


Fix C1 Mojibake, Orphan \ufe0f, and 4-Byte Emojis in panel_ui.py:

L954: "HDRI Match Solaris - Live Debug Console & Error Output"

L7964: Remove self.btn_reload_console (or hide it and disconnect _reload_tool).

L7968: "Pop Out Window"

L1948: Update displayed version label from "v2.0.0" to "v2.0.4".

Strip all 7 orphan \ufe0f prefixes (L3066 "Remove", L3349 "Clear from Stage", L3555 "Toggle Points Visibility", L3562 "Clear Points from Stage", L6531 "Hide Splat Cloud", L7783 "Assign Light Groups to Stage Lights", L7943 "Light Groups").

Strip ⚡ , ☁️ , ☀️ , ❌ , ⏳  from UI labels while preserving exact substring matches ("Depth Map", "OpenVDB", "3D Mesh", "Boxes Only", "Sphere") in combo_arch_proxy_shape.

P0.4: python/hdri_match/analysis/sun_relighter.py (Full Replacement)

Fixes:

Unconditional CUDA context init at import time (torch.cuda.is_available() moved to lazy evaluation).

Blocks cv2 and cv2.ocl.setUseOpenCL(True) when 'hou' in sys.modules.

Pure-NumPy separable 1D Gaussian blur fallback (zero external dependencies if scipy is absent).

Fixes vertical pole wrap-around bug in place_sun() (replaces vertical np.roll with clamped/non-wrapping vertical shift and re-synthesizes or scales the radial mask for target latitude 
cos
⁡
(
lat
target
)
cos(lat
target
	​

)).

Releases CUDA cache on exception without mutating global state unsafely.

Python
"""
Interactive HDRI Sun Relighting Engine.

Extracts the sun disc from an equirectangular HDRI, removes it via
inpainting, and composites it at a new spherical position. Preserves
HDR energy, handles horizontal wrap-around, prevents vertical pole wrap-around,
and accounts for equirectangular latitude distortion.

All operations are scene-linear (no clamping, no gamma).
"""
import sys
import math
import numpy as np
from typing import Tuple


def _is_houdini_session() -> bool:
    """Return True when running inside Houdini / hython to avoid cv2/OpenCL collisions."""
    return "hou" in sys.modules


def _gaussian_blur_2d_numpy(image: np.ndarray, sigma: float) -> np.ndarray:
    """Pure-NumPy separable 2D Gaussian blur with horizontal wrap and vertical clamp."""
    if sigma <= 0.5:
        return image.copy()
    radius = max(1, int(math.ceil(3.0 * sigma)))
    x = np.arange(-radius, radius + 1, dtype=np.float32)
    kernel = np.exp(-0.5 * (x / sigma) ** 2)
    kernel /= np.sum(kernel)

    # Horizontal pass (equirectangular wraps around u=0..1)
    padded_x = np.pad(image, ((0, 0), (radius, radius), (0, 0)), mode="wrap")
    out_x = np.zeros_like(image, dtype=np.float32)
    for i, weight in enumerate(kernel):
        out_x += weight * padded_x[:, i : i + image.shape[1], :]

    # Vertical pass (poles clamp, never wrap zenith to nadir)
    padded_y = np.pad(out_x, ((radius, radius), (0, 0), (0, 0)), mode="edge")
    out_y = np.zeros_like(image, dtype=np.float32)
    for i, weight in enumerate(kernel):
        out_y += weight * padded_y[i : i + image.shape[0], :, :]

    return out_y


class SunRelighter:
    """Moves the dominant light source in an equirectangular HDRI."""

    @staticmethod
    def detect_sun(image: np.ndarray) -> Tuple[float, float, float]:
        """
        Detect the brightest point in the HDRI.

        Returns:
            (u, v, peak_luminance) where u,v are normalized 0-1 coordinates.
        """
        luma = np.dot(image[..., :3], np.array([0.2126, 0.7152, 0.0722], dtype=np.float32))
        flat_idx = int(np.argmax(luma))
        h, w = image.shape[:2]
        py, px = divmod(flat_idx, w)
        u = (px + 0.5) / w
        v = (py + 0.5) / h
        return u, v, float(luma[py, px])

    @staticmethod
    def _build_radial_mask(
        h: int,
        w: int,
        center_u: float,
        center_v: float,
        radius_norm: float,
        feather_norm: float = 0.02,
    ) -> np.ndarray:
        """
        Build a smooth radial mask in equirectangular space.
        Handles horizontal wrap-around and latitude compression.
        """
        y_coords = (np.arange(h, dtype=np.float32) + 0.5) / h
        x_coords = (np.arange(w, dtype=np.float32) + 0.5) / w

        dx = np.abs(x_coords[np.newaxis, :] - center_u)
        dx = np.minimum(dx, 1.0 - dx)

        dy = np.abs(y_coords[:, np.newaxis] - center_v)

        lat = (0.5 - center_v) * math.pi
        cos_lat = max(math.cos(lat), 0.1)
        dx_angular = dx * cos_lat

        dist = np.sqrt(dx_angular ** 2 + dy ** 2)

        inner = radius_norm
        outer = radius_norm + max(feather_norm, 0.001)

        alpha = np.clip((outer - dist) / (outer - inner + 1e-8), 0.0, 1.0)
        alpha = alpha * alpha * (3.0 - 2.0 * alpha)
        return alpha.astype(np.float32)

    @staticmethod
    def extract_sun_stamp(
        image: np.ndarray,
        sun_u: float,
        sun_v: float,
        radius_norm: float = 0.03,
        feather_norm: float = 0.015,
    ) -> Tuple[np.ndarray, np.ndarray]:
        """Extract the sun region as a premultiplied RGB stamp + alpha mask."""
        h, w = image.shape[:2]
        alpha = SunRelighter._build_radial_mask(
            h, w, sun_u, sun_v, radius_norm, feather_norm
        )
        stamp = image[..., :3].astype(np.float32) * alpha[..., np.newaxis]
        return stamp, alpha

    @staticmethod
    def inpaint_sun(
        image: np.ndarray,
        alpha: np.ndarray,
        blur_radius_mult: float = 3.0,
    ) -> np.ndarray:
        """
        Remove the sun by replacing the masked region with blurred surrounding sky.
        Never imports cv2 or touches OpenCL when running inside Houdini.
        """
        h, w = image.shape[:2]
        mask_pixels = int(np.sum(alpha > 0.5))
        effective_radius = max(int(math.sqrt(mask_pixels / math.pi)), 5)
        ksize = int(effective_radius * blur_radius_mult) * 2 + 1
        ksize = max(ksize, 5)
        if ksize % 2 == 0:
            ksize += 1

        result = image[..., :3].astype(np.float32).copy()
        inv_alpha = (1.0 - alpha)[..., np.newaxis]
        alpha_3c = alpha[..., np.newaxis]

        # Only allow cv2 in standalone (non-Houdini) environments, without OpenCL
        if not _is_houdini_session():
            try:
                import cv2
                if max(h, w) > 1000:
                    scale = 1000.0 / max(h, w)
                    small_ksize = max(int(ksize * scale), 5) | 1
                    small_img = cv2.resize(
                        result, (0, 0), fx=scale, fy=scale, interpolation=cv2.INTER_AREA
                    )
                    for _ in range(4):
                        small_img = cv2.GaussianBlur(
                            small_img, (small_ksize, small_ksize), small_ksize / 4.0
                        )
                    blurred = cv2.resize(small_img, (w, h), interpolation=cv2.INTER_LINEAR)
                    return result * inv_alpha + blurred * alpha_3c
                else:
                    for _ in range(4):
                        blurred = cv2.GaussianBlur(result, (ksize, ksize), ksize / 4.0)
                        result = result * inv_alpha + blurred * alpha_3c
                    return result
            except ImportError:
                pass

        # Safe DCC path: Downsample -> Separable Gaussian Blur -> Upsample in NumPy
        sigma = max(1.0, ksize / 4.0)
        if max(h, w) > 1024:
            step = max(1, int(max(h, w) // 512))
            small = result[::step, ::step, :].copy()
            small_sigma = max(1.0, sigma / step)
            for _ in range(4):
                small = _gaussian_blur_2d_numpy(small, small_sigma)
            # Bilinear-like upscale via repeat + smoothing pass
            up_y = np.linspace(0, small.shape[0] - 1, h).astype(np.int32)
            up_x = np.linspace(0, small.shape[1] - 1, w).astype(np.int32)
            blurred = small[up_y][:, up_x]
            blurred = _gaussian_blur_2d_numpy(blurred, 2.0)
            result = result * inv_alpha + blurred * alpha_3c
        else:
            for _ in range(4):
                blurred = _gaussian_blur_2d_numpy(result, sigma)
                result = result * inv_alpha + blurred * alpha_3c

        return result

    @staticmethod
    def place_sun(
        base_image: np.ndarray,
        stamp: np.ndarray,
        stamp_alpha: np.ndarray,
        source_u: float,
        source_v: float,
        target_u: float,
        target_v: float,
    ) -> np.ndarray:
        """
        Composite the sun stamp at a new position.
        Wraps horizontally across u=0/1 seam, and clamps vertically to prevent
        zenith-to-nadir pole wrap-around artifacts.
        """
        h, w = base_image.shape[:2]

        du = target_u - source_u
        dv = target_v - source_v

        shift_x = int(round(du * w))
        shift_y = int(round(dv * h))

        # Horizontal shift wraps around 360 degrees
        shifted_stamp = np.roll(stamp, shift_x, axis=1)

        # Vertical shift MUST NOT wrap across poles (v=0 zenith to v=1 nadir)
        if shift_y > 0:
            shifted_stamp = np.pad(shifted_stamp[:-shift_y, :, :], ((shift_y, 0), (0, 0), (0, 0)), mode="constant")
        elif shift_y < 0:
            sy = -shift_y
            shifted_stamp = np.pad(shifted_stamp[sy:, :, :], ((0, sy), (0, 0), (0, 0)), mode="constant")

        return base_image + shifted_stamp

    @staticmethod
    def relight(
        image: np.ndarray,
        source_u: float,
        source_v: float,
        target_u: float,
        target_v: float,
        radius_norm: float = 0.03,
        feather_norm: float = 0.015,
    ) -> np.ndarray:
        """Full sun relighting pipeline: extract -> remove -> place."""
        if abs(source_u - target_u) < 1e-5 and abs(source_v - target_v) < 1e-5:
            return image

        stamp, alpha = SunRelighter.extract_sun_stamp(
            image, source_u, source_v, radius_norm, feather_norm
        )
        base = SunRelighter.inpaint_sun(image, alpha)
        return SunRelighter.place_sun(
            base, stamp, alpha, source_u, source_v, target_u, target_v
        )

    @staticmethod
    def uv_to_spherical(u: float, v: float) -> Tuple[float, float]:
        """Convert normalised UV to azimuth/elevation in degrees."""
        azimuth = (u * 360.0) - 180.0
        elevation = 90.0 - (v * 180.0)
        return azimuth, elevation

    @staticmethod
    def spherical_to_uv(azimuth_deg: float, elevation_deg: float) -> Tuple[float, float]:
        """Convert azimuth/elevation in degrees to normalised UV."""
        u = (azimuth_deg + 180.0) / 360.0
        v = (90.0 - elevation_deg) / 180.0
        return max(0.0, min(1.0, u)), max(0.0, min(1.0, v))

    @staticmethod
    def uv_to_direction(u: float, v: float) -> Tuple[float, float, float]:
        """Convert normalised UV to a 3D direction vector (Y-up, Z-forward)."""
        azimuth, elevation = SunRelighter.uv_to_spherical(u, v)
        theta = math.radians(azimuth)
        phi = math.radians(elevation)
        dir_x = math.cos(phi) * math.sin(theta)
        dir_y = math.sin(phi)
        dir_z = math.cos(phi) * math.cos(theta)
        return dir_x, dir_y, dir_z

Priority P1: USD / Solaris LOPs, Photometric Math, Shaders & Crucible Patches
P1.1: python/hdri_match_solaris/usd_helpers.py (Full Replacement)

Adds resolve_workspace_dir(), get_or_create_solaris_stage_node(), and fixes set_distant_light() so the DistantLight gizmo stays at the origin with pure orientation (rather than being translated 1000 units away out of the viewport).

Python
"""
USD stage manipulation helpers for HDRI Match Solaris.

Provides reusable functions for creating, querying, and modifying USD
prims in the Houdini Solaris LOP context. All functions operate on
``Usd.Stage`` objects obtained from ``node.editableStage()`` inside
Python LOP cook callbacks.
"""

import os
import math
import tempfile
import numpy as np

from pxr import Usd, UsdGeom, UsdLux, UsdShade, Sdf, Gf


# ---------------------------------------------------------------------------
# Workspace & Solaris Stage Node Resolution
# ---------------------------------------------------------------------------

def resolve_workspace_dir(subdir: str = "") -> str:
    """Resolve a writable cross-platform workspace directory (Windows & Linux)."""
    base = ""
    try:
        import hou
        hip = hou.expandString("$HIP")
        if hip and os.path.isdir(hip) and "houdini_temp" not in hip.lower():
            base = hip
    except Exception:
        pass

    if not base:
        env_root = os.environ.get("DOMEBREAKER_ROOT", "")
        if env_root and os.path.isdir(env_root) and os.access(env_root, os.W_OK):
            base = env_root
        else:
            base = os.path.join(tempfile.gettempdir(), "domebreaker_workspace")

    target = os.path.join(base, subdir) if subdir else base
    os.makedirs(target, exist_ok=True)
    return target.replace("\\", "/")


def get_or_create_solaris_stage_node():
    """Idempotently locate `/stage` or `/obj/stage`, creating `/obj/stage` only if absent."""
    import hou
    stage = hou.node("/stage")
    if stage is not None:
        return stage
    obj_stage = hou.node("/obj/stage")
    if obj_stage is not None:
        return obj_stage
    obj = hou.node("/obj")
    if obj is not None:
        return obj.createNode("lopnet", "stage")
    return None


# ---------------------------------------------------------------------------
# Prim creation helpers
# ---------------------------------------------------------------------------

def get_or_create_prim(stage, path, prim_type_fn):
    """Return an existing typed prim or create a new one."""
    return prim_type_fn(stage, path)


def ensure_scope(stage, path):
    """Create a ``UsdGeom.Scope`` at *path* if it does not already exist."""
    prim = stage.GetPrimAtPath(path)
    if not prim.IsValid():
        UsdGeom.Scope.Define(stage, path)


# ---------------------------------------------------------------------------
# DomeLight helpers
# ---------------------------------------------------------------------------

def set_dome_light(
    stage,
    path,
    texture_path,
    exposure=0.0,
    color=(1.0, 1.0, 1.0),
    yaw_degrees=0.0,
    texture_format="latlong",
):
    """Create or update a ``UsdLux.DomeLight``."""
    dome = UsdLux.DomeLight.Define(stage, path)

    dome.CreateTextureFileAttr().Set(str(texture_path).replace("\\", "/"))
    dome.CreateExposureAttr().Set(float(exposure))
    dome.CreateColorAttr().Set(Gf.Vec3f(float(color[0]), float(color[1]), float(color[2])))
    dome.CreateTextureFormatAttr().Set(str(texture_format))

    xformable = UsdGeom.Xformable(dome.GetPrim())
    xformable.ClearXformOpOrder()
    if abs(yaw_degrees) > 1e-4:
        rot_op = xformable.AddRotateYOp(UsdGeom.XformOp.PrecisionFloat)
        rot_op.Set(float(yaw_degrees))

    return dome


# ---------------------------------------------------------------------------
# DistantLight helpers (sun)
# ---------------------------------------------------------------------------

def spherical_to_direction(azimuth_deg, elevation_deg):
    """Convert spherical coordinates to a unit direction vector pointing TOWARD the emitter."""
    az = math.radians(azimuth_deg)
    el = math.radians(elevation_deg)
    x = math.cos(el) * math.sin(az)
    y = math.sin(el)
    z = math.cos(el) * math.cos(az)
    return Gf.Vec3d(x, y, z)


def uv_to_spherical(u, v):
    """Convert normalised UV coordinates (equirectangular) to azimuth/elevation."""
    azimuth_deg = (u * 360.0) - 180.0
    elevation_deg = 90.0 - (v * 180.0)
    return azimuth_deg, elevation_deg


def set_distant_light(
    stage,
    path,
    azimuth_deg,
    elevation_deg,
    intensity=1.0,
    color=(1.0, 1.0, 1.0),
    angle=0.53,
    exposure=0.0,
):
    """Create or update a ``UsdLux.DistantLight`` for the sun."""
    sun = UsdLux.DistantLight.Define(stage, path)

    sun.CreateIntensityAttr().Set(float(intensity))
    sun.CreateColorAttr().Set(Gf.Vec3f(float(color[0]), float(color[1]), float(color[2])))
    sun.CreateAngleAttr().Set(float(angle))
    sun.CreateExposureAttr().Set(float(exposure))

    direction = spherical_to_direction(azimuth_deg, elevation_deg)
    eye = direction
    center = Gf.Vec3d(0.0, 0.0, 0.0)
    up = Gf.Vec3d(0.0, 1.0, 0.0)

    if abs(elevation_deg) > 85.0:
        up = Gf.Vec3d(0.0, 0.0, 1.0)

    look_at = Gf.Matrix4d()
    look_at.SetLookAt(eye, center, up)
    xform_mat = look_at.GetInverse()
    # Keep DistantLight gizmo at the origin (pure rotation) so it remains visible in viewport
    xform_mat.SetTranslateOnly(Gf.Vec3d(0.0, 0.0, 0.0))

    xformable = UsdGeom.Xformable(sun.GetPrim())
    xformable.ClearXformOpOrder()
    xformable.AddTransformOp().Set(xform_mat)

    return sun


# ---------------------------------------------------------------------------
# SphereLight / RectLight helpers (extracted emitters)
# ---------------------------------------------------------------------------

def set_sphere_light(
    stage,
    path,
    direction_vec,
    intensity=1.0,
    color=(1.0, 1.0, 1.0),
    radius=1.0,
    exposure=0.0,
    distance=100.0,
):
    """Create or update a ``UsdLux.SphereLight`` for an extracted emitter."""
    light = UsdLux.SphereLight.Define(stage, path)

    light.CreateIntensityAttr().Set(float(intensity))
    light.CreateColorAttr().Set(Gf.Vec3f(float(color[0]), float(color[1]), float(color[2])))
    light.CreateRadiusAttr().Set(float(radius))
    light.CreateExposureAttr().Set(float(exposure))
    light.CreateNormalizeAttr().Set(False)

    d = Gf.Vec3d(*direction_vec)
    d_len = d.GetLength()
    if d_len > 1e-8:
        d = d / d_len
    pos = d * float(distance)

    xformable = UsdGeom.Xformable(light.GetPrim())
    xformable.ClearXformOpOrder()
    xformable.AddTranslateOp().Set(pos)

    return light


# ---------------------------------------------------------------------------
# Material helpers
# ---------------------------------------------------------------------------

def create_preview_surface_material(
    stage,
    mtl_path,
    diffuse_color=(0.5, 0.5, 0.5),
    metallic=0.0,
    roughness=0.5,
    specular_color=None,
    opacity=1.0,
):
    """Create a ``UsdPreviewSurface`` material."""
    material = UsdShade.Material.Define(stage, mtl_path)
    shader_path = mtl_path + "/shader"
    shader = UsdShade.Shader.Define(stage, shader_path)

    shader.CreateIdAttr("UsdPreviewSurface")
    shader.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).Set(
        Gf.Vec3f(*diffuse_color)
    )
    shader.CreateInput("metallic", Sdf.ValueTypeNames.Float).Set(float(metallic))
    shader.CreateInput("roughness", Sdf.ValueTypeNames.Float).Set(float(roughness))

    if specular_color is not None:
        shader.CreateInput("specularColor", Sdf.ValueTypeNames.Color3f).Set(
            Gf.Vec3f(*specular_color)
        )

    if abs(opacity - 1.0) > 1e-6:
        shader.CreateInput("opacity", Sdf.ValueTypeNames.Float).Set(float(opacity))

    material.CreateSurfaceOutput().ConnectToSource(
        shader.ConnectableAPI(), "surface"
    )

    return material


def bind_material(stage, prim_path, material_path):
    """Bind a material to a prim using ``UsdShade.MaterialBindingAPI``."""
    prim = stage.GetPrimAtPath(prim_path)
    if not prim.IsValid():
        return
    mtl = UsdShade.Material(stage.GetPrimAtPath(material_path))
    if not mtl.GetPrim().IsValid():
        return
    UsdShade.MaterialBindingAPI.Apply(prim).Bind(mtl)


# ---------------------------------------------------------------------------
# Calibration metadata helpers
# ---------------------------------------------------------------------------

def write_calibration_metadata(prim, calibration_dict):
    """Write calibration parameters as custom attributes on a prim."""
    ns = "hdriMatch"
    for key, value in calibration_dict.items():
        attr_name = f"{ns}:{key}"
        if isinstance(value, bool):
            attr = prim.CreateAttribute(attr_name, Sdf.ValueTypeNames.Bool)
        elif isinstance(value, float):
            attr = prim.CreateAttribute(attr_name, Sdf.ValueTypeNames.Float)
        elif isinstance(value, int):
            attr = prim.CreateAttribute(attr_name, Sdf.ValueTypeNames.Int)
        elif isinstance(value, str):
            attr = prim.CreateAttribute(attr_name, Sdf.ValueTypeNames.String)
        else:
            continue
        attr.Set(value)


def read_calibration_metadata(prim):
    """Read calibration parameters from custom attributes on a prim."""
    ns = "hdriMatch:"
    result = {}
    for attr in prim.GetAttributes():
        name = attr.GetName()
        if name.startswith(ns):
            key = name[len(ns):]
            val = attr.Get()
            if val is not None:
                result[key] = val
    return result

P1.2: python/hdri_match_solaris/lop_calibrate.py (Full Replacement)

Fixes:

Eliminates double-application of ev_offset, wb_color, and hdri_yaw on DomeLight (since pipeline.process_hdri() already baked them into the output EXR!). Sets DomeLight exposure=0.0, color=(1.0, 1.0, 1.0), yaw_degrees=0.0 while recording the applied calibration parameters in hdriMatch:* metadata.

Replaces modal hou.ui.displayMessage inside cook() with non-blocking print / warning log so headless farm cooks never hang.

Removes sync_pipeline_to_parms(node, pipeline) from inside cook() to prevent recursive LOP recook loops.

Python
"""
HDRI Calibration Python LOP cook logic for Houdini Solaris.
"""

import os
import traceback


def cook(node):
    """Main cook function for the HDRI Calibrate Python LOP."""
    import hou

    stage = node.editableStage()
    if not stage:
        raise hou.NodeError(
            "No editable stage available. Ensure this node is inside a LOP network."
        )

    from hdri_match_solaris.hda_utils import read_calibration_parms, build_output_path

    parms = read_calibration_parms(node)
    raw_hdri = parms.get("hdri_path") or ""
    raw_plate = parms.get("plate_path") or ""
    hdri_path = hou.expandString(str(raw_hdri)) if raw_hdri else ""
    plate_path = hou.expandString(str(raw_plate)) if raw_plate else ""

    if not hdri_path or not os.path.isfile(hdri_path):
        raise hou.NodeError(f"HDRI file not found: {hdri_path}")

    try:
        from hdri_match.core.pipeline import CalibrationPipeline
        from hdri_match.io.exporter import save_numpy_to_image
    except ImportError as e:
        raise hou.NodeError(
            f"Cannot import hdri_match core modules: {e}\n"
            "Ensure HDRI_MATCH_PLATE_ROOT is set in the Houdini package."
        )

    pipeline = CalibrationPipeline()
    input_cs = parms["input_colorspace"]
    working_cs = parms["working_colorspace"]

    try:
        pipeline.load_inputs(
            hdri_path,
            plate_path,
            input_space=input_cs,
            working_space=working_cs,
        )
    except Exception as e:
        raise hou.NodeError(f"Failed to load images: {e}")

    from hdri_match_solaris.hda_utils import sync_parms_to_pipeline
    sync_parms_to_pipeline(node, pipeline)

    if parms.get("auto_calibrate") and plate_path and os.path.isfile(plate_path):
        try:
            pipeline.compute_calibration(protect_sun=parms.get("protect_sun", True))
        except Exception as e:
            print(
                f"[HDRI Match Solaris] Auto-calibration warning: {e}. "
                "Proceeding with manual parameter values."
            )

    try:
        calibrated = pipeline.process_hdri(use_proxy=False)
    except Exception as e:
        raise hou.NodeError(f"HDRI processing failed: {e}\n{traceback.format_exc()}")

    if calibrated is None:
        raise hou.NodeError("Calibration produced no output.")

    output_path = build_output_path(node, hdri_path)
    try:
        save_numpy_to_image(calibrated, output_path)
        print(f"[HDRI Match Solaris] Calibrated HDRI written to: {output_path}")
    except Exception as e:
        raise hou.NodeError(f"Failed to write calibrated EXR: {e}")

    from hdri_match_solaris.usd_helpers import (
        set_dome_light,
        write_calibration_metadata,
        ensure_scope,
    )

    ensure_scope(stage, "/lights")
    dome_path = "/lights/hdri_dome"

    # NOTE: pipeline.process_hdri() has already baked EV, WB, and yaw into output_path.
    # Keep DomeLight neutral (0 EV, unit white, 0 yaw) to prevent double-application.
    dome = set_dome_light(
        stage,
        dome_path,
        texture_path=output_path.replace("\\", "/"),
        exposure=0.0,
        color=(1.0, 1.0, 1.0),
        yaw_degrees=0.0,
    )

    write_calibration_metadata(
        dome.GetPrim(),
        {
            "ev_offset": float(parms["ev_offset"]),
            "black_offset": float(parms["black_offset"]),
            "temperature": float(parms["temperature"]),
            "tint": float(parms["tint"]),
            "hdri_yaw": float(parms["hdri_yaw"]),
            "source_hdri": hdri_path,
            "source_plate": plate_path or "",
            "calibrated_path": output_path.replace("\\", "/"),
        },
    )

    print(f"[HDRI Match Solaris] DomeLight created at {dome_path}")

P1.3: python/hdri_match_solaris/lop_sun.py (Full Replacement)

Fixes:

Removes node.parm("source_u").set(...) inside cook(node) to eliminate recursive recooks.

Uses resolve_workspace_dir("hdri_match") when $HIP is unset or read-only.

Properly weights sun patch sampling by 
cos
⁡
(
elevation
)
cos(elevation).

Python
"""
Sun Extraction and Relighting Python LOP cook logic for Houdini Solaris.
"""

import os
import math
import numpy as np


def _eval_parm(node, name, default):
    p = node.parm(name)
    if p is None:
        return default
    val = p.eval()
    return default if val is None else val


def cook(node):
    """Main cook function for the Sun Relighting Python LOP."""
    import hou
    from pxr import UsdLux

    stage = node.editableStage()
    if not stage:
        raise hou.NodeError("No editable stage available.")

    raw_hdri = str(_eval_parm(node, "hdri_path", ""))
    hdri_path = hou.expandString(raw_hdri) if raw_hdri else ""
    auto_detect = bool(_eval_parm(node, "auto_detect", True))

    source_u = float(_eval_parm(node, "source_u", 0.5))
    source_v = float(_eval_parm(node, "source_v", 0.25))
    target_u = float(_eval_parm(node, "target_u", 0.5))
    target_v = float(_eval_parm(node, "target_v", 0.25))

    sun_radius = float(_eval_parm(node, "sun_radius", 0.03))
    sun_feather = float(_eval_parm(node, "sun_feather", 0.015))

    sun_intensity = float(_eval_parm(node, "sun_intensity", 1.0))
    sun_exposure = float(_eval_parm(node, "sun_exposure", 0.0))
    sun_angle = float(_eval_parm(node, "sun_angle", 0.53))

    enable_relight = bool(_eval_parm(node, "enable_relight", True))
    create_distant = bool(_eval_parm(node, "create_distant_light", True))

    raw_out = str(_eval_parm(node, "output_path", ""))
    output_path = hou.expandString(raw_out) if raw_out else ""

    from hdri_match_solaris.usd_helpers import (
        read_calibration_metadata,
        resolve_workspace_dir,
        set_distant_light,
        uv_to_spherical,
        ensure_scope,
    )

    if not hdri_path:
        dome_prim = stage.GetPrimAtPath("/lights/hdri_dome")
        if dome_prim.IsValid():
            meta = read_calibration_metadata(dome_prim)
            hdri_path = meta.get("calibrated_path", "")

    if not hdri_path or not os.path.isfile(hdri_path):
        raise hou.NodeError(
            f"HDRI file not found: {hdri_path}\n"
            "Set the 'hdri_path' parameter or connect an upstream "
            "hdri_match_calibrate node."
        )

    try:
        from hdri_match.io.loader import load_exr_to_numpy
        hdri_array = load_exr_to_numpy(hdri_path)
    except Exception as e:
        raise hou.NodeError(f"Failed to load HDRI: {e}")

    if auto_detect:
        try:
            from hdri_match.calibration.sun_detection import SunDetector
            result = SunDetector.detect_sun(hdri_array)
            if result is not None:
                source_u, source_v = float(result[0]), float(result[1])
                print(f"[Sun] Auto-detected sun at UV=({source_u:.4f}, {source_v:.4f})")
        except Exception as e:
            print(f"[Sun] Auto-detection failed: {e}. Using manual values.")

    h, w = hdri_array.shape[:2]
    sun_px = int(np.clip(source_u * w, 0, w - 1))
    sun_py = int(np.clip(source_v * h, 0, h - 1))

    sample_r = max(2, int(sun_radius * min(h, w) * 0.5))
    y0 = max(0, sun_py - sample_r)
    y1 = min(h, sun_py + sample_r)
    x0 = max(0, sun_px - sample_r)
    x1 = min(w, sun_px + sample_r)
    sun_patch = hdri_array[y0:y1, x0:x1, :3]

    if sun_patch.size > 0:
        sun_color_raw = np.mean(sun_patch, axis=(0, 1))
        sun_luminance = float(
            0.2126 * sun_color_raw[0]
            + 0.7152 * sun_color_raw[1]
            + 0.0722 * sun_color_raw[2]
        )
        if sun_luminance > 1e-6:
            sun_color_norm = sun_color_raw / sun_luminance
        else:
            sun_color_norm = np.array([1.0, 1.0, 1.0], dtype=np.float32)
    else:
        sun_color_norm = np.array([1.0, 1.0, 1.0], dtype=np.float32)

    if enable_relight:
        try:
            from hdri_match.analysis.sun_relighter import SunRelighter
            relit_array = SunRelighter.relight(
                hdri_array,
                source_u=source_u,
                source_v=source_v,
                target_u=target_u,
                target_v=target_v,
                radius_norm=sun_radius,
                feather_norm=sun_feather,
            )
        except Exception as e:
            print(f"[Sun] Relighting failed: {e}")
            relit_array = hdri_array
    else:
        relit_array = hdri_array

    if not output_path:
        out_dir = resolve_workspace_dir("hdri_match")
        base = os.path.splitext(os.path.basename(hdri_path))[0]
        output_path = os.path.join(out_dir, f"{base}_sun_relit.exr")

    try:
        from hdri_match.io.exporter import save_numpy_to_image
        save_numpy_to_image(relit_array, output_path)
        print(f"[Sun] Relit HDRI written to: {output_path}")
    except Exception as e:
        raise hou.NodeError(f"Failed to write relit HDRI: {e}")

    dome_prim = stage.GetPrimAtPath("/lights/hdri_dome")
    if dome_prim.IsValid():
        dome = UsdLux.DomeLight(dome_prim)
        dome.CreateTextureFileAttr().Set(output_path.replace("\\", "/"))
        print("[Sun] DomeLight texture updated to relit HDRI.")

    if create_distant:
        ensure_scope(stage, "/lights")
        az, el = uv_to_spherical(target_u, target_v)
        sun_path = "/lights/sun_direct"
        set_distant_light(
            stage,
            sun_path,
            azimuth_deg=az,
            elevation_deg=el,
            intensity=sun_intensity,
            color=tuple(float(c) for c in sun_color_norm[:3]),
            angle=sun_angle,
            exposure=sun_exposure,
        )
        print(f"[Sun] DistantLight created at {sun_path} (az={az:.1f}, el={el:.1f})")

P1.4: python/hdri_match_solaris/lop_light_extract.py (Full Replacement)

Fixes:

Removes import cv2 from _fallback_extract_lights() (replaces with pure-NumPy BFS 8-connected components with horizontal seam wrap-around).

Integrates solid angle 
Δ
Ω
=
sin
⁡
(
𝜃
)
Δ
𝜃
Δ
𝜙
ΔΩ=sin(θ)ΔθΔϕ per pixel so zenith regions aren't artificially inflated.

Clears stale /lights/extracted prims before defining new lights on re-cook.

Python
"""
Multi-Light Extraction Python LOP cook logic for Houdini Solaris.
"""

import os
import math
from collections import deque
import numpy as np


def _eval_parm(node, name, default):
    p = node.parm(name)
    if p is None:
        return default
    val = p.eval()
    return default if val is None else val


def cook(node):
    """Main cook function for the Light Extraction Python LOP."""
    import hou

    stage = node.editableStage()
    if not stage:
        raise hou.NodeError("No editable stage available.")

    from hdri_match_solaris.usd_helpers import (
        set_sphere_light,
        set_distant_light,
        uv_to_spherical,
        ensure_scope,
        read_calibration_metadata,
    )

    raw_hdri = str(_eval_parm(node, "hdri_path", ""))
    hdri_path = hou.expandString(raw_hdri) if raw_hdri else ""
    max_lights = int(_eval_parm(node, "max_lights", 8))
    threshold_ev = float(_eval_parm(node, "threshold_ev", 3.0))
    min_angular_size = float(_eval_parm(node, "min_angular_size", 0.5))
    light_distance = float(_eval_parm(node, "light_distance", 100.0))
    sun_as_distant = bool(_eval_parm(node, "sun_as_distant", True))

    if not hdri_path:
        dome_prim = stage.GetPrimAtPath("/lights/hdri_dome")
        if dome_prim.IsValid():
            meta = read_calibration_metadata(dome_prim)
            hdri_path = meta.get("calibrated_path", "")

    if not hdri_path or not os.path.isfile(hdri_path):
        raise hou.NodeError(
            f"HDRI file not found: {hdri_path}\n"
            "Set the 'hdri_path' parameter or connect an upstream "
            "hdri_match_calibrate node."
        )

    try:
        from hdri_match.io.loader import load_exr_to_numpy
        hdri_array = load_exr_to_numpy(hdri_path)
    except Exception as e:
        raise hou.NodeError(f"Failed to load HDRI: {e}")

    try:
        from hdri_match.analysis.light_extractor import LightExtractor
        lights = LightExtractor.extract_lights(
            hdri_array,
            max_lights=max_lights,
            threshold_ev=threshold_ev,
            min_angular_size_deg=min_angular_size,
        )
    except ImportError:
        lights = _fallback_extract_lights(
            hdri_array, max_lights=max_lights, threshold_ev=threshold_ev
        )
    except Exception as e:
        raise hou.NodeError(f"Light extraction failed: {e}")

    ensure_scope(stage, "/lights")
    # Remove previous /lights/extracted scope to prevent ghost prims on recook
    if stage.GetPrimAtPath("/lights/extracted").IsValid():
        stage.RemovePrim("/lights/extracted")
    ensure_scope(stage, "/lights/extracted")

    if not lights:
        print("[Light Extract] No significant light emitters found in the HDRI.")
        return

    for i, light in enumerate(lights):
        u = float(light.get("u", 0.5))
        v = float(light.get("v", 0.5))
        color = light.get("color", (1.0, 1.0, 1.0))
        intensity = float(light.get("intensity", 1.0))
        angular_size = float(light.get("angular_size_deg", 1.0))

        safe_name = light.get("name", f"light_{i:02d}")
        safe_name = "".join(c if c.isalnum() or c == "_" else "_" for c in safe_name)
        light_path = f"/lights/extracted/{safe_name}"

        az, el = uv_to_spherical(u, v)
        is_sun = (
            sun_as_distant and angular_size < 2.0 and i == 0 and intensity > 10.0
        )

        if is_sun:
            set_distant_light(
                stage,
                light_path,
                azimuth_deg=az,
                elevation_deg=el,
                intensity=intensity,
                color=tuple(float(c) for c in color[:3]),
                angle=angular_size,
            )
        else:
            direction = _spherical_to_cartesian(az, el)
            radius = angular_size * 0.5
            set_sphere_light(
                stage,
                light_path,
                direction_vec=direction,
                intensity=intensity,
                color=tuple(float(c) for c in color[:3]),
                radius=max(0.5, radius),
                distance=light_distance,
            )

    print(f"[HDRI Match Solaris] Extracted {len(lights)} lights into /lights/extracted/")


def _spherical_to_cartesian(az_deg, el_deg):
    az = math.radians(az_deg)
    el = math.radians(el_deg)
    x = math.cos(el) * math.sin(az)
    y = math.sin(el)
    z = math.cos(el) * math.cos(az)
    return (x, y, z)


def _fallback_extract_lights(hdri_array, max_lights=8, threshold_ev=3.0):
    """Pure-NumPy peak-based light extraction with solid-angle weighting & seam wrap."""
    orig_h, orig_w = hdri_array.shape[:2]
    # Downsample to max 512x256 for fast connected-component clustering without cv2
    step_y = max(1, orig_h // 256)
    step_x = max(1, orig_w // 512)
    img = hdri_array[::step_y, ::step_x, :3]
    h, w = img.shape[:2]

    luma = 0.2126 * img[..., 0] + 0.7152 * img[..., 1] + 0.0722 * img[..., 2]
    threshold = 0.18 * (2.0 ** threshold_ev)
    mask = luma > threshold

    # Solid angle per pixel: dOmega = sin(theta) * (pi / h) * (2*pi / w)
    theta = ((np.arange(h, dtype=np.float32) + 0.5) / h) * math.pi
    sin_theta = np.maximum(np.sin(theta), 1e-4)[:, np.newaxis]
    d_omega = sin_theta * (math.pi / h) * (2.0 * math.pi / w)

    visited = np.zeros((h, w), dtype=bool)
    lights = []

    for y in range(h):
        for x in range(w):
            if not mask[y, x] or visited[y, x]:
                continue

            queue = deque([(y, x)])
            visited[y, x] = True
            coords = []

            while queue:
                cy, cx = queue.popleft()
                coords.append((cy, cx))
                for dy in (-1, 0, 1):
                    ny = cy + dy
                    if ny < 0 or ny >= h:
                        continue
                    for dx in (-1, 0, 1):
                        if dy == 0 and dx == 0:
                            continue
                        nx = (cx + dx) % w  # Wrap horizontally across panorama seam
                        if mask[ny, nx] and not visited[ny, nx]:
                            visited[ny, nx] = True
                            queue.append((ny, nx))

            if len(coords) < 2:
                continue

            ys = np.array([c[0] for c in coords], dtype=np.int32)
            xs = np.array([c[1] for c in coords], dtype=np.int32)

            weights = luma[ys, xs] * d_omega[ys, 0]
            total_w = float(np.sum(weights))
            if total_w <= 1e-8:
                continue

            # Circular mean for u (handles seam straddling)
            angles = ((xs + 0.5) / w) * (2.0 * math.pi)
            mean_sin = float(np.sum(np.sin(angles) * weights) / total_w)
            mean_cos = float(np.sum(np.cos(angles) * weights) / total_w)
            u = (math.atan2(mean_sin, mean_cos) % (2.0 * math.pi)) / (2.0 * math.pi)
            v = float(np.sum(((ys + 0.5) / h) * weights) / total_w)

            region_rgb = img[ys, xs]
            omega_sum = float(np.sum(d_omega[ys, 0]))
            mean_color = np.sum(region_rgb * d_omega[ys, 0][:, np.newaxis], axis=0) / max(omega_sum, 1e-8)
            mean_luma = float(
                0.2126 * mean_color[0] + 0.7152 * mean_color[1] + 0.0722 * mean_color[2]
            )
            if mean_luma < 1e-6:
                continue

            color_norm = mean_color / mean_luma
            # Solid angle to equivalent angular diameter: Omega = pi * (theta_r)^2
            angular_size_deg = math.degrees(2.0 * math.sqrt(omega_sum / math.pi))

            lights.append(
                {
                    "name": f"emitter_{len(lights):02d}",
                    "u": float(u),
                    "v": float(v),
                    "color": tuple(float(c) for c in color_norm[:3]),
                    "intensity": float(mean_luma),
                    "angular_size_deg": max(0.5, float(angular_size_deg)),
                }
            )

    lights.sort(key=lambda item: item["intensity"], reverse=True)
    return lights[:max_lights]

P1.5: python/hdri_match_solaris/renderer_materials.py (Shader Output & pbr_emissive Fix)

In gen_usd_preview_surface_code (L85-93):
In pbr_emissive mode, do not scale _ups_tex (which feeds diffuseColor). Instead, create a dedicated _ups_emissive_tex node (or connect unscaled _ups_tex to diffuseColor and scaled _ups_emissive_tex to emissiveColor):

Python
        f'    if {mat_mode_var} == "emissive":',
        f'        _ups_tex.CreateInput("scale", Sdf.ValueTypeNames.Color4f).Set(Gf.Vec4f({emissive_mult_var}, {emissive_mult_var}, {emissive_mult_var}, 1.0))',
        f'        _ups_shader.CreateInput("emissiveColor", Sdf.ValueTypeNames.Color3f).ConnectToSource(_ups_tex.ConnectableAPI(), "rgb")',
        f'    elif {mat_mode_var} == "pbr_emissive":',
        f'        _ups_shader.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).ConnectToSource(_ups_tex.ConnectableAPI(), "rgb")',
        f'        _ups_em_tex = UsdShade.Shader.Define(stage, f"{{{mat_path_var}}}/ups_emissive_tex")',
        f'        _ups_em_tex.CreateIdAttr("UsdUVTexture")',
        f'        _ups_em_tex.CreateInput("file", Sdf.ValueTypeNames.Asset).Set({tex_path_var})',
        f'        _ups_em_tex.CreateInput("st", Sdf.ValueTypeNames.Float2).ConnectToSource(_ups_st.ConnectableAPI(), "result")',
        f'        _ups_em_tex.CreateInput("scale", Sdf.ValueTypeNames.Color4f).Set(Gf.Vec4f({emissive_mult_var}, {emissive_mult_var}, {emissive_mult_var}, 1.0))',
        f'        _ups_shader.CreateInput("emissiveColor", Sdf.ValueTypeNames.Color3f).ConnectToSource(_ups_em_tex.ConnectableAPI(), "rgb")',


Apply the same dedicated _ups_em_tex pattern in gen_create_material_function (L617-620).

In gen_material_output_connections (L364-368):
Fix Arnold and Redshift surface outputs so they connect to _ai_out and _rs_out instead of _ups_out:

Python
        f'    _mat_prim.CreateSurfaceOutput().ConnectToSource(_ups_out)',
        f'    _mat_prim.CreateSurfaceOutput("arnold").ConnectToSource(_ai_out)',
        f'    _mat_prim.CreateSurfaceOutput("karma").ConnectToSource(_km_out)',
        f'    _mat_prim.CreateSurfaceOutput("mtlx").ConnectToSource(_km_out)',
        f'    _mat_prim.CreateSurfaceOutput("redshift").ConnectToSource(_rs_out)',

P1.6: Crucible Light Studio Patches (gradient_builder.py, preset_manager.py, light_manager.py)

python/CrucibleLightStudio/ui/widgets/gradient_builder.py:4-7:

Python
try:
    from PySide2 import QtWidgets, QtCore, QtGui
except ImportError:
    from PySide6 import QtWidgets, QtCore, QtGui


python/CrucibleLightStudio/core/preset_manager.py:44-45 & L259 (Deep-Copy & RIG_DIR Config Fix):

Python
import copy

RIG_DIR = os.environ.get(
    "CRUCIBLE_RIG_PATH",
    _cfg.get("RIG_DIR", os.path.join(_DEFAULT_ROOT, "light_rigs")),
)


And at L259 inside get_light_presets():

Python
def get_light_presets():
    """Return built-in presets and user JSON presets as a nested dict."""
    presets = {"Built-in": copy.deepcopy(LIGHT_PRESETS)}


python/CrucibleLightStudio/ui/widgets/light_manager.py:876-889 (_on_solo_toggled State Preservation):
Store pre-solo enabled states in self._pre_solo_enabled_state: dict[str, bool] before soloing, and restore each light's prior enabled state (and synchronize COL_ENABLE & COL_SOLO checkboxes) when un-soloing:

Python
    def _on_solo_toggled(self, prim_path, checked):
        if not self._backend:
            return
        if not hasattr(self, "_pre_solo_state"):
            self._pre_solo_state = {}

        all_lights = self._backend.get_all_lights()
        if checked:
            # Snapshot pre-solo enabled states only if not already soloing another light
            if not self._pre_solo_state:
                for l_data in all_lights:
                    p = l_data.get("prim_path")
                    if p:
                        self._pre_solo_state[p] = bool(l_data.get("enabled", True))

            for l_data in all_lights:
                p = l_data.get("prim_path")
                if p:
                    self._backend.set_light_enabled(p, p == prim_path)
        else:
            for l_data in all_lights:
                p = l_data.get("prim_path")
                if p:
                    prev_enabled = self._pre_solo_state.get(p, True)
                    self._backend.set_light_enabled(p, prev_enabled)
            self._pre_solo_state.clear()

        self.refresh_lights()

P1.7: Shelf, Python Panels & Package Version Unification

houdini/toolbar/domebreaker.shelf:84 (splatforge_open_panel):
Change line 84 from:

Python
target_interfaces = ["splatforge_panel", "domebreaker_panel"]


to:

Python
target_interfaces = ["splatforge_panel"]


houdini/python_panels/splatforge.pypanel:10-14 (Check /obj/stage before creating duplicate LOP network):

XML
    <script><![CDATA[
def _build_panel():
    try:
        from hdri_match_solaris.usd_helpers import get_or_create_solaris_stage_node
        from hdri_match_solaris.splatforge.ui.splatforge_tab import SplatForgeTab
        return SplatForgeTab(stage_node_getter=get_or_create_solaris_stage_node)
    except Exception as e:


Version Alignment (v2.0.4 & Houdini >= 20.0):

python/domebreaker/__init__.py: __version__ = "2.0.4"

python/hdri_match_solaris/__init__.py: __version__ = "2.0.4", __houdini_min__ = "20.0"

python/hdri_match_solaris/panel_ui.py:1948: "v2.0.4"

Priority P2: Installer, Updater, Shell Hygiene & Offline Test Suite
P2.1: scripts/install.py (Fix Version Parser & Stop Overwriting Tracked houdini/packages/domebreaker.json)

Replace _is_supported_houdini_version (L45-53) with regex version extraction:

Python
import re

def _is_supported_houdini_version(ver_str):
    """Return True if the version string begins with Houdini 20.0 or newer."""
    if not ver_str:
        return False
    m = re.match(r"^(\d+)(?:\.(\d+))?", str(ver_str).strip())
    if not m:
        return False
    major = int(m.group(1))
    minor = int(m.group(2) or 0)
    return (major, minor) >= (20, 0)


Remove lines 222-226 in scripts/install.py (do not overwrite os.path.join(root, "houdini", "packages", "domebreaker.json")).

P2.2: scripts/update.py (Fix Stale Shelf Message, Add Zip-Slip Guard & Safe Git Pull)

Replace lines 79-85 in scripts/update.py so dirty working trees don't trigger mid-update merge conflicts:

Python
        print("[INFO] Pulling updates...")
        pull_res = subprocess.run(
            ["git", "pull", "--ff-only", "origin", "main"],
            cwd=root,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if pull_res.returncode != 0:
            err_msg = pull_res.stderr.decode("utf-8", errors="replace").strip()
            return False, f"Fast-forward pull not possible ({err_msg})"


Replace z.extractall(extract_dir) (L142) with a path-traversal guard:

Python
        with zipfile.ZipFile(zip_path, "r") as z:
            abs_extract = os.path.abspath(extract_dir)
            for member in z.namelist():
                member_path = os.path.abspath(os.path.join(abs_extract, member))
                if not member_path.startswith(abs_extract + os.sep) and member_path != abs_extract:
                    raise RuntimeError(f"Unsafe zip member path blocked: {member}")
            z.extractall(extract_dir)


Update lines 193 and 201:

Python
        print("\nIf Houdini is currently open, please restart Houdini to load the updated code.")

P2.3: Strip Emojis from install.sh, uninstall.sh, update.sh

Replace ⚡, 🎉, and 🗑️ in install.sh (L9, 67, 119), uninstall.sh (L9, 54), and update.sh (L9) with ASCII tags ([DomeBreaker], [SUCCESS], [UNINSTALL]).

P2.4: Offline pytest Suite Recommendation (tests/test_architecture_invariants.py)

Recommendation: YES — Strongly Recommended. Adding a zero-dependency offline pytest suite (tests/test_architecture_invariants.py) that executes in 
<
1.5
s
<1.5s before every commit or deployment to mlk-ws448 will permanently enforce all PCoIP :100 and Solaris invariants.

Create tests/test_architecture_invariants.py:

Python
"""
Offline Architecture & PCoIP :100 Safety Invariant Test Suite for DomeBreaker.
Run via: pytest tests/test_architecture_invariants.py
"""

import ast
import os
import re
import sys
import numpy as np
import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PYTHON_ROOT = os.path.join(REPO_ROOT, "python")

if PYTHON_ROOT not in sys.path:
    sys.path.insert(0, PYTHON_ROOT)


def _iter_python_files():
    for root, _, files in os.walk(PYTHON_ROOT):
        for f in files:
            if f.endswith(".py"):
                yield os.path.join(root, f)


def test_no_c1_mojibake_or_orphan_variation_selectors():
    """Verify zero C1 control bytes (0x80-0x9F) or orphan U+FE0F glyphs exist in UI code."""
    for path in _iter_python_files():
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
        # Check C1 control range U+0080..U+009F
        c1_matches = [ch for ch in text if 0x0080 <= ord(ch) <= 0x009F]
        assert not c1_matches, f"C1 mojibake characters found in {path}"
        assert "\ufe0f" not in text, f"Variation selector U+FE0F found in {path}"


def test_no_hardcoded_windows_e_drive_paths():
    """Ensure no hardcoded developer 'E:/PROJECTS' paths remain in production modules."""
    for path in _iter_python_files():
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
        assert "E:/PROJECTS" not in text and "E:\\PROJECTS" not in text, (
            f"Hardcoded Windows E:/ path found in {path}"
        )


def test_no_runtime_importlib_reload():
    """Ensure importlib.reload is never called in runtime modules."""
    for path in _iter_python_files():
        with open(path, "r", encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=path)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Attribute) and func.attr == "reload":
                    pytest.fail(f"Unsafe runtime importlib.reload() call in {path}:{node.lineno}")


def test_sun_relighter_never_wraps_vertically_across_poles():
    """Verify moving a zenith sun upward does not wrap bright pixels to the nadir."""
    from hdri_match.analysis.sun_relighter import SunRelighter

    h, w = 64, 128
    img = np.zeros((h, w, 3), dtype=np.float32)
    # Place bright sun near zenith (v = 0.05)
    img[2:5, 60:68, :] = 50.0

    relit = SunRelighter.relight(
        img, source_u=0.5, source_v=0.05, target_u=0.5, target_v=0.01, radius_norm=0.08
    )
    # Bottom 10 rows (nadir) must remain strictly zero
    assert np.max(relit[-10:, :, :]) < 1e-4, "Sun stamp wrapped vertically across pole to nadir!"


def test_fallback_light_extractor_runs_without_cv2(monkeypatch):
    """Verify light extraction works in pure NumPy when cv2 is blocked inside Houdini."""
    monkeypatch.setitem(sys.modules, "cv2", None)
    from hdri_match_solaris.lop_light_extract import _fallback_extract_lights

    img = np.full((64, 128, 3), 0.1, dtype=np.float32)
    # Add a seam-straddling bright emitter at u=0 / u=1
    img[30:34, :3, :] = 20.0
    img[30:34, -3:, :] = 20.0

    lights = _fallback_extract_lights(img, max_lights=4, threshold_ev=2.0)
    assert len(lights) == 1, f"Seam-straddling emitter should merge into 1 light, got {len(lights)}"
