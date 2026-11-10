# -*- coding: utf-8 -*-
"""
Crucible Light Studio - Maya Backend (Stub)
=============================================
Concrete implementation of DCCBackend for Maya.
Supports Arnold and Redshift renderers.

TODO: Implement all methods with cmds/pymel API calls.
"""

import logging
from CrucibleLightStudio.core.dcc_backend import DCCBackend
from CrucibleLightStudio.core.stage_manager import LightData

logger = logging.getLogger("CrucibleLightStudio.maya")

try:
    import maya.cmds as cmds
    import maya.OpenMayaUI as omui
except ImportError:
    cmds = omui = None


class MayaBackend(DCCBackend):
    """Maya backend supporting Arnold and Redshift."""

    RENDERERS = ["arnold", "redshift"]

    def get_name(self):
        return "maya"

    def get_active_renderer(self):
        if not cmds:
            return "arnold"
        renderer = cmds.getAttr("defaultRenderGlobals.currentRenderer")
        if "arnold" in renderer.lower():
            return "arnold"
        elif "redshift" in renderer.lower():
            return "redshift"
        return renderer.lower()

    def discover_lights(self):
        if not cmds:
            return []
        lights = []
        for light_shape in cmds.ls(type="light", long=True):
            transform = cmds.listRelatives(light_shape, parent=True,
                                            fullPath=True)[0]
            ld = LightData(
                prim_path=transform,
                name=transform.split("|")[-1],
                light_type=cmds.nodeType(light_shape),
            )
            # Read intensity
            if cmds.attributeQuery("intensity", node=light_shape, exists=True):
                ld.intensity = cmds.getAttr(f"{light_shape}.intensity")
            # Read exposure (Arnold)
            if cmds.attributeQuery("aiExposure", node=light_shape, exists=True):
                ld.exposure = cmds.getAttr(f"{light_shape}.aiExposure")
            # Read color
            if cmds.attributeQuery("color", node=light_shape, exists=True):
                c = cmds.getAttr(f"{light_shape}.color")[0]
                ld.color = (c[0], c[1], c[2])
            lights.append(ld)
        return lights

    def create_light(self, light_type, name=None):
        if not cmds:
            return None
        type_map = {
            "RectLight": "areaLight",
            "DiskLight": "areaLight",
            "SphereLight": "pointLight",
            "DistantLight": "directionalLight",
            "DomeLight": "ambientLight",
        }
        maya_type = type_map.get(light_type, "areaLight")
        light = cmds.shadingNode(maya_type, asLight=True, name=name or light_type)
        return light

    def delete_light(self, light_id):
        if cmds and cmds.objExists(light_id):
            cmds.delete(light_id)
            return True
        return False

    def set_light_param(self, light_id, param_name, value):
        # Stub — map Crucible params to Maya/Arnold attrs
        logger.info("Maya set_light_param: %s.%s = %s",
                     light_id, param_name, value)
        return True

    def get_light_param(self, light_id, param_name):
        return None

    def set_light_enabled(self, light_id, enabled):
        if cmds and cmds.objExists(light_id):
            cmds.setAttr(f"{light_id}.visibility", enabled)
            return True
        return False

    def apply_texture(self, light_id, texture_path):
        logger.info("Maya apply_texture: %s -> %s", light_id, texture_path)
        return True

    def clear_texture(self, light_id):
        return self.apply_texture(light_id, "")

    def get_light_links(self, light_id):
        return {}

    def set_light_link(self, light_id, object_id, linked):
        if cmds:
            if linked:
                cmds.lightlink(make=True, light=light_id, object=object_id)
            else:
                cmds.lightlink(b=True, light=light_id, object=object_id)
            return True
        return False

    def add_fog(self, density=0.1, color=(0.5, 0.5, 0.5)):
        logger.info("Maya add_fog: density=%s", density)
        return True

    def remove_fog(self):
        return True



    def add_lens_effects(self, bloom=False, glare=False, vignette=False):
        logger.info("Maya add_lens_effects")
        return True

    def begin_undo(self, label):
        if cmds:
            cmds.undoInfo(openChunk=True, chunkName=label)

    def end_undo(self):
        if cmds:
            cmds.undoInfo(closeChunk=True)

    def get_main_window(self):
        if omui:
            try:
                from PySide2 import QtWidgets
                from shiboken2 import wrapInstance
            except ImportError:
                from PySide6 import QtWidgets
                from shiboken6 import wrapInstance
            ptr = omui.MQtUtil.mainWindow()
            return wrapInstance(int(ptr), QtWidgets.QWidget)
        return None
