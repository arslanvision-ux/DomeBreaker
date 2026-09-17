"""
DomeBreaker - Solaris USD Lighting & Environment Dissector.

Native Houdini 19.5, 20.0, 20.5, 21.0+ integration for physical HDRI calibration,
sun extraction, multi-light inpainting, ground projection parallax, and lookdev verification.
"""

from hdri_match_solaris import *
from hdri_match_solaris.panel_ui import (
    HdriMatchSolarisPanel,
    DomeBreakerPanel,
    createInterface,
)

__version__ = "1.0.0-beta.1"
__all__ = [
    "HdriMatchSolarisPanel",
    "DomeBreakerPanel",
    "createInterface",
]

