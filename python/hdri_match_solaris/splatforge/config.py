# -*- coding: utf-8 -*-
"""
SPLATFORGE global configuration and defaults.

All tunable parameters are defined here as module-level constants.
Pipeline modules import from this module rather than hardcoding values.
"""

import os

# ---------------------------------------------------------------------------
# Cache configuration
# ---------------------------------------------------------------------------

#: Default cache directory relative to the PLY file location
CACHE_DIR_NAME = "splatforge_cache"

#: Subdirectories within the cache
CACHE_SUBDIRS = (
    "analysis",
    "cameras",
    "geometry",
    "textures",
    "lighting",
    "usd",
)

# ---------------------------------------------------------------------------
# PLY loading defaults
# ---------------------------------------------------------------------------

#: Maximum points to load (None = all). Set to limit memory for huge scenes.
MAX_POINTS_DEFAULT = None

#: Minimum opacity to consider a splat valid for reconstruction
OPACITY_THRESHOLD = 0.15

#: Minimum opacity for light analysis (bright splats may have moderate opacity)
OPACITY_LIGHT_THRESHOLD = 0.05

# ---------------------------------------------------------------------------
# Room detection defaults
# ---------------------------------------------------------------------------

#: Assume Y-up in Houdini; COLMAP/Nerfstudio have Y-down
FLIP_Y_DEFAULT = True

#: Percentile mode for room boundary detection
#: 'tight' (5-95%) avoids exterior splats visible through windows
#: 'full' (2-98%) includes more of the scene
INTERIOR_MODE_DEFAULT = "tight"

#: Default room height for scale estimation (metres)
ASSUMED_ROOM_HEIGHT_M = 2.9

#: RANSAC distance threshold for plane fitting (scene units, pre-scale)
RANSAC_DISTANCE_THRESHOLD = 0.05

#: Minimum number of inliers for a valid RANSAC plane
RANSAC_MIN_INLIERS = 500

#: Maximum RANSAC iterations
RANSAC_MAX_ITERATIONS = 2000

#: Wall planarity tolerance (radians from vertical)
WALL_VERTICAL_TOLERANCE_RAD = 0.15  # ~8.6 degrees

#: Floor/ceiling planarity tolerance (radians from horizontal)
FLOOR_HORIZONTAL_TOLERANCE_RAD = 0.15

# ---------------------------------------------------------------------------
# Segmentation defaults
# ---------------------------------------------------------------------------

#: Voxel size for BFS prop clustering (scene units)
PROP_VOXEL_SIZE = 0.35

#: Minimum points to consider a cluster a valid prop
PROP_MIN_POINTS = 150

#: Maximum number of props to extract
PROP_MAX_COUNT = 32

# ---------------------------------------------------------------------------
# Reconstruction defaults
# ---------------------------------------------------------------------------

#: VDB voxel size for full-scene reconstruction (scene units)
VDB_VOXEL_SIZE_ROOM = 0.08

#: VDB voxel size for per-prop reconstruction (adaptive, this is base)
VDB_VOXEL_SIZE_PROP = 0.03

#: Poisson reconstruction depth (higher = more detail, slower)
POISSON_DEPTH = 8

#: Remesh target edge length (scene units, 0 = auto)
REMESH_TARGET_EDGE = 0.0

#: Maximum faces per prop mesh before decimation
PROP_MAX_FACES = 50000

# ---------------------------------------------------------------------------
# Texture defaults
# ---------------------------------------------------------------------------

#: Number of nearest splats to sample for vertex color projection
VERTEX_COLOR_K_NEAREST = 8

#: Gaussian sigma for spatial color blending
VERTEX_COLOR_SIGMA = 0.04

#: Default texture resolution for baked maps
TEXTURE_RESOLUTION = 2048

#: Texture format for output maps
TEXTURE_FORMAT = "exr"

# ---------------------------------------------------------------------------
# Lighting defaults
# ---------------------------------------------------------------------------

#: Luminance threshold for bright splat candidacy (percentile-based)
LIGHT_LUMA_THRESHOLD = 0.65

#: Spatial clustering distance for light sources (scene units)
LIGHT_CLUSTER_DISTANCE = 0.60

#: Minimum splats to form a valid light cluster
LIGHT_MIN_POINTS = 12

#: Maximum extracted lights
LIGHT_MAX_COUNT = 8

#: HDR highlight expansion boost factor
HDR_BOOST = 35.0

# ---------------------------------------------------------------------------
# HDRI defaults
# ---------------------------------------------------------------------------

#: Equirectangular HDRI resolution (width × height)
HDRI_WIDTH = 4096
HDRI_HEIGHT = 2048

#: Minimum distance from camera for splat inclusion
HDRI_MIN_DIST = 0.35

#: Maximum distance from camera for splat inclusion
HDRI_MAX_DIST = 200.0

# ---------------------------------------------------------------------------
# USD hierarchy paths
# ---------------------------------------------------------------------------

USD_ROOT = "/world"
USD_GSPLAT_REF = "/world/gsplat_reference"
USD_ROOM = "/world/room"
USD_ROOM_FLOOR = "/world/room/floor"
USD_ROOM_CEILING = "/world/room/ceiling"
USD_ROOM_WALLS = "/world/room/walls"
USD_ROOM_OPENINGS = "/world/room/openings"
USD_PROPS = "/world/props"
USD_MATERIALS = "/world/materials"
USD_LIGHTS = "/world/lights"
USD_LIGHTS_DOME = "/world/lights/dome_gsplat_hdri"
USD_LIGHTS_EXTRACTED = "/world/lights/extracted"
USD_LIGHTS_EMISSIVE = "/world/lights/emissive"
USD_CAMERAS = "/world/cameras"
USD_LOOKDEV = "/world/lookdev"
USD_DEBUG = "/world/debug"

# ---------------------------------------------------------------------------
# Confidence thresholds
# ---------------------------------------------------------------------------

#: Confidence levels for display/classification
CONFIDENCE_HIGH = 0.75
CONFIDENCE_MEDIUM = 0.40
CONFIDENCE_LOW = 0.15
