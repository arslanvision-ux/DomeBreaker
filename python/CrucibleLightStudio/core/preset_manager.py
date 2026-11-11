# -*- coding: utf-8 -*-
"""
Crucible Light Studio - Preset Manager
========================================
Manages JSON-based light presets, rig presets, and the asset library
directories (HDRIs, Gobos, Scrims). Supports audition-on-hover.
"""

import json
import logging
import os
import glob

logger = logging.getLogger("CrucibleLightStudio.presets")

# Default library paths — override via environment variables
_DEFAULT_ROOT = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                              "presets")

CONFIG_PATH = os.path.expanduser("~/.crucible_light_studio.json")

def _load_config():
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, 'r') as f:
                return json.load(f)
        except: pass
    return {}

def _save_config(cfg):
    try:
        with open(CONFIG_PATH, 'w') as f:
            json.dump(cfg, f)
    except: pass

_cfg = _load_config()

HDRI_DIR = os.environ.get("CRUCIBLE_HDRI_PATH",
                           _cfg.get("HDRI_DIR", os.path.join(_DEFAULT_ROOT, "hdris")))
GOBO_DIR = os.environ.get("CRUCIBLE_GOBO_PATH",
                           _cfg.get("GOBO_DIR", os.path.join(_DEFAULT_ROOT, "gobos")))
SCRIM_DIR = os.environ.get("CRUCIBLE_SCRIM_PATH",
                            _cfg.get("SCRIM_DIR", os.path.join(_DEFAULT_ROOT, "scrims")))
RIG_DIR = os.environ.get("CRUCIBLE_RIG_PATH",
                          os.path.join(_DEFAULT_ROOT, "light_rigs"))

def set_hdri_dir(path):
    global HDRI_DIR
    HDRI_DIR = path
    c = _load_config()
    c["HDRI_DIR"] = path
    _save_config(c)

def set_gobo_dir(path):
    global GOBO_DIR
    GOBO_DIR = path
    c = _load_config()
    c["GOBO_DIR"] = path
    _save_config(c)

def set_scrim_dir(path):
    global SCRIM_DIR
    SCRIM_DIR = path
    c = _load_config()
    c["SCRIM_DIR"] = path
    _save_config(c)

def set_rig_dir(path):
    global RIG_DIR
    RIG_DIR = path
    c = _load_config()
    c["RIG_DIR"] = path
    _save_config(c)

IMAGE_EXTS = {".exr", ".hdr", ".hdri", ".png", ".jpg", ".jpeg", ".tif",
              ".tiff", ".tx"}


def scan_directory(directory, extensions=None):
    """
    Scan a directory for files matching given extensions.

    Returns:
        List of dicts: [{"name": str, "path": str, "ext": str}, ...]
    """
    if extensions is None:
        extensions = IMAGE_EXTS

    results = []
    if not os.path.isdir(directory):
        logger.warning("Directory not found: %s", directory)
        return results

    for entry in sorted(os.listdir(directory)):
        ext = os.path.splitext(entry)[1].lower()
        if ext in extensions:
            results.append({
                "name": os.path.splitext(entry)[0],
                "path": os.path.join(directory, entry),
                "ext": ext,
            })

    return results


def get_hdri_contents(subpath=None):
    """
    Scan a directory for subfolders and HDRI files.
    Returns:
        List of dicts:
        - Folders: [{"name": str, "path": str, "ext": "", "is_folder": True}, ...]
        - Files:   [{"name": str, "path": str, "ext": str, "is_folder": False}, ...]
    """
    target_dir = HDRI_DIR
    if subpath:
        target_dir = subpath if os.path.isabs(subpath) else os.path.join(HDRI_DIR, subpath)

    if not os.path.isdir(target_dir):
        logger.warning("HDRI Directory not found: %s", target_dir)
        return []

    folders = []
    files = []
    extensions = {".exr", ".hdr", ".hdri", ".tx"}

    try:
        entries = sorted(os.listdir(target_dir), key=lambda x: x.lower())
    except Exception as e:
        logger.error("Error reading HDRI directory %s: %s", target_dir, e)
        return []

    for entry in entries:
        full_p = os.path.join(target_dir, entry)
        if os.path.isdir(full_p):
            if entry.startswith(".") or entry.startswith("__") or entry.lower() in ["thumbs", ".thumbs"]:
                continue
            folders.append({
                "name": entry,
                "path": full_p,
                "ext": "",
                "is_folder": True,
            })
        elif os.path.isfile(full_p):
            if entry.endswith(".thumb.jpg"):
                continue
            ext = os.path.splitext(entry)[1].lower()
            if ext in extensions:
                files.append({
                    "name": os.path.splitext(entry)[0],
                    "path": full_p,
                    "ext": ext,
                    "is_folder": False,
                })

    return folders + files


def get_hdri_folder_tree(root_dir=None, _depth=0, _max_depth=4):
    """
    Return a nested dict of all subfolders under root_dir (defaults to HDRI_DIR).
    Capped at _max_depth levels to prevent hangs on large studio network filesystems.
    """
    if root_dir is None:
        root_dir = HDRI_DIR
    tree = {}
    if _depth >= _max_depth or not os.path.isdir(root_dir):
        return tree
    try:
        for entry in sorted(os.listdir(root_dir), key=lambda x: x.lower()):
            full_p = os.path.join(root_dir, entry)
            if os.path.isdir(full_p) and not os.path.islink(full_p) and not entry.startswith(".") and not entry.startswith("__"):
                tree[entry] = get_hdri_folder_tree(full_p, _depth + 1, _max_depth)
    except Exception:
        pass
    return tree


def get_hdris(subpath=None):
    """Return all HDRI files and subfolders from the library."""
    return get_hdri_contents(subpath)


def get_gobos():
    """Return all gobo/cookie textures from the library."""
    return scan_directory(GOBO_DIR)


def get_scrims():
    """Return all scrim light textures from the library."""
    return scan_directory(SCRIM_DIR)


# ---------------------------------------------------------------------------
# Light Presets (JSON)
# ---------------------------------------------------------------------------
LIGHT_PRESETS = {
    "ARRI SkyPanel S60": {
        "light_type": "RectLight",
        "width": 0.6,
        "height": 0.6,
        "temperature": 5600,
        "use_temperature": True,
        "intensity": 1.0,
        "normalize": True,
        "diffuse": 1.0,
        "specular": 1.0,
    },
    "ARRI SkyPanel S120": {
        "light_type": "RectLight",
        "width": 1.2,
        "height": 0.6,
        "temperature": 5600,
        "use_temperature": True,
        "intensity": 1.0,
        "normalize": True,
    },
    "Kino Flo 4Bank": {
        "light_type": "RectLight",
        "width": 1.2,
        "height": 0.3,
        "temperature": 3200,
        "use_temperature": True,
        "intensity": 0.7,
        "normalize": True,
    },
    "Practical Tungsten": {
        "light_type": "SphereLight",
        "temperature": 2700,
        "use_temperature": True,
        "intensity": 0.3,
        "exposure": -2.0,
    },
    "Sun (Distant)": {
        "light_type": "DistantLight",
        "temperature": 5600,
        "use_temperature": True,
        "intensity": 1.0,
        "exposure": 0.0,
    },
    "Overcast Sky (Dome)": {
        "light_type": "DomeLight",
        "temperature": 6500,
        "use_temperature": True,
        "intensity": 1.0,
    },
    "Golden Hour": {
        "light_type": "DistantLight",
        "temperature": 3500,
        "use_temperature": True,
        "intensity": 0.8,
        "exposure": -0.5,
    },
    "Blue Moon": {
        "light_type": "DistantLight",
        "color": [0.4, 0.5, 0.8],
        "intensity": 0.05,
        "exposure": -4.0,
    },
    "Neon Rim (Cyan)": {
        "light_type": "RectLight",
        "width": 0.1,
        "height": 2.0,
        "color": [0.0, 0.9, 1.0],
        "intensity": 3.0,
    },
    "Softbox Large": {
        "light_type": "RectLight",
        "width": 3.0,
        "height": 3.0,
        "temperature": 5600,
        "use_temperature": True,
        "normalize": True,
    },
}


def get_light_presets():
    """Return built-in presets and user JSON presets as a nested dict."""
    presets = {"Built-in": dict(LIGHT_PRESETS)}

    # Recursively load user presets from rig directory
    if os.path.exists(RIG_DIR):
        for root, dirs, files in os.walk(RIG_DIR):
            rel_path = os.path.relpath(root, RIG_DIR)
            
            # Navigate to the correct nested dict based on folder path
            target_dict = presets
            if rel_path != ".":
                for part in rel_path.split(os.sep):
                    if part not in target_dict:
                        target_dict[part] = {}
                    target_dict = target_dict[part]
                    
            for file in files:
                if file.endswith(".json"):
                    jf = os.path.join(root, file)
                    try:
                        with open(jf, "r") as f:
                            data = json.load(f)
                        preset_name = os.path.splitext(file)[0]
                        data["_filepath"] = jf
                        target_dict[preset_name] = data
                    except Exception as exc:
                        logger.warning("Failed to load preset %s: %s", jf, exc)

    return presets


def save_light_preset(name, light_data_dict, directory=None):
    """
    Save a light configuration as a JSON preset.

    Args:
        name: Preset name (used as filename).
        light_data_dict: Dict of light parameters.
        directory: Save location. Defaults to RIG_DIR.
    """
    if directory is None:
        directory = RIG_DIR

    os.makedirs(directory, exist_ok=True)

    filepath = os.path.join(directory, f"{name}.json")
    with open(filepath, "w") as f:
        json.dump(light_data_dict, f, indent=2)

    logger.info("Saved preset: %s", filepath)
    return filepath


def save_preset_folder(folder_name, lights_list, directory=None):
    """
    Save a complete folder in RIG_DIR containing the selected lights
    both as individual light preset JSONs and as a combined rig preset JSON.

    Args:
        folder_name: Name of the folder to create in RIG_DIR.
        lights_list: List of light dicts representing the lights.
        directory: Base directory (defaults to RIG_DIR).
    Returns:
        The created folder path.
    """
    if directory is None:
        directory = RIG_DIR

    import re
    safe_folder = re.sub(r'[^a-zA-Z0-9_-]', '_', folder_name).strip("_") or "Light_Preset_Folder"
    preset_dir = os.path.join(directory, safe_folder)
    os.makedirs(preset_dir, exist_ok=True)

    # 1. Save each light individually inside the folder
    for light in lights_list:
        l_name = light.get("name", "light")
        safe_lname = re.sub(r'[^a-zA-Z0-9_-]', '_', str(l_name)).strip("_") or "light"
        l_file = os.path.join(preset_dir, f"{safe_lname}.json")
        try:
            with open(l_file, "w", encoding="utf-8") as f:
                json.dump(light, f, indent=2)
        except Exception as e:
            logger.warning("Failed to save light preset %s: %s", l_file, e)

    # 2. Save the complete combined rig preset inside the folder as well
    rig_data = {
        "rig_name": safe_folder,
        "version": "1.0",
        "lights": lights_list,
    }
    rig_file = os.path.join(preset_dir, f"{safe_folder}_Rig.json")
    try:
        with open(rig_file, "w", encoding="utf-8") as f:
            json.dump(rig_data, f, indent=2)
    except Exception as e:
        logger.warning("Failed to save rig preset %s: %s", rig_file, e)

    logger.info("Saved preset folder '%s' with %d light(s) to: %s", safe_folder, len(lights_list), preset_dir)
    return preset_dir


def save_rig_preset(name, lights_list, directory=None):
    """
    Save a complete lighting rig (multiple lights) as a JSON preset.

    Args:
        name: Rig name.
        lights_list: List of LightData dicts representing the full rig.
    """
    if directory is None:
        directory = RIG_DIR

    os.makedirs(directory, exist_ok=True)
    filepath = os.path.join(directory, f"rig_{name}.json")

    rig_data = {
        "rig_name": name,
        "version": "1.0",
        "lights": lights_list,
    }

    with open(filepath, "w") as f:
        json.dump(rig_data, f, indent=2)

    logger.info("Saved rig preset: %s", filepath)
    return filepath

def get_gradient_presets():
    """Load user-saved gradient builder presets from JSON."""
    db_path = os.path.join(_DEFAULT_ROOT, "gradient_presets.json")
    if os.path.exists(db_path):
        try:
            with open(db_path, "r") as f:
                return json.load(f)
        except Exception as e:
            logger.warning("Failed to load gradient presets: %s", e)
    return {}

def save_gradient_preset(name, config):
    """Save a gradient builder configuration to JSON."""
    os.makedirs(_DEFAULT_ROOT, exist_ok=True)
    db_path = os.path.join(_DEFAULT_ROOT, "gradient_presets.json")
    
    presets = get_gradient_presets()
    presets[name] = config
    
    try:
        with open(db_path, "w") as f:
            json.dump(presets, f, indent=2)
        logger.info("Saved gradient preset: %s", name)
    except Exception as e:
        logger.warning("Failed to save gradient preset: %s", e)
