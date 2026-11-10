# -*- coding: utf-8 -*-
"""
Crucible Light Studio - USD Stage Manager
==========================================
Core engine that interfaces with the Solaris USD stage to create, edit,
and manage light primitives. All operations go through a single Edit LOP
node to keep the artist's node graph clean.
"""

import logging
import math

try:
    import hou
except ImportError:
    hou = None

try:
    from pxr import Usd, UsdLux, UsdGeom, Sdf, Gf, UsdShade
except ImportError:
    Usd = UsdLux = UsdGeom = Sdf = Gf = UsdShade = None

logger = logging.getLogger("CrucibleLightStudio")


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
CRUCIBLE_ROOT = "/CrucibleLightStudio"
LIGHTS_ROOT = f"{CRUCIBLE_ROOT}/Lights"


# USD Light type mapping
LIGHT_TYPES = {
    "DomeLight":    UsdLux.DomeLight if UsdLux else None,
    "DistantLight": UsdLux.DistantLight if UsdLux else None,
    "RectLight":    UsdLux.RectLight if UsdLux else None,
    "DiskLight":    UsdLux.DiskLight if UsdLux else None,
    "SphereLight":  UsdLux.SphereLight if UsdLux else None,
    "CylinderLight":UsdLux.CylinderLight if UsdLux else None,
}

# Kelvin-to-RGB LUT for common photographic colour temperatures
KELVIN_PRESETS = {
    "Candle":       1900,
    "Tungsten":     2700,
    "Halogen":      3200,
    "Fluorescent":  4000,
    "Daylight":     5600,
    "Overcast":     6500,
    "Shade":        7500,
    "Blue Sky":     10000,
}


class LightData:
    """Data container representing a single USD light for the UI."""

    def __init__(self, prim_path="", light_type="RectLight", name="",
                 enabled=True, solo=False, exposure=0.0, intensity=1.0,
                 color=(1.0, 1.0, 1.0), temperature=6500,
                 use_temperature=False, width=1.0, height=1.0,
                 texture_path="", gobo_path="",
                 normalize=False, diffuse=1.0, specular=1.0,
                 cone_angle=90.0, softness=0.0,
                 shadow_enable=True, shadow_color=(0, 0, 0),
                 translate=(0.0, 0.0, 0.0), rotate=(0.0, 0.0, 0.0),
                 fx_mode="none", links="", sss=1.0, volume=1.0):
        self.prim_path = prim_path
        self.light_type = light_type
        self.name = name or prim_path.split("/")[-1]
        self.enabled = enabled
        self.solo = solo
        self.exposure = exposure
        self.intensity = intensity
        self.color = color
        self.temperature = temperature
        self.use_temperature = use_temperature
        self.width = width
        self.height = height
        self.texture_path = texture_path
        self.gobo_path = gobo_path
        self.normalize = normalize
        self.diffuse = diffuse
        self.specular = specular
        self.cone_angle = cone_angle
        self.softness = softness
        self.shadow_enable = shadow_enable
        self.shadow_color = shadow_color
        self.translate = translate
        self.rotate = rotate
        self.fx_mode = fx_mode
        self.links = links
        self.sss = sss
        self.volume = volume


def _is_ignored_light(name, prim_path=""):
    """
    Filter out non-lighting utility nodes (lookdev rigs, projection cameras, etc.).
    Environment dome and sun lights (including hdri_dome and hdri_sun) are explicitly
    ALLOWED and displayed in the Light Manager so artists can adjust them directly.
    """
    low_name = str(name).lower()
    low_path = str(prim_path).lower()

    # Explicitly allow dome and sun lights (including hdri_dome and hdri_sun)
    if "dome" in low_name or "sun" in low_name or "dome" in low_path or "sun" in low_path:
        return False

    # Filter out utility / helper nodes
    for keyword in ["lookdev", "projection", "calibration", "ground_grid", "sphere_rig", "room_mesh", "hdri_match"]:
        if keyword in low_name or keyword in low_path:
            return True

    return False

class StageManager:
    """
    Manages interaction with the current Solaris USD stage.

    All write operations are funneled through a single 'Edit LOP' node
    (crucible_edit) so the artist's graph stays clean. Read operations
    traverse the composed stage directly.
    """

    def __init__(self):
        self._edit_node = None
        self._cached_lights = []

    # ------------------------------------------------------------------
    # Stage Access
    # ------------------------------------------------------------------
    def get_current_stage(self):
        """Return the USD stage from the currently-displayed LOP node."""
        if not hou:
            logger.warning("Not running inside Houdini.")
            return None
        try:
            lop_node = hou.node("/stage").displayNode()
            if lop_node is None:
                logger.warning("No display node found in /stage.")
                return None
            return lop_node.stage()
        except Exception as exc:
            logger.error("Failed to get stage: %s", exc)
            return None

    def get_or_create_edit_node(self):
        """
        Get or create the single Edit LOP node that Crucible uses for
        all USD authoring. This keeps the node graph tidy.
        """
        if not hou:
            return None

        stage_net = hou.node("/stage")
        if stage_net is None:
            logger.error("No /stage network found.")
            return None

        # Try to find existing node
        node = stage_net.node("crucible_light_studio")
        if node is not None:
            self._edit_node = node
            return node

        # Create a Python LOP that we drive from our panel
        display = stage_net.displayNode()
        with hou.undos.group("Crucible: Create edit node"):
            node = stage_net.createNode("editproperties", "crucible_light_studio")
            if display:
                node.setInput(0, display)
            node.setDisplayFlag(True)
            node.setRenderFlag(True)
            node.moveToGoodPosition()

        self._edit_node = node
        logger.info("Created Crucible edit node: %s", node.path())
        return node

    # ------------------------------------------------------------------
    # Light Discovery
    # ------------------------------------------------------------------
    def discover_lights(self):
        """
        Walk the composed USD stage and return a list of LightData objects
        for every UsdLux light prim found.
        """
        stage = self.get_current_stage()
        if stage is None:
            return []

        lights = []
        for prim in stage.Traverse():
            if not prim.IsA(UsdLux.BoundableLightBase) and not prim.IsA(UsdLux.NonboundableLightBase):
                continue
            if _is_ignored_light(prim.GetName(), str(prim.GetPath())):
                continue

            ld = LightData(prim_path=str(prim.GetPath()))

            # Determine concrete light type
            for type_name, usd_cls in LIGHT_TYPES.items():
                if usd_cls and prim.IsA(usd_cls):
                    ld.light_type = type_name
                    break

            # Read common attributes
            ld.name = prim.GetName()

            vis_attr = UsdGeom.Imageable(prim).GetVisibilityAttr()
            if vis_attr and vis_attr.Get() == UsdGeom.Tokens.invisible:
                ld.enabled = False

            for attr_name, field in [
                ("inputs:exposure", "exposure"),
                ("inputs:intensity", "intensity"),
                ("inputs:diffuse", "diffuse"),
                ("inputs:specular", "specular"),
                ("inputs:sss", "sss"),
                ("inputs:volume", "volume"),
                ("inputs:enableColorTemperature", "use_temperature"),
                ("inputs:colorTemperature", "temperature"),
                ("inputs:normalize", "normalize"),
            ]:
                attr = prim.GetAttribute(attr_name)
                if attr and attr.HasValue():
                    setattr(ld, field, attr.Get())

            color_attr = prim.GetAttribute("inputs:color")
            if color_attr and color_attr.HasValue():
                c = color_attr.Get()
                ld.color = (c[0], c[1], c[2])

            tex_attr = prim.GetAttribute("inputs:texture:file")
            if tex_attr and tex_attr.HasValue():
                ld.texture_path = str(tex_attr.Get()).replace("@", "")

            # Shaping (for spot-like lights)
            cone_attr = prim.GetAttribute("inputs:shaping:cone:angle")
            if cone_attr and cone_attr.HasValue():
                ld.cone_angle = cone_attr.Get()

            soft_attr = prim.GetAttribute("inputs:shaping:cone:softness")
            if soft_attr and soft_attr.HasValue():
                ld.softness = soft_attr.Get()

            # Size for rect/disk
            w_attr = prim.GetAttribute("inputs:width")
            if w_attr and w_attr.HasValue():
                ld.width = w_attr.Get()
            h_attr = prim.GetAttribute("inputs:height")
            if h_attr and h_attr.HasValue():
                ld.height = h_attr.Get()

            lights.append(ld)

        def _light_sort_key(ld):
            lname = (ld.name or "").lower()
            ltype = (ld.light_type or "").lower()
            if "dome" in lname or "dome" in ltype:
                return (0, lname)
            elif "sun" in lname or "distant" in ltype:
                return (1, lname)
            else:
                return (2, lname)

        lights.sort(key=_light_sort_key)
        self._cached_lights = lights
        return lights

    # ------------------------------------------------------------------
    # Light Creation
    # ------------------------------------------------------------------
    def create_light(self, light_type="RectLight", name=None, parent_path=None):
        """
        Author a new USD light prim on the stage via inline Python LOP.

        Args:
            light_type: One of LIGHT_TYPES keys.
            name: Optional human-readable name.
            parent_path: USD parent path. Defaults to /lights.

        Returns:
            The Sdf.Path of the created light, or None on failure.
        """
        if not hou:
            return None

        parent = parent_path or "/lights"
        if name is None:
            existing = self.discover_lights()
            idx = sum(1 for l in existing if l.light_type == light_type) + 1
            name = f"{light_type}_{idx}"

        prim_path = f"{parent}/{name}"

        # Use an inline Python LOP for atomic creation
        stage_net = hou.node("/stage")
        if stage_net is None:
            return None

        display = stage_net.displayNode()

        with hou.undos.group(f"Crucible: Create {light_type}"):
            # Create the appropriate light node
            node_type_map = {
                "DomeLight": "domelight",
                "DistantLight": "distantlight",
                "RectLight": "rectlight",
                "DiskLight": "disklight",
                "SphereLight": "spherelight",
                "CylinderLight": "cylinderlight",
            }

            houdini_type = node_type_map.get(light_type, "rectlight")
            light_node = stage_net.createNode(houdini_type, name)

            if display:
                light_node.setInput(0, display)

            light_node.setDisplayFlag(True)
            light_node.setRenderFlag(True)
            light_node.moveToGoodPosition()

            # Set default prim path
            try:
                light_node.parm("primpath").set(prim_path)
            except Exception:
                pass

            # Apply sensible defaults
            self._apply_defaults(light_node, light_type)

        logger.info("Created %s at %s", light_type, prim_path)
        return Sdf.Path(prim_path) if Sdf else prim_path

    def _apply_defaults(self, node, light_type):
        """Apply production-sensible defaults to a newly created light."""
        try:
            if node.parm("exposure"):
                node.parm("exposure").set(0.0)
            if node.parm("intensity"):
                node.parm("intensity").set(1.0)

            # Rect and Disk defaults — reasonable studio size
            if light_type == "RectLight":
                if node.parm("width"):
                    node.parm("width").set(2.0)
                if node.parm("height"):
                    node.parm("height").set(2.0)
                if node.parm("normalize"):
                    node.parm("normalize").set(True)

            elif light_type == "DomeLight":
                if node.parm("exposure"):
                    node.parm("exposure").set(0.0)

            elif light_type == "DistantLight":
                if node.parm("angle"):
                    node.parm("angle").set(0.53)  # Sun angular diameter

        except Exception as exc:
            logger.warning("Could not apply defaults: %s", exc)

    # ------------------------------------------------------------------
    # Light Editing
    # ------------------------------------------------------------------
    def set_light_attribute(self, prim_path, attr_name, value):
        """
        Set a single attribute on a light prim using an Edit Properties node.
        """
        stage = self.get_current_stage()
        if stage is None:
            return False

        prim = stage.GetPrimAtPath(prim_path)
        if not prim.IsValid():
            logger.error("Prim not found: %s", prim_path)
            return False

        attr = prim.GetAttribute(attr_name)
        if not attr.IsValid():
            logger.error("Attribute not found: %s on %s", attr_name, prim_path)
            return False

        # Find the LOP node that owns this prim and edit it
        try:
            lop_node = self._find_lop_node_for_prim(prim_path)
            if lop_node is None:
                logger.error("No LOP node found for prim: %s", prim_path)
                return False

            # Map USD attr names to Houdini parm names
            parm_map = {
                "inputs:exposure": "exposure",
                "inputs:intensity": "intensity",
                "inputs:color": "color",
                "inputs:colorTemperature": "colorTemperature",
                "inputs:enableColorTemperature": "enableColorTemperature",
                "inputs:diffuse": "diffuse",
                "inputs:specular": "specular",
                "inputs:sss": "sss",
                "inputs:volume": "volume",
                "inputs:normalize": "normalize",
                "inputs:width": "width",
                "inputs:height": "height",
                "inputs:texture:file": "texture",
                "inputs:shaping:cone:angle": "coneAngle",
                "inputs:shaping:cone:softness": "coneSoftness",
            }

            parm_name = parm_map.get(attr_name, attr_name)

            with hou.undos.group(f"Crucible: Set {attr_name}"):
                if attr_name == "inputs:color" and isinstance(value, (list, tuple)):
                    for i, comp in enumerate(["r", "g", "b"]):
                        p = lop_node.parm(f"{parm_name}{comp}")
                        if p:
                            p.set(value[i])
                else:
                    p = lop_node.parm(parm_name)
                    if p:
                        p.set(value)

            return True

        except Exception as exc:
            logger.error("Failed to set attribute: %s", exc)
            return False

    def set_light_visibility(self, prim_path, visible):
        """Toggle light visibility (enable/disable) without deleting."""
        lop_node = self._find_lop_node_for_prim(prim_path)
        if lop_node is None:
            return False

        with hou.undos.group("Crucible: Toggle light"):
            # Use the light node's own activation if available
            active_parm = lop_node.parm("light_enable")
            if active_parm:
                active_parm.set(visible)
            else:
                # Fallback: set visibility via USD
                stage = self.get_current_stage()
                if stage:
                    prim = stage.GetPrimAtPath(prim_path)
                    if prim.IsValid():
                        img = UsdGeom.Imageable(prim)
                        if visible:
                            img.MakeVisible()
                        else:
                            img.MakeInvisible()
        return True

    def delete_light(self, prim_path):
        """Remove a light prim from the stage."""
        lop_node = self._find_lop_node_for_prim(prim_path)
        if lop_node is None:
            return False

        with hou.undos.group("Crucible: Delete light"):
            lop_node.destroy()

        logger.info("Deleted light node for: %s", prim_path)
        return True

    # ------------------------------------------------------------------
    # Solo / Mute
    # ------------------------------------------------------------------
    def solo_light(self, prim_path):
        """
        Solo a single light: hide all others, show only this one.
        Stores original visibility states for restoration.
        """
        lights = self.discover_lights()
        self._solo_backup = {}

        for ld in lights:
            self._solo_backup[ld.prim_path] = ld.enabled
            if ld.prim_path == prim_path:
                self.set_light_visibility(ld.prim_path, True)
            else:
                self.set_light_visibility(ld.prim_path, False)

    def unsolo_all(self):
        """Restore all lights to their pre-solo visibility states."""
        if not hasattr(self, '_solo_backup'):
            return
        for path, was_enabled in self._solo_backup.items():
            self.set_light_visibility(path, was_enabled)
        self._solo_backup = {}

    # ------------------------------------------------------------------
    # Energy-Conserving Scale
    # ------------------------------------------------------------------
    def energy_conserving_scale(self, prim_path, old_width, old_height,
                                 new_width, new_height):
        """
        Scale a light while adjusting intensity to maintain consistent
        energy output (inspired by HDR Light Studio's Shift+Scale).

        The total energy of an area light is proportional to its area.
        When scaling, we adjust intensity by the inverse area ratio.
        """
        old_area = old_width * old_height
        new_area = new_width * new_height

        if new_area <= 0 or old_area <= 0:
            return

        ratio = old_area / new_area

        self.set_light_attribute(prim_path, "inputs:width", new_width)
        self.set_light_attribute(prim_path, "inputs:height", new_height)

        # Adjust exposure logarithmically
        stage = self.get_current_stage()
        if stage:
            prim = stage.GetPrimAtPath(prim_path)
            if prim.IsValid():
                exp_attr = prim.GetAttribute("inputs:exposure")
                current_exp = exp_attr.Get() if exp_attr else 0.0
                exposure_offset = math.log2(ratio)
                new_exposure = current_exp + exposure_offset
                self.set_light_attribute(prim_path, "inputs:exposure", new_exposure)

    # ------------------------------------------------------------------
    # Texture / Gobo Application
    # ------------------------------------------------------------------
    def apply_texture(self, prim_path, texture_path):
        """Apply an HDRI or gobo texture to a light."""
        return self.set_light_attribute(prim_path, "inputs:texture:file",
                                        texture_path)

    def clear_texture(self, prim_path):
        """Remove texture from a light."""
        return self.set_light_attribute(prim_path, "inputs:texture:file", "")

    # ------------------------------------------------------------------
    # Renderer Detection
    # ------------------------------------------------------------------
    def detect_renderer(self):
        """
        Detect the active renderer from the Solaris render settings.
        Returns 'karma', 'arnold', or 'unknown'.
        """
        stage = self.get_current_stage()
        if stage is None:
            return "unknown"

        # Check for Karma render settings
        for prim in stage.Traverse():
            prim_type = prim.GetTypeName()
            if "Karma" in prim_type or "karma" in str(prim.GetPath()).lower():
                return "karma"
            if "Arnold" in prim_type or "arnold" in str(prim.GetPath()).lower():
                return "arnold"

        # Fallback: check Houdini node types
        if hou:
            stage_net = hou.node("/stage")
            if stage_net:
                for child in stage_net.children():
                    if "karma" in child.type().name().lower():
                        return "karma"
                    if "arnold" in child.type().name().lower():
                        return "arnold"

        return "karma"  # Default to Karma

    # ------------------------------------------------------------------
    # Internal Helpers
    # ------------------------------------------------------------------
    def _find_lop_node_for_prim(self, prim_path):
        """
        Find the Houdini LOP node responsible for authoring a given
        USD prim path. We walk the /stage children looking for a match.
        """
        if not hou:
            return None

        stage_net = hou.node("/stage")
        if stage_net is None:
            return None

        prim_name = prim_path.rstrip("/").split("/")[-1]

        for child in stage_net.children():
            try:
                pp = child.parm("primpath")
                if pp and prim_name in pp.eval():
                    return child
            except Exception:
                continue

            # Also check by node name
            if child.name() == prim_name:
                return child

        return None
