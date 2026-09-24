"""
Renderer-native material builder code generators for Houdini Solaris USD.

Provides code-generation functions that produce Python source strings for
creating Arnold Standard Surface, Karma MaterialX, and Redshift StandardMaterial
shader graphs alongside a UsdPreviewSurface fallback for GL viewport display.

Each generator returns a list of Python code strings suitable for embedding
in a PythonScript LOP node's ``python`` parameter, following the existing
code-generation pattern used throughout DomeBreaker.

All materials are wired to the correct render-context surface outputs:
    - ``mat.CreateSurfaceOutput()``          → UsdPreviewSurface (GL viewport)
    - ``mat.CreateSurfaceOutput("arnold")``  → Arnold Standard Surface
    - ``mat.CreateSurfaceOutput("karma")``   → Karma/MaterialX
    - ``mat.CreateSurfaceOutput("ri")``      → (reserved for RenderMan)
    - ``mat.CreateSurfaceOutput("redshift")``→ Redshift StandardMaterial

Color management: All texture maps are expected in scene-linear / ACEScg.
The ``sourceColorSpace`` is set to ``"raw"`` to bypass any display transform.
"""


def gen_usd_preview_surface_code(
    mat_path_var,
    tex_file_var,
    roughness_var,
    mat_mode_var,
    emissive_mult_var,
    st_varname="st",
):
    """Generate UsdPreviewSurface shader code (GL viewport fallback).

    Args:
        mat_path_var:      Variable name holding ``Sdf.Path`` of the material prim.
        tex_file_var:      Variable name holding the texture file path string.
        roughness_var:     Variable name holding roughness float.
        mat_mode_var:      Variable name holding material mode string ("pbr"/"emissive"/"pbr_emissive").
        emissive_mult_var: Variable name holding emissive multiplier float.
        st_varname:        Primvar name for texture coordinates.

    Returns:
        List of Python code strings.
    """
    lines = [
        f'# --- UsdPreviewSurface (GL Viewport Fallback) ---',
        f'_ups_shader = UsdShade.Shader.Define(stage, {mat_path_var}.AppendChild("PreviewSurface"))',
        f'_ups_shader.CreateIdAttr("UsdPreviewSurface")',
        f'_ups_out = _ups_shader.CreateOutput("surface", Sdf.ValueTypeNames.Token)',
        f'if {mat_mode_var} == "emissive":',
        f'    _ups_shader.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(0.0, 0.0, 0.0))',
        f'    _ups_shader.CreateInput("roughness", Sdf.ValueTypeNames.Float).Set(1.0)',
        f'    _ups_shader.CreateInput("ior", Sdf.ValueTypeNames.Float).Set(1.0)',
        f'    _ups_shader.CreateInput("metallic", Sdf.ValueTypeNames.Float).Set(0.0)',
        f'    _ups_shader.CreateInput("specularColor", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(0.0, 0.0, 0.0))',
        f'elif {mat_mode_var} == "pbr_emissive":',
        f'    _ups_spec = 0.0 if float({roughness_var}) >= 0.99 else max(0.0, min(1.0, 1.0 - float({roughness_var})))',
        f'    _ups_shader.CreateInput("roughness", Sdf.ValueTypeNames.Float).Set(float({roughness_var}))',
        f'    _ups_shader.CreateInput("ior", Sdf.ValueTypeNames.Float).Set(1.0 if float({roughness_var}) >= 0.99 else 1.5)',
        f'    _ups_shader.CreateInput("metallic", Sdf.ValueTypeNames.Float).Set(0.0)',
        f'    _ups_shader.CreateInput("specularColor", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(_ups_spec, _ups_spec, _ups_spec))',
        f'else:',
        f'    _ups_spec = 0.0 if float({roughness_var}) >= 0.99 else max(0.0, min(1.0, 1.0 - float({roughness_var})))',
        f'    _ups_shader.CreateInput("emissiveColor", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(0.0, 0.0, 0.0))',
        f'    _ups_shader.CreateInput("roughness", Sdf.ValueTypeNames.Float).Set(float({roughness_var}))',
        f'    _ups_shader.CreateInput("ior", Sdf.ValueTypeNames.Float).Set(1.0 if float({roughness_var}) >= 0.99 else 1.5)',
        f'    _ups_shader.CreateInput("metallic", Sdf.ValueTypeNames.Float).Set(0.0)',
        f'    _ups_shader.CreateInput("specularColor", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(_ups_spec, _ups_spec, _ups_spec))',
        f'_ups_shader.CreateInput("clearcoat", Sdf.ValueTypeNames.Float).Set(0.0)',
        f'_ups_shader.CreateInput("clearcoatRoughness", Sdf.ValueTypeNames.Float).Set(1.0)',
        f'',
        f'if {tex_file_var} and os.path.isfile({tex_file_var}):',
        f'    _ups_tex = UsdShade.Shader.Define(stage, {mat_path_var}.AppendChild("PreviewTexture"))',
        f'    _ups_tex.CreateIdAttr("UsdUVTexture")',
        f'    _ups_tex.CreateInput("file", Sdf.ValueTypeNames.Asset).Set(Sdf.AssetPath({tex_file_var}))',
        f'    _ups_tex.CreateInput("sourceColorSpace", Sdf.ValueTypeNames.Token).Set("raw")',
        f'    _ups_tex.CreateInput("wrapS", Sdf.ValueTypeNames.Token).Set("repeat")',
        f'    _ups_tex.CreateInput("wrapT", Sdf.ValueTypeNames.Token).Set("clamp")',
        f'    _ups_pvr = UsdShade.Shader.Define(stage, {mat_path_var}.AppendChild("PreviewPrimvarReader"))',
        f'    _ups_pvr.CreateIdAttr("UsdPrimvarReader_float2")',
        f'    _ups_pvr.CreateInput("varname", Sdf.ValueTypeNames.Token).Set("{st_varname}")',
        f'    _ups_pvr.CreateOutput("result", Sdf.ValueTypeNames.Float2)',
        f'    _ups_tex.CreateInput("st", Sdf.ValueTypeNames.Float2).ConnectToSource(_ups_pvr.ConnectableAPI(), "result")',
        f'    _ups_tex.CreateOutput("rgb", Sdf.ValueTypeNames.Color3f)',
        f'    if {mat_mode_var} == "emissive":',
        f'        _ups_tex.CreateInput("scale", Sdf.ValueTypeNames.Color4f).Set(Gf.Vec4f({emissive_mult_var}, {emissive_mult_var}, {emissive_mult_var}, 1.0))',
        f'        _ups_shader.CreateInput("emissiveColor", Sdf.ValueTypeNames.Color3f).ConnectToSource(_ups_tex.ConnectableAPI(), "rgb")',
        f'    elif {mat_mode_var} == "pbr_emissive":',
        f'        _ups_shader.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).ConnectToSource(_ups_tex.ConnectableAPI(), "rgb")',
        f'        _ups_tex.CreateInput("scale", Sdf.ValueTypeNames.Color4f).Set(Gf.Vec4f({emissive_mult_var}, {emissive_mult_var}, {emissive_mult_var}, 1.0))',
        f'        _ups_shader.CreateInput("emissiveColor", Sdf.ValueTypeNames.Color3f).ConnectToSource(_ups_tex.ConnectableAPI(), "rgb")',
        f'    else:',
        f'        _ups_shader.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).ConnectToSource(_ups_tex.ConnectableAPI(), "rgb")',
        f'else:',
        f'    if {mat_mode_var} == "emissive":',
        f'        _ups_shader.CreateInput("emissiveColor", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f({emissive_mult_var}, {emissive_mult_var}, {emissive_mult_var}))',
        f'    elif {mat_mode_var} == "pbr_emissive":',
        f'        _ups_shader.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(0.5, 0.5, 0.5))',
        f'        _ups_shader.CreateInput("emissiveColor", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f({emissive_mult_var}, {emissive_mult_var}, {emissive_mult_var}))',
        f'    else:',
        f'        _ups_shader.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(0.5, 0.5, 0.5))',
        f'',
    ]
    return lines


def gen_arnold_standard_surface_code(
    mat_path_var,
    tex_file_var,
    roughness_var,
    mat_mode_var,
    emissive_mult_var,
    st_varname="st",
):
    """Generate Arnold Standard Surface shader code.

    Creates an ``arnold:standard_surface`` shader with optional
    ``arnold:image`` texture node connected to base_color or emission_color.

    Args:
        mat_path_var:      Variable name holding ``Sdf.Path`` of the material prim.
        tex_file_var:      Variable name holding the texture file path string.
        roughness_var:     Variable name holding roughness float.
        mat_mode_var:      Variable name holding material mode string.
        emissive_mult_var: Variable name holding emissive multiplier float.
        st_varname:        Primvar name for texture coordinates.

    Returns:
        List of Python code strings.
    """
    lines = [
        f'# --- Arnold Standard Surface ---',
        f'_ai_shader = UsdShade.Shader.Define(stage, {mat_path_var}.AppendChild("ArnoldStandardSurface"))',
        f'_ai_shader.CreateIdAttr("arnold:standard_surface")',
        f'_ai_out = _ai_shader.CreateOutput("shader", Sdf.ValueTypeNames.Token)',
        f'',
        f'if {mat_mode_var} == "emissive":',
        f'    _ai_shader.CreateInput("base", Sdf.ValueTypeNames.Float).Set(0.0)',
        f'    _ai_shader.CreateInput("base_color", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(0.0, 0.0, 0.0))',
        f'    _ai_shader.CreateInput("specular", Sdf.ValueTypeNames.Float).Set(0.0)',
        f'    _ai_shader.CreateInput("specular_roughness", Sdf.ValueTypeNames.Float).Set(1.0)',
        f'    _ai_shader.CreateInput("emission", Sdf.ValueTypeNames.Float).Set(float({emissive_mult_var}))',
        f'elif {mat_mode_var} == "pbr_emissive":',
        f'    _ai_shader.CreateInput("base", Sdf.ValueTypeNames.Float).Set(1.0)',
        f'    _ai_shader.CreateInput("specular", Sdf.ValueTypeNames.Float).Set(1.0 - float({roughness_var}))',
        f'    _ai_shader.CreateInput("specular_roughness", Sdf.ValueTypeNames.Float).Set(float({roughness_var}))',
        f'    _ai_shader.CreateInput("specular_IOR", Sdf.ValueTypeNames.Float).Set(1.5)',
        f'    _ai_shader.CreateInput("emission", Sdf.ValueTypeNames.Float).Set(float({emissive_mult_var}))',
        f'else:',
        f'    _ai_shader.CreateInput("base", Sdf.ValueTypeNames.Float).Set(1.0)',
        f'    _ai_shader.CreateInput("specular", Sdf.ValueTypeNames.Float).Set(1.0 - float({roughness_var}))',
        f'    _ai_shader.CreateInput("specular_roughness", Sdf.ValueTypeNames.Float).Set(float({roughness_var}))',
        f'    _ai_shader.CreateInput("specular_IOR", Sdf.ValueTypeNames.Float).Set(1.5)',
        f'    _ai_shader.CreateInput("emission", Sdf.ValueTypeNames.Float).Set(0.0)',
        f'',
        f'if {tex_file_var} and os.path.isfile({tex_file_var}):',
        f'    _ai_tex = UsdShade.Shader.Define(stage, {mat_path_var}.AppendChild("ArnoldImage"))',
        f'    _ai_tex.CreateIdAttr("arnold:image")',
        f'    _ai_tex.CreateInput("filename", Sdf.ValueTypeNames.Asset).Set(Sdf.AssetPath({tex_file_var}))',
        f'    _ai_tex.CreateInput("color_space", Sdf.ValueTypeNames.String).Set("raw")',
        f'    _ai_tex.CreateInput("sflip", Sdf.ValueTypeNames.Bool).Set(False)',
        f'    _ai_tex.CreateInput("tflip", Sdf.ValueTypeNames.Bool).Set(False)',
        f'    _ai_tex.CreateInput("uvset", Sdf.ValueTypeNames.String).Set("{st_varname}")',
        f'    _ai_tex.CreateOutput("rgba", Sdf.ValueTypeNames.Color4f)',
        f'    if {mat_mode_var} == "emissive":',
        f'        _ai_shader.CreateInput("emission_color", Sdf.ValueTypeNames.Color3f).ConnectToSource(_ai_tex.ConnectableAPI(), "rgb")',
        f'    elif {mat_mode_var} == "pbr_emissive":',
        f'        _ai_shader.CreateInput("base_color", Sdf.ValueTypeNames.Color3f).ConnectToSource(_ai_tex.ConnectableAPI(), "rgb")',
        f'        _ai_shader.CreateInput("emission_color", Sdf.ValueTypeNames.Color3f).ConnectToSource(_ai_tex.ConnectableAPI(), "rgb")',
        f'    else:',
        f'        _ai_shader.CreateInput("base_color", Sdf.ValueTypeNames.Color3f).ConnectToSource(_ai_tex.ConnectableAPI(), "rgb")',
        f'else:',
        f'    if {mat_mode_var} == "emissive":',
        f'        _ai_shader.CreateInput("emission_color", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(1.0, 1.0, 1.0))',
        f'    elif {mat_mode_var} == "pbr_emissive":',
        f'        _ai_shader.CreateInput("base_color", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(0.5, 0.5, 0.5))',
        f'        _ai_shader.CreateInput("emission_color", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(1.0, 1.0, 1.0))',
        f'    else:',
        f'        _ai_shader.CreateInput("base_color", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(0.5, 0.5, 0.5))',
        f'',
    ]
    return lines


def gen_karma_materialx_code(
    mat_path_var,
    tex_file_var,
    roughness_var,
    mat_mode_var,
    emissive_mult_var,
    st_varname="st",
    force_specular_roughness_one=False,
):
    """Generate Karma MaterialX-based shader code.

    Creates a ``ND_standard_surface_surfaceshader`` MaterialX shader with
    optional ``ND_image_color3`` texture node for Karma CPU/XPU rendering.

    Args:
        mat_path_var:      Variable name holding ``Sdf.Path`` of the material prim.
        tex_file_var:      Variable name holding the texture file path string.
        roughness_var:     Variable name holding roughness float.
        mat_mode_var:      Variable name holding material mode string.
        emissive_mult_var: Variable name holding emissive multiplier float.
        st_varname:        Primvar name for texture coordinates.
        force_specular_roughness_one: Force specular roughness to 1.0 (e.g. for all-renderers mode).

    Returns:
        List of Python code strings.
    """
    spec_rough_expr = "1.0" if force_specular_roughness_one else f"float({roughness_var})"
    spec_val_expr = "0.0" if force_specular_roughness_one else f"(0.0 if float({roughness_var}) >= 0.95 else max(0.0, min(1.0, 1.0 - float({roughness_var}))))"
    ior_expr = "1.0" if force_specular_roughness_one else f"(1.0 if float({roughness_var}) >= 0.95 else 1.5)"
    lines = [
        f'# --- Karma MaterialX (ND_standard_surface_surfaceshader) ---',
        f'_km_shader = UsdShade.Shader.Define(stage, {mat_path_var}.AppendChild("KarmaMtlX"))',
        f'_km_shader.CreateIdAttr("ND_standard_surface_surfaceshader")',
        f'_km_out = _km_shader.CreateOutput("out", Sdf.ValueTypeNames.Token)',
        f'',
        f'if {mat_mode_var} == "emissive":',
        f'    _km_shader.CreateInput("base", Sdf.ValueTypeNames.Float).Set(0.0)',
        f'    _km_shader.CreateInput("base_color", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(0.0, 0.0, 0.0))',
        f'    _km_shader.CreateInput("specular", Sdf.ValueTypeNames.Float).Set(0.0)',
        f'    _km_shader.CreateInput("specular_roughness", Sdf.ValueTypeNames.Float).Set(1.0)',
        f'    _km_shader.CreateInput("emission", Sdf.ValueTypeNames.Float).Set(float({emissive_mult_var}))',
        f'elif {mat_mode_var} == "pbr_emissive":',
        f'    _km_shader.CreateInput("base", Sdf.ValueTypeNames.Float).Set(1.0)',
        f'    _km_shader.CreateInput("specular", Sdf.ValueTypeNames.Float).Set({spec_val_expr})',
        f'    _km_shader.CreateInput("specular_roughness", Sdf.ValueTypeNames.Float).Set({spec_rough_expr})',
        f'    _km_shader.CreateInput("specular_IOR", Sdf.ValueTypeNames.Float).Set({ior_expr})',
        f'    _km_shader.CreateInput("emission", Sdf.ValueTypeNames.Float).Set(float({emissive_mult_var}))',
        f'else:',
        f'    _km_shader.CreateInput("base", Sdf.ValueTypeNames.Float).Set(1.0)',
        f'    _km_shader.CreateInput("specular", Sdf.ValueTypeNames.Float).Set({spec_val_expr})',
        f'    _km_shader.CreateInput("specular_roughness", Sdf.ValueTypeNames.Float).Set({spec_rough_expr})',
        f'    _km_shader.CreateInput("specular_IOR", Sdf.ValueTypeNames.Float).Set({ior_expr})',
        f'    _km_shader.CreateInput("emission", Sdf.ValueTypeNames.Float).Set(0.0)',
        f'',
        f'if {tex_file_var} and os.path.isfile({tex_file_var}):',
        f'    _km_tex = UsdShade.Shader.Define(stage, {mat_path_var}.AppendChild("KarmaImage"))',
        f'    _km_tex.CreateIdAttr("ND_image_color3")',
        f'    _km_tex.CreateInput("file", Sdf.ValueTypeNames.Asset).Set(Sdf.AssetPath({tex_file_var}))',
        f'    _km_tex.CreateOutput("out", Sdf.ValueTypeNames.Color3f)',
        f'    # MaterialX texcoord reader',
        f'    _km_uv = UsdShade.Shader.Define(stage, {mat_path_var}.AppendChild("KarmaTexcoord"))',
        f'    _km_uv.CreateIdAttr("ND_texcoord_vector2")',
        f'    _km_uv.CreateInput("index", Sdf.ValueTypeNames.Int).Set(0)',
        f'    _km_uv.CreateOutput("out", Sdf.ValueTypeNames.Float2)',
        f'    _km_tex.CreateInput("texcoord", Sdf.ValueTypeNames.Float2).ConnectToSource(_km_uv.ConnectableAPI(), "out")',
        f'    if {mat_mode_var} == "emissive":',
        f'        _km_shader.CreateInput("emission_color", Sdf.ValueTypeNames.Color3f).ConnectToSource(_km_tex.ConnectableAPI(), "out")',
        f'    elif {mat_mode_var} == "pbr_emissive":',
        f'        _km_shader.CreateInput("base_color", Sdf.ValueTypeNames.Color3f).ConnectToSource(_km_tex.ConnectableAPI(), "out")',
        f'        _km_shader.CreateInput("emission_color", Sdf.ValueTypeNames.Color3f).ConnectToSource(_km_tex.ConnectableAPI(), "out")',
        f'    else:',
        f'        _km_shader.CreateInput("base_color", Sdf.ValueTypeNames.Color3f).ConnectToSource(_km_tex.ConnectableAPI(), "out")',
        f'else:',
        f'    if {mat_mode_var} == "emissive":',
        f'        _km_shader.CreateInput("emission_color", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(1.0, 1.0, 1.0))',
        f'    elif {mat_mode_var} == "pbr_emissive":',
        f'        _km_shader.CreateInput("base_color", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(0.5, 0.5, 0.5))',
        f'        _km_shader.CreateInput("emission_color", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(1.0, 1.0, 1.0))',
        f'    else:',
        f'        _km_shader.CreateInput("base_color", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(0.5, 0.5, 0.5))',
        f'',
    ]
    return lines


def gen_redshift_standard_material_code(
    mat_path_var,
    tex_file_var,
    roughness_var,
    mat_mode_var,
    emissive_mult_var,
    st_varname="st",
):
    """Generate Redshift StandardMaterial shader code.

    Creates a ``redshift::StandardMaterial`` shader with optional
    ``redshift::TextureSampler`` for Redshift GPU rendering.

    Args:
        mat_path_var:      Variable name holding ``Sdf.Path`` of the material prim.
        tex_file_var:      Variable name holding the texture file path string.
        roughness_var:     Variable name holding roughness float.
        mat_mode_var:      Variable name holding material mode string.
        emissive_mult_var: Variable name holding emissive multiplier float.
        st_varname:        Primvar name for texture coordinates.

    Returns:
        List of Python code strings.
    """
    lines = [
        f'# --- Redshift StandardMaterial ---',
        f'_rs_shader = UsdShade.Shader.Define(stage, {mat_path_var}.AppendChild("RedshiftStandardMaterial"))',
        f'_rs_shader.CreateIdAttr("redshift::StandardMaterial")',
        f'_rs_out = _rs_shader.CreateOutput("shader", Sdf.ValueTypeNames.Token)',
        f'',
        f'if {mat_mode_var} == "emissive":',
        f'    _rs_shader.CreateInput("base_color", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(0.0, 0.0, 0.0))',
        f'    _rs_shader.CreateInput("refl_weight", Sdf.ValueTypeNames.Float).Set(0.0)',
        f'    _rs_shader.CreateInput("refl_roughness", Sdf.ValueTypeNames.Float).Set(1.0)',
        f'    _rs_shader.CreateInput("refl_ior", Sdf.ValueTypeNames.Float).Set(1.0)',
        f'    _rs_shader.CreateInput("emission_weight", Sdf.ValueTypeNames.Float).Set(float({emissive_mult_var}))',
        f'elif {mat_mode_var} == "pbr_emissive":',
        f'    _rs_spec = 0.0 if float({roughness_var}) >= 0.99 else max(0.0, min(1.0, 1.0 - float({roughness_var})))',
        f'    _rs_shader.CreateInput("refl_weight", Sdf.ValueTypeNames.Float).Set(_rs_spec)',
        f'    _rs_shader.CreateInput("refl_roughness", Sdf.ValueTypeNames.Float).Set(float({roughness_var}))',
        f'    _rs_shader.CreateInput("refl_ior", Sdf.ValueTypeNames.Float).Set(1.0 if float({roughness_var}) >= 0.99 else 1.5)',
        f'    _rs_shader.CreateInput("emission_weight", Sdf.ValueTypeNames.Float).Set(float({emissive_mult_var}))',
        f'else:',
        f'    _rs_spec = 0.0 if float({roughness_var}) >= 0.99 else max(0.0, min(1.0, 1.0 - float({roughness_var})))',
        f'    _rs_shader.CreateInput("refl_weight", Sdf.ValueTypeNames.Float).Set(_rs_spec)',
        f'    _rs_shader.CreateInput("refl_roughness", Sdf.ValueTypeNames.Float).Set(float({roughness_var}))',
        f'    _rs_shader.CreateInput("refl_ior", Sdf.ValueTypeNames.Float).Set(1.0 if float({roughness_var}) >= 0.99 else 1.5)',
        f'    _rs_shader.CreateInput("emission_weight", Sdf.ValueTypeNames.Float).Set(0.0)',
        f'',
        f'if {tex_file_var} and os.path.isfile({tex_file_var}):',
        f'    _rs_tex = UsdShade.Shader.Define(stage, {mat_path_var}.AppendChild("RedshiftTextureSampler"))',
        f'    _rs_tex.CreateIdAttr("redshift::TextureSampler")',
        f'    _rs_tex.CreateInput("tex0", Sdf.ValueTypeNames.Asset).Set(Sdf.AssetPath({tex_file_var}))',
        f'    _rs_tex.CreateInput("tspace_id", Sdf.ValueTypeNames.String).Set("{st_varname}")',
        f'    _rs_tex.CreateInput("color_space", Sdf.ValueTypeNames.String).Set("Raw")',
        f'    _rs_tex.CreateOutput("outColor", Sdf.ValueTypeNames.Color3f)',
        f'    if {mat_mode_var} == "emissive":',
        f'        _rs_shader.CreateInput("emission_color", Sdf.ValueTypeNames.Color3f).ConnectToSource(_rs_tex.ConnectableAPI(), "outColor")',
        f'    elif {mat_mode_var} == "pbr_emissive":',
        f'        _rs_shader.CreateInput("base_color", Sdf.ValueTypeNames.Color3f).ConnectToSource(_rs_tex.ConnectableAPI(), "outColor")',
        f'        _rs_shader.CreateInput("emission_color", Sdf.ValueTypeNames.Color3f).ConnectToSource(_rs_tex.ConnectableAPI(), "outColor")',
        f'    else:',
        f'        _rs_shader.CreateInput("base_color", Sdf.ValueTypeNames.Color3f).ConnectToSource(_rs_tex.ConnectableAPI(), "outColor")',
        f'else:',
        f'    if {mat_mode_var} == "emissive":',
        f'        _rs_shader.CreateInput("emission_color", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(1.0, 1.0, 1.0))',
        f'    elif {mat_mode_var} == "pbr_emissive":',
        f'        _rs_shader.CreateInput("base_color", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(0.5, 0.5, 0.5))',
        f'        _rs_shader.CreateInput("emission_color", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(1.0, 1.0, 1.0))',
        f'    else:',
        f'        _rs_shader.CreateInput("base_color", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(0.5, 0.5, 0.5))',
        f'',
    ]
    return lines


def gen_material_output_connections(mat_path_var):
    """Generate surface output connection code for all render contexts.

    Wires each renderer's shader output to the correct material surface output:
        - Default context  → UsdPreviewSurface (_ups_out)
        - arnold context   → Arnold Standard Surface (_ai_out)
        - karma context    → Karma MaterialX (_km_out)
        - redshift context → Redshift StandardMaterial (_rs_out)

    Args:
        mat_path_var: Variable name holding the material ``Sdf.Path``.

    Returns:
        List of Python code strings.
    """
    lines = [
        f'# --- Wire material surface outputs for each render context ---',
        f'_mat_prim = UsdShade.Material(stage.GetPrimAtPath({mat_path_var}))',
        f'_mat_prim.CreateSurfaceOutput().ConnectToSource(_ups_out)',
        f'_mat_prim.CreateSurfaceOutput("arnold").ConnectToSource(_ups_out)',
        f'_mat_prim.CreateSurfaceOutput("karma").ConnectToSource(_km_out)',
        f'_mat_prim.CreateSurfaceOutput("mtlx").ConnectToSource(_km_out)',
        f'_mat_prim.CreateSurfaceOutput("redshift").ConnectToSource(_ups_out)',
        f'',
    ]
    return lines


def gen_full_material_code(
    mat_path_var,
    tex_file_var,
    roughness_var,
    mat_mode_var,
    emissive_mult_var,
    st_varname="st",
):
    """Generate a complete multi-renderer material (all renderers + UsdPreviewSurface).

    This is the main entry point used by ``_gen_ground_projection_code()`` and
    ``build_usd_room_architecture()`` to create a fully-wired material with
    native shaders for Arnold, Karma, and Redshift alongside a UsdPreviewSurface
    fallback for the Houdini GL viewport.

    Args:
        mat_path_var:      Variable name holding ``Sdf.Path`` of the material prim.
        tex_file_var:      Variable name holding the texture file path string.
        roughness_var:     Variable name holding roughness float.
        mat_mode_var:      Variable name holding material mode string.
        emissive_mult_var: Variable name holding emissive multiplier float.
        st_varname:        Primvar name for texture coordinates.

    Returns:
        List of Python code strings.
    """
    lines = []
    lines.append(f'_mat = UsdShade.Material.Define(stage, {mat_path_var})')
    lines.append('')

    # UsdPreviewSurface (viewport)
    lines.extend(gen_usd_preview_surface_code(
        mat_path_var, tex_file_var, roughness_var,
        mat_mode_var, emissive_mult_var, st_varname,
    ))

    # Arnold
    lines.extend(gen_arnold_standard_surface_code(
        mat_path_var, tex_file_var, roughness_var,
        mat_mode_var, emissive_mult_var, st_varname,
    ))

    # Karma MaterialX (Specular roughness set to 1.0 when targeting all renderers)
    lines.extend(gen_karma_materialx_code(
        mat_path_var, tex_file_var, roughness_var,
        mat_mode_var, emissive_mult_var, st_varname,
        force_specular_roughness_one=True,
    ))

    # Redshift
    lines.extend(gen_redshift_standard_material_code(
        mat_path_var, tex_file_var, roughness_var,
        mat_mode_var, emissive_mult_var, st_varname,
    ))

    # Wire surface outputs
    lines.extend(gen_material_output_connections(mat_path_var))

    return lines


def gen_create_material_function():
    """Generate a reusable ``create_native_material()`` Python function definition.

    This embeds all renderer material creation logic as a single callable function
    inside the generated PythonScript LOP code. Other parts of the generated code
    can call ``create_native_material(stage, mat_path, tex_path, ...)`` to create
    fully-wired multi-renderer materials (Arnold, Karma MaterialX, Redshift,
    and UsdPreviewSurface for GL viewport fallback).

    Supports filtering by ``renderer_target`` ("all", "arnold", "karma", "redshift", "preview").

    Returns:
        List of Python code strings defining the function.
    """
    lines = [
        'def create_native_material(stage, mat_path, tex_file_path, roughness_val=0.85, mat_mode_val="pbr", emissive_mult_val=1.0, st_varname="st", renderer_target="all", opacity_primvar=None):',
        '    """Create delegated native materials (Arnold, Karma, Redshift, or UsdPreviewSurface)."""',
        '    _mat = UsdShade.Material.Define(stage, mat_path)',
        '    target = str(renderer_target).lower() if renderer_target else "all"',
        '    is_all_renderers = ("all" in target) or (target == "all")',
        '    if is_all_renderers:',
        '        target = "all"',
        '        if roughness_val is None:',
        '            roughness_val = 0.85',
        '    build_arnold = target in ("all", "arnold")',
        '    build_karma = target in ("all", "karma")',
        '    build_redshift = target in ("all", "redshift")',
        '    build_preview = target in ("all", "preview")',
        '',
        '    # --- UsdPreviewSurface (Only built when preview or all is requested) ---',
        '    if build_preview:',
        '        _ups_shader = UsdShade.Shader.Define(stage, mat_path.AppendChild("PreviewSurface"))',
        '        _ups_shader.CreateIdAttr("UsdPreviewSurface")',
        '        _ups_out = _ups_shader.CreateOutput("surface", Sdf.ValueTypeNames.Token)',
        '        if mat_mode_val == "emissive":',
        '            _ups_shader.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(0.0, 0.0, 0.0))',
        '            _ups_shader.CreateInput("roughness", Sdf.ValueTypeNames.Float).Set(1.0)',
        '            _ups_shader.CreateInput("ior", Sdf.ValueTypeNames.Float).Set(1.0)',
        '            _ups_shader.CreateInput("metallic", Sdf.ValueTypeNames.Float).Set(0.0)',
        '            _ups_shader.CreateInput("specularColor", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(0.0, 0.0, 0.0))',
        '        elif mat_mode_val == "pbr_emissive":',
        '            _ups_spec = 0.0 if roughness_val >= 0.95 else max(0.0, min(1.0, 1.0 - roughness_val))',
        '            _ups_shader.CreateInput("roughness", Sdf.ValueTypeNames.Float).Set(roughness_val)',
        '            _ups_shader.CreateInput("ior", Sdf.ValueTypeNames.Float).Set(1.0 if roughness_val >= 0.95 else 1.5)',
        '            _ups_shader.CreateInput("metallic", Sdf.ValueTypeNames.Float).Set(0.0)',
        '            _ups_shader.CreateInput("specularColor", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(_ups_spec, _ups_spec, _ups_spec))',
        '        else:',
        '            _ups_spec = 0.0 if roughness_val >= 0.95 else max(0.0, min(1.0, 1.0 - roughness_val))',
        '            _ups_shader.CreateInput("emissiveColor", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(0.0, 0.0, 0.0))',
        '            _ups_shader.CreateInput("roughness", Sdf.ValueTypeNames.Float).Set(roughness_val)',
        '            _ups_shader.CreateInput("ior", Sdf.ValueTypeNames.Float).Set(1.0 if roughness_val >= 0.95 else 1.5)',
        '            _ups_shader.CreateInput("metallic", Sdf.ValueTypeNames.Float).Set(0.0)',
        '            _ups_shader.CreateInput("specularColor", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(_ups_spec, _ups_spec, _ups_spec))',
        '',
        '    # --- Arnold Standard Surface ---',
        '    if build_arnold:',
        '        _ai_shader = UsdShade.Shader.Define(stage, mat_path.AppendChild("ArnoldStandardSurface"))',
        '        _ai_shader.CreateIdAttr("arnold:standard_surface")',
        '        _ai_out = _ai_shader.CreateOutput("shader", Sdf.ValueTypeNames.Token)',
        '        if mat_mode_val == "emissive":',
        '            _ai_shader.CreateInput("base", Sdf.ValueTypeNames.Float).Set(0.0)',
        '            _ai_shader.CreateInput("base_color", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(0.0, 0.0, 0.0))',
        '            _ai_shader.CreateInput("specular", Sdf.ValueTypeNames.Float).Set(0.0)',
        '            _ai_shader.CreateInput("emission", Sdf.ValueTypeNames.Float).Set(emissive_mult_val)',
        '        elif mat_mode_val == "pbr_emissive":',
        '            _ai_spec = 0.0 if roughness_val >= 0.95 else max(0.0, min(1.0, 1.0 - roughness_val))',
        '            _ai_shader.CreateInput("base", Sdf.ValueTypeNames.Float).Set(1.0)',
        '            _ai_shader.CreateInput("specular", Sdf.ValueTypeNames.Float).Set(_ai_spec)',
        '            _ai_shader.CreateInput("specular_roughness", Sdf.ValueTypeNames.Float).Set(roughness_val)',
        '            _ai_shader.CreateInput("emission", Sdf.ValueTypeNames.Float).Set(emissive_mult_val)',
        '        else:',
        '            _ai_spec = 0.0 if roughness_val >= 0.95 else max(0.0, min(1.0, 1.0 - roughness_val))',
        '            _ai_shader.CreateInput("base", Sdf.ValueTypeNames.Float).Set(1.0)',
        '            _ai_shader.CreateInput("specular", Sdf.ValueTypeNames.Float).Set(_ai_spec)',
        '            _ai_shader.CreateInput("specular_roughness", Sdf.ValueTypeNames.Float).Set(roughness_val)',
        '            _ai_shader.CreateInput("emission", Sdf.ValueTypeNames.Float).Set(0.0)',
        '',
        '    # --- Karma MaterialX ---',
        '    if build_karma:',
        '        _km_shader = UsdShade.Shader.Define(stage, mat_path.AppendChild("KarmaMtlX"))',
        '        _km_shader.CreateIdAttr("ND_standard_surface_surfaceshader")',
        '        _km_out = _km_shader.CreateOutput("out", Sdf.ValueTypeNames.Token)',
        '        _km_spec_roughness = float(roughness_val)',
        '        if mat_mode_val == "emissive":',
        '            _km_shader.CreateInput("base", Sdf.ValueTypeNames.Float).Set(0.0)',
        '            _km_shader.CreateInput("specular", Sdf.ValueTypeNames.Float).Set(0.0)',
        '            _km_shader.CreateInput("specular_roughness", Sdf.ValueTypeNames.Float).Set(1.0)',
        '            _km_shader.CreateInput("emission", Sdf.ValueTypeNames.Float).Set(emissive_mult_val)',
        '        elif mat_mode_val == "pbr_emissive":',
        '            _km_spec = 0.0 if _km_spec_roughness >= 0.95 else max(0.0, min(1.0, 1.0 - _km_spec_roughness))',
        '            _km_shader.CreateInput("base", Sdf.ValueTypeNames.Float).Set(1.0)',
        '            _km_shader.CreateInput("specular", Sdf.ValueTypeNames.Float).Set(_km_spec)',
        '            _km_shader.CreateInput("specular_roughness", Sdf.ValueTypeNames.Float).Set(_km_spec_roughness)',
        '            _km_shader.CreateInput("specular_IOR", Sdf.ValueTypeNames.Float).Set(1.5)',
        '            _km_shader.CreateInput("emission", Sdf.ValueTypeNames.Float).Set(emissive_mult_val)',
        '        else:',
        '            _km_spec = 0.0 if _km_spec_roughness >= 0.95 else max(0.0, min(1.0, 1.0 - _km_spec_roughness))',
        '            _km_shader.CreateInput("base", Sdf.ValueTypeNames.Float).Set(1.0)',
        '            _km_shader.CreateInput("specular", Sdf.ValueTypeNames.Float).Set(_km_spec)',
        '            _km_shader.CreateInput("specular_roughness", Sdf.ValueTypeNames.Float).Set(_km_spec_roughness)',
        '            _km_shader.CreateInput("specular_IOR", Sdf.ValueTypeNames.Float).Set(1.5)',
        '            _km_shader.CreateInput("emission", Sdf.ValueTypeNames.Float).Set(0.0)',
        '',
        '    # --- Redshift StandardMaterial ---',
        '    if build_redshift:',
        '        _rs_shader = UsdShade.Shader.Define(stage, mat_path.AppendChild("RedshiftStandardMaterial"))',
        '        _rs_shader.CreateIdAttr("redshift::StandardMaterial")',
        '        _rs_out = _rs_shader.CreateOutput("outColor", Sdf.ValueTypeNames.Color3f)',
        '        if mat_mode_val == "emissive":',
        '            _rs_shader.CreateInput("base_color", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(0.0, 0.0, 0.0))',
        '            _rs_shader.CreateInput("base_color_weight", Sdf.ValueTypeNames.Float).Set(0.0)',
        '            _rs_shader.CreateInput("refl_weight", Sdf.ValueTypeNames.Float).Set(0.0)',
        '            _rs_shader.CreateInput("refl_roughness", Sdf.ValueTypeNames.Float).Set(1.0)',
        '            _rs_shader.CreateInput("refl_ior", Sdf.ValueTypeNames.Float).Set(1.0)',
        '            _rs_shader.CreateInput("emission_weight", Sdf.ValueTypeNames.Float).Set(emissive_mult_val)',
        '        elif mat_mode_val == "pbr_emissive":',
        '            _rs_spec = 0.0 if roughness_val >= 0.95 else max(0.0, min(1.0, 1.0 - roughness_val))',
        '            _rs_shader.CreateInput("base_color_weight", Sdf.ValueTypeNames.Float).Set(1.0)',
        '            _rs_shader.CreateInput("refl_weight", Sdf.ValueTypeNames.Float).Set(_rs_spec)',
        '            _rs_shader.CreateInput("refl_roughness", Sdf.ValueTypeNames.Float).Set(roughness_val)',
        '            _rs_shader.CreateInput("refl_ior", Sdf.ValueTypeNames.Float).Set(1.0 if roughness_val >= 0.95 else 1.5)',
        '            _rs_shader.CreateInput("emission_weight", Sdf.ValueTypeNames.Float).Set(emissive_mult_val)',
        '        else:',
        '            _rs_spec = 0.0 if roughness_val >= 0.95 else max(0.0, min(1.0, 1.0 - roughness_val))',
        '            _rs_shader.CreateInput("base_color_weight", Sdf.ValueTypeNames.Float).Set(1.0)',
        '            _rs_shader.CreateInput("refl_weight", Sdf.ValueTypeNames.Float).Set(_rs_spec)',
        '            _rs_shader.CreateInput("refl_roughness", Sdf.ValueTypeNames.Float).Set(roughness_val)',
        '            _rs_shader.CreateInput("refl_ior", Sdf.ValueTypeNames.Float).Set(1.0 if roughness_val >= 0.95 else 1.5)',
        '            _rs_shader.CreateInput("emission_weight", Sdf.ValueTypeNames.Float).Set(0.0)',
        '',
        '    # Texture connections',
        '    if tex_file_path and os.path.isfile(tex_file_path):',
        '        if build_preview:',
        '            _ups_tex = UsdShade.Shader.Define(stage, mat_path.AppendChild("PreviewTexture"))',
        '            _ups_tex.CreateIdAttr("UsdUVTexture")',
        '            _ups_tex.CreateInput("file", Sdf.ValueTypeNames.Asset).Set(Sdf.AssetPath(tex_file_path))',
        '            _ups_tex.CreateInput("sourceColorSpace", Sdf.ValueTypeNames.Token).Set("raw")',
        '            _ups_tex.CreateInput("wrapS", Sdf.ValueTypeNames.Token).Set("repeat")',
        '            _ups_tex.CreateInput("wrapT", Sdf.ValueTypeNames.Token).Set("clamp")',
        '            _ups_pvr = UsdShade.Shader.Define(stage, mat_path.AppendChild("PreviewPrimvarReader"))',
        '            _ups_pvr.CreateIdAttr("UsdPrimvarReader_float2")',
        '            _ups_pvr.CreateInput("varname", Sdf.ValueTypeNames.Token).Set(st_varname)',
        '            _ups_pvr.CreateOutput("result", Sdf.ValueTypeNames.Float2)',
        '            _ups_tex.CreateInput("st", Sdf.ValueTypeNames.Float2).ConnectToSource(_ups_pvr.ConnectableAPI(), "result")',
        '            _ups_tex.CreateOutput("rgb", Sdf.ValueTypeNames.Color3f)',
        '        if build_arnold:',
        '            _ai_tex = UsdShade.Shader.Define(stage, mat_path.AppendChild("ArnoldImage"))',
        '            _ai_tex.CreateIdAttr("arnold:image")',
        '            _ai_tex.CreateInput("filename", Sdf.ValueTypeNames.Asset).Set(Sdf.AssetPath(tex_file_path))',
        '            _ai_tex.CreateInput("color_space", Sdf.ValueTypeNames.String).Set("raw")',
        '            _ai_tex.CreateInput("uvset", Sdf.ValueTypeNames.String).Set(st_varname)',
        '            _ai_tex.CreateInput("tflip", Sdf.ValueTypeNames.Bool).Set(False)',
        '            _ai_tex.CreateOutput("rgba", Sdf.ValueTypeNames.Color4f)',
        '        if build_karma:',
        '            _km_tex = UsdShade.Shader.Define(stage, mat_path.AppendChild("KarmaImage"))',
        '            _km_tex.CreateIdAttr("ND_image_color3")',
        '            _km_tex.CreateInput("file", Sdf.ValueTypeNames.Asset).Set(Sdf.AssetPath(tex_file_path))',
        '            _km_tex.CreateOutput("out", Sdf.ValueTypeNames.Color3f)',
        '            _km_uv = UsdShade.Shader.Define(stage, mat_path.AppendChild("KarmaTexcoord"))',
        '            _km_uv.CreateIdAttr("ND_texcoord_vector2")',
        '            _km_uv.CreateInput("index", Sdf.ValueTypeNames.Int).Set(0)',
        '            _km_uv.CreateOutput("out", Sdf.ValueTypeNames.Float2)',
        '            _km_tex.CreateInput("texcoord", Sdf.ValueTypeNames.Float2).ConnectToSource(_km_uv.ConnectableAPI(), "out")',
        '        if build_redshift:',
        '            _rs_tex = UsdShade.Shader.Define(stage, mat_path.AppendChild("RedshiftTextureSampler"))',
        '            _rs_tex.CreateIdAttr("redshift::TextureSampler")',
        '            _rs_tex.CreateInput("tex0", Sdf.ValueTypeNames.Asset).Set(Sdf.AssetPath(tex_file_path))',
        '            _rs_tex.CreateInput("tspace_id", Sdf.ValueTypeNames.String).Set(st_varname)',
        '            _rs_tex.CreateInput("tex0_colorSpace", Sdf.ValueTypeNames.String).Set("Raw")',
        '            _rs_tex.CreateOutput("outColor", Sdf.ValueTypeNames.Color3f)',
        '        # Wire RGB from texture to diffuse and/or emission according to mode',
        '        if mat_mode_val == "emissive":',
        '            if build_preview:',
        '                if abs(emissive_mult_val - 1.0) > 1e-4:',
        '                    _ups_tex.CreateInput("scale", Sdf.ValueTypeNames.Color4f).Set(Gf.Vec4f(emissive_mult_val, emissive_mult_val, emissive_mult_val, 1.0))',
        '                _ups_shader.CreateInput("emissiveColor", Sdf.ValueTypeNames.Color3f).ConnectToSource(_ups_tex.ConnectableAPI(), "rgb")',
        '            if build_arnold:',
        '                _ai_shader.CreateInput("emission_color", Sdf.ValueTypeNames.Color3f).ConnectToSource(_ai_tex.ConnectableAPI(), "rgba")',
        '            if build_karma:',
        '                _km_shader.CreateInput("emission_color", Sdf.ValueTypeNames.Color3f).ConnectToSource(_km_tex.ConnectableAPI(), "out")',
        '            if build_redshift:',
        '                _rs_shader.CreateInput("emission_color", Sdf.ValueTypeNames.Color3f).ConnectToSource(_rs_tex.ConnectableAPI(), "outColor")',
        '        elif mat_mode_val == "pbr_emissive":',
        '            if build_preview:',
        '                _ups_shader.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).ConnectToSource(_ups_tex.ConnectableAPI(), "rgb")',
        '                _ups_shader.CreateInput("emissiveColor", Sdf.ValueTypeNames.Color3f).ConnectToSource(_ups_tex.ConnectableAPI(), "rgb")',
        '            if build_arnold:',
        '                _ai_shader.CreateInput("base_color", Sdf.ValueTypeNames.Color3f).ConnectToSource(_ai_tex.ConnectableAPI(), "rgba")',
        '                _ai_shader.CreateInput("emission_color", Sdf.ValueTypeNames.Color3f).ConnectToSource(_ai_tex.ConnectableAPI(), "rgba")',
        '            if build_karma:',
        '                _km_shader.CreateInput("base_color", Sdf.ValueTypeNames.Color3f).ConnectToSource(_km_tex.ConnectableAPI(), "out")',
        '                _km_shader.CreateInput("emission_color", Sdf.ValueTypeNames.Color3f).ConnectToSource(_km_tex.ConnectableAPI(), "out")',
        '            if build_redshift:',
        '                _rs_shader.CreateInput("base_color", Sdf.ValueTypeNames.Color3f).ConnectToSource(_rs_tex.ConnectableAPI(), "outColor")',
        '                _rs_shader.CreateInput("emission_color", Sdf.ValueTypeNames.Color3f).ConnectToSource(_rs_tex.ConnectableAPI(), "outColor")',
        '        else:',
        '            if build_preview:',
        '                _ups_shader.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).ConnectToSource(_ups_tex.ConnectableAPI(), "rgb")',
        '            if build_arnold:',
        '                _ai_shader.CreateInput("base_color", Sdf.ValueTypeNames.Color3f).ConnectToSource(_ai_tex.ConnectableAPI(), "rgba")',
        '            if build_karma:',
        '                _km_shader.CreateInput("base_color", Sdf.ValueTypeNames.Color3f).ConnectToSource(_km_tex.ConnectableAPI(), "out")',
        '            if build_redshift:',
        '                _rs_shader.CreateInput("base_color", Sdf.ValueTypeNames.Color3f).ConnectToSource(_rs_tex.ConnectableAPI(), "outColor")',
        '    else:',
        '        if mat_mode_val == "emissive":',
        '            if build_preview:',
        '                _ups_shader.CreateInput("emissiveColor", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(emissive_mult_val, emissive_mult_val, emissive_mult_val))',
        '            if build_arnold:',
        '                _ai_shader.CreateInput("emission_color", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(1.0, 1.0, 1.0))',
        '            if build_karma:',
        '                _km_shader.CreateInput("emission_color", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(1.0, 1.0, 1.0))',
        '            if build_redshift:',
        '                _rs_shader.CreateInput("emission_color", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(1.0, 1.0, 1.0))',
        '        elif mat_mode_val == "pbr_emissive":',
        '            if build_preview:',
        '                _ups_shader.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(0.5, 0.5, 0.5))',
        '                _ups_shader.CreateInput("emissiveColor", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(emissive_mult_val, emissive_mult_val, emissive_mult_val))',
        '            if build_arnold:',
        '                _ai_shader.CreateInput("base_color", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(0.5, 0.5, 0.5))',
        '                _ai_shader.CreateInput("emission_color", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(1.0, 1.0, 1.0))',
        '            if build_karma:',
        '                _km_shader.CreateInput("base_color", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(0.5, 0.5, 0.5))',
        '                _km_shader.CreateInput("emission_color", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(1.0, 1.0, 1.0))',
        '            if build_redshift:',
        '                _rs_shader.CreateInput("base_color", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(0.5, 0.5, 0.5))',
        '                _rs_shader.CreateInput("emission_color", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(1.0, 1.0, 1.0))',
        '        else:',
        '            if build_preview:',
        '                _ups_shader.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(0.5, 0.5, 0.5))',
        '            if build_arnold:',
        '                _ai_shader.CreateInput("base_color", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(0.5, 0.5, 0.5))',
        '            if build_karma:',
        '                _km_shader.CreateInput("base_color", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(0.5, 0.5, 0.5))',
        '            if build_redshift:',
        '                _rs_shader.CreateInput("base_color", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(0.5, 0.5, 0.5))',
        '',
        '    # --- Alpha feathering: drive opacity from the displayOpacity primvar ---',
        '    if opacity_primvar:',
        '        if build_preview:',
        '            _op_pvr = UsdShade.Shader.Define(stage, mat_path.AppendChild("PreviewOpacityReader"))',
        '            _op_pvr.CreateIdAttr("UsdPrimvarReader_float")',
        '            _op_pvr.CreateInput("varname", Sdf.ValueTypeNames.Token).Set(opacity_primvar)',
        '            _op_pvr.CreateOutput("result", Sdf.ValueTypeNames.Float)',
        '            _ups_shader.CreateInput("opacity", Sdf.ValueTypeNames.Float).ConnectToSource(_op_pvr.ConnectableAPI(), "result")',
        '        if build_arnold:',
        '            _ai_op = UsdShade.Shader.Define(stage, mat_path.AppendChild("ArnoldOpacityReader"))',
        '            _ai_op.CreateIdAttr("arnold:user_data_rgb")',
        '            _ai_op.CreateInput("attribute", Sdf.ValueTypeNames.String).Set("opacity")',
        '            _ai_op.CreateOutput("out", Sdf.ValueTypeNames.Color3f)',
        '            _ai_shader.CreateInput("opacity", Sdf.ValueTypeNames.Color3f).ConnectToSource(_ai_op.ConnectableAPI(), "out")',
        '        if build_karma:',
        '            _km_op = UsdShade.Shader.Define(stage, mat_path.AppendChild("KarmaOpacityReader"))',
        '            _km_op.CreateIdAttr("ND_geompropvalue_float")',
        '            _km_op.CreateInput("geomprop", Sdf.ValueTypeNames.String).Set(opacity_primvar)',
        '            _km_op.CreateOutput("out", Sdf.ValueTypeNames.Float)',
        '            _km_conv = UsdShade.Shader.Define(stage, mat_path.AppendChild("KarmaOpacityConverter"))',
        '            _km_conv.CreateIdAttr("ND_convert_float_color3")',
        '            _km_conv.CreateInput("in", Sdf.ValueTypeNames.Float).ConnectToSource(_km_op.ConnectableAPI(), "out")',
        '            _km_conv.CreateOutput("out", Sdf.ValueTypeNames.Color3f)',
        '            _km_shader.CreateInput("opacity", Sdf.ValueTypeNames.Color3f).ConnectToSource(_km_conv.ConnectableAPI(), "out")',
        '        if build_redshift:',
        '            _rs_op = UsdShade.Shader.Define(stage, mat_path.AppendChild("RedshiftOpacityReader"))',
        '            _rs_op.CreateIdAttr("redshift::RSUserDataColor")',
        '            _rs_op.CreateInput("attribute", Sdf.ValueTypeNames.String).Set(opacity_primvar)',
        '            _rs_op.CreateOutput("outColor", Sdf.ValueTypeNames.Color3f)',
        '            _rs_shader.CreateInput("opacity_color", Sdf.ValueTypeNames.Color3f).ConnectToSource(_rs_op.ConnectableAPI(), "outColor")',
        '',
        '    # Wire surface outputs strictly for selected delegate',
        '    if target == "all":',
        '        _mat.CreateSurfaceOutput().ConnectToSource(_km_out)',
        '        _mat.CreateSurfaceOutput("mtlx").ConnectToSource(_km_out)',
        '        _mat.CreateSurfaceOutput("karma").ConnectToSource(_km_out)',
        '        _mat.CreateSurfaceOutput("arnold").ConnectToSource(_km_out)',
        '        _mat.CreateSurfaceOutput("redshift").ConnectToSource(_km_out)',
        '        _mat.CreateSurfaceOutput("Redshift").ConnectToSource(_km_out)',
        '        if build_preview:',
        '            _mat.CreateSurfaceOutput("preview").ConnectToSource(_ups_out)',
        '    else:',
        '        if build_preview:',
        '            _mat.CreateSurfaceOutput().ConnectToSource(_ups_out)',
        '        if build_arnold:',
        '            _mat.CreateSurfaceOutput("arnold").ConnectToSource(_ai_out)',
        '            if not build_preview:',
        '                _mat.CreateSurfaceOutput().ConnectToSource(_ai_out)',
        '        if build_karma:',
        '            _mat.CreateSurfaceOutput("karma").ConnectToSource(_km_out)',
        '            _mat.CreateSurfaceOutput("mtlx").ConnectToSource(_km_out)',
        '            if not build_preview and not build_arnold:',
        '                _mat.CreateSurfaceOutput().ConnectToSource(_km_out)',
        '        if build_redshift:',
        '            _mat.CreateSurfaceOutput("redshift").ConnectToSource(_rs_out)',
        '            _mat.CreateSurfaceOutput("Redshift").ConnectToSource(_rs_out)',
        '            if not build_preview and not build_arnold and not build_karma:',
        '                _mat.CreateSurfaceOutput().ConnectToSource(_rs_out)',
        '    return _mat',
        '',
    ]
    return lines


def create_or_update_material_library(stage_node, p, mat_lib_node_name=None):
    """Create or update a native Houdini Solaris Material Library LOP node in /stage.

    Instantiates interactive VOP shader networks inside the Material Library node and assigns
    them to the room / ground geometry meshes. Enforces delegate exclusivity (only Redshift,
    Arnold, Karma, or USD Preview shaders are built based on renderer_target) and wires
    textures strictly to diffuse and/or emission according to the chosen material mode (emissive,
    pbr_emissive, or pbr). For the ground disc, wires displayOpacity into the native opacity
    input to preserve feather softness.

    Args:
        stage_node: The parent /stage network in Houdini (hou.Node).
        p: Dictionary of parameters collected from the panel or settings.
        mat_lib_node_name: Optional custom node name (defaults to p['mat_lib_node_name'] or 'hdri_match_materials').

    Returns:
        The created or updated materiallibrary hou.Node.
    """
    if not stage_node:
        return None

    try:
        import hou
    except ImportError:
        return None

    if mat_lib_node_name is None:
        mat_lib_node_name = p.get("mat_lib_node_name", "hdri_match_materials") if isinstance(p, dict) else "hdri_match_materials"

    mat_lib = stage_node.node(mat_lib_node_name)
    is_new = False
    if mat_lib is None:
        mat_lib = stage_node.createNode("materiallibrary", mat_lib_node_name)
        mat_lib.setColor(hou.Color((0.2, 0.6, 0.85)))
        is_new = True

    mat_lib.bypass(False)
    mat_lib.parm("matpathprefix").set("/materials/")

    # Determine active renderer target
    raw_target = str(p.get("renderer_target", "preview")).strip().lower()
    has_arnold_vop = False
    try:
        has_arnold_vop = bool(mat_lib.childTypeCategory().nodeType("arnold_materialbuilder"))
    except Exception:
        has_arnold_vop = False

    is_all_renderers = ("all" in raw_target) or (raw_target == "all")

    if any(k in raw_target for k in ("karma", "karmacpu", "karmagpu", "mtlx")):
        target = "karma"
    elif "arnold" in raw_target:
        # In Solaris, Arnold natively compiles and renders MaterialX standard_surface shaders.
        # Use arnold_materialbuilder only if HtoA VOP plugins are installed; otherwise MaterialX.
        target = "arnold" if has_arnold_vop else "karma"
    elif "redshift" in raw_target:
        target = "redshift"
    elif "preview" in raw_target or "usd" in raw_target:
        target = "preview"
    elif is_all_renderers:
        target = "all"
    else:
        target = "karma"

    raw_mode = p.get("mat_mode")
    if raw_mode is None:
        raw_mode = p.get("ground_mat_mode")
    if raw_mode is None:
        raw_mode = p.get("arch_mat_mode")

    if isinstance(raw_mode, int):
        # 0: PBR (Lit Diffuse), 1: Emissive, 2: PBR + Emissive Fill
        if raw_mode == 1:
            mat_mode = "emissive"
        elif raw_mode == 2:
            mat_mode = "pbr_emissive"
        else:
            mat_mode = "pbr"
    else:
        raw_str = str(raw_mode or "pbr").strip().lower()
        if "pbr" in raw_str and "emissive" in raw_str:
            mat_mode = "pbr_emissive"
        elif "emissive" in raw_str:
            mat_mode = "emissive"
        else:
            mat_mode = "pbr"

    raw_em = p.get("emissive_mult") if p.get("emissive_mult") is not None else (p.get("ground_emissive_mult") if p.get("ground_emissive_mult") is not None else p.get("arch_emissive_mult"))
    try:
        emissive_mult = float(raw_em) if raw_em is not None else 1.0
    except (ValueError, TypeError):
        emissive_mult = 1.0

    roughness = 1.0 if is_all_renderers else float(p.get("ground_roughness", 1.0))
    spec_val = 0.0 if (roughness >= 0.95 or is_all_renderers) else max(0.0, min(1.0, 1.0 - roughness))
    ior_val = 1.0 if (roughness >= 0.95 or is_all_renderers) else 1.5
    feather = float(p.get("ground_feather", 0.15))

    proj_mode = p.get("proj_mode", "room_box")
    planar_textures = p.get("planar_textures", {})
    hdri_tex = p.get("hdri_texture", "")

    # Determine target scene prefix: splatforge (/stage/room) vs domebreaker (/environment/ground_dome)
    mat_lib_name = str(p.get("mat_lib_node_name", mat_lib.name() if hasattr(mat_lib, "name") else "hdri_materials")).lower()
    is_splatforge = "splat" in mat_lib_name or "splat" in str(proj_mode).lower()

    # Query editable stage if available to confirm exact primitive paths
    stg = None
    try:
        stg = mat_lib.stage()
    except Exception:
        pass

    def _resolve_geopath(candidates):
        if stg:
            for cand in candidates:
                # Remove wildcard for validity check
                check_path = cand[:-2] if cand.endswith("/*") else cand
                prim = stg.GetPrimAtPath(check_path)
                if prim and prim.IsValid():
                    return cand
        # Fallback to prefix-based candidate
        return candidates[0] if is_splatforge else (candidates[1] if len(candidates) > 1 else candidates[0])

    # Define surface map specs with context-accurate geometry paths
    if proj_mode == "room_box":
        surfaces = [
            ("floor", "floor_mat", planar_textures.get("floor", hdri_tex),
             _resolve_geopath(["/stage/room/floor", "/environment/ground_dome/floor", "/world/room/floor"])),
            ("ceiling", "ceiling_mat", planar_textures.get("ceiling", hdri_tex),
             _resolve_geopath(["/stage/room/ceiling", "/environment/ground_dome/ceiling", "/world/room/ceiling"])),
            ("wall_north", "wall_north_mat", planar_textures.get("wall_north", hdri_tex),
             _resolve_geopath(["/stage/room/walls/wall_north", "/environment/ground_dome/walls/wall_north", "/environment/ground_dome/wall_north"])),
            ("wall_south", "wall_south_mat", planar_textures.get("wall_south", hdri_tex),
             _resolve_geopath(["/stage/room/walls/wall_south", "/environment/ground_dome/walls/wall_south", "/environment/ground_dome/wall_south"])),
            ("wall_east", "wall_east_mat", planar_textures.get("wall_east", hdri_tex),
             _resolve_geopath(["/stage/room/walls/wall_east", "/environment/ground_dome/walls/wall_east", "/environment/ground_dome/wall_east"])),
            ("wall_west", "wall_west_mat", planar_textures.get("wall_west", hdri_tex),
             _resolve_geopath(["/stage/room/walls/wall_west", "/environment/ground_dome/walls/wall_west", "/environment/ground_dome/wall_west"])),
            ("props", "props_mat", planar_textures.get("props", hdri_tex),
             _resolve_geopath(["/stage/room/props/*", "/environment/ground_dome/props/*", "/world/room/props/*"])),
        ]
    else:
        # Ground disc mode
        ground_tex = planar_textures.get("ground", hdri_tex)
        surfaces = [
            ("ground", "ground_mat", ground_tex,
             _resolve_geopath(["/environment/ground_dome/ground_plane", "/environment/ground_dome/ground_mesh", "/environment/ground_dome/ground_disc"])),
        ]

    active_mat_names = {s[1] for s in surfaces}
    expected_type_map = {
        "redshift": "redshift::StandardMaterial",
        "arnold": "arnold_materialbuilder",
        "karma": "mtlxstandard_surface",
        "all": "mtlxstandard_surface",
        "preview": "usdpreviewsurface",
    }
    target_mat_type = expected_type_map.get(target, "mtlxstandard_surface")

    # Clean up existing nodes inside mat_lib that belong to a different renderer type or are obsolete
    for child in list(mat_lib.children()):
        c_name = child.name()
        c_type = child.type().name()
        if c_name in active_mat_names and c_type != target_mat_type:
            child.destroy()
        elif not any(c_name.startswith(f"{s[0]}_") or c_name == s[1] for s in surfaces):
            child.destroy()

    # Setup assignment multiparm on mat_lib
    mat_lib.parm("materials").set(len(surfaces))

    for idx, (surf_key, mat_name, tex_path, geopath) in enumerate(surfaces, start=1):
        # Configure assignment multiparm entry
        if mat_lib.parm(f"matflag{idx}"):
            mat_lib.parm(f"matflag{idx}").set(1)
        mat_lib.parm(f"matnode{idx}").set(mat_name)
        mat_lib.parm(f"matpath{idx}").set(f"/materials/{mat_name}")
        mat_lib.parm(f"assign{idx}").set(1)
        mat_lib.parm(f"geopath{idx}").set(geopath)

        norm_tex_path = str(tex_path).replace("\\", "/") if tex_path else ""
        has_feather = (surf_key == "ground" and feather > 1e-4)

        # -------------------------------------------------------------
        # 1. REDSHIFT DELEGATED MATERIAL
        # -------------------------------------------------------------
        if target == "redshift":
            rs_mat = mat_lib.node(mat_name)
            if rs_mat is None or rs_mat.type().name() != "redshift::StandardMaterial":
                if rs_mat is not None:
                    rs_mat.destroy()
                rs_mat = mat_lib.createNode("redshift::StandardMaterial", mat_name)

            if surf_key == "props" and not norm_tex_path:
                rs_tex_name = f"{surf_key}_vcol"
                rs_tex = mat_lib.node(rs_tex_name)
                if rs_tex is None or rs_tex.type().name() != "redshift::RSUserDataColor":
                    if rs_tex is not None:
                        rs_tex.destroy()
                    rs_tex = mat_lib.createNode("redshift::RSUserDataColor", rs_tex_name)
                if rs_tex.parm("attribute"):
                    rs_tex.parm("attribute").set("displayColor")
            else:
                rs_tex_name = f"{surf_key}_tex"
                rs_tex = mat_lib.node(rs_tex_name)
                if rs_tex is None or rs_tex.type().name() != "redshift::TextureSampler":
                    if rs_tex is not None:
                        rs_tex.destroy()
                    rs_tex = mat_lib.createNode("redshift::TextureSampler", rs_tex_name)

                if rs_tex.parm("tex0") and norm_tex_path:
                    rs_tex.parm("tex0").set(norm_tex_path)
                if rs_tex.parm("tex0_colorSpace"):
                    rs_tex.parm("tex0_colorSpace").set("Raw")
                if rs_tex.parm("tspace_id"):
                    rs_tex.parm("tspace_id").set("st")

            # Mode wiring: Emissive, PBR+Emissive, or PBR
            if mat_mode == "emissive":
                rs_mat.setInput(32, rs_tex, 0)  # emission_color
                rs_mat.setInput(0, None, 0)     # disconnect base_color
                if rs_mat.parm("emission_weight"):
                    rs_mat.parm("emission_weight").set(emissive_mult)
                if rs_mat.parm("base_color_weight"):
                    rs_mat.parm("base_color_weight").set(0.0)
                if rs_mat.parm("refl_weight"):
                    rs_mat.parm("refl_weight").set(0.0)
                if rs_mat.parm("refl_roughness"):
                    rs_mat.parm("refl_roughness").set(1.0)
                if rs_mat.parm("refl_ior"):
                    rs_mat.parm("refl_ior").set(1.0)
            elif mat_mode == "pbr_emissive":
                rs_mat.setInput(0, rs_tex, 0)   # base_color
                rs_mat.setInput(32, rs_tex, 0)  # emission_color
                if rs_mat.parm("base_color_weight"):
                    rs_mat.parm("base_color_weight").set(1.0)
                if rs_mat.parm("emission_weight"):
                    rs_mat.parm("emission_weight").set(emissive_mult)
                if rs_mat.parm("refl_weight"):
                    rs_mat.parm("refl_weight").set(spec_val)
                if rs_mat.parm("refl_roughness"):
                    rs_mat.parm("refl_roughness").set(roughness)
                if rs_mat.parm("refl_ior"):
                    rs_mat.parm("refl_ior").set(ior_val)
            else:
                # PBR only: no emission
                rs_mat.setInput(0, rs_tex, 0)   # base_color
                rs_mat.setInput(32, None, 0)    # disconnect emission
                if rs_mat.parm("base_color_weight"):
                    rs_mat.parm("base_color_weight").set(1.0)
                if rs_mat.parm("emission_weight"):
                    rs_mat.parm("emission_weight").set(0.0)
                if rs_mat.parm("refl_weight"):
                    rs_mat.parm("refl_weight").set(spec_val)
                if rs_mat.parm("refl_roughness"):
                    rs_mat.parm("refl_roughness").set(roughness)
                if rs_mat.parm("refl_ior"):
                    rs_mat.parm("refl_ior").set(ior_val)

            # Ground feathering opacity
            rs_op_name = f"{surf_key}_op"
            if has_feather:
                rs_op = mat_lib.node(rs_op_name)
                if rs_op is None or rs_op.type().name() != "redshift::RSUserDataColor":
                    if rs_op is not None:
                        rs_op.destroy()
                    rs_op = mat_lib.createNode("redshift::RSUserDataColor", rs_op_name)
                if rs_op.parm("attribute"):
                    rs_op.parm("attribute").set("displayOpacity")
                rs_mat.setInput(33, rs_op, 0)  # opacity_color
            else:
                rs_op = mat_lib.node(rs_op_name)
                if rs_op is not None:
                    rs_op.destroy()
                rs_mat.setInput(33, None, 0)

        # -------------------------------------------------------------
        # 2. ARNOLD DELEGATED MATERIAL
        # -------------------------------------------------------------
        elif target == "arnold":
            amb = mat_lib.node(mat_name)
            if amb is None or amb.type().name() != "arnold_materialbuilder":
                if amb is not None:
                    amb.destroy()
                amb = mat_lib.createNode("arnold_materialbuilder", mat_name)

            out_mat = amb.node("OUT_material")
            ai_mat_name = f"{surf_key}_surface"
            ai_mat = amb.node(ai_mat_name)
            if ai_mat is None or ai_mat.type().name() != "arnold::standard_surface":
                if ai_mat is not None:
                    ai_mat.destroy()
                ai_mat = amb.createNode("arnold::standard_surface", ai_mat_name)

            if surf_key == "props" and not norm_tex_path:
                ai_tex_name = f"{surf_key}_vcol"
                ai_tex = amb.node(ai_tex_name)
                if ai_tex is None or ai_tex.type().name() != "arnold::user_data_rgb":
                    if ai_tex is not None:
                        ai_tex.destroy()
                    ai_tex = amb.createNode("arnold::user_data_rgb", ai_tex_name)
                if ai_tex.parm("attribute"):
                    ai_tex.parm("attribute").set("displayColor")
            else:
                ai_tex_name = f"{surf_key}_image"
                ai_tex = amb.node(ai_tex_name)
                if ai_tex is None or ai_tex.type().name() != "arnold::image":
                    if ai_tex is not None:
                        ai_tex.destroy()
                    ai_tex = amb.createNode("arnold::image", ai_tex_name)

                if ai_tex.parm("filename") and norm_tex_path:
                    ai_tex.parm("filename").set(norm_tex_path)
                if ai_tex.parm("color_space"):
                    ai_tex.parm("color_space").set("raw")
                if ai_tex.parm("uvset"):
                    ai_tex.parm("uvset").set("st")

            if out_mat:
                out_mat.setInput(0, ai_mat, 0)

            # Mode wiring: Emissive, PBR+Emissive, or PBR
            if mat_mode == "emissive":
                ai_mat.setInput(38, ai_tex, 0)  # emission_color
                ai_mat.setInput(1, None, 0)     # disconnect base_color
                if ai_mat.parm("emission"):
                    ai_mat.parm("emission").set(emissive_mult)
                if ai_mat.parm("base"):
                    ai_mat.parm("base").set(0.0)
                if ai_mat.parm("specular"):
                    ai_mat.parm("specular").set(0.0)
            elif mat_mode == "pbr_emissive":
                ai_mat.setInput(1, ai_tex, 0)   # base_color
                ai_mat.setInput(38, ai_tex, 0)  # emission_color
                if ai_mat.parm("base"):
                    ai_mat.parm("base").set(1.0)
                if ai_mat.parm("emission"):
                    ai_mat.parm("emission").set(emissive_mult)
                if ai_mat.parm("specular"):
                    ai_mat.parm("specular").set(spec_val)
                if ai_mat.parm("specular_roughness"):
                    ai_mat.parm("specular_roughness").set(roughness)
            else:
                ai_mat.setInput(1, ai_tex, 0)   # base_color
                ai_mat.setInput(37, None, 0)    # disconnect emission weight
                ai_mat.setInput(38, None, 0)    # disconnect emission_color
                if ai_mat.parm("base"):
                    ai_mat.parm("base").set(1.0)
                if ai_mat.parm("emission"):
                    ai_mat.parm("emission").set(0.0)
                if ai_mat.parm("specular"):
                    ai_mat.parm("specular").set(spec_val)
                if ai_mat.parm("specular_roughness"):
                    ai_mat.parm("specular_roughness").set(roughness)

            # Ground feathering opacity
            ai_op_name = f"{surf_key}_op"
            if has_feather:
                ai_op = amb.node(ai_op_name)
                if ai_op is None or ai_op.type().name() != "arnold::user_data_rgb":
                    if ai_op is not None:
                        ai_op.destroy()
                    ai_op = amb.createNode("arnold::user_data_rgb", ai_op_name)
                if ai_op.parm("attribute"):
                    ai_op.parm("attribute").set("opacity")
                ai_mat.setInput(39, ai_op, 0)  # opacity (color)
            else:
                ai_op = amb.node(ai_op_name)
                if ai_op is not None:
                    ai_op.destroy()
                ai_mat.setInput(39, None, 0)

            try:
                amb.layoutChildren()
            except Exception:
                pass

        # -------------------------------------------------------------
        # 3. KARMA (MATERIALX) DELEGATED MATERIAL
        # -------------------------------------------------------------
        elif target in ("karma", "all", "materialx"):
            km_mat = mat_lib.node(mat_name)
            if km_mat is None or km_mat.type().name() != "mtlxstandard_surface":
                if km_mat is not None:
                    km_mat.destroy()
                km_mat = mat_lib.createNode("mtlxstandard_surface", mat_name)

            if surf_key == "props" and not norm_tex_path:
                km_tex_name = f"{surf_key}_vcol"
                km_tex = mat_lib.node(km_tex_name)
                if km_tex is None or km_tex.type().name() != "mtlxgeomcolor":
                    if km_tex is not None:
                        km_tex.destroy()
                    km_tex = mat_lib.createNode("mtlxgeomcolor", km_tex_name)
            else:
                km_tex_name = f"{surf_key}_tex"
                km_tex = mat_lib.node(km_tex_name)
                if km_tex is None or km_tex.type().name() != "mtlximage":
                    if km_tex is not None:
                        km_tex.destroy()
                    km_tex = mat_lib.createNode("mtlximage", km_tex_name)

                if km_tex.parm("file") and norm_tex_path:
                    km_tex.parm("file").set(norm_tex_path)

                km_uv_name = f"{surf_key}_uv"
                km_uv = mat_lib.node(km_uv_name)
                if km_uv is None or km_uv.type().name() != "mtlxtexcoord":
                    if km_uv is not None:
                        km_uv.destroy()
                    km_uv = mat_lib.createNode("mtlxtexcoord", km_uv_name)
                if km_uv.parm("index"):
                    km_uv.parm("index").set(0)
                km_tex.setInput(3, km_uv, 0)  # texcoord

            # Determine specular & roughness values for MaterialX
            if is_all_renderers:
                km_roughness = 1.0
                km_spec = 0.0
                km_ior = 1.0
            else:
                km_roughness = roughness
                km_spec = spec_val
                km_ior = ior_val

            # Mode wiring: Emissive, PBR+Emissive, or PBR
            if mat_mode == "emissive":
                km_mat.setInput(37, km_tex, 0)  # emission_color
                km_mat.setInput(1, None, 0)     # disconnect base_color
                if km_mat.parm("emission"):
                    km_mat.parm("emission").set(emissive_mult)
                if km_mat.parm("base"):
                    km_mat.parm("base").set(0.0)
                if km_mat.parm("specular"):
                    km_mat.parm("specular").set(0.0)
                if km_mat.parm("specular_roughness"):
                    km_mat.parm("specular_roughness").set(1.0)
            elif mat_mode == "pbr_emissive":
                km_mat.setInput(1, km_tex, 0)   # base_color
                km_mat.setInput(37, km_tex, 0)  # emission_color
                if km_mat.parm("base"):
                    km_mat.parm("base").set(1.0)
                if km_mat.parm("emission"):
                    km_mat.parm("emission").set(emissive_mult)
                if km_mat.parm("specular"):
                    km_mat.parm("specular").set(km_spec)
                if km_mat.parm("specular_roughness"):
                    km_mat.parm("specular_roughness").set(km_roughness)
                if km_mat.parm("specular_IOR"):
                    km_mat.parm("specular_IOR").set(km_ior)
            else:
                km_mat.setInput(1, km_tex, 0)   # base_color
                km_mat.setInput(37, None, 0)    # disconnect emission
                if km_mat.parm("base"):
                    km_mat.parm("base").set(1.0)
                if km_mat.parm("emission"):
                    km_mat.parm("emission").set(0.0)
                if km_mat.parm("specular"):
                    km_mat.parm("specular").set(km_spec)
                if km_mat.parm("specular_roughness"):
                    km_mat.parm("specular_roughness").set(km_roughness)
                if km_mat.parm("specular_IOR"):
                    km_mat.parm("specular_IOR").set(km_ior)

            # Ground feathering opacity
            km_op_name = f"{surf_key}_op"
            km_conv_name = f"{surf_key}_op_conv"
            if has_feather:
                km_op = mat_lib.node(km_op_name)
                if km_op is None or km_op.type().name() != "mtlxgeompropvalue":
                    if km_op is not None:
                        km_op.destroy()
                    km_op = mat_lib.createNode("mtlxgeompropvalue", km_op_name)
                if km_op.parm("geomprop"):
                    km_op.parm("geomprop").set("displayOpacity")
                if km_op.parm("signature"):
                    km_op.parm("signature").set("float")

                km_conv = mat_lib.node(km_conv_name)
                if km_conv is None or km_conv.type().name() != "mtlxconvert":
                    if km_conv is not None:
                        km_conv.destroy()
                    km_conv = mat_lib.createNode("mtlxconvert", km_conv_name)
                km_conv.setInput(0, km_op, 0)
                km_mat.setInput(38, km_conv, 0)  # opacity (color3)
            else:
                km_conv = mat_lib.node(km_conv_name)
                if km_conv is not None:
                    km_conv.destroy()
                km_op = mat_lib.node(km_op_name)
                if km_op is not None:
                    km_op.destroy()
                km_mat.setInput(38, None, 0)

        # -------------------------------------------------------------
        # 4. USD PREVIEW SURFACE (OR FALLBACK)
        # -------------------------------------------------------------
        else:
            ups = mat_lib.node(mat_name)
            if ups is None or ups.type().name() != "usdpreviewsurface":
                if ups is not None:
                    ups.destroy()
                ups = mat_lib.createNode("usdpreviewsurface", mat_name)

            if surf_key == "props" and not norm_tex_path:
                vcol_name = f"{surf_key}_vcol"
                tex_node = mat_lib.node(vcol_name)
                if tex_node is None or tex_node.type().name() != "usdprimvarreader":
                    if tex_node is not None:
                        tex_node.destroy()
                    tex_node = mat_lib.createNode("usdprimvarreader", vcol_name)
                if tex_node.parm("varname"):
                    tex_node.parm("varname").set("displayColor")
                if tex_node.parm("signature"):
                    tex_node.parm("signature").set("float3")
                tex_rgb_idx = 0
                tex_out_token = "result"
            else:
                tex_node_name = f"{surf_key}_tex"
                tex_node = mat_lib.node(tex_node_name)
                if tex_node is None or not tex_node.type().name().startswith("usduvtexture"):
                    if tex_node is not None:
                        tex_node.destroy()
                    tex_node = mat_lib.createNode("usduvtexture", tex_node_name)

                st_node_name = f"{surf_key}_st"
                st_node = mat_lib.node(st_node_name)
                if st_node is None or st_node.type().name() != "usdprimvarreader":
                    if st_node is not None:
                        st_node.destroy()
                    st_node = mat_lib.createNode("usdprimvarreader", st_node_name)

                if st_node.parm("varname"):
                    st_node.parm("varname").set("st")
                if st_node.parm("signature"):
                    st_node.parm("signature").set("float2")

                if tex_node.parm("file") and norm_tex_path:
                    tex_node.parm("file").set(norm_tex_path)
                if tex_node.parm("wrapS"):
                    tex_node.parm("wrapS").set("clamp")
                if tex_node.parm("wrapT"):
                    tex_node.parm("wrapT").set("clamp")

                tex_node.setInput(1, st_node, 0)

                # Determine output index for 'rgb' on usduvtexture (index 4 in Houdini VOPs; index 0 is 'r')
                tex_rgb_idx = 4
                tex_out_token = "rgb"
                if hasattr(tex_node, "outputIndex"):
                    try:
                        oi = tex_node.outputIndex("rgb")
                        if oi >= 0:
                            tex_rgb_idx = oi
                    except Exception:
                        pass

            diff_idx = 0
            emis_idx = 1
            if hasattr(ups, "inputIndex"):
                try:
                    di = ups.inputIndex("diffuseColor")
                    if di >= 0:
                        diff_idx = di
                    ei = ups.inputIndex("emissiveColor")
                    if ei >= 0:
                        emis_idx = ei
                except Exception:
                    pass

            # Mode wiring: Emissive, PBR+Emissive, or PBR
            if mat_mode == "emissive":
                # Connect RGB to emissiveColor
                connected_emis = False
                if hasattr(ups, "setNamedInput"):
                    try:
                        ups.setNamedInput("emissiveColor", tex_node, tex_out_token)
                        connected_emis = True
                    except Exception:
                        pass
                if not connected_emis:
                    ups.setInput(emis_idx, tex_node, tex_rgb_idx)

                # Disconnect diffuseColor
                if hasattr(ups, "setNamedInput"):
                    try:
                        ups.setNamedInput("diffuseColor", None, "")
                    except Exception:
                        ups.setInput(diff_idx, None, 0)
                else:
                    ups.setInput(diff_idx, None, 0)

                if ups.parm("diffuseColorr"):
                    ups.parm("diffuseColorr").set(0.0)
                    ups.parm("diffuseColorg").set(0.0)
                    ups.parm("diffuseColorb").set(0.0)
                if ups.parm("roughness"):
                    ups.parm("roughness").set(1.0)
                if ups.parm("metallic"):
                    ups.parm("metallic").set(0.0)
                if ups.parm("ior"):
                    ups.parm("ior").set(1.0)
                if ups.parm("specularColorr"):
                    ups.parm("specularColorr").set(0.0)
                    ups.parm("specularColorg").set(0.0)
                    ups.parm("specularColorb").set(0.0)

            elif mat_mode == "pbr_emissive":
                # Connect RGB to diffuseColor
                connected_diff = False
                if hasattr(ups, "setNamedInput"):
                    try:
                        ups.setNamedInput("diffuseColor", tex_node, tex_out_token)
                        connected_diff = True
                    except Exception:
                        pass
                if not connected_diff:
                    ups.setInput(diff_idx, tex_node, tex_rgb_idx)

                # Connect RGB to emissiveColor
                connected_emis = False
                if hasattr(ups, "setNamedInput"):
                    try:
                        ups.setNamedInput("emissiveColor", tex_node, tex_out_token)
                        connected_emis = True
                    except Exception:
                        pass
                if not connected_emis:
                    ups.setInput(emis_idx, tex_node, tex_rgb_idx)

                if ups.parm("roughness"):
                    ups.parm("roughness").set(roughness)
                if ups.parm("metallic"):
                    ups.parm("metallic").set(0.0)
                if ups.parm("ior"):
                    ups.parm("ior").set(ior_val)
                if ups.parm("specularColorr"):
                    ups.parm("specularColorr").set(spec_val)
                    ups.parm("specularColorg").set(spec_val)
                    ups.parm("specularColorb").set(spec_val)

            else:
                # Standard PBR: Connect RGB to diffuseColor
                connected_diff = False
                if hasattr(ups, "setNamedInput"):
                    try:
                        ups.setNamedInput("diffuseColor", tex_node, tex_out_token)
                        connected_diff = True
                    except Exception:
                        pass
                if not connected_diff:
                    ups.setInput(diff_idx, tex_node, tex_rgb_idx)

                # Disconnect emissiveColor
                if hasattr(ups, "setNamedInput"):
                    try:
                        ups.setNamedInput("emissiveColor", None, "")
                    except Exception:
                        ups.setInput(emis_idx, None, 0)
                else:
                    ups.setInput(emis_idx, None, 0)

                if ups.parm("emissiveColorr"):
                    ups.parm("emissiveColorr").set(0.0)
                    ups.parm("emissiveColorg").set(0.0)
                    ups.parm("emissiveColorb").set(0.0)
                if ups.parm("roughness"):
                    ups.parm("roughness").set(roughness)
                if ups.parm("metallic"):
                    ups.parm("metallic").set(0.0)
                if ups.parm("ior"):
                    ups.parm("ior").set(ior_val)
                if ups.parm("specularColorr"):
                    ups.parm("specularColorr").set(spec_val)
                    ups.parm("specularColorg").set(spec_val)
                    ups.parm("specularColorb").set(spec_val)

            # Ground feathering opacity
            op_node_name = f"{surf_key}_op"
            opac_idx = 8
            if hasattr(ups, "inputIndex"):
                try:
                    oi = ups.inputIndex("opacity")
                    if oi >= 0:
                        opac_idx = oi
                except Exception:
                    pass

            if has_feather:
                op_node = mat_lib.node(op_node_name)
                if op_node is None or op_node.type().name() != "usdprimvarreader":
                    if op_node is not None:
                        op_node.destroy()
                    op_node = mat_lib.createNode("usdprimvarreader", op_node_name)
                if op_node.parm("varname"):
                    op_node.parm("varname").set("displayOpacity")
                if op_node.parm("signature"):
                    op_node.parm("signature").set("float")

                connected_opac = False
                if hasattr(ups, "setNamedInput"):
                    try:
                        ups.setNamedInput("opacity", op_node, "result")
                        connected_opac = True
                    except Exception:
                        pass
                if not connected_opac:
                    ups.setInput(opac_idx, op_node, 0)  # opacity
            else:
                op_node = mat_lib.node(op_node_name)
                if op_node is not None:
                    op_node.destroy()
                if hasattr(ups, "setNamedInput"):
                    try:
                        ups.setNamedInput("opacity", None, "")
                    except Exception:
                        ups.setInput(opac_idx, None, 0)
                else:
                    ups.setInput(opac_idx, None, 0)

    # Clean layout inside mat_lib
    try:
        mat_lib.layoutChildren()
    except Exception:
        pass

    return mat_lib

