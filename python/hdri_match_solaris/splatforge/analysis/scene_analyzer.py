# -*- coding: utf-8 -*-
"""
SPLATFORGE Scene Analyzer — Statistical Analysis of Gaussian Splat Scenes.

Computes spatial distribution metrics, density analysis, and diagnostic
statistics used by downstream reconstruction and lighting modules.
"""

import numpy as np

from hdri_match_solaris.splatforge import config


class SceneAnalysis:
    """Immutable result container for scene analysis."""

    def __init__(self, data):
        self._data = dict(data)

    def __getitem__(self, key):
        return self._data[key]

    def get(self, key, default=None):
        return self._data.get(key, default)

    def to_dict(self):
        return dict(self._data)

    def __repr__(self):
        n = self._data.get("count", 0)
        return f"SceneAnalysis(splats={n:,})"


def analyze_scene(splat_data, flip_y=True, scene_scale=1.0,
                  progress_callback=None):
    """Compute comprehensive spatial statistics for a Gaussian Splat scene.

    Args:
        splat_data:         ``SplatData`` instance.
        flip_y:             Convert COLMAP +Y-down to Houdini +Y-up.
        scene_scale:        Scene unit multiplier.
        progress_callback:  Optional ``callable(percent, message)``.

    Returns:
        ``SceneAnalysis`` instance containing spatial and radiometric stats.
    """
    if progress_callback:
        progress_callback(5, "Computing scene statistics...")

    pos = splat_data.positions_houdini(flip_y=flip_y, scene_scale=scene_scale)
    colors = splat_data.colors
    opacities = splat_data.opacities
    scales = splat_data.scales
    count = splat_data.count

    # ------------------------------------------------------------------
    # Spatial statistics
    # ------------------------------------------------------------------
    bbox_min = np.min(pos, axis=0)
    bbox_max = np.max(pos, axis=0)
    bbox_size = bbox_max - bbox_min

    # Robust center (median, outlier-resistant)
    if count > 100000:
        step = count // 50000
        center = np.median(pos[::step], axis=0)
    else:
        center = np.median(pos, axis=0)

    # Radial distribution from center
    dists = np.linalg.norm(pos - center, axis=1)
    radius_50 = float(np.percentile(dists, 50))
    radius_90 = float(np.percentile(dists, 90))
    radius_95 = float(np.percentile(dists, 95))
    radius_99 = float(np.percentile(dists, 99))

    if progress_callback:
        progress_callback(30, "Computing density statistics...")

    # ------------------------------------------------------------------
    # Density analysis
    # ------------------------------------------------------------------
    # Y-axis (height) distribution
    y_vals = pos[:, 1]
    y_percentiles = {
        f"y_p{p}": float(np.percentile(y_vals, p))
        for p in [1, 3, 5, 10, 25, 50, 75, 90, 95, 97, 99]
    }

    # Scale statistics
    mean_scale = np.mean(scales, axis=0)
    median_scale = np.median(scales, axis=0)
    max_scale = np.max(scales, axis=0)

    if progress_callback:
        progress_callback(50, "Computing radiometric statistics...")

    # ------------------------------------------------------------------
    # Radiometric statistics
    # ------------------------------------------------------------------
    luminance = (0.2126 * colors[:, 0] +
                 0.7152 * colors[:, 1] +
                 0.0722 * colors[:, 2])

    effective_energy = luminance * opacities

    # Opacity distribution
    opacity_hist_edges = [0.0, 0.1, 0.2, 0.3, 0.5, 0.7, 0.9, 1.0]
    opacity_hist = np.histogram(opacities, bins=opacity_hist_edges)[0].tolist()

    # Bright splat count (candidates for light sources)
    bright_threshold = float(np.percentile(effective_energy, 95))
    bright_count = int(np.sum(effective_energy >= bright_threshold))

    if progress_callback:
        progress_callback(75, "Estimating scene scale...")

    # ------------------------------------------------------------------
    # Scale estimation
    # ------------------------------------------------------------------
    # Estimate capture-to-metres scale using assumed room height
    raw_floor_y = float(np.percentile(y_vals, 3))
    raw_ceil_y = float(np.percentile(y_vals, 97))
    raw_height = max(0.5, raw_ceil_y - raw_floor_y)

    # If raw height is unreasonably large (capture units, not metres),
    # estimate a scale factor
    if raw_height > 6.0:
        estimated_scale = float(config.ASSUMED_ROOM_HEIGHT_M / raw_height)
    else:
        estimated_scale = 1.0

    if progress_callback:
        progress_callback(100, "Scene analysis complete.")

    return SceneAnalysis({
        "count": count,
        "filepath": splat_data.filepath,

        # Spatial
        "bbox_min": bbox_min.tolist(),
        "bbox_max": bbox_max.tolist(),
        "bbox_size": bbox_size.tolist(),
        "center": center.tolist(),
        "radius_50": radius_50,
        "radius_90": radius_90,
        "radius_95": radius_95,
        "radius_99": radius_99,

        # Height distribution
        **y_percentiles,
        "raw_floor_y": raw_floor_y,
        "raw_ceil_y": raw_ceil_y,
        "raw_height": raw_height,

        # Scale
        "estimated_scale": estimated_scale,
        "mean_scale": mean_scale.tolist(),
        "median_scale": median_scale.tolist(),
        "max_scale": max_scale.tolist(),

        # Radiometric
        "mean_luminance": float(np.mean(luminance)),
        "median_luminance": float(np.median(luminance)),
        "max_luminance": float(np.max(luminance)),
        "mean_opacity": float(np.mean(opacities)),
        "median_opacity": float(np.median(opacities)),
        "opacity_histogram": opacity_hist,
        "bright_splat_count": bright_count,
        "bright_threshold": bright_threshold,
        "mean_energy": float(np.mean(effective_energy)),
        "max_energy": float(np.max(effective_energy)),

        # Coordinate system
        "flip_y": flip_y,
        "scene_scale": scene_scale,
    })
