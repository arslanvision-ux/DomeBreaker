"""
HDRI Calibration Python LOP cook logic for Houdini Solaris.

This module implements the core calibration pipeline as a Python LOP node.
It reads HDRI and Plate images, computes exposure/white-balance calibration,
processes the HDRI through the full pipeline (EV, WB, horizon, soft-clip,
yaw rotation), writes the calibrated EXR to disk, and updates the USD stage
with a DomeLight referencing the output.

Usage:
    Called from a Python LOP's ``python`` parameter or from an HDA's
    cook callback.  The ``cook(node)`` function is the entry point.
"""

import os
import numpy as np
import traceback


def cook(node):
    """Main cook function for the HDRI Calibrate Python LOP.

    Reads all parameters from *node*, runs the calibration pipeline,
    writes the calibrated HDRI to disk, and creates a DomeLight on the
    USD stage pointing to the output file.

    Args:
        node: ``hou.Node`` — the Python LOP node being cooked.
    """
    import hou

    stage = node.editableStage()
    if not stage:
        raise hou.NodeError("No editable stage available. "
                            "Ensure this node is inside a LOP network.")

    # ------------------------------------------------------------------
    # 1. Read parameters
    # ------------------------------------------------------------------
    from hdri_match_solaris.hda_utils import read_calibration_parms, build_output_path

    parms = read_calibration_parms(node)
    hdri_path = hou.expandString(str(parms.get("hdri_path") or ""))
    plate_path = hou.expandString(str(parms.get("plate_path") or ""))

    if not hdri_path or not os.path.isfile(hdri_path):
        raise hou.NodeError(f"HDRI file not found: {hdri_path}")

    # ------------------------------------------------------------------
    # 2. Import core pipeline from hdri_match
    # ------------------------------------------------------------------
    try:
        from hdri_match.core.pipeline import CalibrationPipeline
        from hdri_match.io.loader import load_exr_to_numpy
        from hdri_match.io.exporter import save_numpy_to_image
    except ImportError as e:
        raise hou.NodeError(
            f"Cannot import hdri_match core modules: {e}\n"
            "Ensure HDRI_MATCH_PLATE_ROOT is set in the Houdini package."
        )

    # ------------------------------------------------------------------
    # 3. Initialize pipeline and load images
    # ------------------------------------------------------------------
    pipeline = CalibrationPipeline()

    input_cs = parms["input_colorspace"]
    plate_cs = parms.get("plate_colorspace", "Auto")
    working_cs = parms["working_colorspace"]

    try:
        pipeline.load_inputs(hdri_path, plate_path,
                             input_space=input_cs,
                             working_space=working_cs,
                             plate_space=plate_cs)
    except Exception as e:
        raise hou.NodeError(f"Failed to load images: {e}")

    # ------------------------------------------------------------------
    # 4. Apply parameters to pipeline state
    # ------------------------------------------------------------------
    from hdri_match_solaris.hda_utils import sync_parms_to_pipeline
    sync_parms_to_pipeline(node, pipeline)

    # ------------------------------------------------------------------
    # 5. Auto-calibrate (compute EV and WB from HDRI vs Plate)
    # ------------------------------------------------------------------
    if parms["auto_calibrate"] and plate_path and os.path.isfile(plate_path):
        try:
            pipeline.compute_calibration(
                protect_sun=parms["protect_sun"]
            )
        except Exception as e:
            msg = f"Auto-calibration warning: {e}. Proceeding with manual parameter values."
            print(f"[HDRI Match Solaris] {msg}")
            if hasattr(node, "addWarning"):
                try:
                    node.addWarning(msg)
                except Exception:
                    pass

    # ------------------------------------------------------------------
    # 6. Process the HDRI through the full pipeline (bakes EV/WB/Yaw into EXR)
    # ------------------------------------------------------------------
    try:
        calibrated = pipeline.process_hdri(use_proxy=False)
    except Exception as e:
        raise hou.NodeError(f"HDRI processing failed: {e}\n{traceback.format_exc()}")

    if calibrated is None:
        raise hou.NodeError("Calibration produced no output.")

    # ------------------------------------------------------------------
    # 7. Write calibrated EXR to disk
    # ------------------------------------------------------------------
    output_path = build_output_path(node, hdri_path)
    try:
        save_numpy_to_image(calibrated, output_path)
        print(f"[HDRI Match Solaris] Calibrated HDRI written to: {output_path}")
    except Exception as e:
        raise hou.NodeError(f"Failed to write calibrated EXR: {e}")

    # ------------------------------------------------------------------
    # 8. Create/update DomeLight on the USD stage (neutral transforms; baked in EXR)
    # ------------------------------------------------------------------
    from hdri_match_solaris.usd_helpers import (
        set_dome_light, write_calibration_metadata, ensure_scope
    )

    ensure_scope(stage, "/lights")
    dome_path = "/lights/hdri_dome"

    dome = set_dome_light(
        stage, dome_path,
        texture_path=output_path.replace("\\", "/"),
        exposure=0.0,
        color=(1.0, 1.0, 1.0),
        yaw_degrees=0.0,
    )

    # Store computed calibration metadata on the DomeLight prim
    write_calibration_metadata(dome.GetPrim(), {
        "ev_offset": float(pipeline.state.ev_offset),
        "black_offset": float(pipeline.state.black_offset),
        "temperature": float(pipeline.state.temperature),
        "tint": float(pipeline.state.tint),
        "hdri_yaw": float(pipeline.state.hdri_yaw),
        "baked_in_texture": True,
        "source_hdri": hdri_path,
        "source_plate": plate_path or "",
        "calibrated_path": output_path,
    })

    print(f"[HDRI Match Solaris] DomeLight created at {dome_path}")
