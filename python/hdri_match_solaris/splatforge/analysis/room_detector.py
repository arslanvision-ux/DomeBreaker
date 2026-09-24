# -*- coding: utf-8 -*-
"""
SPLATFORGE Room Detector — Architectural Plane & Aperture Detection.

Extends the existing ``GaussianSplatScene.analyze_room_architecture()``
with optional RANSAC-based plane fitting (via Open3D) for higher quality
plane estimation, while preserving a pure-numpy fallback that wraps the
existing percentile-based approach.

Two detection backends are available:

1. **Percentile** (default, zero dependencies):
   Wraps the existing ``analyze_room_architecture()`` method directly.
   Fast, robust for typical single-room captures.

2. **RANSAC** (requires Open3D):
   Detects N dominant planes via iterative RANSAC, classifies them as
   floor / ceiling / wall by orientation, and regularises wall angles
   to Manhattan-world assumptions.

Both backends output the same ``RoomData`` structure.
"""

import math
import numpy as np

from hdri_match_solaris.splatforge import config


class RoomPlane:
    """A single detected architectural plane (floor, ceiling, or wall)."""

    def __init__(self, label, normal, distance, inlier_count=0,
                 center=None, extents=None, wall_id=None):
        """
        Args:
            label:        ``'floor'``, ``'ceiling'``, or ``'wall'``.
            normal:       (3,) unit normal pointing inward.
            distance:     Signed distance from origin along normal.
            inlier_count: Number of splats supporting this plane.
            center:       (3,) world-space center of the plane.
            extents:      (2,) plane span (width, height) for walls; (2,) for floor.
            wall_id:      Optional cardinal direction: ``'N'``, ``'S'``, ``'E'``, ``'W'``.
        """
        self.label = label
        self.normal = np.asarray(normal, dtype=np.float32)
        self.distance = float(distance)
        self.inlier_count = int(inlier_count)
        self.center = np.asarray(center, dtype=np.float32) if center is not None else None
        self.extents = np.asarray(extents, dtype=np.float32) if extents is not None else None
        self.wall_id = wall_id

    def to_dict(self):
        d = {
            "label": self.label,
            "normal": self.normal.tolist(),
            "distance": self.distance,
            "inlier_count": self.inlier_count,
        }
        if self.center is not None:
            d["center"] = self.center.tolist()
        if self.extents is not None:
            d["extents"] = self.extents.tolist()
        if self.wall_id is not None:
            d["wall_id"] = self.wall_id
        return d

    def __repr__(self):
        wid = f" ({self.wall_id})" if self.wall_id else ""
        return (f"RoomPlane(label={self.label}{wid}, "
                f"inliers={self.inlier_count:,}, d={self.distance:.3f})")


class RoomData:
    """Complete room analysis result.

    Provides unified access to room bounds, detected planes, apertures
    (windows/doors), and interior props regardless of the detection backend.
    """

    def __init__(self, data_dict, planes=None):
        """
        Args:
            data_dict: The raw result dict from ``analyze_room_architecture()``
                       or equivalent.
            planes:    Optional list of ``RoomPlane`` instances.
        """
        self._data = dict(data_dict)
        self._planes = planes or []

    # ------------------------------------------------------------------
    # Direct access to common fields
    # ------------------------------------------------------------------

    @property
    def floor_y(self):
        return float(self._data.get("floor_y", 0.0))

    @property
    def ceil_y(self):
        return float(self._data.get("ceil_y", 3.0))

    @property
    def height(self):
        return float(self._data.get("height", 3.0))

    @property
    def width(self):
        return float(self._data.get("width", 5.0))

    @property
    def depth(self):
        return float(self._data.get("depth", 5.0))

    @property
    def center(self):
        return self._data.get("center", (0.0, 0.0, 0.0))

    @property
    def x_min(self):
        return float(self._data.get("x_min", 0.0))

    @property
    def x_max(self):
        return float(self._data.get("x_max", 0.0))

    @property
    def z_min(self):
        return float(self._data.get("z_min", 0.0))

    @property
    def z_max(self):
        return float(self._data.get("z_max", 0.0))

    @property
    def estimated_scale(self):
        return float(self._data.get("estimated_scale", 1.0))

    @property
    def windows(self):
        return self._data.get("windows", [])

    @property
    def doors(self):
        return self._data.get("doors", [])

    @property
    def props(self):
        return self._data.get("props", [])

    @property
    def planes(self):
        return list(self._planes)

    @property
    def floor_plane(self):
        for p in self._planes:
            if p.label == "floor":
                return p
        return None

    @property
    def ceiling_plane(self):
        for p in self._planes:
            if p.label == "ceiling":
                return p
        return None

    @property
    def wall_planes(self):
        return [p for p in self._planes if p.label == "wall"]

    @property
    def raw(self):
        """Access the underlying raw data dict."""
        return self._data

    def to_dict(self):
        d = dict(self._data)
        if self._planes:
            d["detected_planes"] = [p.to_dict() for p in self._planes]
        return d

    def __repr__(self):
        return (f"RoomData(floor_y={self.floor_y:.2f}, ceil_y={self.ceil_y:.2f}, "
                f"W={self.width:.2f}, D={self.depth:.2f}, "
                f"windows={len(self.windows)}, doors={len(self.doors)}, "
                f"planes={len(self._planes)})")


# ---------------------------------------------------------------------------
# Percentile-based detector (wraps existing code)
# ---------------------------------------------------------------------------

def detect_room_percentile(splat_data, flip_y=True, scene_scale=1.0,
                           interior_mode=None, extract_props=False,
                           progress_callback=None):
    """Detect room architecture using the existing percentile-based approach.

    This wraps ``GaussianSplatScene.analyze_room_architecture()`` and produces
    ``RoomPlane`` objects from the percentile-detected boundaries.

    Args:
        splat_data:         ``SplatData`` instance.
        flip_y:             Convert COLMAP +Y-down to Houdini +Y-up.
        scene_scale:        Scene unit multiplier.
        interior_mode:      ``'tight'`` or ``'full'`` (default from config).
        extract_props:      Also extract interior prop clusters.
        progress_callback:  Optional ``callable(percent, message)``.

    Returns:
        ``RoomData`` instance.
    """
    if interior_mode is None:
        interior_mode = config.INTERIOR_MODE_DEFAULT

    scene = splat_data.scene
    raw = scene.analyze_room_architecture(
        flip_y=flip_y,
        scene_scale=scene_scale,
        interior_mode=interior_mode,
        extract_props=extract_props,
        progress_callback=progress_callback,
    )

    if raw is None:
        return RoomData({})

    # Build RoomPlane objects from the percentile boundaries
    planes = _planes_from_room_dict(raw)
    return RoomData(raw, planes=planes)


def _planes_from_room_dict(rd):
    """Synthesise RoomPlane objects from a raw room dict."""
    planes = []
    floor_y = rd.get("floor_y", 0.0)
    ceil_y = rd.get("ceil_y", 3.0)
    x_min = rd.get("x_min", 0.0)
    x_max = rd.get("x_max", 0.0)
    z_min = rd.get("z_min", 0.0)
    z_max = rd.get("z_max", 0.0)
    width = rd.get("width", 1.0)
    depth = rd.get("depth", 1.0)
    height = rd.get("height", 1.0)
    cx = (x_min + x_max) * 0.5
    cz = (z_min + z_max) * 0.5

    # Floor
    planes.append(RoomPlane(
        label="floor",
        normal=[0.0, 1.0, 0.0],
        distance=floor_y,
        center=[cx, floor_y, cz],
        extents=[width, depth],
    ))

    # Ceiling
    planes.append(RoomPlane(
        label="ceiling",
        normal=[0.0, -1.0, 0.0],
        distance=ceil_y,
        center=[cx, ceil_y, cz],
        extents=[width, depth],
    ))

    # Walls (normals point inward)
    # West wall (X = x_min, normal = +X)
    planes.append(RoomPlane(
        label="wall", wall_id="W",
        normal=[1.0, 0.0, 0.0],
        distance=x_min,
        center=[x_min, (floor_y + ceil_y) * 0.5, cz],
        extents=[depth, height],
    ))
    # East wall (X = x_max, normal = -X)
    planes.append(RoomPlane(
        label="wall", wall_id="E",
        normal=[-1.0, 0.0, 0.0],
        distance=x_max,
        center=[x_max, (floor_y + ceil_y) * 0.5, cz],
        extents=[depth, height],
    ))
    # North wall (Z = z_min, normal = +Z)
    planes.append(RoomPlane(
        label="wall", wall_id="N",
        normal=[0.0, 0.0, 1.0],
        distance=z_min,
        center=[cx, (floor_y + ceil_y) * 0.5, z_min],
        extents=[width, height],
    ))
    # South wall (Z = z_max, normal = -Z)
    planes.append(RoomPlane(
        label="wall", wall_id="S",
        normal=[0.0, 0.0, -1.0],
        distance=z_max,
        center=[cx, (floor_y + ceil_y) * 0.5, z_max],
        extents=[width, height],
    ))
    return planes


# ---------------------------------------------------------------------------
# RANSAC-based detector (requires Open3D)
# ---------------------------------------------------------------------------

def detect_room_ransac(splat_data, flip_y=True, scene_scale=1.0,
                       distance_threshold=None, min_inliers=None,
                       max_iterations=None, extract_props=False,
                       progress_callback=None):
    """Detect room architecture using RANSAC plane fitting via Open3D.

    This provides higher quality plane estimation compared to the percentile
    approach, especially for non-rectangular rooms or scenes with significant
    exterior splat leakage.

    Falls back to ``detect_room_percentile()`` if Open3D is unavailable.

    Args:
        splat_data:           ``SplatData`` instance.
        flip_y:               Convert COLMAP +Y-down to Houdini +Y-up.
        scene_scale:          Scene unit multiplier.
        distance_threshold:   RANSAC inlier distance (default from config).
        min_inliers:          Minimum inliers per plane (default from config).
        max_iterations:       RANSAC iterations (default from config).
        extract_props:        Also extract interior prop clusters.
        progress_callback:    Optional ``callable(percent, message)``.

    Returns:
        ``RoomData`` instance.
    """
    try:
        import open3d as o3d
    except ImportError:
        if progress_callback:
            progress_callback(0, "Open3D not available, falling back to percentile detection.")
        return detect_room_percentile(
            splat_data, flip_y=flip_y, scene_scale=scene_scale,
            extract_props=extract_props, progress_callback=progress_callback,
        )

    if distance_threshold is None:
        distance_threshold = config.RANSAC_DISTANCE_THRESHOLD
    if min_inliers is None:
        min_inliers = config.RANSAC_MIN_INLIERS
    if max_iterations is None:
        max_iterations = config.RANSAC_MAX_ITERATIONS

    if progress_callback:
        progress_callback(5, "Preparing point cloud for RANSAC...")

    # Also run the percentile detector for apertures and props
    base_room = detect_room_percentile(
        splat_data, flip_y=flip_y, scene_scale=scene_scale,
        extract_props=extract_props, progress_callback=None,
    )

    # Prepare filtered point cloud
    pos = splat_data.positions_houdini(flip_y=flip_y, scene_scale=scene_scale)
    valid = splat_data.filter_by_opacity(config.OPACITY_THRESHOLD)
    pos_f = pos[valid]

    if len(pos_f) < 1000:
        return base_room

    # Subsample for RANSAC performance
    max_pts = 200000
    if len(pos_f) > max_pts:
        idx = np.random.RandomState(42).choice(len(pos_f), max_pts, replace=False)
        pos_f = pos_f[idx]

    pcd = o3d.geometry.PointCloud()
    pcd.points = o3d.utility.Vector3dVector(pos_f.astype(np.float64))

    if progress_callback:
        progress_callback(15, "Estimating normals...")

    pcd.estimate_normals(
        search_param=o3d.geometry.KDTreeSearchParamHybrid(
            radius=distance_threshold * 5, max_nn=30
        )
    )

    if progress_callback:
        progress_callback(25, "Running iterative RANSAC plane detection...")

    # Iterative RANSAC: detect up to 10 planes
    planes = []
    remaining_pcd = pcd
    remaining_pts = np.asarray(remaining_pcd.points)

    for plane_idx in range(10):
        if len(remaining_pts) < min_inliers:
            break

        plane_model, inliers = remaining_pcd.segment_plane(
            distance_threshold=distance_threshold,
            ransac_n=3,
            num_iterations=max_iterations,
        )

        if len(inliers) < min_inliers:
            break

        a, b, c, d = plane_model
        normal = np.array([a, b, c], dtype=np.float32)
        norm_len = np.linalg.norm(normal)
        if norm_len < 1e-8:
            break
        normal /= norm_len

        inlier_pts = remaining_pts[inliers]
        plane_center = np.mean(inlier_pts, axis=0).astype(np.float32)

        # Classify: horizontal (floor/ceiling) or vertical (wall)
        y_component = abs(float(normal[1]))
        is_horizontal = y_component > math.cos(config.FLOOR_HORIZONTAL_TOLERANCE_RAD)
        is_vertical = y_component < math.sin(config.WALL_VERTICAL_TOLERANCE_RAD)

        if is_horizontal:
            # Floor or ceiling based on Y position
            if plane_center[1] < (base_room.floor_y + base_room.ceil_y) * 0.5:
                label = "floor"
                # Ensure normal points up
                if normal[1] < 0:
                    normal = -normal
            else:
                label = "ceiling"
                # Ensure normal points down
                if normal[1] > 0:
                    normal = -normal

            extents = _compute_horizontal_extents(inlier_pts)
        elif is_vertical:
            label = "wall"
            # Ensure normal points inward (toward room center)
            to_center = np.array(base_room.center, dtype=np.float32) - plane_center
            if np.dot(normal, to_center) < 0:
                normal = -normal

            extents = _compute_wall_extents(inlier_pts, base_room)
        else:
            # Angled surface — skip for room detection
            remaining_pcd = remaining_pcd.select_by_index(inliers, invert=True)
            remaining_pts = np.asarray(remaining_pcd.points)
            continue

        # Determine wall cardinal direction
        wall_id = None
        if label == "wall":
            wall_id = _classify_wall_cardinal(normal)

        rp = RoomPlane(
            label=label,
            normal=normal.tolist(),
            distance=float(-d / norm_len),
            inlier_count=len(inliers),
            center=plane_center.tolist(),
            extents=extents.tolist() if extents is not None else None,
            wall_id=wall_id,
        )
        planes.append(rp)

        if progress_callback:
            progress_callback(
                25 + int(50 * (plane_idx + 1) / 10),
                f"Detected {label}{' (' + wall_id + ')' if wall_id else ''}: "
                f"{len(inliers):,} inliers"
            )

        # Remove inliers from cloud
        remaining_pcd = remaining_pcd.select_by_index(inliers, invert=True)
        remaining_pts = np.asarray(remaining_pcd.points)

    if progress_callback:
        progress_callback(90, f"Detected {len(planes)} architectural planes.")

    # Merge RANSAC planes with percentile-based aperture data
    result_data = base_room.raw
    result_data["detection_backend"] = "ransac"
    result_data["ransac_planes_detected"] = len(planes)

    if progress_callback:
        progress_callback(100, "Room detection complete.")

    return RoomData(result_data, planes=planes)


def _compute_horizontal_extents(pts):
    """Compute XZ extents for a horizontal plane."""
    x_range = float(np.percentile(pts[:, 0], 97) - np.percentile(pts[:, 0], 3))
    z_range = float(np.percentile(pts[:, 2], 97) - np.percentile(pts[:, 2], 3))
    return np.array([x_range, z_range], dtype=np.float32)


def _compute_wall_extents(pts, room_data):
    """Compute horizontal span and height for a wall plane."""
    y_range = float(np.percentile(pts[:, 1], 97) - np.percentile(pts[:, 1], 3))
    # Horizontal span depends on wall orientation
    x_range = float(np.percentile(pts[:, 0], 97) - np.percentile(pts[:, 0], 3))
    z_range = float(np.percentile(pts[:, 2], 97) - np.percentile(pts[:, 2], 3))
    horiz_span = max(x_range, z_range)
    return np.array([horiz_span, y_range], dtype=np.float32)


def _classify_wall_cardinal(normal):
    """Classify a wall normal into a cardinal direction (N/S/E/W)."""
    nx, ny, nz = normal
    abs_x = abs(nx)
    abs_z = abs(nz)
    if abs_x > abs_z:
        return "W" if nx > 0 else "E"
    else:
        return "N" if nz > 0 else "S"


# ---------------------------------------------------------------------------
# Unified detection entry point
# ---------------------------------------------------------------------------

def detect_room(splat_data, flip_y=True, scene_scale=1.0,
                backend="auto", extract_props=False,
                progress_callback=None, **kwargs):
    """Detect room architecture using the best available backend.

    Args:
        splat_data:         ``SplatData`` instance.
        flip_y:             Convert COLMAP +Y-down to Houdini +Y-up.
        scene_scale:        Scene unit multiplier.
        backend:            ``'auto'`` (prefer RANSAC), ``'ransac'``, or
                            ``'percentile'``.
        extract_props:      Also extract interior prop clusters.
        progress_callback:  Optional ``callable(percent, message)``.
        **kwargs:           Forwarded to the chosen backend.

    Returns:
        ``RoomData`` instance.
    """
    from hdri_match_solaris.splatforge import HAS_OPEN3D

    if backend == "auto":
        backend = "ransac" if HAS_OPEN3D else "percentile"

    if backend == "ransac":
        return detect_room_ransac(
            splat_data, flip_y=flip_y, scene_scale=scene_scale,
            extract_props=extract_props,
            progress_callback=progress_callback, **kwargs,
        )
    else:
        return detect_room_percentile(
            splat_data, flip_y=flip_y, scene_scale=scene_scale,
            extract_props=extract_props,
            progress_callback=progress_callback,
        )
