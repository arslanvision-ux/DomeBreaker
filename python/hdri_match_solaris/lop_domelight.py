"""
DomeLight Python LOP cook logic for Houdini Solaris.

Creates or updates a UsdLux.DomeLight on the USD stage using the calibrated
HDRI texture from an upstream ``hdri_match_calibrate`` node or from a
user-specified file path.

This node can operate standalone (with a direct HDRI path) or read the
calibration metadata from an upstream DomeLight prim to inherit the
calibrated texture path.
"""

import os


def cook(node):
    """Main cook function for the DomeLight Python LOP.

    Args:
        node: ``hou.Node`` — the Python LOP node being cooked.
    """
    import hou
    from pxr import UsdLux

    stage = node.editableStage()
    if not stage:
        raise hou.NodeError("No editable stage available.")

    from hdri_match_solaris.usd_helpers import (
        set_dome_light, read_calibration_metadata, ensure_scope
    )

    # ------------------------------------------------------------------
    # Read parameters
    # ------------------------------------------------------------------
    hdri_path = hou.expandString(node.parm("hdri_path").eval()) if node.parm("hdri_path") else ""
    dome_prim_path = node.parm("dome_path").eval() if node.parm("dome_path") else "/lights/hdri_dome"
    exposure = node.parm("exposure").eval() if node.parm("exposure") else 0.0
    yaw = node.parm("yaw").eval() if node.parm("yaw") else 0.0
    color_r = node.parm("colorr").eval() if node.parm("colorr") else 1.0
    color_g = node.parm("colorg").eval() if node.parm("colorg") else 1.0
    color_b = node.parm("colorb").eval() if node.parm("colorb") else 1.0
    texture_format = node.parm("texture_format").eval() if node.parm("texture_format") else "latlong"

    # ------------------------------------------------------------------
    # Try to inherit from upstream calibration node
    # ------------------------------------------------------------------
    if not hdri_path:
        # Check if there is already a DomeLight prim with calibration metadata
        existing_prim = stage.GetPrimAtPath(dome_prim_path)
        if existing_prim.IsValid():
            meta = read_calibration_metadata(existing_prim)
            if "calibrated_path" in meta:
                hdri_path = meta["calibrated_path"]
                print(f"[DomeLight] Inherited calibrated HDRI: {hdri_path}")

    if not hdri_path:
        raise hou.NodeError(
            "No HDRI path specified and no upstream calibration metadata found.\n"
            "Either set the 'hdri_path' parameter or connect an "
            "hdri_match_calibrate node upstream."
        )

    # ------------------------------------------------------------------
    # Create/update the DomeLight
    # ------------------------------------------------------------------
    ensure_scope(stage, "/lights")

    dome = set_dome_light(
        stage, dome_prim_path,
        texture_path=hdri_path.replace("\\", "/"),
        exposure=exposure,
        color=(color_r, color_g, color_b),
        yaw_degrees=yaw,
        texture_format=texture_format,
    )

    print(f"[HDRI Match Solaris] DomeLight updated at {dome_prim_path}")
    print(f"  Texture: {hdri_path}")
    print(f"  Exposure: {exposure:.2f} EV  |  Yaw: {yaw:.1f} deg")
