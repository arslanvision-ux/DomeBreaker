# -*- coding: utf-8 -*-
"""
HDRI Room Dimension Analyzer
============================
Analyzes 3D physical room dimensions (Width, Depth, Height, Camera Offsets, and Orientation)
directly from a 2D equirectangular 360° HDRI panorama.

Under the architectural Manhattan World assumption (orthogonal vertical walls and horizontal
floor/ceiling), the solver detects:
1. Vertical wall-wall corner intersections (azimuth angles phi_1..4).
2. Floor-wall boundary sinusoidal curves (giving wall distances via camera height anchor).
3. Ceiling-wall boundary sinusoidal curves (giving total room height).
4. Room yaw orientation relative to camera forward.

Pure NumPy + PIL implementation for real-time sub-second execution without heavy ML dependencies.
"""

import math
import os
import numpy as np
from PIL import Image, ImageDraw


def _downsample_and_luminance(img_array, target_w=1024, target_h=512):
    """Convert input HDRI image array to normalized log-luminance thumbnail for fast gradient analysis."""
    if not isinstance(img_array, np.ndarray):
        return None, None

    h, w = img_array.shape[:2]
    # Ensure 3-channel RGB float32
    if img_array.ndim == 2:
        rgb = np.stack([img_array, img_array, img_array], axis=-1).astype(np.float32)
    elif img_array.shape[2] >= 3:
        rgb = img_array[..., :3].astype(np.float32)
    else:
        rgb = img_array.astype(np.float32)

    # Downsample if needed
    if w > target_w or h > target_h:
        step_x = max(1, w // target_w)
        step_y = max(1, h // target_h)
        rgb_small = rgb[::step_y, ::step_x]
    else:
        rgb_small = rgb

    th, tw = rgb_small.shape[:2]

    # Compute luminance (Rec.709 / ACEScg weights)
    lum = 0.2126 * rgb_small[..., 0] + 0.7152 * rgb_small[..., 1] + 0.0722 * rgb_small[..., 2]
    lum = np.maximum(0.0, lum)

    # Compress dynamic range via robust log transform
    med_val = float(np.median(lum[lum > 0])) if np.any(lum > 0) else 1.0
    if med_val <= 0.0:
        med_val = 1.0
    log_lum = np.log1p(lum / med_val)
    # Normalize to 0..1
    p99 = float(np.percentile(log_lum, 99.0))
    if p99 > 1e-4:
        norm_lum = np.clip(log_lum / p99, 0.0, 1.0)
    else:
        norm_lum = np.clip(log_lum, 0.0, 1.0)

    return norm_lum, rgb_small


def _smooth_1d(signal, window_size=11):
    """Simple moving average with circular boundary wrapping."""
    if window_size <= 1:
        return signal
    pad = window_size // 2
    wrapped = np.concatenate([signal[-pad:], signal, signal[:pad]])
    kernel = np.ones(window_size, dtype=np.float32) / window_size
    smoothed = np.convolve(wrapped, kernel, mode="valid")
    return smoothed[:len(signal)]


def _detect_wall_corners(norm_lum):
    """
    Detect the 4 dominant orthogonal vertical corners and room yaw orientation.
    In equirectangular panoramas, vertical wall corners remain vertical columns.
    """
    h, w = norm_lum.shape
    # Focus on equatorial wall belt (elevation -35 deg to +35 deg)
    y_start = int(h * 0.30)
    y_end = int(h * 0.70)
    belt = norm_lum[y_start:y_end, :]

    # Horizontal gradient (circular wrapping across 360-deg seams)
    left = np.roll(belt, 1, axis=1)
    right = np.roll(belt, -1, axis=1)
    grad_x = np.abs(right - left)

    # Accumulate vertical line energy across columns
    col_scores = np.mean(grad_x, axis=0)
    col_scores = _smooth_1d(col_scores, window_size=max(5, w // 64))

    # Manhattan World assumption: corners are spaced exactly 90 deg (w / 4) apart.
    quarter = w // 4
    best_score = -1.0
    best_offset = 0

    # Search for base corner offset in [0, quarter)
    for offset in range(quarter):
        c0 = offset
        c1 = (offset + quarter) % w
        c2 = (offset + 2 * quarter) % w
        c3 = (offset + 3 * quarter) % w
        quad_score = col_scores[c0] + col_scores[c1] + col_scores[c2] + col_scores[c3]
        if quad_score > best_score:
            best_score = quad_score
            best_offset = offset

    # The 4 corner pixel column indices
    corners_x = [
        best_offset,
        (best_offset + quarter) % w,
        (best_offset + 2 * quarter) % w,
        (best_offset + 3 * quarter) % w,
    ]
    corners_x.sort()

    # Convert best_offset to room yaw in degrees (-180..+180)
    # At x = w/2, azimuth = 0 (forward / North / -Z)
    base_azimuth_rad = (float(best_offset) / w) * 2.0 * math.pi - math.pi
    yaw_deg = math.degrees(base_azimuth_rad)

    return corners_x, yaw_deg, col_scores


def _detect_floor_boundary(norm_lum, corners_x):
    """
    Detect the boundary line between floor and wall in the lower hemisphere.
    Returns the detected boundary row y_floor[x] for each column x.
    """
    h, w = norm_lum.shape
    y_mid = h // 2

    # Vertical gradient in lower hemisphere
    down = np.roll(norm_lum, -1, axis=0)
    up = np.roll(norm_lum, 1, axis=0)
    grad_y = np.abs(down - up)

    floor_y = np.zeros(w, dtype=np.float32)

    # Search each column for the strongest floor-to-wall transition
    # Lower hemisphere is y in [y_mid + 5, h - 5]
    search_start = y_mid + int(h * 0.04)
    search_end = h - int(h * 0.04)

    for x in range(w):
        col_grad = grad_y[search_start:search_end, x]
        if len(col_grad) == 0:
            floor_y[x] = search_start + (search_end - search_start) * 0.5
            continue
        max_idx = int(np.argmax(col_grad))
        floor_y[x] = float(search_start + max_idx)

    # Smooth the raw floor detections
    floor_y = _smooth_1d(floor_y, window_size=max(7, w // 32))

    return floor_y


def _detect_ceiling_boundary(norm_lum, corners_x):
    """
    Detect the boundary line between wall and ceiling in the upper hemisphere.
    Returns the detected boundary row y_ceil[x] for each column x.
    """
    h, w = norm_lum.shape
    y_mid = h // 2

    down = np.roll(norm_lum, -1, axis=0)
    up = np.roll(norm_lum, 1, axis=0)
    grad_y = np.abs(down - up)

    ceil_y = np.zeros(w, dtype=np.float32)

    search_start = int(h * 0.04)
    search_end = y_mid - int(h * 0.04)

    for x in range(w):
        col_grad = grad_y[search_start:search_end, x]
        if len(col_grad) == 0:
            ceil_y[x] = search_start + (search_end - search_start) * 0.5
            continue
        max_idx = int(np.argmax(col_grad))
        ceil_y[x] = float(search_start + max_idx)

    # Smooth the raw ceiling detections
    ceil_y = _smooth_1d(ceil_y, window_size=max(7, w // 32))

    return ceil_y


def analyze_hdri_room(image_input, tripod_height=1.50, user_yaw_offset=0.0):
    """
    Analyze physical 3D room dimensions from an equirectangular HDRI.

    Args:
        image_input: NumPy array (float32 or uint8 [H, W, 3]) or file path (str).
        tripod_height: Known or assumed height of the panoramic camera lens from the floor (meters).
                       Acts as the fundamental metric scale anchor. Default is 1.50m.
        user_yaw_offset: Additional user yaw rotation offset in degrees.

    Returns:
        dict containing:
            - room_width: Solved room dimension along X (meters)
            - room_depth: Solved room dimension along Z (meters)
            - room_height: Solved total floor-to-ceiling height (meters)
            - tripod_height: Scale anchor used (meters)
            - cam_offset_x: Solved camera displacement from room center along X (meters)
            - cam_offset_z: Solved camera displacement from room center along Z (meters)
            - yaw: Detected room rotation angle in degrees
            - confidence: Detection confidence score (0..100%)
            - floor_y_curve: 1D array of solved floor boundary rows (normalized 0..1)
            - ceil_y_curve: 1D array of solved ceiling boundary rows (normalized 0..1)
            - corners_x_norm: List of 4 normalized corner column positions (0..1)
    """
    # 1. Load image if string path provided
    if isinstance(image_input, str):
        if not os.path.isfile(image_input):
            raise FileNotFoundError(f"HDRI image file not found: {image_input}")
        try:
            # Check for OpenEXR / image loading
            pil_img = Image.open(image_input)
            img_arr = np.array(pil_img, dtype=np.float32)
        except Exception:
            # Fallback for HDR/EXR formats via raw numpy or image loader
            import sys
            for cand in [os.environ.get("HDRI_MATCH_PLATE_ROOT", ""), "E:/PROJECTS/HDRI_Match_Plate", "e:/PROJECTS/HDRI_Match_Plate"]:
                if cand and os.path.isdir(cand) and cand not in sys.path:
                    sys.path.insert(0, cand)
            from hdri_match.io.loader import load_exr_to_numpy
            img_arr = load_exr_to_numpy(image_input)
    else:
        img_arr = np.asarray(image_input, dtype=np.float32)

    norm_lum, rgb_small = _downsample_and_luminance(img_arr, target_w=1024, target_h=512)
    if norm_lum is None:
        raise ValueError("Invalid image array provided for HDRI room analysis.")

    h, w = norm_lum.shape
    y_mid = h / 2.0
    tripod_h = max(0.5, float(tripod_height))

    # 2. Detect 4 orthogonal vertical corners & base yaw
    corners_x, detected_yaw, col_scores = _detect_wall_corners(norm_lum)

    # 3. Detect floor and ceiling boundary profiles across all columns
    floor_y_raw = _detect_floor_boundary(norm_lum, corners_x)
    ceil_y_raw = _detect_ceiling_boundary(norm_lum, corners_x)

    # 4. Fit Manhattan 4-wall distances from floor elevations
    # In equirectangular:
    # x in [0, w-1] -> azimuth phi = (x / w) * 2*pi - pi
    # elevation theta = -((y - y_mid) / (h / 2)) * (pi / 2)  (negative below equator)
    # Ray distance to floor contact: d(phi) = tripod_h / |tan(theta_floor)|
    wall_distances = []
    wall_elevations_floor = []
    wall_elevations_ceil = []

    # Process each of the 4 wall spans between corners
    for i in range(4):
        c_curr = corners_x[i]
        c_next = corners_x[(i + 1) % 4]
        if c_next > c_curr:
            cols = np.arange(c_curr, c_next)
        else:
            cols = np.concatenate([np.arange(c_curr, w), np.arange(0, c_next)])

        if len(cols) == 0:
            wall_distances.append(4.0)
            continue

        # Ignore outer 15% of the wall span near corners to avoid corner occlusion
        trim = max(1, int(len(cols) * 0.15))
        core_cols = cols[trim:-trim] if len(cols) > 2 * trim else cols

        # Mean/median floor row in wall core
        mean_y_floor = float(np.median(floor_y_raw[core_cols]))
        mean_y_ceil = float(np.median(ceil_y_raw[core_cols]))

        # Elevation angles (radians)
        # Floor is below equator (y > y_mid) -> theta < 0
        elev_floor = -((mean_y_floor - y_mid) / y_mid) * (math.pi / 2.0)
        elev_floor = min(-0.05, max(-1.45, elev_floor))  # clamp -83 deg to -3 deg

        # Ceiling is above equator (y < y_mid) -> theta > 0
        elev_ceil = ((y_mid - mean_y_ceil) / y_mid) * (math.pi / 2.0)
        elev_ceil = max(0.05, min(1.45, elev_ceil))   # clamp +3 deg to +83 deg

        # Perpendicular distance to this wall = tripod_h / |tan(elev_floor)|
        dist_perp = tripod_h / max(0.05, abs(math.tan(elev_floor)))
        dist_perp = max(1.0, min(100.0, dist_perp))  # clamp 1m to 100m

        wall_distances.append(dist_perp)
        wall_elevations_floor.append(elev_floor)
        wall_elevations_ceil.append(elev_ceil)

    # 4 walls: 0=Wall A, 1=Wall B, 2=Wall C, 3=Wall D
    # Opposite pairs: (0, 2) and (1, 3)
    d0, d1, d2, d3 = wall_distances

    dim_a = max(1.5, d0 + d2)
    dim_b = max(1.5, d1 + d3)
    offset_a = (d0 - d2) * 0.5
    offset_b = (d1 - d3) * 0.5

    # Align solved dimensions to X (width) and Z (depth)
    # Wall 0 and 2 align closer to X or Z depending on base yaw
    eff_yaw = (detected_yaw + user_yaw_offset) % 360.0
    if abs(eff_yaw) < 45.0 or abs(eff_yaw) > 135.0:
        room_width = float(round(dim_a, 2))
        room_depth = float(round(dim_b, 2))
        cam_offset_x = float(round(offset_a, 2))
        cam_offset_z = float(round(offset_b, 2))
    else:
        room_width = float(round(dim_b, 2))
        room_depth = float(round(dim_a, 2))
        cam_offset_x = float(round(offset_b, 2))
        cam_offset_z = float(round(offset_a, 2))

    # 5. Solve ceiling height & total room height
    # h_ceil = dist * tan(elev_ceil)
    h_ceils = [d * math.tan(ec) for d, ec in zip(wall_distances, wall_elevations_ceil)]
    h_ceil_median = float(np.median(h_ceils)) if h_ceils else 1.5
    h_ceil_median = max(0.8, min(20.0, h_ceil_median))
    room_height = float(round(tripod_h + h_ceil_median, 2))

    # 6. Compute detection confidence score (0..100)
    # High score if col_scores show distinct corner peaks and floor boundary is continuous
    corner_peaks = [col_scores[c] for c in corners_x]
    mean_peak = float(np.mean(corner_peaks))
    mean_all = float(np.mean(col_scores)) + 1e-6
    peak_contrast = min(3.0, mean_peak / mean_all)
    confidence = int(min(98.0, max(40.0, 35.0 + peak_contrast * 20.0)))

    # 7. Generate smoothed analytic boundary curves for overlay visualization
    overlay_curves = compute_room_overlay_curves(
        room_width=room_width,
        room_depth=room_depth,
        room_height=room_height,
        tripod_height=tripod_h,
        yaw_deg=eff_yaw,
        num_points=w,
    )
    floor_curve_norm = overlay_curves["floor_y_curve"]
    ceil_curve_norm = overlay_curves["ceil_y_curve"]
    corners_x_norm = [float(c) / w for c in corners_x]

    return {
        "room_width": room_width,
        "room_depth": room_depth,
        "room_height": room_height,
        "tripod_height": round(tripod_h, 2),
        "cam_offset_x": cam_offset_x,
        "cam_offset_z": cam_offset_z,
        "yaw": round(eff_yaw, 1),
        "confidence": confidence,
        "floor_y_curve": floor_curve_norm,
        "ceil_y_curve": ceil_curve_norm,
        "corners_x_norm": corners_x_norm,
    }


def compute_room_overlay_curves(room_width, room_depth, room_height, tripod_height, yaw_deg=0.0, num_points=512):
    """
    Compute normalized floor and ceiling boundary curves (0..1) and corner columns for arbitrary room dimensions.
    Enables instant interactive preview updates when the artist adjusts sliders in the UI.
    """
    w = max(64, int(num_points))
    h = w // 2
    y_mid = h / 2.0
    tripod_h = max(0.1, float(tripod_height))
    h_ceil = max(0.2, float(room_height) - tripod_h)

    floor_curve = np.zeros(w, dtype=np.float32)
    ceil_curve = np.zeros(w, dtype=np.float32)

    eff_yaw = float(yaw_deg) % 360.0
    half_w = max(0.2, float(room_width) * 0.5)
    half_d = max(0.2, float(room_depth) * 0.5)

    for x in range(w):
        az = (float(x) / w) * 2.0 * math.pi - math.pi - math.radians(eff_yaw)
        cos_az = abs(math.cos(az)) + 1e-5
        sin_az = abs(math.sin(az)) + 1e-5
        d_az = min(half_d / cos_az, half_w / sin_az)

        th_f = -math.atan(tripod_h / max(0.05, d_az))
        row_f = y_mid - (th_f / (math.pi / 2.0)) * y_mid
        floor_curve[x] = np.clip(row_f / h, 0.0, 1.0)

        th_c = math.atan(h_ceil / max(0.05, d_az))
        row_c = y_mid - (th_c / (math.pi / 2.0)) * y_mid
        ceil_curve[x] = np.clip(row_c / h, 0.0, 1.0)

    # 4 corner angles in normalized x [0..1]
    corner_azimuths = [
        math.atan2(half_w, half_d),
        math.atan2(half_w, -half_d),
        math.atan2(-half_w, -half_d),
        math.atan2(-half_w, half_d),
    ]
    corners_x_norm = []
    for ca in corner_azimuths:
        c_world = (ca + math.radians(eff_yaw) + math.pi) % (2.0 * math.pi) - math.pi
        norm_x = (c_world + math.pi) / (2.0 * math.pi)
        corners_x_norm.append(norm_x)
    corners_x_norm.sort()

    return {
        "floor_y_curve": floor_curve,
        "ceil_y_curve": ceil_curve,
        "corners_x_norm": corners_x_norm,
    }


def generate_room_analysis_overlay(image_input, result, preview_w=640, preview_h=320):
    """
    Render an annotated visual preview of the HDRI with detected 3D room boundaries overlaid.
    - Floor boundary: Vibrant Cyan curve (#00e5ff)
    - Ceiling boundary: Bright Amber curve (#ffb700)
    - 4 Wall Corners: Bright Magenta vertical lines (#ff007f)
    - Horizon: Subtle dashed eye-level line

    Returns:
        PIL.Image in RGB format.
    """
    if isinstance(image_input, str) and os.path.isfile(image_input):
        pil_base = Image.open(image_input).convert("RGB")
    elif isinstance(image_input, np.ndarray):
        # Tone-mapped uint8
        arr = image_input.copy().astype(np.float32)
        if arr.ndim == 2:
            arr = np.stack([arr, arr, arr], axis=-1)
        arr = arr[..., :3]
        max_v = float(np.percentile(arr, 98.0)) if np.any(arr > 0) else 1.0
        if max_v <= 0.0:
            max_v = 1.0
        # Simple gamma curve for preview
        norm = np.clip(arr / max_v, 0.0, 1.0) ** (1.0 / 2.2)
        uint8_img = (norm * 255.0).astype(np.uint8)
        pil_base = Image.fromarray(uint8_img, mode="RGB")
    else:
        pil_base = Image.new("RGB", (preview_w, preview_h), (30, 30, 35))

    resample_filter = getattr(getattr(Image, 'Resampling', None), 'BILINEAR', getattr(Image, 'BILINEAR', 2))
    pil_resized = pil_base.resize((preview_w, preview_h), resample_filter)
    draw = ImageDraw.Draw(pil_resized)

    # 1. Horizon Line (dashed eye-level equator)
    y_mid = preview_h // 2
    for x_dash in range(0, preview_w, 16):
        draw.line([(x_dash, y_mid), (min(preview_w, x_dash + 8), y_mid)], fill=(180, 180, 190), width=1)

    # 2. Vertical Wall Corners (Magenta)
    corners_norm = result.get("corners_x_norm", [0.125, 0.375, 0.625, 0.875])
    for c_norm in corners_norm:
        cx = int(c_norm * preview_w) % preview_w
        draw.line([(cx, 0), (cx, preview_h)], fill=(255, 0, 127), width=2)

    # 3. Floor Boundary (Cyan)
    floor_curve = result.get("floor_y_curve")
    if floor_curve is not None and len(floor_curve) > 0:
        pts_floor = []
        for px in range(preview_w):
            idx = int((px / preview_w) * len(floor_curve)) % len(floor_curve)
            py = int(floor_curve[idx] * preview_h)
            pts_floor.append((px, py))
        if len(pts_floor) > 1:
            draw.line(pts_floor, fill=(0, 229, 255), width=3)

    # 4. Ceiling Boundary (Amber)
    ceil_curve = result.get("ceil_y_curve")
    if ceil_curve is not None and len(ceil_curve) > 0:
        pts_ceil = []
        for px in range(preview_w):
            idx = int((px / preview_w) * len(ceil_curve)) % len(ceil_curve)
            py = int(ceil_curve[idx] * preview_h)
            pts_ceil.append((px, py))
        if len(pts_ceil) > 1:
            draw.line(pts_ceil, fill=(255, 183, 0), width=3)

    return pil_resized
