# -*- coding: utf-8 -*-
"""
SPLATFORGE Light Extractor — USD Light Creation from Detected Clusters.

Wraps the existing ``lop_splat`` light spawning pipeline and extends it
with emissive geometry detection.  Converts ``LightAnalysis`` clusters
into USD-ready light definitions.
"""

import numpy as np

from hdri_match_solaris.splatforge import config


class LightDefinition:
    """Specification for a USD light to be created in Solaris."""

    def __init__(self, light_type, name, position, color=(1.0, 1.0, 1.0),
                 intensity=1.0, exposure=0.0, width=1.0, height=1.0,
                 normal=None, rotation=None, temperature=None,
                 cast_shadows=True, source_cluster_id=None):
        """
        Args:
            light_type:        ``'rect'``, ``'sphere'``, ``'dome'``, ``'distant'``.
            name:              USD prim name (e.g. ``'rect_light_01'``).
            position:          (x, y, z) world position.
            color:             (r, g, b) scene-linear colour.
            intensity:         Light intensity.
            exposure:          Exposure in EV stops.
            width:             Width (for RectLight).
            height:            Height (for RectLight).
            normal:            Emission direction (unit vector).
            rotation:          (rx, ry, rz) Euler rotation in degrees.
            temperature:       Correlated colour temperature in Kelvin.
            cast_shadows:      Whether the light casts shadows.
            source_cluster_id: ID of the source cluster for traceability.
        """
        self.light_type = light_type
        self.name = name
        self.position = tuple(position)
        self.color = tuple(color)
        self.intensity = float(intensity)
        self.exposure = float(exposure)
        self.width = float(width)
        self.height = float(height)
        self.normal = tuple(normal) if normal is not None else (0.0, 0.0, -1.0)
        self.rotation = tuple(rotation) if rotation is not None else (0.0, 0.0, 0.0)
        self.temperature = float(temperature) if temperature is not None else None
        self.cast_shadows = cast_shadows
        self.source_cluster_id = source_cluster_id

    def to_dict(self):
        d = {
            "light_type": self.light_type,
            "name": self.name,
            "position": self.position,
            "color": self.color,
            "intensity": self.intensity,
            "exposure": self.exposure,
            "width": self.width,
            "height": self.height,
            "normal": self.normal,
            "rotation": self.rotation,
            "cast_shadows": self.cast_shadows,
        }
        if self.temperature is not None:
            d["temperature"] = self.temperature
        if self.source_cluster_id is not None:
            d["source_cluster_id"] = self.source_cluster_id
        return d

    def __repr__(self):
        return (f"LightDefinition(type={self.light_type}, name={self.name}, "
                f"intensity={self.intensity:.2f})")


def extract_light_definitions(light_analysis, hdri_result=None, room_data=None):
    """Convert light analysis clusters and room window openings into USD-ready LightDefinitions.

    Window openings detected in the room architecture are converted into properly
    dimensioned and oriented aperture portal lights that match the window cutouts.
    Additional non-window clusters (ceiling fixtures, practical lamps) are also included.

    Args:
        light_analysis: ``LightAnalysis`` from ``light_analyzer``.
        hdri_result:    Optional dict from ``hdri_generator`` for dome light.
        room_data:      Optional ``RoomData`` from ``room_detector``.

    Returns:
        List of ``LightDefinition`` instances.
    """
    defs = []

    # 1. Dome light from HDRI
    if hdri_result and hdri_result.get("hdri_path"):
        defs.append(LightDefinition(
            light_type="dome",
            name="dome_gsplat_hdri",
            position=(0.0, 0.0, 0.0),
            intensity=1.0,
            exposure=-1.0,  # Reduced to avoid double-illumination with extracted lights
        ))

    # Resolve room_data
    if room_data is None and hasattr(light_analysis, "room_data"):
        room_data = light_analysis.room_data

    # 2. Window Aperture RectLights (matched to room openings)
    window_positions = []
    if room_data and getattr(room_data, "windows", None):
        for win in room_data.windows:
            wall = win.get("wall", "window")
            pos = win.get("pos", (0.0, 0.0, 0.0))
            normal = win.get("normal", (0.0, 0.0, -1.0))
            w = float(win.get("width", 1.0))
            h = float(win.get("height", 1.0))
            col = tuple(win.get("color", (1.0, 1.0, 1.0)))
            intensity = float(win.get("intensity", 1.5))
            rot = _normal_to_rotation(normal)

            window_positions.append(np.array(pos, dtype=np.float32))

            defs.append(LightDefinition(
                light_type="rect",
                name=f"window_light_{wall}",
                position=pos,
                color=col,
                intensity=intensity,
                width=w,
                height=h,
                normal=normal,
                rotation=rot,
                temperature=None,
                cast_shadows=True,
                source_cluster_id=f"win_{wall}",
            ))

    # 2b. HDRI Hotspot-Based Practical Lights
    #     Analyze the baked HDRI for bright emitter regions (windows, ceiling
    #     lamps, etc.) and project them onto room surfaces to create RectLights.
    #     This ensures that inpainted regions in the dome map are compensated
    #     by matching practical lights.
    hdri_hotspot_lights = []
    if hdri_result and room_data:
        hdri_arr = hdri_result.get("hdri_array")
        hdri_path = hdri_result.get("hdri_path")
        if hdri_arr is not None or (hdri_path and __import__("os").path.isfile(hdri_path)):
            try:
                from hdri_match_solaris.splatforge.lighting.hdri_hotspot_analyzer import (
                    analyze_hdri_hotspots,
                    project_hotspots_to_room,
                    inpaint_hotspots_from_hdri,
                )

                hotspots = analyze_hdri_hotspots(
                    hdri_array=hdri_arr,
                    hdri_path=hdri_path,
                    thresh_ev=2.5,
                    range_ev=8.0,
                    max_candidates=16,
                )

                if hotspots:
                    cam_pos = hdri_result.get("probe_pos")
                    hs_lights = project_hotspots_to_room(hotspots, room_data, camera_pos=cam_pos)

                    # Deduplicate against window lights
                    for ld in hs_lights:
                        ld_pos = np.array(ld.position, dtype=np.float32)
                        is_dup = False
                        for w_pos in window_positions:
                            if np.linalg.norm(ld_pos - w_pos) < 0.5:
                                is_dup = True
                                break
                        if not is_dup:
                            hdri_hotspot_lights.append(ld)
                            defs.append(ld)

                    # Optionally inpaint hotspots from the HDRI dome map
                    if hdri_arr is not None and hotspots:
                        import os
                        base_path = hdri_path or ""
                        if base_path:
                            inpaint_dir = os.path.dirname(base_path)
                            inpaint_name = os.path.splitext(os.path.basename(base_path))[0] + "_inpainted.exr"
                            inpaint_path = os.path.join(inpaint_dir, inpaint_name)
                        else:
                            inpaint_path = None

                        inp_result = inpaint_hotspots_from_hdri(
                            hdri_arr, hotspots, output_path=inpaint_path,
                        )
                        if inp_result.get("inpainted_path"):
                            hdri_result["inpainted_path"] = inp_result["inpainted_path"]

            except Exception as exc:
                print(f"[SPLATFORGE] HDRI hotspot analysis warning: {exc}")

    # 3. Practical / Interior RectLights from clusters
    if light_analysis and hasattr(light_analysis, "clusters"):
        for i, cluster in enumerate(light_analysis.clusters):
            cid = _cluster_attr(cluster, "cluster_id", i + 1)
            centroid = _cluster_attr(cluster, "centroid",
                                     _cluster_attr(cluster, "pos", [0, 2, 0]))
            c_pos = np.array(centroid, dtype=np.float32)

            # Skip if this cluster overlaps an already created window light
            is_window_dup = False
            for w_pos in window_positions:
                if np.linalg.norm(c_pos - w_pos) < 15.0:
                    is_window_dup = True
                    break
            if is_window_dup:
                continue

            normal = _cluster_attr(cluster, "normal", [0, 0, -1])
            width = _cluster_attr(cluster, "width", 1.0)
            height = _cluster_attr(cluster, "height", 1.0)
            color = _cluster_attr(cluster, "color", [1.0, 1.0, 1.0])
            intensity = _cluster_attr(cluster, "intensity", 1.0)
            temperature = _cluster_attr(cluster, "temperature", None)
            rotation = _normal_to_rotation(normal)

            defs.append(LightDefinition(
                light_type="rect",
                name=f"practical_light_{cid:02d}",
                position=centroid,
                color=color,
                intensity=intensity,
                width=width,
                height=height,
                normal=normal,
                rotation=rotation,
                temperature=temperature,
                source_cluster_id=cid,
            ))

    return defs


def _cluster_attr(c, attr, default=None):
    """Safely get attribute from cluster (object or dict)."""
    if hasattr(c, attr):
        val = getattr(c, attr)
        if val is not None:
            if isinstance(val, np.ndarray):
                return val.tolist()
            return val
    if isinstance(c, dict):
        val = c.get(attr, default)
        if isinstance(val, np.ndarray):
            return val.tolist()
        return val
    return default


def _normal_to_rotation(normal):
    """Convert a unit normal vector to Euler rotation (rx, ry, rz) degrees.

    Assumes the light faces the normal direction from its default orientation
    (typically facing -Z in USD).
    """
    import math
    nx, ny, nz = normal if len(normal) == 3 else (0, 0, -1)
    length = math.sqrt(nx * nx + ny * ny + nz * nz)
    if length < 1e-8:
        return (0.0, 0.0, 0.0)
    nx, ny, nz = nx / length, ny / length, nz / length

    # Rotation from -Z to the normal direction
    ry = math.degrees(math.atan2(-nx, -nz))
    rx = math.degrees(math.asin(max(-1.0, min(1.0, ny))))
    return (float(rx), float(ry), 0.0)
