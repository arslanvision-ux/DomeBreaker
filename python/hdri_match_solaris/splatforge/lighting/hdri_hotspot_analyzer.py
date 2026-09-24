# -*- coding: utf-8 -*-
"""
SPLATFORGE HDRI Hotspot Analyzer — Detect & Project Bright Emitters.

Analyzes a baked equirectangular HDRI to find bright hotspot regions
(windows, ceiling lamps, bright fixtures) using connected-component
luminance segmentation. Projects those hotspots onto detected room
surfaces (walls, ceiling) via ray-plane intersection to produce
3D-positioned LightDefinitions for the Solaris pipeline.

Also provides HDRI inpainting: paints out detected hotspots from the
dome map so that practical lights can be used for direct illumination
without double-counting from the dome.
"""

import math
import os
import numpy as np

from hdri_match_solaris.splatforge import config
from hdri_match_solaris.splatforge.lighting.light_extractor import LightDefinition


# ---------------------------------------------------------------------------
# HDRI Hotspot Detection
# ---------------------------------------------------------------------------

def analyze_hdri_hotspots(hdri_array=None, hdri_path=None,
                         thresh_ev=2.5, range_ev=8.0,
                         max_candidates=32, work_w=512,
                         min_pixel_area=4,
                         progress_callback=None):
    """Detect bright emitter hotspots in an equirectangular HDRI.

    Uses connected-component luminance segmentation (pure numpy, no cv2/scipy
    dependency) to find distinct bright regions and compute their angular
    positions, pixel footprints, peak values, and average colors.

    Args:
        hdri_array:        (H, W, 3+) float32 numpy array. If None, loaded
                           from ``hdri_path``.
        hdri_path:         Path to EXR file (used if ``hdri_array`` is None).
        thresh_ev:         EV stops above median luma for emitter candidacy.
        range_ev:          Dynamic range window from peak to retain secondary
                           hotspots.
        max_candidates:    Maximum hotspots to return.
        work_w:            Downsampled width for BFS flood fill (performance).
        min_pixel_area:    Minimum connected-component pixel count at work
                           resolution to consider a valid hotspot.
        progress_callback: Optional ``callable(percent, message)``.

    Returns:
        List of hotspot dicts, each with:
            ``px, py``: Pixel coordinates in original HDRI.
            ``az, el``: Azimuth and elevation in degrees.
            ``hw, hh``: Half-width and half-height in pixels.
            ``val``:    Peak luminance value.
            ``color``:  (r, g, b) average scene-linear color of the hotspot.
            ``is_primary``: Whether above the primary threshold.
    """
    if progress_callback:
        progress_callback(5, "Loading HDRI for hotspot analysis...")

    arr = _load_hdri_array(hdri_array, hdri_path)
    if arr is None:
        return []

    H, W = arr.shape[:2]

    if progress_callback:
        progress_callback(10, "Computing luminance map...")

    # Scene-linear luminance (Rec.709 coefficients)
    luma = (0.2126 * arr[..., 0] +
            0.7152 * arr[..., 1] +
            0.0722 * arr[..., 2]).astype(np.float32)

    valid = luma[luma > 0]
    if valid.size == 0:
        return []

    median_luma = float(np.median(valid))
    max_luma = float(np.max(luma))
    p95_luma = float(np.percentile(valid, 95.0))

    # Adaptive threshold: EV stops above median
    base_thresh = max(0.01, median_luma) * (2.0 ** float(thresh_ev))
    if base_thresh >= max_luma * 0.95:
        base_thresh = max(0.01, median_luma + (max_luma - median_luma) * 0.35)

    # Emitter threshold
    th = max(base_thresh, min(p95_luma, max_luma * 0.1))
    th = max(th, median_luma * (2.0 ** 2.0))

    if progress_callback:
        progress_callback(20, f"Threshold: {th:.3f} (median={median_luma:.3f}, max={max_luma:.1f})")

    # Downsample for BFS performance
    step = max(1, int(round(W / float(work_w))))
    l_s = luma[::step, ::step]
    mask_s = l_s >= th

    dh, dw = l_s.shape
    lab_s = np.zeros_like(mask_s, dtype=np.int32)
    cur_lbl = 0
    comps = []

    if progress_callback:
        progress_callback(30, "Flood-fill connected components...")

    # BFS connected-component labeling (wraps horizontally for equirect)
    for y in range(dh):
        for x in range(dw):
            if mask_s[y, x] and lab_s[y, x] == 0:
                cur_lbl += 1
                queue = [(y, x)]
                lab_s[y, x] = cur_lbl
                pts = []
                while queue:
                    cy, cx = queue.pop(0)
                    pts.append((cy, cx))
                    for ny, nx in ((cy - 1, cx), (cy + 1, cx),
                                   (cy, (cx - 1) % dw), (cy, (cx + 1) % dw)):
                        if 0 <= ny < dh and mask_s[ny, nx] and lab_s[ny, nx] == 0:
                            lab_s[ny, nx] = cur_lbl
                            queue.append((ny, nx))

                if len(pts) >= min_pixel_area:
                    ys = [p[0] for p in pts]
                    xs = np.array([p[1] for p in pts])

                    # Handle wrap-around at equirectangular seam
                    if np.max(xs) - np.min(xs) > dw * 0.7:
                        xs_shifted = (xs + dw // 2) % dw
                        bw = (np.max(xs_shifted) - np.min(xs_shifted) + 1) * step
                        cx_p = int(((int(np.mean(xs_shifted)) - dw // 2) % dw) * step)
                    else:
                        bw = (np.max(xs) - np.min(xs) + 1) * step
                        cx_p = int(np.mean(xs)) * step
                    bh = (max(ys) - min(ys) + 1) * step
                    cy_p = int(np.mean(ys)) * step

                    # Peak luminance and average color
                    pk = max(l_s[p[0], p[1]] for p in pts)

                    # Sample average color at original resolution
                    color_samples = []
                    for py_s, px_s in pts[:200]:  # Cap sampling for performance
                        oy = min(py_s * step, H - 1)
                        ox = min(px_s * step, W - 1)
                        color_samples.append(arr[oy, ox, :3])
                    avg_color = np.mean(color_samples, axis=0).tolist() if color_samples else [1.0, 1.0, 1.0]

                    # Angular coordinates
                    u = (cx_p + 0.5) / W
                    v = (cy_p + 0.5) / H
                    az = (0.5 - u) * 360.0   # Degrees, +ve = left of center
                    el = 90.0 - v * 180.0     # Degrees, +ve = above horizon

                    comps.append({
                        "val": float(pk),
                        "px": cx_p,
                        "py": cy_p,
                        "az": az,
                        "el": el,
                        "hw": max(4, bw // 2 + 4),
                        "hh": max(4, bh // 2 + 4),
                        "color": avg_color,
                        "pts": len(pts),
                    })

    comps.sort(key=lambda c: c["val"], reverse=True)

    if progress_callback:
        progress_callback(60, f"Found {len(comps)} candidate regions above threshold.")

    # Primary threshold: within range_ev stops of peak
    ref_peak = comps[0]["val"] if comps else 0.0
    primary_thresh = max(base_thresh, ref_peak * (2.0 ** (-float(range_ev)))) if ref_peak > 0 else base_thresh

    # Non-maximum suppression: merge smaller components inside larger ones
    merged = []
    for c in comps:
        # Skip floor reflections (below -15 degrees elevation)
        is_floor = c["el"] < -15.0
        is_primary = (c["val"] >= primary_thresh) and (not is_floor or c["val"] >= max_luma * 0.3)
        if not is_primary:
            continue

        inside = False
        for m in merged:
            dx = abs(c["px"] - m["px"])
            dx = min(dx, W - dx)
            dy = abs(c["py"] - m["py"])
            if dx <= m["hw"] and dy <= m["hh"]:
                inside = True
                break
        if not inside:
            merged.append(c)

    hotspots = []
    for i, c in enumerate(merged[:int(max_candidates)]):
        hotspots.append({
            "index": i + 1,
            "val": c["val"],
            "px": c["px"],
            "py": c["py"],
            "az": c["az"],
            "el": c["el"],
            "hw": c["hw"],
            "hh": c["hh"],
            "color": c["color"],
            "is_primary": True,
        })

    if progress_callback:
        progress_callback(80, f"Detected {len(hotspots)} primary hotspot(s).")

    return hotspots


# ---------------------------------------------------------------------------
# Project Hotspots onto Room Surfaces
# ---------------------------------------------------------------------------

def project_hotspots_to_room(hotspots, room_data, camera_pos=None):
    """Project HDRI hotspots onto detected room surfaces.

    For each hotspot, casts a ray from the bake probe origin in the
    direction of the hotspot's (azimuth, elevation) and intersects it
    with room walls and ceiling to determine 3D position and orientation.

    Args:
        hotspots:    List of hotspot dicts from ``analyze_hdri_hotspots()``.
        room_data:   ``RoomData`` instance from ``room_detector``.
        camera_pos:  (x, y, z) bake probe origin. If None, uses room center.

    Returns:
        List of ``LightDefinition`` instances positioned on room surfaces.
    """
    if not hotspots or room_data is None:
        return []

    # Resolve camera position
    if camera_pos is None:
        center = room_data.center
        if isinstance(center, (list, tuple)) and len(center) >= 3:
            camera_pos = (float(center[0]), float(center[1]), float(center[2]))
        else:
            camera_pos = (0.0, 1.5, 0.0)

    cam = np.array(camera_pos, dtype=np.float64)

    # Room bounds
    floor_y = room_data.floor_y
    ceil_y = room_data.ceil_y
    x_min = room_data.x_min
    x_max = room_data.x_max
    z_min = room_data.z_min
    z_max = room_data.z_max

    # Define room planes: (point_on_plane, normal, label, wall_id)
    planes = [
        (np.array([0, ceil_y, 0]), np.array([0, -1, 0]), "ceiling", "ceiling"),
        (np.array([x_min, 0, 0]), np.array([1, 0, 0]), "wall", "west"),
        (np.array([x_max, 0, 0]), np.array([-1, 0, 0]), "wall", "east"),
        (np.array([0, 0, z_min]), np.array([0, 0, 1]), "wall", "north"),
        (np.array([0, 0, z_max]), np.array([0, 0, -1]), "wall", "south"),
    ]

    light_defs = []

    for hs in hotspots:
        az_rad = math.radians(hs["az"])
        el_rad = math.radians(hs["el"])

        # Direction vector from (azimuth, elevation)
        # Convention: az=0 is forward (+Z in splat bake space).
        # With the 180deg dome rotation fix, the HDRI azimuth=0 maps to
        # Houdini -Z. The bake itself encodes directions in bake-camera space.
        # We project in camera-centered coordinates then offset by cam position.
        dx = -math.sin(az_rad) * math.cos(el_rad)
        dy = math.sin(el_rad)
        dz = -math.cos(az_rad) * math.cos(el_rad)
        direction = np.array([dx, dy, dz], dtype=np.float64)
        d_len = np.linalg.norm(direction)
        if d_len < 1e-8:
            continue
        direction /= d_len

        # Ray-plane intersection against all room planes
        best_t = float("inf")
        best_plane = None
        best_point = None

        for plane_pt, plane_n, label, wall_id in planes:
            plane_n_f = plane_n.astype(np.float64)
            denom = np.dot(direction, plane_n_f)
            if abs(denom) < 1e-8:
                continue

            t = np.dot(plane_pt.astype(np.float64) - cam, plane_n_f) / denom
            if t <= 0:
                continue

            hit = cam + t * direction

            # Bounds check: hit point must be within room extents
            margin = 0.05
            if label == "ceiling":
                if not (x_min - margin <= hit[0] <= x_max + margin and
                        z_min - margin <= hit[2] <= z_max + margin):
                    continue
            elif wall_id in ("west", "east"):
                if not (z_min - margin <= hit[2] <= z_max + margin and
                        floor_y - margin <= hit[1] <= ceil_y + margin):
                    continue
            elif wall_id in ("north", "south"):
                if not (x_min - margin <= hit[0] <= x_max + margin and
                        floor_y - margin <= hit[1] <= ceil_y + margin):
                    continue

            if t < best_t:
                best_t = t
                best_plane = (label, wall_id, plane_n_f)
                best_point = hit

        if best_point is None:
            continue

        label, wall_id, plane_normal = best_plane
        pos = best_point.tolist()

        # Estimate light dimensions from hotspot pixel footprint
        # Use the HDRI resolution to compute angular extent
        hdri_h_deg = (hs["hh"] * 2.0 / 1024.0) * 180.0
        hdri_w_deg = (hs["hw"] * 2.0 / 2048.0) * 360.0
        light_w = max(0.2, 2.0 * best_t * math.tan(math.radians(hdri_w_deg / 2.0)))
        light_h = max(0.2, 2.0 * best_t * math.tan(math.radians(hdri_h_deg / 2.0)))

        # Clamp to reasonable dimensions
        light_w = min(light_w, abs(x_max - x_min) * 0.8)
        light_h = min(light_h, abs(ceil_y - floor_y) * 0.8)

        # Normalise color to unit brightness (intensity carries the magnitude)
        raw_color = hs.get("color", [1.0, 1.0, 1.0])
        color_max = max(raw_color[0], raw_color[1], raw_color[2], 0.01)
        color = [raw_color[0] / color_max,
                 raw_color[1] / color_max,
                 raw_color[2] / color_max]

        # Intensity from peak luminance (log-scale)
        intensity = max(0.5, math.log2(max(1.0, hs["val"])) * 0.5)

        # Compute rotation from normal — light faces INWARD
        from hdri_match_solaris.splatforge.lighting.light_extractor import _normal_to_rotation
        inward_normal = (-plane_normal).tolist()
        rotation = _normal_to_rotation(inward_normal)

        name = f"hdri_practical_{wall_id}_{hs['index']:02d}"
        ld = LightDefinition(
            light_type="rect",
            name=name,
            position=pos,
            color=color,
            intensity=intensity,
            exposure=0.0,
            width=light_w,
            height=light_h,
            normal=inward_normal,
            rotation=rotation,
            temperature=None,
            cast_shadows=True,
            source_cluster_id=f"hdri_hs_{hs['index']}",
        )
        light_defs.append(ld)

    return light_defs


# ---------------------------------------------------------------------------
# HDRI Inpainting — Remove Hotspot Regions
# ---------------------------------------------------------------------------

def inpaint_hotspots_from_hdri(hdri_array, hotspots, output_path=None,
                               padding=8, blend_falloff=12,
                               progress_callback=None):
    """Inpaint detected hotspot regions from the HDRI dome map.

    Replaces each hotspot region with diffused surrounding-color fill
    to remove direct illumination from the dome texture. This allows
    practical lights to provide the direct contribution without
    double-counting from the dome.

    Args:
        hdri_array:       (H, W, 3+) float32 array (modified in-place).
        hotspots:         List of hotspot dicts with ``px, py, hw, hh``.
        output_path:      Optional path to save the inpainted EXR.
        padding:          Extra pixels around the hotspot to inpaint.
        blend_falloff:    Pixel distance for blending inpaint into surroundings.
        progress_callback: Optional ``callable(percent, message)``.

    Returns:
        dict with ``'inpainted_path'`` (str or None), ``'array'`` (the modified array),
        ``'hotspot_count'`` (int).
    """
    if hdri_array is None or not hotspots:
        return {
            "inpainted_path": None,
            "array": hdri_array,
            "hotspot_count": 0,
        }

    arr = hdri_array.copy()
    H, W = arr.shape[:2]

    if progress_callback:
        progress_callback(5, f"Inpainting {len(hotspots)} hotspot region(s)...")

    for i, hs in enumerate(hotspots):
        cx, cy = int(hs["px"]), int(hs["py"])
        hw, hh = int(hs["hw"]) + padding, int(hs["hh"]) + padding

        # Build mask for this hotspot (with padding)
        y0 = max(0, cy - hh)
        y1 = min(H, cy + hh)

        # Sample surrounding ring color (non-hotspot boundary)
        ring_w = blend_falloff + 4
        ry0 = max(0, y0 - ring_w)
        ry1 = min(H, y1 + ring_w)

        ring_samples = []
        for ry in range(ry0, ry1):
            for rx_off in range(-hw - ring_w, hw + ring_w + 1):
                rx = (cx + rx_off) % W
                # Only sample if outside the hotspot rect but inside the ring
                in_hotspot = (y0 <= ry < y1) and (abs(rx_off) <= hw)
                if not in_hotspot:
                    ring_samples.append(arr[ry, rx, :3])

        if not ring_samples:
            continue

        fill_color = np.median(ring_samples, axis=0)

        # Apply inpainting: replace hotspot region with fill color,
        # blending at edges using a smooth falloff
        for ry in range(y0, y1):
            for rx_off in range(-hw, hw + 1):
                rx = (cx + rx_off) % W

                # Distance from hotspot edge for blend falloff
                dx = max(0, abs(rx_off) - (hw - blend_falloff))
                dy = max(0, min(abs(ry - cy) - (hh - blend_falloff), blend_falloff))
                edge_dist = math.sqrt(dx * dx + dy * dy)

                if edge_dist < blend_falloff:
                    # Blend ratio: 0 at center (full fill), 1 at edge (original)
                    t = edge_dist / max(1, blend_falloff)
                    # Smooth step
                    t = t * t * (3.0 - 2.0 * t)
                    arr[ry, rx, :3] = fill_color * (1.0 - t) + arr[ry, rx, :3] * t
                else:
                    arr[ry, rx, :3] = fill_color

        if progress_callback:
            pct = 5 + int(85 * (i + 1) / len(hotspots))
            progress_callback(pct, f"Inpainted hotspot {i + 1}/{len(hotspots)}")

    # Save inpainted EXR
    saved_path = None
    if output_path:
        try:
            from hdri_match_solaris.gaussian_splat import GaussianSplatBaker
            os.makedirs(os.path.dirname(output_path), exist_ok=True)
            GaussianSplatBaker.save_exr(arr, output_path)
            saved_path = output_path
            if progress_callback:
                progress_callback(100, f"Inpainted HDRI saved: {os.path.basename(output_path)}")
        except Exception as e:
            print(f"[SPLATFORGE] Inpainted HDRI save warning: {e}")

    return {
        "inpainted_path": saved_path,
        "array": arr,
        "hotspot_count": len(hotspots),
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_hdri_array(hdri_array=None, hdri_path=None):
    """Load HDRI array from memory or disk."""
    if hdri_array is not None:
        return hdri_array

    if hdri_path and os.path.isfile(hdri_path):
        try:
            from hdri_match_solaris.gaussian_splat import GaussianSplatBaker
            return GaussianSplatBaker.load_exr(hdri_path)
        except Exception as e:
            print(f"[SPLATFORGE] Could not load HDRI for hotspot analysis: {e}")

    return None
