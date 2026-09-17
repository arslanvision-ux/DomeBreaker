"""
Multi-Light Extraction Python LOP cook logic for Houdini Solaris.

Analyses the calibrated HDRI panorama to detect and extract individual
light emitters (sun, practicals, sky patches, windows), then creates
corresponding USD light prims on the stage.

Each detected emitter becomes a ``UsdLux.SphereLight`` positioned at the
correct direction from the origin, with colour and intensity derived from
the integrated radiance within the emitter's solid angle.
"""

import os
import math
import numpy as np


def cook(node):
    """Main cook function for the Light Extraction Python LOP.

    Args:
        node: ``hou.Node`` -- the Python LOP node being cooked.
    """
    import hou

    stage = node.editableStage()
    if not stage:
        raise hou.NodeError("No editable stage available.")

    from hdri_match_solaris.usd_helpers import (
        set_sphere_light, set_distant_light, uv_to_spherical,
        ensure_scope, write_calibration_metadata
    )

    # ------------------------------------------------------------------
    # Read parameters
    # ------------------------------------------------------------------
    hdri_path = hou.expandString(node.parm("hdri_path").eval()) if node.parm("hdri_path") else ""
    max_lights = node.parm("max_lights").eval() if node.parm("max_lights") else 8
    threshold_ev = node.parm("threshold_ev").eval() if node.parm("threshold_ev") else 3.0
    min_angular_size = node.parm("min_angular_size").eval() if node.parm("min_angular_size") else 0.5
    light_distance = node.parm("light_distance").eval() if node.parm("light_distance") else 100.0
    sun_as_distant = node.parm("sun_as_distant").eval() if node.parm("sun_as_distant") else True

    # ------------------------------------------------------------------
    # Inherit HDRI path from upstream calibration metadata
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
    # Load HDRI
    # ------------------------------------------------------------------
    try:
        from hdri_match.io.loader import load_exr_to_numpy
        hdri_array = load_exr_to_numpy(hdri_path)
    except Exception as e:
        raise hou.NodeError(f"Failed to load HDRI: {e}")

    # ------------------------------------------------------------------
    # Extract lights using the analysis module
    # ------------------------------------------------------------------
    try:
        from hdri_match.analysis.light_extractor import LightExtractor
        lights = LightExtractor.extract_lights(
            hdri_array,
            max_lights=max_lights,
            threshold_ev=threshold_ev,
            min_angular_size_deg=min_angular_size,
        )
    except ImportError:
        # Fallback: simple peak detection
        lights = _fallback_extract_lights(hdri_array, max_lights, threshold_ev)
    except Exception as e:
        raise hou.NodeError(f"Light extraction failed: {e}")

    if not lights:
        print("[Light Extract] No significant light emitters found in the HDRI.")
        return

    # ------------------------------------------------------------------
    # Create USD light prims
    # ------------------------------------------------------------------
    ensure_scope(stage, "/lights")
    ensure_scope(stage, "/lights/extracted")

    for i, light in enumerate(lights):
        u = light.get("u", 0.5)
        v = light.get("v", 0.5)
        color = light.get("color", (1.0, 1.0, 1.0))
        intensity = light.get("intensity", 1.0)
        angular_size = light.get("angular_size_deg", 1.0)

        safe_name = light.get("name", f"light_{i:02d}")
        safe_name = "".join(c if c.isalnum() or c == "_" else "_" for c in safe_name)
        light_path = f"/lights/extracted/{safe_name}"

        az, el = uv_to_spherical(u, v)

        # Use DistantLight for the primary sun (small angular size, high intensity)
        is_sun = (sun_as_distant and angular_size < 2.0 and i == 0
                  and intensity > 10.0)

        if is_sun:
            set_distant_light(
                stage, light_path,
                azimuth_deg=az,
                elevation_deg=el,
                intensity=intensity,
                color=tuple(float(c) for c in color[:3]),
                angle=angular_size,
            )
            print(f"  [{i}] DistantLight '{safe_name}' - "
                  f"az={az:.1f}, el={el:.1f}, intensity={intensity:.1f}")
        else:
            # Compute direction vector for SphereLight placement
            direction = _spherical_to_cartesian(az, el)
            radius = angular_size * 0.5  # Approximate

            set_sphere_light(
                stage, light_path,
                direction_vec=direction,
                intensity=intensity,
                color=tuple(float(c) for c in color[:3]),
                radius=max(0.5, radius),
                distance=light_distance,
            )
            print(f"  [{i}] SphereLight '{safe_name}' - "
                  f"az={az:.1f}, el={el:.1f}, intensity={intensity:.1f}")

    print(f"[HDRI Match Solaris] Extracted {len(lights)} lights into /lights/extracted/")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _spherical_to_cartesian(az_deg, el_deg):
    """Convert azimuth/elevation to Cartesian direction (Y-up)."""
    az = math.radians(az_deg)
    el = math.radians(el_deg)
    x = math.cos(el) * math.sin(az)
    y = math.sin(el)
    z = math.cos(el) * math.cos(az)
    return (x, y, z)


def _fallback_extract_lights(hdri_array, max_lights=8, threshold_ev=3.0):
    """Simple peak-based light extraction fallback.

    Uses luminance thresholding and connected component analysis to find
    bright emitters when the full ``LightExtractor`` is not available.
    """
    import cv2

    h, w = hdri_array.shape[:2]
    luma = (0.2126 * hdri_array[..., 0] +
            0.7152 * hdri_array[..., 1] +
            0.0722 * hdri_array[..., 2])

    # Threshold at mid-grey * 2^threshold_ev
    threshold = 0.18 * (2.0 ** threshold_ev)
    mask = (luma > threshold).astype(np.uint8)

    # Connected components
    num_labels, labels, stats, centroids = cv2.connectedComponentsWithStats(
        mask, connectivity=8)

    lights = []
    for label_id in range(1, num_labels):
        area = stats[label_id, cv2.CC_STAT_AREA]
        if area < 4:
            continue

        cx, cy = centroids[label_id]
        u = cx / w
        v = cy / h

        # Sample colour
        region_mask = (labels == label_id)
        region_pixels = hdri_array[region_mask]
        mean_color = np.mean(region_pixels, axis=0)
        mean_luma = float(0.2126 * mean_color[0] + 0.7152 * mean_color[1] +
                          0.0722 * mean_color[2])

        if mean_luma < 1e-6:
            continue

        color_norm = mean_color / mean_luma

        # Angular size from pixel area
        angular_size = math.degrees(2.0 * math.sqrt(area / math.pi) / w * math.pi)

        lights.append({
            "name": f"emitter_{len(lights):02d}",
            "u": float(u),
            "v": float(v),
            "color": tuple(float(c) for c in color_norm[:3]),
            "intensity": float(mean_luma),
            "angular_size_deg": float(angular_size),
        })

    # Sort by intensity descending
    lights.sort(key=lambda x: x["intensity"], reverse=True)
    return lights[:max_lights]
