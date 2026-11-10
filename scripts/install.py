#!/usr/bin/env python3
"""
=============================================================================
DomeBreaker - Automated Package Installer & Uninstaller (Windows & Linux)
=============================================================================
Detects installed Houdini versions (19.5, 20.0, 20.5, 21.0+), generates a clean
Houdini package descriptor pointing directly to this DomeBreaker installation,
and registers the DomeBreaker shelf and Solaris Python panel.

If DomeBreaker is already installed, provides an interactive option to
Reinstall/Update or Uninstall.

Usage:
    python scripts/install.py                 # Interactive (prompts if already installed)
    python scripts/install.py --install       # Direct install/update
    python scripts/install.py --uninstall     # Direct uninstall
    or run install.bat / uninstall.bat (Windows)
    or run install.sh / uninstall.sh (Linux)
=============================================================================
"""

import os
import sys
import json
import glob
import platform
import argparse

# Force UTF-8 output on Windows consoles to prevent cp1251/cp1252 charmap errors
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
if hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def get_project_root():
    """Return the absolute path of the DomeBreaker project root."""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(script_dir)
    return os.path.normpath(root).replace("\\", "/")


def _is_supported_houdini_version(ver_str):
    """Return True if the version string is Houdini 20.0 or newer."""
    if not ver_str:
        return False
    try:
        parts = ver_str.split(".")
        major = float(parts[0] + "." + parts[1]) if len(parts) >= 2 else float(parts[0])
        return major >= 20.0
    except Exception:
        return False


def cleanup_legacy_versions():
    """Remove any old DomeBreaker package descriptors from unsupported Houdini versions (< 20.0)."""
    home = os.path.expanduser("~")
    legacy_roots = [
        os.path.join(home, "Documents"),
        os.path.join(home, "OneDrive", "Documents"),
        home,
    ]
    for c_root in legacy_roots:
        if os.path.isdir(c_root):
            for match in glob.glob(os.path.join(c_root, "houdini*")):
                base = os.path.basename(match)
                ver_str = base.replace("houdini", "").strip()
                if ver_str and not _is_supported_houdini_version(ver_str):
                    for pkg in ["domebreaker.json", "hdri_match_solaris.json"]:
                        old_f = os.path.join(match, "packages", pkg)
                        if os.path.isfile(old_f):
                            try:
                                os.remove(old_f)
                                print(f"[INFO] Cleaned up legacy package descriptor: {old_f}")
                            except Exception:
                                pass


def find_houdini_user_dirs():
    """Find all supported Houdini preference directories (Houdini 20.0, 20.5, 21.0+)."""
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
                        ver_str = base.replace("houdini", "").strip()
                        if ver_str and _is_supported_houdini_version(ver_str):
                            dirs.append(os.path.normpath(match))

    elif system == "Linux":
        for pattern in [os.path.join(home, "houdini*"), os.path.join(home, ".houdini*")]:
            for match in glob.glob(pattern):
                if os.path.isdir(match):
                    base = os.path.basename(match).lstrip(".")
                    ver_str = base.replace("houdini", "").strip()
                    if ver_str and _is_supported_houdini_version(ver_str):
                        dirs.append(os.path.normpath(match))

    elif system == "Darwin":
        mac_pref = os.path.join(home, "Library", "Preferences", "houdini")
        if os.path.isdir(mac_pref):
            for match in glob.glob(os.path.join(mac_pref, "*")):
                base = os.path.basename(match)
                if os.path.isdir(match) and _is_supported_houdini_version(base):
                    dirs.append(os.path.normpath(match))

    env_pref = os.environ.get("HOUDINI_USER_PREF_DIR")
    if env_pref and os.path.isdir(env_pref) and os.path.normpath(env_pref) not in dirs:
        dirs.append(os.path.normpath(env_pref))

    dirs = sorted(list(set(dirs)), reverse=True)
    return dirs


def find_existing_installations(pref_dirs):
    """Find existing DomeBreaker package descriptor files."""
    found = []
    for p_dir in pref_dirs:
        packages_dir = os.path.join(p_dir, "packages")
        for pkg_name in ["domebreaker.json", "hdri_match_solaris.json"]:
            pkg_file = os.path.join(packages_dir, pkg_name)
            if os.path.isfile(pkg_file):
                found.append(pkg_file)
    return found


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
        "houdini_version": ">= 20.0",
        "description": "DomeBreaker - Solaris USD Lighting & Environment Suite"
    }


def install(target_dir=None):
    """Execute the installation."""
    print("=" * 65)
    print("   [DomeBreaker] Solaris USD Suite Installer")
    print("=" * 65)

    cleanup_legacy_versions()

    root = get_project_root()
    print(f"[INFO] DomeBreaker Root: {root}")
    print(f"[INFO] Operating System: {platform.system()} ({platform.machine()})")
    print(f"[INFO] Python: {platform.python_version()} ({sys.executable})")

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

    print(f"\n[INFO] Target Houdini preference directory(ies):")
    for p in pref_dirs:
        print(f"  * {p}")

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
        print(f"[SUCCESS] DomeBreaker successfully registered into {installed_count} Houdini version(s)!")
        print("=" * 65)
        print("\nNext Steps:")
        print("1. Launch Houdini (or restart if already running).")
        print("2. In the top toolbar, click the 'DomeBreaker' shelf tab.")
        print("3. Click the 'DomeBreaker' shelf tool or open via:")
        print("   Windows menu -> Python Panel -> DomeBreaker")
        print("\nHappy Lookdev & Lighting!")
        return True
    else:
        print("[ERROR] Installation failed.")
        return False


def uninstall(target_dir=None):
    """Execute uninstallation by removing package descriptors from Houdini preference folders."""
    print("=" * 65)
    print("   [DomeBreaker] Solaris USD Suite Uninstaller")
    print("=" * 65)

    cleanup_legacy_versions()

    pref_dirs = [target_dir] if target_dir else find_houdini_user_dirs()
    if not pref_dirs:
        print("[WARNING] No Houdini preference directories detected to uninstall from.")
        return False

    existing = find_existing_installations(pref_dirs)
    if not existing:
        print("[INFO] DomeBreaker does not appear to be installed in any detected Houdini directory.")
        return True

    removed_count = 0
    for pkg_file in existing:
        try:
            os.remove(pkg_file)
            print(f"[SUCCESS] Removed package descriptor: {pkg_file}")
            removed_count += 1
        except Exception as e:
            print(f"[ERROR] Failed to remove {pkg_file}: {e}")

    print("\n" + "=" * 65)
    if removed_count > 0:
        print(f"[SUCCESS] DomeBreaker successfully uninstalled from {removed_count} Houdini version(s)!")
        print("=" * 65)
        print("Your custom scenes, plates, and Houdini user preferences were preserved untouched.")
        return True
    else:
        print("[WARNING] Could not remove package descriptors.")
        return False


def main():
    parser = argparse.ArgumentParser(description="DomeBreaker Package Installer / Uninstaller")
    parser.add_argument("--install", "-i", action="store_true", help="Force install or update")
    parser.add_argument("--uninstall", "-u", action="store_true", help="Uninstall DomeBreaker")
    parser.add_argument("--target", "-t", type=str, default=None, help="Specific Houdini preference directory")
    args = parser.parse_args()

    # Direct flag invocations
    if args.uninstall:
        success = uninstall(args.target)
        sys.exit(0 if success else 1)

    if args.install:
        success = install(args.target)
        sys.exit(0 if success else 1)

    # Interactive flow
    pref_dirs = [args.target] if args.target else find_houdini_user_dirs()
    existing = find_existing_installations(pref_dirs)

    if existing:
        print("=" * 65)
        print("   [DomeBreaker] Solaris USD Suite Manager")
        print("=" * 65)
        print(f"[INFO] Existing DomeBreaker installation(s) detected:")
        for ep in existing:
            print(f"  * {ep}")
        print("\nDomeBreaker is already installed. What would you like to do?")
        print("  [1] Reinstall / Update package registration (default)")
        print("  [2] Uninstall DomeBreaker from Houdini")
        print("  [3] Cancel & Exit")
        print("-" * 65)

        choice = "1"
        try:
            val = input("Enter choice [1/2/3] (default: 1): ").strip()
            digits = "".join(c for c in val if c.isdigit())
            if digits:
                choice = digits[0]
        except (EOFError, KeyboardInterrupt):
            pass

        if choice == "2":
            success = uninstall(args.target)
            sys.exit(0 if success else 1)
        elif choice == "3":
            print("\nOperation cancelled. No changes were made.")
            sys.exit(0)
        else:
            success = install(args.target)
            sys.exit(0 if success else 1)
    else:
        success = install(args.target)
        sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
