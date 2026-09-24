# -*- coding: utf-8 -*-
"""
SPLATFORGE Cache Manager — Disk-Based Intermediate Result Caching.

Provides a transparent cache layer that stores expensive intermediate results
(parsed PLY data, room analysis, reconstructed geometry, textures, lighting)
to avoid redundant recomputation.

Cache is stored alongside the source PLY file under a ``splatforge_cache/``
directory by default.
"""

import os
import json
import hashlib
import time
import numpy as np

from hdri_match_solaris.splatforge import config


class CacheManager:
    """
    Manages disk-based caching for SPLATFORGE pipeline stages.

    Each cache entry is keyed by a stage name and a content hash derived from
    the input parameters that produced it.  The manager supports JSON, NumPy
    ``.npz``, and raw binary blobs.

    Args:
        base_dir: Root directory for the cache.  If ``None``, derived from the
                  PLY source path.
    """

    def __init__(self, base_dir=None):
        self._base_dir = base_dir
        if self._base_dir:
            os.makedirs(self._base_dir, exist_ok=True)

    @classmethod
    def for_ply(cls, ply_path):
        """Create a CacheManager rooted next to the given PLY file.

        The cache directory is ``<ply_dir>/splatforge_cache/``.
        """
        ply_dir = os.path.dirname(os.path.abspath(ply_path))
        cache_root = os.path.join(ply_dir, config.CACHE_DIR_NAME)
        os.makedirs(cache_root, exist_ok=True)
        for subdir in config.CACHE_SUBDIRS:
            os.makedirs(os.path.join(cache_root, subdir), exist_ok=True)
        return cls(base_dir=cache_root)

    @property
    def base_dir(self):
        return self._base_dir

    def _subdir(self, stage):
        """Return the subdirectory for a given pipeline stage."""
        for known in config.CACHE_SUBDIRS:
            if stage.startswith(known):
                path = os.path.join(self._base_dir, known)
                os.makedirs(path, exist_ok=True)
                return path
        # Fallback to base dir
        return self._base_dir

    @staticmethod
    def _hash_key(*args):
        """Compute a short hash from arbitrary arguments for cache keying."""
        h = hashlib.sha256()
        for a in args:
            h.update(str(a).encode("utf-8"))
        return h.hexdigest()[:16]

    # ------------------------------------------------------------------
    # JSON cache
    # ------------------------------------------------------------------

    def save_json(self, stage, name, data, params_key=None):
        """Save a JSON-serializable dict/list to cache.

        Args:
            stage: Pipeline stage name (e.g. ``'analysis'``).
            name:  Entry name within the stage (e.g. ``'room_data'``).
            data:  JSON-serializable object.
            params_key: Optional string to differentiate parameter variants.

        Returns:
            Absolute path to the saved file.
        """
        subdir = self._subdir(stage)
        suffix = f"_{params_key}" if params_key else ""
        filepath = os.path.join(subdir, f"{name}{suffix}.json")
        filepath = filepath.replace("\\", "/")
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, default=_json_default)
        return filepath

    def load_json(self, stage, name, params_key=None):
        """Load a cached JSON entry.  Returns ``None`` if not found."""
        subdir = self._subdir(stage)
        suffix = f"_{params_key}" if params_key else ""
        filepath = os.path.join(subdir, f"{name}{suffix}.json")
        if os.path.isfile(filepath):
            with open(filepath, "r", encoding="utf-8") as f:
                return json.load(f)
        return None

    # ------------------------------------------------------------------
    # NumPy cache
    # ------------------------------------------------------------------

    def save_npz(self, stage, name, params_key=None, **arrays):
        """Save NumPy arrays to a compressed ``.npz`` file.

        Args:
            stage:      Pipeline stage name.
            name:       Entry name.
            params_key: Optional parameter variant key.
            **arrays:   Keyword arrays to store (e.g. ``positions=arr``).

        Returns:
            Absolute path to the saved file.
        """
        subdir = self._subdir(stage)
        suffix = f"_{params_key}" if params_key else ""
        filepath = os.path.join(subdir, f"{name}{suffix}.npz")
        filepath = filepath.replace("\\", "/")
        np.savez_compressed(filepath, **arrays)
        return filepath

    def load_npz(self, stage, name, params_key=None):
        """Load a cached ``.npz`` archive.  Returns dict of arrays or ``None``."""
        subdir = self._subdir(stage)
        suffix = f"_{params_key}" if params_key else ""
        filepath = os.path.join(subdir, f"{name}{suffix}.npz")
        if os.path.isfile(filepath):
            data = np.load(filepath, allow_pickle=False)
            return dict(data)
        return None

    # ------------------------------------------------------------------
    # File existence checks
    # ------------------------------------------------------------------

    def has(self, stage, name, ext="json", params_key=None):
        """Check if a cached entry exists."""
        subdir = self._subdir(stage)
        suffix = f"_{params_key}" if params_key else ""
        filepath = os.path.join(subdir, f"{name}{suffix}.{ext}")
        return os.path.isfile(filepath)

    def get_path(self, stage, name, ext="exr", params_key=None):
        """Get the absolute path for a cache entry (may not exist yet)."""
        subdir = self._subdir(stage)
        suffix = f"_{params_key}" if params_key else ""
        filepath = os.path.join(subdir, f"{name}{suffix}.{ext}")
        return filepath.replace("\\", "/")

    def clear_stage(self, stage):
        """Remove all cached files for a given stage."""
        subdir = self._subdir(stage)
        if os.path.isdir(subdir):
            import shutil
            shutil.rmtree(subdir)
            os.makedirs(subdir, exist_ok=True)

    def clear_all(self):
        """Remove entire cache."""
        if self._base_dir and os.path.isdir(self._base_dir):
            import shutil
            shutil.rmtree(self._base_dir)
            os.makedirs(self._base_dir, exist_ok=True)
            for subdir in config.CACHE_SUBDIRS:
                os.makedirs(os.path.join(self._base_dir, subdir), exist_ok=True)


def _json_default(obj):
    """JSON serializer fallback for numpy types."""
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        return float(obj)
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")
