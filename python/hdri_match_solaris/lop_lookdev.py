"""
Lookdev Verification Rig Python LOP cook logic for Houdini Solaris.

Creates a set of lookdev verification spheres on the USD stage:
    - Mirror Chrome sphere (metallic=1.0, roughness=0.0)
    - 18% Neutral Grey sphere (diffuseColor=0.18, roughness=0.6)
    - Optional Matte White sphere (diffuseColor=0.9, roughness=0.9)

These are parented under ``/lookdev/`` and use ``UsdPreviewSurface``
materials for renderer-agnostic compatibility (works with Karma, Arnold,
Renderman, etc.).
"""
import math


def _create_smooth_sphere_mesh(stage, path, radius, rings=48, sectors=96):
    """Creates a high-density, smooth polygonal UV sphere mesh (UsdGeom.Mesh).

    Explicit mesh geometry with vertex normals prevents Hydra render delegates
    (Arnold HdArnold, Karma, Redshift) from using coarse implicit tessellations,
    ensuring flawless spherical silhouettes and reflections in all viewports.
    """
    from pxr import UsdGeom, Gf, Sdf
    prim = stage.GetPrimAtPath(path)
    if prim.IsValid() and prim.GetTypeName() != "Mesh":
        stage.RemovePrim(path)

    mesh = UsdGeom.Mesh.Define(stage, path)
    r_val = float(radius)

    points = []
    normals = []
    uvs = []

    for r in range(rings + 1):
        phi = math.pi * float(r) / float(rings)
        y = r_val * math.cos(phi)
        ny = math.cos(phi)
        sin_phi = math.sin(phi)
        v = 1.0 - (float(r) / float(rings))

        for s in range(sectors + 1):
            theta = 2.0 * math.pi * float(s) / float(sectors)
            x = r_val * sin_phi * math.cos(theta)
            z = r_val * sin_phi * math.sin(theta)
            nx = sin_phi * math.cos(theta)
            nz = sin_phi * math.sin(theta)
            u = float(s) / float(sectors)

            points.append(Gf.Vec3f(float(x), float(y), float(z)))
            normals.append(Gf.Vec3f(float(nx), float(ny), float(nz)))
            uvs.append(Gf.Vec2f(float(u), float(v)))

    face_vertex_counts = []
    face_vertex_indices = []

    for r in range(rings):
        for s in range(sectors):
            p0 = r * (sectors + 1) + s
            p1 = (r + 1) * (sectors + 1) + s
            p2 = (r + 1) * (sectors + 1) + (s + 1)
            p3 = r * (sectors + 1) + (s + 1)

            face_vertex_counts.append(4)
            face_vertex_indices.extend([p0, p1, p2, p3])

    mesh.GetPointsAttr().Set(points)
    mesh.GetFaceVertexCountsAttr().Set(face_vertex_counts)
    mesh.GetFaceVertexIndicesAttr().Set(face_vertex_indices)
    mesh.GetNormalsAttr().Set(normals)
    mesh.SetNormalsInterpolation(UsdGeom.Tokens.vertex)

    tex_coords = UsdGeom.PrimvarsAPI(mesh).CreatePrimvar(
        "st", Sdf.ValueTypeNames.TexCoord2fArray, UsdGeom.Tokens.vertex
    )
    tex_coords.Set(uvs)

    mesh.GetExtentAttr().Set([Gf.Vec3f(-r_val, -r_val, -r_val), Gf.Vec3f(r_val, r_val, r_val)])
    mesh.GetPrim().CreateAttribute("sphere_radius", Sdf.ValueTypeNames.Float).Set(r_val)
    mesh.GetPrim().CreateAttribute("radius", Sdf.ValueTypeNames.Double).Set(float(r_val))

    return mesh


def cook(node):
    """Main cook function for the Lookdev Spheres Python LOP.

    Args:
        node: ``hou.Node`` -- the Python LOP node being cooked.
    """
    import hou
    from pxr import UsdGeom, Gf, Sdf

    stage = node.editableStage()
    if not stage:
        raise hou.NodeError("No editable stage available.")

    from hdri_match_solaris.usd_helpers import (
        ensure_scope, create_preview_surface_material, bind_material
    )

    # ------------------------------------------------------------------
    # Read parameters
    # ------------------------------------------------------------------
    root_path = node.parm("root_path").eval() if node.parm("root_path") else "/lookdev"
    sphere_radius = node.parm("sphere_radius").eval() if node.parm("sphere_radius") else 0.15
    spacing = node.parm("spacing").eval() if node.parm("spacing") else 0.45
    include_white = node.parm("include_white").eval() if node.parm("include_white") else False
    include_macbeth = node.parm("include_macbeth").eval() if node.parm("include_macbeth") else False

    # Position offset (default 1.0m stand height)
    pos_x = node.parm("pos_x").eval() if node.parm("pos_x") else 0.0
    pos_y = node.parm("pos_y").eval() if node.parm("pos_y") else 1.0
    pos_z = node.parm("pos_z").eval() if node.parm("pos_z") else 0.0

    # ------------------------------------------------------------------
    # Create hierarchy
    # ------------------------------------------------------------------
    ensure_scope(stage, root_path)
    mtl_path = f"{root_path}/materials"
    ensure_scope(stage, mtl_path)
    spheres_path = f"{root_path}/spheres"

    # Create the spheres Xform group
    spheres_xform = UsdGeom.Xform.Define(stage, spheres_path)
    xformable = UsdGeom.Xformable(spheres_xform.GetPrim())
    xformable.ClearXformOpOrder()
    xformable.AddTranslateOp().Set(Gf.Vec3d(pos_x, pos_y, pos_z))

    # ------------------------------------------------------------------
    # Chrome Ball (High-res smooth mesh)
    # ------------------------------------------------------------------
    chrome_path = f"{spheres_path}/chrome_sphere"
    chrome = _create_smooth_sphere_mesh(stage, chrome_path, sphere_radius)
    chrome_xform = UsdGeom.Xformable(chrome.GetPrim())
    chrome_xform.ClearXformOpOrder()
    chrome_xform.AddTranslateOp().Set(Gf.Vec3d(-spacing * 0.5, sphere_radius, 0))

    create_preview_surface_material(
        stage, f"{mtl_path}/chrome_mtl",
        diffuse_color=(0.95, 0.95, 0.95),
        metallic=1.0,
        roughness=0.001,
        specular_color=(1.0, 1.0, 1.0),
    )
    bind_material(stage, chrome_path, f"{mtl_path}/chrome_mtl")

    # ------------------------------------------------------------------
    # 18% Grey Ball (High-res smooth mesh)
    # ------------------------------------------------------------------
    grey_path = f"{spheres_path}/grey_sphere"
    grey = _create_smooth_sphere_mesh(stage, grey_path, sphere_radius)
    grey_xform = UsdGeom.Xformable(grey.GetPrim())
    grey_xform.ClearXformOpOrder()
    grey_xform.AddTranslateOp().Set(Gf.Vec3d(spacing * 0.5, sphere_radius, 0))

    create_preview_surface_material(
        stage, f"{mtl_path}/grey_mtl",
        diffuse_color=(0.18, 0.18, 0.18),
        metallic=0.0,
        roughness=0.6,
    )
    bind_material(stage, grey_path, f"{mtl_path}/grey_mtl")

    # ------------------------------------------------------------------
    # Optional: Matte White Ball (High-res smooth mesh)
    # ------------------------------------------------------------------
    if include_white:
        white_path = f"{spheres_path}/white_sphere"
        white = _create_smooth_sphere_mesh(stage, white_path, sphere_radius)
        white_xform = UsdGeom.Xformable(white.GetPrim())
        white_xform.ClearXformOpOrder()
        white_xform.AddTranslateOp().Set(Gf.Vec3d(spacing * 1.5, sphere_radius, 0))

        create_preview_surface_material(
            stage, f"{mtl_path}/white_mtl",
            diffuse_color=(0.9, 0.9, 0.9),
            metallic=0.0,
            roughness=0.9,
        )
        bind_material(stage, white_path, f"{mtl_path}/white_mtl")

    # ------------------------------------------------------------------
    # Optional: Macbeth ColorChecker (flat card approximation)
    # ------------------------------------------------------------------
    if include_macbeth:
        _create_macbeth_card(stage, f"{spheres_path}/macbeth_card",
                             mtl_path, spacing, sphere_radius)

    # ------------------------------------------------------------------
    # Ground plane (only if explicitly enabled via parameter)
    # ------------------------------------------------------------------
    include_ground = node.parm("include_ground").eval() if node.parm("include_ground") else False
    if include_ground:
        ground_path = f"{root_path}/ground_plane"
        ground = UsdGeom.Mesh.Define(stage, ground_path)
        ground.GetPointsAttr().Set([
            Gf.Vec3f(-50, 0, -50),
            Gf.Vec3f(50, 0, -50),
            Gf.Vec3f(50, 0, 50),
            Gf.Vec3f(-50, 0, 50),
        ])
        ground.GetFaceVertexCountsAttr().Set([4])
        ground.GetFaceVertexIndicesAttr().Set([0, 1, 2, 3])
        ground.GetNormalsAttr().Set([Gf.Vec3f(0, 1, 0)] * 4)

        create_preview_surface_material(
            stage, f"{mtl_path}/ground_mtl",
            diffuse_color=(0.4, 0.4, 0.4),
            metallic=0.0,
            roughness=0.8,
        )
        bind_material(stage, ground_path, f"{mtl_path}/ground_mtl")
    else:
        # Clean up legacy ground plane if present
        if stage.GetPrimAtPath(f"{root_path}/ground_plane").IsValid():
            stage.RemovePrim(f"{root_path}/ground_plane")
        if stage.GetPrimAtPath(f"{mtl_path}/ground_mtl").IsValid():
            stage.RemovePrim(f"{mtl_path}/ground_mtl")

    print(f"[HDRI Match Solaris] Lookdev rig created at {root_path}")
    print(f"  Chrome sphere: {chrome_path}")
    print(f"  Grey sphere:   {grey_path}")


# ---------------------------------------------------------------------------
# Macbeth ColorChecker card
# ---------------------------------------------------------------------------

# Standard Macbeth 24-patch sRGB colours (approximate)
_MACBETH_COLORS_SRGB = [
    (0.400, 0.200, 0.161),  # Dark Skin
    (0.690, 0.451, 0.380),  # Light Skin
    (0.322, 0.400, 0.537),  # Blue Sky
    (0.294, 0.376, 0.180),  # Foliage
    (0.459, 0.388, 0.596),  # Blue Flower
    (0.369, 0.647, 0.588),  # Bluish Green
    (0.737, 0.420, 0.102),  # Orange
    (0.227, 0.271, 0.569),  # Purplish Blue
    (0.671, 0.259, 0.259),  # Moderate Red
    (0.286, 0.169, 0.318),  # Purple
    (0.537, 0.667, 0.133),  # Yellow Green
    (0.788, 0.576, 0.102),  # Orange Yellow
    (0.133, 0.176, 0.475),  # Blue
    (0.294, 0.529, 0.192),  # Green
    (0.553, 0.149, 0.137),  # Red
    (0.843, 0.749, 0.102),  # Yellow
    (0.596, 0.259, 0.471),  # Magenta
    (0.090, 0.400, 0.549),  # Cyan
    (0.871, 0.871, 0.871),  # White
    (0.588, 0.588, 0.588),  # Neutral 8
    (0.365, 0.365, 0.365),  # Neutral 6.5
    (0.200, 0.200, 0.200),  # Neutral 5
    (0.090, 0.090, 0.090),  # Neutral 3.5
    (0.031, 0.031, 0.031),  # Black
]


def _srgb_to_linear(c):
    """Convert sRGB gamma to scene-linear (gamma 2.2 approximation)."""
    return tuple(max(0.0, v) ** 2.2 for v in c)


def _create_macbeth_card(stage, card_path, mtl_path, spacing, sphere_radius):
    """Create a simplified Macbeth card as 24 small quads."""
    from pxr import UsdGeom, Gf, Sdf

    from hdri_match_solaris.usd_helpers import (
        create_preview_surface_material, bind_material
    )

    xform = UsdGeom.Xform.Define(stage, card_path)
    xfm = UsdGeom.Xformable(xform.GetPrim())
    xfm.ClearXformOpOrder()
    xfm.AddTranslateOp().Set(Gf.Vec3d(0, sphere_radius * 2.5, -spacing))
    xfm.AddRotateXOp().Set(-15.0)  # Slight tilt toward camera

    patch_size = sphere_radius * 0.3
    gap = patch_size * 0.1
    cols = 6
    rows = 4

    total_w = cols * (patch_size + gap)
    total_h = rows * (patch_size + gap)

    for idx, color_srgb in enumerate(_MACBETH_COLORS_SRGB):
        row = idx // cols
        col = idx % cols

        x = col * (patch_size + gap) - total_w * 0.5
        y = (rows - 1 - row) * (patch_size + gap) - total_h * 0.5

        patch_path = f"{card_path}/patch_{idx:02d}"
        patch = UsdGeom.Mesh.Define(stage, patch_path)
        patch.GetPointsAttr().Set([
            Gf.Vec3f(x, y, 0),
            Gf.Vec3f(x + patch_size, y, 0),
            Gf.Vec3f(x + patch_size, y + patch_size, 0),
            Gf.Vec3f(x, y + patch_size, 0),
        ])
        patch.GetFaceVertexCountsAttr().Set([4])
        patch.GetFaceVertexIndicesAttr().Set([0, 1, 2, 3])

        color_linear = _srgb_to_linear(color_srgb)
        mtl_name = f"macbeth_{idx:02d}"
        create_preview_surface_material(
            stage, f"{mtl_path}/{mtl_name}",
            diffuse_color=color_linear,
            metallic=0.0,
            roughness=0.9,
        )
        bind_material(stage, patch_path, f"{mtl_path}/{mtl_name}")


def create_lookdev_rig_node(
    stage_node,
    node_name="hdri_match_lookdev",
    pos=(0.0, 0.0, 0.0),
    radius=0.15,
    stand_height=1.2,
    rig_scale=1.0,
    spacing=0.45,
    include_white=True,
    include_macbeth=True,
    include_stand=True,
    root_path="/lookdev",
):
    """
    Create or update a PythonScript LOP in /stage that cooks the Lookdev Verification Rig.
    Instantiates Mirror Chrome sphere, 18% Neutral Grey sphere, optional Matte White sphere,
    and Macbeth ColorChecker chart mounted on a physically proportioned tripod stand
    at eye level (default 1.2m stand height above floor).

    Exposes spare parameters on the LOP node for live interactive adjustment of:
        - pos_x, floor_y, pos_z
        - stand_height (m)
        - sphere_radius (m)
        - rig_scale
        - spacing (m)
        - include_white, include_macbeth, include_stand
    """
    if not stage_node:
        return None
    import hou

    node = stage_node.node(node_name)
    if not node:
        node = stage_node.createNode("pythonscript", node_name)
        node.setColor(hou.Color((0.7, 0.3, 0.85)))

    # Ensure spare parameters exist on the node for live user tweaking
    ptg = node.parmTemplateGroup()
    parms_to_add = [
        hou.FloatParmTemplate("pos_x", "Position X", 1, default_value=[float(pos[0])]),
        hou.FloatParmTemplate("floor_y", "Floor Y (m)", 1, default_value=[float(pos[1])]),
        hou.FloatParmTemplate("pos_z", "Position Z", 1, default_value=[float(pos[2])]),
        hou.FloatParmTemplate("stand_height", "Stand Height (m)", 1, default_value=[float(stand_height)], min=0.0, max=3.0),
        hou.FloatParmTemplate("sphere_radius", "Sphere Radius (m)", 1, default_value=[float(radius)], min=0.02, max=1.5),
        hou.FloatParmTemplate("rig_scale", "Rig Scale", 1, default_value=[float(rig_scale)], min=0.1, max=10.0),
        hou.FloatParmTemplate("spacing", "Sphere Spacing (m)", 1, default_value=[float(spacing)], min=0.1, max=3.0),
        hou.ToggleParmTemplate("include_white", "Include Matte White Sphere", default_value=include_white),
        hou.ToggleParmTemplate("include_macbeth", "Include Macbeth Chart", default_value=include_macbeth),
        hou.ToggleParmTemplate("include_stand", "Include Tripod Stand", default_value=include_stand),
    ]

    modified_ptg = False
    for pt in parms_to_add:
        if not ptg.find(pt.name()):
            ptg.append(pt)
            modified_ptg = True
    if modified_ptg:
        node.setParmTemplateGroup(ptg)

    # Set parameters if they were explicitly provided
    if node.parm("pos_x"): node.parm("pos_x").set(float(pos[0]))
    if node.parm("floor_y"): node.parm("floor_y").set(float(pos[1]))
    if node.parm("pos_z"): node.parm("pos_z").set(float(pos[2]))
    if node.parm("stand_height"): node.parm("stand_height").set(float(stand_height))
    if node.parm("sphere_radius"): node.parm("sphere_radius").set(float(radius))
    if node.parm("rig_scale"): node.parm("rig_scale").set(float(rig_scale))
    if node.parm("spacing"): node.parm("spacing").set(float(spacing))
    if node.parm("include_white"): node.parm("include_white").set(bool(include_white))
    if node.parm("include_macbeth"): node.parm("include_macbeth").set(bool(include_macbeth))
    if node.parm("include_stand"): node.parm("include_stand").set(bool(include_stand))

    code = f'''import math
import hou
from pxr import UsdGeom, UsdShade, Gf, Sdf

node = hou.pwd()
stage = node.editableStage()
if stage:
    root = "{root_path}"
    pos_x = float(node.parm("pos_x").eval() if node.parm("pos_x") else {pos[0]})
    floor_y = float(node.parm("floor_y").eval() if node.parm("floor_y") else {pos[1]})
    pos_z = float(node.parm("pos_z").eval() if node.parm("pos_z") else {pos[2]})
    base_h = float(node.parm("stand_height").eval() if node.parm("stand_height") else {stand_height})
    base_r = float(node.parm("sphere_radius").eval() if node.parm("sphere_radius") else {radius})
    rig_scale = float(node.parm("rig_scale").eval() if node.parm("rig_scale") else {rig_scale})
    base_sp = float(node.parm("spacing").eval() if node.parm("spacing") else {spacing})
    include_white = bool(node.parm("include_white").eval() if node.parm("include_white") else {include_white})
    include_macbeth = bool(node.parm("include_macbeth").eval() if node.parm("include_macbeth") else {include_macbeth})
    include_stand = bool(node.parm("include_stand").eval() if node.parm("include_stand") else {include_stand})

    eff_radius = base_r * rig_scale
    eff_spacing = base_sp * rig_scale
    eff_stand_h = base_h * rig_scale
    crossbar_y = floor_y + eff_stand_h

    for path in [root, f"{{root}}/materials", f"{{root}}/spheres", f"{{root}}/stand", f"{{root}}/macbeth"]:
        if not stage.GetPrimAtPath(path).IsValid():
            UsdGeom.Scope.Define(stage, path)

    # 1. UsdPreviewSurface Materials
    # Mirror Chrome
    # Helper to setup multi-renderer surface outputs
    def _wire_mtl(mtl, shd):
        if not shd.GetOutput("surface"):
            shd.CreateOutput("surface", Sdf.ValueTypeNames.Token)
        s_api = shd.ConnectableAPI()
        mtl.CreateSurfaceOutput().ConnectToSource(s_api, "surface")
        mtl.CreateSurfaceOutput("karma").ConnectToSource(s_api, "surface")
        mtl.CreateSurfaceOutput("arnold").ConnectToSource(s_api, "surface")
        mtl.CreateSurfaceOutput("redshift").ConnectToSource(s_api, "surface")

    # Chrome Mirror Metal
    c_mtl = UsdShade.Material.Define(stage, f"{{root}}/materials/chrome_mtl")
    c_shd = UsdShade.Shader.Define(stage, f"{{root}}/materials/chrome_mtl/shader")
    c_shd.CreateIdAttr("UsdPreviewSurface")
    c_shd.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(0.95, 0.95, 0.95))
    c_shd.CreateInput("metallic", Sdf.ValueTypeNames.Float).Set(1.0)
    c_shd.CreateInput("roughness", Sdf.ValueTypeNames.Float).Set(0.001)
    c_shd.CreateInput("ior", Sdf.ValueTypeNames.Float).Set(100.0)
    _wire_mtl(c_mtl, c_shd)

    # 18% Neutral Grey
    g_mtl = UsdShade.Material.Define(stage, f"{{root}}/materials/grey_mtl")
    g_shd = UsdShade.Shader.Define(stage, f"{{root}}/materials/grey_mtl/shader")
    g_shd.CreateIdAttr("UsdPreviewSurface")
    g_shd.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(0.18, 0.18, 0.18))
    g_shd.CreateInput("metallic", Sdf.ValueTypeNames.Float).Set(0.0)
    g_shd.CreateInput("roughness", Sdf.ValueTypeNames.Float).Set(0.6)
    _wire_mtl(g_mtl, g_shd)

    # Matte White
    if include_white:
        w_mtl = UsdShade.Material.Define(stage, f"{{root}}/materials/white_mtl")
        w_shd = UsdShade.Shader.Define(stage, f"{{root}}/materials/white_mtl/shader")
        w_shd.CreateIdAttr("UsdPreviewSurface")
        w_shd.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(0.9, 0.9, 0.9))
        w_shd.CreateInput("metallic", Sdf.ValueTypeNames.Float).Set(0.0)
        w_shd.CreateInput("roughness", Sdf.ValueTypeNames.Float).Set(0.9)
        _wire_mtl(w_mtl, w_shd)

    # Stand Black Anodized Aluminum
    s_mtl = UsdShade.Material.Define(stage, f"{{root}}/materials/stand_mtl")
    s_shd = UsdShade.Shader.Define(stage, f"{{root}}/materials/stand_mtl/shader")
    s_shd.CreateIdAttr("UsdPreviewSurface")
    s_shd.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(0.06, 0.06, 0.06))
    s_shd.CreateInput("metallic", Sdf.ValueTypeNames.Float).Set(0.85)
    s_shd.CreateInput("roughness", Sdf.ValueTypeNames.Float).Set(0.3)
    _wire_mtl(s_mtl, s_shd)

    # 2. Geometry Spheres (High-Density Smooth Polygonal Meshes with Vertex Normals)
    def _build_smooth_sphere(stage_obj, prim_path, rad, rings=48, sectors=96):
        p = stage_obj.GetPrimAtPath(prim_path)
        if p.IsValid() and p.GetTypeName() != "Mesh":
            stage_obj.RemovePrim(prim_path)
        m = UsdGeom.Mesh.Define(stage_obj, prim_path)
        rv = float(rad)
        pts = []
        nrms = []
        uv_arr = []
        for r_i in range(rings + 1):
            phi = math.pi * float(r_i) / float(rings)
            y_v = rv * math.cos(phi)
            ny_v = math.cos(phi)
            sin_p = math.sin(phi)
            v_coord = 1.0 - (float(r_i) / float(rings))
            for s_i in range(sectors + 1):
                theta = 2.0 * math.pi * float(s_i) / float(sectors)
                x_v = rv * sin_p * math.cos(theta)
                z_v = rv * sin_p * math.sin(theta)
                nx_v = sin_p * math.cos(theta)
                nz_v = sin_p * math.sin(theta)
                u_coord = float(s_i) / float(sectors)
                pts.append(Gf.Vec3f(float(x_v), float(y_v), float(z_v)))
                nrms.append(Gf.Vec3f(float(nx_v), float(ny_v), float(nz_v)))
                uv_arr.append(Gf.Vec2f(float(u_coord), float(v_coord)))
        fc = []
        fi = []
        for r_i in range(rings):
            for s_i in range(sectors):
                p0 = r_i * (sectors + 1) + s_i
                p1 = (r_i + 1) * (sectors + 1) + s_i
                p2 = (r_i + 1) * (sectors + 1) + (s_i + 1)
                p3 = r_i * (sectors + 1) + (s_i + 1)
                fc.append(4)
                # Counter-clockwise winding so geometric normals point outward matching vertex normals
                fi.extend([p0, p3, p2, p1])
        m.GetPointsAttr().Set(pts)
        m.GetFaceVertexCountsAttr().Set(fc)
        m.GetFaceVertexIndicesAttr().Set(fi)
        m.GetNormalsAttr().Set(nrms)
        m.SetNormalsInterpolation(UsdGeom.Tokens.vertex)
        m.CreateOrientationAttr().Set(UsdGeom.Tokens.rightHanded)
        m.CreateDoubleSidedAttr().Set(True)
        st_pv = UsdGeom.PrimvarsAPI(m).CreatePrimvar("st", Sdf.ValueTypeNames.TexCoord2fArray, UsdGeom.Tokens.vertex)
        st_pv.Set(uv_arr)
        m.GetExtentAttr().Set([Gf.Vec3f(-rv, -rv, -rv), Gf.Vec3f(rv, rv, rv)])
        m.GetPrim().CreateAttribute("sphere_radius", Sdf.ValueTypeNames.Float).Set(rv)
        return m

    # Chrome Ball
    ch = _build_smooth_sphere(stage, f"{{root}}/spheres/chrome_sphere", eff_radius)
    xf_ch = UsdGeom.Xformable(ch.GetPrim())
    xf_ch.ClearXformOpOrder()
    xf_ch.AddTranslateOp().Set(Gf.Vec3d(pos_x - eff_spacing * 0.5, crossbar_y + eff_radius, pos_z))
    UsdShade.MaterialBindingAPI.Apply(ch.GetPrim()).Bind(c_mtl)

    # Grey Ball
    gr = _build_smooth_sphere(stage, f"{{root}}/spheres/grey_sphere", eff_radius)
    xf_gr = UsdGeom.Xformable(gr.GetPrim())
    xf_gr.ClearXformOpOrder()
    xf_gr.AddTranslateOp().Set(Gf.Vec3d(pos_x + eff_spacing * 0.5, crossbar_y + eff_radius, pos_z))
    UsdShade.MaterialBindingAPI.Apply(gr.GetPrim()).Bind(g_mtl)

    # White Ball
    if include_white:
        wh = _build_smooth_sphere(stage, f"{{root}}/spheres/white_sphere", eff_radius)
        xf_wh = UsdGeom.Xformable(wh.GetPrim())
        xf_wh.ClearXformOpOrder()
        xf_wh.AddTranslateOp().Set(Gf.Vec3d(pos_x + eff_spacing * 1.5, crossbar_y + eff_radius, pos_z))
        UsdShade.MaterialBindingAPI.Apply(wh.GetPrim()).Bind(w_mtl)
    elif stage.GetPrimAtPath(f"{{root}}/spheres/white_sphere").IsValid():
        stage.RemovePrim(f"{{root}}/spheres/white_sphere")

    # 3. Physically Proportioned Lookdev Stand
    if include_stand and eff_stand_h > 0.05:
        # Telescopic Central Mast
        mast = UsdGeom.Cylinder.Define(stage, f"{{root}}/stand/mast")
        mast.GetRadiusAttr().Set(0.018 * rig_scale)
        mast.GetHeightAttr().Set(eff_stand_h)
        mast.GetAxisAttr().Set("Y")
        xf_m = UsdGeom.Xformable(mast.GetPrim())
        xf_m.ClearXformOpOrder()
        xf_m.AddTranslateOp().Set(Gf.Vec3d(pos_x, floor_y + eff_stand_h * 0.5, pos_z))
        UsdShade.MaterialBindingAPI.Apply(mast.GetPrim()).Bind(s_mtl)

        # Tripod Base Legs (touching the floor at floor_y)
        leg_radius = 0.012 * rig_scale
        foot_spread = 0.38 * rig_scale
        collar_h = 0.30 * rig_scale
        for i in range(3):
            angle = i * (2.0 * math.pi / 3.0)
            lx = math.sin(angle) * foot_spread
            lz = math.cos(angle) * foot_spread
            leg_len = math.sqrt(lx*lx + lz*lz + collar_h*collar_h)
            leg = UsdGeom.Cylinder.Define(stage, f"{{root}}/stand/leg_{{i+1}}")
            leg.GetRadiusAttr().Set(leg_radius)
            leg.GetHeightAttr().Set(leg_len)
            leg.GetAxisAttr().Set("Y")
            xf_l = UsdGeom.Xformable(leg.GetPrim())
            xf_l.ClearXformOpOrder()
            mx = pos_x + lx * 0.5
            my = floor_y + collar_h * 0.5
            mz = pos_z + lz * 0.5
            xf_l.AddTranslateOp().Set(Gf.Vec3d(mx, my, mz))
            rot_y = math.degrees(math.atan2(lx, lz))
            rot_x = math.degrees(math.atan2(math.sqrt(lx*lx + lz*lz), collar_h))
            xf_l.AddRotateYOp().Set(rot_y)
            xf_l.AddRotateXOp().Set(rot_x)
            UsdShade.MaterialBindingAPI.Apply(leg.GetPrim()).Bind(s_mtl)

        # Horizontal T-bar Crossbar
        cbar_len = eff_spacing * (3.2 if include_white else 2.2)
        cbar_cx = pos_x + (eff_spacing * 0.5 if include_white else 0.0)
        cbar = UsdGeom.Cylinder.Define(stage, f"{{root}}/stand/crossbar")
        cbar.GetRadiusAttr().Set(0.012 * rig_scale)
        cbar.GetHeightAttr().Set(cbar_len)
        cbar.GetAxisAttr().Set("X")
        xf_c = UsdGeom.Xformable(cbar.GetPrim())
        xf_c.ClearXformOpOrder()
        xf_c.AddTranslateOp().Set(Gf.Vec3d(cbar_cx, crossbar_y, pos_z))
        UsdShade.MaterialBindingAPI.Apply(cbar.GetPrim()).Bind(s_mtl)
    else:
        # Clean up stand prims if disabled
        for sp in ["mast", "leg_1", "leg_2", "leg_3", "crossbar"]:
            if stage.GetPrimAtPath(f"{{root}}/stand/{{sp}}").IsValid():
                stage.RemovePrim(f"{{root}}/stand/{{sp}}")

    # 4. Optional Macbeth ColorChecker Chart (mounted below crossbar)
    if include_macbeth:
        card_path = f"{{root}}/macbeth/chart"
        card_xform = UsdGeom.Xform.Define(stage, card_path)
        xf_cd = UsdGeom.Xformable(card_xform.GetPrim())
        xf_cd.ClearXformOpOrder()
        chart_cx = pos_x + (eff_spacing * 0.5 if include_white else 0.0)
        chart_cy = crossbar_y - eff_radius * 0.9
        xf_cd.AddTranslateOp().Set(Gf.Vec3d(chart_cx, chart_cy, pos_z + 0.02 * rig_scale))
        xf_cd.AddRotateXOp().Set(-8.0)

        # 24 Macbeth standard patches
        patch_w = eff_radius * 0.18
        patch_h = eff_radius * 0.18
        gap = patch_w * 0.12
        cols = 6
        rows = 4
        tot_w = cols * (patch_w + gap)
        tot_h = rows * (patch_h + gap)

        macbeth_srgb = [
            (0.400, 0.200, 0.161), (0.690, 0.451, 0.380), (0.322, 0.400, 0.537), (0.294, 0.376, 0.180),
            (0.459, 0.388, 0.596), (0.369, 0.647, 0.588), (0.737, 0.420, 0.102), (0.227, 0.271, 0.569),
            (0.671, 0.259, 0.259), (0.286, 0.169, 0.318), (0.537, 0.667, 0.133), (0.788, 0.576, 0.102),
            (0.133, 0.176, 0.475), (0.294, 0.529, 0.192), (0.553, 0.149, 0.137), (0.843, 0.749, 0.102),
            (0.596, 0.259, 0.471), (0.090, 0.400, 0.549), (0.871, 0.871, 0.871), (0.588, 0.588, 0.588),
            (0.365, 0.365, 0.365), (0.200, 0.200, 0.200), (0.090, 0.090, 0.090), (0.031, 0.031, 0.031),
        ]

        # Chart backing frame
        frame = UsdGeom.Mesh.Define(stage, f"{{card_path}}/backing")
        hw = tot_w * 0.55
        hh = tot_h * 0.55
        frame.GetPointsAttr().Set([Gf.Vec3f(-hw, -hh, -0.002), Gf.Vec3f(hw, -hh, -0.002), Gf.Vec3f(hw, hh, -0.002), Gf.Vec3f(-hw, hh, -0.002)])
        frame.GetFaceVertexCountsAttr().Set([4])
        frame.GetFaceVertexIndicesAttr().Set([0, 1, 2, 3])
        UsdShade.MaterialBindingAPI.Apply(frame.GetPrim()).Bind(s_mtl)

        for idx, cs in enumerate(macbeth_srgb):
            r_idx = idx // cols
            c_idx = idx % cols
            px = c_idx * (patch_w + gap) - tot_w * 0.5
            py = (rows - 1 - r_idx) * (patch_h + gap) - tot_h * 0.5
            p_mesh = UsdGeom.Mesh.Define(stage, f"{{card_path}}/patch_{{idx:02d}}")
            p_mesh.GetPointsAttr().Set([
                Gf.Vec3f(px, py, 0.001), Gf.Vec3f(px + patch_w, py, 0.001),
                Gf.Vec3f(px + patch_w, py + patch_h, 0.001), Gf.Vec3f(px, py + patch_h, 0.001)
            ])
            p_mesh.GetFaceVertexCountsAttr().Set([4])
            p_mesh.GetFaceVertexIndicesAttr().Set([0, 1, 2, 3])

            m_path = f"{{root}}/materials/mb_{{idx:02d}}"
            m_mat = UsdShade.Material.Define(stage, m_path)
            m_shd = UsdShade.Shader.Define(stage, f"{{m_path}}/shader")
            m_shd.CreateIdAttr("UsdPreviewSurface")
            lin_c = (max(0.0, cs[0])**2.2, max(0.0, cs[1])**2.2, max(0.0, cs[2])**2.2)
            m_shd.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(lin_c[0], lin_c[1], lin_c[2]))
            m_shd.CreateInput("roughness", Sdf.ValueTypeNames.Float).Set(0.85)
            m_shd.CreateInput("metallic", Sdf.ValueTypeNames.Float).Set(0.0)
            m_out = m_mat.CreateSurfaceOutput()
            m_out.ConnectToSource(m_shd.ConnectableAPI(), "surface")
            m_mat.CreateSurfaceOutput("karma").ConnectToSource(m_shd.ConnectableAPI(), "surface")
            m_mat.CreateSurfaceOutput("arnold").ConnectToSource(m_shd.ConnectableAPI(), "surface")
            m_mat.CreateSurfaceOutput("redshift").ConnectToSource(m_shd.ConnectableAPI(), "surface")
            UsdShade.MaterialBindingAPI.Apply(p_mesh.GetPrim()).Bind(m_mat)
    elif stage.GetPrimAtPath(f"{{root}}/macbeth").IsValid():
        stage.RemovePrim(f"{{root}}/macbeth")
'''
    node.parm("python").set(code)
    node.bypass(False)
    node.cook(force=True)
    return node

