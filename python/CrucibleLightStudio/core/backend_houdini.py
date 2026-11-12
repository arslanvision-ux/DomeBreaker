# -*- coding: utf-8 -*-
"""
Crucible Light Studio - Houdini/Solaris Backend
=================================================
Houdini Solaris backend designed to interface with the Crucible Light Studio.
This backend manages physical Houdini LOP nodes natively in the graph, providing
artists with physical nodes they can wire and interact with in the Network Editor.
"""

import os
import json
import logging
import math

try:
    import hou
except ImportError:
    hou = None

from CrucibleLightStudio.core.dcc_backend import DCCBackend
from CrucibleLightStudio.core.stage_manager import LightData, LIGHT_TYPES

logger = logging.getLogger("CrucibleLightStudio.houdini")

def _get_lop_parm(node, name_substring):
    """Helper to find LOP parameters which have dynamic suffixes in Houdini 19/20/21."""
    
    # 0. Try exact match first
    p = node.parm(name_substring)
    if p: return p
    
    name_substring = name_substring.lower()
    
    # 0.5 Try case-insensitive exact match
    for p in node.parms():
        if p.name().lower() == name_substring:
            return p
            
    # 1. Try exact USD schema alias
    p = node.parm(f"inputs:{name_substring}")
    if p: return p
    
    # 1.5 Try case-insensitive exact USD schema alias
    target = f"inputs:{name_substring}"
    for p in node.parms():
        if p.name().lower() == target:
            return p
    
    # 2. Try xn__ prefix with the alias
    for p in node.parms():
        pname = p.name().lower()
        if f"inputs:{name_substring}" in pname or f"inputs_{name_substring}" in pname or f"inputs{name_substring}" in pname:
            if "control" in pname: continue
            return p
            
    # 3. Fallback search
    for p in node.parms():
        pname = p.name().lower()
        if name_substring in pname:
            if "control" in pname: continue
            if name_substring == "color" and ("temperature" in pname or "space" in pname): continue
            if name_substring == "texture" and "enable" in pname: continue
            if name_substring == "colortemperature" and "enable" in pname: continue
            return p
    return None


def _splice_into_stage_stream(crucible_node, stage):
    """Ensure crucible_light_studio is placed at the bottom of the lighting stream in /stage:
    hdri_dome -> hdri_sun -> [practicals] -> hdri_match_projection -> hdri_match_lookdev -> crucible_light_studio -> [camera/render].
    """
    if not stage or not crucible_node:
        return

    dome_node = stage.node("hdri_dome")
    sun_node = stage.node("hdri_sun")
    proj_node = stage.node("hdri_match_projection")
    lookdev_node = stage.node("hdri_match_lookdev")

    # Find practicals / extracted lights
    practicals = []
    for c in stage.children():
        if c not in (dome_node, sun_node, proj_node, lookdev_node, crucible_node):
            if c.name().startswith("practical_") or c.name().startswith("emitter_") or c.name().startswith("light_extract"):
                practicals.append(c)

    # Determine lighting tail
    tail = None
    if lookdev_node:
        tail = lookdev_node
    elif proj_node:
        tail = proj_node
    elif practicals:
        tail = practicals[-1]
    elif sun_node:
        tail = sun_node
    elif dome_node:
        tail = dome_node
    else:
        for n in stage.children():
            if n != crucible_node and not n.type().name().startswith("camera") and not n.type().name().startswith("render"):
                tail = n

    old_outputs = []
    if tail and tail != crucible_node:
        # Find what was connected to tail's output so we can rewire to crucible_node
        for child in stage.children():
            if child != crucible_node and child.inputs() and child.inputs()[0] == tail:
                old_outputs.append(child)

        crucible_node.setInput(0, tail)

        # Rewire old outputs to crucible_node
        for o in old_outputs:
            try:
                o.setInput(0, crucible_node)
            except Exception:
                pass

    if old_outputs:
        try:
            old_outputs[-1].setDisplayFlag(True)
        except Exception:
            pass
    else:
        try:
            crucible_node.setDisplayFlag(True)
            crucible_node.setRenderFlag(True)
        except Exception:
            pass

    try:
        stage.layoutChildren()
    except Exception:
        pass

def _enable_lop_control(node, p, enable=True):
    """Force-enable or disable the USD authoring control for a parameter in Solaris."""
    if not p: return
    pname = p.name()
    ctrl = node.parm(pname + "_control")
    
    if not ctrl:
        # In Solaris, parm is often `<prefix>_<suffix>` and control is `<prefix>_control_<suffix>`
        # e.g. `xn__inputsexposure_vya` -> `xn__inputsexposure_control_wcb`
        # e.g. `xn__inputscolor_ztar` -> `xn__inputscolor_control_06a`
        # e.g. `xn__inputsenableColorTemperature_omb` -> `xn__inputsenableColorTemperature_control_pzb`
        # e.g. `xn__inputscolorTemperature_wcb` -> `xn__inputscolorTemperature_control_xpb`
        # Strip trailing component suffix (last `_...`)
        prefix = pname.rsplit("_", 1)[0] if "_" in pname else pname
        # Also handle color components like `xn__inputscolor_ztar` -> `xn__inputscolor`
        if prefix.endswith(("r", "g", "b")) and "_" in prefix:
            alt_prefix = prefix.rsplit("_", 1)[0]
        else:
            alt_prefix = prefix

        for cp in node.parms():
            cname = cp.name()
            if "control" in cname.lower():
                if cname.startswith(f"{prefix}_control") or cname.startswith(f"{alt_prefix}_control"):
                    ctrl = cp
                    break
                    
    if not ctrl:
        base_name = p.name()
        if "_" in base_name:
            for part in base_name.replace(":", "_").split("_"):
                if any(k in part.lower() for k in ["color", "texture", "exposure", "intensity", "diffuse", "specular", "temperature", "lpe", "aov", "group"]):
                    base_name = part
                    break
                    
        for cp in node.parms():
            cname = cp.name().lower()
            if "control" in cname and base_name.lower() in cname:
                ctrl = cp
                break

    if not ctrl:
        clean_name = p.name().lower().replace("xn__", "").replace("inputs", "").replace(":", "_")
        for cp in node.parms():
            cname = cp.name().lower()
            if "control" in cname:
                for token in ["exposure", "color", "intensity", "diffuse", "specular", "temperature", "texture", "sss", "volume"]:
                    if token in clean_name and token in cname:
                        ctrl = cp
                        break
            if ctrl:
                break
    if ctrl:
        try:
            ctrl.set("set" if enable else "none")
            if not enable:
                try: ctrl.set("block")
                except: pass
        except:
            try: ctrl.set(1 if enable else 0)
            except: pass

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

class HoudiniBackend(DCCBackend):
    RENDERERS = ["karma", "arnold", "redshift"]

    def __init__(self):
        self._solo_backup = {}
        self._active_renderer = "karma"

    def _find_light_node(self, prim_path):
        """
        Find the native Houdini node corresponding to prim_path.
        Supports internal nodes (e.g. /stage/crucible_light_studio/light1)
        and external USD stage nodes (e.g. /lights/practicals/practical_01 -> /stage/practical_01).
        """
        if not hou: return None
        node = hou.node(prim_path)
        if node: return node

        hda = self._get_hda()
        stage = hda.parent() if hda else (hou.node("/stage") or hou.node("/obj/stage"))
        if not stage: return None

        prim_name = prim_path.rstrip("/").split("/")[-1]
        cand = stage.node(prim_name)
        if cand: return cand

        for child in stage.children():
            p_prim = child.parm("primpath")
            if p_prim and (p_prim.eval() == prim_path or p_prim.eval().endswith("/" + prim_name)):
                return child
            if child.name() == prim_name:
                return child

        return None
        
    def get_name(self):
        return "houdini"

    def set_active_renderer(self, renderer):
        self._active_renderer = str(renderer).lower()

    def get_active_renderer(self):
        return self._active_renderer

    def _get_hda(self, create_if_missing=False):
        if hou and hasattr(hou, "isUIAvailable") and hou.isUIAvailable():
            try:
                desktop = hou.ui.curDesktop()
                pane = desktop.paneTabOfType(hou.paneTabType.NetworkEditor)
                if pane and pane.pwd().childTypeCategory() == hou.lopNodeTypeCategory():
                    pwd = pane.pwd()
                    for node in pwd.children():
                        if node.type().name() == "crucible_light_studio":
                            return node
            except Exception:
                pass
        
        # Fallback
        stage = hou.node("/stage") if hou else None
        if stage:
            for node in stage.children():
                if node.type().name() == "crucible_light_studio" or "crucible" in node.name().lower():
                    return node

        if create_if_missing and hou:
            try:
                module_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                root = os.environ.get("CRUCIBLE_LIGHT_STUDIO_ROOT", module_dir)
                hda_candidates = [
                    os.path.join(root, "hda", "crucible_light_studio.hda"),
                    os.path.join(module_dir, "hda", "crucible_light_studio.hda"),
                    os.path.join(module_dir, "..", "..", "houdini", "otls", "crucible_light_studio.hda"),
                ]
                loaded_hdafiles = {f.replace("\\", "/") for f in hou.hda.loadedFiles()}
                for hda_file in hda_candidates:
                    if os.path.exists(hda_file):
                        if hda_file.replace("\\", "/") not in loaded_hdafiles:
                            try:
                                hou.hda.installFile(hda_file)
                            except Exception:
                                pass
                        break
                if not stage:
                    from hdri_match_solaris.usd_helpers import get_or_create_solaris_stage_node
                    stage = get_or_create_solaris_stage_node(create=True)
                node = stage.createNode("crucible_light_studio", "crucible_light_studio")
                _splice_into_stage_stream(node, stage)
                node.moveToGoodPosition()
                return node
            except Exception as e:
                logger.error(f"Failed to auto-create crucible_light_studio node: {e}")
                return None

        return None

    def _is_light_node(self, node):
        """Check if a node is a USD light LOP (handles versioned types like light::2.0)."""
        if not node: return False
        tname = node.type().name().lower()
        # Match light, domelight, distantlight but NOT lightlinker, lightmixer etc.
        if tname.startswith("domelight") or tname.startswith("distantlight"):
            return True
        # For generic 'light' node, ensure it's exactly 'light' or 'light::version'
        if tname == "light" or tname.startswith("light::"):
            return True
        return False

    def _apply_edits(self, ld, attrs):
        """Apply local dictionary overrides onto a LightData instance."""
        if not attrs or not isinstance(attrs, dict): return
        if "exposure" in attrs: ld.exposure = float(attrs["exposure"])
        if "intensity" in attrs: ld.intensity = float(attrs["intensity"])
        if "color" in attrs and isinstance(attrs["color"], (list, tuple)) and len(attrs["color"]) >= 3:
            ld.color = (float(attrs["color"][0]), float(attrs["color"][1]), float(attrs["color"][2]))
        if "temperature" in attrs: ld.temperature = float(attrs["temperature"])
        if "use_temperature" in attrs: ld.use_temperature = bool(attrs["use_temperature"])
        if "texture" in attrs: ld.texture_path = str(attrs["texture"])
        if "diffuse" in attrs: ld.diffuse = float(attrs["diffuse"])
        if "specular" in attrs: ld.specular = float(attrs["specular"])
        if "sss" in attrs: ld.sss = float(attrs["sss"])
        if "volume" in attrs: ld.volume = float(attrs["volume"])
        if "fx_mode" in attrs: ld.fx_mode = str(attrs["fx_mode"])
        if "enabled" in attrs: ld.enabled = bool(attrs["enabled"])

    def _extract_node_light_data(self, node, prim_path=None):
        """Extract LightData from a native Houdini LOP light node."""
        if not prim_path:
            p_prim = _get_lop_parm(node, "primpath") or node.parm("primpath")
            prim_path = p_prim.evalAsString() if p_prim else f"/lights/practicals/{node.name()}"

        ld = LightData(prim_path=prim_path, name=node.name())
        ld.light_type = "RectLight"

        tname = node.type().name().lower()
        if tname.startswith("domelight"):
            ld.light_type = "DomeLight"
        elif tname.startswith("distantlight"):
            ld.light_type = "DistantLight"
        elif tname.startswith("light"):
            lt_parm = _get_lop_parm(node, "lighttype")
            if lt_parm:
                lt_str = lt_parm.evalAsString().lower()
                if "disk" in lt_str: ld.light_type = "DiskLight"
                elif "sphere" in lt_str: ld.light_type = "SphereLight"
                elif "cylinder" in lt_str: ld.light_type = "CylinderLight"
                elif "point" in lt_str: ld.light_type = "PointLight"
                elif "dome" in lt_str or "env" in lt_str: ld.light_type = "DomeLight"
                elif "distant" in lt_str: ld.light_type = "DistantLight"
                else: ld.light_type = "RectLight"

        w_parm = _get_lop_parm(node, "width")
        if w_parm:
            try: ld.width = float(w_parm.eval())
            except Exception: pass

        h_parm = _get_lop_parm(node, "height")
        if h_parm:
            try: ld.height = float(h_parm.eval())
            except Exception: pass

        rad_parm = _get_lop_parm(node, "radius")
        if rad_parm:
            try: ld.radius = float(rad_parm.eval())
            except Exception: pass

        temp_en = _get_lop_parm(node, "enablecolortemperature")
        if temp_en:
            try: ld.use_temperature = bool(temp_en.eval())
            except Exception: pass

        p_exp = _get_lop_parm(node, "exposure")
        if p_exp:
            try: ld.exposure = float(p_exp.eval())
            except Exception: pass

        p_int = _get_lop_parm(node, "intensity")
        if p_int:
            try: ld.intensity = float(p_int.eval())
            except Exception: pass

        p_col = _get_lop_parm(node, "color")
        if p_col:
            try:
                c = p_col.tuple().eval()
                if isinstance(c, (tuple, list)) and len(c) >= 3:
                    ld.color = (float(c[0]), float(c[1]), float(c[2]))
            except Exception: pass

        p_tex = _get_lop_parm(node, "texture") or _get_lop_parm(node, "texturefile") or _get_lop_parm(node, "texture:file")
        if p_tex:
            try: ld.texture_path = str(p_tex.eval())
            except Exception: pass

        p_temp = _get_lop_parm(node, "colortemperature")
        if p_temp:
            try: ld.temperature = float(p_temp.eval())
            except Exception: pass

        try: ld.fx_mode = node.userData("fx_mode") or "none"
        except Exception: ld.fx_mode = "none"

        p_diff = _get_lop_parm(node, "diffuse")
        if p_diff:
            try: ld.diffuse = float(p_diff.eval())
            except Exception: pass

        p_spec = _get_lop_parm(node, "specular")
        if p_spec:
            try: ld.specular = float(p_spec.eval())
            except Exception: pass

        p_sss = _get_lop_parm(node, "sss") or _get_lop_parm(node, "sssMultiplier") or node.parm("xn__redshiftlightRSL_sssScale_uhbf")
        if p_sss:
            try: ld.sss = float(p_sss.eval())
            except Exception: pass

        p_vol = _get_lop_parm(node, "volume") or _get_lop_parm(node, "volumeMultiplier") or node.parm("xn__redshiftlightRSL_volumeScale_qmbf")
        if p_vol:
            try: ld.volume = float(p_vol.eval())
            except Exception: pass

        p_t = node.parmTuple("t")
        if p_t:
            try: ld.translate = [float(p_t[0].eval()), float(p_t[1].eval()), float(p_t[2].eval())]
            except Exception: pass

        p_r = node.parmTuple("r")
        if p_r:
            try: ld.rotate = [float(p_r[0].eval()), float(p_r[1].eval()), float(p_r[2].eval())]
            except Exception: pass

        # Disabled / Bypassed status
        ld.enabled = not node.isBypassed()
        return ld

    def discover_lights(self):
        lights = []
        if not hou:
            return lights

        hda = self._get_hda()
        internal_prim_paths = set()

        # Load local edits
        edits_json = "{}"
        if hda:
            try: edits_json = hda.evalParm("light_edits_json")
            except Exception: pass
        try: edits = json.loads(edits_json)
        except Exception: edits = {}

        # 1. Discover internal lights inside HDA
        if hda:
            for node in hda.children():
                if _is_ignored_light(node.name(), node.path()):
                    continue
                if self._is_light_node(node):
                    internal_prim_paths.add(node.path())
                    ld = self._extract_node_light_data(node, prim_path=node.path())
                    if node.path() in edits:
                        self._apply_edits(ld, edits[node.path()])
                    lights.append(ld)

        # 2. Discover external light nodes in /stage (discovers BOTH enabled and disabled/bypassed lights!)
        stage_parent = hda.parent() if hda else (hou.node("/stage") or hou.node("/obj/stage"))
        if stage_parent:
            for node in stage_parent.children():
                if node == hda:
                    continue
                if _is_ignored_light(node.name(), node.path()):
                    continue
                if self._is_light_node(node):
                    p_prim = _get_lop_parm(node, "primpath") or node.parm("primpath")
                    p_str = p_prim.evalAsString() if p_prim else ""
                    if not p_str:
                        p_str = f"/lights/practicals/{node.name()}"

                    if p_str in internal_prim_paths or node.path() in internal_prim_paths:
                        continue
                    internal_prim_paths.add(p_str)
                    internal_prim_paths.add(node.path())

                    ld = self._extract_node_light_data(node, prim_path=p_str)
                    if p_str in edits:
                        self._apply_edits(ld, edits[p_str])
                    elif node.path() in edits:
                        self._apply_edits(ld, edits[node.path()])
                    lights.append(ld)

        # 3. Discover external USD stage prims (from sublayers, files, etc.)
        input_node = hda.inputs()[0] if (hda and hda.inputs()) else None
        if not input_node and stage_parent:
            lops = [c for c in stage_parent.children() if hasattr(c, "stage")]
            if lops:
                input_node = lops[-1]

        if input_node:
            stage = input_node.stage()
            if stage:
                from pxr import Usd, UsdLux, UsdGeom
                for prim in stage.TraverseAll():
                    is_light = False
                    if hasattr(UsdLux, "LightAPI") and prim.HasAPI(UsdLux.LightAPI):
                        is_light = True
                    elif "Light" in prim.GetTypeName():
                        is_light = True

                    if is_light:
                        prim_path = str(prim.GetPath())
                        if prim_path in internal_prim_paths:
                            continue
                        name = prim.GetName()
                        if _is_ignored_light(name, prim_path):
                            continue

                        internal_prim_paths.add(prim_path)
                        ld = LightData(prim_path=prim_path, name=name)
                        ld.light_type = prim.GetTypeName() or "Light"
                        ld.enabled = prim.IsActive()
                        if prim.HasAttribute("inputs:mute"):
                            mute_attr = prim.GetAttribute("inputs:mute")
                            if mute_attr.IsValid() and mute_attr.Get() is True:
                                ld.enabled = False

                        try:
                            exp_attr = prim.GetAttribute("inputs:exposure")
                            if not exp_attr.IsValid() and hasattr(prim, "GetExposureAttr"):
                                exp_attr = prim.GetExposureAttr()
                            if exp_attr.IsValid():
                                ld.exposure = float(exp_attr.Get() or 0.0)

                            int_attr = prim.GetAttribute("inputs:intensity")
                            if not int_attr.IsValid() and hasattr(prim, "GetIntensityAttr"):
                                int_attr = prim.GetIntensityAttr()
                            if int_attr.IsValid():
                                ld.intensity = float(int_attr.Get() or 1.0)

                            col_attr = prim.GetAttribute("inputs:color")
                            if not col_attr.IsValid() and hasattr(prim, "GetColorAttr"):
                                col_attr = prim.GetColorAttr()
                            if col_attr.IsValid():
                                c = col_attr.Get()
                                if c:
                                    ld.color = (float(c[0]), float(c[1]), float(c[2]))

                            temp_attr = prim.GetAttribute("inputs:colorTemperature")
                            if not temp_attr.IsValid() and hasattr(prim, "GetColorTemperatureAttr"):
                                temp_attr = prim.GetColorTemperatureAttr()
                            if temp_attr.IsValid():
                                ld.temperature = float(temp_attr.Get() or 6500.0)

                            en_temp_attr = prim.GetAttribute("inputs:enableColorTemperature")
                            if not en_temp_attr.IsValid() and hasattr(prim, "GetEnableColorTemperatureAttr"):
                                en_temp_attr = prim.GetEnableColorTemperatureAttr()
                            if en_temp_attr.IsValid():
                                ld.use_temperature = bool(en_temp_attr.Get())

                            tex_attr = prim.GetAttribute("inputs:texture:file")
                            if tex_attr.IsValid():
                                val = tex_attr.Get()
                                if val:
                                    ld.texture_path = str(val.resolvedPath or val.path)

                            diff_attr = prim.GetAttribute("inputs:diffuse")
                            if diff_attr.IsValid():
                                ld.diffuse = float(diff_attr.Get() or 1.0)
                            spec_attr = prim.GetAttribute("inputs:specular")
                            if spec_attr.IsValid():
                                ld.specular = float(spec_attr.Get() or 1.0)
                            sss_attr = prim.GetAttribute("inputs:sss")
                            if not sss_attr.IsValid():
                                sss_attr = prim.GetAttribute("inputs:sssMultiplier")
                            if sss_attr.IsValid():
                                ld.sss = float(sss_attr.Get() or 1.0)
                            vol_attr = prim.GetAttribute("inputs:volume")
                            if not vol_attr.IsValid():
                                vol_attr = prim.GetAttribute("inputs:volumeMultiplier")
                            if vol_attr.IsValid():
                                ld.volume = float(vol_attr.Get() or 1.0)
                        except Exception:
                            pass

                        node_ref = self._find_light_node(prim_path)
                        if node_ref:
                            try:
                                fx = node_ref.userData("fx_mode")
                                if fx: ld.fx_mode = fx
                            except Exception: pass
                            try:
                                t_p = node_ref.parmTuple("t")
                                if t_p: ld.translate = [float(v) for v in t_p.eval()]
                                r_p = node_ref.parmTuple("r")
                                if r_p: ld.rotate = [float(v) for v in r_p.eval()]
                                w_p = _get_lop_parm(node_ref, "width")
                                if w_p: ld.width = float(w_p.eval())
                                h_p = _get_lop_parm(node_ref, "height")
                                if h_p: ld.height = float(h_p.eval())
                                rad_p = _get_lop_parm(node_ref, "radius")
                                if rad_p: ld.radius = float(rad_p.eval())
                            except Exception: pass
                            if ld.sss == 1.0:
                                p_sss = node_ref.parm("xn__redshiftlightRSL_sssScale_uhbf") or node_ref.parm("sss")
                                if p_sss:
                                    try: ld.sss = float(p_sss.eval())
                                    except Exception: pass
                            if ld.volume == 1.0:
                                p_vol = node_ref.parm("xn__redshiftlightRSL_volumeScale_qmbf") or node_ref.parm("volume")
                                if p_vol:
                                    try: ld.volume = float(p_vol.eval())
                                    except Exception: pass

                        if prim_path in edits:
                            self._apply_edits(ld, edits[prim_path])

                        lights.append(ld)

        # 4. Populate light linking data
        if hda:
            try:
                linking_json = hda.evalParm("light_linking_json")
                if linking_json:
                    links_dict = json.loads(linking_json)
                    for ld in lights:
                        if ld.name in links_dict:
                            ld.links = ",".join(links_dict[ld.name])
            except Exception:
                pass

        # Sort lights so environment / key sun lights (DomeLight, DistantLight) appear at top, followed by practicals
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
        return lights

    def _wire_lights(self, hda):
        """Wire all light and utility nodes into a clean chain: input → lights → utilities → output."""
        lights = []
        utility_nodes = []
        utility_names = ["crucible_fog", "crucible_lens_fx", "linking_engine"]
        
        for n in hda.children():
            if self._is_light_node(n):
                lights.append(n)
            elif n.name() in utility_names:
                utility_nodes.append(n)
                
        lights.sort(key=lambda x: x.name())
        # Sort utilities in a predictable order
        utility_order = {name: i for i, name in enumerate(utility_names)}
        utility_nodes.sort(key=lambda x: utility_order.get(x.name(), 99))
        
        # Clean up ONLY old legacy Python scripts, NOT our utility nodes
        for n in hda.children():
            if n.name() in ["light_engine", "fog_engine"]:
                try:
                    n.destroy()
                except:
                    # If locked in HDA, forcefully disconnect it from everything
                    try:
                        n.setInput(0, None)
                    except: pass
            elif n.type().name() == "python" and n.name() not in utility_names:
                try:
                    n.destroy()
                except:
                    try: n.setInput(0, None)
                    except: pass
        
        input_node = hda.item("1")
        if not input_node:
            for n in hda.children():
                if n.type().name() == "subnetconnector":
                    input_node = n
                    break
        
        # We should only have ONE output node inside the subnet. Delete extra outputs!
        outputs = []
        for n in hda.children():
            if n.type().name() == "output":
                outputs.append(n)
                
        output_node = hda.item("output0")
        if outputs:
            if not output_node:
                output_node = outputs[0]
            for o in outputs:
                if o != output_node:
                    try:
                        o.destroy()
                    except:
                        # If node cannot be destroyed (e.g. locked HDA), disconnect it
                        try: o.setInput(0, None)
                        except: pass

        # Wire: input → lights → utility nodes → output
        prev = input_node
        for l in lights:
            if prev:
                l.setInput(0, prev)
            prev = l
        
        for u in utility_nodes:
            if prev:
                u.setInput(0, prev)
            prev = u
            
        if output_node and prev:
            output_node.setInput(0, prev)
            
        hda.layoutChildren()

    def create_light(self, light_type="RectLight", name=None):
        hda = self._get_hda(create_if_missing=True)
        if not hda: return None
        
        hda.allowEditingOfContents()
        
        lop_type = "light"
        ltype_parm = "rect"
        if light_type == "DomeLight": lop_type = "domelight"
        elif light_type == "DistantLight": lop_type = "distantlight"
        elif light_type == "DiskLight": ltype_parm = "disk"
        elif light_type == "SphereLight": ltype_parm = "sphere"
        elif light_type == "CylinderLight": ltype_parm = "cylinder"
        elif light_type == "PointLight": ltype_parm = "point"
        
        node = hda.createNode(lop_type)
        if name:
            node.setName(name.replace(" ", "_"), unique_name=True)
            
        # Automatically set LPE / Light Group AOVs based on the node name for ALL renderers
        safe_name = node.name()
        for p in node.parms():
            cname = p.name().lower()
            desc = p.description().lower()
            if "control" in cname: continue
            
            is_tag = False
            # Karma
            if "karmalightlightgroup" in cname: is_tag = True
            # Arnold
            if "arnoldaov" in cname: is_tag = True
            # Redshift
            if "redshiftlight_group" in cname or "redshiftlightgroup" in cname: is_tag = True
            
            # General fallbacks
            if ("lpetag" in cname or "aov" in cname or "lightgroup" in cname or "light_group" in cname):
                is_tag = True
                
            if is_tag and p.parmTemplate().type() == hou.parmTemplateType.String:
                try: p.set(safe_name)
                except: pass
                _enable_lop_control(node, p, enable=True)
        if lop_type == "light":
            p = _get_lop_parm(node, "lighttype")
            if p:
                try:
                    tmpl = p.parmTemplate()
                    if hasattr(tmpl, "menuItems"):
                        items = tmpl.menuItems()
                        labels = tmpl.menuLabels()
                        target_idx = 0 # point default
                        for i, lbl in enumerate(labels):
                            lbl_low = lbl.lower()
                            if "rect" in light_type.lower() and ("rect" in lbl_low or "grid" in lbl_low): target_idx = i
                            elif "disk" in light_type.lower() and "disk" in lbl_low: target_idx = i
                            elif "sphere" in light_type.lower() and "sphere" in lbl_low: target_idx = i
                            elif "cylinder" in light_type.lower() and "cylinder" in lbl_low: target_idx = i
                        p.set(items[target_idx])
                except:
                    pass
                        
            # Enable dimensions for area lights
            if light_type in ["RectLight", "DiskLight", "CylinderLight"]:
                w_en = _get_lop_parm(node, "enablewidth")
                if w_en: w_en.set(1)
                h_en = _get_lop_parm(node, "enableheight")
                if h_en: h_en.set(1)
            
        self._wire_lights(hda)
        # Ensure crucible_light_studio is at the bottom of the stream when extra lights are added
        if hda and hda.parent():
            _splice_into_stage_stream(hda, hda.parent())
        return node.path()

    def remove_light(self, prim_path):
        node = self._find_light_node(prim_path)
        if not node:
            return
        
        hda = self._get_hda()
        # Internal light inside Crucible HDA
        if hda and node.parent() == hda:
            node.destroy()
            self._wire_lights(hda)
            return

        # External stage node (e.g. practical_01, practical_02...)
        # Re-link upstream to downstream outputs to heal the LOP network stream
        in_nodes = node.inputs()
        upstream_node = in_nodes[0] if in_nodes else None
        
        downstream_nodes = list(node.outputs())
        for out_node in downstream_nodes:
            for idx, inp in enumerate(out_node.inputs()):
                if inp == node:
                    out_node.setInput(idx, upstream_node)
                    
        node.destroy()
        
        # Clean up overrides in light_edits_json if present
        if hda:
            try:
                edits_p = hda.parm("light_edits_json")
                if edits_p:
                    import json
                    edits = json.loads(edits_p.eval() or "{}")
                    if prim_path in edits:
                        del edits[prim_path]
                        edits_p.set(json.dumps(edits))
            except Exception:
                pass

    def duplicate_light(self, prim_path):
        node = self._find_light_node(prim_path)
        if not node: return None
        hda = self._get_hda()
        parent = node.parent()
        if not parent: return None

        dup = hou.copyNodesTo([node], parent)[0]
        # Clear connections that were copied over to prevent cyclic wires
        for i in range(len(dup.inputs())):
            dup.setInput(i, None)

        safe_name = dup.name()

        # Update primpath parameter to match duplicate node's new name
        p_prim = dup.parm("primpath") or _get_lop_parm(dup, "primpath")
        if p_prim:
            old_val = p_prim.evalAsString()
            if old_val:
                parts = old_val.rstrip("/").split("/")
                dir_path = "/".join(parts[:-1])
                new_primpath = f"{dir_path}/{safe_name}" if dir_path else f"/lights/{safe_name}"
            else:
                new_primpath = f"/lights/practicals/{safe_name}"
            try:
                p_prim.set(new_primpath)
            except Exception:
                pass
        else:
            new_primpath = f"/lights/practicals/{safe_name}"

        # Update AOV / Light Group tags
        for p in dup.parms():
            cname = p.name().lower()
            if "control" in cname: continue
            is_tag = ("karmalightlightgroup" in cname or "arnoldaov" in cname or
                      "redshiftlight_group" in cname or "redshiftlightgroup" in cname or
                      "lpetag" in cname or "aov" in cname or "lightgroup" in cname or "light_group" in cname)
            if is_tag and p.parmTemplate().type() == hou.parmTemplateType.String:
                try: p.set(safe_name)
                except: pass

        # Slightly offset translation so the duplicate doesn't sit exactly on top of original
        t_p = dup.parmTuple("t")
        if t_p:
            try:
                t_vals = list(t_p.eval())
                t_vals[0] += 0.5
                t_p.set(t_vals)
            except Exception:
                pass

        # Position node cleanly in the network editor
        try:
            pos = node.position()
            dup.setPosition(hou.Vector2(pos[0], pos[1] - 1.0))
        except Exception:
            pass

        if hda and parent == hda:
            self._wire_lights(hda)
        else:
            # Wire duplicate directly downstream of the original node in /stage
            outs = list(node.outputs())
            dup.setInput(0, node)
            for out_node in outs:
                for idx, inp in enumerate(out_node.inputs()):
                    if inp == node:
                        out_node.setInput(idx, dup)

        return new_primpath

    def keyframe_light(self, prim_path):
        node = self._find_light_node(prim_path)
        if not node: return
        
        cur_frame = hou.frame() if hou else 1.0
        for param_name in ["exposure", "color", "intensity"]:
            p = _get_lop_parm(node, param_name)
            if p:
                try:
                    # Get the tuple so we keyframe all components (e.g. R, G, B)
                    ptup = p.tuple()
                    for sub_p in ptup:
                        k = hou.Keyframe()
                        k.setFrame(cur_frame)
                        k.setValue(sub_p.eval())
                        sub_p.setKeyframe(k)
                    
                    # Force USD to author this keyframed parameter
                    _enable_lop_control(node, p, enable=True)
                except: pass

    def set_light_param(self, prim_path, param_name, value):
        node = self._find_light_node(prim_path)
        if node:
            if param_name == "name":
                import re
                safe_name = re.sub(r'[^a-zA-Z0-9_]', '_', str(value))
                node.setName(safe_name, unique_name=True)
                final_name = node.name()
                for p in node.parms():
                    cname = p.name().lower()
                    desc = p.description().lower()
                    if "control" in cname: continue
                    is_tag = ("lpetag" in cname or "lpe tag" in desc or
                              "aov" in cname or "aov light group" in desc or
                              "lightgroup" in cname or "light group" in desc)
                    if is_tag and p.parmTemplate().type() == hou.parmTemplateType.String:
                        try: p.set(final_name)
                        except: pass
                        _enable_lop_control(node, p, enable=True)
                return

            elif param_name == "type":
                p = _get_lop_parm(node, "lighttype")
                if p:
                    tmpl = p.parmTemplate()
                    if hasattr(tmpl, "menuItems"):
                        items = tmpl.menuItems()
                        labels = tmpl.menuLabels()
                        target_idx = 0
                        for i, lbl in enumerate(labels):
                            lbl_low = lbl.lower()
                            val_low = str(value).lower()
                            if "dome" in val_low and ("dome" in lbl_low or "env" in lbl_low): target_idx = i
                            elif "distant" in val_low and ("distant" in lbl_low or "sun" in lbl_low): target_idx = i
                            elif "rect" in val_low and ("rect" in lbl_low or "grid" in lbl_low): target_idx = i
                            elif "disk" in val_low and "disk" in lbl_low: target_idx = i
                            elif "sphere" in val_low and "sphere" in lbl_low: target_idx = i
                            elif "cylinder" in val_low and "cylinder" in lbl_low: target_idx = i
                        try: p.set(items[target_idx])
                        except: pass
                return

            elif param_name == "enable":
                node.bypass(not bool(value))
                return

            elif param_name == "translate":
                t_p = node.parmTuple("t")
                if t_p and isinstance(value, (list, tuple)) and len(value) >= 3:
                    try: t_p.set([float(v) for v in value[:3]])
                    except: pass
                return

            elif param_name == "rotate":
                r_p = node.parmTuple("r")
                if r_p and isinstance(value, (list, tuple)) and len(value) >= 3:
                    try: r_p.set([float(v) for v in value[:3]])
                    except: pass
                return

            elif param_name == "width":
                w_p = _get_lop_parm(node, "width")
                if w_p:
                    try:
                        w_p.set(float(value))
                        _enable_lop_control(node, w_p, True)
                    except: pass
                return

            elif param_name == "height":
                h_p = _get_lop_parm(node, "height")
                if h_p:
                    try:
                        h_p.set(float(value))
                        _enable_lop_control(node, h_p, True)
                    except: pass
                return

            elif param_name == "radius":
                rad_p = _get_lop_parm(node, "radius")
                if rad_p:
                    try:
                        rad_p.set(float(value))
                        _enable_lop_control(node, rad_p, True)
                    except: pass
                return

            elif param_name == "exposure":
                val = float(value)
                set_ok = False
                for p in node.parms():
                    pname = p.name().lower()
                    if "exposure" in pname and "control" not in pname and "redshift" not in pname:
                        if p.parmTemplate().type() == hou.parmTemplateType.Float:
                            p.set(val)
                            _enable_lop_control(node, p, True)
                            set_ok = True
                            break
                if not set_ok:
                    for p in node.parms():
                        if "exposure" in p.name().lower() and "control" not in p.name().lower():
                            if p.parmTemplate().type() == hou.parmTemplateType.Float:
                                p.set(val)
                                _enable_lop_control(node, p, True)
                                break

            elif param_name == "intensity":
                val = float(value)
                set_ok = False
                for p in node.parms():
                    pname = p.name().lower()
                    if ("inputs:intensity" in pname or "inputsintensity" in pname or pname == "intensity") and "control" not in pname:
                        if p.parmTemplate().type() == hou.parmTemplateType.Float:
                            p.set(val)
                            _enable_lop_control(node, p, True)
                            set_ok = True
                            break
                if not set_ok:
                    for p in node.parms():
                        pname = p.name().lower()
                        if "intensity" in pname and "control" not in pname and "redshift" not in pname:
                            if p.parmTemplate().type() == hou.parmTemplateType.Float:
                                p.set(val)
                                _enable_lop_control(node, p, True)
                                break

            elif param_name == "color":
                if isinstance(value, (list, tuple)) and len(value) >= 3:
                    r, g, b = float(value[0]), float(value[1]), float(value[2])
                else:
                    r, g, b = 1.0, 1.0, 1.0
                set_ok = False
                for pt in node.parmTuples():
                    ptname = pt.name().lower()
                    if "color" in ptname and not any(x in ptname for x in ["temperature", "space", "control", "shadow"]):
                        if len(pt) >= 3:
                            pt.set((r, g, b))
                            _enable_lop_control(node, pt[0], True)
                            set_ok = True
                            break
                if not set_ok:
                    for ch, v in [("r", r), ("g", g), ("b", b)]:
                        for p in node.parms():
                            pname = p.name().lower()
                            if "color" in pname and pname.endswith(ch) and "control" not in pname and "shadow" not in pname:
                                try:
                                    p.set(v)
                                    _enable_lop_control(node, p, True)
                                except Exception: pass

            elif param_name in ("temperature", "colortemperature"):
                val = float(value)
                for p in node.parms():
                    pname = p.name().lower()
                    if "colortemperature" in pname and "enable" not in pname and "control" not in pname:
                        if p.parmTemplate().type() == hou.parmTemplateType.Float:
                            p.set(val)
                            _enable_lop_control(node, p, True)
                            break

            elif param_name == "use_temperature":
                bval = int(bool(value))
                for p in node.parms():
                    pname = p.name().lower()
                    if "enablecolortemperature" in pname and "control" not in pname:
                        p.set(bval)
                        _enable_lop_control(node, p, True)
                        break

            elif param_name in ("texture", "texture_path", "gobo_path"):
                tex_path = str(value) if value else ""
                for p in node.parms():
                    pname = p.name().lower()
                    if ("texturefile" in pname or "texture:file" in pname or (pname.endswith("texture") and "control" not in pname)):
                        if "control" not in pname and p.parmTemplate().type() == hou.parmTemplateType.String:
                            p.set(tex_path)
                            _enable_lop_control(node, p, bool(tex_path))
                            break

            elif param_name == "diffuse":
                val = float(value)
                for p in node.parms():
                    pname = p.name().lower()
                    if "diffuse" in pname and "control" not in pname and p.parmTemplate().type() == hou.parmTemplateType.Float:
                        p.set(val)
                        _enable_lop_control(node, p, True)

            elif param_name == "specular":
                val = float(value)
                for p in node.parms():
                    pname = p.name().lower()
                    if "specular" in pname and "control" not in pname and p.parmTemplate().type() == hou.parmTemplateType.Float:
                        p.set(val)
                        _enable_lop_control(node, p, True)

            elif param_name == "sss":
                val = float(value)
                for p in node.parms():
                    pname = p.name().lower()
                    if ("sss" in pname or "sssscale" in pname) and "control" not in pname:
                        if p.parmTemplate().type() == hou.parmTemplateType.Float:
                            p.set(val)
                            _enable_lop_control(node, p, True)

            elif param_name == "volume":
                val = float(value)
                # Strictly target float volumeScale / inputs:volume, ignore int volumeSamples
                for p in node.parms():
                    pname = p.name().lower()
                    if "sample" in pname or "night" in pname:
                        continue
                    if ("volumescale" in pname or "volume" in pname) and "control" not in pname:
                        if p.parmTemplate().type() == hou.parmTemplateType.Float:
                            p.set(val)
                            _enable_lop_control(node, p, True)
                            break

            elif param_name == "fx_mode":
                mode = str(value).lower()
                node.setUserData("fx_mode", mode)
                int_parm = None
                for p in node.parms():
                    pname = p.name().lower()
                    if ("inputs:intensity" in pname or "inputsintensity" in pname or pname == "intensity") and "control" not in pname:
                        if p.parmTemplate().type() == hou.parmTemplateType.Float:
                            int_parm = p
                            break
                if not int_parm:
                    for p in node.parms():
                        pname = p.name().lower()
                        if "intensity" in pname and "control" not in pname and "redshift" not in pname:
                            if p.parmTemplate().type() == hou.parmTemplateType.Float:
                                int_parm = p
                                break
                if int_parm:
                    if mode == "none":
                        base_val = node.userData("fx_base_intensity")
                        if base_val is not None:
                            try:
                                int_parm.deleteAllKeyframes()
                                int_parm.set(float(base_val))
                            except: pass
                    else:
                        if node.userData("fx_base_intensity") is None:
                            curr_val = int_parm.eval()
                            if curr_val <= 0: curr_val = 1.0
                            node.setUserData("fx_base_intensity", str(curr_val))
                        base = float(node.userData("fx_base_intensity") or 1.0)
                        if base <= 0: base = 1.0
                        if mode == "fire":
                            expr = f"{base} * fit01(noise($T * 7.5, 0.5), 0.6, 1.4)"
                        elif mode == "neon":
                            expr = f"{base} * if(noise($T * 25.0) > 0.85, 0.25, 1.0)"
                        elif mode == "tv":
                            expr = f"{base} * fit01(noise($T * 1.8, 12.3), 0.65, 1.35)"
                        elif mode == "strobe":
                            expr = f"{base} * if(int($F) % 8 < 2, 2.5, 0.0)"
                        else:
                            expr = f"{base}"
                        int_parm.setExpression(expr)

        # ALSO update light_edits_json on HDA for backup and persistence
        hda = self._get_hda()
        if hda:
            try:
                edits_json = hda.evalParm("light_edits_json") or "{}"
                edits = json.loads(edits_json)
                if prim_path not in edits: edits[prim_path] = {}
                if param_name == "color" and isinstance(value, (list, tuple)):
                    val = [float(v) for v in value]
                else:
                    val = value
                edits[prim_path][param_name] = val
                hda.parm("light_edits_json").set(json.dumps(edits))
            except Exception:
                pass

    def set_light_solo(self, prim_path, is_solo):
        target_node = hou.node(prim_path)
        if not target_node: return
        hda = target_node.parent()
        if not hda: return
        
        if is_solo:
            self._solo_backup = {}
            for n in hda.children():
                if self._is_light_node(n):
                    if n != target_node:
                        self._solo_backup[n.path()] = not n.isBypassed()
                        n.bypass(True)
        else:
            for path, was_enabled in self._solo_backup.items():
                n = hou.node(path)
                if n:
                    n.bypass(not was_enabled)
            self._solo_backup.clear()

    def select_node(self, prim_path):
        """Select a light node in the Houdini network editor."""
        if not hou: return
        node = self._find_light_node(prim_path)
        if node:
            node.setSelected(True, clear_all_selected=True)

    def set_rig_state(self, rig_type, is_active):
        pass

    def select_lights(self, prim_paths):
        """Select multiple light nodes in Houdini simultaneously."""
        if not hou: return
        first = True
        for p in prim_paths:
            node = self._find_light_node(p)
            if node:
                node.setSelected(True, clear_all_selected=first)
                first = False

    def select_light(self, prim_path):
        self.select_lights([prim_path])

    def test_connection(self):
        return hou is not None
        
    # --- Missing Abstract Methods (Stubs / Implementations) ---
    def delete_light(self, light_id):
        self.remove_light(light_id)
        
    def get_light_param(self, light_id, param_name):
        return None
        
    def set_light_enabled(self, light_id, enabled):
        self.set_light_param(light_id, "enable", enabled)
        
    def apply_texture(self, light_id, texture_path):
        self.set_light_param(light_id, "texture", texture_path)
        
    def clear_texture(self, light_id):
        self.set_light_param(light_id, "texture", "")
        
    def get_light_links(self, light_id):
        """Return dict of {object_path: linked_bool} for a light.
        Discovers geometry prims from the stage and checks USD light linking collections."""
        result = {}
        try:
            hda = self._get_hda()
            if not hda:
                return result
            stage = hda.stage()
            if not stage:
                return result
            from pxr import UsdGeom
            # Gather all mesh/xform geometry paths on the stage
            for prim in stage.Traverse():
                if prim.IsA(UsdGeom.Mesh) or prim.IsA(UsdGeom.Scope):
                    result[str(prim.GetPath())] = True
        except Exception as e:
            logger.debug("get_light_links failed: %s", e)
        return result
        
    def set_light_link(self, light_id, object_id, linked):
        """Set light linking via USD collections. Stub — full implementation requires
        a lightlinker LOP which is managed separately in production."""
        logger.info("set_light_link: %s -> %s = %s", light_id, object_id, linked)
        
    def add_fog(self, density=0.1, color=(0.5, 0.5, 0.5)):
        """Create a fog/atmosphere volume appropriate for the active renderer."""
        hda = self._get_hda()
        if not hda:
            return
        hda.allowEditingOfContents()
        renderer = self._active_renderer
        
        # Check if fog node already exists
        existing = hda.node("crucible_fog")
        if existing:
            existing.destroy()
        
        script = (
            'import hou\n'
            'node = hou.pwd()\n'
            'hda = node.parent()\n'
            'p_en = hda.parm("enable_fog")\n'
            'if p_en and p_en.eval() == 0:\n'
            '    pass # Wait, if we return, nothing is created (correct)\n'
            'else:\n'
            '    stage = node.editableStage()\n'
            '    if stage:\n'
            '        from pxr import Usd, UsdGeom, UsdShade, Sdf, Gf\n'
            '        p_d = hda.parm("fog_density"); raw_d = p_d.eval() if p_d else 0.1\n'
            '        p_c = hda.parmTuple("fog_color"); c = p_c.eval() if p_c else (0.5, 0.5, 0.5)\n'
            '\n'
            '        # Compute scene bounding box to dynamically encapsulate the fog\n'
            '        bbox_cache = UsdGeom.BBoxCache(Usd.TimeCode.Default(), [UsdGeom.Tokens.default_])\n'
            '        bounds = bbox_cache.ComputeWorldBound(stage.GetPseudoRoot())\n'
            '        b_range = bounds.GetRange()\n'
            '        if not b_range.IsEmpty():\n'
            '            min_pt = b_range.GetMin()\n'
            '            max_pt = b_range.GetMax()\n'
            '            size = max_pt - min_pt\n'
            '            center = (min_pt + max_pt) / 2.0\n'
            '            scale = max(size[0], max(size[1], size[2])) * 1.5\n'
            '            if scale < 10.0: scale = 10.0\n'
            '        else:\n'
            '            center = Gf.Vec3d(0,0,0)\n'
            '            scale = 1000.0\n'
            '\n'
            '        # Normalize density based on scene scale so it never goes pitch black\n'
            '        d = raw_d / scale\n'
            '\n'
            '        # Create Fog Box\n'
            '        xf = UsdGeom.Xform.Define(stage, "/CrucibleLightStudio/Atmosphere")\n'
            '        vol = UsdGeom.Cube.Define(stage, "/CrucibleLightStudio/Atmosphere/fog_box")\n'
            '        vol.GetSizeAttr().Set(scale)\n'
            '        vol.AddTranslateOp().Set(center)\n'
            '        mat = UsdShade.Material.Define(stage, "/CrucibleLightStudio/Atmosphere/fog_mtl")\n'
            '        sh = UsdShade.Shader.Define(stage, "/CrucibleLightStudio/Atmosphere/fog_mtl/volume")\n'
        )

        if renderer == "karma":
            script += (
                '        sh.CreateIdAttr("kma_principled_volume")\n'
                '        sh.CreateInput("density", Sdf.ValueTypeNames.Float).Set(d)\n'
                '        sh.CreateInput("scatterColor", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(*c))\n'
                '        mat.CreateVolumeOutput().ConnectToSource(sh.ConnectableAPI(), "volume")\n'
            )
        elif renderer == "arnold":
            script += (
                '        sh.CreateIdAttr("arnold:standard_volume")\n'
                '        sh.CreateInput("density", Sdf.ValueTypeNames.Float).Set(d)\n'
                '        sh.CreateInput("scatter", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(*c))\n'
                '        mat.CreateVolumeOutput("arnold").ConnectToSource(sh.ConnectableAPI(), "out")\n'
                '        # Arnold REQUIRES a step_size attribute on meshes to render them as volumes\n'
                '        vol.GetPrim().CreateAttribute("arnold:step_size", Sdf.ValueTypeNames.Float).Set(scale * 0.01)\n'
            )
        elif renderer == "redshift":
            script += (
                '        sh.CreateIdAttr("redshift:Volume")\n'
                '        sh.CreateInput("scatter", Sdf.ValueTypeNames.Float).Set(d)\n'
                '        sh.CreateInput("tint", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(*c))\n'
                '        mat.CreateVolumeOutput("redshift").ConnectToSource(sh.ConnectableAPI(), "outColor")\n'
            )
        else:
            script += (
                '        # Fallback to generic preview (not a true volume)\n'
                '        sh.CreateIdAttr("UsdPreviewSurface")\n'
                '        sh.CreateInput("opacity", Sdf.ValueTypeNames.Float).Set(d)\n'
                '        sh.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(*c))\n'
                '        mat.CreateSurfaceOutput().ConnectToSource(sh.ConnectableAPI(), "surface")\n'
            )

        script += (
            '        UsdShade.MaterialBindingAPI.Apply(vol.GetPrim()).Bind(mat)\n'
        )
        
        fog_node = hda.createNode("pythonscript", "crucible_fog")
        fog_node.parm("python").set(script)
        
        self._wire_lights(hda)
        logger.info("Added %s fog", renderer)
        
    def remove_fog(self):
        """Remove the Crucible-injected fog node."""
        hda = self._get_hda()
        if not hda:
            return
        hda.allowEditingOfContents()
        existing = hda.node("crucible_fog")
        if existing:
            existing.destroy()
            self._wire_lights(hda)
        logger.info("Removed fog")
        

        
    def add_lens_effects(self, bloom=False, glare=False, vignette=False):
        """Add post-process lens effects via render settings for the active renderer."""
        hda = self._get_hda()
        if not hda:
            return
        hda.allowEditingOfContents()
        renderer = self._active_renderer
        
        # Remove existing lens fx node if present
        existing = hda.node("crucible_lens_fx")
        if existing:
            existing.destroy()
        
        if renderer == "karma":
            # Karma lens effects via karmarenderproperties
            lens_node = hda.createNode("karmarenderproperties", "crucible_lens_fx")
            if bloom:
                p = lens_node.parm("bloom_enable")
                if p: p.set(1)
                p = lens_node.parm("bloom_intensity")
                if p: p.set(0.5)
            if glare:
                p = lens_node.parm("glare_enable") 
                if p: p.set(1)
        elif renderer == "arnold":
            # Arnold post-processing via imager nodes
            lens_node = hda.createNode("pythonscript", "crucible_lens_fx")
            script = (
                'node = hou.pwd()\n'
                'stage = node.editableStage()\n'
                'if stage:\n'
                '    from pxr import Sdf\n'
                '    rp = "/Render/Products/renderproduct"\n'
                '    prim = stage.OverridePrim(rp)\n'
            )
            if bloom:
                script += '    prim.CreateAttribute("arnold:imager:bloom:enable", Sdf.ValueTypeNames.Bool).Set(True)\n'
            lens_node.parm("python").set(script)
        elif renderer == "redshift":
            # Redshift post-processing
            lens_node = hda.createNode("pythonscript", "crucible_lens_fx")
            script = (
                'node = hou.pwd()\n'
                'stage = node.editableStage()\n'
                'if stage:\n'
                '    from pxr import Sdf\n'
                '    rp = "/Render/Products/renderproduct"\n'
                '    prim = stage.OverridePrim(rp)\n'
            )
            if bloom:
                script += '    prim.CreateAttribute("redshift:post:bloom:enable", Sdf.ValueTypeNames.Bool).Set(True)\n'
            lens_node.parm("python").set(script)
        else:
            lens_node = hda.createNode("pythonscript", "crucible_lens_fx")
        
        self._wire_lights(hda)
        logger.info("Added %s lens effects (bloom=%s, glare=%s)", renderer, bloom, glare)
        
    def get_main_window(self):
        if hou: return hou.qt.mainWindow()
        return None

    # ------------------------------------------------------------------
    # Undo
    # ------------------------------------------------------------------
    def begin_undo(self, name):
        if hou: hou.undos.addGroup(name)
        
    def end_undo(self):
        pass
