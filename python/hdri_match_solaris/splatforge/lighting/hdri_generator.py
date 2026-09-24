# -*- coding: utf-8 -*-
"""
SPLATFORGE HDRI Generator — Equirectangular Environment Map Baking.

Wraps the existing ``GaussianSplatBaker.bake_equirectangular()`` behind the
SPLATFORGE pipeline API, adding cache integration and automatic probe
position selection.
"""

import os
import numpy as np

from hdri_match_solaris.gaussian_splat import GaussianSplatBaker
from hdri_match_solaris.splatforge import config
from hdri_match_solaris.splatforge.io.cache_manager import CacheManager


def generate_hdri(splat_data, camera_pos=None, output_path=None,
                  width=None, height=None,
                  flip_y=True, scene_scale=1.0,
                  use_cache=True, progress_callback=None):
    """Bake an equirectangular HDRI from the Gaussian Splat scene.

    If ``camera_pos`` is not specified, uses the scene center with a
    slight Y offset (typical capture height).

    Args:
        splat_data:         ``SplatData`` instance.
        camera_pos:         (x, y, z) bake origin in Houdini space.
                            If None, auto-computed from scene center.
        output_path:        Path for the output EXR. If None, uses cache.
        width:              HDRI width in pixels (default from config).
        height:             HDRI height in pixels (default from config).
        flip_y:             Coordinate system conversion flag.
        scene_scale:        Scene unit multiplier.
        use_cache:          Check for cached HDRI before re-baking.
        progress_callback:  Optional ``callable(percent, message)``.

    Returns:
        dict with keys ``'hdri_path'``, ``'camera_pos'``, ``'resolution'``.
    """
    if width is None:
        width = config.HDRI_WIDTH
    if height is None:
        height = config.HDRI_HEIGHT

    # Determine output path
    cache = CacheManager.for_ply(splat_data.filepath)
    if output_path is None:
        res_tag = f"{width}x{height}"
        res_path = cache.get_path("textures", f"hdri_dome_{res_tag}", ext="exr")
        legacy_path = cache.get_path("textures", "hdri_dome", ext="exr")
        if os.path.isfile(res_path):
            output_path = res_path
        elif os.path.isfile(legacy_path) and (width, height) == (config.HDRI_WIDTH, config.HDRI_HEIGHT):
            output_path = legacy_path
        else:
            output_path = res_path
    output_path = output_path.replace("\\", "/")

    # Check cache
    if use_cache and os.path.isfile(output_path):
        if progress_callback:
            progress_callback(100, f"HDRI loaded from cache ({width}x{height}).")
        # Load from cache so downstream hotspot analyzer has access to raw pixels
        cached_arr = None
        try:
            cached_arr = GaussianSplatBaker.load_exr(output_path)
        except Exception:
            pass
        return {
            "hdri_path": output_path,
            "camera_pos": camera_pos,
            "probe_pos": camera_pos,
            "resolution": (width, height),
            "hdri_array": cached_arr,
            "cached": True,
        }

    # Auto-compute camera position
    if camera_pos is None:
        center = splat_data.center.copy()
        if flip_y:
            center[1] *= -1.0
            center[2] *= -1.0
        if abs(scene_scale - 1.0) > 1e-5:
            center *= scene_scale
        # Offset Y slightly upward (typical eye height)
        camera_pos = (float(center[0]), float(center[1]) + 0.15, float(center[2]))

    if progress_callback:
        progress_callback(5, f"Baking HDRI from ({camera_pos[0]:.2f}, "
                             f"{camera_pos[1]:.2f}, {camera_pos[2]:.2f})...")

    # Bake
    scene = splat_data.scene
    hdri_array = GaussianSplatBaker.bake_equirectangular(
        scene=scene,
        camera_pos=camera_pos,
        width=width,
        height=height,
        min_dist=config.HDRI_MIN_DIST,
        max_dist=config.HDRI_MAX_DIST,
        flip_y=flip_y,
        progress_callback=progress_callback,
    )

    if hdri_array is None:
        raise RuntimeError("HDRI baking returned None.")

    # Ensure output directory exists
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    # Save EXR
    if progress_callback:
        progress_callback(90, "Saving HDRI EXR...")

    GaussianSplatBaker.save_exr(hdri_array, output_path)

    if progress_callback:
        progress_callback(100, f"HDRI saved: {os.path.basename(output_path)}")

    return {
        "hdri_path": output_path,
        "camera_pos": camera_pos,
        "probe_pos": camera_pos,
        "resolution": (width, height),
        "hdri_array": hdri_array,
        "cached": False,
    }
