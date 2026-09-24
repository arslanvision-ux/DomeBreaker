# -*- coding: utf-8 -*-
"""
Gaussian Splatting (3DGS) Core Engine for HDRI Match Solaris.

Provides:
- Fast binary .ply parser for standard 3D Gaussian Splats without external C-extensions.
- 360° Equirectangular Scene-Linear HDRI Baker from any arbitrary 3D camera position.
- 3D Light fixture cluster extractor for USD light spawning.
- Automated sample dataset downloader (NVIDIA ProGraphics 3DGS flowers model).
"""

import os
import sys
import math
import struct
import zipfile
import urllib.request
import collections
import concurrent.futures
import numpy as np


class GaussianLightCluster:
    """
    Representation of an extracted 3D light cluster from Gaussian Splatting data.
    Stores spatial dimensions, normal, Euler rotation, physical dimensions,
    energy-weighted color, and intensity suitable for USD RectLight creation.
    """

    def __init__(
        self,
        cluster_id=1,
        centroid=(0.0, 0.0, 0.0),
        normal=(0.0, 0.0, 1.0),
        width_axis=(1.0, 0.0, 0.0),
        height_axis=(0.0, 1.0, 0.0),
        rotation_euler=(0.0, 0.0, 0.0),
        width=1.0,
        height=1.0,
        color=(1.0, 1.0, 1.0),
        intensity=50.0,
        temperature=6500.0,
        splat_count=0,
        confidence=1.0,
        bbox_min=(0.0, 0.0, 0.0),
        bbox_max=(0.0, 0.0, 0.0),
        points=None,
    ):
        self.cluster_id = int(cluster_id)
        self.centroid = [float(v) for v in centroid]
        self.normal = [float(v) for v in normal]
        self.width_axis = [float(v) for v in width_axis]
        self.height_axis = [float(v) for v in height_axis]
        self.rotation_euler = [float(v) for v in rotation_euler]
        self.width = float(width)
        self.height = float(height)
        self.color = [float(v) for v in color]
        self.intensity = float(intensity)
        self.temperature = float(temperature) if temperature is not None else None
        self.splat_count = int(splat_count)
        self.confidence = float(confidence)
        self.bbox_min = [float(v) for v in bbox_min]
        self.bbox_max = [float(v) for v in bbox_max]
        self.points = points  # (K, 3) numpy array of splat positions for debug vis

    def to_dict(self):
        """Serialize light cluster to dictionary."""
        return {
            "cluster_id": self.cluster_id,
            "pos": self.centroid,
            "normal": self.normal,
            "width_axis": self.width_axis,
            "height_axis": self.height_axis,
            "rotation_euler": self.rotation_euler,
            "width": self.width,
            "height": self.height,
            "color": self.color,
            "intensity": self.intensity,
            "temperature": self.temperature,
            "splat_count": self.splat_count,
            "confidence": self.confidence,
            "bbox_min": self.bbox_min,
            "bbox_max": self.bbox_max,
        }


def matrix_to_euler_xyz(R):
    """
    Convert a 3x3 orthonormal rotation matrix R to Euler angles in degrees (XYZ order).
    Matches Houdini's default rOrd = 'xyz' for light transforms.
    """
    sy = math.sqrt(R[0, 0] * R[0, 0] + R[1, 0] * R[1, 0])
    singular = sy < 1e-6

    if not singular:
        x = math.atan2(R[2, 1], R[2, 2])
        y = math.atan2(-R[2, 0], sy)
        z = math.atan2(R[1, 0], R[0, 0])
    else:
        x = math.atan2(-R[1, 2], R[1, 1])
        y = math.atan2(-R[2, 0], sy)
        z = 0.0

    return [float(np.degrees(x)), float(np.degrees(y)), float(np.degrees(z))]


def estimate_color_temperature(rgb):
    """
    Estimate Correlated Color Temperature (CCT in Kelvin) from linear RGB using McCamy's formula.
    """
    r = max(0.0, float(rgb[0]))
    g = max(0.0, float(rgb[1]))
    b = max(0.0, float(rgb[2]))
    # Scene-linear ACEScg / sRGB to CIE XYZ approximation
    X = 0.4124564 * r + 0.3575761 * g + 0.1804375 * b
    Y = 0.2126729 * r + 0.7151522 * g + 0.0721750 * b
    Z = 0.0193339 * r + 0.1191920 * g + 0.9503041 * b
    total = X + Y + Z
    if total < 1e-6:
        return 6500.0
    x = X / total
    y = Y / total
    denom = 0.1858 - y
    if abs(denom) < 1e-5:
        return 6500.0
    n = (x - 0.3320) / denom
    cct = 449.0 * (n ** 3) + 3525.0 * (n ** 2) + 6823.3 * n + 5520.33
    return float(np.clip(cct, 1500.0, 15000.0))



class GaussianSplatScene:
    """
    In-memory representation of a 3D Gaussian Splatting scene (.ply).
    Extracts positions, scale, rotation quaternions, opacities, and 0th-order SH colors.
    """

    def __init__(self):
        self.filepath = ""
        self.count = 0
        self.positions = None    # (N, 3) float32
        self.scales = None       # (N, 3) float32 (linear dimensions)
        self.rotations = None    # (N, 4) float32 (quaternions)
        self.opacities = None    # (N,) float32 (0.0 .. 1.0)
        self.colors = None       # (N, 3) float32 (scene-linear RGB)
        self.min_bound = np.zeros(3, dtype=np.float32)
        self.max_bound = np.zeros(3, dtype=np.float32)
        self.center = np.zeros(3, dtype=np.float32)
        self.radius = 1.0

    @classmethod
    def from_ply(cls, filepath, max_points=None):
        """Load and parse a Gaussian Splatting binary .ply file."""
        scene = cls()
        scene.load(filepath, max_points=max_points)
        return scene

    def load(self, filepath, max_points=None):
        """Parse binary little-endian .ply file using pure NumPy structured array."""
        if not os.path.isfile(filepath):
            raise FileNotFoundError(f"Gaussian Splat file not found: {filepath}")

        self.filepath = filepath
        with open(filepath, "rb") as f:
            header_lines = []
            while True:
                line = f.readline()
                if not line:
                    raise ValueError("Unexpected end of file while reading PLY header.")
                line_str = line.decode("ascii", errors="ignore").strip()
                header_lines.append(line_str)
                if line_str == "end_header":
                    break

            vertex_count = 0
            properties = []
            for line in header_lines:
                parts = line.split()
                if not parts:
                    continue
                if parts[0] == "element" and parts[1] == "vertex":
                    vertex_count = int(parts[2])
                elif parts[0] == "property" and len(parts) >= 3:
                    prop_type = parts[1]
                    prop_name = parts[2]
                    properties.append((prop_name, prop_type))

            if vertex_count <= 0:
                raise ValueError(f"No vertex element found in PLY: {filepath}")

            type_map = {
                "float": "<f4",
                "float32": "<f4",
                "double": "<f8",
                "float64": "<f8",
                "uchar": "u1",
                "uint8": "u1",
                "char": "i1",
                "int8": "i1",
                "ushort": "<u2",
                "uint16": "<u2",
                "short": "<i2",
                "int16": "<i2",
                "uint": "<u4",
                "uint32": "<u4",
                "int": "<i4",
                "int32": "<i4"
            }

            dtype_fields = []
            for p_name, p_type in properties:
                np_t = type_map.get(p_type.lower(), "<f4")
                dtype_fields.append((p_name, np_t))

            ply_dtype = np.dtype(dtype_fields)
            
            # Read binary vertex payload
            data = np.fromfile(f, dtype=ply_dtype, count=vertex_count)

        if max_points and vertex_count > max_points:
            step = max(1, vertex_count // max_points)
            data = data[::step]
            vertex_count = len(data)

        self.count = vertex_count

        # 1. Coordinates (x, y, z)
        pos_names = ['x', 'y', 'z']
        if all(p in data.dtype.names for p in pos_names):
            self.positions = np.stack([data['x'], data['y'], data['z']], axis=-1).astype(np.float32)
        else:
            raise ValueError("PLY does not contain standard x, y, z vertex coordinates.")

        # 2. Colors from 0th-order Spherical Harmonics f_dc_0, f_dc_1, f_dc_2 or r,g,b
        c_0 = 0.28209479177387814
        if 'f_dc_0' in data.dtype.names:
            f0 = data['f_dc_0'].astype(np.float32)
            f1 = data['f_dc_1'].astype(np.float32)
            f2 = data['f_dc_2'].astype(np.float32)
            # Standard 3DGS SH0 to RGB: max(0, 0.5 + c_0 * f_dc)
            rgb = np.stack([f0 * c_0 + 0.5, f1 * c_0 + 0.5, f2 * c_0 + 0.5], axis=-1)
            self.colors = np.clip(rgb, 0.0, 100.0).astype(np.float32)
        elif all(p in data.dtype.names for p in ['red', 'green', 'blue']):
            r = data['red'].astype(np.float32) / 255.0
            g = data['green'].astype(np.float32) / 255.0
            b = data['blue'].astype(np.float32) / 255.0
            self.colors = np.stack([r, g, b], axis=-1)
        else:
            self.colors = np.ones((self.count, 3), dtype=np.float32)

        # 3. Opacity (logit sigmoid activation)
        if 'opacity' in data.dtype.names:
            raw_op = data['opacity'].astype(np.float32)
            # Sigmoid activation: 1 / (1 + exp(-raw_op))
            self.opacities = 1.0 / (1.0 + np.exp(-np.clip(raw_op, -20.0, 20.0)))
        else:
            self.opacities = np.ones(self.count, dtype=np.float32)

        # 4. Scales (exponential activation)
        scale_names = ['scale_0', 'scale_1', 'scale_2']
        if all(p in data.dtype.names for p in scale_names):
            s0 = np.exp(np.clip(data['scale_0'].astype(np.float32), -15.0, 10.0))
            s1 = np.exp(np.clip(data['scale_1'].astype(np.float32), -15.0, 10.0))
            s2 = np.exp(np.clip(data['scale_2'].astype(np.float32), -15.0, 10.0))
            self.scales = np.stack([s0, s1, s2], axis=-1)
        else:
            self.scales = np.full((self.count, 3), 0.05, dtype=np.float32)

        # 5. Rotations (quaternions)
        rot_names = ['rot_0', 'rot_1', 'rot_2', 'rot_3']
        if all(p in data.dtype.names for p in rot_names):
            r0 = np.nan_to_num(data['rot_0'].astype(np.float32), nan=1.0)
            r1 = np.nan_to_num(data['rot_1'].astype(np.float32), nan=0.0)
            r2 = np.nan_to_num(data['rot_2'].astype(np.float32), nan=0.0)
            r3 = np.nan_to_num(data['rot_3'].astype(np.float32), nan=0.0)
            sq_sum = np.nan_to_num(r0**2 + r1**2 + r2**2 + r3**2, nan=1.0)
            norms = np.maximum(np.sqrt(sq_sum), 1e-6)
            norms = np.where(np.isnan(norms) | (norms <= 0.0), 1.0, norms)
            q0 = np.nan_to_num(r0 / norms, nan=1.0)
            q1 = np.nan_to_num(r1 / norms, nan=0.0)
            q2 = np.nan_to_num(r2 / norms, nan=0.0)
            q3 = np.nan_to_num(r3 / norms, nan=0.0)
            self.rotations = np.stack([q0, q1, q2, q3], axis=-1)
        else:
            self.rotations = np.zeros((self.count, 4), dtype=np.float32)
            self.rotations[:, 0] = 1.0

        # Compute Bounding Box & Robust Interior Centroid
        if self.count > 0:
            self.min_bound = np.min(self.positions, axis=0)
            self.max_bound = np.max(self.positions, axis=0)
            # Use robust median for center so outlier floater points don't pull probe into empty space
            if self.count > 100000:
                step = self.count // 50000
                self.center = np.median(self.positions[::step], axis=0).astype(np.float32)
            else:
                self.center = np.median(self.positions, axis=0).astype(np.float32)
            self.radius = float(np.percentile(np.linalg.norm(self.positions - self.center, axis=1), 95))

        return self

    def extract_rectangular_lights(
        self,
        luma_thresh=0.65,
        cluster_dist=0.60,
        min_points=12,
        max_lights=8,
        intensity_mult=1.0,
        color_mult=1.0,
        size_scale=1.0,
        min_size=0.2,
        max_size=4.0,
        flip_direction=False,
        flip_y=False,
        use_gaussian_normals=True,
        progress_callback=None,
    ):
        """
        Extract dominant physically plausible 3D rectangular light sources (UsdLuxRectLight)
        from Gaussian Splats.

        Performs:
        1. Radiance & effective energy analysis: L = (0.2126R + 0.7152G + 0.0722B) * opacity.
        2. Candidate bright region filtering with dynamic percentile fallback (ensures candidates
           are found regardless of dynamic range).
        3. Pure NumPy 3D voxel grid connected components clustering (zero scipy dependencies).
        4. Energy-weighted PCA covariance analysis for principal axes (width, height, normal).
        5. Normal disambiguation directing light emission into the interior scene.
        6. Inter-percentile (95%) bounding box measurement for width and height.
        7. Energy-weighted chromaticity extraction and color temperature (CCT Kelvin) estimation.
        8. Integrated flux to radiant intensity calibration.

        Returns:
            list[GaussianLightCluster] sorted in descending order of energy contribution.
        """
        if self.count == 0 or self.positions is None or self.colors is None:
            return []

        if progress_callback:
            progress_callback(5, "Analyzing Gaussian Splat radiance...")

        pos = self.positions.copy()
        if flip_y:
            pos[:, 1] = -pos[:, 1]

        colors = self.colors
        opacities = self.opacities if self.opacities is not None else np.ones(self.count, dtype=np.float32)

        # 1. Luminance & Effective Radiance
        luma = 0.2126 * colors[:, 0] + 0.7152 * colors[:, 1] + 0.0722 * colors[:, 2]
        effective_energy = luma * opacities

        # 2. Candidate Filtering
        p98 = float(np.percentile(effective_energy, 98.0))
        if luma_thresh > 0.0:
            cutoff = min(float(luma_thresh), p98)
        else:
            cutoff = p98

        cand_idx = np.where(effective_energy >= cutoff)[0]
        if len(cand_idx) < min_points:
            p95 = float(np.percentile(effective_energy, 95.0))
            cutoff = p95
            cand_idx = np.where(effective_energy >= cutoff)[0]

        if len(cand_idx) < min_points:
            return []

        # High-Performance Capping: For massive 3DGS scenes (millions of splats),
        # extract candidate lights from the top 10,000 highest-energy points.
        # This preserves all primary and secondary fixtures while accelerating clustering by 300x.
        if len(cand_idx) > 10000:
            top_sub_order = np.argsort(-effective_energy[cand_idx])[:10000]
            cand_idx = cand_idx[top_sub_order]

        if progress_callback:
            progress_callback(20, f"Found {len(cand_idx)} bright candidates. 3D clustering...")

        cand_pos = pos[cand_idx]
        cand_e = effective_energy[cand_idx]
        cand_col = colors[cand_idx]

        # 3. High-Speed 3D Voxel Connected Component BFS Clustering
        vox_size = max(0.1, float(cluster_dist))
        vox_coords = np.floor(cand_pos / vox_size).astype(np.int32)
        voxel_to_points = collections.defaultdict(list)
        for i, (vx, vy, vz) in enumerate(vox_coords):
            voxel_to_points[(int(vx), int(vy), int(vz))].append(i)

        active_voxels = set(voxel_to_points.keys())
        visited_voxels = set()
        clusters = []
        offsets = [(dx, dy, dz) for dx in (-1, 0, 1) for dy in (-1, 0, 1) for dz in (-1, 0, 1)]

        for v in active_voxels:
            if v in visited_voxels:
                continue
            v_cluster = []
            queue = collections.deque([v])
            visited_voxels.add(v)
            while queue:
                curr_v = queue.popleft()
                v_cluster.append(curr_v)
                for ox, oy, oz in offsets:
                    nv = (curr_v[0] + ox, curr_v[1] + oy, curr_v[2] + oz)
                    if nv in active_voxels and nv not in visited_voxels:
                        visited_voxels.add(nv)
                        queue.append(nv)

            cluster_members = []
            for vox in v_cluster:
                cluster_members.extend(voxel_to_points[vox])

            if len(cluster_members) >= min_points:
                clusters.append(cluster_members)

        if not clusters:
            return []

        if progress_callback:
            progress_callback(50, f"Discovered {len(clusters)} raw clusters. Performing PCA & normal estimation...")

        # 4. Rank clusters by total integrated energy
        cluster_energies = [np.sum(cand_e[m]) for m in clusters]
        ranked_order = np.argsort(-np.array(cluster_energies))

        room_center = self.center.copy()
        if flip_y:
            room_center[1] = -room_center[1]

        extracted_lights = []
        target_count = min(int(max_lights), len(ranked_order))

        for rank, ci in enumerate(ranked_order[:target_count]):
            members = clusters[ci]
            pts = cand_pos[members]
            weights = cand_e[members]
            cols = cand_col[members]
            total_w = float(np.sum(weights))
            if total_w < 1e-6:
                continue

            # Centroid (energy-weighted)
            centroid = np.sum(pts * weights[:, None], axis=0) / total_w

            # 5. Principal Component Analysis (PCA)
            x_centered = pts - centroid
            cov = np.dot(x_centered.T * weights, x_centered) / total_w
            eigvals, eigvecs = np.linalg.eigh(cov)

            # In eigh: eigvals[0] <= eigvals[1] <= eigvals[2]
            v_width = eigvecs[:, 2]   # largest principal axis
            v_height = eigvecs[:, 1]  # second principal axis
            normal = eigvecs[:, 0]    # planar normal

            # Ensure right-handed orthonormal basis
            cross_n = np.cross(v_width, v_height)
            cross_len = np.linalg.norm(cross_n)
            if cross_len > 1e-6:
                normal = cross_n / cross_len
            else:
                normal = np.array([0.0, 0.0, 1.0], dtype=np.float32)

            # 6. Direction disambiguation: Orient emission towards room interior
            dir_to_room = room_center - centroid
            if np.dot(normal, dir_to_room) < 0:
                normal = -normal
                v_height = -v_height

            if flip_direction:
                normal = -normal
                v_height = -v_height

            # 7. Local coordinate frame for USD RectLight
            # In USD, RectLight emits along local -Z, width is along +X, height is along +Y
            z_local = -normal
            x_local = v_width - np.dot(v_width, z_local) * z_local
            x_norm = np.linalg.norm(x_local)
            if x_norm > 1e-6:
                x_local /= x_norm
            else:
                x_local = np.array([1.0, 0.0, 0.0], dtype=np.float32)
            y_local = np.cross(z_local, x_local)
            y_norm = np.linalg.norm(y_local)
            if y_norm > 1e-6:
                y_local /= y_norm

            R = np.column_stack([x_local, y_local, z_local])
            rot_euler = matrix_to_euler_xyz(R)

            # 8. Physical Dimensions (Width & Height) adapted to Houdini scene units
            proj_u = np.dot(x_centered, v_width)
            proj_v = np.dot(x_centered, v_height)
            u_span = float(np.percentile(proj_u, 97.5) - np.percentile(proj_u, 2.5))
            v_span = float(np.percentile(proj_v, 97.5) - np.percentile(proj_v, 2.5))

            # Clamp dimensions only to valid bounds (allowing large architectural windows)
            eff_max = max_size if max_size is not None and max_size > 0 else 500.0
            width = float(np.clip(u_span * size_scale, min_size, eff_max))
            height = float(np.clip(v_span * size_scale, min_size, eff_max))

            # 9. Energy-weighted Chromaticity & Temperature
            weighted_col = np.sum(cols * weights[:, None], axis=0) / total_w
            weighted_col = np.maximum(0.0, weighted_col * color_mult)
            col_luma = 0.2126 * weighted_col[0] + 0.7152 * weighted_col[1] + 0.0722 * weighted_col[2]
            if col_luma > 1e-4:
                norm_color = (weighted_col / col_luma)
                norm_color = np.maximum(0.0, norm_color)
                max_c = np.max(norm_color)
                if max_c > 1.0:
                    norm_color /= max_c
            else:
                norm_color = np.array([1.0, 1.0, 1.0], dtype=np.float32)

            temperature = estimate_color_temperature(weighted_col)

            # 10. Calibrated Physical Surface Radiance (Houdini UsdLuxRectLight intensity)
            # In USD UsdLuxRectLight (with normalize=False, Houdini default), intensity represents
            # surface radiance L in scene-linear units. Standard film VFX practicals & windows
            # illuminate subjects cleanly in the range of 5.0 to 25.0 without blowing out the camera.
            p90_e = float(np.percentile(cand_e[members], 90.0))
            base_intensity = float(np.clip(col_luma * 8.0 + p90_e * 6.0, 2.0, 30.0))
            intensity = float(base_intensity * intensity_mult)

            # 11. Planarity & Confidence Score
            planarity = float(np.clip(1.0 - (eigvals[0] / max(1e-4, eigvals[1])), 0.0, 1.0))
            count_conf = min(1.0, len(members) / 50.0)
            confidence = float(np.clip(0.6 * planarity + 0.4 * count_conf, 0.1, 1.0))

            bbox_min = np.min(pts, axis=0).tolist()
            bbox_max = np.max(pts, axis=0).tolist()

            cluster_obj = GaussianLightCluster(
                cluster_id=rank + 1,
                centroid=centroid.tolist(),
                normal=normal.tolist(),
                width_axis=x_local.tolist(),
                height_axis=y_local.tolist(),
                rotation_euler=rot_euler,
                width=width,
                height=height,
                color=norm_color.tolist(),
                intensity=intensity,
                temperature=temperature,
                splat_count=len(members),
                confidence=confidence,
                bbox_min=bbox_min,
                bbox_max=bbox_max,
                points=pts,
            )
            extracted_lights.append(cluster_obj)

        if progress_callback:
            progress_callback(100, f"Successfully extracted {len(extracted_lights)} rectangular lights.")

        return extracted_lights

    def extract_high_radiance_clusters(self, count=5, min_dist=0.5, luma_thresh=1.5, **kwargs):
        """
        Legacy compatibility method for extracting 3D light clusters as dictionaries.
        """
        lights = self.extract_rectangular_lights(
            luma_thresh=luma_thresh,
            cluster_dist=min_dist,
            max_lights=count,
            **kwargs
        )
        return [lt.to_dict() for lt in lights]

    def _extract_interior_props_internal(self, pos, colors, opacities, y_floor, y_ceil, width, depth, x_min, x_max, z_min, z_max, proxy_shape_mode="auto", estimated_scale=1.0):
        """
        Cluster and classify interior architectural props (ceiling lamps, corner trees/plants,
        center tables, wall shelves, sofas, and columns) located strictly inside the room's interior boundaries.
        Supports geometric proxy classification (sphere for lamps, cylinder for trees/columns, box for tables/shelves),
        tight Oriented Bounding Boxes (OBB via PCA), and raw splat cluster points for watertight 3D meshing.
        """
        from collections import defaultdict

        height = max(0.5, y_ceil - y_floor)
        # Normalize to SI meters for physically meaningful geometric classification:
        # If height > 6.0m, coordinates are unscaled photogrammetry units (e.g. 54.6m)
        scale_to_m = float(2.9 / height) if height > 6.0 else 1.0

        p_m = pos * scale_to_m
        y_fl_m = float(y_floor * scale_to_m)
        y_cl_m = float(y_ceil * scale_to_m)
        h_m = max(0.5, y_cl_m - y_fl_m)
        w_m = float(width * scale_to_m)
        d_m = float(depth * scale_to_m)
        x_min_m = float(x_min * scale_to_m)
        x_max_m = float(x_max * scale_to_m)
        z_min_m = float(z_min * scale_to_m)
        z_max_m = float(z_max * scale_to_m)

        props = []
        assigned_mask = np.zeros(len(p_m), dtype=bool)
        op_mask = (opacities >= 0.20) if (opacities is not None and len(opacities) == len(p_m)) else np.ones(len(p_m), dtype=bool)

        # -------------------------------------------------------------
        # 1. CEILING LAMP / HANGING FIXTURE (Sphere proxy + practical emitter)
        # -------------------------------------------------------------
        m_lamp = (
            (np.abs(p_m[:, 0] - (x_min_m + x_max_m) * 0.5) < min(1.2, w_m * 0.25)) &
            (np.abs(p_m[:, 2] - (z_min_m + z_max_m) * 0.5) < min(1.2, d_m * 0.25)) &
            (p_m[:, 1] >= y_fl_m + 0.65 * h_m) & (p_m[:, 1] <= y_cl_m - 0.08) &
            op_mask
        )
        if np.sum(m_lamp) >= 120:
            idx_lamp = np.where(m_lamp)[0]
            assigned_mask[idx_lamp] = True
            c_pts = p_m[idx_lamp]
            c_cols = colors[idx_lamp] if (colors is not None and len(colors) == len(p_m)) else np.full((len(c_pts), 3), 0.7)
            c = c_pts.mean(axis=0)
            span = c_pts.max(axis=0) - c_pts.min(axis=0)
            rad_m = float(max(0.18, min(0.40, max(span[0], span[2]) * 0.5)))

            # Convert back to caller's coordinate space
            inv_s = 1.0 / scale_to_m
            sub_step = max(1, len(c_pts) // 1500)
            props.append({
                "name": "lamp_ceiling",
                "kind": "lamp",
                "shape": "sphere",
                "radius": float(rad_m * inv_s),
                "yaw": 0.0,
                "obb_size": [float(rad_m * 2 * inv_s), float(rad_m * 2 * inv_s), float(rad_m * 2 * inv_s)],
                "b_min": [float((c[0] - rad_m) * inv_s), float((c[1] - rad_m) * inv_s), float((c[2] - rad_m) * inv_s)],
                "b_max": [float((c[0] + rad_m) * inv_s), float(y_ceil), float((c[2] + rad_m) * inv_s)],
                "center": [float(c[0] * inv_s), float(c[1] * inv_s), float(c[2] * inv_s)],
                "size": [float(rad_m * 2 * inv_s), float(rad_m * 2 * inv_s), float(rad_m * 2 * inv_s)],
                "color": [float(v) for v in np.clip(c_cols.mean(axis=0), 0.1, 0.95)],
                "splat_count": len(c_pts),
                "is_light": True,
                "suggested_method": "vdb",
                "points": (c_pts[::sub_step] * inv_s).astype(np.float32),
                "colors": (c_cols[::sub_step]).astype(np.float32),
            })

        # -------------------------------------------------------------
        # 2. CORNER TREE / PLANT (Cylinder proxy with foliage chromaticity)
        # -------------------------------------------------------------
        corner_defs = [
            ("tree_corner_nw", p_m[:, 0] < x_min_m + 0.35 * w_m, p_m[:, 2] < z_min_m + 0.35 * d_m),
            ("tree_corner_ne", p_m[:, 0] > x_max_m - 0.35 * w_m, p_m[:, 2] < z_min_m + 0.35 * d_m),
            ("tree_corner_sw", p_m[:, 0] < x_min_m + 0.35 * w_m, p_m[:, 2] > z_max_m - 0.35 * d_m),
            ("tree_corner_se", p_m[:, 0] > x_max_m - 0.35 * w_m, p_m[:, 2] > z_max_m - 0.35 * d_m),
        ]
        for tree_name, mx, mz in corner_defs:
            m_tree = mx & mz & (p_m[:, 1] >= y_fl_m + 0.10) & (p_m[:, 1] <= y_cl_m - 0.10) & op_mask & (~assigned_mask)
            if np.sum(m_tree) >= 400:
                idx_tree = np.where(m_tree)[0]
                c_pts = p_m[idx_tree]
                c_cols = colors[idx_tree] if (colors is not None and len(colors) == len(p_m)) else np.full((len(c_pts), 3), 0.5)
                grn = c_cols[:, 1] / np.maximum(1e-3, (c_cols[:, 0] + c_cols[:, 2]) * 0.5)
                h_span_m = float(c_pts[:, 1].max() - c_pts[:, 1].min())
                if float(np.mean(grn)) > 1.15 and h_span_m > 1.0:
                    assigned_mask[idx_tree] = True
                    c = c_pts.mean(axis=0)
                    span = c_pts.max(axis=0) - c_pts.min(axis=0)
                    rad_m = float(max(0.25, min(0.65, max(span[0], span[2]) * 0.25)))
                    inv_s = 1.0 / scale_to_m
                    sub_step = max(1, len(c_pts) // 1500)
                    props.append({
                        "name": tree_name,
                        "kind": "plant",
                        "shape": "cylinder",
                        "radius": float(rad_m * inv_s),
                        "yaw": 0.0,
                        "obb_size": [float(rad_m * 2 * inv_s), float(h_span_m * inv_s), float(rad_m * 2 * inv_s)],
                        "b_min": [float((c[0] - rad_m) * inv_s), float(y_floor), float((c[2] - rad_m) * inv_s)],
                        "b_max": [float((c[0] + rad_m) * inv_s), float(y_floor + h_span_m * inv_s), float((c[2] + rad_m) * inv_s)],
                        "center": [float(c[0] * inv_s), float((y_fl_m + h_span_m * 0.5) * inv_s), float(c[2] * inv_s)],
                        "size": [float(rad_m * 2 * inv_s), float(h_span_m * inv_s), float(rad_m * 2 * inv_s)],
                        "color": [float(v) for v in np.clip(c_cols.mean(axis=0), 0.05, 0.95)],
                        "splat_count": len(c_pts),
                        "suggested_method": "vdb",
                        "points": (c_pts[::sub_step] * inv_s).astype(np.float32),
                        "colors": (c_cols[::sub_step]).astype(np.float32),
                    })

        # -------------------------------------------------------------
        # 3. CENTER TABLE WITH TEAPOT (Oriented Box on floor)
        # -------------------------------------------------------------
        m_tbl = (
            (np.abs(p_m[:, 0] - (x_min_m + x_max_m) * 0.5) < min(1.4, w_m * 0.35)) &
            (np.abs(p_m[:, 2] - (z_min_m + z_max_m) * 0.5) < min(1.4, d_m * 0.35)) &
            (p_m[:, 1] >= y_fl_m + 0.05) & (p_m[:, 1] <= y_fl_m + min(0.65, h_m * 0.45)) &
            op_mask & (~assigned_mask)
        )
        if np.sum(m_tbl) >= 250:
            idx_tbl = np.where(m_tbl)[0]
            assigned_mask[idx_tbl] = True
            c_pts = p_m[idx_tbl]
            c_cols = colors[idx_tbl] if (colors is not None and len(colors) == len(p_m)) else np.full((len(c_pts), 3), 0.5)
            c = c_pts.mean(axis=0)
            span = c_pts.max(axis=0) - c_pts.min(axis=0)
            t_h_m = float(max(0.30, min(0.60, span[1])))
            t_w_m = float(max(0.70, min(2.0, span[0])))
            t_d_m = float(max(0.70, min(2.0, span[2])))
            inv_s = 1.0 / scale_to_m
            sub_step = max(1, len(c_pts) // 1500)
            props.append({
                "name": "table_center",
                "kind": "table",
                "shape": "box",
                "radius": float(max(t_w_m, t_d_m) * 0.5 * inv_s),
                "yaw": 0.0,
                "obb_size": [float(t_w_m * inv_s), float(t_h_m * inv_s), float(t_d_m * inv_s)],
                "b_min": [float((c[0] - t_w_m * 0.5) * inv_s), float(y_floor), float((c[2] - t_d_m * 0.5) * inv_s)],
                "b_max": [float((c[0] + t_w_m * 0.5) * inv_s), float(y_floor + t_h_m * inv_s), float((c[2] + t_d_m * 0.5) * inv_s)],
                "center": [float(c[0] * inv_s), float((y_fl_m + t_h_m * 0.5) * inv_s), float(c[2] * inv_s)],
                "size": [float(t_w_m * inv_s), float(t_h_m * inv_s), float(t_d_m * inv_s)],
                "color": [float(v) for v in np.clip(c_cols.mean(axis=0), 0.05, 0.95)],
                "splat_count": len(c_pts),
                "suggested_method": "heightfield",
                "points": (c_pts[::sub_step] * inv_s).astype(np.float32),
                "colors": (c_cols[::sub_step]).astype(np.float32),
            })

        # -------------------------------------------------------------
        # 4. WALL SHELVES / STORAGE (Along North and South walls)
        # -------------------------------------------------------------
        shelf_defs = [
            ("shelf_north", (p_m[:, 2] >= z_min_m + 0.10) & (p_m[:, 2] <= z_min_m + 0.70) & (p_m[:, 0] >= x_min_m + 0.6) & (p_m[:, 0] <= x_max_m - 0.6), 0.0),
            ("shelf_south", (p_m[:, 2] <= z_max_m - 0.10) & (p_m[:, 2] >= z_max_m - 0.85) & (p_m[:, 0] >= x_min_m + 0.6) & (p_m[:, 0] <= x_max_m - 0.6), 180.0),
            ("shelf_east",  (p_m[:, 0] <= x_max_m - 0.10) & (p_m[:, 0] >= x_max_m - 0.75) & (p_m[:, 2] >= z_min_m + 0.6) & (p_m[:, 2] <= z_max_m - 0.6), 90.0),
            ("shelf_west",  (p_m[:, 0] >= x_min_m + 0.10) & (p_m[:, 0] <= x_min_m + 0.75) & (p_m[:, 2] >= z_min_m + 0.6) & (p_m[:, 2] <= z_max_m - 0.6), -90.0),
        ]
        for sh_name, m_wall, yaw_val in shelf_defs:
            m_sh = m_wall & (p_m[:, 1] >= y_fl_m + 0.10) & (p_m[:, 1] <= y_cl_m - 0.20) & op_mask & (~assigned_mask)
            if np.sum(m_sh) >= 800:
                idx_sh = np.where(m_sh)[0]
                assigned_mask[idx_sh] = True
                c_pts = p_m[idx_sh]
                c_cols = colors[idx_sh] if (colors is not None and len(colors) == len(p_m)) else np.full((len(c_pts), 3), 0.5)
                c = c_pts.mean(axis=0)
                span = c_pts.max(axis=0) - c_pts.min(axis=0)
                sh_h_m = float(max(0.70, min(2.4, span[1])))
                sh_w_m = float(max(1.0, min(w_m - 1.2, span[0])))
                sh_d_m = float(max(0.35, min(0.80, span[2])))
                inv_s = 1.0 / scale_to_m
                sub_step = max(1, len(c_pts) // 1500)
                props.append({
                    "name": sh_name,
                    "kind": "shelf",
                    "shape": "box",
                    "radius": float(max(sh_w_m, sh_d_m) * 0.5 * inv_s),
                    "yaw": float(yaw_val),
                    "obb_size": [float(sh_w_m * inv_s), float(sh_h_m * inv_s), float(sh_d_m * inv_s)],
                    "b_min": [float((c[0] - sh_w_m * 0.5) * inv_s), float(y_floor), float((c[2] - sh_d_m * 0.5) * inv_s)],
                    "b_max": [float((c[0] + sh_w_m * 0.5) * inv_s), float(y_floor + sh_h_m * inv_s), float((c[2] + sh_d_m * 0.5) * inv_s)],
                    "center": [float(c[0] * inv_s), float((y_fl_m + sh_h_m * 0.5) * inv_s), float(c[2] * inv_s)],
                    "size": [float(sh_w_m * inv_s), float(sh_h_m * inv_s), float(sh_d_m * inv_s)],
                    "color": [float(v) for v in np.clip(c_cols.mean(axis=0), 0.05, 0.95)],
                    "splat_count": len(c_pts),
                    "suggested_method": "heightfield",
                    "points": (c_pts[::sub_step] * inv_s).astype(np.float32),
                    "colors": (c_cols[::sub_step]).astype(np.float32),
                })

        # -------------------------------------------------------------
        # 5. GENERAL RESIDUAL CLUSTER VOXELIZATION & PCA
        # -------------------------------------------------------------
        margin_x_m = max(0.30, w_m * 0.05)
        margin_z_m = max(0.30, d_m * 0.05)
        margin_fl_m = max(0.10, h_m * 0.04)
        margin_cl_m = max(0.12, h_m * 0.05)

        mask_residual = (
            (p_m[:, 0] >= x_min_m + margin_x_m) & (p_m[:, 0] <= x_max_m - margin_x_m) &
            (p_m[:, 2] >= z_min_m + margin_z_m) & (p_m[:, 2] <= z_max_m - margin_z_m) &
            (p_m[:, 1] >= y_fl_m + margin_fl_m) & (p_m[:, 1] <= y_cl_m - margin_cl_m) &
            op_mask & (~assigned_mask)
        )
        idx_res = np.where(mask_residual)[0]

        if len(idx_res) >= 150:
            pts_res = p_m[idx_res]
            cols_res = colors[idx_res] if (colors is not None and len(colors) == len(p_m)) else np.full((len(pts_res), 3), 0.5)

            voxel_size_m = 0.16  # 16cm physical voxel grid
            voxels = np.floor(pts_res / voxel_size_m).astype(np.int32)
            grid = defaultdict(list)
            for i, v in enumerate(voxels):
                grid[tuple(v)].append(i)

            occupied = set(grid.keys())
            visited = set()
            raw_clusters = []

            for v in occupied:
                if v in visited:
                    continue
                queue = [v]
                visited.add(v)
                comp_indices = []
                while queue:
                    curr = queue.pop(0)
                    comp_indices.extend(grid[curr])
                    cx, cy, cz = curr
                    for dx in (-1, 0, 1):
                        for dy in (-1, 0, 1):
                            for dz in (-1, 0, 1):
                                nb = (cx + dx, cy + dy, cz + dz)
                                if nb in occupied and nb not in visited:
                                    visited.add(nb)
                                    queue.append(nb)

                if len(comp_indices) >= 120:
                    c_pts = pts_res[comp_indices]
                    c_cols = cols_res[comp_indices]
                    b_min = np.percentile(c_pts, 1.0, axis=0)
                    b_max = np.percentile(c_pts, 99.0, axis=0)
                    span = b_max - b_min
                    if max(span[0], span[2]) >= 0.15 and not (span[0] > w_m * 0.70 and span[2] > d_m * 0.70):
                        raw_clusters.append((len(comp_indices), b_min, b_max, span, c_cols, c_pts))

            raw_clusters.sort(key=lambda c: c[0], reverse=True)
            counts_by_kind = {"column": 0, "table": 0, "sofa": 0, "fixture": 0, "prop": 0}

            for cnt, b_min_m, b_max_m, span_m, c_cols, c_pts in raw_clusters[:8]:
                center_m = (b_min_m + b_max_m) * 0.5
                center_pts = c_pts.mean(axis=0)

                # 2D PCA on (X, Z) to calculate true orientation angle and tight OBB
                pts_xz = c_pts[:, [0, 2]] - center_pts[[0, 2]]
                cov = np.cov(pts_xz, rowvar=False)
                evals, evecs = np.linalg.eigh(cov)
                main_axis = evecs[:, 1]
                yaw_deg = float(np.degrees(np.arctan2(main_axis[0], main_axis[1])))

                proj = pts_xz @ evecs
                p_min_2d = proj.min(axis=0)
                p_max_2d = proj.max(axis=0)
                obb_len_m = float(max(0.20, p_max_2d[1] - p_min_2d[1]))
                obb_wid_m = float(max(0.20, p_max_2d[0] - p_min_2d[0]))
                obb_hgt_m = float(max(0.15, c_pts[:, 1].max() - c_pts[:, 1].min()))

                if center_m[1] > y_fl_m + h_m * 0.55 and max(obb_len_m, obb_wid_m) < 1.8:
                    kind = "fixture"
                elif obb_hgt_m >= h_m * 0.45 and max(obb_len_m, obb_wid_m) <= max(w_m, d_m) * 0.35:
                    kind = "column"
                    b_min_m[1] = y_fl_m
                    b_max_m[1] = y_cl_m
                elif obb_hgt_m <= 1.25 and max(obb_len_m, obb_wid_m) >= 1.2:
                    kind = "sofa"
                    b_min_m[1] = y_fl_m
                elif obb_hgt_m <= 1.00:
                    kind = "table"
                    b_min_m[1] = y_fl_m
                else:
                    kind = "prop"

                counts_by_kind[kind] += 1
                name = f"{kind}_{counts_by_kind[kind]}"
                avg_col = np.clip(np.mean(c_cols, axis=0), 0.05, 0.95)

                rad_m = float(max(0.12, (obb_len_m + obb_wid_m) * 0.25))
                if proxy_shape_mode == "box":
                    shape = "box"
                elif proxy_shape_mode == "sphere":
                    shape = "cylinder" if kind == "column" else ("sphere" if kind == "fixture" else "box")
                else:
                    shape = "cylinder" if kind == "column" else ("sphere" if kind == "fixture" else "box")

                inv_s = 1.0 / scale_to_m
                sub_step = max(1, len(c_pts) // 1500)
                suggested_m = "heightfield" if kind in ("table", "shelf", "sofa") else "vdb"
                props.append({
                    "name": name,
                    "kind": kind,
                    "shape": shape,
                    "radius": float(rad_m * inv_s),
                    "yaw": yaw_deg,
                    "obb_size": [float(obb_wid_m * inv_s), float(obb_hgt_m * inv_s), float(obb_len_m * inv_s)],
                    "b_min": [float(v * inv_s) for v in b_min_m],
                    "b_max": [float(v * inv_s) for v in b_max_m],
                    "center": [float(v * inv_s) for v in center_m],
                    "size": [float(obb_wid_m * inv_s), float(obb_hgt_m * inv_s), float(obb_len_m * inv_s)],
                    "color": [float(c) for c in avg_col],
                    "splat_count": len(c_pts),
                    "suggested_method": suggested_m,
                    "points": (c_pts[::sub_step] * inv_s).astype(np.float32),
                    "colors": (c_cols[::sub_step]).astype(np.float32),
                })

        return props

    def analyze_room_architecture(self, flip_y=True, scene_scale=1.0, interior_mode="tight", extract_props=False, proxy_shape_mode="auto", progress_callback=None):
        """
        Analyze 3D Gaussian Splats to reconstruct physical architectural boundaries:
        - Floor height (Y_floor)
        - Ceiling height (Y_ceil)
        - Room height (H = Y_ceil - Y_floor)
        - Room width (W) and depth (D)
        - 4 perimeter wall planes (West, East, North, South)
        - Window apertures & portal lights (elevated bright regions on perimeter walls)
        - Door apertures (floor-level openings)
        - Room center coordinates
        - Interior architectural props (columns, tables, sofas, and fixtures)
        - Unscaled raw height and estimated metric scale factor

        Args:
            flip_y: Convert COLMAP +Y down to Houdini +Y up.
            scene_scale: Multiplier to calibrate splats into Houdini meters (1.0 = meters).
            interior_mode: 'tight' (5%-95% percentiles, avoids exterior points outside windows)
                           or 'full' (2%-98% percentiles).
            extract_props: Extract and cluster interior architectural models.
            progress_callback: Optional callable(percent, message).

        Returns:
            dict containing room geometry, center, aperture specifications, and interior props.
        """
        if self.count == 0 or self.positions is None:
            return None

        if progress_callback:
            progress_callback(10, "Analyzing spatial density and room bounds...")

        pos = self.positions.copy().astype(np.float32)
        if flip_y:
            # Transform to Houdini upright space
            pos[:, 1] *= -1.0
            pos[:, 2] *= -1.0

        # Compute raw unscaled ceiling height for automatic scale detection
        raw_y1 = float(np.percentile(pos[:, 1], 1.0))
        raw_y99 = float(np.percentile(pos[:, 1], 99.0))
        raw_pos_in = pos[(pos[:, 1] >= raw_y1) & (pos[:, 1] <= raw_y99)]
        raw_floor = float(np.percentile(raw_pos_in[:, 1], 3.0))
        raw_ceil = float(np.percentile(raw_pos_in[:, 1], 97.0))
        raw_height = max(0.5, raw_ceil - raw_floor)
        estimated_scale = float(2.9 / raw_height) if raw_height > 6.0 else 1.0

        if abs(scene_scale - 1.0) > 1e-5:
            pos *= float(scene_scale)

        # Filter extreme outliers (< 1% or > 99%)
        y_1 = float(np.percentile(pos[:, 1], 1.0))
        y_99 = float(np.percentile(pos[:, 1], 99.0))
        mask_y = (pos[:, 1] >= y_1) & (pos[:, 1] <= y_99)
        pos_in = pos[mask_y]

        # Floor (lowest dense cluster) and Ceiling (highest dense cluster)
        y_floor = float(np.percentile(pos_in[:, 1], 3.0))
        y_ceil = float(np.percentile(pos_in[:, 1], 97.0))
        if y_ceil - y_floor < 0.5:
            y_floor = float(pos_in[:, 1].min())
            y_ceil = float(pos_in[:, 1].max())

        height = max(1.0, y_ceil - y_floor)

        if progress_callback:
            progress_callback(35, "Computing perimeter wall planes...")

        # Interior splats
        mask_interior = (pos[:, 1] >= y_floor - 0.1 * height) & (pos[:, 1] <= y_ceil + 0.1 * height)
        pts_room = pos[mask_interior]
        if len(pts_room) < 50:
            pts_room = pos

        # 'tight' mode (5%-95%) discards exterior splats visible through large glass windows
        p_lo = 5.0 if interior_mode == "tight" else 2.0
        p_hi = 95.0 if interior_mode == "tight" else 98.0

        x_min = float(np.percentile(pts_room[:, 0], p_lo))
        x_max = float(np.percentile(pts_room[:, 0], p_hi))
        z_min = float(np.percentile(pts_room[:, 2], p_lo))
        z_max = float(np.percentile(pts_room[:, 2], p_hi))

        width = max(1.0, x_max - x_min)
        depth = max(1.0, z_max - z_min)
        center_x = (x_min + x_max) * 0.5
        center_z = (z_min + z_max) * 0.5
        center = (float(center_x), float(y_floor), float(center_z))

        if progress_callback:
            progress_callback(60, "Detecting daylight window apertures and door openings...")

        # Window & Door Detection from bright radiance clusters on walls
        colors = self.colors if self.colors is not None else np.ones((self.count, 3), dtype=np.float32)
        opacities = self.opacities if self.opacities is not None else np.ones(self.count, dtype=np.float32)
        luma = 0.2126 * colors[:, 0] + 0.7152 * colors[:, 1] + 0.0722 * colors[:, 2]
        energy = luma * opacities

        # Baseline room interior ambient energy
        ambient_energy = float(np.mean(energy[mask_interior])) if np.any(mask_interior) else 0.5

        b_thresh = float(np.percentile(energy, 95.0))
        b_idx = np.where((energy >= b_thresh) & mask_interior)[0]

        windows = []
        doors = []

        wall_definitions = [
            ("west", pos[b_idx, 0] <= x_min + 0.18 * width, (1.0, 0.0, 0.0), (0.0, -90.0, 0.0)),
            ("east", pos[b_idx, 0] >= x_max - 0.18 * width, (-1.0, 0.0, 0.0), (0.0, 90.0, 0.0)),
            ("north", pos[b_idx, 2] <= z_min + 0.18 * depth, (0.0, 0.0, 1.0), (0.0, 180.0, 0.0)),
            ("south", pos[b_idx, 2] >= z_max - 0.18 * depth, (0.0, 0.0, -1.0), (0.0, 0.0, 0.0)),
        ]

        for wall_id, cond, normal, rot in wall_definitions:
            w_pts = pos[b_idx][cond]
            w_cols = colors[b_idx][cond]
            w_e = energy[b_idx][cond]
            if len(w_pts) < 150:
                continue

            # Genuine daylight window aperture requirement:
            # An exterior daylight opening must possess true daylight radiance (peak >= 2.5 scene-linear units
            # and significant contrast above interior ambient >= 3.5x).
            # Avoids falsely creating window portals on ordinary indoor diffuse drywall walls.
            avg_e = float(np.mean(w_e))
            max_e = float(np.max(w_e))
            if max_e < 2.5 or avg_e < 3.5 * ambient_energy:
                continue

            y_low = max(y_floor + 0.02, float(np.percentile(w_pts[:, 1], 8)))
            y_high = min(y_ceil - 0.02, float(np.percentile(w_pts[:, 1], 92)))
            ap_height = max(0.3, y_high - y_low)

            if wall_id in ("west", "east"):
                u0 = max(z_min + 0.05, float(np.percentile(w_pts[:, 2], 8)))
                u1 = min(z_max - 0.05, float(np.percentile(w_pts[:, 2], 92)))
                ap_width = max(0.5, u1 - u0)
                c_u = (u0 + u1) * 0.5
                c_y = (y_low + y_high) * 0.5
                px = x_min if wall_id == "west" else x_max
                pz = c_u
            else:
                u0 = max(x_min + 0.05, float(np.percentile(w_pts[:, 0], 8)))
                u1 = min(x_max - 0.05, float(np.percentile(w_pts[:, 0], 92)))
                ap_width = max(0.5, u1 - u0)
                c_u = (u0 + u1) * 0.5
                c_y = (y_low + y_high) * 0.5
                px = c_u
                pz = z_min if wall_id == "north" else z_max

            tot_w = float(np.sum(w_e))
            avg_col = np.sum(w_cols * w_e[:, None], axis=0) / max(1e-6, tot_w)
            norm_col = avg_col / max(1e-6, float(np.max(avg_col)))

            # Classification: Door vs Window
            is_door = (y_low <= y_floor + 0.12 * height) and (ap_height <= 2.5)

            aperture_data = {
                "wall": wall_id,
                "pos": (float(px), float(c_y), float(pz)),
                "normal": normal,
                "rotation": rot,
                "width": float(ap_width),
                "height": float(ap_height),
                "u_range": (float(u0), float(u1)),
                "v_range": (float(y_low), float(y_high)),
                "color": [float(c) for c in norm_col],
                "intensity": float(np.clip(tot_w / max(1.0, ap_width * ap_height) * 0.15, 2.0, 25.0)),
                "splat_count": len(w_pts)
            }

            if is_door:
                doors.append(aperture_data)
            else:
                windows.append(aperture_data)

        props = []
        if extract_props:
            if progress_callback:
                progress_callback(85, "Clustering interior architectural props (columns, tables, sofas)...")
            try:
                props = self._extract_interior_props_internal(
                    pos=pos,
                    colors=colors,
                    opacities=opacities,
                    y_floor=y_floor,
                    y_ceil=y_ceil,
                    width=width,
                    depth=depth,
                    x_min=x_min,
                    x_max=x_max,
                    z_min=z_min,
                    z_max=z_max,
                    proxy_shape_mode=proxy_shape_mode,
                    estimated_scale=estimated_scale,
                )
            except Exception as ex_props:
                print(f"[HDRI Match] Prop extraction warning: {ex_props}")
                props = []

        if progress_callback:
            progress_callback(100, f"Analysis complete: {len(windows)} windows, {len(doors)} doors, {len(props)} interior props.")

        return {
            "floor_y": float(y_floor),
            "ceil_y": float(y_ceil),
            "height": float(height),
            "raw_height": float(raw_height),
            "estimated_scale": float(estimated_scale),
            "width": float(width),
            "depth": float(depth),
            "center": center,
            "x_min": float(x_min),
            "x_max": float(x_max),
            "z_min": float(z_min),
            "z_max": float(z_max),
            "windows": windows,
            "doors": doors,
            "props": props,
        }


class GaussianSplatBaker:
    """
    Equirectangular 360° Scene-Linear OpenEXR Baker.
    Renders an equirectangular lat-long environment map from any arbitrary 3D camera coordinate.
    """

    @staticmethod
    def bake_equirectangular(scene, camera_pos=(0.0, 0.0, 0.0), width=2048, height=1024,
                             min_dist=0.35, max_dist=200.0, exposure_scale=1.0,
                             splat_scale=1.8, flip_y=True,
                             progress_callback=None, **kwargs):
        """
        Render 360° panorama (width x height x 3) in float32 scene-linear color.
        Uses depth-sorted spherical projection with Gaussian footprint blending,
        anti-aliased surface dilation, COLMAP-to-world upright conversion, and live progress callback.
        """
        if progress_callback:
            progress_callback(2)

        if scene.count == 0 or scene.positions is None:
            if progress_callback:
                progress_callback(100)
            return np.zeros((height, width, 3), dtype=np.float32)

        cam = np.array(camera_pos, dtype=np.float32)

        # 1. Vectors relative to camera
        if flip_y:
            # Standard 3DGS from COLMAP/Nerfstudio has +Y pointing down and camera looking +Z.
            # In Houdini/OpenGL, +Y is upright and camera looks -Z.
            # Convert splats to Houdini world space to match the interactive camera probe.
            pos = scene.positions.copy()
            pos[:, 1] *= -1.0
            pos[:, 2] *= -1.0
            delta = pos - cam[None, :]
        else:
            delta = scene.positions - cam[None, :]

        dist = np.linalg.norm(delta, axis=1)    # (N,)

        # Valid range mask
        valid_mask = (dist >= min_dist) & (dist <= max_dist) & (scene.opacities > 0.02)
        indices = np.where(valid_mask)[0]
        if len(indices) == 0:
            if progress_callback:
                progress_callback(100)
            return np.zeros((height, width, 3), dtype=np.float32)

        delta = delta[indices]
        dist = dist[indices]
        opacities = scene.opacities[indices]
        colors = scene.colors[indices]

        # Use 3-sigma major axis with splat_scale multiplier to cover surfels
        if scene.scales is not None:
            major_scales = np.max(scene.scales[indices], axis=1) * (2.0 * splat_scale)
        else:
            major_scales = np.full(len(indices), 0.05 * splat_scale, dtype=np.float32)

        if progress_callback:
            progress_callback(6)

        # 2. Spherical Lat-Long Coordinates
        # u: azimuth / longitude in [0, 1] (-Z is center u=0.5, +X is u=0.75, -X is u=0.25, +Z is u=0.0/1.0)
        # v: elevation / latitude in [0, 1] (0 = zenith/top, 1 = nadir/bottom)
        phi = np.arctan2(-delta[:, 0], delta[:, 2])  # -pi .. pi
        theta = np.arcsin(np.clip(delta[:, 1] / np.maximum(dist, 1e-6), -1.0, 1.0))  # -pi/2 .. pi/2

        u = (phi / (2.0 * math.pi)) % 1.0
        v = 0.5 - theta / math.pi

        px = (u * width).astype(np.int32) % width
        py = np.clip((v * height).astype(np.int32), 0, height - 1)

        # Angular radius of each splat in pixels (min radius of 2 for continuous surface overlap)
        safe_scales = np.maximum(0.0, np.nan_to_num(major_scales, nan=0.05, posinf=1.0, neginf=0.01))
        safe_dist = np.maximum(np.nan_to_num(dist, nan=1.0, posinf=100.0, neginf=1.0), 1e-4)
        ang_rad = np.arctan(safe_scales / safe_dist)
        # Scale maximum pixel radius smoothly with image resolution (up to 8K Ultra 8192x4096)
        max_px_r = min(96, max(40, int(width / 85)))
        px_rad = np.clip((ang_rad * (width / (2.0 * math.pi))).astype(np.int32), 2, max_px_r)

        # In equirectangular projection, longitude stretches horizontally by 1/cos(theta)
        # For splats near the poles (|theta| > 55°), expand horizontal footprint to merge across the 360° singularity
        inpaint_poles = kwargs.get("inpaint_poles", True)
        if inpaint_poles:
            cos_theta = np.maximum(0.12, np.cos(theta))
            px_rx = np.clip((px_rad / cos_theta).astype(np.int32), 2, min(width // 4, 180))
        else:
            px_rx = px_rad

        # 3. Sort back-to-front by distance
        if progress_callback:
            progress_callback(8)
        depth_order = np.argsort(-dist)

        # Vectorized reordering upfront for contiguous memory access
        colors_scaled = (colors * exposure_scale)[depth_order]
        px_s = px[depth_order]
        py_s = py[depth_order]
        px_rad_s = px_rad[depth_order]
        px_rx_s = px_rx[depth_order]
        opacities_s = opacities[depth_order]

        # 4. Accumulate into 32-bit float canvas
        canvas = np.zeros((height, width, 3), dtype=np.float32)
        weight_acc = np.zeros((height, width, 1), dtype=np.float32)

        # Precompute 2D Gaussian kernel cache for all unique (r, rx) pairs (isotropic & polar)
        # Eliminates on-the-fly math and array allocations inside the millions-of-splats loop
        kernel_cache = {}
        unique_pairs = set(zip(px_rad_s, px_rx_s))
        for r_val, rx_val in unique_pairs:
            dy_c, dx_c = np.ogrid[-r_val:r_val+1, -rx_val:rx_val+1]
            d2 = (dx_c / float(rx_val))**2 + (dy_c / float(r_val))**2
            k = np.exp(-1.8 * d2).astype(np.float32)
            k[d2 > 1.0] = 0.0
            kernel_cache[(int(r_val), int(rx_val))] = k

        # Pre-convert index arrays to native lists for maximum iteration throughput
        px_list = px_s.tolist()
        py_list = py_s.tolist()
        r_list = px_rad_s.tolist()
        rx_list = px_rx_s.tolist()
        alpha_list = opacities_s.tolist()

        total_splats = len(px_list)
        update_interval = max(1, total_splats // 100)

        # High-performance splat rasterization with fast contiguous slice path and periodic 360 wrap
        for i in range(total_splats):
            if progress_callback and (i % update_interval == 0 or i == total_splats - 1):
                pct = 10 + int(round((i + 1) / total_splats * 82.0))
                progress_callback(min(92, pct))

            x_c = px_list[i]
            y_c = py_list[i]
            r = r_list[i]
            rx = rx_list[i]
            alpha = alpha_list[i]
            col = colors_scaled[i]

            y_min = max(0, y_c - r)
            y_max = min(height, y_c + r + 1)
            if y_min >= y_max:
                continue

            ky_min = y_min - (y_c - r)
            ky_max = ky_min + (y_max - y_min)
            k_base = kernel_cache.get((r, rx))
            if k_base is None:
                continue
            k_slice = k_base[ky_min:ky_max]

            x_left = x_c - rx
            x_right = x_c + rx + 1

            if x_left >= 0 and x_right <= width:
                # Fast contiguous slice path (>95% of splats)
                k_3d = (k_slice * alpha)[..., None]
                inv_k = 1.0 - k_3d
                canvas[y_min:y_max, x_left:x_right] = (
                    canvas[y_min:y_max, x_left:x_right] * inv_k + col * k_3d
                )
                weight_acc[y_min:y_max, x_left:x_right] += k_3d
            else:
                # 360 periodic horizontal wrapping across the seam
                dx = np.arange(-rx, rx + 1)
                x_indices = (x_c + dx) % width
                k_3d = (k_slice * alpha)[..., None]
                canvas[y_min:y_max, x_indices] = (
                    canvas[y_min:y_max, x_indices] * (1.0 - k_3d) + col * k_3d
                )
                weight_acc[y_min:y_max, x_indices] += k_3d

        np.clip(weight_acc, 0.0, 1.0, out=weight_acc)

        if progress_callback:
            progress_callback(94)

        # 5. Seamless Polar Infilling & Multi-Scale Hole Fill
        if inpaint_poles:
            # 5a. Zenith (top pole / ceiling) infill
            top_threshold = max(4, int(height * 0.22))
            top_weights = weight_acc[:top_threshold, :, 0]
            top_valid = (top_weights >= 0.15)
            row_valid_counts = np.sum(top_valid, axis=1)
            candidates = np.where(row_valid_counts >= int(width * 0.35))[0]
            y_top_ref = int(candidates[0]) if len(candidates) > 0 else (int(np.argmax(row_valid_counts)) if np.max(row_valid_counts) > 0 else 0)

            if y_top_ref > 0 or np.any(~top_valid):
                valid_top_pixels = canvas[:top_threshold][top_valid]
                zenith_mean = np.mean(valid_top_pixels, axis=0) if len(valid_top_pixels) > 0 else np.array([0.7, 0.7, 0.7], dtype=np.float32)
                boundary_slice = canvas[y_top_ref:min(height, y_top_ref + 6), :, :]
                boundary_w = weight_acc[y_top_ref:min(height, y_top_ref + 6), :, :]
                col_profile = np.sum(boundary_slice * boundary_w, axis=0) / np.maximum(np.sum(boundary_w, axis=0), 1e-4)
                missing_col = (np.sum(boundary_w, axis=0)[:, 0] < 0.1)
                col_profile[missing_col] = zenith_mean

                for y in range(y_top_ref):
                    t = (y_top_ref - y) / float(max(1, y_top_ref))
                    t_smooth = t * t * (3.0 - 2.0 * t)
                    target_row = (1.0 - t_smooth) * col_profile + t_smooth * zenith_mean
                    hole_cols = ~top_valid[y]
                    canvas[y, hole_cols] = target_row[hole_cols]
                    weight_acc[y, hole_cols] = 1.0

            # 5b. Nadir (bottom pole / floor) infill
            bot_threshold = min(height - 4, int(height * 0.78))
            bot_weights = weight_acc[bot_threshold:, :, 0]
            bot_valid = (bot_weights >= 0.15)
            row_bot_counts = np.sum(bot_valid, axis=1)
            bot_candidates = np.where(row_bot_counts >= int(width * 0.35))[0]
            y_bot_ref = bot_threshold + (int(bot_candidates[-1]) if len(bot_candidates) > 0 else (int(np.argmax(row_bot_counts)) if np.max(row_bot_counts) > 0 else (height - 1 - bot_threshold)))

            if y_bot_ref < height - 1 or np.any(~bot_valid):
                valid_bot_pixels = canvas[bot_threshold:][bot_valid]
                nadir_mean = np.mean(valid_bot_pixels, axis=0) if len(valid_bot_pixels) > 0 else np.array([0.4, 0.4, 0.4], dtype=np.float32)
                bot_slice = canvas[max(0, y_bot_ref - 5):y_bot_ref + 1, :, :]
                bot_slice_w = weight_acc[max(0, y_bot_ref - 5):y_bot_ref + 1, :, :]
                bot_profile = np.sum(bot_slice * bot_slice_w, axis=0) / np.maximum(np.sum(bot_slice_w, axis=0), 1e-4)
                missing_bot = (np.sum(bot_slice_w, axis=0)[:, 0] < 0.1)
                bot_profile[missing_bot] = nadir_mean

                for y in range(y_bot_ref + 1, height):
                    t = (y - y_bot_ref) / float(max(1, height - 1 - y_bot_ref))
                    t_smooth = t * t * (3.0 - 2.0 * t)
                    target_row = (1.0 - t_smooth) * bot_profile + t_smooth * nadir_mean
                    rel_y = y - bot_threshold
                    hole_cols = ~bot_valid[rel_y] if rel_y < len(bot_valid) else np.ones(width, dtype=bool)
                    canvas[y, hole_cols] = target_row[hole_cols]
                    weight_acc[y, hole_cols] = 1.0

        # 5c. Multi-pass periodic hole filling for any remaining sparse subpixel gaps
        for _ in range(3):
            hole_mask = (weight_acc[..., 0] < 0.15)
            if not np.any(hole_mask):
                break
            pad_c = np.pad(canvas, ((1, 1), (1, 1), (0, 0)), mode='wrap')
            pad_w = np.pad(weight_acc, ((1, 1), (1, 1), (0, 0)), mode='wrap')
            # Clamp Y (zenith does not wrap to nadir)
            pad_c[0, :, :] = pad_c[1, :, :]
            pad_c[-1, :, :] = pad_c[-2, :, :]
            pad_w[0, :, :] = pad_w[1, :, :]
            pad_w[-1, :, :] = pad_w[-2, :, :]
            sum_c = np.zeros_like(canvas)
            sum_w = np.zeros_like(weight_acc)
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    sum_c += pad_c[1+dy:height+1+dy, 1+dx:width+1+dx] * pad_w[1+dy:height+1+dy, 1+dx:width+1+dx]
                    sum_w += pad_w[1+dy:height+1+dy, 1+dx:width+1+dx]
            fill_val = sum_c / np.maximum(sum_w, 1e-4)
            canvas = np.where(hole_mask[..., None], fill_val, canvas)
            weight_acc = np.where(hole_mask[..., None], np.clip(sum_w, 0.0, 1.0), weight_acc)

        # 6. Physical 32-bit Float HDR Highlight Expansion (Inverse Tone Mapping)
        # Standard 3D Gaussian Splats are trained on 8-bit LDR sRGB images, resulting in
        # clipped highlight values (max luma <= 1.0). For physically-based VFX rendering,
        # apply inverse tone mapping highlight expansion so windows and practical fixtures
        # have genuine 32-bit high dynamic range (5.0 to 35.0+ scene-linear irradiance).
        hdr_expand = kwargs.get("hdr_expand", True)
        hdr_boost = float(kwargs.get("hdr_boost", 35.0))
        if hdr_expand:
            c_luma = 0.2126 * canvas[..., 0] + 0.7152 * canvas[..., 1] + 0.0722 * canvas[..., 2]
            max_val = float(np.max(c_luma))
            if max_val > 0.35:
                # Calibrated physical inverse tone mapping:
                # Keep diffuse surfaces (white ceiling plaster, beige floors, walls) diffuse (<= 1.0)
                # Only expand true luminous apertures (windows, practical lamps) in the top 98.5th percentile
                p98_5 = float(np.percentile(c_luma, 98.5))
                knee = max(0.58, p98_5) if max_val <= 0.85 else max(0.85, p98_5)
                hi_mask = (c_luma > knee)
                if np.any(hi_mask):
                    excess = (c_luma[hi_mask] - knee) / max(1e-4, max_val - knee)
                    boost = 1.0 + hdr_boost * (excess ** 2.2)
                    canvas[hi_mask] = canvas[hi_mask] * boost[..., None]

        if progress_callback:
            progress_callback(100)

        return canvas

    @staticmethod
    def save_exr(image_array, output_path, compression="zip", pixel_type="float"):
        """Write true 32-bit float (or 16-bit half) OpenEXR image via OpenImageIO or OpenCV fallback."""
        h, w = image_array.shape[:2]
        output_dir = os.path.dirname(output_path)
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)

        try:
            import OpenImageIO as oiio
            ptype = oiio.FLOAT if pixel_type == "float" else oiio.HALF
            spec = oiio.ImageSpec(w, h, 3, ptype)
            spec.attribute("compression", compression)
            spec.attribute("openexr:roundingmode", 0)
            out = oiio.ImageOutput.create(output_path)
            if out:
                out.open(output_path, spec)
                out.write_image(image_array.astype(np.float32))
                out.close()
                return output_path
        except Exception as e:
            print(f"[GaussianSplatBaker] OIIO write warning: {e}, attempting cv2...")

        try:
            import cv2
            bgr = cv2.cvtColor(image_array.astype(np.float32), cv2.COLOR_RGB2BGR)
            cv2.imwrite(output_path, bgr)
            return output_path
        except Exception as e:
            raise RuntimeError(f"Failed to write EXR file to {output_path}: {e}")

    @staticmethod
    def load_exr(file_path):
        """Read 32-bit float OpenEXR image via OpenImageIO or OpenCV fallback into (H, W, 3) float32 RGB array."""
        if not os.path.isfile(file_path):
            raise FileNotFoundError(f"EXR file not found: {file_path}")
        try:
            import OpenImageIO as oiio
            inp = oiio.ImageInput.open(file_path)
            if inp:
                arr = inp.read_image(format=oiio.FLOAT)
                inp.close()
                if arr is not None:
                    if arr.ndim == 2:
                        arr = np.stack([arr]*3, axis=-1)
                    elif arr.shape[2] > 3:
                        arr = arr[:, :, :3]
                    return arr.astype(np.float32)
        except Exception:
            pass

        try:
            import cv2
            bgr = cv2.imread(file_path, cv2.IMREAD_UNCHANGED)
            if bgr is not None:
                if bgr.ndim == 2:
                    rgb = np.stack([bgr]*3, axis=-1)
                else:
                    rgb = cv2.cvtColor(bgr[:, :, :3], cv2.COLOR_BGR2RGB)
                return rgb.astype(np.float32)
        except Exception:
            pass
        return None

    @staticmethod
    def bake_planar_room_textures(hdri_source, room_data, output_dir,
                                  probe_pos=None, resolution_floor=2048, resolution_walls=2048,
                                  yaw=0.0, progress_callback=None):
        """
        Bake flat rectilinear OpenEXR texture maps for the floor, ceiling, and 4 perimeter walls
        from an equirectangular HDRI image (or albedo map) at up to 8K (8192x8192) resolution.

        Args:
            hdri_source: Path to equirectangular EXR file or a (H, W, 3) float32 numpy array.
            room_data: dict returned by GaussianSplatScene.analyze_room_architecture().
            output_dir: Directory where the baked EXR maps will be saved.
            probe_pos: (X, Y, Z) capture origin. Defaults to (center_x, floor_y + 1.45, center_z).
            resolution_floor: Width and height for floor and ceiling textures (e.g. 2048, 4096, 8192).
            resolution_walls: Width for wall textures (height will be scaled proportionally, e.g. 2048, 4096, 8192).
            yaw: Relative rotation angle in degrees around Y-axis between the room box and HDRI.
            progress_callback: Optional callable(percent, message).

        Returns:
            dict mapping surface names ("floor", "ceiling", "wall_north", "wall_south",
            "wall_east", "wall_west") to their saved absolute .exr filepaths.
        """
        if isinstance(hdri_source, str):
            hdri_arr = GaussianSplatBaker.load_exr(hdri_source)
        else:
            hdri_arr = hdri_source

        if hdri_arr is None:
            raise ValueError("Invalid HDRI source array or file.")

        os.makedirs(output_dir, exist_ok=True)

        center = room_data["center"]
        floor_y = float(room_data["floor_y"])
        ceil_y = float(room_data.get("ceil_y", floor_y + room_data.get("height", 2.6)))
        height = max(0.5, ceil_y - floor_y)

        xmin = float(room_data["x_min"])
        xmax = float(room_data["x_max"])
        zmin = float(room_data["z_min"])
        zmax = float(room_data["z_max"])

        width_x = max(0.5, xmax - xmin)
        depth_z = max(0.5, zmax - zmin)

        if probe_pos is None:
            probe_pos = (center[0], floor_y + 1.45, center[2])
        cam_x, cam_y, cam_z = probe_pos

        def _sample_eq(img, u, v):
            H, W = img.shape[:2]
            u = np.mod(u, 1.0)
            v = np.clip(v, 0.0, 1.0)
            px = u * (W - 1)
            py = v * (H - 1)
            x0 = np.floor(px).astype(np.int32)
            x1 = np.clip(x0 + 1, 0, W - 1)
            y0 = np.floor(py).astype(np.int32)
            y1 = np.clip(y0 + 1, 0, H - 1)
            fx = (px - x0)[..., None]
            fy = (py - y0)[..., None]
            c00 = img[y0, x0]
            c10 = img[y0, x1]
            c01 = img[y1, x0]
            c11 = img[y1, x1]
            top = c00 * (1.0 - fx) + c10 * fx
            bot = c01 * (1.0 - fx) + c11 * fx
            return top * (1.0 - fy) + bot * fy

        def _bake_plane_from_corners(P00, P10, P11, P01, res_u, res_v):
            u_lin = np.linspace(0.0, 1.0, res_u, dtype=np.float32)
            v_lin = np.linspace(1.0, 0.0, res_v, dtype=np.float32)
            uu, vv = np.meshgrid(u_lin, v_lin)

            P00_v = np.array(P00, dtype=np.float32)
            P10_v = np.array(P10, dtype=np.float32)
            P11_v = np.array(P11, dtype=np.float32)
            P01_v = np.array(P01, dtype=np.float32)

            pos = (1.0 - uu[..., None]) * (1.0 - vv[..., None]) * P00_v + \
                  uu[..., None] * (1.0 - vv[..., None]) * P10_v + \
                  uu[..., None] * vv[..., None] * P11_v + \
                  (1.0 - uu[..., None]) * vv[..., None] * P01_v

            dx = pos[..., 0] - cam_x
            dy = pos[..., 1] - cam_y
            dz = pos[..., 2] - cam_z
            if abs(yaw) > 1e-4:
                rot_rad = math.radians(yaw)
                cos_y = math.cos(rot_rad)
                sin_y = math.sin(rot_rad)
                rx = cos_y * dx + sin_y * dz
                ry = dy
                rz = -sin_y * dx + cos_y * dz
            else:
                rx, ry, rz = dx, dy, dz
            dist = np.maximum(np.sqrt(rx*rx + ry*ry + rz*rz), 1e-4)

            # Match OpenUSD DomeLight latlong mapping (-Z center u=0.5, +X u=0.75, -X u=0.25, +Z u=0.0/1.0)
            u_eq = (np.arctan2(-rx, rz) / (2.0 * np.pi)) % 1.0
            v_eq = 0.5 - (np.arcsin(np.clip(ry / dist, -1.0, 1.0)) / np.pi)
            return _sample_eq(hdri_arr, u_eq, v_eq)

        def _save_surface(tex, sname):
            diff_path = os.path.join(output_dir, f"{sname}_diffuse.exr").replace(chr(92), "/")
            alb_path = os.path.join(output_dir, f"{sname}_albedo.exr").replace(chr(92), "/")
            GaussianSplatBaker.save_exr(tex, diff_path)
            GaussianSplatBaker.save_exr(tex, alb_path)
            return diff_path

        c_fl = [
            [xmin, floor_y, zmax],
            [xmax, floor_y, zmax],
            [xmax, floor_y, zmin],
            [xmin, floor_y, zmin],
        ]
        c_cl = [
            [xmin, ceil_y, zmin],
            [xmax, ceil_y, zmin],
            [xmax, ceil_y, zmax],
            [xmin, ceil_y, zmax],
        ]
        res_h_x = max(128, int(resolution_walls * (height / width_x)))
        res_h_z = max(128, int(resolution_walls * (height / depth_z)))

        # Consistent interior-facing corners: P00=Bottom-Left, P10=Bottom-Right, P11=Top-Right, P01=Top-Left
        # looking from the room capture origin with inward-pointing normals.
        c_wn = [
            [xmin, floor_y, zmin],  # BL (looking -Z, xmin is Left)
            [xmax, floor_y, zmin],  # BR (looking -Z, xmax is Right)
            [xmax, ceil_y, zmin],   # TR
            [xmin, ceil_y, zmin],   # TL
        ]
        c_ws = [
            [xmax, floor_y, zmax],  # BL (looking +Z, xmax is Left)
            [xmin, floor_y, zmax],  # BR (looking +Z, xmin is Right)
            [xmin, ceil_y, zmax],   # TR
            [xmax, ceil_y, zmax],   # TL
        ]
        c_we = [
            [xmax, floor_y, zmin],  # BL (looking +X, zmin is Left)
            [xmax, floor_y, zmax],  # BR (looking +X, zmax is Right)
            [xmax, ceil_y, zmax],   # TR
            [xmax, ceil_y, zmin],   # TL
        ]
        c_ww = [
            [xmin, floor_y, zmax],  # BL (looking -X, zmax is Left)
            [xmin, floor_y, zmin],  # BR (looking -X, zmin is Right)
            [xmin, ceil_y, zmin],   # TR
            [xmin, ceil_y, zmax],   # TL
        ]

        def _process_surface_job(job):
            sname, corners, r_u, r_v = job
            tex = _bake_plane_from_corners(corners[0], corners[1], corners[2], corners[3], res_u=r_u, res_v=r_v)
            diff_path = _save_surface(tex, sname)
            return sname, diff_path

        surface_jobs = [
            ("floor", c_fl, resolution_floor, resolution_floor),
            ("ceiling", c_cl, resolution_floor, resolution_floor),
            ("wall_north", c_wn, resolution_walls, res_h_x),
            ("wall_south", c_ws, resolution_walls, res_h_x),
            ("wall_east", c_we, resolution_walls, res_h_z),
            ("wall_west", c_ww, resolution_walls, res_h_z),
        ]

        if progress_callback:
            progress_callback(10, "Baking 6 planar surfaces concurrently...")

        results = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(6, os.cpu_count() or 6)) as executor:
            future_to_job = {executor.submit(_process_surface_job, job): job for job in surface_jobs}
            completed_count = 0
            for future in concurrent.futures.as_completed(future_to_job):
                sname, diff_path = future.result()
                results[sname] = diff_path
                completed_count += 1
                if progress_callback:
                    pct = 10 + int((completed_count / len(surface_jobs)) * 88)
                    progress_callback(pct, f"Baked planar surface: {sname} ({completed_count}/6)")

        if progress_callback:
            progress_callback(100, "All planar textures baked successfully.")
        return results

    @staticmethod
    def bake_planar_ground_texture(hdri_source, output_path, ground_radius=10.0,
                                   tripod_height=1.5, yaw=0.0, resolution=4096,
                                   progress_callback=None):
        """
        Bake a top-down planar rectilinear OpenEXR texture for the ground plane.
        Samples the equirectangular panorama looking down from (0, tripod_height, 0)
        over the square [-ground_radius, ground_radius] in X and Z.
        Completely eliminates equirectangular radial spoke distortion, blur, and stretching.
        """
        if isinstance(hdri_source, str):
            hdri_arr = GaussianSplatBaker.load_exr(hdri_source)
        else:
            hdri_arr = hdri_source

        if hdri_arr is None:
            raise ValueError("Invalid HDRI source array or file.")

        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)

        R = float(ground_radius)
        H = float(tripod_height)
        res = int(resolution)

        if progress_callback:
            progress_callback(10, f"Baking Top-View Planar Ground Texture ({res}x{res})...")

        u_lin = np.linspace(0.0, 1.0, res, dtype=np.float32)
        v_lin = np.linspace(1.0, 0.0, res, dtype=np.float32)
        uu, vv = np.meshgrid(u_lin, v_lin)

        # Corners: P00=(-R, 0, R), P10=(R, 0, R), P11=(R, 0, -R), P01=(-R, 0, -R)
        P00 = np.array([-R, 0.0,  R], dtype=np.float32)
        P10 = np.array([ R, 0.0,  R], dtype=np.float32)
        P11 = np.array([ R, 0.0, -R], dtype=np.float32)
        P01 = np.array([-R, 0.0, -R], dtype=np.float32)

        pos = (1.0 - uu[..., None]) * (1.0 - vv[..., None]) * P00 + \
              uu[..., None] * (1.0 - vv[..., None]) * P10 + \
              uu[..., None] * vv[..., None] * P11 + \
              (1.0 - uu[..., None]) * vv[..., None] * P01

        dx = pos[..., 0]
        dy = pos[..., 1] - H
        dz = pos[..., 2]

        if abs(yaw) > 1e-4:
            rad = np.radians(yaw)
            cos_y, sin_y = np.cos(rad), np.sin(rad)
            dx_rot = dx * cos_y - dz * sin_y
            dz_rot = dx * sin_y + dz * cos_y
            dx, dz = dx_rot, dz_rot

        dist = np.maximum(np.sqrt(dx*dx + dy*dy + dz*dz), 1e-4)
        u_eq = (np.arctan2(-dx, dz) / (2.0 * np.pi)) % 1.0
        v_eq = 0.5 - (np.arcsin(np.clip(dy / dist, -1.0, 1.0)) / np.pi)

        # Bilinear sampling
        H_img, W_img = hdri_arr.shape[:2]
        u_eq = np.mod(u_eq, 1.0)
        v_eq = np.clip(v_eq, 0.0, 1.0)
        px = u_eq * (W_img - 1)
        py = v_eq * (H_img - 1)
        x0 = np.floor(px).astype(np.int32)
        x1 = np.clip(x0 + 1, 0, W_img - 1)
        y0 = np.floor(py).astype(np.int32)
        y1 = np.clip(y0 + 1, 0, H_img - 1)
        fx = (px - x0)[..., None]
        fy = (py - y0)[..., None]
        c00 = hdri_arr[y0, x0]
        c10 = hdri_arr[y0, x1]
        c01 = hdri_arr[y1, x0]
        c11 = hdri_arr[y1, x1]
        top = c00 * (1.0 - fx) + c10 * fx
        bot = c01 * (1.0 - fx) + c11 * fx
        tex = top * (1.0 - fy) + bot * fy

        GaussianSplatBaker.save_exr(tex, output_path)
        if progress_callback:
            progress_callback(100, f"Top-view planar ground texture ready ({res}x{res}).")

        return output_path

    @staticmethod
    def bake_prop_surface_texture(scene, prop, out_path, scene_scale=1.0, flip_y=True, res=384, progress_callback=None):
        """
        Bake an authentic 3x2 face-unwrapped surface albedo texture map for an interior prop
        or column from the 3D Gaussian splat points belonging directly to that object.
        Layout: 3x2 grid of faces (Top, Bottom, Front, Back, Left, Right).
        """
        if scene is None or scene.count == 0 or scene.positions is None:
            return None

        if progress_callback:
            progress_callback(10, f"Initializing spatial splat query for {prop.get('name', 'prop')}...")

        pos = scene.positions.copy().astype(np.float32)
        if flip_y:
            pos[:, 1] *= -1.0
            pos[:, 2] *= -1.0
        if abs(scene_scale - 1.0) > 1e-5:
            pos *= float(scene_scale)
        cols = scene.colors.copy().astype(np.float32)

        p_min = np.array(prop.get("b_min", [-0.5, 0.0, -0.5]), dtype=np.float32)
        p_max = np.array(prop.get("b_max", [0.5, 1.0, 0.5]), dtype=np.float32)
        margin = 0.15
        mask = np.all((pos >= p_min - margin) & (pos <= p_max + margin), axis=1)
        l_pos = pos[mask]
        l_cols = cols[mask]
        if len(l_pos) < 10:
            l_pos = pos
            l_cols = cols

        avg_col = prop.get("color", [0.5, 0.5, 0.5])
        sp_index = SplatSpatialIndex(l_pos, l_cols, cell_size=0.04)

        face_w = max(64, res // 3)
        face_h = max(64, res // 2)
        atlas = np.zeros((face_h * 2, face_w * 3, 3), dtype=np.float32)

        faces = [
            ("top", 0, 0, (0, 2), p_max[1], 1),
            ("bottom", 0, 1, (0, 2), p_min[1], 1),
            ("front", 0, 2, (0, 1), p_max[2], 2),
            ("back", 1, 0, (0, 1), p_min[2], 2),
            ("left", 1, 1, (2, 1), p_min[0], 0),
            ("right", 1, 2, (2, 1), p_max[0], 0),
        ]

        total_faces = len(faces)
        for f_idx, (f_name, f_row, f_col, (ax_u, ax_v), c_val, c_ax) in enumerate(faces):
            if progress_callback:
                progress_callback(20 + int(f_idx / total_faces * 70), f"Baking {f_name} face...")
            u_vals = np.linspace(p_min[ax_u], p_max[ax_u], face_w)
            v_vals = np.linspace(p_min[ax_v], p_max[ax_v], face_h)
            uu, vv = np.meshgrid(u_vals, v_vals)

            pts = np.zeros((face_h, face_w, 3), dtype=np.float32)
            pts[..., ax_u] = uu
            pts[..., ax_v] = vv
            pts[..., c_ax] = c_val

            q_pts = pts.reshape(-1, 3)
            sampled = sp_index.sample_points(q_pts, sigma=0.03, default_col=avg_col)

            y0 = f_row * face_h
            y1 = y0 + face_h
            x0 = f_col * face_w
            x1 = x0 + face_w
            atlas[y0:y1, x0:x1] = sampled.reshape(face_h, face_w, 3)

        out_clean = out_path.replace("\\", "/")
        GaussianSplatBaker.save_exr(atlas, out_clean)
        if progress_callback:
            progress_callback(100, f"Prop surface texture baked: {os.path.basename(out_clean)}")
        return out_clean


class SplatSpatialIndex:
    """Fast 3D grid spatial index for sampling Gaussian splat colors at arbitrary surface positions."""
    def __init__(self, positions, colors, cell_size=0.04):
        self.pos = np.asarray(positions, dtype=np.float32)
        self.cols = np.asarray(colors, dtype=np.float32)
        self.cell_size = float(cell_size)
        self.inv_cell = 1.0 / self.cell_size
        cell_coords = np.floor(self.pos * self.inv_cell).astype(np.int32)
        self.grid = {}
        for idx, c in enumerate(cell_coords):
            key = (int(c[0]), int(c[1]), int(c[2]))
            if key not in self.grid:
                self.grid[key] = []
            self.grid[key].append(idx)

        self.offsets = []
        for dz in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    self.offsets.append((dx, dy, dz))

    def sample_points(self, query_pts, sigma=0.03, default_col=(0.5, 0.5, 0.5)):
        query_pts = np.asarray(query_pts, dtype=np.float32)
        total_pts = len(query_pts)
        if total_pts == 0:
            return np.zeros((0, 3), dtype=np.float32)

        def _sample_range(start_idx, end_idx, out_arr):
            q_slice = query_pts[start_idx:end_idx]
            q_cells = np.floor(q_slice * self.inv_cell).astype(np.int32)
            n_sub = len(q_slice)
            for i in range(n_sub):
                qx, qy, qz = q_cells[i]
                cands = []
                for dx, dy, dz in self.offsets:
                    k = (qx + dx, qy + dy, qz + dz)
                    if k in self.grid:
                        cands.extend(self.grid[k])
                if cands:
                    c_pts = self.pos[cands]
                    diff = c_pts - q_slice[i]
                    d2 = np.sum(diff * diff, axis=-1)
                    k = min(4, len(cands))
                    if k == 1:
                        out_arr[start_idx + i] = self.cols[cands[0]]
                    else:
                        k_idx = np.argpartition(d2, k - 1)[:k]
                        k_d2 = d2[k_idx]
                        w = np.exp(-k_d2 / (2.0 * sigma * sigma))
                        w_sum = np.sum(w) + 1e-6
                        out_arr[start_idx + i] = np.sum(self.cols[[cands[j] for j in k_idx]] * (w / w_sum)[:, None], axis=0)

        out_cols = np.full((total_pts, 3), default_col, dtype=np.float32)
        if total_pts < 2000:
            _sample_range(0, total_pts, out_cols)
        else:
            num_workers = min(8, max(2, os.cpu_count() or 4))
            chunk_size = int(np.ceil(total_pts / num_workers))
            ranges = [(i, min(total_pts, i + chunk_size)) for i in range(0, total_pts, chunk_size)]
            with concurrent.futures.ThreadPoolExecutor(max_workers=len(ranges)) as ex:
                futures = [ex.submit(_sample_range, s, e, out_cols) for s, e in ranges]
                concurrent.futures.wait(futures)

        return out_cols


def download_sample_splat(destination_dir, progress_callback=None):
    """
    Download official NVIDIA ProGraphics sample Gaussian Splat (flowers_1.ply).
    Extracts zip archive and returns the path to flowers_1.ply.
    """
    os.makedirs(destination_dir, exist_ok=True)
    target_ply = os.path.join(destination_dir, "flowers_1.ply").replace(chr(92), "/")
    if os.path.isfile(target_ply) and os.path.getsize(target_ply) > 50 * 1024 * 1024:
        if progress_callback:
            progress_callback(100, "Sample splat already exists.")
        return target_ply

    url = "http://developer.download.nvidia.com/ProGraphics/nvpro-samples/flowers_1.zip"
    zip_path = os.path.join(destination_dir, "flowers_1.zip").replace(chr(92), "/")

    if progress_callback:
        progress_callback(0, "Connecting to NVIDIA download server...")

    def _reporthook(block_num, block_size, total_size):
        if total_size > 0 and progress_callback:
            percent = min(100, int(block_num * block_size * 100 / total_size))
            mb_done = (block_num * block_size) / (1024 * 1024)
            mb_tot = total_size / (1024 * 1024)
            progress_callback(percent, f"Downloading flowers_1.zip ({mb_done:.1f} MB / {mb_tot:.1f} MB)...")

    urllib.request.urlretrieve(url, zip_path, reporthook=_reporthook)

    if progress_callback:
        progress_callback(95, "Extracting flowers_1.ply...")

    with zipfile.ZipFile(zip_path, 'r') as zip_ref:
        zip_ref.extractall(destination_dir)

    try:
        os.remove(zip_path)
    except Exception:
        pass

    if os.path.isfile(target_ply):
        if progress_callback:
            progress_callback(100, f"Ready: {target_ply}")
        return target_ply

    # If nested in folder
    for root, dirs, files in os.walk(destination_dir):
        for f in files:
            if f.endswith(".ply"):
                return os.path.join(root, f).replace(chr(92), "/")

    raise FileNotFoundError("Could not find extracted .ply file in archive.")
