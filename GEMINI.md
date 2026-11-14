# DomeBreaker Project Rules (Local)

## 1. Project Context & Architecture
- **Target**: Houdini Solaris (USD + Arnold) plugin (`python/hdri_match_solaris/`, `python/hdri_match/`, `python/domebreaker/`).
- **Workflow**: Developed on Windows (`d:\PROJECTS\DomeBreaker`), deployed via GitHub (`origin main`) to Rocky Linux 8 workstations without `git` (via `./update.sh` / `scripts/update.py` downloading `main.zip`). Never deploy DomeBreaker to unrelated servers.

## 2. Mandatory Technical Invariants
- **Runtime Safety**: Zero `cv2` imports in Houdini runtime, zero `importlib.reload()`, zero hardcoded developer drive paths.
- **PCoIP Virtual X11 (`DISPLAY=:100`) Glyph Safety**: Keep `panel_ui.py`, `splatforge/__init__.py`, `install.sh`, `uninstall.sh`, and `update.sh` free of unsupported Unicode emojis/glyphs.
- **Version Sync**: Whenever bumping version, update `VERSION`, `python/domebreaker/__init__.py`, and `python/hdri_match_solaris/__init__.py` together, and add/run invariant tests.

## 3. DomeBreaker Subagent Routing (Argon -> Subagents)
- **Tier 1 (`Model="flash_lite"`)**:
  - Search symbols/functions in `python/hdri_match_solaris/panel_ui.py` and `python/hdri_match/core/colorspace.py`.
  - Check version consistency across `VERSION`, `__init__.py` files, and `git status` / `git diff`.
- **Tier 2 (`Model="flash"`)**:
  - Run `pytest -v tests` and `python -m py_compile` on modified `.py` files.
  - Execute `git add`, `git commit`, `git tag`, and `git push origin main --tags` after verification.
- **Tier 3 (Argon Core)**:
  - OCIO/color space transformations, HDRI/plate calibration math, USD/Arnold Solaris LOP graph logic, and `panel_ui.py` state/bug fixes.
