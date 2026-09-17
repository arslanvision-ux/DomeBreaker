"""
HDA parameter read/write utilities for HDRI Match Solaris.

Provides functions to read Houdini node parameters into Python dicts
and push calibration state back to HDA parm interfaces.  This bridges
the gap between the ``hou.Node`` parameter tree and the
``CalibrationPipeline`` / ``ImageState`` data model from the original
``hdri_match`` package.
"""

import os
import re


def _parm_val(node, name, default=None):
    """Safely read a parameter value, returning *default* if missing."""
    p = node.parm(name)
    if p is not None:
        return p.eval()
    pt = node.parmTuple(name)
    if pt is not None:
        return pt.eval()
    return default


def read_calibration_parms(node):
    """Read all HDRI calibration parameters from an HDA node.

    Args:
        node: ``hou.Node`` with the calibration HDA interface.

    Returns:
        Dictionary with all calibration parameters.
    """
    return {
        # File paths
        "hdri_path": _parm_val(node, "hdri_path", ""),
        "plate_path": _parm_val(node, "plate_path", ""),

        # Color space
        "input_colorspace": _parm_val(node, "input_colorspace", "Linear"),
        "working_colorspace": _parm_val(node, "working_colorspace", "Linear"),

        # Calibration toggles
        "auto_calibrate": _parm_val(node, "auto_calibrate", False),
        "apply_exposure_match": _parm_val(node, "apply_exposure_match", True),
        "protect_sun": _parm_val(node, "protect_sun", True),

        # Sky mode
        "sky_mode": _parm_val(node, "sky_mode", "top_40"),

        # Core calibration values
        "ev_offset": _parm_val(node, "ev_offset", 0.0),
        "black_offset": _parm_val(node, "black_offset", 0.0),
        "temperature": _parm_val(node, "temperature", 0.0),
        "tint": _parm_val(node, "tint", 0.0),
        "hdri_yaw": _parm_val(node, "hdri_yaw", 0.0),

        # Horizon split
        "horizon_enable": _parm_val(node, "horizon_enable", False),
        "horizon_height": _parm_val(node, "horizon_height", 0.5),
        "horizon_feather": _parm_val(node, "horizon_feather", 0.1),
        "sky_ev_offset": _parm_val(node, "sky_ev_offset", 0.0),
        "sky_temperature": _parm_val(node, "sky_temperature", 0.0),
        "sky_tint": _parm_val(node, "sky_tint", 0.0),
        "sky_desat": _parm_val(node, "sky_desat", 0.0),
        "ground_ev_offset": _parm_val(node, "ground_ev_offset", 0.0),
        "ground_temperature": _parm_val(node, "ground_temperature", 0.0),
        "ground_tint": _parm_val(node, "ground_tint", 0.0),
        "ground_desat": _parm_val(node, "ground_desat", 0.0),

        # Soft-clip
        "softclip_enable": _parm_val(node, "softclip_enable", False),
        "softclip_threshold": _parm_val(node, "softclip_threshold", 5.0),
        "softclip_rolloff": _parm_val(node, "softclip_rolloff", 2.0),

        # Output
        "output_path": _parm_val(node, "output_path", ""),
    }


def write_calibration_parms(node, state_dict):
    """Push calibration state values back to HDA parameters.

    Only sets parameters that exist on the node. Silently skips missing parms.

    Args:
        node:       ``hou.Node`` with the calibration HDA interface.
        state_dict: Dictionary with calibration key/value pairs.
    """
    for key, value in state_dict.items():
        p = node.parm(key)
        if p is not None:
            try:
                p.set(value)
            except Exception:
                pass


def sync_pipeline_to_parms(node, pipeline):
    """Push the ``CalibrationPipeline.state`` into node parameters.

    Args:
        node:     ``hou.Node`` with the calibration HDA interface.
        pipeline: ``CalibrationPipeline`` instance with computed state.
    """
    st = pipeline.state
    write_calibration_parms(node, {
        "ev_offset": st.ev_offset,
        "black_offset": st.black_offset,
        "temperature": st.temperature,
        "tint": st.tint,
        "hdri_yaw": st.hdri_yaw,
    })


def sync_parms_to_pipeline(node, pipeline):
    """Push node parameter values into the ``CalibrationPipeline.state``.

    Args:
        node:     ``hou.Node`` with the calibration HDA interface.
        pipeline: ``CalibrationPipeline`` instance.
    """
    parms = read_calibration_parms(node)
    st = pipeline.state

    st.ev_offset = parms["ev_offset"]
    st.black_offset = parms["black_offset"]
    st.temperature = parms["temperature"]
    st.tint = parms["tint"]
    st.hdri_yaw = parms["hdri_yaw"]
    st.apply_exposure_match = parms["apply_exposure_match"]
    st.sky_mode = parms["sky_mode"]

    # Horizon
    st.horizon_enable = parms["horizon_enable"]
    st.horizon_height = parms["horizon_height"]
    st.horizon_feather = parms["horizon_feather"]
    st.sky_ev_offset = parms["sky_ev_offset"]
    st.sky_temperature = parms["sky_temperature"]
    st.sky_tint = parms["sky_tint"]
    st.sky_desat = parms["sky_desat"]
    st.ground_ev_offset = parms["ground_ev_offset"]
    st.ground_temperature = parms["ground_temperature"]
    st.ground_tint = parms["ground_tint"]
    st.ground_desat = parms["ground_desat"]

    # Soft-clip
    st.softclip_enable = parms["softclip_enable"]
    st.softclip_threshold = parms["softclip_threshold"]
    st.softclip_rolloff = parms["softclip_rolloff"]


def build_output_path(node, hdri_path):
    """Determine the output EXR path for the calibrated HDRI.

    If the user specified an explicit ``output_path``, use it.
    Otherwise, derive from the HDRI filename with ``_calibrated`` suffix,
    placed in the Houdini ``$HIP/hdri_match/`` directory.

    Args:
        node:      ``hou.Node`` with the calibration HDA interface.
        hdri_path: Original HDRI file path.

    Returns:
        Absolute path string for the output EXR.
    """
    import hou

    explicit = _parm_val(node, "output_path", "")
    if explicit:
        return hou.expandString(explicit)

    # Auto-derive from HDRI filename
    hip = hou.expandString("$HIP")
    out_dir = os.path.join(hip, "hdri_match")
    os.makedirs(out_dir, exist_ok=True)

    base = os.path.splitext(os.path.basename(hdri_path))[0]
    base = re.sub(r'(_calibrated(_[A-Za-z0-9_]+)?)+$', '', base, flags=re.IGNORECASE) or "hdri"
    return os.path.join(out_dir, f"{base}_calibrated.exr")
