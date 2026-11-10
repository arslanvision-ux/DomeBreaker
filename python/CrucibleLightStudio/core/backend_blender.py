# -*- coding: utf-8 -*-
"""
Crucible Light Studio - Blender Backend (Stub)
================================================
Concrete implementation of DCCBackend for Blender.
Supports Cycles renderer.

TODO: Implement all methods with bpy API calls.
"""

import logging
from CrucibleLightStudio.core.dcc_backend import DCCBackend
from CrucibleLightStudio.core.stage_manager import LightData

logger = logging.getLogger("CrucibleLightStudio.blender")

try:
    import bpy
except ImportError:
    bpy = None


class BlenderBackend(DCCBackend):
    """Blender backend supporting Cycles."""

    RENDERERS = ["cycles"]

    def get_name(self):
        return "blender"

    def get_active_renderer(self):
        if bpy:
            return bpy.context.scene.render.engine.lower()
        return "cycles"

    def discover_lights(self):
        if not bpy:
            return []
        lights = []
        for obj in bpy.data.objects:
            if obj.type == 'LIGHT':
                ld = LightData(
                    prim_path=obj.name,
                    name=obj.name,
                    light_type=obj.data.type,
                    enabled=not obj.hide_render,
                )
                ld.intensity = obj.data.energy
                ld.color = tuple(obj.data.color[:3])
                lights.append(ld)
        return lights

    def create_light(self, light_type, name=None):
        if not bpy:
            return None
        type_map = {
            "RectLight": "AREA",
            "DiskLight": "AREA",
            "SphereLight": "POINT",
            "DistantLight": "SUN",
            "DomeLight": "SUN",  # Blender uses World for dome
        }
        bl_type = type_map.get(light_type, "AREA")
        light_data = bpy.data.lights.new(name=name or light_type,
                                          type=bl_type)
        obj = bpy.data.objects.new(name=name or light_type,
                                    object_data=light_data)
        bpy.context.collection.objects.link(obj)
        return obj.name

    def delete_light(self, light_id):
        if bpy and light_id in bpy.data.objects:
            obj = bpy.data.objects[light_id]
            bpy.data.objects.remove(obj, do_unlink=True)
            return True
        return False

    def set_light_param(self, light_id, param_name, value):
        logger.info("Blender set_light_param: %s.%s = %s",
                     light_id, param_name, value)
        return True

    def get_light_param(self, light_id, param_name):
        return None

    def set_light_enabled(self, light_id, enabled):
        if bpy and light_id in bpy.data.objects:
            bpy.data.objects[light_id].hide_render = not enabled
            return True
        return False

    def apply_texture(self, light_id, texture_path):
        logger.info("Blender apply_texture: %s -> %s",
                     light_id, texture_path)
        return True

    def clear_texture(self, light_id):
        return True

    def get_light_links(self, light_id):
        return {}

    def set_light_link(self, light_id, object_id, linked):
        return True

    def add_fog(self, density=0.1, color=(0.5, 0.5, 0.5)):
        if bpy:
            bpy.context.scene.world.use_nodes = True
            logger.info("Blender add_fog: density=%s", density)
        return True

    def remove_fog(self):
        return True



    def add_lens_effects(self, bloom=False, glare=False, vignette=False):
        if bpy:
            bpy.context.scene.render.use_compositing = True
            logger.info("Blender add_lens_effects")
        return True

    def begin_undo(self, label):
        pass  # Blender handles undo differently via operators

    def end_undo(self):
        pass

    def get_main_window(self):
        return None  # Blender Qt access is limited
