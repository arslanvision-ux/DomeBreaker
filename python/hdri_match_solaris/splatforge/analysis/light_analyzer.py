# -*- coding: utf-8 -*-
"""
SPLATFORGE Light Analyzer — Wraps Existing Light Cluster Extraction.

Provides a clean interface to the existing ``GaussianSplatScene``
light extraction pipeline, wrapping ``extract_rectangular_lights()``
and ``extract_high_radiance_clusters()`` behind the SPLATFORGE API.
"""

import numpy as np

from hdri_match_solaris.splatforge import config


class LightAnalysis:
    """Container for extracted lighting information."""

    def __init__(self, clusters, metadata=None, room_data=None):
        """
        Args:
            clusters:  List of GaussianLightCluster objects or dicts.
            metadata:  Optional analysis metadata dict.
            room_data: Optional RoomData for window-aware extraction.
        """
        self._clusters = list(clusters or [])
        self._metadata = metadata or {}
        self._room_data = room_data

    @property
    def clusters(self):
        return self._clusters

    @property
    def cluster_count(self):
        return len(self._clusters)

    @property
    def metadata(self):
        return self._metadata

    @property
    def room_data(self):
        return self._room_data

    def to_dict_list(self):
        """Serialise clusters to a list of dicts."""
        result = []
        for c in self._clusters:
            if hasattr(c, 'to_dict'):
                result.append(c.to_dict())
            elif isinstance(c, dict):
                result.append(c)
            else:
                # Attempt attribute access
                d = {}
                for attr in ("cluster_id", "centroid", "pos", "normal",
                             "width_axis", "height_axis", "width", "height",
                             "color", "intensity", "temperature", "bbox_min",
                             "bbox_max", "point_count"):
                    val = getattr(c, attr, None)
                    if val is not None:
                        if isinstance(val, np.ndarray):
                            d[attr] = val.tolist()
                        else:
                            d[attr] = val
                result.append(d)
        return result

    def __repr__(self):
        return f"LightAnalysis(clusters={self.cluster_count})"


def analyze_lights(splat_data, flip_y=True, scene_scale=1.0,
                   luma_thresh=None, max_lights=None, cluster_dist=None,
                   room_data=None, progress_callback=None):
    """Extract 3D light sources from the Gaussian Splat scene.

    Wraps the existing ``GaussianSplatScene.extract_rectangular_lights()``
    method to produce positioned, oriented RectLight candidates.

    Args:
        splat_data:         ``SplatData`` instance.
        flip_y:             Convert COLMAP +Y-down to Houdini +Y-up.
        scene_scale:        Scene unit multiplier.
        luma_thresh:        Optional luminance cutoff override.
        max_lights:         Maximum number of practical clusters to extract.
        cluster_dist:       Clustering distance threshold.
        room_data:          Optional ``RoomData`` for room-aware window extraction.
        progress_callback:  Optional ``callable(percent, message)``.

    Returns:
        ``LightAnalysis`` instance.
    """
    if progress_callback:
        progress_callback(5, "Extracting light sources from Gaussian Splats...")

    scene = splat_data.scene

    luma = luma_thresh if luma_thresh is not None else config.LIGHT_LUMA_THRESHOLD
    max_l = max_lights if max_lights is not None else config.LIGHT_MAX_COUNT
    dist = cluster_dist if cluster_dist is not None else config.LIGHT_CLUSTER_DISTANCE

    try:
        clusters = scene.extract_rectangular_lights(
            luma_thresh=luma,
            cluster_dist=dist,
            min_points=config.LIGHT_MIN_POINTS,
            max_lights=max_l,
            size_scale=scene_scale,
            flip_y=flip_y,
            progress_callback=progress_callback,
        )
    except Exception as e:
        print(f"[SPLATFORGE] Light extraction warning: {e}")
        clusters = []

    metadata = {
        "light_count": len(clusters),
        "luma_threshold": luma,
        "flip_y": flip_y,
        "scene_scale": scene_scale,
    }

    if progress_callback:
        progress_callback(100, f"Extracted {len(clusters)} light source(s).")

    return LightAnalysis(clusters, metadata=metadata, room_data=room_data)
