#!/usr/bin/env python3
"""
=============================================================================
DomeBreaker - Automated Command-Line Updater for Windows & Linux
=============================================================================
Allows clients and users to update DomeBreaker to the latest GitHub release
or main branch with a single command, without redownloading manually.

Supports both:
  1. Git clone repositories (runs git fetch & git pull)
  2. Standalone / ZIP installations (fetches latest release archive from GitHub API)

Repository:
    https://github.com/arslanvision-ux/DomeBreaker
=============================================================================
"""

import os
import sys
import json
import shutil
import urllib.request
import zipfile
import subprocess
import tempfile

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

GITHUB_REPO = "arslanvision-ux/DomeBreaker"
GITHUB_API_COMMITS = f"https://api.github.com/repos/{GITHUB_REPO}/commits/main"
GITHUB_ZIP_URL = f"https://github.com/{GITHUB_REPO}/archive/refs/heads/main.zip"


def get_project_root():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.normpath(os.path.dirname(script_dir)).replace("\\", "/")


def update_via_git(root):
    """Attempt update using git pull."""
    git_dir = os.path.join(root, ".git")
    if not os.path.isdir(git_dir):
        return False, "Not a git repository"

    # Check if git command exists
    try:
        subprocess.run(["git", "--version"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
    except Exception:
        return False, "git command not available in PATH"

    print("[INFO] Updating via Git...")
    try:
        # Check current remote
        remotes_out = subprocess.check_output(["git", "remote", "-v"], cwd=root).decode("utf-8")
        if "origin" not in remotes_out:
            print(f"[INFO] Setting git remote origin to https://github.com/{GITHUB_REPO}.git")
            subprocess.run(["git", "remote", "add", "origin", f"https://github.com/{GITHUB_REPO}.git"], cwd=root, check=True)

        # Fetch
        print("[INFO] Fetching latest changes from origin...")
        subprocess.run(["git", "fetch", "origin"], cwd=root, check=True)

        # Check status
        status_out = subprocess.check_output(["git", "status", "-uno"], cwd=root).decode("utf-8")
        if "Your branch is up to date" in status_out:
            print("\n[SUCCESS] DomeBreaker is already up to date with the latest release!")
            return True, "Up to date"

        # Pull
        print("[INFO] Pulling updates...")
        pull_res = subprocess.run(["git", "pull", "--ff-only", "origin", "main"], cwd=root, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if pull_res.returncode != 0:
            # Fallback to standard pull
            pull_res = subprocess.run(["git", "pull", "origin", "main"], cwd=root, check=True)

        print("\n[SUCCESS] DomeBreaker successfully updated via Git!")
        return True, "Updated"
    except Exception as e:
        print(f"[WARNING] Git update encountered an issue: {e}")
        return False, str(e)


def update_via_zip(root):
    """Download the latest repository zip archive from GitHub and update files."""
    print(f"[INFO] Checking latest updates from GitHub ({GITHUB_REPO})...")

    headers = {
        "User-Agent": "DomeBreaker-Updater",
        "Accept": "application/vnd.github.v3+json"
    }

    try:
        req = urllib.request.Request(GITHUB_API_COMMITS, headers=headers)
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            latest_sha = data.get("sha", "")[:7]
            commit_msg = data.get("commit", {}).get("message", "").split("\n")[0]
            commit_date = data.get("commit", {}).get("author", {}).get("date", "")[:10]
            print(f"[INFO] Latest GitHub Commit: [{latest_sha}] {commit_msg} ({commit_date})")
    except Exception as e:
        print(f"[WARNING] Could not query GitHub commit info ({e}), proceeding with archive download...")

    version_file = os.path.join(root, "VERSION")
    current_ver = "unknown"
    if os.path.isfile(version_file):
        try:
            with open(version_file, "r", encoding="utf-8") as f:
                current_ver = f.read().strip()
        except Exception:
            pass

    print(f"[INFO] Current local version: {current_ver}")
    print(f"[INFO] Downloading latest package from: {GITHUB_ZIP_URL}")

    with tempfile.TemporaryDirectory() as tmp_dir:
        zip_path = os.path.join(tmp_dir, "update.zip")
        try:
            req = urllib.request.Request(GITHUB_ZIP_URL, headers={"User-Agent": "DomeBreaker-Updater"})
            with urllib.request.urlopen(req, timeout=30) as response, open(zip_path, "wb") as out_file:
                shutil.copyfileobj(response, out_file)
        except Exception as e:
            print(f"[ERROR] Failed to download update from GitHub: {e}")
            return False

        print("[INFO] Extracting update archive...")
        extract_dir = os.path.join(tmp_dir, "extracted")
        with zipfile.ZipFile(zip_path, "r") as z:
            z.extractall(extract_dir)

        # GitHub archives extract into a top-level directory like 'DomeBreaker-main'
        subfolders = [os.path.join(extract_dir, d) for d in os.listdir(extract_dir) if os.path.isdir(os.path.join(extract_dir, d))]
        src_root = subfolders[0] if subfolders else extract_dir

        # Copy over production directories
        update_targets = [
            ("python", os.path.join(root, "python")),
            ("houdini", os.path.join(root, "houdini")),
            ("scripts", os.path.join(root, "scripts")),
        ]

        single_files = [
            "install.bat", "install.sh", "update.bat", "update.sh",
            "README.md", "INSTALL.md", "LICENSE", "VERSION"
        ]

        for sub, dst_path in update_targets:
            src_path = os.path.join(src_root, sub)
            if os.path.isdir(src_path):
                print(f"[INFO] Updating {sub}/...")
                # Copy tree with overwrite
                for root_dir, dirs, files in os.walk(src_path):
                    rel_dir = os.path.relpath(root_dir, src_path)
                    target_dir = os.path.join(dst_path, rel_dir) if rel_dir != "." else dst_path
                    os.makedirs(target_dir, exist_ok=True)
                    for f in files:
                        s_f = os.path.join(root_dir, f)
                        d_f = os.path.join(target_dir, f)
                        shutil.copy2(s_f, d_f)

        for fname in single_files:
            s_f = os.path.join(src_root, fname)
            d_f = os.path.join(root, fname)
            if os.path.isfile(s_f):
                shutil.copy2(s_f, d_f)

        print("\n" + "=" * 65)
        print("[SUCCESS] DomeBreaker successfully updated to the latest GitHub version!")
        print("=" * 65)
        return True


def run_update():
    print("=" * 65)
    print("   [DomeBreaker] Command-Line Auto-Updater")
    print("=" * 65)

    root = get_project_root()
    print(f"[INFO] DomeBreaker Directory: {root}\n")

    # Try Git first
    git_ok, reason = update_via_git(root)
    if git_ok:
        print("\nIf Houdini is currently open, click 'Reload DomeBreaker' on the shelf")
        print("or restart Houdini to load the updated code.")
        return True

    # Fallback to direct download
    print(f"[INFO] Git update skipped ({reason}). Using direct GitHub updater...")
    success = update_via_zip(root)
    if success:
        print("\nIf Houdini is currently open, click 'Reload DomeBreaker' on the shelf")
        print("or restart Houdini to load the updated code.")
    return success


if __name__ == "__main__":
    ok = run_update()
    sys.exit(0 if ok else 1)
