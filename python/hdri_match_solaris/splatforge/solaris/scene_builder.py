# -*- coding: utf-8 -*-
"""
SPLATFORGE Scene Builder — Solaris USD Assembly & Pipeline Construction.

Assembles the complete reconstructed VFX scene in Houdini 21 Solaris (/stage):
- /world/room (floor, ceiling, walls with aperture cutouts)
- /world/materials (MaterialX / UsdPreviewSurface PBR shaders)
- /world/lights (DomeLight with baked HDRI + RectLights from extracted practicals)
- /obj/splatforge_room_geo (Houdini SOP representation for modeling/inspection)
- Karma render settings & automatic linear stream wiring
"""

import os
import math
import numpy as np

try:
    import hou
except ImportError:
    hou = None

try:
    from pxr import Usd, UsdGeom, UsdShade, UsdLux, Gf, Vt, Sdf
except ImportError:
    Usd = UsdGeom = UsdShade = UsdLux = Gf = Vt = Sdf = None

from hdri_match_solaris.splatforge.reconstruction.room_reconstructor import (
    reconstruct_room_shell,
    create_room_sop_geometry,
    RoomMeshData,
)


def assemble_splatforge_scene(
    stage_node=None,
    splat_data=None,
    room_data=None,
    room_mesh=None,
    light_analysis=None,
    hdri_result=None,
    build_sop_geo=True,
    build_dome_light=True,
    build_rect_lights=True,
    build_karma_settings=True,
    progress_callback=None,
):
    """Assemble the complete reconstructed VFX scene in Houdini Solaris.

    Args:
        stage_node:          Target ``hou.LopNetwork`` (defaults to ``/stage``).
        splat_data:          ``SplatData`` instance (optional).
        room_data:           ``RoomData`` instance (optional).
        room_mesh:           ``RoomMeshData`` instance (optional, auto-generated from room_data).
        light_analysis:      ``LightAnalysis`` instance (optional).
        hdri_result:         dict with ``'hdri_path'`` from ``generate_hdri`` (optional).
        build_sop_geo:       Also create/update ``/obj/splatforge_room_geo`` SOP.
        build_dome_light:    Create DomeLight LOP if HDRI is available.
        build_rect_lights:   Create RectLight LOPs if lights were extracted.
        build_karma_settings: Create Karma render settings LOP node.
        progress_callback:   Optional ``callable(percent, message)``.

    Returns:
        dict with details of created nodes and prims.
    """
    if not hou:
        raise RuntimeError("assemble_splatforge_scene requires Houdini Python (hou)")

    # 1. Resolve /stage LOP network
    if stage_node is None:
        stage_node = hou.node("/stage")
        if stage_node is None:
            obj = hou.node("/obj")
            if obj:
                stage_node = obj.createNode("lopnet", "stage")
            else:
                raise RuntimeError("Could not find or create /stage LOP network.")

    if progress_callback:
        progress_callback(10, "Preparing room reconstruction...")

    # 2. Ensure room_mesh
    if room_mesh is None and room_data is not None:
        room_mesh = reconstruct_room_shell(room_data, include_openings=True)

    created_nodes = []
    created_prims = []

    # ------------------------------------------------------------------
    # 3. Create SOP Geometry in /obj (for modeling / inspection)
    # ------------------------------------------------------------------
    if build_sop_geo and room_mesh is not None:
        if progress_callback:
            progress_callback(20, "Building SOP room geometry in /obj...")
        try:
            sop_node = build_room_sop_node(room_mesh, splat_data)
            if sop_node:
                created_nodes.append(sop_node.path())
        except Exception as e:
            print(f"[SPLATFORGE] Warning creating SOP geo: {e}")

    # ------------------------------------------------------------------
    # 4. Create Room Architecture LOP in /stage
    # ------------------------------------------------------------------
    room_lop = None
    if room_mesh is not None:
        if progress_callback:
            progress_callback(40, "Authoring USD Room Architecture in /stage...")

        room_lop = stage_node.node("splatforge_room")
        if not room_lop:
            room_lop = stage_node.createNode("pythonscript", "splatforge_room")
            room_lop.setColor(hou.Color((0.85, 0.45, 0.2)))

        # Prepare script for the PythonScript LOP
        py_code = _generate_room_usd_script(room_mesh, room_data)
        room_lop.parm("python").set(py_code)
        created_nodes.append(room_lop.path())
        created_prims.extend(["/world/room/floor", "/world/room/ceiling", "/world/room/walls"])

    # ------------------------------------------------------------------
    # 5. Create Dome Light LOP in /stage (if HDRI available)
    # ------------------------------------------------------------------
    dome_lop = None
    hdri_path = ""
    if hdri_result and isinstance(hdri_result, dict):
        hdri_path = hdri_result.get("hdri_path", "")
    elif splat_data is not None:
        # Check cache for existing baked HDRI
        from hdri_match_solaris.splatforge.io.cache_manager import CacheManager
        cache = CacheManager.for_ply(splat_data.filepath)
        cached_hdri = cache.get_path("textures", "hdri_dome", ext="exr")
        if os.path.isfile(cached_hdri):
            hdri_path = cached_hdri

    if build_dome_light and hdri_path and os.path.isfile(hdri_path):
        if progress_callback:
            progress_callback(60, "Configuring USD Dome Light...")
        norm_hdri = hdri_path.replace("\\", "/")
        dome_lop = stage_node.node("splatforge_dome")
        if not dome_lop:
            dome_lop = stage_node.createNode("domelight", "splatforge_dome")
            dome_lop.setColor(hou.Color((0.95, 0.75, 0.2)))
        
        # Set primpath
        for p_name in ["primpath", "primpath1"]:
            p = dome_lop.parm(p_name)
            if p:
                p.set("/world/lights/dome_hdri")
                break

        # Set texture file parameter
        for p_name in ["xn__inputstexturefile_51a", "texturefile", "texture"]:
            p = dome_lop.parm(p_name)
            if p:
                p.set(norm_hdri)
                break

        # Lower dome intensity slightly so extracted lights pop
        for p_name in ["xn__inputsexposure_0eb", "exposure"]:
            p = dome_lop.parm(p_name)
            if p:
                p.set(-0.5)
                break

        # Align dome with room geometry orientation: equirectangular bake center faces -Z, matching USD DomeLight default ry = 0.0
        ry_parm = dome_lop.parm("ry")
        if ry_parm:
            ry_parm.set(0.0)

        created_nodes.append(dome_lop.path())
        created_prims.append("/world/lights/dome_hdri")
    elif dome_lop is None:
        # If dome light node already exists in stage, ensure it defaults to ry = 0.0
        existing_dome = stage_node.node("splatforge_dome")
        if existing_dome:
            ry_parm = existing_dome.parm("ry")
            if ry_parm:
                ry_parm.set(0.0)

    # ------------------------------------------------------------------
    # 6. Create Extracted Rect Light LOPs in /stage (matching window apertures)
    # ------------------------------------------------------------------
    light_lops = []
    has_light_source = (light_analysis is not None) or (room_data and getattr(room_data, "windows", None))
    if build_rect_lights and has_light_source:
        if progress_callback:
            progress_callback(75, "Spawning extracted USD Rect Lights matching room windows...")
        from hdri_match_solaris.splatforge.lighting.light_extractor import extract_light_definitions
        light_defs = extract_light_definitions(light_analysis, hdri_result, room_data=room_data)
        rect_defs = [ld for ld in light_defs if ld.light_type == "rect"]

        # Clean up obsolete splatforge light nodes
        active_names = {f"splatforge_light_{ld.name}" for ld in rect_defs}
        for c in list(stage_node.children()):
            if c.name().startswith("splatforge_light_") and c.name() not in active_names:
                c.destroy()

        for ld in rect_defs:
            node_name = f"splatforge_light_{ld.name}"
            lt = stage_node.node(node_name)
            if not lt:
                lt = stage_node.createNode("light", node_name)
                lt.setColor(hou.Color((1.0, 0.9, 0.3)))

            # Set light type to UsdLuxRectLight
            pt = lt.parm("lighttype")
            if pt:
                pt.set("UsdLuxRectLight")

            prim_p = lt.parm("primpath")
            if prim_p:
                prim_p.set(f"/world/lights/{ld.name}")

            # Position
            tx = lt.parm("tx")
            ty = lt.parm("ty")
            tz = lt.parm("tz")
            if tx and ty and tz:
                tx.set(float(ld.position[0]))
                ty.set(float(ld.position[1]))
                tz.set(float(ld.position[2]))

            # Rotation
            rx = lt.parm("rx")
            ry = lt.parm("ry")
            rz = lt.parm("rz")
            if rx and ry and rz:
                rx.set(float(ld.rotation[0]))
                ry.set(float(ld.rotation[1]))
                rz.set(float(ld.rotation[2]))

            # Dimensions (matching exact window aperture or cluster bounds)
            # Solaris light LOP parameter bindings for UsdLuxRectLight
            w_ctrl = lt.parm("xn__inputswidth_control_06a")
            if w_ctrl:
                w_ctrl.set("set")
            w_parm = lt.parm("xn__inputswidth_zta") or lt.parm("width")
            if w_parm:
                w_parm.set(max(0.05, float(ld.width)))

            h_ctrl = lt.parm("xn__inputsheight_control_n8a")
            if h_ctrl:
                h_ctrl.set("set")
            h_parm = lt.parm("xn__inputsheight_mva") or lt.parm("height")
            if h_parm:
                h_parm.set(max(0.05, float(ld.height)))

            # Color
            cr = lt.parm("colorr")
            cg = lt.parm("colorg")
            cb = lt.parm("colorb")
            if cr and cg and cb:
                cr.set(float(ld.color[0]))
                cg.set(float(ld.color[1]))
                cb.set(float(ld.color[2]))

            # Intensity & Exposure
            int_parm = lt.parm("xn__inputsintensity_0eb") or lt.parm("intensity")
            if int_parm:
                int_parm.set(float(ld.intensity))
            exp_parm = lt.parm("exposure") or lt.parm("xn__inputsexposure_0eb")
            if exp_parm:
                exp_parm.set(float(ld.exposure))

            light_lops.append(lt)
            created_nodes.append(lt.path())
            created_prims.append(f"/world/lights/{ld.name}")

        # If hotspot analysis produced an inpainted HDRI, update dome texture
        # so the dome map no longer double-counts the extracted practical emitters
        if dome_lop and hdri_result and hdri_result.get("inpainted_path"):
            inpainted = hdri_result["inpainted_path"].replace("\\", "/")
            if os.path.isfile(inpainted):
                for p_name in ["xn__inputstexturefile_51a", "texturefile", "texture"]:
                    p = dome_lop.parm(p_name)
                    if p:
                        p.set(inpainted)
                        break
                ry_parm = dome_lop.parm("ry")
                if ry_parm:
                    ry_parm.set(0.0)

    # ------------------------------------------------------------------
    # 7. Create Karma Render Settings LOP
    # ------------------------------------------------------------------
    karma_lop = None
    if build_karma_settings:
        if progress_callback:
            progress_callback(90, "Configuring Karma render settings...")
        karma_lop = stage_node.node("splatforge_karma_settings")
        if not karma_lop:
            karma_lop = stage_node.createNode("karmarenderproperties", "splatforge_karma_settings")
            karma_lop.setColor(hou.Color((0.5, 0.25, 0.75)))
        created_nodes.append(karma_lop.path())

    # ------------------------------------------------------------------
    # 8. Deterministically Wire the Linear Solaris Pipeline Stream
    # ------------------------------------------------------------------
    stream = []
    if room_lop:
        stream.append(room_lop)
    if dome_lop:
        stream.append(dome_lop)
    stream.extend(light_lops)
    if karma_lop:
        stream.append(karma_lop)

    # Wire in series
    for idx in range(len(stream) - 1):
        src = stream[idx]
        dst = stream[idx + 1]
        dst.setInput(0, src)

    # Layout nodes nicely in network view
    stage_node.layoutChildren(stream)

    # Set display and render flags on terminal node
    if stream:
        terminal = stream[-1]
        terminal.setDisplayFlag(True)
        terminal.setCurrent(True, clear_all_selected=True)

    if progress_callback:
        progress_callback(100, f"USD Scene assembled: {len(created_prims)} prims in /stage")

    return {
        "status": "success",
        "stage_path": stage_node.path(),
        "created_nodes": created_nodes,
        "created_prims": created_prims,
        "room_prims": 3 if room_mesh else 0,
        "dome_light": dome_lop is not None,
        "rect_lights": len(light_lops),
    }


def _generate_room_usd_script(room_mesh, room_data=None):
    """Generate self-contained Python code for the PythonScript LOP node.

    Defines the floor, ceiling, and wall meshes under /world/room with
    MaterialX / UsdPreviewSurface materials. Submeshes are pre-partitioned
    so faceVertexCounts, faceVertexIndices, normals, and UVs match 1-to-1.
    """
    submeshes = room_mesh.get_category_submeshes()

    code = f'''# SPLATFORGE Generated Room Mesh Script
import hou
from pxr import Usd, UsdGeom, UsdShade, Gf, Vt, Sdf

stage = hou.pwd().editableStage()

# 1. Define /world, /world/room, and /world/materials scopes
UsdGeom.Scope.Define(stage, "/world")
room_scope = UsdGeom.Scope.Define(stage, "/world/room")
mat_scope = UsdGeom.Scope.Define(stage, "/world/materials")

# 2. Create materials
materials = {{}}
mat_configs = {{
    "floor": Gf.Vec3f(0.28, 0.28, 0.30),
    "ceiling": Gf.Vec3f(0.85, 0.85, 0.85),
    "walls": Gf.Vec3f(0.70, 0.68, 0.65),
}}

for cat_name, col in mat_configs.items():
    mat_path = f"/world/materials/mtl_{{cat_name}}"
    material = UsdShade.Material.Define(stage, mat_path)
    shader = UsdShade.Shader.Define(stage, f"{{mat_path}}/shader")
    shader.CreateIdAttr("UsdPreviewSurface")
    shader.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).Set(col)
    shader.CreateInput("roughness", Sdf.ValueTypeNames.Float).Set(0.5)
    shader.CreateInput("metallic", Sdf.ValueTypeNames.Float).Set(0.0)
    material.CreateSurfaceOutput().ConnectToSource(shader.ConnectableAPI(), "surface")
    materials[cat_name] = material

# 3. Submesh payload
submeshes = {repr(submeshes)}

# 4. Build sub-meshes with guaranteed 1-to-1 attribute lengths
for cat_name, sdata in submeshes.items():
    mesh_path = f"/world/room/{{cat_name}}"
    mesh_prim = UsdGeom.Mesh.Define(stage, mesh_path)

    pts = [Gf.Vec3f(*p) for p in sdata["points"]]
    mesh_prim.CreatePointsAttr(pts)
    mesh_prim.CreateFaceVertexCountsAttr(sdata["counts"])
    mesh_prim.CreateFaceVertexIndicesAttr(sdata["indices"])

    norms = [Gf.Vec3f(*n) for n in sdata["normals"]]
    mesh_prim.CreateNormalsAttr(norms)
    mesh_prim.SetNormalsInterpolation(UsdGeom.Tokens.faceVarying)

    if pts:
        bbox = Gf.Range3f()
        for p in pts:
            bbox.UnionWith(p)
        mesh_prim.CreateExtentAttr([bbox.GetMin(), bbox.GetMax()])

    tex_coords = UsdGeom.PrimvarsAPI(mesh_prim).CreatePrimvar(
        "st", Sdf.ValueTypeNames.TexCoord2fArray, UsdGeom.Tokens.faceVarying
    )
    tex_coords.Set([Gf.Vec2f(*uv) for uv in sdata["uvs"]])

    if cat_name in materials:
        UsdShade.MaterialBindingAPI.Apply(mesh_prim.GetPrim()).Bind(materials[cat_name])

# 5. Set double-sided on room meshes
for p in stage.GetPrimAtPath("/world/room").GetChildren():
    if p.IsA(UsdGeom.Mesh):
        UsdGeom.Mesh(p).CreateDoubleSidedAttr(True)
'''
    return code


def build_room_sop_node(room_mesh, splat_data=None):
    """Create or update /obj/splatforge_room_geo safely via file SOP and bgeo.sc.

    NEVER mutates read-only node.geometry() on the UI thread to prevent
    viewport thread crashes.
    """
    if not hou or room_mesh is None:
        return None
    obj = hou.node("/obj")
    if not obj:
        return None

    geo_node = obj.node("splatforge_room_geo")
    if not geo_node:
        geo_node = obj.createNode("geo", "splatforge_room_geo")
        geo_node.setColor(hou.Color((0.2, 0.65, 0.45)))
        for c in geo_node.children():
            c.destroy()

    # Determine file path
    from hdri_match_solaris.splatforge.io.cache_manager import CacheManager
    cache = CacheManager.for_ply(splat_data.filepath) if (splat_data and getattr(splat_data, "filepath", None)) else None
    bgeo_path = cache.get_path("geometry", "room_shell", ext="bgeo.sc") if cache else None
    if not bgeo_path:
        import tempfile
        bgeo_path = os.path.join(tempfile.gettempdir(), "splatforge_room_shell.bgeo.sc").replace("\\", "/")

    # Create geometry offline and save to disk
    temp_geo = hou.Geometry()
    create_room_sop_geometry(room_mesh, temp_geo)
    temp_geo.saveToFile(bgeo_path)

    # Use file SOP to load it safely
    file_sop = geo_node.node("load_room_shell")
    if not file_sop:
        file_sop = geo_node.createNode("file", "load_room_shell")
        file_sop.setColor(hou.Color((0.2, 0.8, 0.4)))
    file_sop.parm("file").set(bgeo_path)
    if file_sop.parm("reload"):
        file_sop.parm("reload").pressButton()

    file_sop.setDisplayFlag(True)
    file_sop.setRenderFlag(True)
    return geo_node


def update_scene_lights(stage_node, light_analysis=None, hdri_result=None, room_data=None):
    """Update or spawn light nodes in an existing Solaris /stage network.

    Extracts window aperture portal lights matching the room openings and
    any interior practical clusters, updates the node graph, and re-wires
    the linear pipeline stream.
    """
    if not hou or not stage_node:
        return []

    from hdri_match_solaris.splatforge.lighting.light_extractor import extract_light_definitions
    light_defs = extract_light_definitions(light_analysis, hdri_result, room_data=room_data)
    rect_defs = [ld for ld in light_defs if ld.light_type == "rect"]

    active_names = {f"splatforge_light_{ld.name}" for ld in rect_defs}
    for c in list(stage_node.children()):
        if c.name().startswith("splatforge_light_") and c.name() not in active_names:
            c.destroy()

    light_lops = []
    for ld in rect_defs:
        node_name = f"splatforge_light_{ld.name}"
        lt = stage_node.node(node_name)
        if not lt:
            lt = stage_node.createNode("light", node_name)
            lt.setColor(hou.Color((1.0, 0.9, 0.3)))

        pt = lt.parm("lighttype")
        if pt:
            pt.set("UsdLuxRectLight")

        prim_p = lt.parm("primpath")
        if prim_p:
            prim_p.set(f"/world/lights/{ld.name}")

        tx, ty, tz = lt.parm("tx"), lt.parm("ty"), lt.parm("tz")
        if tx and ty and tz:
            tx.set(float(ld.position[0]))
            ty.set(float(ld.position[1]))
            tz.set(float(ld.position[2]))

        rx, ry, rz = lt.parm("rx"), lt.parm("ry"), lt.parm("rz")
        if rx and ry and rz:
            rx.set(float(ld.rotation[0]))
            ry.set(float(ld.rotation[1]))
            rz.set(float(ld.rotation[2]))

        w_ctrl = lt.parm("xn__inputswidth_control_06a")
        if w_ctrl:
            w_ctrl.set("set")
        w_parm = lt.parm("xn__inputswidth_zta") or lt.parm("width")
        if w_parm:
            w_parm.set(max(0.05, float(ld.width)))

        h_ctrl = lt.parm("xn__inputsheight_control_n8a")
        if h_ctrl:
            h_ctrl.set("set")
        h_parm = lt.parm("xn__inputsheight_mva") or lt.parm("height")
        if h_parm:
            h_parm.set(max(0.05, float(ld.height)))

        cr, cg, cb = lt.parm("colorr"), lt.parm("colorg"), lt.parm("colorb")
        if cr and cg and cb:
            cr.set(float(ld.color[0]))
            cg.set(float(ld.color[1]))
            cb.set(float(ld.color[2]))

        int_parm = lt.parm("xn__inputsintensity_0eb") or lt.parm("intensity")
        if int_parm:
            int_parm.set(float(ld.intensity))
        exp_parm = lt.parm("exposure") or lt.parm("xn__inputsexposure_0eb")
        if exp_parm:
            exp_parm.set(float(ld.exposure))

        light_lops.append(lt)

    # Re-wire linear stream
    room_lop = stage_node.node("splatforge_room")
    dome_lop = stage_node.node("splatforge_dome")
    if dome_lop:
        ry_parm = dome_lop.parm("ry")
        if ry_parm:
            ry_parm.set(0.0)
    karma_lop = stage_node.node("splatforge_karma_settings")

    stream = []
    if room_lop:
        stream.append(room_lop)
    if dome_lop:
        stream.append(dome_lop)
    stream.extend(light_lops)
    if karma_lop:
        stream.append(karma_lop)

    for idx in range(len(stream) - 1):
        stream[idx + 1].setInput(0, stream[idx])

    stage_node.layoutChildren(stream)
    return light_lops
