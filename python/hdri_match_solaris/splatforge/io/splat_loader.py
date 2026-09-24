# -*- coding: utf-8 -*-
"""
SPLATFORGE Splat Loader — Cached Gaussian Splat I/O.

Wraps the existing ``GaussianSplatScene`` PLY parser with transparent disk
caching of parsed attributes.  Subsequent loads of the same PLY file skip
the binary parsing entirely and reload from a compact ``.npz`` archive.
"""

import os
import time
import numpy as np

from hdri_match_solaris.gaussian_splat import GaussianSplatScene
from hdri_match_solaris.splatforge import config
from hdri_match_solaris.splatforge.io.cache_manager import CacheManager


class SplatData:
    """
    Immutable container for parsed Gaussian Splat data.

    Holds the core arrays (positions, colors, opacities, scales, rotations)
    plus derived metadata (bounds, center, count).  This is the primary data
    object passed through the SPLATFORGE pipeline.

    All arrays are read-only views to prevent accidental mutation of shared
    state across pipeline stages.
    """

    def __init__(self, scene, filepath=""):
        """
        Construct a SplatData from a loaded ``GaussianSplatScene``.

        Args:
            scene: A ``GaussianSplatScene`` instance with data loaded.
            filepath: Original PLY file path for provenance.
        """
        self._filepath = filepath
        self._count = int(scene.count)

        # Core arrays (make read-only copies)
        self._positions = np.array(scene.positions, dtype=np.float32, copy=True)
        self._positions.flags.writeable = False

        self._colors = np.array(scene.colors, dtype=np.float32, copy=True)
        self._colors.flags.writeable = False

        self._opacities = np.array(scene.opacities, dtype=np.float32, copy=True)
        self._opacities.flags.writeable = False

        self._scales = np.array(scene.scales, dtype=np.float32, copy=True)
        self._scales.flags.writeable = False

        self._rotations = np.array(scene.rotations, dtype=np.float32, copy=True)
        self._rotations.flags.writeable = False

        # Derived bounds
        self._min_bound = np.array(scene.min_bound, dtype=np.float32, copy=True)
        self._max_bound = np.array(scene.max_bound, dtype=np.float32, copy=True)
        self._center = np.array(scene.center, dtype=np.float32, copy=True)
        self._radius = float(scene.radius)

        # Keep reference to underlying scene for legacy API access
        self._scene = scene

    # ------------------------------------------------------------------
    # Properties (all read-only)
    # ------------------------------------------------------------------

    @property
    def filepath(self):
        """Original PLY file path."""
        return self._filepath

    @property
    def count(self):
        """Number of Gaussian splats."""
        return self._count

    @property
    def positions(self):
        """(N, 3) float32 positions in original capture coordinate system."""
        return self._positions

    @property
    def colors(self):
        """(N, 3) float32 scene-linear RGB (SH0 decoded)."""
        return self._colors

    @property
    def opacities(self):
        """(N,) float32 sigmoid-activated opacity [0..1]."""
        return self._opacities

    @property
    def scales(self):
        """(N, 3) float32 exponentially-activated scale per axis."""
        return self._scales

    @property
    def rotations(self):
        """(N, 4) float32 normalised quaternions (w, x, y, z)."""
        return self._rotations

    @property
    def min_bound(self):
        """(3,) float32 axis-aligned bounding box minimum."""
        return self._min_bound

    @property
    def max_bound(self):
        """(3,) float32 axis-aligned bounding box maximum."""
        return self._max_bound

    @property
    def center(self):
        """(3,) float32 robust median center (outlier-resistant)."""
        return self._center

    @property
    def radius(self):
        """float — 95th-percentile radius from center."""
        return self._radius

    @property
    def scene(self):
        """Access to the underlying ``GaussianSplatScene`` for legacy API."""
        return self._scene

    # ------------------------------------------------------------------
    # Convenience methods
    # ------------------------------------------------------------------

    def positions_houdini(self, flip_y=True, scene_scale=1.0):
        """Return positions transformed to Houdini coordinate space.

        Args:
            flip_y: If True, negate Y and Z to convert COLMAP +Y-down to
                    Houdini +Y-up.
            scene_scale: Multiplier for scene unit calibration.

        Returns:
            (N, 3) float32 array in Houdini world space.
        """
        pos = self._positions.copy()
        if flip_y:
            pos[:, 1] *= -1.0
            pos[:, 2] *= -1.0
        if abs(scene_scale - 1.0) > 1e-5:
            pos *= float(scene_scale)
        return pos

    def filter_by_opacity(self, min_opacity=None):
        """Return indices of splats above the opacity threshold.

        Args:
            min_opacity: Minimum opacity.  Defaults to
                         ``config.OPACITY_THRESHOLD``.

        Returns:
            1D int64 index array.
        """
        if min_opacity is None:
            min_opacity = config.OPACITY_THRESHOLD
        return np.where(self._opacities >= min_opacity)[0]

    def luminance(self):
        """Compute per-splat luminance (Rec. 709).

        Returns:
            (N,) float32 luminance array.
        """
        return (0.2126 * self._colors[:, 0] +
                0.7152 * self._colors[:, 1] +
                0.0722 * self._colors[:, 2])

    def effective_energy(self):
        """Compute per-splat effective energy (luminance × opacity).

        Returns:
            (N,) float32 energy array.
        """
        return self.luminance() * self._opacities

    def summary(self):
        """Return a human-readable summary dict."""
        return {
            "filepath": self._filepath,
            "count": self._count,
            "min_bound": self._min_bound.tolist(),
            "max_bound": self._max_bound.tolist(),
            "center": self._center.tolist(),
            "radius": self._radius,
            "mean_opacity": float(np.mean(self._opacities)),
            "median_opacity": float(np.median(self._opacities)),
            "mean_luminance": float(np.mean(self.luminance())),
            "max_luminance": float(np.max(self.luminance())),
        }

    def __repr__(self):
        return (f"SplatData(count={self._count:,}, "
                f"center=[{self._center[0]:.2f}, {self._center[1]:.2f}, {self._center[2]:.2f}], "
                f"radius={self._radius:.2f})")


# ---------------------------------------------------------------------------
# Public loader function
# ---------------------------------------------------------------------------

def load_splat(ply_path, max_points=None, use_cache=True, progress_callback=None):
    """Load a Gaussian Splat PLY file with transparent caching.

    On first load, the PLY is parsed via ``GaussianSplatScene`` and the
    extracted arrays are saved to a ``.npz`` cache file.  Subsequent loads
    restore from cache, which is typically 5-10× faster.

    Args:
        ply_path:           Absolute path to the ``.ply`` file.
        max_points:         Maximum number of points to load (None = all).
        use_cache:          If True, use the NPZ cache when available.
        progress_callback:  Optional ``callable(percent, message)``.

    Returns:
        ``SplatData`` instance.

    Raises:
        FileNotFoundError: If the PLY file does not exist.
        ValueError:        If the PLY file is malformed.
    """
    ply_path = os.path.abspath(ply_path).replace("\\", "/")
    if not os.path.isfile(ply_path):
        raise FileNotFoundError(f"Gaussian Splat PLY not found: {ply_path}")

    if max_points is None:
        max_points = config.MAX_POINTS_DEFAULT

    cache = CacheManager.for_ply(ply_path)
    cache_key = f"mp{max_points}" if max_points else "full"

    # Try cache first
    if use_cache and cache.has("analysis", "splat_parsed", ext="npz", params_key=cache_key):
        if progress_callback:
            progress_callback(10, "Loading from cache...")
        cached = cache.load_npz("analysis", "splat_parsed", params_key=cache_key)
        if cached is not None:
            scene = GaussianSplatScene()
            scene.filepath = ply_path
            scene.positions = cached["positions"]
            scene.colors = cached["colors"]
            scene.opacities = cached["opacities"]
            scene.scales = cached["scales"]
            scene.rotations = cached["rotations"]
            scene.count = len(scene.positions)
            scene.min_bound = np.min(scene.positions, axis=0)
            scene.max_bound = np.max(scene.positions, axis=0)
            if scene.count > 100000:
                step = scene.count // 50000
                scene.center = np.median(scene.positions[::step], axis=0).astype(np.float32)
            else:
                scene.center = np.median(scene.positions, axis=0).astype(np.float32)
            scene.radius = float(np.percentile(
                np.linalg.norm(scene.positions - scene.center, axis=1), 95))

            if progress_callback:
                progress_callback(100, f"Loaded {scene.count:,} splats from cache.")
            return SplatData(scene, filepath=ply_path)

    # Parse PLY
    if progress_callback:
        progress_callback(5, f"Parsing PLY: {os.path.basename(ply_path)}...")

    t0 = time.time()
    scene = GaussianSplatScene.from_ply(ply_path, max_points=max_points)
    parse_time = time.time() - t0

    if progress_callback:
        progress_callback(80, f"Parsed {scene.count:,} splats in {parse_time:.1f}s")

    # Save to cache
    if use_cache:
        if progress_callback:
            progress_callback(90, "Saving to cache...")
        try:
            cache.save_npz(
                "analysis", "splat_parsed", params_key=cache_key,
                positions=scene.positions,
                colors=scene.colors,
                opacities=scene.opacities,
                scales=scene.scales,
                rotations=scene.rotations,
            )
        except Exception as e:
            print(f"[SPLATFORGE] Cache write warning: {e}")

    if progress_callback:
        progress_callback(100, f"Ready: {scene.count:,} splats")

    return SplatData(scene, filepath=ply_path)
