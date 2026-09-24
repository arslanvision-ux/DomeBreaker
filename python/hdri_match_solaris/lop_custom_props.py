"""
lop_custom_props.py - Custom 3D Props Integration & Gaussian Splat Projection Texturing for Solaris.

Allows artists to load their own custom 3D models (USD, OBJ, FBX, BGEO, ABC) into the
Solaris architectural room hierarchy (/stage/room/custom_props/{prop_name}), automatically
snap them to the detected splat floor, and camera-project the high-resolution baked HDRI/albedo
texture directly onto the props.
"""

import os, sys, math, json
import hou
from pxr import Usd, UsdGeom, UsdShade, Sdf, Gf, Vt


def ensure_usd_compatible_mesh(file_path):
    """
    Ensure the mesh file is in a format Solaris reference LOP natively supports.
    Solaris reference LOP natively loads: .usd, .usda, .usdc, .bgeo, .bgeo.sc, .obj, .abc.
    For .fbx or other formats, converts and caches to .bgeo.sc in a local cache directory.
    """
    if not file_path or not os.path.isfile(file_path):
        return file_path

    ext = os.path.splitext(file_path)[1].lower()
    if ext in [".usd", ".usda", ".usdc", ".bgeo", ".bgeo.sc", ".obj", ".abc"]:
        return file_path

    if ext == ".fbx":
        try:
            cache_dir = os.path.join(os.path.dirname(file_path), "_usd_cache")
            os.makedirs(cache_dir, exist_ok=True)
            base = os.path.splitext(os.path.basename(file_path))[0]
            out_bgeo = os.path.join(cache_dir, f"{base}.bgeo.sc").replace("\\", "/")

            if not os.path.isfile(out_bgeo) or os.path.getmtime(file_path) > os.path.getmtime(out_bgeo):
                g = hou.Geometry()
                g.loadFromFile(file_path)
                g.saveToFile(out_bgeo)
            return out_bgeo
        except Exception as e:
            print(f"[HDRI Match] FBX conversion note: {e}")
            return file_path

    return file_path


def clear_custom_props_nodes(stage_node):
    """Cleanly remove existing custom prop LOP nodes from /stage."""
    if not stage_node or not hou:
        return
    for name in ["splat_custom_props_ref", "splat_custom_props_xform", "splat_custom_props_projector"]:
        node = stage_node.node(name)
        if node:
            node.destroy()


def setup_custom_props_nodes(
    stage_node,
    custom_props,
    probe_pos=(0.0, 1.5, 0.0),
    floor_y=0.0,
    hdri_texture="",
    renderer_target="all",
    mat_mode="pbr",
    wire_into_stream=True,
):
    """
    Build native Solaris nodes for referencing custom 3D props and camera-projecting
    Gaussian Splat textures onto them.

    Args:
        stage_node: Solaris /stage LOP network node.
        custom_props: list of dicts:
            [
                {
                    "name": "sofa_1",
                    "file_path": "path/to/sofa.usd",
                    "texture_path": "",
                    "uv_mode": "mesh_uv" or "project_splat",
                    "tx": 0.0, "ty": 0.0, "tz": 0.0,
                    "rx": 0.0, "ry": 0.0, "rz": 0.0,
                    "scale": 1.0,
                    "snap_floor": True,
                    "project_texture": True,
                    "cast_shadows": True,
                    "enabled": True,
                }, ...
            ]
        probe_pos: (X, Y, Z) capture origin of Gaussian Splats.
        floor_y: Detected room floor height in meters.
        hdri_texture: Path to baked equirectangular HDRI / albedo EXR map.
        renderer_target: Target renderer ("all", "karma", "arnold", "redshift", "preview").
        mat_mode: Material mode ("pbr", "emissive", "pbr_emissive").
        wire_into_stream: Automatically wire nodes into the Solaris LOP stream.

    Returns:
        Tail LOP node of the custom props chain, or None.
    """
    if not stage_node or not hou:
        return None

    from hdri_match_solaris import renderer_materials

    # Filter enabled props with valid file paths
    active_props = []
    for p in (custom_props or []):
        if not p.get("enabled", True):
            continue
        fp = p.get("file_path", "").strip()
        if fp and os.path.isfile(fp):
            # Ensure compatible format
            comp_fp = ensure_usd_compatible_mesh(fp)
            prop_copy = dict(p)
            prop_copy["file_path"] = comp_fp.replace("\\", "/")
            active_props.append(prop_copy)

    if not active_props:
        clear_custom_props_nodes(stage_node)
        room_node = stage_node.node("splat_room_architecture")
        if room_node:
            room_node.cook(force=True)
        return None

    # 1. Reference LOP node
    ref_node = stage_node.node("splat_custom_props_ref")
    if not ref_node:
        ref_node = stage_node.createNode("reference", "splat_custom_props_ref")
        ref_node.setColor(hou.Color((0.3, 0.65, 0.5)))

    # Configure multiple file references in the reference LOP
    num_files = len(active_props)
    if ref_node.parm("num_files"):
        ref_node.parm("num_files").set(num_files)

    for i, prop in enumerate(active_props):
        idx = i + 1
        p_name = prop.get("name", f"prop_{idx}").strip().replace(" ", "_")
        p_file = prop.get("file_path", "").replace("\\", "/")
        prim_path = f"/stage/room/custom_props/{p_name}"

        fp_parm = ref_node.parm(f"filepath{idx}")
        pp_parm = ref_node.parm(f"primpath{idx}")
        if fp_parm:
            fp_parm.set(p_file)
        if pp_parm:
            pp_parm.set(prim_path)

    # 2. Xform & Camera-Projection PythonScript LOP
    proj_node = stage_node.node("splat_custom_props_projector")
    if not proj_node:
        proj_node = stage_node.createNode("pythonscript", "splat_custom_props_projector")
        proj_node.setColor(hou.Color((0.25, 0.75, 0.6)))

    cam_x, cam_y, cam_z = probe_pos
    props_json = json.dumps(active_props)
    clean_hdri = hdri_texture.replace("\\", "/") if hdri_texture else ""

    py_code = [
        'import math, json, os',
        'from pxr import Usd, UsdGeom, UsdShade, Gf, Vt, Sdf',
        '',
        'stage = hou.pwd().editableStage()',
        f'cam_pos = Gf.Vec3f({cam_x}, {cam_y}, {cam_z})',
        f'floor_y = float({floor_y})',
        f'default_renderer_target = "{renderer_target}"',
        f'default_mat_mode = "{mat_mode}"',
        f'tex_file = r"{clean_hdri}"',
        f'props_data = json.loads(r"""{props_json}""")',
        '',
        '# --- Automatic Projection Texture Resolution ---',
        'if not tex_file or not os.path.isfile(tex_file):',
        '    for d_path in ["/stage/hdri_dome", "/stage/lights/dome", "/stage/lights/hdri_dome", "/stage/dome_light", "/environment/hdri_dome"]:',
        '        d_prim = stage.GetPrimAtPath(d_path)',
        '        if d_prim and d_prim.IsValid():',
        '            for a_name in ["inputs:texture:file", "texture:file", "filePath"]:',
        '                attr = d_prim.GetAttribute(a_name)',
        '                if attr and attr.HasValue():',
        '                    val = attr.Get()',
        '                    if val:',
        '                        v_str = str(val.path if hasattr(val, "path") else val).strip()',
        '                        if v_str and os.path.isfile(v_str):',
        '                            tex_file = v_str.replace(chr(92), "/")',
        '                            break',
        '        if tex_file and os.path.isfile(tex_file):',
        '            break',
        '',
        'if not tex_file or not os.path.isfile(tex_file):',
        '    cands = [',
        '        "E:/PROJECTS/HDRI_MATCH_SOLARIS/SPLATS/Reception room/splatforge_cache/textures/hdri_dome.exr",',
        '        "E:/PROJECTS/HDRI_MATCH_SOLARIS/SPLATS/Reception room/splatforge_cache/textures/hdri_dome_2048x1024.exr",',
        '        "E:/PROJECTS/HDRI_MATCH_SOLARIS/hdri_match/planar_textures_4096/floor_albedo.exr",',
        '        "hdri_match/planar_textures_4096/floor_albedo.exr",',
        '    ]',
        '    for c in cands:',
        '        if os.path.isfile(c):',
        '            tex_file = c.replace(chr(92), "/")',
        '            break',
        '',
        'def project_point(p):',
        '    dx = p[0] - cam_pos[0]',
        '    dy = p[1] - cam_pos[1]',
        '    dz = p[2] - cam_pos[2]',
        '    dist = math.sqrt(dx*dx + dy*dy + dz*dz)',
        '    if dist < 1e-6: dist = 1e-6',
        '    vx, vy, vz = dx / dist, dy / dist, dz / dist',
        '    u = (math.atan2(-vx, vz) / (2.0 * math.pi)) % 1.0',
        '    v = 0.5 + (math.asin(max(-1.0, min(1.0, vy))) / math.pi)',
        '    return Gf.Vec2f(float(u), float(v))',
        '',
    ]

    # Embed native multi-renderer create_native_material function
    py_code.extend(renderer_materials.gen_create_material_function())

    py_code.extend([
        '# 1. Transform props (translation, rotation, scale, floor snap, shadows)',
        'props_by_name = {}',
        'for prop in props_data:',
        '    p_name = prop.get("name", "prop").strip().replace(" ", "_")',
        '    props_by_name[p_name] = prop',
        '    prim_path = f"/stage/room/custom_props/{p_name}"',
        '    prim = stage.GetPrimAtPath(prim_path)',
        '    if not prim or not prim.IsValid():',
        '        continue',
        '',
        '    xformable = UsdGeom.Xformable(prim)',
        '    xformable.ClearXformOpOrder()',
        '',
        '    tx = float(prop.get("tx", 0.0))',
        '    ty = float(prop.get("ty", 0.0))',
        '    tz = float(prop.get("tz", 0.0))',
        '    rx = float(prop.get("rx", 0.0))',
        '    ry = float(prop.get("ry", 0.0))',
        '    rz = float(prop.get("rz", 0.0))',
        '    s_val = max(1e-4, float(prop.get("scale", 1.0)))',
        '',
        '    # Automatic floor snapping (sets bottom bounding box at floor_y)',
        '    if prop.get("snap_floor", True):',
        '        bbox_cache = UsdGeom.BBoxCache(Usd.TimeCode.Default(), [UsdGeom.Tokens.default_])',
        '        bbox = bbox_cache.ComputeUntransformedBound(prim)',
        '        box_range = bbox.ComputeAlignedRange()',
        '        if not box_range.IsEmpty():',
        '            min_y_local = box_range.GetMin()[1] * s_val',
        '            ty = floor_y - min_y_local + float(prop.get("ty", 0.0))',
        '        else:',
        '            ty = floor_y + float(prop.get("ty", 0.0))',
        '',
        '    op_t = xformable.AddTranslateOp(UsdGeom.XformOp.PrecisionFloat)',
        '    op_t.Set(Gf.Vec3f(tx, ty, tz))',
        '    op_r = xformable.AddRotateXYZOp(UsdGeom.XformOp.PrecisionFloat)',
        '    op_r.Set(Gf.Vec3f(rx, ry, rz))',
        '    op_s = xformable.AddScaleOp(UsdGeom.XformOp.PrecisionFloat)',
        '    op_s.Set(Gf.Vec3f(s_val, s_val, s_val))',
        '',
        '    # Shadow visibility',
        '    cast_sh = prop.get("cast_shadows", True)',
        '    if not cast_sh:',
        '        prim.CreateAttribute("primvars:karma:object:rendervisibility", Sdf.ValueTypeNames.String, False).Set("primary diffuse reflect refract")',
        '        prim.CreateAttribute("primvars:arnold:visibility:shadow", Sdf.ValueTypeNames.Bool, False).Set(False)',
        '        prim.CreateAttribute("arnold:visibility:shadow", Sdf.ValueTypeNames.Int, False).Set(0)',
        '        prim.CreateAttribute("primvars:redshift:object:MESHFLAG_SHADOWCASTER", Sdf.ValueTypeNames.Bool, False).Set(False)',
        '        prim.CreateAttribute("redshift:object:MESHFLAG_SHADOWCASTER", Sdf.ValueTypeNames.Bool, False).Set(False)',
        '    else:',
        '        prim.CreateAttribute("primvars:karma:object:rendervisibility", Sdf.ValueTypeNames.String, False).Set("*")',
        '        prim.CreateAttribute("primvars:arnold:visibility:shadow", Sdf.ValueTypeNames.Bool, False).Set(True)',
        '        prim.CreateAttribute("arnold:visibility:shadow", Sdf.ValueTypeNames.Int, False).Set(1)',
        '        prim.CreateAttribute("primvars:redshift:object:MESHFLAG_SHADOWCASTER", Sdf.ValueTypeNames.Bool, False).Set(True)',
        '        prim.CreateAttribute("redshift:object:MESHFLAG_SHADOWCASTER", Sdf.ValueTypeNames.Bool, False).Set(True)',
        '',
        '# 2. Material Creation, Projection & Binding per Prop',
        'props_scope = stage.GetPrimAtPath("/stage/room/custom_props")',
        'if props_scope and props_scope.IsValid():',
        '    xf_cache = UsdGeom.XformCache()',
        '    for prop_prim in props_scope.GetChildren():',
        '        p_name = prop_prim.GetName()',
        '        if p_name.endswith("_mat"):',
        '            continue',
        '        p_cfg = props_by_name.get(p_name, {})',
        '        p_tex = p_cfg.get("texture_path", "").strip().replace(chr(92), "/")',
        '        has_selected_texture = bool(p_tex and os.path.isfile(p_tex))',
        '        do_proj = p_cfg.get("project_texture", True)',
        '        uv_mode = p_cfg.get("uv_mode", "mesh_uv")',
        '        p_rough = float(p_cfg.get("roughness", 0.65))',
        '        p_mode = p_cfg.get("mat_mode", default_mat_mode)',
        '        p_target = p_cfg.get("renderer_target", default_renderer_target)',
        '',
        '        # Respect shader and texture selection:',
        '        # If texture is explicitly selected for this prop, use that texture.',
        '        # Otherwise, project the high-resolution baked HDRI / splat texture.',
        '        if has_selected_texture:',
        '            tex_to_use = p_tex',
        '            use_projection = (uv_mode == "project_splat")',
        '        else:',
        '            tex_to_use = tex_file if (do_proj and tex_file and os.path.isfile(tex_file)) else ""',
        '            use_projection = True',
        '',
        '        # Build native delegated material respecting shader selection (Karma MaterialX, Arnold, Redshift, etc.)',
        '        mat_path = Sdf.Path(f"/stage/room/custom_props/{p_name}_mat")',
        '        prop_mat = create_native_material(',
        '            stage,',
        '            mat_path,',
        '            tex_to_use,',
        '            roughness_val=p_rough,',
        '            mat_mode_val=p_mode,',
        '            emissive_mult_val=1.0,',
        '            st_varname="st",',
        '            renderer_target=p_target,',
        '        )',
        '',
        '        # Wire sibling PBR maps if an explicit prop texture directory has them',
        '        if has_selected_texture and os.path.isfile(p_tex):',
        '            tex_dir = os.path.dirname(p_tex)',
        '            if os.path.isdir(tex_dir):',
        '                rough_map, metal_map, nor_map = None, None, None',
        '                for fn in os.listdir(tex_dir):',
        '                    fl = fn.lower()',
        '                    if not fl.endswith((".exr", ".hdr", ".png", ".jpg", ".jpeg", ".tif", ".tiff")):',
        '                        continue',
        '                    fp = os.path.join(tex_dir, fn).replace(chr(92), "/")',
        '                    if ("rough" in fl or "_r." in fl) and not rough_map:',
        '                        rough_map = fp',
        '                    elif ("metal" in fl or "_m." in fl) and not metal_map:',
        '                        metal_map = fp',
        '                    elif ("nor" in fl or "_n." in fl or "normal" in fl) and not nor_map:',
        '                        nor_map = fp',
        '                km_prim = stage.GetPrimAtPath(mat_path.AppendChild("KarmaMtlX"))',
        '                km_uv_prim = stage.GetPrimAtPath(mat_path.AppendChild("KarmaTexcoord"))',
        '                if km_prim and km_prim.IsValid() and km_uv_prim and km_uv_prim.IsValid():',
        '                    km_sh = UsdShade.Shader(km_prim)',
        '                    km_uv_sh = UsdShade.Shader(km_uv_prim)',
        '                    if rough_map:',
        '                        km_r = UsdShade.Shader.Define(stage, mat_path.AppendChild("KarmaRoughness"))',
        '                        km_r.CreateIdAttr("ND_image_float")',
        '                        km_r.CreateInput("file", Sdf.ValueTypeNames.Asset).Set(Sdf.AssetPath(rough_map))',
        '                        km_r.CreateInput("texcoord", Sdf.ValueTypeNames.Float2).ConnectToSource(km_uv_sh.ConnectableAPI(), "out")',
        '                        km_r.CreateOutput("out", Sdf.ValueTypeNames.Float)',
        '                        km_sh.CreateInput("specular_roughness", Sdf.ValueTypeNames.Float).ConnectToSource(km_r.ConnectableAPI(), "out")',
        '                    if metal_map:',
        '                        km_m = UsdShade.Shader.Define(stage, mat_path.AppendChild("KarmaMetallic"))',
        '                        km_m.CreateIdAttr("ND_image_float")',
        '                        km_m.CreateInput("file", Sdf.ValueTypeNames.Asset).Set(Sdf.AssetPath(metal_map))',
        '                        km_m.CreateInput("texcoord", Sdf.ValueTypeNames.Float2).ConnectToSource(km_uv_sh.ConnectableAPI(), "out")',
        '                        km_m.CreateOutput("out", Sdf.ValueTypeNames.Float)',
        '                        km_sh.CreateInput("metalness", Sdf.ValueTypeNames.Float).ConnectToSource(km_m.ConnectableAPI(), "out")',
        '                    if nor_map:',
        '                        km_n_img = UsdShade.Shader.Define(stage, mat_path.AppendChild("KarmaNormalImage"))',
        '                        km_n_img.CreateIdAttr("ND_image_vector3")',
        '                        km_n_img.CreateInput("file", Sdf.ValueTypeNames.Asset).Set(Sdf.AssetPath(nor_map))',
        '                        km_n_img.CreateInput("texcoord", Sdf.ValueTypeNames.Float2).ConnectToSource(km_uv_sh.ConnectableAPI(), "out")',
        '                        km_n_img.CreateOutput("out", Sdf.ValueTypeNames.Float3)',
        '                        km_n_map = UsdShade.Shader.Define(stage, mat_path.AppendChild("KarmaNormalMap"))',
        '                        km_n_map.CreateIdAttr("ND_normalmap")',
        '                        km_n_map.CreateInput("in", Sdf.ValueTypeNames.Float3).ConnectToSource(km_n_img.ConnectableAPI(), "out")',
        '                        km_n_map.CreateOutput("out", Sdf.ValueTypeNames.Float3)',
        '                        km_sh.CreateInput("normal", Sdf.ValueTypeNames.Float3).ConnectToSource(km_n_map.ConnectableAPI(), "out")',
        '',
        '        # Bind material to the prop root prim',
        '        if prop_mat:',
        '            UsdShade.MaterialBindingAPI.Apply(prop_prim).Bind(prop_mat)',
        '',
        '        for prim in Usd.PrimRange(prop_prim):',
        '            if prim.IsA(UsdGeom.Mesh):',
        '                mesh = UsdGeom.Mesh(prim)',
        '                mesh.CreateSubdivisionSchemeAttr().Set(UsdGeom.Tokens.none)',
        '                prim.CreateAttribute("primvars:karma:object:dicing:quality", Sdf.ValueTypeNames.Float, False).Set(0.0)',
        '                prim.CreateAttribute("primvars:arnold:subdiv_type", Sdf.ValueTypeNames.String, False).Set("none")',
        '                pv_api = UsdGeom.PrimvarsAPI(prim)',
        '                st_pv = pv_api.GetPrimvar("st")',
        '                uv_pv = pv_api.GetPrimvar("uv")',
        '                has_native = bool(st_pv) or bool(uv_pv)',
        '',
        '                if use_projection or not has_native:',
        '                    pts = mesh.GetPointsAttr().Get()',
        '                    if pts:',
        '                        w_xf = xf_cache.GetLocalToWorldTransform(prim)',
        '                        uvs = [project_point(w_xf.Transform(pt)) for pt in pts]',
        '                        st_new = pv_api.CreatePrimvar("st", Sdf.ValueTypeNames.TexCoord2fArray, UsdGeom.Tokens.vertex)',
        '                        st_new.Set(Vt.Vec2fArray(uvs))',
        '                        uv_new = pv_api.CreatePrimvar("uv", Sdf.ValueTypeNames.TexCoord2fArray, UsdGeom.Tokens.vertex)',
        '                        uv_new.Set(Vt.Vec2fArray(uvs))',
        '                else:',
        '                    # Preserve native unwrapped UVs from the 3D model',
        '                    if not st_pv and uv_pv:',
        '                        vals = uv_pv.Get()',
        '                        if vals:',
        '                            st_new = pv_api.CreatePrimvar("st", Sdf.ValueTypeNames.TexCoord2fArray, uv_pv.GetInterpolation())',
        '                            st_new.Set(vals)',
        '                    elif not uv_pv and st_pv:',
        '                        vals = st_pv.Get()',
        '                        if vals:',
        '                            uv_new = pv_api.CreatePrimvar("uv", Sdf.ValueTypeNames.TexCoord2fArray, st_pv.GetInterpolation())',
        '                            uv_new.Set(vals)',
        '',
        '                if prop_mat:',
        '                    UsdShade.MaterialBindingAPI.Apply(prim).Bind(prop_mat)',
        '                    for subset in UsdGeom.Subset.GetAllGeomSubsets(mesh):',
        '                        UsdShade.MaterialBindingAPI.Apply(subset.GetPrim()).Bind(prop_mat)',
    ])

    proj_node.parm("python").set("\n".join(py_code))
    ref_node.bypass(False)
    proj_node.bypass(False)

    # Wire reference -> projector
    proj_node.setInput(0, ref_node)
    ref_node.cook(force=True)
    proj_node.cook(force=True)

    # Wire into Solaris stage chain using deterministic acyclic wiring
    if wire_into_stream:
        from hdri_match_solaris.lop_splat import wire_solaris_stage_stream
        wire_solaris_stage_stream(stage_node)

    return proj_node

