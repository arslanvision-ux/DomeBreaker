#!/usr/bin/env python3
"""
=============================================================================
DomeBreaker - Automated Package Installer for Windows & Linux
=============================================================================
Detects installed Houdini versions (19.5, 20.0, 20.5, 21.0+), generates a clean
Houdini package descriptor pointing directly to this DomeBreaker installation,
and registers the DomeBreaker shelf and Solaris Python panel.

Usage:
    python scripts/install.py
    or run install.bat (Windows) / install.sh (Linux)
=============================================================================
"""

import os
import sys
import json
import glob
import platform


def get_project_root():
    """Return the absolute path of the DomeBreaker project root."""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(script_dir)
    return os.path.normpath(root).replace("\\", "/")


def find_houdini_user_dirs():
    """Find all Houdini preference directories for the current user."""
    home = os.path.expanduser("~")
    dirs = []
    system = platform.system()

    if system == "Windows":
        candidates_roots = [
            os.path.join(home, "Documents"),
            os.path.join(home, "OneDrive", "Documents"),
            home,
        ]
        for c_root in candidates_roots:
            if os.path.isdir(c_root):
                for match in glob.glob(os.path.join(c_root, "houdini*")):
                    if os.path.isdir(match):
                        base = os.path.basename(match)
                        # Filter out non-versioned or backup folders
                        ver_str = base.replace("houdini", "").strip()
                        if ver_str and (ver_str[0].isdigit()):
                            dirs.append(os.path.normpath(match))

    elif system == "Linux":
        # Check ~/houdiniX.Y and ~/.houdiniX.Y
        for pattern in [os.path.join(home, "houdini*"), os.path.join(home, ".houdini*")]:
            for match in glob.glob(pattern):
                if os.path.isdir(match):
                    base = os.path.basename(match).lstrip(".")
                    ver_str = base.replace("houdini", "").strip()
                    if ver_str and ver_str[0].isdigit():
                        dirs.append(os.path.normpath(match))

    elif system == "Darwin":
        mac_pref = os.path.join(home, "Library", "Preferences", "houdini")
        if os.path.isdir(mac_pref):
            for match in glob.glob(os.path.join(mac_pref, "*")):
                if os.path.isdir(match) and os.path.basename(match)[0].isdigit():
                    dirs.append(os.path.normpath(match))

    # Also check HOUDINI_USER_PREF_DIR if set
    env_pref = os.environ.get("HOUDINI_USER_PREF_DIR")
    if env_pref and os.path.isdir(env_pref) and os.path.normpath(env_pref) not in dirs:
        dirs.append(os.path.normpath(env_pref))

    # Sort so newest versions come first (e.g. 21.0, 20.5, 20.0)
    dirs = sorted(list(set(dirs)), reverse=True)
    return dirs


def generate_package_json(project_root):
    """Generate the package JSON dictionary."""
    return {
        "env": [
            {
                "DOMEBREAKER_ROOT": project_root
            },
            {
                "HDRI_MATCH_SOLARIS_ROOT": "$DOMEBREAKER_ROOT"
            },
            {
                "PYTHONPATH": {
                    "value": [
                        "$DOMEBREAKER_ROOT/python"
                    ],
                    "method": "append"
                }
            }
        ],
        "path": [
            "$DOMEBREAKER_ROOT/houdini"
        ],
        "houdini_version": ">= 19.5",
        "description": "DomeBreaker - Solaris USD Lighting & Environment Suite"
    }


def install(target_dir=None):
    """Execute the installation."""
    print("=" * 65)
    print("   ⚡ DomeBreaker - Solaris USD Suite Installer")
    print("=" * 65)

    root = get_project_root()
    print(f"[INFO] DomeBreaker Root: {root}")
    print(f"[INFO] Operating System: {platform.system()} ({platform.machine()})")
    print(f"[INFO] Python: {platform.python_version()} ({sys.executable})")

    # Validate essential structure
    req_paths = [
        os.path.join(root, "python", "hdri_match_solaris"),
        os.path.join(root, "houdini", "toolbar"),
        os.path.join(root, "houdini", "python_panels"),
    ]
    for rp in req_paths:
        if not os.path.exists(rp):
            print(f"[ERROR] Missing required project directory: {rp}")
            return False

    pref_dirs = [target_dir] if target_dir else find_houdini_user_dirs()

    if not pref_dirs:
        print("\n[WARNING] No Houdini user preference directories were automatically detected!")
        print("You can manually install DomeBreaker by copying 'domebreaker.json' into your")
        print("Houdini packages folder (e.g. ~/houdini20.5/packages/ or Documents/houdini20.5/packages/).")
        return False

    print(f"\n[INFO] Found {len(pref_dirs)} Houdini preference directory(ies):")
    for p in pref_dirs:
        print(f"  • {p}")

    pkg_data = generate_package_json(root)
    pkg_json_str = json.dumps(pkg_data, indent=4) + "\n"

    installed_count = 0
    for p_dir in pref_dirs:
        packages_dir = os.path.join(p_dir, "packages")
        os.makedirs(packages_dir, exist_ok=True)
        pkg_file = os.path.join(packages_dir, "domebreaker.json")

        try:
            with open(pkg_file, "w", encoding="utf-8") as f:
                f.write(pkg_json_str)
            print(f"[SUCCESS] Registered: {pkg_file}")
            installed_count += 1
        except Exception as e:
            print(f"[ERROR] Failed to write {pkg_file}: {e}")

    # Also save a canonical copy inside the repository's houdini/packages folder
    repo_pkg = os.path.join(root, "houdini", "packages", "domebreaker.json")
    os.makedirs(os.path.dirname(repo_pkg), exist_ok=True)
    with open(repo_pkg, "w", encoding="utf-8") as f:
        f.write(pkg_json_str)

    print("\n" + "=" * 65)
    if installed_count > 0:
        print(f"🎉 DomeBreaker successfully installed into {installed_count} Houdini version(s)!")
        print("=" * 65)
        print("\nNext Steps:")
        print("1. Launch Houdini (or restart if already running).")
        print("2. In the top toolbar, click the 'DomeBreaker' shelf tab.")
        print("3. Click the 'DomeBreaker' shelf tool or open via:")
        print("   Windows menu -> Python Panel -> DomeBreaker")
        print("\nHappy Lookdev & Lighting!")
        return True
    else:
        print("❌ Installation failed.")
        return False


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else None
    success = install(target)
    sys.exit(0 if success else 1)
