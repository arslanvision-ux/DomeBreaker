"""
DomeBreaker - Solaris USD Lighting & Environment Dissector.

Native Houdini 21+ integration for physical HDRI calibration, sun extraction,
multi-light inpainting, ground projection parallax, and lookdev verification.
"""

from hdri_match_solaris import *
from hdri_match_solaris.panel_ui import (
    HdriMatchSolarisPanel,
    DomeBreakerPanel,
    createInterface,
)

__version__ = "2.0.0"
__all__ = [
    "HdriMatchSolarisPanel",
    "DomeBreakerPanel",
    "createInterface",
]
