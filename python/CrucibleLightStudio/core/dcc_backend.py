# -*- coding: utf-8 -*-
"""
Crucible Light Studio - DCC Abstraction Layer
===============================================
Abstract base class that all DCC backends must implement. This allows
Crucible Light Studio to run identically in Houdini, Maya, and Blender
with renderer-specific backends (Karma, Arnold, Redshift, Cycles).
"""

import abc
import logging

logger = logging.getLogger("CrucibleLightStudio.dcc")


class DCCBackend(abc.ABC):
    """
    Abstract interface for DCC-specific light operations.
    
    Each DCC (Houdini, Maya, Blender) implements this interface so the
    UI layer never needs to know which application it's running in.
    """

    # Supported renderers per DCC
    RENDERERS = []

    @abc.abstractmethod
    def get_name(self):
        """Return DCC name string, e.g. 'houdini', 'maya', 'blender'."""
        ...

    @abc.abstractmethod
    def get_active_renderer(self):
        """Return active renderer string: 'karma', 'arnold', 'redshift', 'cycles'."""
        ...

    # ------------------------------------------------------------------
    # Light CRUD
    # ------------------------------------------------------------------
    @abc.abstractmethod
    def discover_lights(self):
        """Return list of LightData for all lights in the scene."""
        ...

    @abc.abstractmethod
    def create_light(self, light_type, name=None):
        """Create a new light. Returns identifier (path/name)."""
        ...

    @abc.abstractmethod
    def delete_light(self, light_id):
        """Delete a light by its identifier."""
        ...

    @abc.abstractmethod
    def set_light_param(self, light_id, param_name, value):
        """Set a parameter on a light."""
        ...

    @abc.abstractmethod
    def get_light_param(self, light_id, param_name):
        """Get a parameter value from a light."""
        ...

    @abc.abstractmethod
    def set_light_enabled(self, light_id, enabled):
        """Enable or disable a light."""
        ...

    # ------------------------------------------------------------------
    # Textures
    # ------------------------------------------------------------------
    @abc.abstractmethod
    def apply_texture(self, light_id, texture_path):
        """Apply an image texture (HDRI/gobo) to a light."""
        ...

    @abc.abstractmethod
    def clear_texture(self, light_id):
        """Remove texture from a light."""
        ...

    # ------------------------------------------------------------------
    # Light Linking
    # ------------------------------------------------------------------
    @abc.abstractmethod
    def get_light_links(self, light_id):
        """Return dict of {object_id: linked_bool} for a light."""
        ...

    @abc.abstractmethod
    def set_light_link(self, light_id, object_id, linked):
        """Set whether a light illuminates a specific object."""
        ...

    # ------------------------------------------------------------------
    # Atmospherics
    # ------------------------------------------------------------------
    @abc.abstractmethod
    def add_fog(self, density=0.1, color=(0.5, 0.5, 0.5)):
        """Inject fog/atmosphere volume appropriate for the renderer."""
        ...

    @abc.abstractmethod
    def remove_fog(self):
        """Remove any Crucible-injected fog."""
        ...



    # ------------------------------------------------------------------
    # Lens Effects
    # ------------------------------------------------------------------
    @abc.abstractmethod
    def add_lens_effects(self, bloom=False, glare=False, vignette=False):
        """Add post-process lens effects appropriate for the renderer."""
        ...

    # ------------------------------------------------------------------
    # Undo
    # ------------------------------------------------------------------
    @abc.abstractmethod
    def begin_undo(self, label):
        """Begin an undo group."""
        ...

    @abc.abstractmethod
    def end_undo(self):
        """End an undo group."""
        ...

    # ------------------------------------------------------------------
    # Qt parent
    # ------------------------------------------------------------------
    @abc.abstractmethod
    def get_main_window(self):
        """Return the DCC's main QWidget for parenting dialogs."""
        ...


def detect_dcc():
    """
    Auto-detect which DCC we are running inside and return the
    appropriate backend class (not instantiated).
    """
    try:
        import hou  # noqa: F401
        return "houdini"
    except ImportError:
        pass

    try:
        import maya.cmds  # noqa: F401
        return "maya"
    except ImportError:
        pass

    try:
        import bpy  # noqa: F401
        return "blender"
    except ImportError:
        pass

    return "standalone"


def get_backend():
    """
    Factory: return an instantiated backend for the current DCC.
    """
    dcc = detect_dcc()

    if dcc == "houdini":
        from CrucibleLightStudio.core.backend_houdini import HoudiniBackend
        return HoudiniBackend()
    elif dcc == "maya":
        from CrucibleLightStudio.core.backend_maya import MayaBackend
        return MayaBackend()
    elif dcc == "blender":
        from CrucibleLightStudio.core.backend_blender import BlenderBackend
        return BlenderBackend()
    else:
        logger.warning("No DCC detected. Running in standalone/mock mode.")
        return None
