# -*- coding: utf-8 -*-
"""
splat_prop_reconstructor.py — Advanced 3D Prop Reconstruction from Gaussian Splats.

Provides two specialized reconstruction pipelines for converting raw Gaussian Splats
into clean, production-ready 3D polygonal geometry for Houdini Solaris:

1. Top-Down Depth Heightfield:
   - Perfect for horizontal props: reception counters, tables, desks, benches, platforms.
   - Projects 2D top-down elevation Y(X, Z), preserves items sitting on surfaces,
     drops watertight vertical skirts down to the detected floor, and builds base geometry.

2. OpenVDB Volumetric Mesh:
   - Perfect for organic and complex 3D props: trees, potted plants, lamps, chairs, fixtures.
   - Converts Gaussian splats to Signed Distance Fields (SDF) via OpenVDB, fuses overlapping
     footprints, smooths noise, and converts to optimized adaptive polygons with vertex normals.
"""

import os
import sys
import math
from collections import deque
import numpy as np

try:
    import hou
except ImportError:
    hou = None


def cluster_interior_splats(
    pts,
    floor_y=0.0,
    ceil_y=2.9,
    room_bounds=None,
    vox_size=0.15,
    min_splats=500,
    margin_wall=0.15,
):
    """
    Cluster interior splats into discrete prop clusters using fast 3D voxel BFS.

    Args:
        pts: (N, 3) float32 coordinates in Houdini world space.
        floor_y: Room floor height in meters.
        ceil_y: Room ceiling height in meters.
        room_bounds: (x_min, x_max, z_min, z_max) tuple or None.
        vox_size: Voxel grid cell size in meters (default 0.15m).
        min_splats: Minimum splats to qualify as a distinct prop cluster.
        margin_wall: Distance from walls to exclude architectural walls.

    Returns:
        List of cluster dicts:
        [
            {
                "id": int,
                "name": str,
                "count": int,
                "center": (x, y, z),
                "size": (sx, sy, sz),
                "bounds": (min_xyz, max_xyz),
                "points": (M, 3) float32,
                "suggested_method": 'heightfield' or 'vdb',
            }, ...
        ]
    """
    if len(pts) == 0:
        return []

    # 1. Filter out floor, ceiling, and wall boundary points
    mask = (pts[:, 1] >= floor_y + 0.06) & (pts[:, 1] <= ceil_y - 0.12)
    if room_bounds is not None and len(room_bounds) >= 4:
        x_min, x_max, z_min, z_max = room_bounds
        mask &= (
            (pts[:, 0] >= x_min + margin_wall) & (pts[:, 0] <= x_max - margin_wall) &
            (pts[:, 2] >= z_min + margin_wall) & (pts[:, 2] <= z_max - margin_wall)
        )

    p_sub = pts[mask]
    if len(p_sub) < min_splats:
        return []

    # 2. Voxel quantization
    v_min = np.min(p_sub, axis=0)
    v_idx = np.floor((p_sub - v_min) / vox_size).astype(np.int32)

    vox_to_pts = {}
    for i, coord in enumerate(v_idx):
        c_tuple = (int(coord[0]), int(coord[1]), int(coord[2]))
        if c_tuple not in vox_to_pts:
            vox_to_pts[c_tuple] = []
        vox_to_pts[c_tuple].append(i)

    # Filter out noisy, sparse voxels (< 5 points)
    dense_vox = {k: v for k, v in vox_to_pts.items() if len(v) >= 5}
    if not dense_vox:
        return []

    # 3. 26-connectivity BFS connected components
    unvisited = set(dense_vox.keys())
    neighbors = []
    for dx in [-1, 0, 1]:
        for dy in [-1, 0, 1]:
            for dz in [-1, 0, 1]:
                if dx == 0 and dy == 0 and dz == 0:
                    continue
                neighbors.append((dx, dy, dz))

    clusters = []
    c_counter = 1

    while unvisited:
        start = unvisited.pop()
        current_vox = [start]
        queue = deque([start])
        while queue:
            curr = queue.popleft()
            for dx, dy, dz in neighbors:
                nb = (curr[0] + dx, curr[1] + dy, curr[2] + dz)
                if nb in unvisited:
                    unvisited.remove(nb)
                    queue.append(nb)
                    current_vox.append(nb)

        c_pt_indices = []
        for vox in current_vox:
            c_pt_indices.extend(dense_vox[vox])

        if len(c_pt_indices) >= min_splats:
            c_pts = p_sub[c_pt_indices]
            b_min = c_pts.min(axis=0)
            b_max = c_pts.max(axis=0)
            size = b_max - b_min
            center = (b_min + b_max) * 0.5

            # Heuristic for suggested reconstruction method:
            # If horizontal aspect ratio is high and height is table-height (< 1.4m), suggest heightfield.
            # If tall / organic (height > 1.2m, tree-like or thin), suggest OpenVDB.
            aspect_horizontal = max(size[0], size[2]) / max(1e-4, size[1])
            if size[1] <= 1.25 and aspect_horizontal >= 1.5:
                method = "heightfield"
                name = f"table_cluster_{c_counter}"
            elif size[1] >= 1.2:
                method = "vdb"
                name = f"tree_fixture_{c_counter}"
            else:
                method = "vdb"
                name = f"prop_cluster_{c_counter}"

            clusters.append({
                "id": c_counter,
                "name": name,
                "count": len(c_pt_indices),
                "center": (float(center[0]), float(center[1]), float(center[2])),
                "size": (float(size[0]), float(size[1]), float(size[2])),
                "bounds": (b_min, b_max),
                "points": c_pts,
                "suggested_method": method,
            })
            c_counter += 1

    # Sort largest cluster first
    clusters.sort(key=lambda x: x["count"], reverse=True)
    return clusters


def reconstruct_heightfield_prop(
    points,
    floor_y=0.0,
    grid_res=0.03,
    skirt_to_floor=True,
    output_path=None,
):
    """
    Reconstruct a watertight solid 3D prop using Top-Down Depth Heightfield.

    Ideal for tables, counters, desks with items/clutter on them.

    Args:
        points: (N, 3) float32 coordinates of the prop cluster.
        floor_y: Detected room floor elevation.
        grid_res: Horizontal sampling resolution in meters (default 0.03m).
        skirt_to_floor: Drop vertical side walls all the way down to floor_y.
        output_path: Destination .bgeo.sc or .usd file path.

    Returns:
        output_path if successful, or None.
    """
    if len(points) == 0:
        return None

    min_x, max_x = float(points[:, 0].min()), float(points[:, 0].max())
    min_z, max_z = float(points[:, 2].min()), float(points[:, 2].max())
    min_y = float(points[:, 1].min())

    base_y = float(floor_y) if skirt_to_floor else max(floor_y, min_y - 0.05)

    nx = int(np.ceil((max_x - min_x) / grid_res)) + 1
    nz = int(np.ceil((max_z - min_z) / grid_res)) + 1

    nx = max(2, min(nx, 300))
    nz = max(2, min(nz, 300))

    height_grid = np.full((nx, nz), np.nan, dtype=np.float32)
    ix = np.clip(np.floor((points[:, 0] - min_x) / grid_res).astype(np.int32), 0, nx - 1)
    iz = np.clip(np.floor((points[:, 2] - min_z) / grid_res).astype(np.int32), 0, nz - 1)

    # 1. Project maximum elevation per cell
    for p_y, x_i, z_i in zip(points[:, 1], ix, iz):
        if np.isnan(height_grid[x_i, z_i]) or p_y > height_grid[x_i, z_i]:
            height_grid[x_i, z_i] = float(p_y)

    # Infill missing cells with nearest valid elevation or median
    valid_mask = ~np.isnan(height_grid)
    if np.count_nonzero(valid_mask) == 0:
        return None

    fill_val = float(np.nanmedian(height_grid))
    height_grid = np.nan_to_num(height_grid, nan=fill_val)

    # Simple 3x3 spatial smoothing filter to eliminate noise
    smoothed_grid = height_grid.copy()
    for i in range(1, nx - 1):
        for j in range(1, nz - 1):
            smoothed_grid[i, j] = np.mean(height_grid[i-1:i+2, j-1:j+2])
    height_grid = smoothed_grid

    # 2. Build polygonal geometry
    if not hou:
        return None

    geo = hou.Geometry()

    # Create top surface points
    pt_top = {}
    pt_base = {}

    for i in range(nx):
        x_val = float(min_x + i * grid_res)
        for j in range(nz):
            z_val = float(min_z + j * grid_res)
            y_val = float(height_grid[i, j])

            p_t = geo.createPoint()
            p_t.setPosition(hou.Vector3(x_val, y_val, z_val))
            pt_top[(i, j)] = p_t

            p_b = geo.createPoint()
            p_b.setPosition(hou.Vector3(x_val, base_y, z_val))
            pt_base[(i, j)] = p_b

    # A. Top quads
    for i in range(nx - 1):
        for j in range(nz - 1):
            poly = geo.createPolygon()
            poly.addVertex(pt_top[(i, j)])
            poly.addVertex(pt_top[(i+1, j)])
            poly.addVertex(pt_top[(i+1, j+1)])
            poly.addVertex(pt_top[(i, j+1)])

    # B. Bottom base quads (reversed winding for outward normals)
    for i in range(nx - 1):
        for j in range(nz - 1):
            poly = geo.createPolygon()
            poly.addVertex(pt_base[(i, j+1)])
            poly.addVertex(pt_base[(i+1, j+1)])
            poly.addVertex(pt_base[(i+1, j)])
            poly.addVertex(pt_base[(i, j)])

    # C. Side skirt walls
    # South edge (j = 0)
    for i in range(nx - 1):
        poly = geo.createPolygon()
        poly.addVertex(pt_top[(i, 0)])
        poly.addVertex(pt_base[(i, 0)])
        poly.addVertex(pt_base[(i+1, 0)])
        poly.addVertex(pt_top[(i+1, 0)])

    # North edge (j = nz - 1)
    for i in range(nx - 1):
        poly = geo.createPolygon()
        poly.addVertex(pt_top[(i+1, nz-1)])
        poly.addVertex(pt_base[(i+1, nz-1)])
        poly.addVertex(pt_base[(i, nz-1)])
        poly.addVertex(pt_top[(i, nz-1)])

    # West edge (i = 0)
    for j in range(nz - 1):
        poly = geo.createPolygon()
        poly.addVertex(pt_top[(0, j+1)])
        poly.addVertex(pt_base[(0, j+1)])
        poly.addVertex(pt_base[(0, j)])
        poly.addVertex(pt_top[(0, j)])

    # East edge (i = nx - 1)
    for j in range(nz - 1):
        poly = geo.createPolygon()
        poly.addVertex(pt_top[(nx-1, j)])
        poly.addVertex(pt_base[(nx-1, j)])
        poly.addVertex(pt_base[(nx-1, j+1)])
        poly.addVertex(pt_top[(nx-1, j+1)])

    # 3. Add vertex normals
    geo.computeVertexNormals()

    # Determine default output path
    if not output_path:
        out_dir = "E:/PROJECTS/HDRI_MATCH_SOLARIS/scenes/props"
        os.makedirs(out_dir, exist_ok=True)
        output_path = os.path.join(out_dir, "prop_heightfield.bgeo.sc").replace("\\", "/")

    norm_path = output_path.replace("\\", "/")
    os.makedirs(os.path.dirname(norm_path), exist_ok=True)
    geo.saveToFile(norm_path)
    return norm_path


def reconstruct_vdb_prop(
    points,
    voxel_size=0.035,
    radius_scale=1.2,
    smooth_iterations=1,
    adaptivity=0.01,
    output_path=None,
):
    """
    Reconstruct an organic or complex 3D prop using Houdini's native OpenVDB SOP engine.

    Ideal for trees, plants, fixtures, organic sculptures, chairs.

    Args:
        points: (N, 3) float32 coordinates of the prop cluster.
        voxel_size: OpenVDB voxel size in meters (default 0.035m).
        radius_scale: Multiplier on splat particle radius (default 1.2x).
        smooth_iterations: Number of Gaussian/Laplacian smoothing steps (default 1).
        adaptivity: Polygon mesh adaptivity (default 0.01 for clean topology).
        output_path: Destination .bgeo.sc or .usd file path.

    Returns:
        output_path if successful, or None.
    """
    if len(points) == 0 or not hou:
        return None

    # Determine default output path
    if not output_path:
        out_dir = "E:/PROJECTS/HDRI_MATCH_SOLARIS/scenes/props"
        os.makedirs(out_dir, exist_ok=True)
        output_path = os.path.join(out_dir, "prop_vdb.bgeo.sc").replace("\\", "/")

    norm_path = output_path.replace("\\", "/")
    os.makedirs(os.path.dirname(norm_path), exist_ok=True)

    # 1. Create temporary particle geometry
    pt_geo = hou.Geometry()
    pt_geo.addAttrib(hou.attribType.Point, "pscale", float(voxel_size * 1.1))
    for p_pos in points:
        p = pt_geo.createPoint()
        p.setPosition(hou.Vector3(float(p_pos[0]), float(p_pos[1]), float(p_pos[2])))

    temp_pts_file = os.path.join(os.path.dirname(norm_path), "_temp_splat_pts.bgeo.sc").replace("\\", "/")
    pt_geo.saveToFile(temp_pts_file)

    # 2. Build temporary headless SOP network
    obj = hou.node("/obj")
    if not obj:
        return None

    sop_net = obj.createNode("geo", "_temp_vdb_builder")
    try:
        for c in sop_net.children():
            c.destroy()

        file_in = sop_net.createNode("file", "read_pts")
        file_in.parm("file").set(temp_pts_file)

        vdb_sop = sop_net.createNode("vdbfromparticles", "vdb_particles")
        vdb_sop.setInput(0, file_in)
        vdb_sop.parm("voxelsize").set(float(voxel_size))
        vdb_sop.parm("radiusscale").set(float(radius_scale))
        vdb_sop.parm("minvoxelradius").set(1.5)

        smooth_sop = sop_net.createNode("vdbsmooth", "vdb_smooth")
        smooth_sop.setInput(0, vdb_sop)
        smooth_sop.parm("iterations").set(int(smooth_iterations))

        convert_sop = sop_net.createNode("convertvdb", "vdb_to_poly")
        convert_sop.setInput(0, smooth_sop)
        convert_sop.parm("conversion").set(1)  # Convert to Polygons
        convert_sop.parm("adaptivity").set(float(adaptivity))

        normal_sop = sop_net.createNode("normal", "add_normals")
        normal_sop.setInput(0, convert_sop)

        out_sop = sop_net.createNode("rop_geometry", "save_vdb_mesh")
        out_sop.setInput(0, normal_sop)
        out_sop.parm("sopoutput").set(norm_path)
        out_sop.parm("execute").pressButton()

        return norm_path
    finally:
        sop_net.destroy()
        if os.path.exists(temp_pts_file):
            try:
                os.remove(temp_pts_file)
            except OSError:
                pass
