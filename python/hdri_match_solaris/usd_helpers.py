"""
USD stage manipulation helpers for HDRI Match Solaris.

Provides reusable functions for creating, querying, and modifying USD
prims in the Houdini Solaris LOP context.  All functions operate on
``Usd.Stage`` objects obtained from ``node.editableStage()`` inside
Python LOP cook callbacks.
"""

import math
import numpy as np

from pxr import Usd, UsdGeom, UsdLux, UsdShade, Sdf, Gf


# ---------------------------------------------------------------------------
# Prim creation helpers
# ---------------------------------------------------------------------------

def get_or_create_prim(stage, path, prim_type_fn):
    """Return an existing typed prim or create a new one.

    Args:
        stage:        ``Usd.Stage`` to operate on.
        path:         USD prim path string (e.g. ``'/lights/dome'``).
        prim_type_fn: Callable that defines the prim,
                      e.g. ``UsdLux.DomeLight.Define``.

    Returns:
        The typed prim schema object.
    """
    prim = stage.GetPrimAtPath(path)
    if prim.IsValid():
        return prim_type_fn(stage, path)
    return prim_type_fn(stage, path)


def ensure_scope(stage, path):
    """Create a ``UsdGeom.Scope`` at *path* if it does not already exist."""
    prim = stage.GetPrimAtPath(path)
    if not prim.IsValid():
        UsdGeom.Scope.Define(stage, path)


# ---------------------------------------------------------------------------
# DomeLight helpers
# ---------------------------------------------------------------------------

def set_dome_light(stage, path, texture_path, exposure=0.0,
                   color=(1.0, 1.0, 1.0), yaw_degrees=0.0,
                   texture_format="latlong"):
    """Create or update a ``UsdLux.DomeLight``.

    Args:
        stage:          Editable USD stage.
        path:           Prim path for the DomeLight.
        texture_path:   File path to the HDRI texture (EXR).
        exposure:       Exposure value in EV stops.
        color:          RGB colour multiplier tuple.
        yaw_degrees:    Y-axis rotation in degrees.
        texture_format: ``'latlong'`` or ``'mirroredBall'``.

    Returns:
        The ``UsdLux.DomeLight`` schema.
    """
    dome = UsdLux.DomeLight.Define(stage, path)

    dome.CreateTextureFileAttr().Set(texture_path)
    dome.CreateExposureAttr().Set(float(exposure))
    dome.CreateColorAttr().Set(Gf.Vec3f(*color))
    dome.CreateTextureFormatAttr().Set(texture_format)

    # Apply Y-axis rotation (yaw) via xform ops
    xformable = UsdGeom.Xformable(dome.GetPrim())
    xformable.ClearXformOpOrder()
    if abs(yaw_degrees) > 1e-4:
        rot_op = xformable.AddRotateYOp(UsdGeom.XformOp.PrecisionFloat)
        rot_op.Set(float(yaw_degrees))

    return dome


# ---------------------------------------------------------------------------
# DistantLight helpers (sun)
# ---------------------------------------------------------------------------

def spherical_to_direction(azimuth_deg, elevation_deg):
    """Convert spherical coordinates to a unit direction vector.

    Uses VFX / Houdini convention: Y-up, azimuth measured from +Z axis
    clockwise when viewed from above, elevation measured from the XZ plane.

    Args:
        azimuth_deg:   Azimuth angle in degrees (0 = +Z, 90 = +X).
        elevation_deg: Elevation angle in degrees (0 = horizon, 90 = zenith).

    Returns:
        ``Gf.Vec3d`` unit direction vector.
    """
    az = math.radians(azimuth_deg)
    el = math.radians(elevation_deg)
    x = math.cos(el) * math.sin(az)
    y = math.sin(el)
    z = math.cos(el) * math.cos(az)
    return Gf.Vec3d(x, y, z)


def uv_to_spherical(u, v):
    """Convert normalised UV coordinates (equirectangular) to azimuth/elevation.

    Args:
        u: Horizontal position 0..1 (0 = left edge, 1 = right edge).
        v: Vertical position 0..1 (0 = top / zenith, 1 = bottom / nadir).

    Returns:
        (azimuth_deg, elevation_deg) tuple.
    """
    azimuth_deg = (u * 360.0) - 180.0
    elevation_deg = 90.0 - (v * 180.0)
    return azimuth_deg, elevation_deg


def set_distant_light(stage, path, azimuth_deg, elevation_deg,
                      intensity=1.0, color=(1.0, 1.0, 1.0),
                      angle=0.53, exposure=0.0):
    """Create or update a ``UsdLux.DistantLight`` for the sun.

    The light is positioned at a large distance along the computed direction
    vector and aimed at the origin.

    Args:
        stage:         Editable USD stage.
        path:          Prim path for the DistantLight.
        azimuth_deg:   Solar azimuth in degrees.
        elevation_deg: Solar elevation in degrees.
        intensity:     Light intensity scalar.
        color:         RGB colour tuple.
        angle:         Angular diameter in degrees (default 0.53 for the sun).
        exposure:      Exposure in EV stops.

    Returns:
        The ``UsdLux.DistantLight`` schema.
    """
    sun = UsdLux.DistantLight.Define(stage, path)

    sun.CreateIntensityAttr().Set(float(intensity))
    sun.CreateColorAttr().Set(Gf.Vec3f(*color))
    sun.CreateAngleAttr().Set(float(angle))
    sun.CreateExposureAttr().Set(float(exposure))

    # Compute direction and build a look-at transform
    direction = spherical_to_direction(azimuth_deg, elevation_deg)
    radius = 1000.0  # Place far from origin
    eye = direction * radius
    center = Gf.Vec3d(0, 0, 0)
    up = Gf.Vec3d(0, 1, 0)

    # If the sun is near-zenith, pick a different up vector to avoid gimbal lock
    if abs(elevation_deg) > 85.0:
        up = Gf.Vec3d(0, 0, 1)

    look_at = Gf.Matrix4d()
    look_at.SetLookAt(eye, center, up)
    xform_mat = look_at.GetInverse()

    xformable = UsdGeom.Xformable(sun.GetPrim())
    xformable.ClearXformOpOrder()
    xformable.AddTransformOp().Set(xform_mat)

    return sun


# ---------------------------------------------------------------------------
# SphereLight / RectLight helpers (extracted emitters)
# ---------------------------------------------------------------------------

def set_sphere_light(stage, path, direction_vec, intensity=1.0,
                     color=(1.0, 1.0, 1.0), radius=1.0, exposure=0.0,
                     distance=100.0):
    """Create or update a ``UsdLux.SphereLight`` for an extracted emitter.

    Args:
        stage:         Editable USD stage.
        path:          Prim path.
        direction_vec: (x, y, z) unit direction from origin.
        intensity:     Light intensity.
        color:         RGB colour tuple.
        radius:        Sphere radius.
        exposure:      Exposure in EV stops.
        distance:      Distance from origin to place the light.

    Returns:
        The ``UsdLux.SphereLight`` schema.
    """
    light = UsdLux.SphereLight.Define(stage, path)

    light.CreateIntensityAttr().Set(float(intensity))
    light.CreateColorAttr().Set(Gf.Vec3f(*color))
    light.CreateRadiusAttr().Set(float(radius))
    light.CreateExposureAttr().Set(float(exposure))

    # Position the light
    d = Gf.Vec3d(*direction_vec)
    d_len = d.GetLength()
    if d_len > 1e-8:
        d = d / d_len
    pos = d * distance

    xformable = UsdGeom.Xformable(light.GetPrim())
    xformable.ClearXformOpOrder()
    xformable.AddTranslateOp().Set(pos)

    return light


# ---------------------------------------------------------------------------
# Material helpers
# ---------------------------------------------------------------------------

def create_preview_surface_material(stage, mtl_path, diffuse_color=(0.5, 0.5, 0.5),
                                    metallic=0.0, roughness=0.5,
                                    specular_color=None, opacity=1.0):
    """Create a ``UsdPreviewSurface`` material.

    Args:
        stage:          Editable USD stage.
        mtl_path:       Prim path for the material (e.g. ``'/materials/chrome'``).
        diffuse_color:  RGB diffuse colour tuple.
        metallic:       Metallic factor (0..1).
        roughness:      Roughness factor (0..1).
        specular_color: Optional specular colour tuple.
        opacity:        Opacity factor (0..1).

    Returns:
        The ``UsdShade.Material`` schema.
    """
    material = UsdShade.Material.Define(stage, mtl_path)
    shader_path = mtl_path + "/shader"
    shader = UsdShade.Shader.Define(stage, shader_path)

    shader.CreateIdAttr("UsdPreviewSurface")
    shader.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).Set(
        Gf.Vec3f(*diffuse_color))
    shader.CreateInput("metallic", Sdf.ValueTypeNames.Float).Set(float(metallic))
    shader.CreateInput("roughness", Sdf.ValueTypeNames.Float).Set(float(roughness))

    if specular_color is not None:
        shader.CreateInput("specularColor", Sdf.ValueTypeNames.Color3f).Set(
            Gf.Vec3f(*specular_color))

    if abs(opacity - 1.0) > 1e-6:
        shader.CreateInput("opacity", Sdf.ValueTypeNames.Float).Set(float(opacity))

    material.CreateSurfaceOutput().ConnectToSource(
        shader.ConnectableAPI(), "surface")

    return material


def bind_material(stage, prim_path, material_path):
    """Bind a material to a prim using ``UsdShade.MaterialBindingAPI``."""
    prim = stage.GetPrimAtPath(prim_path)
    if not prim.IsValid():
        return
    mtl = UsdShade.Material(stage.GetPrimAtPath(material_path))
    if not mtl.GetPrim().IsValid():
        return
    UsdShade.MaterialBindingAPI.Apply(prim).Bind(mtl)


# ---------------------------------------------------------------------------
# Calibration metadata helpers
# ---------------------------------------------------------------------------

def write_calibration_metadata(prim, calibration_dict):
    """Write calibration parameters as custom attributes on a prim.

    This allows downstream LOP nodes to read the calibration state without
    parsing external files.

    Args:
        prim:             ``Usd.Prim`` to annotate.
        calibration_dict: Dictionary of calibration key/value pairs.
    """
    ns = "hdriMatch"
    for key, value in calibration_dict.items():
        attr_name = f"{ns}:{key}"
        if isinstance(value, float):
            attr = prim.CreateAttribute(attr_name, Sdf.ValueTypeNames.Float)
        elif isinstance(value, bool):
            attr = prim.CreateAttribute(attr_name, Sdf.ValueTypeNames.Bool)
        elif isinstance(value, int):
            attr = prim.CreateAttribute(attr_name, Sdf.ValueTypeNames.Int)
        elif isinstance(value, str):
            attr = prim.CreateAttribute(attr_name, Sdf.ValueTypeNames.String)
        else:
            continue
        attr.Set(value)


def read_calibration_metadata(prim):
    """Read calibration parameters from custom attributes on a prim.

    Returns:
        Dictionary of calibration key/value pairs (or empty dict).
    """
    ns = "hdriMatch:"
    result = {}
    for attr in prim.GetAttributes():
        name = attr.GetName()
        if name.startswith(ns):
            key = name[len(ns):]
            val = attr.Get()
            if val is not None:
                result[key] = val
    return result
