# -*- coding: utf-8 -*-
"""
SPLATFORGE — Gaussian Splat → VFX-Ready Reconstructed Environment

Converts a Gaussian Splatting capture of a real environment into a usable
VFX scene consisting of reconstructed room geometry, reconstructed props,
textures derived from the Gaussian splat, extracted lighting/environment
information, all organized as USD in Solaris and rendered/relit with Karma.

Part of the DomeBreaker / HDRI Match Solaris suite.
"""

__version__ = "0.1.0"
__author__ = "DomeBreaker"
__houdini_min__ = "20.5"

import sys
import importlib

# ---------------------------------------------------------------------------
# Feature detection — optional dependencies
# ---------------------------------------------------------------------------

def _check_module(name):
    """Check if a Python module is importable without side effects."""
    try:
        importlib.import_module(name)
        return True
    except ImportError:
        return False


HAS_OPEN3D = _check_module("open3d")
HAS_SKLEARN = _check_module("sklearn")
HAS_SCIPY = _check_module("scipy")
HAS_CV2 = _check_module("cv2")
HAS_HOU = _check_module("hou")


def feature_summary():
    """Return a dict summarising available optional features."""
    return {
        "open3d": HAS_OPEN3D,
        "sklearn": HAS_SKLEARN,
        "scipy": HAS_SCIPY,
        "opencv": HAS_CV2,
        "houdini": HAS_HOU,
        "reconstruction_backend": "open3d" if HAS_OPEN3D else "vdb_native",
        "segmentation_backend": "dbscan" if HAS_SKLEARN else "voxel_bfs",
    }


def print_feature_summary():
    """Print a human-readable feature availability summary."""
    features = feature_summary()
    print("\n=== SPLATFORGE Feature Summary ===")
    for key, val in features.items():
        status = "✓" if val is True else ("✗" if val is False else val)
        print(f"  {key:30s} {status}")
    print("=" * 38 + "\n")
