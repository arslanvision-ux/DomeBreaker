"""
Sun Extraction and Relighting Python LOP cook logic for Houdini Solaris.

Detects the sun position in the HDRI, extracts the direct solar radiance,
inpaints the sun disk, repositions the sun emitter at a user-specified
target direction, and creates a UsdLux.DistantLight on the USD stage to
represent the direct sunlight component.

This node reads the calibrated HDRI (from an upstream calibrate node or
from disk), performs the relighting operation, writes the updated HDRI
(sun-free dome + repositioned sun), and creates both:
    - An updated DomeLight texture (with the sun removed / repositioned)
    - A DistantLight for the direct sun contribution
"""

import os
import math
import numpy as np
import traceback


def cook(node):
    """Main cook function for the Sun Relighting Python LOP.

    Args:
        node: ``hou.Node`` -- the Python LOP node being cooked.
    """
    import hou

    stage = node.editableStage()
    if not stage:
        raise hou.NodeError("No editable stage available.")

    # ------------------------------------------------------------------
    # 1. Read parameters
    # ------------------------------------------------------------------
    hdri_path = hou.expandString(node.parm("hdri_path").eval()) if node.parm("hdri_path") else ""
    auto_detect = node.parm("auto_detect").eval() if node.parm("auto_detect") else True

    # Source sun position (normalised UV, 0-1)
    source_u = node.parm("source_u").eval() if node.parm("source_u") else 0.5
    source_v = node.parm("source_v").eval() if node.parm("source_v") else 0.25

    # Target sun position (normalised UV, 0-1)
    target_u = node.parm("target_u").eval() if node.parm("target_u") else 0.5
    target_v = node.parm("target_v").eval() if node.parm("target_v") else 0.25

    # Extraction parameters
    sun_radius = node.parm("sun_radius").eval() if node.parm("sun_radius") else 0.03
    sun_feather = node.parm("sun_feather").eval() if node.parm("sun_feather") else 0.015

    # Sun light parameters
    sun_intensity = node.parm("sun_intensity").eval() if node.parm("sun_intensity") else 1.0
    sun_exposure = node.parm("sun_exposure").eval() if node.parm("sun_exposure") else 0.0
    sun_angle = node.parm("sun_angle").eval() if node.parm("sun_angle") else 0.53

    enable_relight = node.parm("enable_relight").eval() if node.parm("enable_relight") else True
    create_distant = node.parm("create_distant_light").eval() if node.parm("create_distant_light") else True

    output_path = hou.expandString(node.parm("output_path").eval()) if node.parm("output_path") else ""

    # ------------------------------------------------------------------
    # 2. Try to inherit HDRI path from upstream calibration metadata
    # ------------------------------------------------------------------
    if not hdri_path:
        from hdri_match_solaris.usd_helpers import read_calibration_metadata
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

    # ------------------------------------------------------------------
    # 3. Load the HDRI
    # ------------------------------------------------------------------
    try:
        from hdri_match.io.loader import load_exr_to_numpy
        hdri_array = load_exr_to_numpy(hdri_path)
    except Exception as e:
        raise hou.NodeError(f"Failed to load HDRI: {e}")

    # ------------------------------------------------------------------
    # 4. Auto-detect sun position
    # ------------------------------------------------------------------
    if auto_detect:
        try:
            from hdri_match.calibration.sun_detection import SunDetector
            result = SunDetector.detect_sun(hdri_array)
            if result is not None:
                source_u, source_v = result[0], result[1]
                # Push detected values back to parms for display
                if node.parm("source_u"):
                    node.parm("source_u").set(source_u)
                if node.parm("source_v"):
                    node.parm("source_v").set(source_v)
                print(f"[Sun] Auto-detected sun at UV=({source_u:.4f}, {source_v:.4f})")
        except Exception as e:
            print(f"[Sun] Auto-detection failed: {e}. Using manual values.")

    # ------------------------------------------------------------------
    # 5. Extract sun colour and intensity from the HDRI
    # ------------------------------------------------------------------
    h, w = hdri_array.shape[:2]
    sun_px = int(source_u * w)
    sun_py = int(source_v * h)

    # Sample a small region around the sun centroid for colour
    sample_r = max(2, int(sun_radius * min(h, w) * 0.5))
    y0 = max(0, sun_py - sample_r)
    y1 = min(h, sun_py + sample_r)
    x0 = max(0, sun_px - sample_r)
    x1 = min(w, sun_px + sample_r)
    sun_patch = hdri_array[y0:y1, x0:x1, :3]

    if sun_patch.size > 0:
        sun_color_raw = np.mean(sun_patch, axis=(0, 1))
        sun_luminance = float(0.2126 * sun_color_raw[0] +
                              0.7152 * sun_color_raw[1] +
                              0.0722 * sun_color_raw[2])
        if sun_luminance > 1e-6:
            sun_color_norm = sun_color_raw / sun_luminance
        else:
            sun_color_norm = np.array([1.0, 1.0, 1.0])
    else:
        sun_color_norm = np.array([1.0, 1.0, 1.0])
        sun_luminance = 1.0

    # ------------------------------------------------------------------
    # 6. Perform sun relighting (extract, inpaint, reposition)
    # ------------------------------------------------------------------
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

    # ------------------------------------------------------------------
    # 7. Write the relit HDRI to disk
    # ------------------------------------------------------------------
    if not output_path:
        hip = hou.expandString("$HIP")
        out_dir = os.path.join(hip, "hdri_match")
        os.makedirs(out_dir, exist_ok=True)
        base = os.path.splitext(os.path.basename(hdri_path))[0]
        output_path = os.path.join(out_dir, f"{base}_sun_relit.exr")

    try:
        from hdri_match.io.exporter import save_numpy_to_image
        save_numpy_to_image(relit_array, output_path)
        print(f"[Sun] Relit HDRI written to: {output_path}")
    except Exception as e:
        raise hou.NodeError(f"Failed to write relit HDRI: {e}")

    # ------------------------------------------------------------------
    # 8. Update the DomeLight texture to the relit HDRI
    # ------------------------------------------------------------------
    from pxr import UsdLux
    dome_prim = stage.GetPrimAtPath("/lights/hdri_dome")
    if dome_prim.IsValid():
        dome = UsdLux.DomeLight(dome_prim)
        dome.GetTextureFileAttr().Set(output_path.replace("\\", "/"))
        print("[Sun] DomeLight texture updated to relit HDRI.")

    # ------------------------------------------------------------------
    # 9. Create DistantLight for the direct sun
    # ------------------------------------------------------------------
    if create_distant:
        from hdri_match_solaris.usd_helpers import (
            set_distant_light, uv_to_spherical, ensure_scope
        )

        ensure_scope(stage, "/lights")

        # Use the TARGET position for the DistantLight direction
        az, el = uv_to_spherical(target_u, target_v)

        sun_path = "/lights/sun_direct"
        set_distant_light(
            stage, sun_path,
            azimuth_deg=az,
            elevation_deg=el,
            intensity=sun_intensity,
            color=tuple(float(c) for c in sun_color_norm[:3]),
            angle=sun_angle,
            exposure=sun_exposure,
        )

        print(f"[Sun] DistantLight created at {sun_path}")
        print(f"  Direction: azimuth={az:.1f} deg, elevation={el:.1f} deg")
        print(f"  Color: ({sun_color_norm[0]:.3f}, {sun_color_norm[1]:.3f}, {sun_color_norm[2]:.3f})")
