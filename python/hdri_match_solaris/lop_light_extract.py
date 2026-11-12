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
    raw_hdri = node.parm("hdri_path").eval() if node.parm("hdri_path") else ""
    hdri_path = hou.expandString(str(raw_hdri or ""))
    max_lights = int(node.parm("max_lights").eval()) if node.parm("max_lights") else 8
    threshold_ev = float(node.parm("threshold_ev").eval()) if node.parm("threshold_ev") else 3.0
    min_angular_size = float(node.parm("min_angular_size").eval()) if node.parm("min_angular_size") else 0.5
    light_distance = float(node.parm("light_distance").eval()) if node.parm("light_distance") else 100.0
    sun_as_distant = bool(node.parm("sun_as_distant").eval()) if node.parm("sun_as_distant") else True

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

    # Clear stale /lights/extracted children first so reduced max_lights or schema switches never leave ghosts
    ensure_scope(stage, "/lights")
    extracted_prim = stage.GetPrimAtPath("/lights/extracted")
    if extracted_prim.IsValid():
        for child in list(extracted_prim.GetChildren()):
            stage.RemovePrim(child.GetPath())

    if not lights:
        print("[Light Extract] No significant light emitters found in the HDRI.")
        return

    # ------------------------------------------------------------------
    # Create USD light prims
    # ------------------------------------------------------------------
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
    """Peak-based light extraction fallback in pure NumPy (no cv2).

    Uses luminance thresholding, downsampled horizontal-wrapping 8-connected
    components BFS, and sin(theta) solid-angle weighting for equirectangular
    HDRIs so polar distortion and u=0/u=1 seam splits are avoided.
    """
    from collections import deque

    h, w = hdri_array.shape[:2]
    # Downsample to manageable working grid if very large so pure-Python BFS is instant
    max_work_w = 512
    if w > max_work_w:
        step = max(1, w // max_work_w)
        work = hdri_array[::step, ::step, :3]
    else:
        work = hdri_array[..., :3]

    wh, ww = work.shape[:2]
    luma = 0.2126 * work[..., 0] + 0.7152 * work[..., 1] + 0.0722 * work[..., 2]

    threshold = 0.18 * (2.0 ** float(threshold_ev))
    active = luma > threshold
    if not np.any(active):
        return []

    # Solid-angle row weights: sin(colatitude) = sin(pi * (y + 0.5) / wh)
    row_theta = math.pi * (np.arange(wh, dtype=np.float32) + 0.5) / float(wh)
    sin_theta = np.maximum(np.sin(row_theta), 1e-4)
    dtheta = math.pi / float(wh)
    dphi = (2.0 * math.pi) / float(ww)
    total_sphere_area = 4.0 * math.pi

    visited = np.zeros((wh, ww), dtype=bool)
    ys, xs = np.nonzero(active)
    lights = []

    for sy, sx in zip(ys.tolist(), xs.tolist()):
        if visited[sy, sx]:
            continue
        # Iterative 8-connected BFS wrapping horizontally across seam
        visited[sy, sx] = True
        queue = deque([(sy, sx)])
        comp_y = []
        comp_x = []
        while queue:
            cy, cx = queue.popleft()
            comp_y.append(cy)
            comp_x.append(cx)
            for dy in (-1, 0, 1):
                ny = cy + dy
                if ny < 0 or ny >= wh:
                    continue
                for dx in (-1, 0, 1):
                    if dx == 0 and dy == 0:
                        continue
                    nx = (cx + dx) % ww
                    if active[ny, nx] and not visited[ny, nx]:
                        visited[ny, nx] = True
                        queue.append((ny, nx))

        if len(comp_y) < 2 and max(w, h) > 64:
            continue

        iy = np.array(comp_y, dtype=np.int64)
        ix = np.array(comp_x, dtype=np.int64)
        pix_rgb = work[iy, ix]
        pix_luma = luma[iy, ix]
        pix_omega = sin_theta[iy] * dtheta * dphi
        omega_sum = float(np.sum(pix_omega))
        if omega_sum <= 1e-9:
            continue

        weights = pix_luma * pix_omega
        w_sum = float(np.sum(weights))
        if w_sum <= 1e-9:
            continue

        # Seam-safe circular azimuth centroid
        phi = (ix.astype(np.float32) + 0.5) / float(ww) * (2.0 * math.pi) - math.pi
        mean_phi = math.atan2(
            float(np.sum(weights * np.sin(phi))),
            float(np.sum(weights * np.cos(phi))),
        )
        u = ((mean_phi + math.pi) / (2.0 * math.pi)) % 1.0
        v = float(np.sum(weights * ((iy.astype(np.float32) + 0.5) / float(wh))) / w_sum)

        # Solid-angle weighted mean radiance
        weighted_rgb = np.sum(pix_rgb * pix_omega[:, None], axis=0) / omega_sum
        mean_luma = float(0.2126 * weighted_rgb[0] + 0.7152 * weighted_rgb[1] + 0.0722 * weighted_rgb[2])
        if mean_luma < 1e-6:
            continue

        color_norm = weighted_rgb / mean_luma
        # Solid-angle angular diameter: omega = 2 * pi * (1 - cos(half_angle))
        cos_half = max(-1.0, min(1.0, 1.0 - min(omega_sum, total_sphere_area) / (2.0 * math.pi)))
        angular_size = max(0.5, math.degrees(2.0 * math.acos(cos_half)))

        lights.append({
            "name": f"emitter_{len(lights):02d}",
            "u": float(u),
            "v": float(v),
            "color": tuple(float(c) for c in color_norm[:3]),
            "intensity": float(mean_luma),
            "angular_size_deg": float(angular_size),
        })

    lights.sort(key=lambda x: x["intensity"], reverse=True)
    return lights[:max_lights]
