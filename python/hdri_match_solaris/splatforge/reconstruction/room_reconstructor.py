# -*- coding: utf-8 -*-
"""
SPLATFORGE Room Reconstructor — Clean Mesh Generation from Detected Planes.

Takes ``RoomData`` (from room_detector) and generates production-quality
polygonal geometry:

- **Floor quad** at detected Y with correct XZ extents
- **Ceiling quad** at detected Y with correct XZ extents
- **Wall quads** for each cardinal direction
- **Window/door cutouts** boolean-subtracted from walls

Output is designed for direct Houdini SOP consumption or USD export —
clean quads with proper normals, UVs, and semantic attributes.
"""

import math
import numpy as np


class RoomMeshData:
    """Container for reconstructed room shell mesh data.

    Stores vertex positions, polygon face indices, normals, UVs, and
    per-face semantic labels suitable for creating Houdini geometry or
    USD meshes.
    """

    def __init__(self):
        self.vertices = []       # List of (x, y, z) tuples
        self.faces = []          # List of [v0, v1, v2, v3] index lists (quads)
        self.normals = []        # Per-face normals
        self.uvs = []            # Per-vertex UVs (u, v)
        self.face_labels = []    # Per-face semantic label
        self.face_wall_ids = []  # Per-face wall cardinal ID (or '')

    @property
    def vertex_count(self):
        return len(self.vertices)

    @property
    def face_count(self):
        return len(self.faces)

    def add_quad(self, p0, p1, p2, p3, normal, label, wall_id="",
                 uv0=None, uv1=None, uv2=None, uv3=None):
        """Add a single quad face to the mesh.

        Args:
            p0, p1, p2, p3: Corner positions (x, y, z), counter-clockwise
                            when viewed from the front (normal direction).
            normal:         Face normal (x, y, z).
            label:          Semantic label (``'floor'``, ``'ceiling'``, ``'wall'``).
            wall_id:        Optional cardinal direction.
            uv0..uv3:       Optional UV coordinates per vertex.
        """
        base = len(self.vertices)
        self.vertices.extend([tuple(p0), tuple(p1), tuple(p2), tuple(p3)])
        self.faces.append([base, base + 1, base + 2, base + 3])
        self.normals.append(tuple(normal))
        self.face_labels.append(label)
        self.face_wall_ids.append(wall_id)

        # Default UVs: unit quad
        if uv0 is None:
            uv0, uv1, uv2, uv3 = (0, 0), (1, 0), (1, 1), (0, 1)
        self.uvs.extend([tuple(uv0), tuple(uv1), tuple(uv2), tuple(uv3)])

    def add_triangulated_quad_with_hole(self, corners, hole_corners, normal,
                                        label, wall_id=""):
        """Add a quad with a rectangular hole (window/door cutout).

        The quad is triangulated into 8 triangles surrounding the hole.
        Each triangle gets the same semantic label and wall_id.

        Args:
            corners:      4 corner positions of the outer quad [p0, p1, p2, p3].
            hole_corners: 4 corner positions of the hole [h0, h1, h2, h3].
            normal:       Face normal.
            label:        Semantic label.
            wall_id:      Cardinal direction.
        """
        # Outer corners: p0(BL), p1(BR), p2(TR), p3(TL)
        # Hole corners:  h0(BL), h1(BR), h2(TR), h3(TL)
        p0, p1, p2, p3 = corners
        h0, h1, h2, h3 = hole_corners

        base = len(self.vertices)
        self.vertices.extend([
            tuple(p0), tuple(p1), tuple(p2), tuple(p3),  # 0-3: outer
            tuple(h0), tuple(h1), tuple(h2), tuple(h3),  # 4-7: hole
        ])

        # 8 triangles forming the frame around the hole
        tris = [
            # Bottom strip
            [base + 0, base + 1, base + 5],
            [base + 0, base + 5, base + 4],
            # Right strip
            [base + 1, base + 2, base + 6],
            [base + 1, base + 6, base + 5],
            # Top strip
            [base + 2, base + 3, base + 7],
            [base + 2, base + 7, base + 6],
            # Left strip
            [base + 3, base + 0, base + 4],
            [base + 3, base + 4, base + 7],
        ]

        for tri in tris:
            self.faces.append(tri)
            self.normals.append(tuple(normal))
            self.face_labels.append(label)
            self.face_wall_ids.append(wall_id)

        # Simple UVs for the 8 vertices
        for _ in range(8):
            self.uvs.append((0.5, 0.5))

    def get_category_submeshes(self):
        """Partition faces into semantic sub-meshes ('floor', 'ceiling', 'walls').

        Guarantees that faceVertexCounts, faceVertexIndices, normals, and UVs
        strictly match 1-to-1 with zero buffer mismatches.

        Returns:
            dict mapping category name to dict with:
                'points':  list of (x, y, z) tuples for vertices used by this sub-mesh
                'counts':  list of face vertex counts
                'indices': list of local vertex indices into 'points' (sum(counts) == len(indices))
                'normals': list of face-varying normal tuples (x, y, z) (sum(counts) == len(normals))
                'uvs':     list of face-varying UV tuples (u, v) (sum(counts) == len(uvs))
        """
        categories = ["floor", "ceiling", "walls"]
        result = {}
        for cat in categories:
            local_pts = []
            v_map = {}
            cat_counts = []
            cat_indices = []
            cat_normals = []
            cat_uvs = []

            for fi, face in enumerate(self.faces):
                lbl = self.face_labels[fi]
                f_cat = "floor" if lbl == "floor" else ("ceiling" if lbl == "ceiling" else "walls")
                if f_cat != cat:
                    continue
                cat_counts.append(len(face))
                norm = self.normals[fi]
                for vi in face:
                    if vi not in v_map:
                        v_map[vi] = len(local_pts)
                        local_pts.append(self.vertices[vi])
                    cat_indices.append(v_map[vi])
                    cat_normals.append(norm)
                    uv = self.uvs[vi] if vi < len(self.uvs) else (0.5, 0.5)
                    cat_uvs.append(uv)

            if cat_counts:
                result[cat] = {
                    "points": local_pts,
                    "counts": cat_counts,
                    "indices": cat_indices,
                    "normals": cat_normals,
                    "uvs": cat_uvs,
                }
        return result

    def to_numpy(self):
        """Convert to NumPy arrays for Houdini geometry creation.

        Returns:
            dict with ``'P'``, ``'N'``, ``'uv'``, ``'point_uv'``, ``'face_counts'``,
            ``'face_indices'``, ``'labels'``, ``'wall_ids'`` arrays.
        """
        verts = np.array(self.vertices, dtype=np.float32)
        normals = []
        labels = []
        wall_ids = []
        face_counts = []
        face_indices = []
        face_uvs = []

        for i, face in enumerate(self.faces):
            face_counts.append(len(face))
            face_indices.extend(face)
            for vi in face:
                normals.append(self.normals[i])
                labels.append(self.face_labels[i])
                wall_ids.append(self.face_wall_ids[i])
                face_uvs.append(self.uvs[vi] if vi < len(self.uvs) else (0.5, 0.5))

        point_uvs = np.array(self.uvs, dtype=np.float32) if self.uvs else np.zeros((len(verts), 2), dtype=np.float32)

        return {
            "P": verts,
            "N": np.array(normals, dtype=np.float32),
            "uv": np.array(face_uvs, dtype=np.float32),
            "point_uv": point_uvs,
            "face_counts": np.array(face_counts, dtype=np.int32),
            "face_indices": np.array(face_indices, dtype=np.int32),
            "labels": labels,
            "wall_ids": wall_ids,
        }

    def __repr__(self):
        return (f"RoomMeshData(verts={self.vertex_count}, "
                f"faces={self.face_count})")


# ---------------------------------------------------------------------------
# Reconstruction
# ---------------------------------------------------------------------------

def reconstruct_room_shell(room_data, include_openings=True,
                           progress_callback=None):
    """Generate a clean polygonal room shell from detected room planes.

    Produces floor, ceiling, and wall quads with optional window/door
    cutouts subtracted from the walls.

    Args:
        room_data:          ``RoomData`` instance from ``room_detector``.
        include_openings:   If True, subtract window/door apertures from walls.
        progress_callback:  Optional ``callable(percent, message)``.

    Returns:
        ``RoomMeshData`` instance containing the room shell geometry.
    """
    mesh = RoomMeshData()

    floor_y = room_data.floor_y
    ceil_y = room_data.ceil_y
    x_min = room_data.x_min
    x_max = room_data.x_max
    z_min = room_data.z_min
    z_max = room_data.z_max

    if progress_callback:
        progress_callback(10, "Generating floor and ceiling quads...")

    # ------------------------------------------------------------------
    # Floor (Y = floor_y, normal = +Y)
    # ------------------------------------------------------------------
    mesh.add_quad(
        p0=(x_min, floor_y, z_max),  # BL
        p1=(x_max, floor_y, z_max),  # BR
        p2=(x_max, floor_y, z_min),  # TR
        p3=(x_min, floor_y, z_min),  # TL
        normal=(0.0, 1.0, 0.0),
        label="floor",
        uv0=(0, 0), uv1=(1, 0), uv2=(1, 1), uv3=(0, 1),
    )

    # ------------------------------------------------------------------
    # Ceiling (Y = ceil_y, normal = -Y)
    # ------------------------------------------------------------------
    mesh.add_quad(
        p0=(x_min, ceil_y, z_min),
        p1=(x_max, ceil_y, z_min),
        p2=(x_max, ceil_y, z_max),
        p3=(x_min, ceil_y, z_max),
        normal=(0.0, -1.0, 0.0),
        label="ceiling",
        uv0=(0, 0), uv1=(1, 0), uv2=(1, 1), uv3=(0, 1),
    )

    if progress_callback:
        progress_callback(30, "Generating wall quads...")

    # ------------------------------------------------------------------
    # Walls
    # ------------------------------------------------------------------
    # Collect apertures by wall
    apertures_by_wall = {"west": [], "east": [], "north": [], "south": []}
    if include_openings:
        for ap in room_data.windows + room_data.doors:
            wall_name = ap.get("wall", "")
            if wall_name in apertures_by_wall:
                apertures_by_wall[wall_name].append(ap)

    # Wall definitions: (wall_id, cardinal, corners BL-BR-TR-TL, normal)
    wall_defs = [
        ("W", "west",
         [(x_min, floor_y, z_max), (x_min, floor_y, z_min),
          (x_min, ceil_y, z_min), (x_min, ceil_y, z_max)],
         (1.0, 0.0, 0.0)),
        ("E", "east",
         [(x_max, floor_y, z_min), (x_max, floor_y, z_max),
          (x_max, ceil_y, z_max), (x_max, ceil_y, z_min)],
         (-1.0, 0.0, 0.0)),
        ("N", "north",
         [(x_min, floor_y, z_min), (x_max, floor_y, z_min),
          (x_max, ceil_y, z_min), (x_min, ceil_y, z_min)],
         (0.0, 0.0, 1.0)),
        ("S", "south",
         [(x_max, floor_y, z_max), (x_min, floor_y, z_max),
          (x_min, ceil_y, z_max), (x_max, ceil_y, z_max)],
         (0.0, 0.0, -1.0)),
    ]

    for wall_id, cardinal, corners, normal in wall_defs:
        aps = apertures_by_wall.get(cardinal, [])

        if not aps or not include_openings:
            # Solid wall — single quad
            mesh.add_quad(
                p0=corners[0], p1=corners[1],
                p2=corners[2], p3=corners[3],
                normal=normal,
                label="wall",
                wall_id=wall_id,
            )
        else:
            # Wall with aperture cutouts
            # For simplicity, handle the first aperture with triangulated frame
            ap = aps[0]
            hole_corners = _aperture_to_wall_corners(ap, wall_id, corners)
            if hole_corners is not None:
                mesh.add_triangulated_quad_with_hole(
                    corners=corners,
                    hole_corners=hole_corners,
                    normal=normal,
                    label="wall",
                    wall_id=wall_id,
                )
                # Add additional apertures as simple cutouts
                # (multiple apertures on same wall → extend in future)
            else:
                mesh.add_quad(
                    p0=corners[0], p1=corners[1],
                    p2=corners[2], p3=corners[3],
                    normal=normal,
                    label="wall",
                    wall_id=wall_id,
                )

    if progress_callback:
        progress_callback(100, f"Room shell: {mesh.vertex_count} vertices, "
                               f"{mesh.face_count} faces.")

    return mesh


def _aperture_to_wall_corners(aperture, wall_id, wall_corners):
    """Convert an aperture definition to 4 hole corner positions on a wall.

    Args:
        aperture:     Aperture dict from room_data (u_range, v_range, pos).
        wall_id:      Cardinal direction ('W', 'E', 'N', 'S').
        wall_corners: 4 corner positions of the wall quad.

    Returns:
        List of 4 hole corner positions [BL, BR, TR, TL], or None on failure.
    """
    u_range = aperture.get("u_range")
    v_range = aperture.get("v_range")
    if not u_range or not v_range:
        return None

    u0, u1 = u_range
    y_lo, y_hi = v_range

    p0 = wall_corners[0]  # BL

    if wall_id in ("W", "E"):
        # Wall spans Z, fixed X
        x_val = p0[0]
        if wall_id == "W":
            # BL is z_max, BR is z_min → Z decreases left to right
            h_bl = (x_val, y_lo, u1)
            h_br = (x_val, y_lo, u0)
            h_tr = (x_val, y_hi, u0)
            h_tl = (x_val, y_hi, u1)
        else:
            # BL is z_min, BR is z_max
            h_bl = (x_val, y_lo, u0)
            h_br = (x_val, y_lo, u1)
            h_tr = (x_val, y_hi, u1)
            h_tl = (x_val, y_hi, u0)
    elif wall_id in ("N", "S"):
        # Wall spans X, fixed Z
        z_val = p0[2]
        if wall_id == "N":
            # BL is x_min, BR is x_max
            h_bl = (u0, y_lo, z_val)
            h_br = (u1, y_lo, z_val)
            h_tr = (u1, y_hi, z_val)
            h_tl = (u0, y_hi, z_val)
        else:
            # BL is x_max, BR is x_min → X decreases
            h_bl = (u1, y_lo, z_val)
            h_br = (u0, y_lo, z_val)
            h_tr = (u0, y_hi, z_val)
            h_tl = (u1, y_hi, z_val)
    else:
        return None

    return [h_bl, h_br, h_tr, h_tl]


# ---------------------------------------------------------------------------
# Houdini SOP geometry creation
# ---------------------------------------------------------------------------

def create_room_sop_geometry(mesh_data, geo):
    """Write ``RoomMeshData`` into a Houdini ``hou.Geometry`` object.

    Creates points, polygons, normals, UVs, and semantic string attributes.

    Args:
        mesh_data: ``RoomMeshData`` instance.
        geo:       ``hou.Geometry`` to populate (should be empty/cleared).
    """
    try:
        import hou
    except ImportError:
        raise RuntimeError("create_room_sop_geometry requires Houdini Python")

    geo.clear()

    # Create attributes
    geo.addAttrib(hou.attribType.Vertex, "N", (0.0, 1.0, 0.0))
    geo.addAttrib(hou.attribType.Vertex, "uv", (0.0, 0.0, 0.0))
    geo.addAttrib(hou.attribType.Prim, "semantic_class", "")
    geo.addAttrib(hou.attribType.Prim, "wall_id", "")

    # Create points
    points = []
    for i in range(len(mesh_data.vertices)):
        pt = geo.createPoint()
        pt.setPosition(hou.Vector3(mesh_data.vertices[i]))
        points.append(pt)

    # Create polygons with vertex normals, UVs, and primitive attributes
    for fi, face in enumerate(mesh_data.faces):
        poly = geo.createPolygon()
        n = mesh_data.normals[fi]
        for vi in face:
            vtx = poly.addVertex(points[vi])
            vtx.setAttribValue("N", hou.Vector3(n))
            if vi < len(mesh_data.uvs):
                u, v = mesh_data.uvs[vi]
                vtx.setAttribValue("uv", (u, v, 0.0))

        poly.setAttribValue("semantic_class", mesh_data.face_labels[fi])
        poly.setAttribValue("wall_id", mesh_data.face_wall_ids[fi])
