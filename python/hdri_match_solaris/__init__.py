"""
HDRI Match Solaris — Native Houdini 21+ Solaris LOP Pipeline.

Ports the HDRI Match Plate calibration, sun relighting, and lookdev
verification tools into native Houdini Solaris LOP nodes that operate
directly on the USD stage.

This package imports core math modules from the original ``hdri_match``
package (color science, exposure, white balance, projection, sun relighting)
rather than duplicating them.  The original package must be on PYTHONPATH,
which the Houdini package descriptor handles automatically.
"""

__version__ = "1.0.0"
__author__ = "HDRI Match Plate"
__houdini_min__ = "21.0"

import os
import sys


def _ensure_hdri_match_available():
    """Verify that the original hdri_match core package is importable."""
    try:
        import hdri_match.core.colorspace  # noqa: F401
        return True
    except ImportError:
        # Attempt fallback: look for HDRI_MATCH_PLATE_ROOT env var
        root = os.environ.get("HDRI_MATCH_PLATE_ROOT", "")
        if root and os.path.isdir(root) and root not in sys.path:
            sys.path.insert(0, root)
            try:
                import hdri_match.core.colorspace  # noqa: F401
                return True
            except ImportError:
                pass
        return False


HDRI_MATCH_AVAILABLE = _ensure_hdri_match_available()

if not HDRI_MATCH_AVAILABLE:
    import warnings
    warnings.warn(
        "[HDRI Match Solaris] Could not import 'hdri_match' core package.  "
        "Ensure HDRI_MATCH_PLATE_ROOT points to the HDRI_Match_Plate project "
        "directory, or add it to PYTHONPATH via the Houdini package JSON.",
        RuntimeWarning,
    )


# Auto-register OpenImageIO adapter for Houdini
try:
    import hdri_match_solaris.oiio_adapter
except Exception:
    pass
