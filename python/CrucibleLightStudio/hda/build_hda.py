import hou
import os

def build_crucible_light_studio_hda():
    hda_dir = os.path.dirname(os.path.abspath(__file__))
    hda_file = os.path.join(hda_dir, "crucible_light_studio.hda")
    
    update_mode = False
    if os.path.exists(hda_file):
        print(f"HDA already exists at {hda_file}. Attempting to remove...")
        try:
            os.remove(hda_file)
        except Exception as e:
            print(f"Failed to remove existing HDA (likely locked by active Houdini session): {e}")
            print("Switching to Update-In-Place mode...")
            update_mode = True
            
    if not update_mode:
        # Create a temporary LOP network to build the subnet
        stage = hou.node("/stage")
        subnet = stage.createNode("subnet", "crucible_light_studio_temp")
        
        # Setup internal nodes
        input_node = subnet.indirectInputs()[0]
    
    # 1. Light Engine (Python LOP)
    light_engine = subnet.createNode("pythonscript", "light_engine")
    light_engine.setInput(0, input_node)
    
    # 2. Light Linking Engine (Python LOP)
    linking_engine = subnet.createNode("pythonscript", "linking_engine")
    linking_engine.setInput(0, light_engine)
    
    # 4. Fog Engine (Python LOP)
    fog_engine = subnet.createNode("pythonscript", "fog_engine")
    fog_engine.setInput(0, linking_engine)
    
    # Output node
    output_node = subnet.createNode("output", "output0")
    output_node.setInput(0, fog_engine)
    
    # Layout
    subnet.layoutChildren()
    
    # Set the Python scripts for each node (we'll fetch these from external files later, or embed them)
    # For now, we will add stub code that will read from the HDA's PythonModule.
    
    light_engine_script = """
node = hou.pwd()
hda = node.parent()
# Force dependency on all HDA parameters
_ = [p.eval() for p in hda.parms()]
if hasattr(hda.hdaModule(), 'run_light_engine'):
    hda.hdaModule().run_light_engine(node)
"""
    light_engine.parm("python").set(light_engine_script)
    
    linking_engine_script = """
node = hou.pwd()
hda = node.parent()
_ = [p.eval() for p in hda.parms()]
if hasattr(hda.hdaModule(), 'run_linking_engine'):
    hda.hdaModule().run_linking_engine(node)
"""
    linking_engine.parm("python").set(linking_engine_script)
    
    fog_engine_script = """
node = hou.pwd()
hda = node.parent()
_ = [p.eval() for p in hda.parms()]
if hasattr(hda.hdaModule(), 'run_fog_engine'):
    hda.hdaModule().run_fog_engine(node)
"""
    fog_engine.parm("python").set(fog_engine_script)

    hda_name = "crucible_light_studio"
    hda_label = "Crucible Light Studio"
    
    if not update_mode:
        # Convert subnet to digital asset
        hda_node = subnet.createDigitalAsset(
            name=hda_name,
            hda_file_name=hda_file,
            description=hda_label,
            min_num_inputs=1,
            max_num_inputs=1,
            compress_contents=True
        )
        hda_def = hda_node.type().definition()
    else:
        # Fetch the existing definition loaded in memory
        node_type = hou.nodeType(hou.lopNodeTypeCategory(), hda_name)
        if not node_type:
            print("ERROR: HDA is not loaded in current session. Cannot update in place.")
            return
        hda_def = node_type.definition()
        hda_node = None
    
    # Setup Parameters
    ptg = hou.ParmTemplateGroup()
    
    # Global Settings Tab
    tab_global = hou.FolderParmTemplate("tab_global", "Global")
    tab_global.addParmTemplate(hou.IntParmTemplate("active_light", "Active Light Index", 1, default_value=(1,)))
    tab_global.addParmTemplate(hou.StringParmTemplate("dcc_renderer", "Renderer", 1, default_value=("karma",), menu_items=("karma", "arnold", "redshift"), menu_labels=("Karma", "Arnold", "Redshift")))
    tab_global.addParmTemplate(hou.StringParmTemplate("light_linking_json", "Light Linking JSON", 1, default_value=("{}",)))
    tab_global.addParmTemplate(hou.StringParmTemplate("light_edits_json", "Light Edits JSON", 1, default_value=("{}",)))
    # hide the JSON parm in production, keep visible for debug
    # ptg.hideFolder("Global") # Could hide it, but let's keep it in a tab for now
    ptg.append(tab_global)
    
    # Lights MultiParm
    lights_folder = hou.FolderParmTemplate("lights_folder", "Lights", folder_type=hou.folderType.MultiparmBlock)
    lights_folder.addParmTemplate(hou.ToggleParmTemplate("light#_enable", "Enable", default_value=True))
    lights_folder.addParmTemplate(hou.ToggleParmTemplate("light#_solo", "Solo", default_value=False))
    lights_folder.addParmTemplate(hou.StringParmTemplate("light#_name", "Name", 1, default_value=("light#",)))
    lights_folder.addParmTemplate(hou.StringParmTemplate("light#_type", "Type", 1, default_value=("RectLight",), 
                                                         menu_items=("RectLight", "DiskLight", "SphereLight", "CylinderLight", "DistantLight", "DomeLight"),
                                                         menu_labels=("Rect Light", "Disk Light", "Sphere Light", "Cylinder Light", "Distant Light", "Dome Light")))
    lights_folder.addParmTemplate(hou.FloatParmTemplate("light#_exposure", "Exposure", 1, default_value=(0.0,)))
    lights_folder.addParmTemplate(hou.FloatParmTemplate("light#_intensity", "Intensity", 1, default_value=(1.0,)))
    lights_folder.addParmTemplate(hou.FloatParmTemplate("light#_color", "Color", 3, default_value=(1.0, 1.0, 1.0), naming_scheme=hou.parmNamingScheme.RGBA, look=hou.parmLook.ColorSquare))
    lights_folder.addParmTemplate(hou.ToggleParmTemplate("light#_use_temp", "Use Temperature", default_value=False))
    lights_folder.addParmTemplate(hou.FloatParmTemplate("light#_temp", "Temperature", 1, default_value=(6500.0,)))
    lights_folder.addParmTemplate(hou.StringParmTemplate("light#_texture", "Texture", 1, default_value=("",), string_type=hou.stringParmType.FileReference))
    lights_folder.addParmTemplate(hou.FloatParmTemplate("light#_diffuse", "Diffuse", 1, default_value=(1.0,)))
    lights_folder.addParmTemplate(hou.FloatParmTemplate("light#_specular", "Specular", 1, default_value=(1.0,)))
    lights_folder.addParmTemplate(hou.FloatParmTemplate("light#_width", "Width", 1, default_value=(2.0,)))
    lights_folder.addParmTemplate(hou.FloatParmTemplate("light#_height", "Height", 1, default_value=(2.0,)))
    lights_folder.addParmTemplate(hou.FloatParmTemplate("light#_tx", "Translate X", 1, default_value=(0.0,)))
    lights_folder.addParmTemplate(hou.FloatParmTemplate("light#_ty", "Translate Y", 1, default_value=(0.0,)))
    lights_folder.addParmTemplate(hou.FloatParmTemplate("light#_tz", "Translate Z", 1, default_value=(0.0,)))
    lights_folder.addParmTemplate(hou.FloatParmTemplate("light#_rx", "Rotate X", 1, default_value=(0.0,)))
    lights_folder.addParmTemplate(hou.FloatParmTemplate("light#_ry", "Rotate Y", 1, default_value=(0.0,)))
    lights_folder.addParmTemplate(hou.FloatParmTemplate("light#_rz", "Rotate Z", 1, default_value=(0.0,)))
    lights_folder.addParmTemplate(hou.StringParmTemplate("light#_fx", "FX Mode", 1, default_value=("none",),
                                                         menu_items=("none", "fire", "neon", "tv", "strobe"),
                                                         menu_labels=("None", "Fire Flicker", "Broken Neon", "TV Glow", "Strobe")))
    lights_folder.addParmTemplate(hou.StringParmTemplate("light#_lpetag", "LPE Tag", 1, default_value=("",)))
    
    ptg.append(lights_folder)
    

    
    # Fog Tab
    tab_fog = hou.FolderParmTemplate("tab_fog", "Atmosphere")
    tab_fog.addParmTemplate(hou.ToggleParmTemplate("enable_fog", "Enable Fog", default_value=False))
    tab_fog.addParmTemplate(hou.FloatParmTemplate("fog_density", "Density", 1, default_value=(0.1,)))
    tab_fog.addParmTemplate(hou.FloatParmTemplate("fog_color", "Color", 3, default_value=(0.5, 0.5, 0.5), naming_scheme=hou.parmNamingScheme.RGBA, look=hou.parmLook.ColorSquare))
    tab_fog.addParmTemplate(hou.FloatParmTemplate("fog_distance", "Distance / Size", 1, default_value=(1000.0,)))
    tab_fog.addParmTemplate(hou.FloatParmTemplate("fog_height", "Height", 1, default_value=(100.0,)))
    tab_fog.addParmTemplate(hou.FloatParmTemplate("fog_falloff", "Height Falloff", 1, default_value=(0.1,)))
    ptg.append(tab_fog)
    

    
    # Apply parameters
    hda_def.setParmTemplateGroup(ptg)
    
    # Set PythonModule (this is where our execution logic will live)
    python_module_code = """
import json
from pxr import Usd, UsdLux, UsdGeom, Sdf, Gf, UsdShade, UsdCollectionAPI

def run_light_engine(node):
    stage = node.editableStage()
    if not stage:
        return
        
    hda = node.parent()
    num_lights = hda.evalParm("lights_folder")
    renderer = hda.evalParm("dcc_renderer")
    
    # Gather solo state
    any_solo = False
    for i in range(1, num_lights + 1):
        if hda.evalParm(f"light{i}_solo"):
            any_solo = True
            break

    # We will put lights in a common root to keep it organized
    lights_root = "/CrucibleLightStudio/Lights"
    if not stage.GetPrimAtPath("/CrucibleLightStudio"):
        UsdGeom.Xform.Define(stage, "/CrucibleLightStudio")
    if not stage.GetPrimAtPath(lights_root):
        UsdGeom.Xform.Define(stage, lights_root)
        
    for i in range(1, num_lights + 1):
        name = hda.evalParm(f"light{i}_name").replace(" ", "_")
        if not name:
            name = f"light_{i}"
            
        light_path = f"{lights_root}/{name}"
        l_type = hda.evalParm(f"light{i}_type")
        
        # Define the light prim
        if l_type == "RectLight":
            light_prim = UsdLux.RectLight.Define(stage, light_path)
            light_prim.GetWidthAttr().Set(hda.evalParm(f"light{i}_width"))
            light_prim.GetHeightAttr().Set(hda.evalParm(f"light{i}_height"))
        elif l_type == "DiskLight":
            light_prim = UsdLux.DiskLight.Define(stage, light_path)
            # Disk light uses radius, we can average width/height or use width
            light_prim.GetRadiusAttr().Set(hda.evalParm(f"light{i}_width") / 2.0)
        elif l_type == "SphereLight":
            light_prim = UsdLux.SphereLight.Define(stage, light_path)
            light_prim.GetRadiusAttr().Set(hda.evalParm(f"light{i}_width") / 2.0)
        elif l_type == "CylinderLight":
            light_prim = UsdLux.CylinderLight.Define(stage, light_path)
            light_prim.GetRadiusAttr().Set(hda.evalParm(f"light{i}_width") / 2.0)
            light_prim.GetLengthAttr().Set(hda.evalParm(f"light{i}_height"))
        elif l_type == "DistantLight":
            light_prim = UsdLux.DistantLight.Define(stage, light_path)
        elif l_type == "DomeLight":
            light_prim = UsdLux.DomeLight.Define(stage, light_path)
        else:
            light_prim = UsdLux.RectLight.Define(stage, light_path)
            
        # Common attributes
        light_api = UsdLux.LightAPI(light_prim.GetPrim()) if hasattr(UsdLux, 'LightAPI') else light_prim
        
        # Visibility (Enable + Solo logic)
        is_enabled = hda.evalParm(f"light{i}_enable")
        is_solo = hda.evalParm(f"light{i}_solo")
        
        visible = is_enabled
        if any_solo and not is_solo:
            visible = False
            
        vis_attr = UsdGeom.Imageable(light_prim.GetPrim()).CreateVisibilityAttr()
        vis_attr.Set(UsdGeom.Tokens.inherited if visible else UsdGeom.Tokens.invisible)
        
        if hasattr(light_prim, 'CreateExposureAttr'):
            light_prim.CreateExposureAttr().Set(hda.evalParm(f"light{i}_exposure"))
            
            # --- FX Engine ---
            intensity = hda.evalParm(f"light{i}_intensity")
            fx_mode = hda.evalParm(f"light{i}_fx")
            if fx_mode != "none":
                import math
                import random
                t = hou.frame() / max(1.0, hou.fps())
                if fx_mode == "fire":
                    intensity *= (math.sin(t * 15) * 0.5 + 0.5) * 0.7 + (math.sin(t * 7.3) * 0.5 + 0.5) * 0.3
                elif fx_mode == "neon":
                    # Random flickering, deterministic by frame
                    random.seed(int(hou.frame() + i * 100))
                    intensity *= 1.0 if random.random() > 0.05 else 0.1
                elif fx_mode == "tv":
                    intensity *= (math.sin(t * 2) * 0.2 + 0.8)
                elif fx_mode == "strobe":
                    intensity = intensity if (int(t * 10) % 2) == 0 else 0.0
                    
            light_prim.CreateIntensityAttr().Set(intensity)
            
            light_prim.CreateDiffuseAttr().Set(hda.evalParm(f"light{i}_diffuse"))
            light_prim.CreateSpecularAttr().Set(hda.evalParm(f"light{i}_specular"))
            
            color = hda.evalParmTuple(f"light{i}_color")
            light_prim.CreateColorAttr().Set(Gf.Vec3f(*color))
            
            light_prim.CreateEnableColorTemperatureAttr().Set(bool(hda.evalParm(f"light{i}_use_temp")))
            light_prim.CreateColorTemperatureAttr().Set(hda.evalParm(f"light{i}_temp"))
            
            # --- LPE Tag for Karma AOV ---
            lpe_tag = hda.evalParm(f"light{i}_lpetag")
            if not lpe_tag:
                lpe_tag = name
            light_prim.GetPrim().CreateAttribute("karma:light:lpetag", Sdf.ValueTypeNames.String).Set(lpe_tag)
        else:
            # Older USD versions
            light_api.CreateExposureAttr().Set(hda.evalParm(f"light{i}_exposure"))
            light_api.CreateIntensityAttr().Set(hda.evalParm(f"light{i}_intensity"))
            light_api.CreateDiffuseAttr().Set(hda.evalParm(f"light{i}_diffuse"))
            light_api.CreateSpecularAttr().Set(hda.evalParm(f"light{i}_specular"))
            color = hda.evalParmTuple(f"light{i}_color")
            light_api.CreateColorAttr().Set(Gf.Vec3f(*color))
            light_api.CreateEnableColorTemperatureAttr().Set(bool(hda.evalParm(f"light{i}_use_temp")))
            light_api.CreateColorTemperatureAttr().Set(hda.evalParm(f"light{i}_temp"))

        # Texture
        tex = hda.evalParm(f"light{i}_texture")
        if tex:
            # Handle texture paths
            tex_file = str(tex).replace('@', '')
            if hasattr(light_prim, 'CreateTextureFileAttr'):
                light_prim.CreateTextureFileAttr().Set(Sdf.AssetPath(tex_file))
            elif hasattr(light_api, 'CreateTextureFileAttr'):
                light_api.CreateTextureFileAttr().Set(Sdf.AssetPath(tex_file))
            else:
                light_prim.GetPrim().CreateAttribute("inputs:texture:file", Sdf.ValueTypeNames.Asset).Set(Sdf.AssetPath(tex_file))

        # Renderer specific attributes
        if renderer == "karma":
            # Karma natively uses UsdLux, no extra translation needed
            pass
        elif renderer == "arnold":
            # Example: Arnold-specific light translation
            light_prim.GetPrim().CreateAttribute("arnold:exposure", Sdf.ValueTypeNames.Float).Set(hda.evalParm(f"light{i}_exposure"))
        elif renderer == "redshift":
            # Example: Redshift-specific light translation
            light_prim.GetPrim().CreateAttribute("redshift:light:exposure", Sdf.ValueTypeNames.Float).Set(hda.evalParm(f"light{i}_exposure"))

        # Transforms
        tx = hda.evalParm(f"light{i}_tx")
        ty = hda.evalParm(f"light{i}_ty")
        tz = hda.evalParm(f"light{i}_tz")
        rx = hda.evalParm(f"light{i}_rx")
        ry = hda.evalParm(f"light{i}_ry")
        rz = hda.evalParm(f"light{i}_rz")
        
        xform = UsdGeom.Xformable(light_prim.GetPrim())
        xform.ClearXformOpOrder()
        xform.AddTranslateOp().Set(Gf.Vec3d(tx, ty, tz))
        xform.AddRotateXYZOp().Set(Gf.Vec3d(rx, ry, rz))

    # Apply Overrides to Existing Lights
    edits_json = hda.evalParm("light_edits_json")
    try:
        edits = json.loads(edits_json)
    except:
        edits = {}
        
    for prim_path, attrs in edits.items():
        prim = stage.GetPrimAtPath(prim_path)
        if not prim.IsValid():
            # If prim doesn't exist, we can create an override prim
            prim = stage.OverridePrim(prim_path)
            
        light_api = UsdLux.LightAPI(prim) if hasattr(UsdLux, 'LightAPI') else prim
        
        if "exposure" in attrs:
            if hasattr(prim, 'CreateExposureAttr'): prim.CreateExposureAttr().Set(attrs["exposure"])
            else: light_api.CreateExposureAttr().Set(attrs["exposure"])
            
        if "intensity" in attrs:
            if hasattr(prim, 'CreateIntensityAttr'): prim.CreateIntensityAttr().Set(attrs["intensity"])
            else: light_api.CreateIntensityAttr().Set(attrs["intensity"])
            
        if "color" in attrs:
            c = attrs["color"]
            if hasattr(prim, 'CreateColorAttr'): prim.CreateColorAttr().Set(Gf.Vec3f(c[0], c[1], c[2]))
            else: light_api.CreateColorAttr().Set(Gf.Vec3f(c[0], c[1], c[2]))
            
        if "texture" in attrs:
            tex_file = str(attrs["texture"]).replace('@', '')
            if hasattr(prim, 'CreateTextureFileAttr'):
                prim.CreateTextureFileAttr().Set(Sdf.AssetPath(tex_file))
            elif hasattr(light_api, 'CreateTextureFileAttr'):
                light_api.CreateTextureFileAttr().Set(Sdf.AssetPath(tex_file))
            else:
                prim.CreateAttribute("inputs:texture:file", Sdf.ValueTypeNames.Asset).Set(Sdf.AssetPath(tex_file))

def run_linking_engine(node):
    stage = node.editableStage()
    if not stage:
        return
        
    hda = node.parent()
    linking_json = hda.evalParm("light_linking_json")
    try:
        links = json.loads(linking_json)
    except:
        links = {}
        
    if not links:
        return
        
    for light_name, geo_paths in links.items():
        light_prim = None
        for p in stage.Traverse():
            if p.GetName() == light_name:
                light_prim = p
                break
                
        if not light_prim or not light_prim.IsValid():
            continue
            
        coll_api = UsdCollectionAPI.Apply(light_prim, "lightLink")
        
        # We explicitly list included paths and exclude root
        includes = coll_api.CreateIncludesRel()
        includes.ClearTargets()
        for path in geo_paths:
            if stage.GetPrimAtPath(path).IsValid():
                includes.AddTarget(Sdf.Path(path))
        
        coll_api.CreateIncludeRootAttr().Set(False)

def run_fog_engine(node):
    stage = node.editableStage()
    if not stage:
        return
    
    hda = node.parent()
    enable_fog = hda.evalParm("enable_fog")
    if not enable_fog:
        return
    
    density = hda.evalParm("fog_density")
    color = hda.evalParmTuple("fog_color")
    renderer = hda.evalParm("dcc_renderer")
    
    fog_root = "/CrucibleLightStudio/Atmosphere"
    fog_path = f"{fog_root}/fog_volume"
    
    if not stage.GetPrimAtPath("/CrucibleLightStudio"):
        UsdGeom.Xform.Define(stage, "/CrucibleLightStudio")
    
    # Compute scene bounding box
    bbox_cache = UsdGeom.BBoxCache(Usd.TimeCode.Default(), [UsdGeom.Tokens.default_])
    bounds = bbox_cache.ComputeWorldBound(stage.GetPseudoRoot())
    b_range = bounds.GetRange()
    if not b_range.IsEmpty():
        min_pt = b_range.GetMin()
        max_pt = b_range.GetMax()
        size = max_pt - min_pt
        center = (min_pt + max_pt) / 2.0
        scale = max(size[0], max(size[1], size[2])) * 1.5
        if scale < 10.0: scale = 10.0
    else:
        center = Gf.Vec3d(0,0,0)
        scale = 1000.0
    
    d = density / scale
    
    vol = UsdGeom.Cube.Define(stage, fog_path)
    vol.GetSizeAttr().Set(scale)
    vol.AddTranslateOp().Set(center)
    
    mat_path = f"{fog_root}/fog_mtl"
    mat = UsdShade.Material.Define(stage, mat_path)
    sh = UsdShade.Shader.Define(stage, f"{mat_path}/volume")
    
    if renderer == "karma":
        sh.CreateIdAttr("kma_principled_volume")
        sh.CreateInput("density", Sdf.ValueTypeNames.Float).Set(d)
        sh.CreateInput("scatterColor", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(*color))
        mat.CreateVolumeOutput().ConnectToSource(sh.ConnectableAPI(), "volume")
    elif renderer == "arnold":
        sh.CreateIdAttr("arnold:standard_volume")
        sh.CreateInput("density", Sdf.ValueTypeNames.Float).Set(d)
        sh.CreateInput("scatter", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(*color))
        mat.CreateVolumeOutput("arnold").ConnectToSource(sh.ConnectableAPI(), "out")
        vol.GetPrim().CreateAttribute("arnold:step_size", Sdf.ValueTypeNames.Float).Set(scale * 0.01)
    elif renderer == "redshift":
        sh.CreateIdAttr("redshift:Volume")
        sh.CreateInput("scatter", Sdf.ValueTypeNames.Float).Set(d)
        sh.CreateInput("tint", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(*color))
        mat.CreateVolumeOutput("redshift").ConnectToSource(sh.ConnectableAPI(), "outColor")
    else:
        sh.CreateIdAttr("UsdPreviewSurface")
        sh.CreateInput("opacity", Sdf.ValueTypeNames.Float).Set(d)
        sh.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(*color))
        mat.CreateSurfaceOutput().ConnectToSource(sh.ConnectableAPI(), "surface")
    
    UsdShade.MaterialBindingAPI.Apply(vol.GetPrim()).Bind(mat)
"""
    
    hda_def.sections()["PythonModule"].setContents(python_module_code)
    
    viewer_state_code = """
import hou

class LightPaintState(object):
    def __init__(self, state_name, scene_viewer):
        self.state_name = state_name
        self.scene_viewer = scene_viewer
        
        # HUD setup
        self.hud_template = hou.StateHUDTemplate("light_paint_hud")
        self.hud_template.addLabel("mode", "Mode (M):", "Reflection")
        self.hud_template.addLabel("active_light", "Active Light:", "None")
        self.hud_template.addLabel("distance", "Distance (Scroll):", "5.0")
        
        self.modes = ["Reflection", "Diffuse", "Rim"]
        self.mode_idx = 0
        self.active_light_path = ""
        self.active_light_name = ""
        self.distance = 5.0

    def onEnter(self, kwargs):
        node = kwargs["node"]
        self.scene_viewer.hudInfo(template=self.hud_template)
        self.update_hud(node)
        
    def update_hud(self, node):
        if not node: return
        values = {"mode": self.modes[self.mode_idx], "distance": str(round(self.distance, 1))}
        try:
            self.active_light_path = node.userData("active_light_path") or ""
            self.active_light_name = self.active_light_path.split("/")[-1] if self.active_light_path else "None"
            values["active_light"] = self.active_light_name
        except:
            values["active_light"] = "None"
        self.scene_viewer.hudInfo(values=values)

    def onMouseEvent(self, kwargs):
        ui_event = kwargs["ui_event"]
        device = ui_event.device()
        node = kwargs["node"]
        vp = self.scene_viewer.curViewport()
        
        if device.isMouseWheel():
            delta = 0.5 if ui_event.mouseWheel() > 0 else -0.5
            self.distance = max(0.5, self.distance + delta)
            self.update_hud(node)
            return True
            
        if device.isLeftButton() and device.isButtonPressed():
            origin, direction = ui_event.ray()
            
            hit_pos = None
            normal = None
            
            # Try getting snap info
            if "viewer_event" in kwargs:
                snap = kwargs["viewer_event"].snappingRay()
                if snap and snap.get("snapped"):
                    hit_pos = snap.get("origin_point")
                    # some versions of Houdini return normal in snappingRay if primitive snap is on
                    if "normal" in snap:
                        normal = snap.get("normal")
            
            if not hit_pos:
                # Approximate 10 units away
                hit_pos = origin + direction * 10.0
                
            cam_xform = vp.cameraTransform()
            cam_pos = hou.Vector3(cam_xform.extractTranslates())
            cam_vec = (cam_pos - hit_pos).normalized()
            
            if not normal:
                normal = cam_vec 
                
            mode = self.modes[self.mode_idx]
            light_pos = hou.Vector3()
            
            if mode == "Reflection":
                # L = reflect(-V, N)
                dot = normal.dot(cam_vec)
                light_vec = (normal * (2.0 * dot)) - cam_vec
                light_pos = hit_pos + light_vec.normalized() * self.distance
            elif mode == "Diffuse":
                light_pos = hit_pos + normal.normalized() * self.distance
            elif mode == "Rim":
                # Place light slightly offset behind the object
                rim_vec = (cam_vec * -1.0) + (hou.Vector3(0, 1, 0) * 0.2)
                light_pos = hit_pos + rim_vec.normalized() * self.distance
                
            # Look at hit_pos (Z-axis points backwards for lights in USD)
            z_axis = (light_pos - hit_pos).normalized()
            up = hou.Vector3(0, 1, 0)
            if abs(z_axis.dot(up)) > 0.99:
                up = hou.Vector3(1, 0, 0)
            x_axis = up.cross(z_axis).normalized()
            y_axis = z_axis.cross(x_axis).normalized()
            
            m3 = hou.Matrix3((x_axis[0], x_axis[1], x_axis[2],
                              y_axis[0], y_axis[1], y_axis[2],
                              z_axis[0], z_axis[1], z_axis[2]))
            rot = m3.extractRotates()
                
            # Refresh active light path in case it changed via UI
            try:
                self.active_light_path = node.userData("active_light_path") or ""
            except:
                pass

            target_node = hou.node(self.active_light_path)
            if target_node:
                # Explicitly enable LOP parameter controls so USD honors the changes!
                try:
                    for ctrl_name in ["xformOp_translate_control", "t_control"]:
                        c = target_node.parm(ctrl_name)
                        if c: c.set("set")
                    for ctrl_name in ["xformOp_rotateXYZ_control", "r_control"]:
                        c = target_node.parm(ctrl_name)
                        if c: c.set("set")
                except:
                    pass

                # Safely set parameters explicitly (avoiding parmTuple failures on some Houdini versions)
                try:
                    if target_node.parm("tx"): target_node.parm("tx").set(light_pos[0])
                    if target_node.parm("ty"): target_node.parm("ty").set(light_pos[1])
                    if target_node.parm("tz"): target_node.parm("tz").set(light_pos[2])
                    
                    if target_node.parm("rx"): target_node.parm("rx").set(rot[0])
                    if target_node.parm("ry"): target_node.parm("ry").set(rot[1])
                    if target_node.parm("rz"): target_node.parm("rz").set(rot[2])
                except:
                    pass
                
                # Flash HUD
                self.update_hud(node)
                self.scene_viewer.hudInfo(values={"active_light": f"Placed at {round(light_pos[0],1)}, {round(light_pos[1],1)}, {round(light_pos[2],1)}"})
                return True
            else:
                self.scene_viewer.hudInfo(values={"active_light": "Please select a light first!"})
                return False

    def onKeyEvent(self, kwargs):
        ui_event = kwargs["ui_event"]
        device = ui_event.device()
        if device.keyString() == "M" or device.keyString() == "m":
            self.mode_idx = (self.mode_idx + 1) % len(self.modes)
            self.update_hud(kwargs["node"])
            return True
        return False

def createViewerStateTemplate():
    state_typename = "crucible_light_paint"
    state_label = "Crucible Light Paint"
    state_cat = hou.lopNodeTypeCategory()

    template = hou.ViewerStateTemplate(state_typename, state_label, state_cat)
    template.bindFactory(LightPaintState)
    template.bindIcon("TOOLS_paint")
    return template
"""
    hda_def.sections()["ViewerStateModule"].setContents(viewer_state_code)
    hda_def.setExtraInfo("DefaultState=crucible_light_paint")
    
    # Save the updated definition (will overwrite the .hda file even if locked by this instance)
    hda_def.save(hda_def.libraryFilePath(), hda_node, hda_def.options())
    
    if not update_mode and hda_node:
        hda_node.destroy()
        
    print(f"Successfully {'updated' if update_mode else 'built'} HDA: {hda_file}")

if __name__ == "__main__":
    build_crucible_light_studio_hda()
