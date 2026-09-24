# -*- coding: utf-8 -*-
"""
Solaris LOP Stage Integration for 3D Gaussian Splatting.

Provides:
- Creation and real-time synchronization of 3D Probe Locator in /stage.
- Querying and setting probe world coordinates.
- Spawning native USD RectLights and SphereLights from 3D Gaussian Splat radiance clusters.
"""

import math
import os
import sys
import numpy as np
try:
    import hou
except ImportError:
    hou = None

try:
    from pxr import Usd, UsdGeom, UsdShade, Sdf, Vt, Gf
except ImportError:
    Usd = UsdGeom = UsdShade = Sdf = Vt = Gf = None

try:
    from hdri_match_solaris import renderer_materials as _rmat
except ImportError:
    try:
        import renderer_materials as _rmat
    except ImportError:
        _rmat = None


def get_or_create_splat_probe(stage_node, initial_pos=(0.0, 1.5, 0.0)):
    """
    Find or create the interactive 3D Splat Probe camera in /stage.
    Uses a vibrant cyan color and camera/locator representation for easy viewport dragging.
    """
    if not stage_node or not hou:
        return None

    probe_node = stage_node.node("hdri_match_splat_probe")
    if probe_node is None:
        try:
            # Create camera node in /stage
            probe_node = stage_node.createNode("camera", "hdri_match_splat_probe")
            probe_node.setColor(hou.Color((0.0, 0.8, 0.88)))
            
            # Setup USD primpath
            prim_parm = probe_node.parm("primpath")
            if prim_parm:
                prim_parm.set("/cameras/splat_probe")

            # Set initial position
            for ax, val in zip(["tx", "ty", "tz"], initial_pos):
                p = probe_node.parm(ax)
                if p:
                    p.set(float(val))

            # Enable panoramic equirectangular projection if parameter exists
            proj_parm = probe_node.parm("projection")
            if proj_parm:
                try:
                    proj_parm.set("panoramic")
                except Exception:
                    pass

            # Probe is an interactive locator camera in /stage; keep it clean and independent
            stage_node.layoutChildren()
        except Exception as e:
            print(f"[HDRI Match Splat] Failed to create probe node: {e}")
            return None

    return probe_node


def get_probe_position(stage_node):
    """Get the current (x, y, z) position of the splat probe."""
    if not stage_node or not hou:
        return (0.0, 1.5, 0.0)

    probe = stage_node.node("hdri_match_splat_probe")
    if not probe:
        return (0.0, 1.5, 0.0)

    try:
        tx = float(probe.parm("tx").eval() if probe.parm("tx") else 0.0)
        ty = float(probe.parm("ty").eval() if probe.parm("ty") else 1.5)
        tz = float(probe.parm("tz").eval() if probe.parm("tz") else 0.0)
        return (tx, ty, tz)
    except Exception:
        return (0.0, 1.5, 0.0)


def set_probe_position(stage_node, pos):
    """Set the (x, y, z) position of the splat probe in /stage."""
    if not stage_node or not hou:
        return

    probe = get_or_create_splat_probe(stage_node, initial_pos=pos)
    if not probe:
        return

    try:
        for ax, val in zip(["tx", "ty", "tz"], pos):
            p = probe.parm(ax)
            if p:
                p.set(float(val))
    except Exception as e:
        print(f"[HDRI Match Splat] Failed to set probe position: {e}")


def snap_probe_to_active_viewport_camera(stage_node):
    """Snap the probe position to current active Houdini viewport perspective camera."""
    if not stage_node or not hou or not hou.isUIAvailable():
        return None

    try:
        cur_desktop = hou.ui.curDesktop()
        scene_viewer = cur_desktop.paneTabOfType(hou.paneTabType.SceneViewer)
        if scene_viewer:
            cur_viewport = scene_viewer.curViewport()
            cam_xform = cur_viewport.viewTransform()
            origin = cam_xform.extractTranslates()
            pos = (float(origin[0]), float(origin[1]), float(origin[2]))
            set_probe_position(stage_node, pos)
            return pos
    except Exception as e:
        print(f"[HDRI Match Splat] Viewport snap error: {e}")

    return None


def clear_splat_lights(stage_node):
    """
    Remove all existing splat lights and debug visualization nodes in /stage.
    """
    if not stage_node or not hou:
        return 0

    removed = 0
    for child in list(stage_node.children()):
        if child.name().startswith("splat_3d_light_") or child.name() == "splat_lights_debug":
            try:
                child.destroy()
                removed += 1
            except Exception:
                pass
    return removed


def _get_cluster_attr(c, attr, default=None):
    """Safely get attribute whether cluster is a GaussianLightCluster object or a dict."""
    if hasattr(c, attr):
        val = getattr(c, attr)
        return val if val is not None else default
    if isinstance(c, dict):
        return c.get(attr, default)
    return default


def create_splat_lights_debug_visualization(stage_node, clusters, clear_existing=True):
    """
    Create an interactive debug representation in /stage under /debug/splat_lights_vis.
    Generates:
    - Point cloud of detected Gaussian splats colored by cluster.
    - Energy-weighted centroid marker.
    - Emission surface normal vector (pointing into the interior scene).
    - Rectangular light plane wireframe (exact dimensions and orientation of the USD RectLight).
    - 3D Bounding box wireframe.
    """
    if not stage_node or not hou or not clusters:
        return None

    if clear_existing:
        old = stage_node.node("splat_lights_debug")
        if old:
            try:
                old.destroy()
            except Exception:
                pass

    try:
        sc = stage_node.createNode("sopcreate", "splat_lights_debug")
        sc.setColor(hou.Color((0.15, 0.85, 0.70)))
        
        primpath_p = sc.parm("primpath")
        if primpath_p:
            primpath_p.set("/debug/splat_lights_vis")

        sc.allowEditingOfContents()
        sopnet = sc.node("sopnet")
        if not sopnet:
            return sc

        # Prepare serializable debug data
        debug_items = []
        for i, c in enumerate(clusters):
            cid = _get_cluster_attr(c, "cluster_id", i + 1)
            raw_c = _get_cluster_attr(c, "centroid", _get_cluster_attr(c, "pos", [0, 2, 0]))
            centroid = [float(v) for v in raw_c]
            normal = [float(v) for v in _get_cluster_attr(c, "normal", [0, 0, 1])]
            width_axis = [float(v) for v in _get_cluster_attr(c, "width_axis", [1, 0, 0])]
            height_axis = [float(v) for v in _get_cluster_attr(c, "height_axis", [0, 1, 0])]
            width = float(_get_cluster_attr(c, "width", 1.0))
            height = float(_get_cluster_attr(c, "height", 1.0))
            color = [float(v) for v in _get_cluster_attr(c, "color", [1, 1, 1])]
            bbox_min = [float(v) for v in _get_cluster_attr(c, "bbox_min", [centroid[0]-0.5, centroid[1]-0.5, centroid[2]-0.5])]
            bbox_max = [float(v) for v in _get_cluster_attr(c, "bbox_max", [centroid[0]+0.5, centroid[1]+0.5, centroid[2]+0.5])]

            # Sample member points (downsample to max 300 points per cluster for fluid viewport)
            pts = _get_cluster_attr(c, "points", None)
            sample_pts = []
            if pts is not None and len(pts) > 0:
                if len(pts) > 300:
                    step = len(pts) // 300
                    sub_pts = pts[::step][:300]
                else:
                    sub_pts = pts
                sample_pts = [[float(v) for v in p] for p in sub_pts]

            debug_items.append({
                "id": cid,
                "centroid": centroid,
                "normal": normal,
                "width_axis": width_axis,
                "height_axis": height_axis,
                "width": width,
                "height": height,
                "color": color,
                "bbox_min": bbox_min,
                "bbox_max": bbox_max,
                "sample_points": sample_pts,
            })

        py_sop = sopnet.createNode("python", "build_splat_vis")
        code = f'''# Auto-generated 3D Gaussian Splat Lighting Debug Geometry
import hou

node = hou.pwd()
geo = node.geometry()
geo.clear()

geo.addAttrib(hou.attribType.Point, "Cd", (1.0, 1.0, 1.0))
geo.addAttrib(hou.attribType.Point, "light_name", "")
geo.addAttrib(hou.attribType.Point, "debug_element", "")

palette = [
    (0.18, 0.80, 0.44),  # emerald
    (0.20, 0.60, 1.00),  # dodger blue
    (1.00, 0.65, 0.00),  # amber orange
    (0.95, 0.26, 0.21),  # coral red
    (0.61, 0.35, 0.71),  # amethyst
    (0.13, 0.85, 0.85),  # cyan
    (1.00, 0.85, 0.10),  # sunflower
    (0.90, 0.30, 0.60),  # raspberry
]

DATA = {repr(debug_items)}

for i, item in enumerate(DATA):
    col = palette[i % len(palette)]
    c_name = f"SplatLight_{{item['id']:02d}}"
    c = item['centroid']
    n = item['normal']
    wx = item['width_axis']
    hy = item['height_axis']
    w = item['width']
    h = item['height']

    # 1. Centroid point
    pt_c = geo.createPoint()
    pt_c.setPosition(c)
    pt_c.setAttribValue("Cd", (1.0, 1.0, 1.0))
    pt_c.setAttribValue("light_name", c_name)
    pt_c.setAttribValue("debug_element", "centroid")

    # 2. Emission Normal Vector Arrow
    pt_n = geo.createPoint()
    pt_n.setPosition((c[0] + n[0]*0.8, c[1] + n[1]*0.8, c[2] + n[2]*0.8))
    pt_n.setAttribValue("Cd", (0.0, 1.0, 0.5))
    pt_n.setAttribValue("light_name", c_name)
    pt_n.setAttribValue("debug_element", "normal")
    n_line = geo.createPolygon(is_closed=False)
    n_line.addVertex(pt_c)
    n_line.addVertex(pt_n)

    # 3. Rectangular Light Plane Wireframe
    hw = w * 0.5
    hh = h * 0.5
    corners = [
        (c[0] - wx[0]*hw - hy[0]*hh, c[1] - wx[1]*hw - hy[1]*hh, c[2] - wx[2]*hw - hy[2]*hh),
        (c[0] + wx[0]*hw - hy[0]*hh, c[1] + wx[1]*hw - hy[1]*hh, c[2] + wx[2]*hw - hy[2]*hh),
        (c[0] + wx[0]*hw + hy[0]*hh, c[1] + wx[1]*hw + hy[1]*hh, c[2] + wx[2]*hw + hy[2]*hh),
        (c[0] - wx[0]*hw + hy[0]*hh, c[1] - wx[1]*hw + hy[1]*hh, c[2] - wx[2]*hw + hy[2]*hh),
    ]
    rect_pts = []
    for cp in corners:
        p = geo.createPoint()
        p.setPosition(cp)
        p.setAttribValue("Cd", col)
        p.setAttribValue("light_name", c_name)
        p.setAttribValue("debug_element", "rect_outline")
        rect_pts.append(p)

    rect_poly = geo.createPolygon(is_closed=True)
    for p in rect_pts:
        rect_poly.addVertex(p)

    # Cross diagonals
    diag1 = geo.createPolygon(is_closed=False)
    diag1.addVertex(rect_pts[0])
    diag1.addVertex(rect_pts[2])
    diag2 = geo.createPolygon(is_closed=False)
    diag2.addVertex(rect_pts[1])
    diag2.addVertex(rect_pts[3])

    # 4. Bounding Box Wireframe
    bmin = item.get("bbox_min")
    bmax = item.get("bbox_max")
    if bmin and bmax:
        box_corners = [
            (bmin[0], bmin[1], bmin[2]), (bmax[0], bmin[1], bmin[2]),
            (bmax[0], bmax[1], bmin[2]), (bmin[0], bmax[1], bmin[2]),
            (bmin[0], bmin[1], bmax[2]), (bmax[0], bmin[1], bmax[2]),
            (bmax[0], bmax[1], bmax[2]), (bmin[0], bmax[1], bmax[2]),
        ]
        bpts = []
        for bp in box_corners:
            p = geo.createPoint()
            p.setPosition(bp)
            p.setAttribValue("Cd", (col[0]*0.35, col[1]*0.35, col[2]*0.35))
            p.setAttribValue("light_name", c_name)
            p.setAttribValue("debug_element", "bbox")
            bpts.append(p)
        edges = [
            (0,1), (1,2), (2,3), (3,0),
            (4,5), (5,6), (6,7), (7,4),
            (0,4), (1,5), (2,6), (3,7)
        ]
        for e0, e1 in edges:
            bline = geo.createPolygon(is_closed=False)
            bline.addVertex(bpts[e0])
            bline.addVertex(bpts[e1])

    # 5. Cluster splats
    for sp in item.get("sample_points", []):
        p = geo.createPoint()
        p.setPosition(sp)
        p.setAttribValue("Cd", col)
        p.setAttribValue("light_name", c_name)
        p.setAttribValue("debug_element", "splat_point")

    # 6. Directional Light Rays to Lookdev Center / Scene Focus (-3.5, 0.8, 0.0)
    pt_tgt = geo.createPoint()
    pt_tgt.setPosition((-3.5, 0.8, 0.0))
    pt_tgt.setAttribValue("Cd", (0.2, 0.85, 1.0))
    pt_tgt.setAttribValue("light_name", c_name)
    pt_tgt.setAttribValue("debug_element", "light_ray")
    ray_line = geo.createPolygon(is_closed=False)
    ray_line.addVertex(pt_c)
    ray_line.addVertex(pt_tgt)
'''
        py_sop.parm("python").set(code)
        out_node = sopnet.node("OUT")
        if out_node:
            out_node.setInput(0, py_sop)

        return sc
    except Exception as e:
        print(f"[HDRI Match Splat] Failed to create debug vis node: {e}")
        return None


def spawn_3d_splat_lights(
    stage_node,
    clusters,
    light_type="rect",
    prim_path_prefix="/lights/splats",
    clear_existing=True,
    create_debug_vis=True,
):
    """
    Spawn native Solaris 'light' LOPs from 3D Gaussian Splat radiance clusters.
    Supports UsdLuxRectLight (default) and UsdLuxSphereLight, complete with
    energy-weighted color, intensity, physical dimensions, orientation, and
    optional debug visualization.
    """
    if not stage_node or not hou or not clusters:
        return []

    created_nodes = []

    if clear_existing:
        clear_splat_lights(stage_node)

    # Upstream parent tail
    sun_node = stage_node.node("hdri_sun")
    dome_node = stage_node.node("hdri_dome")
    prev_node = sun_node if sun_node else dome_node

    is_rect = (light_type == "rect" or light_type == "rectangular")

    for i, c in enumerate(clusters):
        cid = _get_cluster_attr(c, "cluster_id", i + 1)
        name = f"splat_3d_light_{cid:02d}"
        pos = _get_cluster_attr(c, "centroid", _get_cluster_attr(c, "pos", [0, 2, 0]))
        col = _get_cluster_attr(c, "color", [1, 1, 1])
        intensity = _get_cluster_attr(c, "intensity", 50.0)
        width = _get_cluster_attr(c, "width", 1.0)
        height = _get_cluster_attr(c, "height", 1.0)
        rot_euler = _get_cluster_attr(c, "rotation_euler", [0.0, 0.0, 0.0])
        temperature = _get_cluster_attr(c, "temperature", None)

        try:
            lt_node = stage_node.createNode("light", name)
            lt_node.setColor(hou.Color((0.95, 0.75, 0.15)))

            # USD Primitive Path
            if lt_node.parm("primpath"):
                lt_node.parm("primpath").set(f"{prim_path_prefix}/SplatLight_{cid:02d}")

            # Translations
            for ax, val in zip(["tx", "ty", "tz"], pos):
                p = lt_node.parm(ax)
                if p:
                    p.set(float(val))

            # Light Type Specifics
            if is_rect:
                if lt_node.parm("lighttype"):
                    lt_node.parm("lighttype").set("UsdLuxRectLight")

                # Euler Rotations
                for r_ax, val in zip(["rx", "ry", "rz"], rot_euler):
                    p = lt_node.parm(r_ax)
                    if p:
                        p.set(float(val))

                # Width & Height
                if lt_node.parm("xn__inputswidth_control_06a"):
                    lt_node.parm("xn__inputswidth_control_06a").set("set")
                if lt_node.parm("xn__inputswidth_zta"):
                    lt_node.parm("xn__inputswidth_zta").set(max(0.05, float(width)))

                if lt_node.parm("xn__inputsheight_control_n8a"):
                    lt_node.parm("xn__inputsheight_control_n8a").set("set")
                if lt_node.parm("xn__inputsheight_mva"):
                    lt_node.parm("xn__inputsheight_mva").set(max(0.05, float(height)))
            else:
                if lt_node.parm("lighttype"):
                    lt_node.parm("lighttype").set("UsdLuxSphereLight")

                radius = max(0.05, 0.5 * min(float(width), float(height)))
                if lt_node.parm("xn__inputsradius_control_wcb"):
                    lt_node.parm("xn__inputsradius_control_wcb").set("set")
                if lt_node.parm("xn__inputsradius_vya"):
                    lt_node.parm("xn__inputsradius_vya").set(radius)

            # Color (Scene-Linear RGB)
            if lt_node.parm("xn__inputscolor_control_06a"):
                lt_node.parm("xn__inputscolor_control_06a").set("set")
            col_tuple = lt_node.parmTuple("xn__inputscolor_zta")
            if col_tuple:
                col_tuple.set(tuple(float(v) for v in col))
            else:
                for c_ax, val in zip(["xn__inputscolor_r_k8a", "xn__inputscolor_g_k8a", "xn__inputscolor_b_k8a"], col):
                    p = lt_node.parm(c_ax)
                    if p:
                        p.set(float(val))

            # Radiant Intensity
            if lt_node.parm("xn__inputsintensity_control_jeb"):
                lt_node.parm("xn__inputsintensity_control_jeb").set("set")
            if lt_node.parm("xn__inputsintensity_i0a"):
                lt_node.parm("xn__inputsintensity_i0a").set(max(0.1, float(intensity)))

            # Normalization (0 = standard VFX non-normalized surface radiance)
            for norm_ctrl in ["xn__inputsnormalize_control_jeb", "xn__inputsnormalize_control_1wb"]:
                if lt_node.parm(norm_ctrl):
                    lt_node.parm(norm_ctrl).set("set")
            for norm_p in ["xn__inputsnormalize_i0a", "xn__inputsnormalize_06a"]:
                if lt_node.parm(norm_p):
                    lt_node.parm(norm_p).set(0)

            # Houdini viewport guide scale
            if lt_node.parm("xn__houdiniguidescale_control_thb"):
                lt_node.parm("xn__houdiniguidescale_control_thb").set("set")
            if lt_node.parm("xn__houdiniguidescale_s3a"):
                lt_node.parm("xn__houdiniguidescale_s3a").set(1.0)

            # Connect node into stage stream
            if prev_node and lt_node != prev_node:
                lt_node.setInput(0, prev_node)
            prev_node = lt_node

            created_nodes.append(lt_node)
        except Exception as e:
            print(f"[HDRI Match Splat] Failed to create light {name}: {e}")

    # Optional Debug Visualization Geometry in Solaris
    if create_debug_vis and created_nodes:
        debug_node = create_splat_lights_debug_visualization(stage_node, clusters, clear_existing=False)
        if debug_node:
            if prev_node and debug_node != prev_node:
                debug_node.setInput(0, prev_node)
            prev_node = debug_node

    # Reconnect downstream lookdev node
    lookdev = stage_node.node("hdri_match_lookdev")
    if lookdev and prev_node and lookdev != prev_node:
        lookdev.setInput(0, prev_node)

    stage_node.layoutChildren()
    return created_nodes


def focus_viewport_on_probe(stage_node, pos=None):
    """
    Center and frame the active Scene Viewer viewport camera on the Splat Probe.
    """
    if not hou or not hou.isUIAvailable():
        return False

    try:
        if pos is None and stage_node:
            pos = get_probe_position(stage_node)
        if pos is None:
            return False

        desk = hou.ui.curDesktop()
        scene_viewer = desk.paneTabOfType(hou.paneTabType.SceneViewer)
        if scene_viewer:
            cur_viewport = scene_viewer.curViewport()
            if cur_viewport:
                bbox = hou.BoundingBox(
                    float(pos[0]) - 1.5, float(pos[1]) - 1.5, float(pos[2]) - 1.5,
                    float(pos[0]) + 1.5, float(pos[1]) + 1.5, float(pos[2]) + 1.5
                )
                cur_viewport.frameBoundingBox(bbox)
                return True
    except Exception as e:
        print(f"[HDRI Match Splat] Viewport focus error: {e}")

    return False


def compute_splat_houdini_dimensions(
    scene,
    scene_scale=1.0,
    flip_y=True,
    center_to_origin=False,
    snap_floor_y0=False,
):
    """
    Calculate bounding box dimensions and centroid of Gaussian splats in Houdini meters.
    Returns:
        dict: {
            'width': float,
            'height': float,
            'depth': float,
            'min_bound': [x, y, z],
            'max_bound': [x, y, z],
            'centroid': [x, y, z],
            'raw_height': float,
            'suggested_scale': float,
        }
    """
    if scene is None or scene.count == 0 or scene.positions is None:
        return None

    pos = scene.positions.copy().astype(np.float32)
    if flip_y:
        pos[:, 1] *= -1.0
        pos[:, 2] *= -1.0

    # Measure raw height (2% to 98% percentiles to ignore distant floaters)
    raw_floor = float(np.percentile(pos[:, 1], 2.0))
    raw_ceil = float(np.percentile(pos[:, 1], 98.0))
    raw_h = max(0.1, raw_ceil - raw_floor)
    suggested_scale = float(2.9 / raw_h) if (raw_h > 6.0 or raw_h < 0.6) else 1.0

    scale_f = float(scene_scale)
    if abs(scale_f - 1.0) > 1e-6:
        pos *= scale_f

    if center_to_origin:
        med_xz = np.median(pos[:, [0, 2]], axis=0)
        pos[:, 0] -= med_xz[0]
        pos[:, 2] -= med_xz[1]

    if snap_floor_y0:
        fl_y = float(np.percentile(pos[:, 1], 1.5))
        pos[:, 1] -= fl_y

    min_b = [float(pos[:, 0].min()), float(pos[:, 1].min()), float(pos[:, 2].min())]
    max_b = [float(pos[:, 0].max()), float(pos[:, 1].max()), float(pos[:, 2].max())]
    centroid = [float(np.median(pos[:, 0])), float(np.median(pos[:, 1])), float(np.median(pos[:, 2]))]

    # Bounding dimensions (1st to 99th percentile to filter outliers)
    w_x = float(np.percentile(pos[:, 0], 99.0) - np.percentile(pos[:, 0], 1.0))
    h_y = float(np.percentile(pos[:, 1], 99.0) - np.percentile(pos[:, 1], 1.0))
    d_z = float(np.percentile(pos[:, 2], 99.0) - np.percentile(pos[:, 2], 1.0))

    return {
        "width": max(0.01, w_x),
        "height": max(0.01, h_y),
        "depth": max(0.01, d_z),
        "min_bound": min_b,
        "max_bound": max_b,
        "centroid": centroid,
        "raw_height": raw_h,
        "suggested_scale": suggested_scale,
    }


def export_splat_points_usd(
    scene,
    output_usd_path,
    point_size=0.03,
    size_mode="uniform",
    density_pct=100.0,
    max_points=None,
    min_opacity=0.05,
    subsample_mode="strided",
    render_mode="sphere",
    create_material=True,
    roughness=0.50,
    prim_path="/splats/points",
    flip_y=True,
    scene_scale=1.0,
    center_to_origin=False,
    snap_floor_y0=False,
):
    """
    Export a production-ready USD Point Cloud (UsdGeom.Points) from a GaussianSplatScene instance.
    Guarantees full adherence to Houdini metric dimensions:
      - Stage Units: metersPerUnit = 1.0, upAxis = Y.
      - Metric Scaling: optional multiplier (e.g. cm->m = 0.01, or auto-calibrated photogrammetry).
      - Extent: authors mandatory bounding box extent attribute on UsdGeom.Points and Xform.
      - Point Size: uniform in meters, splat-scale proportional, or opacity-weighted.
      - Density Subsampling: percentage (1% - 100%) and max points limit.
      - Opacity Filtering: cutoff threshold (default 0.05) to eliminate ghost splats.
      - Render Tokens: primvars for Karma ('sphere', 'disc', 'point'), Arnold, and Redshift.
      - Shading: auto-creates and binds UsdPreviewSurface material mapping displayColor to diffuseColor.
      - Base Model: usable as a standalone renderable geometry base model or referenced into Solaris.
    """
    import os
    import numpy as np
    from pxr import Usd, UsdGeom, UsdShade, Vt, Sdf, Gf

    output_usd_path = output_usd_path.replace("\\", "/").strip('"')
    os.makedirs(os.path.dirname(output_usd_path), exist_ok=True)

    if scene.count <= 0 or scene.positions is None:
        raise ValueError("Gaussian splat scene contains no points.")

    # 1. Filter by minimum opacity cutoff
    if scene.opacities is not None and min_opacity > 0.0:
        valid_mask = scene.opacities >= float(min_opacity)
        indices = np.where(valid_mask)[0]
    else:
        indices = np.arange(scene.count)

    if len(indices) == 0:
        indices = np.arange(scene.count)

    # 2. Subsample based on density percentage and max points cap
    density_ratio = max(0.001, min(1.0, float(density_pct) / 100.0))
    target_count = max(1, int(round(len(indices) * density_ratio)))
    if max_points is not None and int(max_points) > 0:
        target_count = min(target_count, int(max_points))

    if target_count < len(indices):
        if subsample_mode == "random":
            rng = np.random.default_rng(42)
            chosen = rng.choice(indices, size=target_count, replace=False)
            chosen.sort()
            indices = chosen
        else:
            step = max(1, len(indices) // target_count)
            indices = indices[::step][:target_count]

    # 3. Extract positions, colors, and opacities
    pts = scene.positions[indices].copy().astype(np.float32)
    cols = scene.colors[indices].copy()
    ops = (
        scene.opacities[indices].copy()
        if scene.opacities is not None
        else np.ones(len(pts), dtype=np.float32)
    )

    if flip_y:
        pts[:, 1] *= -1.0
        pts[:, 2] *= -1.0

    # Apply metric scale calibration to Houdini meters (1 unit = 1 meter)
    scene_scale = float(scene_scale)
    if abs(scene_scale - 1.0) > 1e-6:
        pts *= scene_scale

    # Centering & Ground alignment
    if center_to_origin:
        med_xz = np.median(pts[:, [0, 2]], axis=0)
        pts[:, 0] -= med_xz[0]
        pts[:, 2] -= med_xz[1]

    if snap_floor_y0:
        fl_y = float(np.percentile(pts[:, 1], 1.5))
        pts[:, 1] -= fl_y

    # 4. Calculate point widths in meters
    point_size = max(1e-4, float(point_size))
    if size_mode == "scale_proportional" and scene.scales is not None:
        sc = scene.scales[indices].copy()
        if abs(scene_scale - 1.0) > 1e-6:
            sc *= scene_scale
        mean_dim = np.mean(sc, axis=-1)
        avg_scale = np.median(mean_dim) if len(mean_dim) > 0 else 1.0
        if avg_scale < 1e-6:
            avg_scale = 1.0
        widths = np.clip(
            mean_dim * (point_size / avg_scale),
            point_size * 0.1,
            point_size * 10.0,
        ).astype(np.float32)
    elif size_mode == "opacity_weighted":
        widths = np.clip(
            point_size * (ops ** 0.5),
            point_size * 0.05,
            point_size,
        ).astype(np.float32)
    else:  # "uniform"
        widths = np.full(len(pts), point_size, dtype=np.float32)

    # 5. Build USD Stage in memory with Houdini meter units
    stage = Usd.Stage.CreateInMemory()
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.y)
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)

    root_path = "/splats"
    xform = UsdGeom.Xform.Define(stage, root_path)

    clean_prim_path = prim_path if prim_path.startswith("/") else f"/{prim_path}"
    pts_prim = UsdGeom.Points.Define(stage, clean_prim_path)
    pts_prim.CreatePointsAttr(Vt.Vec3fArray.FromNumpy(pts.astype(np.float32)))
    color_pv = pts_prim.CreateDisplayColorPrimvar(UsdGeom.Tokens.vertex)
    color_pv.Set(Vt.Vec3fArray.FromNumpy(cols.astype(np.float32)))
    op_pv = pts_prim.CreateDisplayOpacityPrimvar(UsdGeom.Tokens.vertex)
    op_pv.Set(Vt.FloatArray.FromNumpy(ops.astype(np.float32)))
    pts_prim.CreateWidthsAttr(Vt.FloatArray.FromNumpy(widths.astype(np.float32)))

    # Author mandatory extent bounding box in Houdini meters for viewport & Hydra
    max_half_w = float(np.max(widths) * 0.5) if len(widths) > 0 else 0.0
    min_pt = Gf.Vec3f(
        float(pts[:, 0].min() - max_half_w),
        float(pts[:, 1].min() - max_half_w),
        float(pts[:, 2].min() - max_half_w),
    )
    max_pt = Gf.Vec3f(
        float(pts[:, 0].max() + max_half_w),
        float(pts[:, 1].max() + max_half_w),
        float(pts[:, 2].max() + max_half_w),
    )
    extent_array = Vt.Vec3fArray([min_pt, max_pt])
    pts_prim.CreateExtentAttr(extent_array)

    # Set renderer tokens
    p = pts_prim.GetPrim()
    rm = str(render_mode).lower()
    if "sphere" in rm:
        p.CreateAttribute("primvars:karma:object:pointmode", Sdf.ValueTypeNames.String, False).Set("sphere")
        p.CreateAttribute("primvars:arnold:mode", Sdf.ValueTypeNames.String, False).Set("sphere")
        p.CreateAttribute("primvars:redshift:object:point_mode", Sdf.ValueTypeNames.Int, False).Set(1)
    elif "disc" in rm:
        p.CreateAttribute("primvars:karma:object:pointmode", Sdf.ValueTypeNames.String, False).Set("disc")
        p.CreateAttribute("primvars:arnold:mode", Sdf.ValueTypeNames.String, False).Set("quad")
        p.CreateAttribute("primvars:redshift:object:point_mode", Sdf.ValueTypeNames.Int, False).Set(0)
    else:  # "point"
        p.CreateAttribute("primvars:karma:object:pointmode", Sdf.ValueTypeNames.String, False).Set("point")
        p.CreateAttribute("primvars:arnold:mode", Sdf.ValueTypeNames.String, False).Set("point")

    # 6. Universal PBR Shaded Material
    if create_material:
        mat_path = Sdf.Path(f"{root_path}/splat_points_mat")
        mat = UsdShade.Material.Define(stage, mat_path)
        pbr_path = mat_path.AppendChild("PBRShader")
        pbr = UsdShade.Shader.Define(stage, pbr_path)
        pbr.CreateIdAttr("UsdPreviewSurface")
        pbr.CreateInput("roughness", Sdf.ValueTypeNames.Float).Set(float(roughness))
        pbr.CreateInput("ior", Sdf.ValueTypeNames.Float).Set(1.5)

        # Reader for displayColor
        pvr_col = UsdShade.Shader.Define(stage, mat_path.AppendChild("PrimvarReader_color"))
        pvr_col.CreateIdAttr("UsdPrimvarReader_float3")
        pvr_col.CreateInput("varname", Sdf.ValueTypeNames.Token).Set("displayColor")
        pbr.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).ConnectToSource(pvr_col.ConnectableAPI(), "result")

        # Reader for displayOpacity
        pvr_op = UsdShade.Shader.Define(stage, mat_path.AppendChild("PrimvarReader_opacity"))
        pvr_op.CreateIdAttr("UsdPrimvarReader_float")
        pvr_op.CreateInput("varname", Sdf.ValueTypeNames.Token).Set("displayOpacity")
        pbr.CreateInput("opacity", Sdf.ValueTypeNames.Float).ConnectToSource(pvr_op.ConnectableAPI(), "result")

        # Surface outputs
        pbr_out = pbr.CreateOutput("surface", Sdf.ValueTypeNames.Token)
        mat.CreateSurfaceOutput().ConnectToSource(pbr_out)
        mat.CreateSurfaceOutput("karma").ConnectToSource(pbr_out)
        mat.CreateSurfaceOutput("arnold").ConnectToSource(pbr_out)

        # Bind to points prim
        UsdShade.MaterialBindingAPI(p).Bind(mat)

    stage.SetDefaultPrim(xform.GetPrim())
    stage.GetRootLayer().Export(output_usd_path)

    # Force reload layer in Sdf cache if already open in Houdini
    existing_layer = Sdf.Layer.Find(output_usd_path)
    if existing_layer:
        try:
            existing_layer.Reload(force=True)
        except Exception:
            pass

    return output_usd_path


def export_splat_point_cloud_usd(scene, output_usd_path, max_points=300000, flip_y=True):
    """
    Backwards-compatible wrapper for export_splat_points_usd.
    """
    return export_splat_points_usd(
        scene=scene,
        output_usd_path=output_usd_path,
        point_size=0.04,
        size_mode="uniform",
        max_points=max_points,
        flip_y=flip_y,
        create_material=True,
    )


def create_splat_scene_vis_node(stage_node, usd_path, prim_path="/splats"):
    """
    Create or wire a 'reference' LOP node in /stage to display the 3D Gaussian Splat scene
    point cloud in real-time in the Houdini viewport.
    """
    if not stage_node or not hou:
        return None

    ref_node = stage_node.node("splat_scene_cloud")
    if not ref_node:
        ref_node = stage_node.createNode("reference", "splat_scene_cloud")
        ref_node.setColor(hou.Color((0.3, 0.7, 0.9)))

    usd_clean = usd_path.replace("\\", "/").strip('"')
    if " " in usd_clean:
        usd_clean = f'"{usd_clean}"'

    ref_node.parm("filepath1").set(usd_clean)
    if ref_node.parm("primpath1"):
        ref_node.parm("primpath1").set(prim_path)
    if ref_node.parm("primpath"):
        ref_node.parm("primpath").set(prim_path)
    ref_node.bypass(False)

    # Wire appropriately into Solaris stage stream:
    dome = stage_node.node("hdri_dome")
    sun = stage_node.node("hdri_sun")
    upstream = sun if sun else dome

    lookdev = stage_node.node("hdri_match_lookdev")
    splat_lights = sorted([n for n in stage_node.children() if n.name().startswith("splat_3d_light_")], key=lambda x: x.name())
    first_light = splat_lights[0] if splat_lights else None
    target_downstream = first_light or lookdev

    if target_downstream:
        # Splice ref_node into the chain right before target_downstream
        td_inputs = target_downstream.inputs()
        if td_inputs and td_inputs[0] != ref_node:
            ref_node.setInput(0, td_inputs[0])
        elif upstream and not ref_node.inputs():
            ref_node.setInput(0, upstream)
        target_downstream.setInput(0, ref_node)
        if lookdev:
            lookdev.setDisplayFlag(True)
        else:
            target_downstream.setDisplayFlag(True)
    elif upstream:
        ref_node.setInput(0, upstream)
        ref_node.setDisplayFlag(True)
    else:
        # Find any active tail node in /stage
        tail = None
        for child in stage_node.children():
            if child != ref_node and not child.isBypassed() and not child.outputs():
                tail = child
                break
        if tail:
            ref_node.setInput(0, tail)
        ref_node.setDisplayFlag(True)

    stage_node.layoutChildren()
    return ref_node


def set_splat_scene_vis(stage_node, visible=True, usd_path=None, prim_path="/splats"):
    """
    Set visibility of the splat scene cloud by bypassing / un-bypassing or loading it.
    """
    if not stage_node or not hou:
        return None

    ref_node = stage_node.node("splat_scene_cloud")
    if not visible:
        if ref_node:
            ref_node.bypass(True)
        return ref_node

    # If visible requested:
    if ref_node:
        ref_node.bypass(False)
        if usd_path:
            usd_clean = usd_path.replace("\\", "/").strip('"')
            if " " in usd_clean:
                usd_clean = f'"{usd_clean}"'
            ref_node.parm("filepath1").set(usd_clean)
            if ref_node.parm("primpath1"):
                ref_node.parm("primpath1").set(prim_path)
            if ref_node.parm("primpath"):
                ref_node.parm("primpath").set(prim_path)

        # Ensure it has display flag or downstream lookdev has display flag
        lookdev = stage_node.node("hdri_match_lookdev")
        if lookdev:
            lookdev.setDisplayFlag(True)
        elif not ref_node.outputs():
            ref_node.setDisplayFlag(True)

        # If it was left disconnected, rewire it
        if not ref_node.inputs() and not ref_node.outputs():
            return create_splat_scene_vis_node(stage_node, usd_path or ref_node.parm("filepath1").eval(), prim_path)

        return ref_node
    elif usd_path and os.path.isfile(usd_path.strip('"')):
        return create_splat_scene_vis_node(stage_node, usd_path, prim_path)
    return None


def load_splat_points_in_stage(stage_node, usd_path, prim_path="/splats"):
    """
    Create or wire a 'reference' LOP node in /stage for the USD Points geometry,
    wiring it cleanly into the Solaris stage stream and setting display flag.
    """
    if not stage_node or not hou:
        return None

    ref_node = stage_node.node("splat_points_ref")
    if not ref_node:
        ref_node = stage_node.createNode("reference", "splat_points_ref")
        ref_node.setColor(hou.Color((0.2, 0.75, 0.85)))

    usd_clean = usd_path.replace("\\", "/").strip('"')
    if " " in usd_clean:
        usd_clean = f'"{usd_clean}"'

    ref_node.parm("filepath1").set(usd_clean)
    if ref_node.parm("primpath1"):
        ref_node.parm("primpath1").set(prim_path)
    if ref_node.parm("primpath"):
        ref_node.parm("primpath").set(prim_path)
    ref_node.bypass(False)

    wire_solaris_stage_stream(stage_node)
    ref_node.cook(force=True)
    return ref_node


def set_splat_points_vis(stage_node, visible=True):
    """
    Toggle visibility of the splat points geometry node in /stage.
    """
    if not stage_node or not hou:
        return None
    ref_node = stage_node.node("splat_points_ref")
    if ref_node:
        ref_node.bypass(not visible)
        wire_solaris_stage_stream(stage_node)
    return ref_node


def clear_splat_points_from_stage(stage_node):
    """
    Cleanly remove the splat points geometry reference from /stage and rewire stream.
    """
    if not stage_node or not hou:
        return False
    ref_node = stage_node.node("splat_points_ref")
    if ref_node:
        ref_node.destroy()
        wire_solaris_stage_stream(stage_node)
        return True
    return False


def get_stage_mesh_prims(stage_node):
    """
    Query the active Solaris stage to find all UsdGeom.Mesh primitives available for texturing.
    """
    if not stage_node or not hou:
        return []
    try:
        display_node = stage_node.displayNode()
        target_node = display_node or (stage_node.children()[-1] if stage_node.children() else None) or stage_node
        stage = None
        if hasattr(target_node, "stage"):
            stage = target_node.stage()
        elif hasattr(target_node, "editableStage"):
            stage = target_node.editableStage()
        elif hasattr(stage_node, "stage"):
            stage = stage_node.stage()
        if not stage:
            return []

        mesh_paths = []
        for prim in stage.Traverse():
            if prim.IsA(UsdGeom.Mesh):
                p_str = str(prim.GetPath())
                # Filter out internal test/probe/lookdev meshes
                low = p_str.lower()
                if "lookdev" in low or "probe" in low or "debug" in low or "materials" in low:
                    continue
                mesh_paths.append(p_str)
        return sorted(mesh_paths)
    except Exception as e:
        print(f"[HDRI Match Splat] Error discovering stage meshes: {e}")
        return []


def apply_splat_vertex_colors_to_mesh(
    stage_node,
    mesh_prim_path,
    splat_scene,
    flip_y=True,
    scene_scale=1.0,
    center_to_origin=False,
    snap_floor_y0=False,
    search_radius=0.10,
    default_color=(0.5, 0.5, 0.5),
):
    """
    Sample 3D Gaussian Splats at each vertex of target mesh in /stage and write true
    displayColor vertex colors with dedicated PBR shader.
    """
    if not stage_node or not hou or not splat_scene:
        return {"status": "error", "message": "Missing stage node or splat scene."}

    display_node = stage_node.displayNode() or (stage_node.children()[-1] if stage_node.children() else None) or stage_node
    stage = None
    if hasattr(display_node, "stage"):
        stage = display_node.stage()
    elif hasattr(stage_node, "stage"):
        stage = stage_node.stage()
    if not stage:
        return {"status": "error", "message": "Could not access USD stage."}

    prim = stage.GetPrimAtPath(mesh_prim_path)
    if not prim or not prim.IsValid() or not prim.IsA(UsdGeom.Mesh):
        return {"status": "error", "message": f"Primitive '{mesh_prim_path}' is not a valid UsdGeom.Mesh."}

    mesh = UsdGeom.Mesh(prim)
    pts = mesh.GetPointsAttr().Get()
    if not pts:
        return {"status": "error", "message": f"Mesh '{mesh_prim_path}' contains no points."}

    # Extract world coordinates
    xf_cache = UsdGeom.XformCache()
    w_xf = xf_cache.GetLocalToWorldTransform(prim)
    pts_world = np.array(
        [[w_xf.Transform(p)[0], w_xf.Transform(p)[1], w_xf.Transform(p)[2]] for p in pts],
        dtype=np.float32,
    )

    # Build spatial index
    from hdri_match_solaris.gaussian_splat import SplatSpatialIndex

    splat_pos = splat_scene.positions.copy().astype(np.float32)
    if flip_y:
        splat_pos[:, 1] *= -1.0
        splat_pos[:, 2] *= -1.0

    if abs(float(scene_scale) - 1.0) > 1e-6:
        splat_pos *= float(scene_scale)

    if center_to_origin:
        med_xz = np.median(splat_pos[:, [0, 2]], axis=0)
        splat_pos[:, 0] -= med_xz[0]
        splat_pos[:, 2] -= med_xz[1]

    if snap_floor_y0:
        fl_y = float(np.percentile(splat_pos[:, 1], 1.5))
        splat_pos[:, 1] -= fl_y

    sp_idx = SplatSpatialIndex(splat_pos, splat_scene.colors, cell_size=max(0.04, search_radius * 0.5))
    sampled_cols = sp_idx.sample_points(pts_world, sigma=search_radius, default_col=default_color)

    # Save to cache file for clean non-bloating pythonscript LOP
    cache_dir = os.path.join(os.path.dirname(hou.hipFile.path()), "hdri_match", "cache").replace("\\", "/")
    if not os.path.isdir(os.path.dirname(cache_dir)):
        cache_dir = os.path.expanduser("~/hdri_match_cache").replace("\\", "/")
    os.makedirs(cache_dir, exist_ok=True)

    safe_name = mesh_prim_path.strip("/").replace("/", "_").replace(" ", "_")
    vcol_cache_path = os.path.join(cache_dir, f"{safe_name}_vcol.npy").replace("\\", "/")
    np.save(vcol_cache_path, sampled_cols.astype(np.float32))

    # Create or configure pythonscript LOP node
    texturizer = stage_node.node("splat_mesh_texturizer")
    if not texturizer:
        texturizer = stage_node.createNode("pythonscript", "splat_mesh_texturizer")
        texturizer.setColor(hou.Color((0.3, 0.8, 0.6)))

    clean_npy = vcol_cache_path.replace("\\", "/")
    py_code = f"""import os, numpy as np
from pxr import Usd, UsdGeom, UsdShade, Sdf, Vt

stage = hou.pwd().editableStage()
prim_path = "{mesh_prim_path}"
cache_file = r"{clean_npy}"

prim = stage.GetPrimAtPath(prim_path)
if prim and prim.IsValid() and prim.IsA(UsdGeom.Mesh) and os.path.isfile(cache_file):
    cols_arr = np.load(cache_file)
    mesh = UsdGeom.Mesh(prim)
    mesh.CreateSubdivisionSchemeAttr().Set(UsdGeom.Tokens.none)
    prim.CreateAttribute("primvars:karma:object:dicing:quality", Sdf.ValueTypeNames.Float, False).Set(0.0)
    prim.CreateAttribute("primvars:arnold:subdiv_type", Sdf.ValueTypeNames.String, False).Set("none")
    
    pv_col = mesh.CreateDisplayColorPrimvar(UsdGeom.Tokens.vertex)
    pv_col.Set(Vt.Vec3fArray.FromNumpy(cols_arr))
    
    # Dedicated material with UsdPrimvarReader_float3 displayColor
    mat_path = Sdf.Path(f"/stage/materials/{safe_name}_splat_vcol_mat")
    mat = UsdShade.Material.Define(stage, mat_path)
    pbr = UsdShade.Shader.Define(stage, mat_path.AppendChild("PBRShader"))
    pbr.CreateIdAttr("UsdPreviewSurface")
    pbr.CreateInput("roughness", Sdf.ValueTypeNames.Float).Set(0.65)
    pvr = UsdShade.Shader.Define(stage, mat_path.AppendChild("PrimvarReader_color"))
    pvr.CreateIdAttr("UsdPrimvarReader_float3")
    pvr.CreateInput("varname", Sdf.ValueTypeNames.Token).Set("displayColor")
    pbr.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).ConnectToSource(pvr.ConnectableAPI(), "result")
    pbr_out = pbr.CreateOutput("surface", Sdf.ValueTypeNames.Token)
    mat.CreateSurfaceOutput().ConnectToSource(pbr_out)
    mat.CreateSurfaceOutput("karma").ConnectToSource(pbr_out)
    mat.CreateSurfaceOutput("arnold").ConnectToSource(pbr_out)
    UsdShade.MaterialBindingAPI(prim).Bind(mat)
"""
    texturizer.parm("python").set(py_code)
    texturizer.bypass(False)
    wire_solaris_stage_stream(stage_node)
    texturizer.cook(force=True)

    return {
        "status": "success",
        "mesh": mesh_prim_path,
        "vertex_count": len(pts),
        "mode": "vertex_colors",
        "cache_file": vcol_cache_path,
    }


def apply_splat_projection_to_mesh(
    stage_node,
    mesh_prim_path,
    hdri_texture,
    probe_pos=(0.0, 1.5, 0.0),
    roughness=0.65,
):
    """
    Camera-project the baked equirectangular splat HDRI/albedo texture onto target mesh
    by generating spherical 'st' UVs and binding dedicated UsdPreviewSurface material.
    """
    if not stage_node or not hou or not hdri_texture:
        return {"status": "error", "message": "Missing stage node or HDRI texture."}

    clean_hdri = hdri_texture.replace("\\", "/").strip('"')
    if not os.path.isfile(clean_hdri):
        return {"status": "error", "message": f"HDRI file not found: {clean_hdri}"}

    cam_x, cam_y, cam_z = probe_pos
    safe_name = mesh_prim_path.strip("/").replace("/", "_").replace(" ", "_")

    texturizer = stage_node.node("splat_mesh_texturizer")
    if not texturizer:
        texturizer = stage_node.createNode("pythonscript", "splat_mesh_texturizer")
        texturizer.setColor(hou.Color((0.3, 0.8, 0.6)))

    py_code = f"""import math, os
from pxr import Usd, UsdGeom, UsdShade, Sdf, Vt, Gf

stage = hou.pwd().editableStage()
prim_path = "{mesh_prim_path}"
tex_file = r"{clean_hdri}"
cam_pos = Gf.Vec3f({cam_x}, {cam_y}, {cam_z})

def project_point(p):
    dx = p[0] - cam_pos[0]
    dy = p[1] - cam_pos[1]
    dz = p[2] - cam_pos[2]
    dist = math.sqrt(dx*dx + dy*dy + dz*dz)
    if dist < 1e-6: dist = 1e-6
    vx, vy, vz = dx / dist, dy / dist, dz / dist
    u = (math.atan2(-vx, vz) / (2.0 * math.pi)) % 1.0
    v = 0.5 - (math.asin(max(-1.0, min(1.0, vy))) / math.pi)
    return Gf.Vec2f(float(u), float(1.0 - v))

prim = stage.GetPrimAtPath(prim_path)
if prim and prim.IsValid() and prim.IsA(UsdGeom.Mesh):
    mesh = UsdGeom.Mesh(prim)
    mesh.CreateSubdivisionSchemeAttr().Set(UsdGeom.Tokens.none)
    prim.CreateAttribute("primvars:karma:object:dicing:quality", Sdf.ValueTypeNames.Float, False).Set(0.0)
    prim.CreateAttribute("primvars:arnold:subdiv_type", Sdf.ValueTypeNames.String, False).Set("none")
    
    pts = mesh.GetPointsAttr().Get()
    if pts:
        xf_cache = UsdGeom.XformCache()
        w_xf = xf_cache.GetLocalToWorldTransform(prim)
        uvs = [project_point(w_xf.Transform(pt)) for pt in pts]
        pv_api = UsdGeom.PrimvarsAPI(prim)
        st_pv = pv_api.CreatePrimvar("st", Sdf.ValueTypeNames.TexCoord2fArray, UsdGeom.Tokens.vertex)
        st_pv.Set(Vt.Vec2fArray(uvs))
        
        # Dedicated material with UsdUVTexture
        mat_path = Sdf.Path(f"/stage/materials/{safe_name}_splat_proj_mat")
        mat = UsdShade.Material.Define(stage, mat_path)
        pbr = UsdShade.Shader.Define(stage, mat_path.AppendChild("PBRShader"))
        pbr.CreateIdAttr("UsdPreviewSurface")
        pbr.CreateInput("roughness", Sdf.ValueTypeNames.Float).Set(float({roughness}))
        pbr.CreateInput("ior", Sdf.ValueTypeNames.Float).Set(1.5)
        
        pvr_st = UsdShade.Shader.Define(stage, mat_path.AppendChild("PrimvarReader_st"))
        pvr_st.CreateIdAttr("UsdPrimvarReader_float2")
        pvr_st.CreateInput("varname", Sdf.ValueTypeNames.Token).Set("st")
        
        tex = UsdShade.Shader.Define(stage, mat_path.AppendChild("TextureSampler"))
        tex.CreateIdAttr("UsdUVTexture")
        tex.CreateInput("file", Sdf.ValueTypeNames.Asset).Set(Sdf.AssetPath(tex_file))
        cs = "raw" if tex_file.lower().endswith((".exr", ".hdr")) else "sRGB"
        tex.CreateInput("sourceColorSpace", Sdf.ValueTypeNames.Token).Set(cs)
        tex.CreateInput("st", Sdf.ValueTypeNames.Float2).ConnectToSource(pvr_st.ConnectableAPI(), "result")
        pbr.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).ConnectToSource(tex.ConnectableAPI(), "rgb")
        
        pbr_out = pbr.CreateOutput("surface", Sdf.ValueTypeNames.Token)
        mat.CreateSurfaceOutput().ConnectToSource(pbr_out)
        mat.CreateSurfaceOutput("karma").ConnectToSource(pbr_out)
        mat.CreateSurfaceOutput("arnold").ConnectToSource(pbr_out)
        UsdShade.MaterialBindingAPI(prim).Bind(mat)
"""
    texturizer.parm("python").set(py_code)
    texturizer.bypass(False)
    wire_solaris_stage_stream(stage_node)
    texturizer.cook(force=True)

    return {
        "status": "success",
        "mesh": mesh_prim_path,
        "mode": "projected_texture",
        "hdri_texture": clean_hdri,
    }


def clear_portal_lights(stage_node):
    """
    Remove all existing physical portal light nodes from /stage and rewire cleanly.
    """
    if not stage_node or not hou:
        return 0

    portal_nodes = [child for child in stage_node.children() if child.name().startswith("portal_")]
    if not portal_nodes:
        return 0

    first_portal = portal_nodes[0]
    last_portal = portal_nodes[-1]
    upstream = first_portal.inputs()[0] if first_portal.inputs() else None
    downstream_nodes = [out_n for out_n in last_portal.outputs() if out_n not in portal_nodes]

    removed = 0
    for p in portal_nodes:
        try:
            p.destroy()
            removed += 1
        except Exception:
            pass

    if upstream and downstream_nodes:
        for dn in downstream_nodes:
            dn.setInput(0, upstream)

    stage_node.layoutChildren()
    return removed


def spawn_portal_lights(
    stage_node,
    windows,
    cam_pos=(0.0, 1.5, 0.0),
    hdri_texture="",
    portal_intensity_mult=1.0,
    portal_texture_mode="cropped",
    clear_existing=True,
):
    """
    Spawn physical Houdini Solaris native 'light' LOP nodes for daylight window portals.
    Each window is created as an interactive native UsdLuxRectLight node in /stage,
    allowing artists to select, move, scale, tweak intensity, texture, or light-link
    directly in the Houdini Network Editor and viewport.

    USD primitive paths are mapped to /stage/room/windows/{portal_name} to maintain clean
    scene graph hierarchy matching the architectural room scope.
    """
    if not stage_node or not hou or not windows:
        return []

    # 1. Clean existing portal nodes if requested
    if clear_existing:
        clear_portal_lights(stage_node)

    # 2. Prepare portal textures (cropped directional HDR patches or full HDRI)
    portal_tex_list = []
    raw_hdri_for_portals = None
    orig_hdri = hdri_texture.replace(chr(92), "/") if hdri_texture else ""
    cam_x, cam_y, cam_z = cam_pos

    if portal_texture_mode == "cropped" and orig_hdri and os.path.isfile(orig_hdri):
        try:
            for p_cand in [os.environ.get("HDRI_MATCH_PLATE_ROOT", ""), "E:/PROJECTS/HDRI_Match_Plate", "e:/PROJECTS/HDRI_Match_Plate"]:
                if p_cand and os.path.isdir(p_cand) and p_cand not in sys.path:
                    sys.path.insert(0, p_cand)
            from hdri_match.io.loader import load_exr_to_numpy
            from hdri_match_solaris.gaussian_splat import GaussianSplatBaker
            raw_hdri_for_portals = load_exr_to_numpy(orig_hdri)
        except Exception:
            raw_hdri_for_portals = None

    tex_dir = os.path.dirname(orig_hdri) if orig_hdri else ""
    base_stem = os.path.splitext(os.path.basename(orig_hdri))[0] if orig_hdri else "splat"

    for i, win in enumerate(windows):
        p_tex = ""
        if portal_texture_mode == "full" and orig_hdri and os.path.isfile(orig_hdri):
            p_tex = orig_hdri
        elif portal_texture_mode == "cropped" and raw_hdri_for_portals is not None and tex_dir:
            wall_name = win.get("wall", "win")
            crop_path = os.path.join(tex_dir, f"{base_stem}_portal_{wall_name}_{i+1}.exr").replace(chr(92), "/")
            if not os.path.isfile(crop_path):
                try:
                    H_img, W_img = raw_hdri_for_portals.shape[:2]
                    pos = win["pos"]
                    dx = pos[0] - cam_x
                    dy = pos[1] - cam_y
                    dz = pos[2] - cam_z
                    dist = math.sqrt(dx * dx + dy * dy + dz * dz)
                    u_c = (math.atan2(-dx, dz) / (2.0 * math.pi)) % 1.0
                    v_c = 0.5 - (math.asin(max(-1.0, min(1.0, dy / max(1e-4, dist)))) / math.pi)

                    du = (win["width"] / max(1e-4, dist)) / (2.0 * math.pi)
                    dv = (win["height"] / max(1e-4, dist)) / math.pi

                    x_c = int(u_c * W_img)
                    y_c = int(v_c * H_img)
                    hw = max(16, int(0.5 * du * W_img))
                    hh = max(16, int(0.5 * dv * H_img))

                    y0 = int(np.clip(y_c - hh, 0, H_img - 1))
                    y1 = int(np.clip(y_c + hh, y0 + 1, H_img))

                    shift = int(W_img // 2 - x_c)
                    rolled = np.roll(raw_hdri_for_portals, shift, axis=1)
                    mid_x = W_img // 2
                    crop = rolled[y0:y1, max(0, mid_x - hw):min(W_img, mid_x + hw)].copy()
                    GaussianSplatBaker.save_exr(crop, crop_path)
                    p_tex = crop_path
                except Exception:
                    p_tex = orig_hdri
            else:
                p_tex = crop_path
        portal_tex_list.append(p_tex)

    # 3. Create physical Solaris 'light' LOP nodes
    created_nodes = []
    room_node = stage_node.node("splat_room_architecture")
    lookdev_node = stage_node.node("hdri_match_lookdev")

    for i, win in enumerate(windows):
        p_name = f"portal_{win.get('wall', 'win')}_{i+1}"
        pos = win["pos"]
        rot = win["rotation"]
        w_val = float(win["width"])
        h_val = float(win["height"])
        col = win.get("color", [1.0, 1.0, 1.0])
        p_tex = portal_tex_list[i] if i < len(portal_tex_list) else ""

        lt_node = stage_node.node(p_name)
        if lt_node is None:
            lt_node = stage_node.createNode("light", p_name)
        # Professional Solaris color coding: cyan-blue for daylight portals
        lt_node.setColor(hou.Color((0.35, 0.75, 1.0)))

        # Setup USD prim path
        prim_parm = lt_node.parm("primpath")
        if prim_parm:
            prim_parm.set(f"/stage/room/windows/{p_name}")

        # Light type
        if lt_node.parm("lighttype"):
            lt_node.parm("lighttype").set("UsdLuxRectLight")

        # Transform (pos & rot)
        for ax, val in zip(["tx", "ty", "tz"], pos):
            p = lt_node.parm(ax)
            if p:
                p.set(float(val))
        for r_ax, val in zip(["rx", "ry", "rz"], rot):
            p = lt_node.parm(r_ax)
            if p:
                p.set(float(val))

        # Dimensions
        if lt_node.parm("xn__inputswidth_control_06a"):
            lt_node.parm("xn__inputswidth_control_06a").set("set")
        if lt_node.parm("xn__inputswidth_zta"):
            lt_node.parm("xn__inputswidth_zta").set(max(0.05, w_val))

        if lt_node.parm("xn__inputsheight_control_n8a"):
            lt_node.parm("xn__inputsheight_control_n8a").set("set")
        if lt_node.parm("xn__inputsheight_mva"):
            lt_node.parm("xn__inputsheight_mva").set(max(0.05, h_val))

        # Radiance Intensity & Exposure
        # When an HDR texture is assigned, the EXR texture already contains real physical scene-linear
        # radiance. With normalize=1, daylight window portals need ~15.0 to illuminate the interior room naturally.
        # Without a texture, we use a sensible baseline radiance (~35.0).
        if p_tex and os.path.isfile(p_tex):
            intensity = 5.0 * float(portal_intensity_mult)
        else:
            intensity = 8.0 * float(portal_intensity_mult)

        if lt_node.parm("xn__inputsintensity_control_jeb"):
            lt_node.parm("xn__inputsintensity_control_jeb").set("set")
        if lt_node.parm("xn__inputsintensity_i0a"):
            lt_node.parm("xn__inputsintensity_i0a").set(max(0.01, intensity))

        if lt_node.parm("xn__inputsexposure_control_wcb"):
            lt_node.parm("xn__inputsexposure_control_wcb").set("set")
        if lt_node.parm("xn__inputsexposure_vya"):
            lt_node.parm("xn__inputsexposure_vya").set(0.0)

        # Color
        if lt_node.parm("xn__inputscolor_control_06a"):
            lt_node.parm("xn__inputscolor_control_06a").set("set")
        eff_col = [1.0, 1.0, 1.0] if p_tex else col
        for c_ax, val in zip(["xn__inputscolor_ztar", "xn__inputscolor_ztag", "xn__inputscolor_ztab"], eff_col):
            p = lt_node.parm(c_ax)
            if p:
                p.set(float(val))

        # Texture File
        if p_tex and os.path.isfile(p_tex):
            if lt_node.parm("xn__inputstexturefile_control_shbh"):
                lt_node.parm("xn__inputstexturefile_control_shbh").set("set")
            if lt_node.parm("xn__inputstexturefile_r3ah"):
                lt_node.parm("xn__inputstexturefile_r3ah").set(p_tex)

        # Normalization (0 = direct radiance per unit area, avoiding photon starvation on large windows)
        for norm_ctrl in ["xn__inputsnormalize_control_jeb", "xn__inputsnormalize_control_1wb"]:
            if lt_node.parm(norm_ctrl):
                lt_node.parm(norm_ctrl).set("set")
        for norm_p in ["xn__inputsnormalize_i0a", "xn__inputsnormalize_06a"]:
            if lt_node.parm(norm_p):
                lt_node.parm(norm_p).set(0)

        # Viewport Guide Scale
        if lt_node.parm("xn__houdiniguidescale_control_thb"):
            lt_node.parm("xn__houdiniguidescale_control_thb").set("set")
        if lt_node.parm("xn__houdiniguidescale_s3a"):
            lt_node.parm("xn__houdiniguidescale_s3a").set(1.0)

        created_nodes.append(lt_node)

    # 4. Wire physical portal nodes into the stage graph
    if created_nodes:
        upstream = room_node
        if not upstream:
            for c in ["splat_scene_cloud", "hdri_sun", "hdri_dome"]:
                n = stage_node.node(c)
                if n:
                    upstream = n
                    break

        downstream_targets = []
        if upstream:
            for out_n in list(upstream.outputs()):
                if out_n not in created_nodes:
                    downstream_targets.append(out_n)

    wire_solaris_stage_stream(stage_node)
    return created_nodes


def wire_solaris_stage_stream(stage_node):
    """
    Deterministically wire all HDRI Match and DomeBreaker Solaris LOP nodes
    into a clean, strictly acyclic linear pipeline stream.

    Order:
    1. hdri_dome (domelight)
    2. hdri_sun (distantlight, if present)
    3. splat_scene_cloud (pointcloud reference, if present & active)
    4. splat_points_ref (renderable USD points geometry, if present & active)
    5. splat_room_architecture (room architecture python LOP)
    6. portal_* (all window portal lights in series, if present)
    7. splat_custom_props_ref (custom props reference LOP, if present)
    8. splat_custom_props_projector (custom props projector LOP, if present)
    9. splat_mesh_texturizer (splat mesh texturizer / vertex color LOP, if present)
    10. hdri_match_lookdev (lookdev rig LOP, if present)
    11. crucible_light_studio (if present)
    12. Downstream external nodes (e.g. transform, camera, render settings, karma)
    """
    if not stage_node or not hou:
        return None

    dome = stage_node.node("hdri_dome")
    sun = stage_node.node("hdri_sun")
    cloud = stage_node.node("splat_scene_cloud")
    bakegs = stage_node.node("splat_scene_bakegs")
    splat_points = stage_node.node("splat_points_ref")
    room = stage_node.node("splat_room_architecture")
    splat_mat_lib = stage_node.node("splatforge_materials")
    hdri_mat_lib = stage_node.node("hdri_match_materials")

    portals = sorted(
        [c for c in stage_node.children() if c.name().startswith("portal_") and "light" in c.type().name()],
        key=lambda x: x.name()
    )

    props_ref = stage_node.node("splat_custom_props_ref")
    props_proj = stage_node.node("splat_custom_props_projector")
    mesh_texturizer = stage_node.node("splat_mesh_texturizer")
    lookdev = stage_node.node("hdri_match_lookdev")

    crucible = None
    for c in stage_node.children():
        cname = c.name().lower()
        ctype = c.type().name().lower()
        if "crucible" in cname or "crucible" in ctype:
            crucible = c
            break

    # Build sequence of valid managed nodes
    chain = []
    if dome: chain.append(dome)
    if sun: chain.append(sun)
    if cloud and not cloud.isBypassed(): chain.append(cloud)
    if bakegs and not bakegs.isBypassed(): chain.append(bakegs)
    if splat_points and not splat_points.isBypassed(): chain.append(splat_points)
    if room and not room.isBypassed(): chain.append(room)
    if splat_mat_lib and not splat_mat_lib.isBypassed():
        chain.append(splat_mat_lib)
        if hdri_mat_lib:
            hdri_mat_lib.bypass(True)
    elif hdri_mat_lib and not hdri_mat_lib.isBypassed() and hdri_mat_lib not in chain:
        chain.append(hdri_mat_lib)
    for p in portals:
        if not p.isBypassed(): chain.append(p)
    if props_ref and not props_ref.isBypassed(): chain.append(props_ref)
    if props_proj and not props_proj.isBypassed(): chain.append(props_proj)
    if mesh_texturizer and not mesh_texturizer.isBypassed(): chain.append(mesh_texturizer)
    if lookdev and not lookdev.isBypassed(): chain.append(lookdev)
    if crucible and not crucible.isBypassed(): chain.append(crucible)

    if not chain:
        return None

    managed_set = set(chain)
    probe = stage_node.node("hdri_match_splat_probe")
    if probe:
        managed_set.add(probe)
    for n in (dome, sun, cloud, bakegs, splat_points, room, splat_mat_lib, hdri_mat_lib, props_ref, props_proj, mesh_texturizer, lookdev, crucible):
        if n: managed_set.add(n)
    for p in portals:
        managed_set.add(p)

    # Detect external downstream nodes (such as transform1, render_settings, karma_rop, camera)
    external_downstream = []
    for c in stage_node.children():
        if c not in managed_set:
            if c.inputs() and any(inp in managed_set for inp in c.inputs()):
                external_downstream.append(c)

    # CRITICAL: Disconnect all managed nodes first to cleanly break any existing cycles!
    for n in chain:
        try:
            n.setInput(0, None)
        except Exception:
            pass

    # Re-wire chain nodes strictly in order
    for idx in range(1, len(chain)):
        try:
            chain[idx].setInput(0, chain[idx - 1])
        except Exception as e:
            print(f"[HDRI Match Stream] Wiring error {chain[idx-1].name()} -> {chain[idx].name()}: {e}")

    # Re-connect external downstream nodes to the tail of the chain
    tail = chain[-1]
    for ext in external_downstream:
        try:
            ext.setInput(0, tail)
        except Exception:
            pass

    # Ensure display flag is set on the terminal node
    display_node = external_downstream[0] if external_downstream else (lookdev or tail)
    try:
        display_node.setDisplayFlag(True)
    except Exception:
        pass

    try:
        stage_node.layoutChildren()
    except Exception:
        pass

    return tail


def reconnect_stage_network(stage_node):
    """
    Alias for wire_solaris_stage_stream for backwards compatibility.
    Wires all Solaris stage nodes into a strictly acyclic linear pipeline stream.
    """
    return wire_solaris_stage_stream(stage_node)


def build_usd_room_architecture(
    stage_node,
    room_data,
    build_floor=True,
    floor_mode="visible",
    build_ceiling=True,
    build_walls=True,
    cut_windows=True,
    build_portals=True,
    build_props=True,
    project_hdri=True,
    hdri_texture="",
    probe_pos=(0.0, 1.5, 0.0),
    roughness=0.85,
    snap_lookdev_to_floor=True,
    double_sided=True,
    room_shadows=True,
    room_invisible=False,
    portal_intensity_mult=1.0,
    portal_texture_mode="cropped",
    mat_mode="pbr",
    emissive_mult=1.0,
    use_planar_textures=False,
    planar_textures_dict=None,
    renderer_target="all",
):
    """
    Construct 3D Room Architecture (Floor, Ceiling, 4 Walls with window cutouts,
    interior architectural props, and UsdLuxRectLight portals) in /stage/room via
    a native PythonScript LOP node.
    Supports visible PBR, Emissive, and Hybrid surface modes, as well as both
    camera-projected spherical HDRI texture and dedicated high-res planar surface maps.
    Supports multi-renderer native materials for Arnold, Karma, Redshift, and UsdPreviewSurface.
    """
    if not stage_node or not hou:
        return None

    node_name = "splat_room_architecture"
    room_node = stage_node.node(node_name)
    if not room_node:
        room_node = stage_node.createNode("pythonscript", node_name)
        room_node.setColor(hou.Color((0.85, 0.45, 0.2)))

    # Ensure spare parameters exist on splat_room_architecture for interactive live tweaking
    ptg = room_node.parmTemplateGroup()
    parms_to_add = [
        hou.ToggleParmTemplate("double_sided", "Double-Sided Walls", default_value=bool(double_sided)),
        hou.ToggleParmTemplate("room_shadows", "Room Casts Shadows", default_value=bool(room_shadows)),
        hou.ToggleParmTemplate("room_invisible", "Invisible Room", default_value=bool(room_invisible)),
    ]
    modified_ptg = False
    for pt in parms_to_add:
        if not ptg.find(pt.name()):
            ptg.append(pt)
            modified_ptg = True
    if modified_ptg:
        room_node.setParmTemplateGroup(ptg)

    if room_node.parm("double_sided"): room_node.parm("double_sided").set(bool(double_sided))
    if room_node.parm("room_shadows"): room_node.parm("room_shadows").set(bool(room_shadows))
    if room_node.parm("room_invisible"): room_node.parm("room_invisible").set(bool(room_invisible))

    # Ensure conflicting legacy projection nodes (hdri_match_projection, hdri_match_materials) are bypassed
    for p_name in ("hdri_match_projection", "hdri_match_materials"):
        pn = stage_node.node(p_name)
        if pn:
            pn.bypass(True)

    if "all" in str(renderer_target).lower():
        roughness = 1.0

    x_min = float(room_data.get("x_min", -5.0))
    x_max = float(room_data.get("x_max", 5.0))
    z_min = float(room_data.get("z_min", -5.0))
    z_max = float(room_data.get("z_max", 5.0))
    y_floor = float(room_data.get("floor_y", 0.0))
    y_ceil = float(room_data.get("ceil_y", 3.0))
    windows = room_data.get("windows", [])
    props = room_data.get("props", []) if build_props else []
    cam_x, cam_y, cam_z = probe_pos
    if cam_y > y_ceil + 0.5 or cam_y < y_floor - 0.5:
        c = room_data.get("center", [0.0, 1.45, 0.0])
        cam_x = float(c[0])
        cam_y = float(y_floor) + 1.45
        cam_z = float(c[2])
    raw_hdri_texture = hdri_texture.replace(chr(92), "/") if hdri_texture else ""
    cand_albedo = raw_hdri_texture
    tex_file = raw_hdri_texture

    if project_hdri and tex_file and os.path.isfile(tex_file):
        stem, ext = os.path.splitext(tex_file)
        if mat_mode == "emissive":
            # In emissive mode, NEVER compress to albedo! Preserve raw scene-linear HDR radiance (> 10.0-35.0+)
            cand_albedo = tex_file
        elif "_albedo" in stem:
            cand_albedo = tex_file
        else:
            cand_albedo = f"{stem}_albedo{ext}"
            if not os.path.isfile(cand_albedo):
                try:
                    for p_cand in [os.environ.get("HDRI_MATCH_PLATE_ROOT", ""), "E:/PROJECTS/HDRI_Match_Plate", "e:/PROJECTS/HDRI_Match_Plate"]:
                        if p_cand and os.path.isdir(p_cand) and p_cand not in sys.path:
                            sys.path.insert(0, p_cand)
                    from hdri_match_solaris.gaussian_splat import GaussianSplatBaker
                    raw_arr = GaussianSplatBaker.load_exr(tex_file)
                    if raw_arr is not None:
                        # Physically plausible albedo compression: maps 0->0, 1->0.5, 35->0.82
                        albedo_arr = np.clip(raw_arr / (1.0 + raw_arr * 0.35) * 0.70, 0.0, 0.85)
                        GaussianSplatBaker.save_exr(albedo_arr, cand_albedo)
                except Exception as ex:
                    print(f"[HDRI Match] Albedo generation warning: {ex}")
                    cand_albedo = tex_file
        tex_file = cand_albedo

    clean_planar_dict = {}
    if planar_textures_dict:
        for k, v in planar_textures_dict.items():
            if v and isinstance(v, str):
                clean_planar_dict[k] = v.replace(chr(92), "/")

    py_lines = [
        'import os, sys, math, json, numpy as np',
        'import hou',
        'from pxr import Usd, UsdGeom, UsdShade, UsdLux, Gf, Vt, Sdf',
        '',
        'for cand in [os.environ.get("HDRI_MATCH_PLATE_ROOT", ""), "E:/PROJECTS/HDRI_Match_Plate", "e:/PROJECTS/HDRI_Match_Plate", "E:/PROJECTS/HDRI_MATCH_SOLARIS/python", "e:/PROJECTS/HDRI_MATCH_SOLARIS/python"]:',
        '    if cand and os.path.isdir(cand) and cand not in sys.path:',
        '        sys.path.insert(0, cand)',
        'try:',
        '    from hdri_match.io.loader import load_exr_to_numpy',
        'except Exception:',
        '    load_exr_to_numpy = None',
        '',
        'stage = hou.pwd().editableStage()',
        'room_scope = UsdGeom.Scope.Define(stage, "/stage/room")',
        '# Explicitly prune /stage/room/props so props geometry is completely removed',
        'if stage.GetPrimAtPath("/stage/room/props"):',
        '    stage.RemovePrim("/stage/room/props")',
        '',
        f'x_min = float({x_min})',
        f'x_max = float({x_max})',
        f'z_min = float({z_min})',
        f'z_max = float({z_max})',
        f'y_floor = float({y_floor})',
        f'y_ceil = float({y_ceil})',
        f'cam_x = float({cam_x})',
        f'cam_y = float({cam_y})',
        f'cam_z = float({cam_z})',
        f'double_sided = bool(hou.pwd().parm("double_sided").eval() if hou.pwd().parm("double_sided") else {double_sided})',
        f'room_shadows = bool(hou.pwd().parm("room_shadows").eval() if hou.pwd().parm("room_shadows") else {room_shadows})',
        f'room_invisible = bool(hou.pwd().parm("room_invisible").eval() if hou.pwd().parm("room_invisible") else {room_invisible})',
        f'tex_file = r"{tex_file}"',
        f'roughness = float({roughness})',
        f'project_hdri = bool({project_hdri})',
        f'floor_mode = "{floor_mode}"',
        f'mat_mode = "{mat_mode}"',
        f'emissive_mult = float({emissive_mult})',
        f'use_planar = bool({use_planar_textures})',
        f'planar_textures = {repr(clean_planar_dict)}',
        f'renderer_target = "{renderer_target}"',
        '',
        '# Load HDRI image array to sample vertex colors for instant OpenGL viewport display',
        'hdri_arr = None',
        'if project_hdri and tex_file and os.path.isfile(tex_file) and load_exr_to_numpy:',
        '    try:',
        '        hdri_arr = load_exr_to_numpy(tex_file)',
        '    except Exception:',
        '        hdri_arr = None',
        'img_h, img_w = (hdri_arr.shape[:2]) if hdri_arr is not None else (512, 1024)',
        '',
        'def project_point(px, py, pz):',
        '    dx = px - cam_x',
        '    dy = py - cam_y',
        '    dz = pz - cam_z',
        '    dist = math.sqrt(dx*dx + dy*dy + dz*dz)',
        '    if dist < 1e-6:',
        '        dist = 1e-6',
        '    vx = dx / dist',
        '    vy = dy / dist',
        '    vz = dz / dist',
        '    u = (math.atan2(-vx, vz) / (2.0 * math.pi)) % 1.0',
        '    v = 0.5 - (math.asin(max(-1.0, min(1.0, vy))) / math.pi)',
        '    return float(u), float(v)',
        '',
        'def add_grid_to_buffers(pts, normals, face_counts, face_indices, face_uvs, colors, corners, normal, nx=6, ny=6, uv_bounds=None, fallback_col=(0.5, 0.5, 0.5)):',
        '    P00, P10, P11, P01 = corners',
        '    grid_indices = np.zeros((ny + 1, nx + 1), dtype=int)',
        '    for j in range(ny + 1):',
        '        tj = float(j) / float(ny)',
        '        for i in range(nx + 1):',
        '            ti = float(i) / float(nx)',
        '            P = (1.0 - ti) * (1.0 - tj) * P00 + ti * (1.0 - tj) * P10 + ti * tj * P11 + (1.0 - ti) * tj * P01',
        '            grid_indices[j, i] = len(pts)',
        '            pts.append(Gf.Vec3f(float(P[0]), float(P[1]), float(P[2])))',
        '            normals.append(Gf.Vec3f(float(normal[0]), float(normal[1]), float(normal[2])))',
        '            u, v = project_point(P[0], P[1], P[2])',
        '            if hdri_arr is not None:',
        '                px_x = int(np.clip(u * img_w, 0, img_w - 1))',
        '                px_y = int(np.clip(v * img_h, 0, img_h - 1))',
        '                colors.append(Gf.Vec3f(float(np.nan_to_num(hdri_arr[px_y, px_x, 0])),',
        '                                       float(np.nan_to_num(hdri_arr[px_y, px_x, 1])),',
        '                                       float(np.nan_to_num(hdri_arr[px_y, px_x, 2]))))',
        '            else:',
        '                colors.append(Gf.Vec3f(float(fallback_col[0]), float(fallback_col[1]), float(fallback_col[2])))',
        '',
        '    for j in range(ny):',
        '        for i in range(nx):',
        '            idx0 = int(grid_indices[j, i])',
        '            idx1 = int(grid_indices[j, i + 1])',
        '            idx2 = int(grid_indices[j + 1, i + 1])',
        '            idx3 = int(grid_indices[j + 1, i])',
        '            face_counts.append(4)',
        '            face_indices.extend([idx0, idx1, idx2, idx3])',
        '',
        '            if uv_bounds is not None:',
        '                u_min_b, u_max_b, v_min_b, v_max_b = uv_bounds',
        '                ti0, tj0 = float(i) / float(nx), float(j) / float(ny)',
        '                ti1, tj1 = float(i + 1) / float(nx), float(j + 1) / float(ny)',
        '                face_uvs.extend([',
        '                    Gf.Vec2f(float(u_min_b + ti0 * (u_max_b - u_min_b)), float(v_min_b + tj0 * (v_max_b - v_min_b))),',
        '                    Gf.Vec2f(float(u_min_b + ti1 * (u_max_b - u_min_b)), float(v_min_b + tj0 * (v_max_b - v_min_b))),',
        '                    Gf.Vec2f(float(u_min_b + ti1 * (u_max_b - u_min_b)), float(v_min_b + tj1 * (v_max_b - v_min_b))),',
        '                    Gf.Vec2f(float(u_min_b + ti0 * (u_max_b - u_min_b)), float(v_min_b + tj1 * (v_max_b - v_min_b)))',
        '                ])',
        '            else:',
        '                p0 = pts[idx0]',
        '                p1 = pts[idx1]',
        '                p2 = pts[idx2]',
        '                p3 = pts[idx3]',
        '',
        '                u0, v0 = project_point(p0[0], p0[1], p0[2])',
        '                u1, v1 = project_point(p1[0], p1[1], p1[2])',
        '                u2, v2 = project_point(p2[0], p2[1], p2[2])',
        '                u3, v3 = project_point(p3[0], p3[1], p3[2])',
        '',
        '                u_vals = [u0, u1, u2, u3]',
        '                if max(u_vals) - min(u_vals) > 0.5:',
        '                    if u0 < 0.5: u0 += 1.0',
        '                    if u1 < 0.5: u1 += 1.0',
        '                    if u2 < 0.5: u2 += 1.0',
        '                    if u3 < 0.5: u3 += 1.0',
        '',
        '                face_uvs.extend([Gf.Vec2f(float(u0), float(1.0 - v0)),',
        '                                 Gf.Vec2f(float(u1), float(1.0 - v1)),',
        '                                 Gf.Vec2f(float(u2), float(1.0 - v2)),',
        '                                 Gf.Vec2f(float(u3), float(1.0 - v3))])',
        '',
        'def sample_point_color(px, py, pz, fallback_col=(0.65, 0.65, 0.65)):',
        '    if hdri_arr is not None:',
        '        u, v = project_point(px, py, pz)',
        '        px_x = int(np.clip(u * img_w, 0, img_w - 1))',
        '        px_y = int(np.clip(v * img_h, 0, img_h - 1))',
        '        return Gf.Vec3f(float(np.nan_to_num(hdri_arr[px_y, px_x, 0])),',
        '                        float(np.nan_to_num(hdri_arr[px_y, px_x, 1])),',
        '                        float(np.nan_to_num(hdri_arr[px_y, px_x, 2])))',
        '    return Gf.Vec3f(float(fallback_col[0]), float(fallback_col[1]), float(fallback_col[2]))',
        '',
        'def compute_projected_face_uvs(face_pts):',
        '    u_vals, v_vals = [], []',
        '    for pt in face_pts:',
        '        u, v = project_point(pt[0], pt[1], pt[2])',
        '        u_vals.append(u)',
        '        v_vals.append(v)',
        '    if max(u_vals) - min(u_vals) > 0.5:',
        '        for k in range(len(u_vals)):',
        '            if u_vals[k] < 0.5:',
        '                u_vals[k] += 1.0',
        '    return [Gf.Vec2f(float(u), float(1.0 - v)) for u, v in zip(u_vals, v_vals)]',
        '',
        'def add_box_face_grid(pts, normals, face_counts, face_indices, face_uvs, colors, corners, normal, base_col, nx=4, ny=4, uv_rect=None):',
        '    add_grid_to_buffers(pts, normals, face_counts, face_indices, face_uvs, colors, corners, normal, nx=nx, ny=ny, uv_bounds=None, fallback_col=base_col)',
        '',
    ]

    # 1. Floor
    if build_floor:
        py_lines.extend([
            '# --- 1. Floor ---',
            'floor_mesh = UsdGeom.Mesh.Define(stage, "/stage/room/floor")',
            'f_pts, f_nrms, f_cnts, f_idxs, f_uvs, f_cols = [], [], [], [], [], []',
            'c_floor = [',
            '    np.array([x_min, y_floor, z_max]),',
            '    np.array([x_max, y_floor, z_max]),',
            '    np.array([x_max, y_floor, z_min]),',
            '    np.array([x_min, y_floor, z_min]),',
            ']',
            'fl_uv_bounds = (0.0, 1.0, 0.0, 1.0) if use_planar else None',
            'add_grid_to_buffers(f_pts, f_nrms, f_cnts, f_idxs, f_uvs, f_cols, c_floor, (0.0, 1.0, 0.0), nx=24, ny=24, uv_bounds=fl_uv_bounds)',
            'floor_mesh.CreatePointsAttr(Vt.Vec3fArray(f_pts))',
            'floor_mesh.CreateFaceVertexCountsAttr(Vt.IntArray(f_cnts))',
            'floor_mesh.CreateFaceVertexIndicesAttr(Vt.IntArray(f_idxs))',
            'floor_mesh.CreateNormalsAttr(Vt.Vec3fArray(f_nrms))',
            'floor_mesh.SetNormalsInterpolation(UsdGeom.Tokens.vertex)',
            'floor_mesh.CreateDoubleSidedAttr(True)',
            'floor_mesh.CreateSubdivisionSchemeAttr().Set(UsdGeom.Tokens.none)',
            'f_pv_api = UsdGeom.PrimvarsAPI(floor_mesh.GetPrim())',
            'f_st_pv = f_pv_api.CreatePrimvar("st", Sdf.ValueTypeNames.TexCoord2fArray, UsdGeom.Tokens.faceVarying)',
            'f_st_pv.Set(Vt.Vec2fArray(f_uvs))',
            'f_uv_pv = f_pv_api.CreatePrimvar("uv", Sdf.ValueTypeNames.TexCoord2fArray, UsdGeom.Tokens.faceVarying)',
            'f_uv_pv.Set(Vt.Vec2fArray(f_uvs))',
            'floor_mesh.CreateDisplayColorPrimvar(UsdGeom.Tokens.vertex).Set(Vt.Vec3fArray(f_cols))',
            'floor_mesh.CreateDisplayOpacityPrimvar(UsdGeom.Tokens.vertex).Set(Vt.FloatArray([1.0] * len(f_pts)))',
            'f_prim = floor_mesh.GetPrim()',
            'f_prim.CreateAttribute("primvars:karma:object:dicing:quality", Sdf.ValueTypeNames.Float, False).Set(0.0)',
            'if floor_mode == "matte":',
            '    f_prim.CreateAttribute("primvars:karma:object:rendervisibility", Sdf.ValueTypeNames.String, False).Set("+shadow")',
            '    f_prim.CreateAttribute("primvars:arnold:visibility:shadow", Sdf.ValueTypeNames.Bool, False).Set(True)',
            '    f_prim.CreateAttribute("primvars:redshift:object:MESHFLAG_MATTE", Sdf.ValueTypeNames.Bool, False).Set(True)',
            'else:',
            '    # Visible Floor: Renders as opaque physical surface with projected HDRI floor texture',
            '    f_prim.CreateAttribute("primvars:arnold:opaque", Sdf.ValueTypeNames.Bool, False).Set(True)',
            '    f_prim.CreateAttribute("arnold:opaque", Sdf.ValueTypeNames.Bool, False).Set(True)',
            '    f_prim.CreateAttribute("primvars:arnold:visibility:shadow", Sdf.ValueTypeNames.Bool, False).Set(bool(room_shadows))',
            '    f_prim.CreateAttribute("arnold:visibility:shadow", Sdf.ValueTypeNames.Int, False).Set(1 if room_shadows else 0)',
            '    f_prim.CreateAttribute("primvars:redshift:object:MESHFLAG_SHADOWCASTER", Sdf.ValueTypeNames.Bool, False).Set(bool(room_shadows))',
            '    f_prim.CreateAttribute("redshift:object:MESHFLAG_SHADOWCASTER", Sdf.ValueTypeNames.Bool, False).Set(bool(room_shadows))',
            '    if room_invisible:',
            '        karma_vis = "-primary" if room_shadows else "-primary & -shadow"',
            '        f_prim.CreateAttribute("primvars:karma:object:rendervisibility", Sdf.ValueTypeNames.String, False).Set(karma_vis)',
            '        f_prim.CreateAttribute("karma:object:rendervisibility", Sdf.ValueTypeNames.String, False).Set(karma_vis)',
            '        f_prim.CreateAttribute("primvars:arnold:visibility:camera", Sdf.ValueTypeNames.Bool, False).Set(False)',
            '        f_prim.CreateAttribute("arnold:visibility:camera", Sdf.ValueTypeNames.Int, False).Set(0)',
            '        f_prim.CreateAttribute("primvars:arnold:visibility:diffuse_reflect", Sdf.ValueTypeNames.Bool, False).Set(True)',
            '        f_prim.CreateAttribute("arnold:visibility:diffuse_reflect", Sdf.ValueTypeNames.Int, False).Set(1)',
            '        f_prim.CreateAttribute("primvars:arnold:visibility:specular_reflect", Sdf.ValueTypeNames.Bool, False).Set(True)',
            '        f_prim.CreateAttribute("arnold:visibility:specular_reflect", Sdf.ValueTypeNames.Int, False).Set(1)',
            '        f_prim.CreateAttribute("primvars:arnold:visibility:diffuse_transmit", Sdf.ValueTypeNames.Bool, False).Set(True)',
            '        f_prim.CreateAttribute("arnold:visibility:diffuse_transmit", Sdf.ValueTypeNames.Int, False).Set(1)',
            '        f_prim.CreateAttribute("primvars:arnold:visibility:specular_transmit", Sdf.ValueTypeNames.Bool, False).Set(True)',
            '        f_prim.CreateAttribute("arnold:visibility:specular_transmit", Sdf.ValueTypeNames.Int, False).Set(1)',
            '        f_prim.CreateAttribute("primvars:arnold:visibility:volume", Sdf.ValueTypeNames.Bool, False).Set(True)',
            '        f_prim.CreateAttribute("arnold:visibility:volume", Sdf.ValueTypeNames.Int, False).Set(1)',
            '        f_prim.CreateAttribute("primvars:arnold:visibility", Sdf.ValueTypeNames.UChar, False).Set((2 if room_shadows else 0) | 4 | 8 | 16 | 32 | 64)',
            '        f_prim.CreateAttribute("arnold:visibility", Sdf.ValueTypeNames.Int, False).Set((2 if room_shadows else 0) | 4 | 8 | 16 | 32 | 64)',
            '        f_prim.CreateAttribute("primvars:redshift:object:MESHFLAG_PRIMARYRAYVISIBLE", Sdf.ValueTypeNames.Bool, False).Set(False)',
            '        f_prim.CreateAttribute("redshift:object:MESHFLAG_PRIMARYRAYVISIBLE", Sdf.ValueTypeNames.Bool, False).Set(False)',
            '        f_prim.CreateAttribute("primvars:redshift:object:MESHFLAG_PRIMARYRAYVIS", Sdf.ValueTypeNames.Bool, False).Set(False)',
            '        f_prim.CreateAttribute("redshift:object:MESHFLAG_PRIMARYRAYVIS", Sdf.ValueTypeNames.Bool, False).Set(False)',
            '        f_prim.CreateAttribute("primvars:redshift:object:MESHFLAG_REFLECTIONVISIBLE", Sdf.ValueTypeNames.Bool, False).Set(True)',
            '        f_prim.CreateAttribute("redshift:object:MESHFLAG_REFLECTIONVISIBLE", Sdf.ValueTypeNames.Bool, False).Set(True)',
            '        f_prim.CreateAttribute("primvars:redshift:object:MESHFLAG_REFLECTIONVIS", Sdf.ValueTypeNames.Bool, False).Set(True)',
            '        f_prim.CreateAttribute("redshift:object:MESHFLAG_REFLECTIONVIS", Sdf.ValueTypeNames.Bool, False).Set(True)',
            '        f_prim.CreateAttribute("primvars:redshift:object:MESHFLAG_REFRACTIONVISIBLE", Sdf.ValueTypeNames.Bool, False).Set(True)',
            '        f_prim.CreateAttribute("redshift:object:MESHFLAG_REFRACTIONVISIBLE", Sdf.ValueTypeNames.Bool, False).Set(True)',
            '        f_prim.CreateAttribute("primvars:redshift:object:MESHFLAG_REFRACTIONVIS", Sdf.ValueTypeNames.Bool, False).Set(True)',
            '        f_prim.CreateAttribute("redshift:object:MESHFLAG_REFRACTIONVIS", Sdf.ValueTypeNames.Bool, False).Set(True)',
            '        f_prim.CreateAttribute("primvars:redshift:object:MESHFLAG_GIVISIBLE", Sdf.ValueTypeNames.Bool, False).Set(True)',
            '        f_prim.CreateAttribute("redshift:object:MESHFLAG_GIVISIBLE", Sdf.ValueTypeNames.Bool, False).Set(True)',
            '        f_prim.CreateAttribute("primvars:redshift:object:MESHFLAG_GIVIS", Sdf.ValueTypeNames.Bool, False).Set(True)',
            '        f_prim.CreateAttribute("redshift:object:MESHFLAG_GIVIS", Sdf.ValueTypeNames.Bool, False).Set(True)',
            '        f_prim.CreateAttribute("primvars:redshift:object:MESHFLAG_GICASTER", Sdf.ValueTypeNames.Bool, False).Set(True)',
            '        f_prim.CreateAttribute("redshift:object:MESHFLAG_GICASTER", Sdf.ValueTypeNames.Bool, False).Set(True)',
            '    else:',
            '        karma_vis = "*" if room_shadows else "-shadow"',
            '        f_prim.CreateAttribute("primvars:karma:object:rendervisibility", Sdf.ValueTypeNames.String, False).Set(karma_vis)',
            '        f_prim.CreateAttribute("karma:object:rendervisibility", Sdf.ValueTypeNames.String, False).Set(karma_vis)',
            '        f_prim.CreateAttribute("primvars:arnold:visibility:camera", Sdf.ValueTypeNames.Bool, False).Set(True)',
            '        f_prim.CreateAttribute("arnold:visibility:camera", Sdf.ValueTypeNames.Int, False).Set(1)',
            '        f_prim.CreateAttribute("primvars:arnold:visibility:diffuse_reflect", Sdf.ValueTypeNames.Bool, False).Set(True)',
            '        f_prim.CreateAttribute("arnold:visibility:diffuse_reflect", Sdf.ValueTypeNames.Int, False).Set(1)',
            '        f_prim.CreateAttribute("primvars:arnold:visibility:specular_reflect", Sdf.ValueTypeNames.Bool, False).Set(True)',
            '        f_prim.CreateAttribute("arnold:visibility:specular_reflect", Sdf.ValueTypeNames.Int, False).Set(1)',
            '        f_prim.CreateAttribute("primvars:arnold:visibility:diffuse_transmit", Sdf.ValueTypeNames.Bool, False).Set(True)',
            '        f_prim.CreateAttribute("arnold:visibility:diffuse_transmit", Sdf.ValueTypeNames.Int, False).Set(1)',
            '        f_prim.CreateAttribute("primvars:arnold:visibility:specular_transmit", Sdf.ValueTypeNames.Bool, False).Set(True)',
            '        f_prim.CreateAttribute("arnold:visibility:specular_transmit", Sdf.ValueTypeNames.Int, False).Set(1)',
            '        f_prim.CreateAttribute("primvars:arnold:visibility:volume", Sdf.ValueTypeNames.Bool, False).Set(True)',
            '        f_prim.CreateAttribute("arnold:visibility:volume", Sdf.ValueTypeNames.Int, False).Set(1)',
            '        f_prim.CreateAttribute("primvars:arnold:visibility", Sdf.ValueTypeNames.UChar, False).Set(255 if room_shadows else 253)',
            '        f_prim.CreateAttribute("arnold:visibility", Sdf.ValueTypeNames.Int, False).Set(255 if room_shadows else 253)',
            '        f_prim.CreateAttribute("primvars:redshift:object:MESHFLAG_PRIMARYRAYVISIBLE", Sdf.ValueTypeNames.Bool, False).Set(True)',
            '        f_prim.CreateAttribute("redshift:object:MESHFLAG_PRIMARYRAYVISIBLE", Sdf.ValueTypeNames.Bool, False).Set(True)',
            '        f_prim.CreateAttribute("primvars:redshift:object:MESHFLAG_PRIMARYRAYVIS", Sdf.ValueTypeNames.Bool, False).Set(True)',
            '        f_prim.CreateAttribute("redshift:object:MESHFLAG_PRIMARYRAYVIS", Sdf.ValueTypeNames.Bool, False).Set(True)',
            '        f_prim.CreateAttribute("primvars:redshift:object:MESHFLAG_REFLECTIONVISIBLE", Sdf.ValueTypeNames.Bool, False).Set(True)',
            '        f_prim.CreateAttribute("redshift:object:MESHFLAG_REFLECTIONVISIBLE", Sdf.ValueTypeNames.Bool, False).Set(True)',
            '        f_prim.CreateAttribute("primvars:redshift:object:MESHFLAG_REFLECTIONVIS", Sdf.ValueTypeNames.Bool, False).Set(True)',
            '        f_prim.CreateAttribute("redshift:object:MESHFLAG_REFLECTIONVIS", Sdf.ValueTypeNames.Bool, False).Set(True)',
            '        f_prim.CreateAttribute("primvars:redshift:object:MESHFLAG_REFRACTIONVISIBLE", Sdf.ValueTypeNames.Bool, False).Set(True)',
            '        f_prim.CreateAttribute("redshift:object:MESHFLAG_REFRACTIONVISIBLE", Sdf.ValueTypeNames.Bool, False).Set(True)',
            '        f_prim.CreateAttribute("primvars:redshift:object:MESHFLAG_REFRACTIONVIS", Sdf.ValueTypeNames.Bool, False).Set(True)',
            '        f_prim.CreateAttribute("redshift:object:MESHFLAG_REFRACTIONVIS", Sdf.ValueTypeNames.Bool, False).Set(True)',
            '        f_prim.CreateAttribute("primvars:redshift:object:MESHFLAG_GIVISIBLE", Sdf.ValueTypeNames.Bool, False).Set(True)',
            '        f_prim.CreateAttribute("redshift:object:MESHFLAG_GIVISIBLE", Sdf.ValueTypeNames.Bool, False).Set(True)',
            '        f_prim.CreateAttribute("primvars:redshift:object:MESHFLAG_GIVIS", Sdf.ValueTypeNames.Bool, False).Set(True)',
            '        f_prim.CreateAttribute("redshift:object:MESHFLAG_GIVIS", Sdf.ValueTypeNames.Bool, False).Set(True)',
            '        f_prim.CreateAttribute("primvars:redshift:object:MESHFLAG_GICASTER", Sdf.ValueTypeNames.Bool, False).Set(True)',
            '        f_prim.CreateAttribute("redshift:object:MESHFLAG_GICASTER", Sdf.ValueTypeNames.Bool, False).Set(True)',
            '',
        ])

    # 2. Ceiling
    if build_ceiling:
        py_lines.extend([
            '# --- 2. Ceiling ---',
            'ceil_mesh = UsdGeom.Mesh.Define(stage, "/stage/room/ceiling")',
            'c_pts, c_nrms, c_cnts, c_idxs, c_uvs, c_cols = [], [], [], [], [], []',
            'c_ceil = [',
            '    np.array([x_min, y_ceil, z_min]),',
            '    np.array([x_max, y_ceil, z_min]),',
            '    np.array([x_max, y_ceil, z_max]),',
            '    np.array([x_min, y_ceil, z_max]),',
            ']',
            'cl_uv_bounds = (0.0, 1.0, 0.0, 1.0) if use_planar else None',
            'add_grid_to_buffers(c_pts, c_nrms, c_cnts, c_idxs, c_uvs, c_cols, c_ceil, (0.0, -1.0, 0.0), nx=24, ny=24, uv_bounds=cl_uv_bounds)',
            'ceil_mesh.CreatePointsAttr(Vt.Vec3fArray(c_pts))',
            'ceil_mesh.CreateFaceVertexCountsAttr(Vt.IntArray(c_cnts))',
            'ceil_mesh.CreateFaceVertexIndicesAttr(Vt.IntArray(c_idxs))',
            'ceil_mesh.CreateNormalsAttr(Vt.Vec3fArray(c_nrms))',
            'ceil_mesh.SetNormalsInterpolation(UsdGeom.Tokens.vertex)',
            'ceil_mesh.CreateDoubleSidedAttr(double_sided)',
            'ceil_mesh.CreateSubdivisionSchemeAttr().Set(UsdGeom.Tokens.none)',
            'c_pv_api = UsdGeom.PrimvarsAPI(ceil_mesh.GetPrim())',
            'c_st_pv = c_pv_api.CreatePrimvar("st", Sdf.ValueTypeNames.TexCoord2fArray, UsdGeom.Tokens.faceVarying)',
            'c_st_pv.Set(Vt.Vec2fArray(c_uvs))',
            'c_uv_pv = c_pv_api.CreatePrimvar("uv", Sdf.ValueTypeNames.TexCoord2fArray, UsdGeom.Tokens.faceVarying)',
            'c_uv_pv.Set(Vt.Vec2fArray(c_uvs))',
            'ceil_mesh.CreateDisplayColorPrimvar(UsdGeom.Tokens.vertex).Set(Vt.Vec3fArray(c_cols))',
            'ceil_mesh.CreateDisplayOpacityPrimvar(UsdGeom.Tokens.vertex).Set(Vt.FloatArray([1.0] * len(c_pts)))',
            'c_prim = ceil_mesh.GetPrim()',
            'c_prim.CreateAttribute("primvars:karma:object:dicing:quality", Sdf.ValueTypeNames.Float, False).Set(0.0)',
            'c_prim.CreateAttribute("primvars:arnold:opaque", Sdf.ValueTypeNames.Bool, False).Set(True)',
            'c_prim.CreateAttribute("arnold:opaque", Sdf.ValueTypeNames.Bool, False).Set(True)',
            'c_prim.CreateAttribute("primvars:arnold:visibility:shadow", Sdf.ValueTypeNames.Bool, False).Set(bool(room_shadows))',
            'c_prim.CreateAttribute("arnold:visibility:shadow", Sdf.ValueTypeNames.Int, False).Set(1 if room_shadows else 0)',
            'c_prim.CreateAttribute("primvars:redshift:object:MESHFLAG_SHADOWCASTER", Sdf.ValueTypeNames.Bool, False).Set(bool(room_shadows))',
            'c_prim.CreateAttribute("redshift:object:MESHFLAG_SHADOWCASTER", Sdf.ValueTypeNames.Bool, False).Set(bool(room_shadows))',
            'if room_invisible:',
            '    karma_vis = "-primary" if room_shadows else "-primary & -shadow"',
            '    c_prim.CreateAttribute("primvars:karma:object:rendervisibility", Sdf.ValueTypeNames.String, False).Set(karma_vis)',
            '    c_prim.CreateAttribute("karma:object:rendervisibility", Sdf.ValueTypeNames.String, False).Set(karma_vis)',
            '    c_prim.CreateAttribute("primvars:arnold:visibility:camera", Sdf.ValueTypeNames.Bool, False).Set(False)',
            '    c_prim.CreateAttribute("arnold:visibility:camera", Sdf.ValueTypeNames.Int, False).Set(0)',
            '    c_prim.CreateAttribute("primvars:arnold:visibility:diffuse_reflect", Sdf.ValueTypeNames.Bool, False).Set(True)',
            '    c_prim.CreateAttribute("arnold:visibility:diffuse_reflect", Sdf.ValueTypeNames.Int, False).Set(1)',
            '    c_prim.CreateAttribute("primvars:arnold:visibility:specular_reflect", Sdf.ValueTypeNames.Bool, False).Set(True)',
            '    c_prim.CreateAttribute("arnold:visibility:specular_reflect", Sdf.ValueTypeNames.Int, False).Set(1)',
            '    c_prim.CreateAttribute("primvars:arnold:visibility:diffuse_transmit", Sdf.ValueTypeNames.Bool, False).Set(True)',
            '    c_prim.CreateAttribute("arnold:visibility:diffuse_transmit", Sdf.ValueTypeNames.Int, False).Set(1)',
            '    c_prim.CreateAttribute("primvars:arnold:visibility:specular_transmit", Sdf.ValueTypeNames.Bool, False).Set(True)',
            '    c_prim.CreateAttribute("arnold:visibility:specular_transmit", Sdf.ValueTypeNames.Int, False).Set(1)',
            '    c_prim.CreateAttribute("primvars:arnold:visibility:volume", Sdf.ValueTypeNames.Bool, False).Set(True)',
            '    c_prim.CreateAttribute("arnold:visibility:volume", Sdf.ValueTypeNames.Int, False).Set(1)',
            '    c_prim.CreateAttribute("primvars:arnold:visibility", Sdf.ValueTypeNames.UChar, False).Set((2 if room_shadows else 0) | 4 | 8 | 16 | 32 | 64)',
            '    c_prim.CreateAttribute("arnold:visibility", Sdf.ValueTypeNames.Int, False).Set((2 if room_shadows else 0) | 4 | 8 | 16 | 32 | 64)',
            '    c_prim.CreateAttribute("primvars:redshift:object:MESHFLAG_PRIMARYRAYVISIBLE", Sdf.ValueTypeNames.Bool, False).Set(False)',
            '    c_prim.CreateAttribute("redshift:object:MESHFLAG_PRIMARYRAYVISIBLE", Sdf.ValueTypeNames.Bool, False).Set(False)',
            '    c_prim.CreateAttribute("primvars:redshift:object:MESHFLAG_PRIMARYRAYVIS", Sdf.ValueTypeNames.Bool, False).Set(False)',
            '    c_prim.CreateAttribute("redshift:object:MESHFLAG_PRIMARYRAYVIS", Sdf.ValueTypeNames.Bool, False).Set(False)',
            '    c_prim.CreateAttribute("primvars:redshift:object:MESHFLAG_REFLECTIONVISIBLE", Sdf.ValueTypeNames.Bool, False).Set(True)',
            '    c_prim.CreateAttribute("redshift:object:MESHFLAG_REFLECTIONVISIBLE", Sdf.ValueTypeNames.Bool, False).Set(True)',
            '    c_prim.CreateAttribute("primvars:redshift:object:MESHFLAG_REFLECTIONVIS", Sdf.ValueTypeNames.Bool, False).Set(True)',
            '    c_prim.CreateAttribute("redshift:object:MESHFLAG_REFLECTIONVIS", Sdf.ValueTypeNames.Bool, False).Set(True)',
            '    c_prim.CreateAttribute("primvars:redshift:object:MESHFLAG_REFRACTIONVISIBLE", Sdf.ValueTypeNames.Bool, False).Set(True)',
            '    c_prim.CreateAttribute("redshift:object:MESHFLAG_REFRACTIONVISIBLE", Sdf.ValueTypeNames.Bool, False).Set(True)',
            '    c_prim.CreateAttribute("primvars:redshift:object:MESHFLAG_REFRACTIONVIS", Sdf.ValueTypeNames.Bool, False).Set(True)',
            '    c_prim.CreateAttribute("redshift:object:MESHFLAG_REFRACTIONVIS", Sdf.ValueTypeNames.Bool, False).Set(True)',
            '    c_prim.CreateAttribute("primvars:redshift:object:MESHFLAG_GIVISIBLE", Sdf.ValueTypeNames.Bool, False).Set(True)',
            '    c_prim.CreateAttribute("redshift:object:MESHFLAG_GIVISIBLE", Sdf.ValueTypeNames.Bool, False).Set(True)',
            '    c_prim.CreateAttribute("primvars:redshift:object:MESHFLAG_GIVIS", Sdf.ValueTypeNames.Bool, False).Set(True)',
            '    c_prim.CreateAttribute("redshift:object:MESHFLAG_GIVIS", Sdf.ValueTypeNames.Bool, False).Set(True)',
            '    c_prim.CreateAttribute("primvars:redshift:object:MESHFLAG_GICASTER", Sdf.ValueTypeNames.Bool, False).Set(True)',
            '    c_prim.CreateAttribute("redshift:object:MESHFLAG_GICASTER", Sdf.ValueTypeNames.Bool, False).Set(True)',
            'else:',
            '    karma_vis = "*" if room_shadows else "-shadow"',
            '    c_prim.CreateAttribute("primvars:karma:object:rendervisibility", Sdf.ValueTypeNames.String, False).Set(karma_vis)',
            '    c_prim.CreateAttribute("karma:object:rendervisibility", Sdf.ValueTypeNames.String, False).Set(karma_vis)',
            '    c_prim.CreateAttribute("primvars:arnold:visibility:camera", Sdf.ValueTypeNames.Bool, False).Set(True)',
            '    c_prim.CreateAttribute("arnold:visibility:camera", Sdf.ValueTypeNames.Int, False).Set(1)',
            '    c_prim.CreateAttribute("primvars:arnold:visibility:diffuse_reflect", Sdf.ValueTypeNames.Bool, False).Set(True)',
            '    c_prim.CreateAttribute("arnold:visibility:diffuse_reflect", Sdf.ValueTypeNames.Int, False).Set(1)',
            '    c_prim.CreateAttribute("primvars:arnold:visibility:specular_reflect", Sdf.ValueTypeNames.Bool, False).Set(True)',
            '    c_prim.CreateAttribute("arnold:visibility:specular_reflect", Sdf.ValueTypeNames.Int, False).Set(1)',
            '    c_prim.CreateAttribute("primvars:arnold:visibility:diffuse_transmit", Sdf.ValueTypeNames.Bool, False).Set(True)',
            '    c_prim.CreateAttribute("arnold:visibility:diffuse_transmit", Sdf.ValueTypeNames.Int, False).Set(1)',
            '    c_prim.CreateAttribute("primvars:arnold:visibility:specular_transmit", Sdf.ValueTypeNames.Bool, False).Set(True)',
            '    c_prim.CreateAttribute("arnold:visibility:specular_transmit", Sdf.ValueTypeNames.Int, False).Set(1)',
            '    c_prim.CreateAttribute("primvars:arnold:visibility:volume", Sdf.ValueTypeNames.Bool, False).Set(True)',
            '    c_prim.CreateAttribute("arnold:visibility:volume", Sdf.ValueTypeNames.Int, False).Set(1)',
            '    c_prim.CreateAttribute("primvars:arnold:visibility", Sdf.ValueTypeNames.UChar, False).Set(255 if room_shadows else 253)',
            '    c_prim.CreateAttribute("arnold:visibility", Sdf.ValueTypeNames.Int, False).Set(255 if room_shadows else 253)',
            '    c_prim.CreateAttribute("primvars:redshift:object:MESHFLAG_PRIMARYRAYVISIBLE", Sdf.ValueTypeNames.Bool, False).Set(True)',
            '    c_prim.CreateAttribute("redshift:object:MESHFLAG_PRIMARYRAYVISIBLE", Sdf.ValueTypeNames.Bool, False).Set(True)',
            '    c_prim.CreateAttribute("primvars:redshift:object:MESHFLAG_PRIMARYRAYVIS", Sdf.ValueTypeNames.Bool, False).Set(True)',
            '    c_prim.CreateAttribute("redshift:object:MESHFLAG_PRIMARYRAYVIS", Sdf.ValueTypeNames.Bool, False).Set(True)',
            '    c_prim.CreateAttribute("primvars:redshift:object:MESHFLAG_REFLECTIONVISIBLE", Sdf.ValueTypeNames.Bool, False).Set(True)',
            '    c_prim.CreateAttribute("redshift:object:MESHFLAG_REFLECTIONVISIBLE", Sdf.ValueTypeNames.Bool, False).Set(True)',
            '    c_prim.CreateAttribute("primvars:redshift:object:MESHFLAG_REFLECTIONVIS", Sdf.ValueTypeNames.Bool, False).Set(True)',
            '    c_prim.CreateAttribute("redshift:object:MESHFLAG_REFLECTIONVIS", Sdf.ValueTypeNames.Bool, False).Set(True)',
            '    c_prim.CreateAttribute("primvars:redshift:object:MESHFLAG_REFRACTIONVISIBLE", Sdf.ValueTypeNames.Bool, False).Set(True)',
            '    c_prim.CreateAttribute("redshift:object:MESHFLAG_REFRACTIONVISIBLE", Sdf.ValueTypeNames.Bool, False).Set(True)',
            '    c_prim.CreateAttribute("primvars:redshift:object:MESHFLAG_REFRACTIONVIS", Sdf.ValueTypeNames.Bool, False).Set(True)',
            '    c_prim.CreateAttribute("redshift:object:MESHFLAG_REFRACTIONVIS", Sdf.ValueTypeNames.Bool, False).Set(True)',
            '    c_prim.CreateAttribute("primvars:redshift:object:MESHFLAG_GIVISIBLE", Sdf.ValueTypeNames.Bool, False).Set(True)',
            '    c_prim.CreateAttribute("redshift:object:MESHFLAG_GIVISIBLE", Sdf.ValueTypeNames.Bool, False).Set(True)',
            '    c_prim.CreateAttribute("primvars:redshift:object:MESHFLAG_GIVIS", Sdf.ValueTypeNames.Bool, False).Set(True)',
            '    c_prim.CreateAttribute("redshift:object:MESHFLAG_GIVIS", Sdf.ValueTypeNames.Bool, False).Set(True)',
            '    c_prim.CreateAttribute("primvars:redshift:object:MESHFLAG_GICASTER", Sdf.ValueTypeNames.Bool, False).Set(True)',
            '    c_prim.CreateAttribute("redshift:object:MESHFLAG_GICASTER", Sdf.ValueTypeNames.Bool, False).Set(True)',
            '',
        ])

    # 3. Walls
    if build_walls:
        py_lines.extend([
            '# --- 3. Perimeter Walls ---',
            'walls_scope = UsdGeom.Scope.Define(stage, "/stage/room/walls")',
            '',
        ])

        wall_configs = [
            ("west", "wall_west", x_min, (1.0, 0.0, 0.0), z_max, z_min, "z"),
            ("east", "wall_east", x_max, (-1.0, 0.0, 0.0), z_min, z_max, "z"),
            ("north", "wall_north", z_min, (0.0, 0.0, 1.0), x_min, x_max, "x"),
            ("south", "wall_south", z_max, (0.0, 0.0, -1.0), x_max, x_min, "x"),
        ]

        for wall_id, wall_mesh_name, c_val, normal, u_wall_min, u_wall_max, u_axis in wall_configs:
            wall_wins = [w for w in windows if w.get("wall") == wall_id] if cut_windows else []

            py_lines.extend([
                f"# Wall {wall_id}",
                f"{wall_mesh_name} = UsdGeom.Mesh.Define(stage, '/stage/room/walls/{wall_mesh_name}')",
                "w_pts, w_nrms, w_cnts, w_idxs, w_uvs, w_cols = [], [], [], [], [], []",
            ])

            if wall_wins:
                win = wall_wins[0]
                u0 = float(win["u_range"][0])
                u1 = float(win["u_range"][1])
                v0 = float(win["v_range"][0])
                v1 = float(win["v_range"][1])

                py_lines.extend([
                    f"u0 = max({u_wall_min} + 0.1, min({u_wall_max} - 0.2, float({u0})))",
                    f"u1 = max(u0 + 0.2, min({u_wall_max} - 0.1, float({u1})))",
                    f"v0 = max(y_floor + 0.1, min(y_ceil - 0.2, float({v0})))",
                    f"v1 = max(v0 + 0.2, min(y_ceil - 0.1, float({v1})))",
                    f"delta_u = float({u_wall_max}) - float({u_wall_min})",
                    "delta_v = y_ceil - y_floor",
                    f"nu0 = (u0 - float({u_wall_min})) / max(1e-4, delta_u)",
                    f"nu1 = (u1 - float({u_wall_min})) / max(1e-4, delta_u)",
                    "nv0 = (v0 - y_floor) / max(1e-4, delta_v)",
                    "nv1 = (v1 - y_floor) / max(1e-4, delta_v)",
                ])

                if u_axis == "z":
                    py_lines.extend([
                        f"c_sill = [np.array([{c_val}, y_floor, {u_wall_min}]), np.array([{c_val}, y_floor, {u_wall_max}]), np.array([{c_val}, v0, {u_wall_max}]), np.array([{c_val}, v0, {u_wall_min}])]",
                        "uv_sill = (0.0, 1.0, 0.0, nv0) if use_planar else None",
                        f"add_grid_to_buffers(w_pts, w_nrms, w_cnts, w_idxs, w_uvs, w_cols, c_sill, {normal}, nx=16, ny=6, uv_bounds=uv_sill)",
                        f"c_lintel = [np.array([{c_val}, v1, {u_wall_min}]), np.array([{c_val}, v1, {u_wall_max}]), np.array([{c_val}, y_ceil, {u_wall_max}]), np.array([{c_val}, y_ceil, {u_wall_min}])]",
                        "uv_lintel = (0.0, 1.0, nv1, 1.0) if use_planar else None",
                        f"add_grid_to_buffers(w_pts, w_nrms, w_cnts, w_idxs, w_uvs, w_cols, c_lintel, {normal}, nx=16, ny=6, uv_bounds=uv_lintel)",
                        f"c_left = [np.array([{c_val}, v0, {u_wall_min}]), np.array([{c_val}, v0, u0]), np.array([{c_val}, v1, u0]), np.array([{c_val}, v1, {u_wall_min}])]",
                        "uv_left = (0.0, nu0, nv0, nv1) if use_planar else None",
                        f"add_grid_to_buffers(w_pts, w_nrms, w_cnts, w_idxs, w_uvs, w_cols, c_left, {normal}, nx=6, ny=16, uv_bounds=uv_left)",
                        f"c_right = [np.array([{c_val}, v0, u1]), np.array([{c_val}, v0, {u_wall_max}]), np.array([{c_val}, v1, {u_wall_max}]), np.array([{c_val}, v1, u1])]",
                        "uv_right = (nu1, 1.0, nv0, nv1) if use_planar else None",
                        f"add_grid_to_buffers(w_pts, w_nrms, w_cnts, w_idxs, w_uvs, w_cols, c_right, {normal}, nx=6, ny=16, uv_bounds=uv_right)",
                    ])
                else:
                    py_lines.extend([
                        f"c_sill = [np.array([{u_wall_min}, y_floor, {c_val}]), np.array([{u_wall_max}, y_floor, {c_val}]), np.array([{u_wall_max}, v0, {c_val}]), np.array([{u_wall_min}, v0, {c_val}])]",
                        "uv_sill = (0.0, 1.0, 0.0, nv0) if use_planar else None",
                        f"add_grid_to_buffers(w_pts, w_nrms, w_cnts, w_idxs, w_uvs, w_cols, c_sill, {normal}, nx=16, ny=6, uv_bounds=uv_sill)",
                        f"c_lintel = [np.array([{u_wall_min}, v1, {c_val}]), np.array([{u_wall_max}, v1, {c_val}]), np.array([{u_wall_max}, y_ceil, {c_val}]), np.array([{u_wall_min}, y_ceil, {c_val}])]",
                        "uv_lintel = (0.0, 1.0, nv1, 1.0) if use_planar else None",
                        f"add_grid_to_buffers(w_pts, w_nrms, w_cnts, w_idxs, w_uvs, w_cols, c_lintel, {normal}, nx=16, ny=6, uv_bounds=uv_lintel)",
                        f"c_left = [np.array([{u_wall_min}, v0, {c_val}]), np.array([u0, v0, {c_val}]), np.array([u0, v1, {c_val}]), np.array([{u_wall_min}, v1, {c_val}])]",
                        "uv_left = (0.0, nu0, nv0, nv1) if use_planar else None",
                        f"add_grid_to_buffers(w_pts, w_nrms, w_cnts, w_idxs, w_uvs, w_cols, c_left, {normal}, nx=6, ny=16, uv_bounds=uv_left)",
                        f"c_right = [np.array([u1, v0, {c_val}]), np.array([{u_wall_max}, v0, {c_val}]), np.array([{u_wall_max}, v1, {c_val}]), np.array([u1, v1, {c_val}])]",
                        "uv_right = (nu1, 1.0, nv0, nv1) if use_planar else None",
                        f"add_grid_to_buffers(w_pts, w_nrms, w_cnts, w_idxs, w_uvs, w_cols, c_right, {normal}, nx=6, ny=16, uv_bounds=uv_right)",
                    ])
            else:
                # Solid wall
                if wall_id == "west":
                    py_lines.append(f"c_wall = [np.array([x_min, y_floor, z_max]), np.array([x_min, y_floor, z_min]), np.array([x_min, y_ceil, z_min]), np.array([x_min, y_ceil, z_max])]")
                elif wall_id == "east":
                    py_lines.append(f"c_wall = [np.array([x_max, y_floor, z_min]), np.array([x_max, y_floor, z_max]), np.array([x_max, y_ceil, z_max]), np.array([x_max, y_ceil, z_min])]")
                elif wall_id == "north":
                    py_lines.append(f"c_wall = [np.array([x_min, y_floor, z_min]), np.array([x_max, y_floor, z_min]), np.array([x_max, y_ceil, z_min]), np.array([x_min, y_ceil, z_min])]")
                else:
                    py_lines.append(f"c_wall = [np.array([x_max, y_floor, z_max]), np.array([x_min, y_floor, z_max]), np.array([x_min, y_ceil, z_max]), np.array([x_max, y_ceil, z_max])]")

                py_lines.append(f"wall_uv = (0.0, 1.0, 0.0, 1.0) if use_planar else None")
                py_lines.append(f"add_grid_to_buffers(w_pts, w_nrms, w_cnts, w_idxs, w_uvs, w_cols, c_wall, {normal}, nx=24, ny=16, uv_bounds=wall_uv)")

            py_lines.extend([
                f"{wall_mesh_name}.CreatePointsAttr(Vt.Vec3fArray(w_pts))",
                f"{wall_mesh_name}.CreateFaceVertexCountsAttr(Vt.IntArray(w_cnts))",
                f"{wall_mesh_name}.CreateFaceVertexIndicesAttr(Vt.IntArray(w_idxs))",
                f"{wall_mesh_name}.CreateNormalsAttr(Vt.Vec3fArray(w_nrms))",
                f"{wall_mesh_name}.SetNormalsInterpolation(UsdGeom.Tokens.vertex)",
                f"{wall_mesh_name}.CreateDoubleSidedAttr(double_sided)",
                f"{wall_mesh_name}.CreateSubdivisionSchemeAttr().Set(UsdGeom.Tokens.none)",
                f"w_pv_api = UsdGeom.PrimvarsAPI({wall_mesh_name}.GetPrim())",
                f"w_st_pv = w_pv_api.CreatePrimvar('st', Sdf.ValueTypeNames.TexCoord2fArray, UsdGeom.Tokens.faceVarying)",
                f"w_st_pv.Set(Vt.Vec2fArray(w_uvs))",
                f"w_uv_pv = w_pv_api.CreatePrimvar('uv', Sdf.ValueTypeNames.TexCoord2fArray, UsdGeom.Tokens.faceVarying)",
                f"w_uv_pv.Set(Vt.Vec2fArray(w_uvs))",
                f"{wall_mesh_name}.CreateDisplayColorPrimvar(UsdGeom.Tokens.vertex).Set(Vt.Vec3fArray(w_cols))",
                f"{wall_mesh_name}.CreateDisplayOpacityPrimvar(UsdGeom.Tokens.vertex).Set(Vt.FloatArray([1.0] * len(w_pts)))",
                f"w_prim = {wall_mesh_name}.GetPrim()",
                f"w_prim.CreateAttribute('primvars:karma:object:dicing:quality', Sdf.ValueTypeNames.Float, False).Set(0.0)",
                "w_prim.CreateAttribute('primvars:arnold:opaque', Sdf.ValueTypeNames.Bool, False).Set(True)",
                "w_prim.CreateAttribute('arnold:opaque', Sdf.ValueTypeNames.Bool, False).Set(True)",
                "w_prim.CreateAttribute('primvars:arnold:visibility:shadow', Sdf.ValueTypeNames.Bool, False).Set(bool(room_shadows))",
                "w_prim.CreateAttribute('arnold:visibility:shadow', Sdf.ValueTypeNames.Int, False).Set(1 if room_shadows else 0)",
                "w_prim.CreateAttribute('primvars:redshift:object:MESHFLAG_SHADOWCASTER', Sdf.ValueTypeNames.Bool, False).Set(bool(room_shadows))",
                "w_prim.CreateAttribute('redshift:object:MESHFLAG_SHADOWCASTER', Sdf.ValueTypeNames.Bool, False).Set(bool(room_shadows))",
                "if room_invisible:",
                "    karma_vis = '-primary' if room_shadows else '-primary & -shadow'",
                "    w_prim.CreateAttribute('primvars:karma:object:rendervisibility', Sdf.ValueTypeNames.String, False).Set(karma_vis)",
                "    w_prim.CreateAttribute('karma:object:rendervisibility', Sdf.ValueTypeNames.String, False).Set(karma_vis)",
                "    w_prim.CreateAttribute('primvars:arnold:visibility:camera', Sdf.ValueTypeNames.Bool, False).Set(False)",
                "    w_prim.CreateAttribute('arnold:visibility:camera', Sdf.ValueTypeNames.Int, False).Set(0)",
                "    w_prim.CreateAttribute('primvars:arnold:visibility:diffuse_reflect', Sdf.ValueTypeNames.Bool, False).Set(True)",
                "    w_prim.CreateAttribute('arnold:visibility:diffuse_reflect', Sdf.ValueTypeNames.Int, False).Set(1)",
                "    w_prim.CreateAttribute('primvars:arnold:visibility:specular_reflect', Sdf.ValueTypeNames.Bool, False).Set(True)",
                "    w_prim.CreateAttribute('arnold:visibility:specular_reflect', Sdf.ValueTypeNames.Int, False).Set(1)",
                "    w_prim.CreateAttribute('primvars:arnold:visibility:diffuse_transmit', Sdf.ValueTypeNames.Bool, False).Set(True)",
                "    w_prim.CreateAttribute('arnold:visibility:diffuse_transmit', Sdf.ValueTypeNames.Int, False).Set(1)",
                "    w_prim.CreateAttribute('primvars:arnold:visibility:specular_transmit', Sdf.ValueTypeNames.Bool, False).Set(True)",
                "    w_prim.CreateAttribute('arnold:visibility:specular_transmit', Sdf.ValueTypeNames.Int, False).Set(1)",
                "    w_prim.CreateAttribute('primvars:arnold:visibility:volume', Sdf.ValueTypeNames.Bool, False).Set(True)",
                "    w_prim.CreateAttribute('arnold:visibility:volume', Sdf.ValueTypeNames.Int, False).Set(1)",
                "    w_prim.CreateAttribute('primvars:arnold:visibility', Sdf.ValueTypeNames.UChar, False).Set((2 if room_shadows else 0) | 4 | 8 | 16 | 32 | 64)",
                "    w_prim.CreateAttribute('arnold:visibility', Sdf.ValueTypeNames.Int, False).Set((2 if room_shadows else 0) | 4 | 8 | 16 | 32 | 64)",
                "    w_prim.CreateAttribute('primvars:redshift:object:MESHFLAG_PRIMARYRAYVISIBLE', Sdf.ValueTypeNames.Bool, False).Set(False)",
                "    w_prim.CreateAttribute('redshift:object:MESHFLAG_PRIMARYRAYVISIBLE', Sdf.ValueTypeNames.Bool, False).Set(False)",
                "    w_prim.CreateAttribute('primvars:redshift:object:MESHFLAG_PRIMARYRAYVIS', Sdf.ValueTypeNames.Bool, False).Set(False)",
                "    w_prim.CreateAttribute('redshift:object:MESHFLAG_PRIMARYRAYVIS', Sdf.ValueTypeNames.Bool, False).Set(False)",
                "    w_prim.CreateAttribute('primvars:redshift:object:MESHFLAG_REFLECTIONVISIBLE', Sdf.ValueTypeNames.Bool, False).Set(True)",
                "    w_prim.CreateAttribute('redshift:object:MESHFLAG_REFLECTIONVISIBLE', Sdf.ValueTypeNames.Bool, False).Set(True)",
                "    w_prim.CreateAttribute('primvars:redshift:object:MESHFLAG_REFLECTIONVIS', Sdf.ValueTypeNames.Bool, False).Set(True)",
                "    w_prim.CreateAttribute('redshift:object:MESHFLAG_REFLECTIONVIS', Sdf.ValueTypeNames.Bool, False).Set(True)",
                "    w_prim.CreateAttribute('primvars:redshift:object:MESHFLAG_REFRACTIONVISIBLE', Sdf.ValueTypeNames.Bool, False).Set(True)",
                "    w_prim.CreateAttribute('redshift:object:MESHFLAG_REFRACTIONVISIBLE', Sdf.ValueTypeNames.Bool, False).Set(True)",
                "    w_prim.CreateAttribute('primvars:redshift:object:MESHFLAG_REFRACTIONVIS', Sdf.ValueTypeNames.Bool, False).Set(True)",
                "    w_prim.CreateAttribute('redshift:object:MESHFLAG_REFRACTIONVIS', Sdf.ValueTypeNames.Bool, False).Set(True)",
                "    w_prim.CreateAttribute('primvars:redshift:object:MESHFLAG_GIVISIBLE', Sdf.ValueTypeNames.Bool, False).Set(True)",
                "    w_prim.CreateAttribute('redshift:object:MESHFLAG_GIVISIBLE', Sdf.ValueTypeNames.Bool, False).Set(True)",
                "    w_prim.CreateAttribute('primvars:redshift:object:MESHFLAG_GIVIS', Sdf.ValueTypeNames.Bool, False).Set(True)",
                "    w_prim.CreateAttribute('redshift:object:MESHFLAG_GIVIS', Sdf.ValueTypeNames.Bool, False).Set(True)",
                "    w_prim.CreateAttribute('primvars:redshift:object:MESHFLAG_GICASTER', Sdf.ValueTypeNames.Bool, False).Set(True)",
                "    w_prim.CreateAttribute('redshift:object:MESHFLAG_GICASTER', Sdf.ValueTypeNames.Bool, False).Set(True)",
                "else:",
                "    karma_vis = '*' if room_shadows else '-shadow'",
                "    w_prim.CreateAttribute('primvars:karma:object:rendervisibility', Sdf.ValueTypeNames.String, False).Set(karma_vis)",
                "    w_prim.CreateAttribute('karma:object:rendervisibility', Sdf.ValueTypeNames.String, False).Set(karma_vis)",
                "    w_prim.CreateAttribute('primvars:arnold:visibility:camera', Sdf.ValueTypeNames.Bool, False).Set(True)",
                "    w_prim.CreateAttribute('arnold:visibility:camera', Sdf.ValueTypeNames.Int, False).Set(1)",
                "    w_prim.CreateAttribute('primvars:arnold:visibility:diffuse_reflect', Sdf.ValueTypeNames.Bool, False).Set(True)",
                "    w_prim.CreateAttribute('arnold:visibility:diffuse_reflect', Sdf.ValueTypeNames.Int, False).Set(1)",
                "    w_prim.CreateAttribute('primvars:arnold:visibility:specular_reflect', Sdf.ValueTypeNames.Bool, False).Set(True)",
                "    w_prim.CreateAttribute('arnold:visibility:specular_reflect', Sdf.ValueTypeNames.Int, False).Set(1)",
                "    w_prim.CreateAttribute('primvars:arnold:visibility:diffuse_transmit', Sdf.ValueTypeNames.Bool, False).Set(True)",
                "    w_prim.CreateAttribute('arnold:visibility:diffuse_transmit', Sdf.ValueTypeNames.Int, False).Set(1)",
                "    w_prim.CreateAttribute('primvars:arnold:visibility:specular_transmit', Sdf.ValueTypeNames.Bool, False).Set(True)",
                "    w_prim.CreateAttribute('arnold:visibility:specular_transmit', Sdf.ValueTypeNames.Int, False).Set(1)",
                "    w_prim.CreateAttribute('primvars:arnold:visibility:volume', Sdf.ValueTypeNames.Bool, False).Set(True)",
                "    w_prim.CreateAttribute('arnold:visibility:volume', Sdf.ValueTypeNames.Int, False).Set(1)",
                "    w_prim.CreateAttribute('primvars:arnold:visibility', Sdf.ValueTypeNames.UChar, False).Set(255 if room_shadows else 253)",
                "    w_prim.CreateAttribute('arnold:visibility', Sdf.ValueTypeNames.Int, False).Set(255 if room_shadows else 253)",
                "    w_prim.CreateAttribute('primvars:redshift:object:MESHFLAG_PRIMARYRAYVISIBLE', Sdf.ValueTypeNames.Bool, False).Set(True)",
                "    w_prim.CreateAttribute('redshift:object:MESHFLAG_PRIMARYRAYVISIBLE', Sdf.ValueTypeNames.Bool, False).Set(True)",
                "    w_prim.CreateAttribute('primvars:redshift:object:MESHFLAG_PRIMARYRAYVIS', Sdf.ValueTypeNames.Bool, False).Set(True)",
                "    w_prim.CreateAttribute('redshift:object:MESHFLAG_PRIMARYRAYVIS', Sdf.ValueTypeNames.Bool, False).Set(True)",
                "    w_prim.CreateAttribute('primvars:redshift:object:MESHFLAG_REFLECTIONVISIBLE', Sdf.ValueTypeNames.Bool, False).Set(True)",
                "    w_prim.CreateAttribute('redshift:object:MESHFLAG_REFLECTIONVISIBLE', Sdf.ValueTypeNames.Bool, False).Set(True)",
                "    w_prim.CreateAttribute('primvars:redshift:object:MESHFLAG_REFLECTIONVIS', Sdf.ValueTypeNames.Bool, False).Set(True)",
                "    w_prim.CreateAttribute('redshift:object:MESHFLAG_REFLECTIONVIS', Sdf.ValueTypeNames.Bool, False).Set(True)",
                "    w_prim.CreateAttribute('primvars:redshift:object:MESHFLAG_REFRACTIONVISIBLE', Sdf.ValueTypeNames.Bool, False).Set(True)",
                "    w_prim.CreateAttribute('redshift:object:MESHFLAG_REFRACTIONVISIBLE', Sdf.ValueTypeNames.Bool, False).Set(True)",
                "    w_prim.CreateAttribute('primvars:redshift:object:MESHFLAG_REFRACTIONVIS', Sdf.ValueTypeNames.Bool, False).Set(True)",
                "    w_prim.CreateAttribute('redshift:object:MESHFLAG_REFRACTIONVIS', Sdf.ValueTypeNames.Bool, False).Set(True)",
                "    w_prim.CreateAttribute('primvars:redshift:object:MESHFLAG_GIVISIBLE', Sdf.ValueTypeNames.Bool, False).Set(True)",
                "    w_prim.CreateAttribute('redshift:object:MESHFLAG_GIVISIBLE', Sdf.ValueTypeNames.Bool, False).Set(True)",
                "    w_prim.CreateAttribute('primvars:redshift:object:MESHFLAG_GIVIS', Sdf.ValueTypeNames.Bool, False).Set(True)",
                "    w_prim.CreateAttribute('redshift:object:MESHFLAG_GIVIS', Sdf.ValueTypeNames.Bool, False).Set(True)",
                "    w_prim.CreateAttribute('primvars:redshift:object:MESHFLAG_GICASTER', Sdf.ValueTypeNames.Bool, False).Set(True)",
                "    w_prim.CreateAttribute('redshift:object:MESHFLAG_GICASTER', Sdf.ValueTypeNames.Bool, False).Set(True)",
                "",
            ])

    # 4. Window Scope (Physical Light nodes defined downstream in Solaris)
    if build_portals and windows:
        py_lines.extend([
            '# --- 4. Window Scope (Physical Light nodes defined downstream in Solaris) ---',
            'windows_scope = UsdGeom.Scope.Define(stage, "/stage/room/windows")',
            '',
        ])

    # 4. Interior Props & Columns
    if build_props and props:
        # Create lightweight serialization of props (strip out raw numpy point arrays to keep LOP script clean)
        clean_props = []
        for p in props:
            p_dict = {}
            for k, v in p.items():
                if k in ("points", "colors"):
                    continue
                if isinstance(v, (np.ndarray, list)):
                    p_dict[k] = [float(x) for x in v]
                else:
                    p_dict[k] = v
            clean_props.append(p_dict)

        py_lines.extend([
            '# --- 4. Interior Props & Columns ---',
            'props_scope = UsdGeom.Scope.Define(stage, "/stage/room/props")',
            f'props_data = {repr(clean_props)}',
            'for prop in props_data:',
            '    p_name = prop.get("name", "prop")',
            '    p_shape = prop.get("shape", "box")',
            '    p_min = prop.get("b_min", [-0.5, 0.0, -0.5])',
            '    p_max = prop.get("b_max", [0.5, 1.0, 0.5])',
            '    p_center = prop.get("center", [0.0, 0.5, 0.0])',
            '    p_yaw = float(prop.get("yaw", 0.0))',
            '    obb_sz = prop.get("obb_size", [p_max[0]-p_min[0], p_max[1]-p_min[1], p_max[2]-p_min[2]])',
            '    p_rad = float(prop.get("radius", 0.3))',
            '    p_col = prop.get("color", [0.65, 0.65, 0.65])',
            '    p_mesh_path = f"/stage/room/props/{p_name}"',
            '    p_mesh_file = prop.get("mesh_file")',
            '    if p_mesh_file and os.path.isfile(p_mesh_file):',
            '        p_mesh_file = p_mesh_file.replace(chr(92), "/")',
            '        p_prim = stage.DefinePrim(p_mesh_path)',
            '        p_prim.GetReferences().ClearReferences()',
            '        p_prim.GetReferences().AddReference(assetPath=p_mesh_file, primPath="/prop_mesh")',
            '        p_prim.CreateAttribute("primvars:karma:object:dicing:quality", Sdf.ValueTypeNames.Float, False).Set(0.0)',
            '        p_prim.CreateAttribute("primvars:arnold:subdiv_type", Sdf.ValueTypeNames.String, False).Set("none")',
            '        p_prim.CreateAttribute("primvars:arnold:subdiv_iterations", Sdf.ValueTypeNames.Int, False).Set(0)',
            '        p_prim.CreateAttribute("primvars:arnold:opaque", Sdf.ValueTypeNames.Bool, False).Set(True)',
            '        p_prim.CreateAttribute("arnold:opaque", Sdf.ValueTypeNames.Bool, False).Set(True)',
            '        p_prim.CreateAttribute("primvars:karma:object:rendervisibility", Sdf.ValueTypeNames.String, False).Set("*")',
            '        p_prim.CreateAttribute("primvars:karma:object:lightsource:doublesided", Sdf.ValueTypeNames.Int, False).Set(1)',
            '        if mat_mode in ("emissive", "pbr_emissive"):',
            '            p_prim.CreateAttribute("primvars:karma:object:treat_as_lightsource", Sdf.ValueTypeNames.Int, False).Set(1)',
            '            p_prim.CreateAttribute("primvars:arnold:mesh_light", Sdf.ValueTypeNames.Bool, False).Set(True)',
            '            p_prim.CreateAttribute("primvars:redshift:object:MESHFLAG_GICASTER", Sdf.ValueTypeNames.Bool, False).Set(True)',
            '        if prop.get("is_light"):',
            '            cx, cy, cz = float(p_center[0]), float(p_center[1]), float(p_center[2])',
            '            r = float(p_rad)',
            '            pl_path = f"/stage/room/props/{p_name}_light"',
            '            pl_light = UsdLux.SphereLight.Define(stage, pl_path)',
            '            pl_light.CreateIntensityAttr().Set(18.0)',
            '            pl_light.CreateExposureAttr().Set(0.0)',
            '            pl_light.CreateColorAttr().Set(Gf.Vec3f(1.0, 0.94, 0.82))',
            '            pl_light.CreateRadiusAttr().Set(float(r * 0.85))',
            '            pl_light.CreateNormalizeAttr().Set(False)',
            '            pl_xform = UsdGeom.Xformable(pl_light.GetPrim())',
            '            pl_xform.ClearXformOpOrder()',
            '            pl_xform.AddTranslateOp().Set(Gf.Vec3d(cx, cy, cz))',
            '        continue',
            '',
            '    p_mesh = UsdGeom.Mesh.Define(stage, p_mesh_path)',
            '    p_prim = p_mesh.GetPrim()',
            '    p_prim.CreateAttribute("primvars:karma:object:dicing:quality", Sdf.ValueTypeNames.Float, False).Set(0.0)',
            '    p_prim.CreateAttribute("primvars:arnold:subdiv_type", Sdf.ValueTypeNames.String, False).Set("none")',
            '    p_prim.CreateAttribute("primvars:arnold:subdiv_iterations", Sdf.ValueTypeNames.Int, False).Set(0)',
            '    x0, y0, z0 = p_min[0], p_min[1], p_min[2]',
            '    x1, y1, z1 = p_max[0], p_max[1], p_max[2]',
            '    pts, counts, indices, normals, uvs, cols = [], [], [], [], [], []',
            '    if p_shape == "cylinder":',
            '        segments = 16',
            '        cx, cz = p_center[0], p_center[2]',
            '        for k in range(segments):',
            '            th = 2.0 * math.pi * k / segments',
            '            px = cx + p_rad * math.cos(th)',
            '            pz = cz + p_rad * math.sin(th)',
            '            pts.append(Gf.Vec3f(px, y0, pz))',
            '            cols.append(sample_point_color(px, y0, pz, p_col))',
            '        for k in range(segments):',
            '            th = 2.0 * math.pi * k / segments',
            '            px = cx + p_rad * math.cos(th)',
            '            pz = cz + p_rad * math.sin(th)',
            '            pts.append(Gf.Vec3f(px, y1, pz))',
            '            cols.append(sample_point_color(px, y1, pz, p_col))',
            '        idx_bot = len(pts)',
            '        pts.append(Gf.Vec3f(cx, y0, cz))',
            '        cols.append(sample_point_color(cx, y0, cz, p_col))',
            '        idx_top = len(pts)',
            '        pts.append(Gf.Vec3f(cx, y1, cz))',
            '        cols.append(sample_point_color(cx, y1, cz, p_col))',
            '        for k in range(segments):',
            '            k_next = (k + 1) % segments',
            '            counts.append(4)',
            '            indices.extend([k, segments + k, segments + k_next, k_next])',
            '            uvs.extend(compute_projected_face_uvs([pts[k], pts[segments + k], pts[segments + k_next], pts[k_next]]))',
            '            th_m = 2.0 * math.pi * (k + 0.5) / segments',
            '            normals.append(Gf.Vec3f(math.cos(th_m), 0.0, math.sin(th_m)))',
            '        for k in range(segments):',
            '            k_next = (k + 1) % segments',
            '            counts.append(3)',
            '            indices.extend([idx_bot, k_next, k])',
            '            uvs.extend(compute_projected_face_uvs([pts[idx_bot], pts[k_next], pts[k]]))',
            '            normals.append(Gf.Vec3f(0.0, -1.0, 0.0))',
            '        for k in range(segments):',
            '            k_next = (k + 1) % segments',
            '            counts.append(3)',
            '            indices.extend([idx_top, segments + k, segments + k_next])',
            '            uvs.extend(compute_projected_face_uvs([pts[idx_top], pts[segments + k], pts[segments + k_next]]))',
            '            normals.append(Gf.Vec3f(0.0, 1.0, 0.0))',
            '        nrm_interp = UsdGeom.Tokens.uniform',
            '    elif p_shape == "sphere":',
            '        # UV Sphere proxy for ceiling lamps and spherical fixtures',
            '        cx, cy, cz = float(p_center[0]), float(p_center[1]), float(p_center[2])',
            '        r = float(p_rad)',
            '        n_lat, n_lon = 10, 16',
            '        for i in range(n_lat + 1):',
            '            lat = math.pi * i / n_lat',
            '            sy = cy + r * math.cos(lat)',
            '            r_ring = r * math.sin(lat)',
            '            for j in range(n_lon + 1):',
            '                lon = 2.0 * math.pi * j / n_lon',
            '                sx = cx + r_ring * math.cos(lon)',
            '                sz = cz + r_ring * math.sin(lon)',
            '                pts.append(Gf.Vec3f(sx, sy, sz))',
            '                normals.append(Gf.Vec3f(math.sin(lat) * math.cos(lon), math.cos(lat), math.sin(lat) * math.sin(lon)))',
            '                cols.append(sample_point_color(sx, sy, sz, p_col))',
            '        for i in range(n_lat):',
            '            for j in range(n_lon):',
            '                p0 = i * (n_lon + 1) + j',
            '                p1 = p0 + 1',
            '                p2 = (i + 1) * (n_lon + 1) + j + 1',
            '                p3 = (i + 1) * (n_lon + 1) + j',
            '                counts.append(4)',
            '                indices.extend([p0, p3, p2, p1])',
            '                uvs.extend(compute_projected_face_uvs([pts[p0], pts[p3], pts[p2], pts[p1]]))',
            '        nrm_interp = UsdGeom.Tokens.vertex',
            '',
            '        # If practical light emitter is enabled, create associated physical UsdLuxSphereLight',
            '        if prop.get("is_light"):',
            '            pl_path = f"/stage/room/props/{p_name}_light"',
            '            pl_light = UsdLux.SphereLight.Define(stage, pl_path)',
            '            pl_light.CreateIntensityAttr().Set(18.0)',
            '            pl_light.CreateExposureAttr().Set(0.0)',
            '            pl_light.CreateColorAttr().Set(Gf.Vec3f(1.0, 0.94, 0.82))',
            '            pl_light.CreateRadiusAttr().Set(float(r * 0.85))',
            '            pl_light.CreateNormalizeAttr().Set(False)',
            '            pl_xform = UsdGeom.Xformable(pl_light.GetPrim())',
            '            pl_xform.ClearXformOpOrder()',
            '            pl_xform.AddTranslateOp().Set(Gf.Vec3d(cx, cy, cz))',
            '    else:',
            '        # Oriented Bounding Box (OBB) using yaw and obb_size from PCA',
            '        hx = float(obb_sz[0]) * 0.5',
            '        hz = float(obb_sz[2]) * 0.5',
            '        cx, cz = float(p_center[0]), float(p_center[2])',
            '        rad = math.radians(p_yaw)',
            '        cos_y = math.cos(rad)',
            '        sin_y = math.sin(rad)',
            '        c0 = np.array([cx - hx * cos_y + hz * sin_y, y0, cz - hx * sin_y - hz * cos_y])',
            '        c1 = np.array([cx + hx * cos_y + hz * sin_y, y0, cz + hx * sin_y - hz * cos_y])',
            '        c2 = np.array([cx + hx * cos_y - hz * sin_y, y0, cz + hx * sin_y + hz * cos_y])',
            '        c3 = np.array([cx - hx * cos_y - hz * sin_y, y0, cz - hx * sin_y + hz * cos_y])',
            '        t0 = np.array([c0[0], y1, c0[2]])',
            '        t1 = np.array([c1[0], y1, c1[2]])',
            '        t2 = np.array([c2[0], y1, c2[2]])',
            '        t3 = np.array([c3[0], y1, c3[2]])',
            '        n_back = (-sin_y, 0.0, -cos_y)',
            '        n_front = (sin_y, 0.0, cos_y)',
            '        n_left = (-cos_y, 0.0, sin_y)',
            '        n_right = (cos_y, 0.0, -sin_y)',
            '        c_box_faces = [',
            '            ([c0, c1, c2, c3], (0.0, -1.0, 0.0), (1.0/3.0, 0.0/2.0, 2.0/3.0, 1.0/2.0)), # bottom',
            '            ([t3, t2, t1, t0], (0.0, 1.0, 0.0), (0.0/3.0, 0.0/2.0, 1.0/3.0, 1.0/2.0)), # top',
            '            ([c1, c0, t0, t1], n_back, (0.0/3.0, 1.0/2.0, 1.0/3.0, 2.0/2.0)), # back',
            '            ([c3, c2, t2, t3], n_front, (2.0/3.0, 0.0/2.0, 3.0/3.0, 1.0/2.0)), # front',
            '            ([c0, c3, t3, t0], n_left, (1.0/3.0, 1.0/2.0, 2.0/3.0, 2.0/2.0)), # left',
            '            ([c2, c1, t1, t2], n_right, (2.0/3.0, 1.0/2.0, 3.0/3.0, 2.0/2.0)), # right',
            '        ]',
            '        for c_corners, c_norm, c_uv in c_box_faces:',
            '            add_box_face_grid(pts, normals, counts, indices, uvs, cols, c_corners, c_norm, p_col, nx=4, ny=4)',
            '        nrm_interp = UsdGeom.Tokens.vertex',
            '',
            '    p_mesh.CreatePointsAttr(Vt.Vec3fArray(pts))',
            '    p_mesh.CreateFaceVertexCountsAttr(Vt.IntArray(counts))',
            '    p_mesh.CreateFaceVertexIndicesAttr(Vt.IntArray(indices))',
            '    p_mesh.CreateExtentAttr(Vt.Vec3fArray([Gf.Vec3f(x0, y0, z0), Gf.Vec3f(x1, y1, z1)]))',
            '    p_mesh.CreateDoubleSidedAttr(True)',
            '    p_mesh.CreateNormalsAttr(Vt.Vec3fArray(normals))',
            '    p_mesh.SetNormalsInterpolation(nrm_interp)',
            '    p_mesh.CreateSubdivisionSchemeAttr().Set(UsdGeom.Tokens.none)',
            '    p_mesh.CreateDisplayColorPrimvar(UsdGeom.Tokens.vertex).Set(Vt.Vec3fArray(cols))',
            '    p_mesh.CreateDisplayOpacityPrimvar(UsdGeom.Tokens.vertex).Set(Vt.FloatArray([1.0] * len(pts)))',
            '    p_pv_api = UsdGeom.PrimvarsAPI(p_prim)',
            '    p_st_pv = p_pv_api.CreatePrimvar("st", Sdf.ValueTypeNames.TexCoord2fArray, UsdGeom.Tokens.faceVarying)',
            '    p_st_pv.Set(Vt.Vec2fArray(uvs))',
            '    p_uv_pv = p_pv_api.CreatePrimvar("uv", Sdf.ValueTypeNames.TexCoord2fArray, UsdGeom.Tokens.faceVarying)',
            '    p_uv_pv.Set(Vt.Vec2fArray(uvs))',
            '    p_prim.CreateAttribute("primvars:karma:object:dicing:quality", Sdf.ValueTypeNames.Float, False).Set(0.0)',
            '    p_prim.CreateAttribute("primvars:arnold:subdiv_type", Sdf.ValueTypeNames.String, False).Set("none")',
            '    p_prim.CreateAttribute("primvars:arnold:subdiv_iterations", Sdf.ValueTypeNames.Int, False).Set(0)',
            '    p_prim.CreateAttribute("primvars:arnold:opaque", Sdf.ValueTypeNames.Bool, False).Set(True)',
            '    p_prim.CreateAttribute("arnold:opaque", Sdf.ValueTypeNames.Bool, False).Set(True)',
            '    p_prim.CreateAttribute("primvars:karma:object:rendervisibility", Sdf.ValueTypeNames.String, False).Set("*")',
            '    p_prim.CreateAttribute("primvars:karma:object:lightsource:doublesided", Sdf.ValueTypeNames.Int, False).Set(1)',
            '    if mat_mode in ("emissive", "pbr_emissive"):',
            '        p_prim.CreateAttribute("primvars:karma:object:treat_as_lightsource", Sdf.ValueTypeNames.Int, False).Set(1)',
            '        p_prim.CreateAttribute("primvars:arnold:mesh_light", Sdf.ValueTypeNames.Bool, False).Set(True)',
            '        p_prim.CreateAttribute("primvars:redshift:object:MESHFLAG_GICASTER", Sdf.ValueTypeNames.Bool, False).Set(True)',
            '',
        ])

    # 5. Render Visibility and Primvars for Architecture
    py_lines.extend([
        '# Surface-specific render visibility and lightsource primvars',
        'mesh_to_key = {',
        '    "floor": "floor",',
        '    "ceiling": "ceiling",',
        '    "walls/wall_north": "wall_north",',
        '    "walls/wall_south": "wall_south",',
        '    "walls/wall_east": "wall_east",',
        '    "walls/wall_west": "wall_west",',
        '}',
        'for mesh_rel_path, surf_key in mesh_to_key.items():',
        '    p_mesh = stage.GetPrimAtPath(f"/stage/room/{mesh_rel_path}")',
        '    if p_mesh and p_mesh.IsValid():',
        '        p_mesh.CreateAttribute("primvars:arnold:opaque", Sdf.ValueTypeNames.Bool, False).Set(True)',
        '        p_mesh.CreateAttribute("arnold:opaque", Sdf.ValueTypeNames.Bool, False).Set(True)',
        '        p_mesh.CreateAttribute("primvars:arnold:visibility:shadow", Sdf.ValueTypeNames.Bool, False).Set(bool(room_shadows))',
        '        p_mesh.CreateAttribute("arnold:visibility:shadow", Sdf.ValueTypeNames.Int, False).Set(1 if room_shadows else 0)',
        '        p_mesh.CreateAttribute("primvars:redshift:object:MESHFLAG_SHADOWCASTER", Sdf.ValueTypeNames.Bool, False).Set(bool(room_shadows))',
        '        p_mesh.CreateAttribute("redshift:object:MESHFLAG_SHADOWCASTER", Sdf.ValueTypeNames.Bool, False).Set(bool(room_shadows))',
        '        if room_invisible:',
        '            karma_vis = "-primary" if room_shadows else "-primary & -shadow"',
        '            p_mesh.CreateAttribute("primvars:karma:object:rendervisibility", Sdf.ValueTypeNames.String, False).Set(karma_vis)',
        '            p_mesh.CreateAttribute("karma:object:rendervisibility", Sdf.ValueTypeNames.String, False).Set(karma_vis)',
        '            p_mesh.CreateAttribute("primvars:arnold:visibility:camera", Sdf.ValueTypeNames.Bool, False).Set(False)',
        '            p_mesh.CreateAttribute("arnold:visibility:camera", Sdf.ValueTypeNames.Int, False).Set(0)',
        '            p_mesh.CreateAttribute("primvars:arnold:visibility:diffuse_reflect", Sdf.ValueTypeNames.Bool, False).Set(True)',
        '            p_mesh.CreateAttribute("arnold:visibility:diffuse_reflect", Sdf.ValueTypeNames.Int, False).Set(1)',
        '            p_mesh.CreateAttribute("primvars:arnold:visibility:specular_reflect", Sdf.ValueTypeNames.Bool, False).Set(True)',
        '            p_mesh.CreateAttribute("arnold:visibility:specular_reflect", Sdf.ValueTypeNames.Int, False).Set(1)',
        '            p_mesh.CreateAttribute("primvars:arnold:visibility:diffuse_transmit", Sdf.ValueTypeNames.Bool, False).Set(True)',
        '            p_mesh.CreateAttribute("arnold:visibility:diffuse_transmit", Sdf.ValueTypeNames.Int, False).Set(1)',
        '            p_mesh.CreateAttribute("primvars:arnold:visibility:specular_transmit", Sdf.ValueTypeNames.Bool, False).Set(True)',
        '            p_mesh.CreateAttribute("arnold:visibility:specular_transmit", Sdf.ValueTypeNames.Int, False).Set(1)',
        '            p_mesh.CreateAttribute("primvars:arnold:visibility:volume", Sdf.ValueTypeNames.Bool, False).Set(True)',
        '            p_mesh.CreateAttribute("arnold:visibility:volume", Sdf.ValueTypeNames.Int, False).Set(1)',
        '            p_mesh.CreateAttribute("primvars:arnold:visibility", Sdf.ValueTypeNames.UChar, False).Set((2 if room_shadows else 0) | 4 | 8 | 16 | 32 | 64)',
        '            p_mesh.CreateAttribute("arnold:visibility", Sdf.ValueTypeNames.Int, False).Set((2 if room_shadows else 0) | 4 | 8 | 16 | 32 | 64)',
        '            p_mesh.CreateAttribute("primvars:redshift:object:MESHFLAG_PRIMARYRAYVISIBLE", Sdf.ValueTypeNames.Bool, False).Set(False)',
        '            p_mesh.CreateAttribute("redshift:object:MESHFLAG_PRIMARYRAYVISIBLE", Sdf.ValueTypeNames.Bool, False).Set(False)',
        '            p_mesh.CreateAttribute("primvars:redshift:object:MESHFLAG_PRIMARYRAYVIS", Sdf.ValueTypeNames.Bool, False).Set(False)',
        '            p_mesh.CreateAttribute("redshift:object:MESHFLAG_PRIMARYRAYVIS", Sdf.ValueTypeNames.Bool, False).Set(False)',
        '            p_mesh.CreateAttribute("primvars:redshift:object:MESHFLAG_REFLECTIONVISIBLE", Sdf.ValueTypeNames.Bool, False).Set(True)',
        '            p_mesh.CreateAttribute("redshift:object:MESHFLAG_REFLECTIONVISIBLE", Sdf.ValueTypeNames.Bool, False).Set(True)',
        '            p_mesh.CreateAttribute("primvars:redshift:object:MESHFLAG_REFLECTIONVIS", Sdf.ValueTypeNames.Bool, False).Set(True)',
        '            p_mesh.CreateAttribute("redshift:object:MESHFLAG_REFLECTIONVIS", Sdf.ValueTypeNames.Bool, False).Set(True)',
        '            p_mesh.CreateAttribute("primvars:redshift:object:MESHFLAG_REFRACTIONVISIBLE", Sdf.ValueTypeNames.Bool, False).Set(True)',
        '            p_mesh.CreateAttribute("redshift:object:MESHFLAG_REFRACTIONVISIBLE", Sdf.ValueTypeNames.Bool, False).Set(True)',
        '            p_mesh.CreateAttribute("primvars:redshift:object:MESHFLAG_REFRACTIONVIS", Sdf.ValueTypeNames.Bool, False).Set(True)',
        '            p_mesh.CreateAttribute("redshift:object:MESHFLAG_REFRACTIONVIS", Sdf.ValueTypeNames.Bool, False).Set(True)',
        '            p_mesh.CreateAttribute("primvars:redshift:object:MESHFLAG_GIVISIBLE", Sdf.ValueTypeNames.Bool, False).Set(True)',
        '            p_mesh.CreateAttribute("redshift:object:MESHFLAG_GIVISIBLE", Sdf.ValueTypeNames.Bool, False).Set(True)',
        '            p_mesh.CreateAttribute("primvars:redshift:object:MESHFLAG_GIVIS", Sdf.ValueTypeNames.Bool, False).Set(True)',
        '            p_mesh.CreateAttribute("redshift:object:MESHFLAG_GIVIS", Sdf.ValueTypeNames.Bool, False).Set(True)',
        '            p_mesh.CreateAttribute("primvars:redshift:object:MESHFLAG_GICASTER", Sdf.ValueTypeNames.Bool, False).Set(True)',
        '            p_mesh.CreateAttribute("redshift:object:MESHFLAG_GICASTER", Sdf.ValueTypeNames.Bool, False).Set(True)',
        '        else:',
        '            karma_vis = "*" if room_shadows else "-shadow"',
        '            p_mesh.CreateAttribute("primvars:karma:object:rendervisibility", Sdf.ValueTypeNames.String, False).Set(karma_vis)',
        '            p_mesh.CreateAttribute("karma:object:rendervisibility", Sdf.ValueTypeNames.String, False).Set(karma_vis)',
        '            p_mesh.CreateAttribute("primvars:arnold:visibility:camera", Sdf.ValueTypeNames.Bool, False).Set(True)',
        '            p_mesh.CreateAttribute("arnold:visibility:camera", Sdf.ValueTypeNames.Int, False).Set(1)',
        '            p_mesh.CreateAttribute("primvars:arnold:visibility:diffuse_reflect", Sdf.ValueTypeNames.Bool, False).Set(True)',
        '            p_mesh.CreateAttribute("arnold:visibility:diffuse_reflect", Sdf.ValueTypeNames.Int, False).Set(1)',
        '            p_mesh.CreateAttribute("primvars:arnold:visibility:specular_reflect", Sdf.ValueTypeNames.Bool, False).Set(True)',
        '            p_mesh.CreateAttribute("arnold:visibility:specular_reflect", Sdf.ValueTypeNames.Int, False).Set(1)',
        '            p_mesh.CreateAttribute("primvars:arnold:visibility:diffuse_transmit", Sdf.ValueTypeNames.Bool, False).Set(True)',
        '            p_mesh.CreateAttribute("arnold:visibility:diffuse_transmit", Sdf.ValueTypeNames.Int, False).Set(1)',
        '            p_mesh.CreateAttribute("primvars:arnold:visibility:specular_transmit", Sdf.ValueTypeNames.Bool, False).Set(True)',
        '            p_mesh.CreateAttribute("arnold:visibility:specular_transmit", Sdf.ValueTypeNames.Int, False).Set(1)',
        '            p_mesh.CreateAttribute("primvars:arnold:visibility:volume", Sdf.ValueTypeNames.Bool, False).Set(True)',
        '            p_mesh.CreateAttribute("arnold:visibility:volume", Sdf.ValueTypeNames.Int, False).Set(1)',
        '            p_mesh.CreateAttribute("primvars:arnold:visibility", Sdf.ValueTypeNames.UChar, False).Set(255 if room_shadows else 253)',
        '            p_mesh.CreateAttribute("arnold:visibility", Sdf.ValueTypeNames.Int, False).Set(255 if room_shadows else 253)',
        '            p_mesh.CreateAttribute("primvars:redshift:object:MESHFLAG_PRIMARYRAYVISIBLE", Sdf.ValueTypeNames.Bool, False).Set(True)',
        '            p_mesh.CreateAttribute("redshift:object:MESHFLAG_PRIMARYRAYVISIBLE", Sdf.ValueTypeNames.Bool, False).Set(True)',
        '            p_mesh.CreateAttribute("primvars:redshift:object:MESHFLAG_PRIMARYRAYVIS", Sdf.ValueTypeNames.Bool, False).Set(True)',
        '            p_mesh.CreateAttribute("redshift:object:MESHFLAG_PRIMARYRAYVIS", Sdf.ValueTypeNames.Bool, False).Set(True)',
        '            p_mesh.CreateAttribute("primvars:redshift:object:MESHFLAG_REFLECTIONVISIBLE", Sdf.ValueTypeNames.Bool, False).Set(True)',
        '            p_mesh.CreateAttribute("redshift:object:MESHFLAG_REFLECTIONVISIBLE", Sdf.ValueTypeNames.Bool, False).Set(True)',
        '            p_mesh.CreateAttribute("primvars:redshift:object:MESHFLAG_REFLECTIONVIS", Sdf.ValueTypeNames.Bool, False).Set(True)',
        '            p_mesh.CreateAttribute("redshift:object:MESHFLAG_REFLECTIONVIS", Sdf.ValueTypeNames.Bool, False).Set(True)',
        '            p_mesh.CreateAttribute("primvars:redshift:object:MESHFLAG_REFRACTIONVISIBLE", Sdf.ValueTypeNames.Bool, False).Set(True)',
        '            p_mesh.CreateAttribute("redshift:object:MESHFLAG_REFRACTIONVISIBLE", Sdf.ValueTypeNames.Bool, False).Set(True)',
        '            p_mesh.CreateAttribute("primvars:redshift:object:MESHFLAG_REFRACTIONVIS", Sdf.ValueTypeNames.Bool, False).Set(True)',
        '            p_mesh.CreateAttribute("redshift:object:MESHFLAG_REFRACTIONVIS", Sdf.ValueTypeNames.Bool, False).Set(True)',
        '            p_mesh.CreateAttribute("primvars:redshift:object:MESHFLAG_GIVISIBLE", Sdf.ValueTypeNames.Bool, False).Set(True)',
        '            p_mesh.CreateAttribute("redshift:object:MESHFLAG_GIVISIBLE", Sdf.ValueTypeNames.Bool, False).Set(True)',
        '            p_mesh.CreateAttribute("primvars:redshift:object:MESHFLAG_GIVIS", Sdf.ValueTypeNames.Bool, False).Set(True)',
        '            p_mesh.CreateAttribute("redshift:object:MESHFLAG_GIVIS", Sdf.ValueTypeNames.Bool, False).Set(True)',
        '            p_mesh.CreateAttribute("primvars:redshift:object:MESHFLAG_GICASTER", Sdf.ValueTypeNames.Bool, False).Set(True)',
        '            p_mesh.CreateAttribute("redshift:object:MESHFLAG_GICASTER", Sdf.ValueTypeNames.Bool, False).Set(True)',
        '        if mat_mode in ("emissive", "pbr_emissive"):',
        '            p_mesh.CreateAttribute("primvars:karma:object:treat_as_lightsource", Sdf.ValueTypeNames.Int, False).Set(1)',
        '            p_mesh.CreateAttribute("primvars:karma:object:lightsource:samplingquality", Sdf.ValueTypeNames.Float, False).Set(1.0)',
        '            p_mesh.CreateAttribute("primvars:karma:object:lightsource:doublesided", Sdf.ValueTypeNames.Int, False).Set(1)',
        '            p_mesh.CreateAttribute("primvars:arnold:mesh_light", Sdf.ValueTypeNames.Bool, False).Set(True)',
        '            p_mesh.CreateAttribute("primvars:redshift:object:MESHFLAG_GICASTER", Sdf.ValueTypeNames.Bool, False).Set(True)',
        '        else:',
        '            if p_mesh.HasAttribute("primvars:karma:object:treat_as_lightsource"):',
        '                p_mesh.CreateAttribute("primvars:karma:object:treat_as_lightsource", Sdf.ValueTypeNames.Int, False).Set(0)',
        '',
        'if mat_mode in ("emissive", "pbr_emissive"):',
        '    room_scope.GetPrim().CreateAttribute("primvars:karma:object:treat_as_lightsource", Sdf.ValueTypeNames.Int, False).Set(1)',
        '    room_scope.GetPrim().CreateAttribute("primvars:karma:object:lightsource:doublesided", Sdf.ValueTypeNames.Int, False).Set(1)',
        '',
    ])

    full_code = "\n".join(py_lines)
    room_node.parm("python").set(full_code)
    room_node.bypass(False)
    room_node.cook(force=True)

    # 6. Author native multi-renderer shaders in a Houdini Material Library LOP (the same way DomeBreaker does)
    try:
        from hdri_match_solaris import renderer_materials
        p_mat_config = {
            "renderer_target": renderer_target or "all",
            "mat_mode": mat_mode or "pbr",
            "emissive_mult": emissive_mult,
            "ground_roughness": roughness,
            "proj_mode": "room_box",
            "planar_textures": planar_textures_dict if use_planar_textures else {},
            "hdri_texture": hdri_texture or "",
            "mat_lib_node_name": "splatforge_materials",
        }
        mat_lib_node = renderer_materials.create_or_update_material_library(stage_node, p_mat_config)
        if mat_lib_node:
            mat_lib_node.cook(force=True)
    except Exception as ex_mat:
        print(f"[SplatForge] Material library creation error: {ex_mat}")

    # Lookdev rig reference if present
    lookdev = stage_node.node("hdri_match_lookdev")


    # Snap lookdev rig to detected floor and center
    if snap_lookdev_to_floor and lookdev:
        try:
            for p_name in ["floor_y", "ground_y", "ty"]:
                p = lookdev.parm(p_name)
                if p:
                    p.set(y_floor)
            # Center in the room if pos_x / pos_z available
            c_x = (x_min + x_max) * 0.5
            c_z = (z_min + z_max) * 0.5
            if lookdev.parm("pos_x"):
                lookdev.parm("pos_x").set(c_x)
            if lookdev.parm("pos_z"):
                lookdev.parm("pos_z").set(c_z)
            lookdev.cook(force=True)
        except Exception:
            pass

    # Spawn physical portal light nodes in /stage
    if build_portals and windows:
        spawn_portal_lights(
            stage_node,
            windows,
            cam_pos=probe_pos,
            hdri_texture=raw_hdri_texture or hdri_texture,
            portal_intensity_mult=portal_intensity_mult,
            portal_texture_mode=portal_texture_mode,
            clear_existing=True,
        )
    elif not build_portals:
        clear_portal_lights(stage_node)

    # Deterministically wire all stage nodes in linear acyclic order
    wire_solaris_stage_stream(stage_node)
    return room_node


def clear_usd_room_architecture(stage_node):
    """
    Remove the splat room architecture node, splatforge materials, and physical portal lights from /stage and rewire stream cleanly.
    """
    if not stage_node or not hou:
        return False

    clear_portal_lights(stage_node)

    for nname in ("splat_room_architecture", "splatforge_materials"):
        node = stage_node.node(nname)
        if node:
            inputs = node.inputs()
            outputs = node.outputs()
            if inputs and outputs:
                for out_n in outputs:
                    out_n.setInput(0, inputs[0])
            node.destroy()

    wire_solaris_stage_stream(stage_node)
    stage_node.layoutChildren()
    return True


def assign_baked_hdri_and_portals(
    stage_node,
    hdri_path,
    room_data=None,
    portal_texture_mode="cropped",
    portal_intensity_mult=1.0,
    roughness=0.85,
    floor_mode="visible",
):
    """
    Assign the baked 360° HDRI (with energy-conserved albedo compression) to the interior
    room meshes (floor, ceiling, walls) and crop and assign directional HDR textures to
    window portal lights. Also updates environmental domelights if present in stage.

    Returns:
        dict with status, paths to assigned textures, and count of textured portals.
    """
    if not stage_node or not hou:
        return {"status": "error", "message": "No stage node or hou module"}

    if not hdri_path or not os.path.isfile(hdri_path):
        raise FileNotFoundError(f"HDRI file does not exist: {hdri_path}")

    from hdri_match_solaris.gaussian_splat import GaussianSplatBaker

    # 1. Ensure compressed albedo map exists
    tex_file = hdri_path.replace(chr(92), "/")
    stem, ext = os.path.splitext(tex_file)
    cand_albedo = f"{stem}_albedo{ext}" if "_albedo" not in stem else tex_file
    if not os.path.isfile(cand_albedo):
        try:
            raw_arr = GaussianSplatBaker.load_exr(tex_file)
            if raw_arr is not None:
                albedo_arr = np.clip(raw_arr / (1.0 + raw_arr * 0.35) * 0.70, 0.0, 0.85)
                GaussianSplatBaker.save_exr(albedo_arr, cand_albedo)
        except Exception as ex:
            print(f"[HDRI Match] Albedo compression warning: {ex}")
            cand_albedo = tex_file

    # 2. Update any domelight in stage to use raw HDRI
    for child in stage_node.children():
        if child.type().name() == "domelight":
            for p in ["xn__inputstexturefile_06a", "texturefile"]:
                if child.parm(p):
                    child.parm(p).set(tex_file)
                    break

    # 3. If room_data provided, call build_usd_room_architecture to rebuild / update cleanly
    portals_count = 0
    if room_data:
        probe_pos = room_data.get("center", (0.0, 1.5, 0.0))
        floor_y = float(room_data.get("floor_y", 0.0))
        probe_pos = (probe_pos[0], floor_y + 1.2, probe_pos[2])

        room_node = build_usd_room_architecture(
            stage_node,
            room_data,
            build_floor=True,
            floor_mode=floor_mode,
            build_ceiling=True,
            build_walls=True,
            cut_windows=True,
            build_portals=True,
            build_props=bool(room_data.get("props")),
            project_hdri=True,
            hdri_texture=tex_file,
            probe_pos=probe_pos,
            roughness=roughness,
            double_sided=True,
            room_shadows=False,
            room_invisible=False,
            portal_intensity_mult=portal_intensity_mult,
            portal_texture_mode=portal_texture_mode,
        )
        portals_count = len(room_data.get("windows", []))
    else:
        # Update existing room node if present
        room_node = stage_node.node("splat_room_architecture")
        if room_node:
            room_node.cook(force=True)

    return {
        "status": "success",
        "hdri_texture": tex_file,
        "albedo_texture": cand_albedo,
        "portals_textured": portals_count,
    }
