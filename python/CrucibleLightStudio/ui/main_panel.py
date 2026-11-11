# -*- coding: utf-8 -*-
"""
Crucible Light Studio - Main Panel
====================================
The top-level QWidget that assembles all sub-widgets into a unified
lighting studio panel. Can be docked as a Houdini Python Panel,
Maya dock widget, or Blender operator panel.
"""

try:
    from PySide2 import QtWidgets, QtCore, QtGui
except ImportError:
    from PySide6 import QtWidgets, QtCore, QtGui

import os
import logging

from CrucibleLightStudio.ui.stylesheet import CRUCIBLE_STYLESHEET
from CrucibleLightStudio.ui.widgets.asset_library import AssetLibraryWidget
from CrucibleLightStudio.ui.widgets.light_manager import LightManagerWidget
from CrucibleLightStudio.core.dcc_backend import get_backend, detect_dcc
from CrucibleLightStudio.core import preset_manager

logger = logging.getLogger("CrucibleLightStudio.ui.main_panel")


class CrucibleLightStudioPanel(QtWidgets.QWidget):
    """
    Main Crucible Light Studio panel.

    Layout (top to bottom):
    ┌──────────────────────────────────────┐
    │  Header: Logo / DCC / Renderer info  │
    ├──────────────────────────────────────┤
    │  Asset Library (HDRIs / Gobos /      │
    │  Scrims / Presets tabs)              │
    ├──────────────────────────────────────┤
    │  Light Manager (spreadsheet)         │
    │  + Light Properties detail panel     │
    ├──────────────────────────────────────┤
    │  Utilities Toolbar                   │
    └──────────────────────────────────────┘
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Crucible Light Studio")
        self.setMinimumSize(420, 400)

        # Detect DCC and get backend
        self._dcc_name = detect_dcc()
        self._backend = get_backend()

        self._build_ui()
        self._connect_signals()
        self._initial_refresh()

    def closeEvent(self, event):
        if hasattr(self, "asset_library") and hasattr(self.asset_library, "stop_workers"):
            try:
                self.asset_library.stop_workers()
            except Exception:
                pass
        super().closeEvent(event)

    # ------------------------------------------------------------------
    # UI Construction
    # ------------------------------------------------------------------
    def _build_ui(self):
        self.setStyleSheet(CRUCIBLE_STYLESHEET)

        main_layout = QtWidgets.QVBoxLayout(self)
        main_layout.setContentsMargins(8, 8, 8, 8)
        main_layout.setSpacing(6)

        # ---- Header ----
        header = self._build_header()
        main_layout.addLayout(header)

        # ---- Main Tabs ----
        self.main_tabs = QtWidgets.QTabWidget()
        
        # 1. Lighting Workspace Tab
        lighting_widget = QtWidgets.QWidget()
        lighting_layout = QtWidgets.QVBoxLayout(lighting_widget)
        lighting_layout.setContentsMargins(0, 4, 0, 0)
        
        # ---- Main splitter ----
        self.splitter = QtWidgets.QSplitter(QtCore.Qt.Vertical)

        # Asset Library
        self.asset_library = AssetLibraryWidget()
        self.splitter.addWidget(self.asset_library)

        # Light Manager
        self.light_manager = LightManagerWidget(backend=self._backend)
        self.splitter.addWidget(self.light_manager)

        # Set initial proportions (generous library, balanced stretch)
        self.splitter.setSizes([320, 480])
        self.splitter.setStretchFactor(0, 1)  # Asset Library gets 1 part
        self.splitter.setStretchFactor(1, 1)  # Light Table gets 1 part
        self.splitter.setCollapsible(0, False)
        self.splitter.setCollapsible(1, False)

        lighting_layout.addWidget(self.splitter)
        self.main_tabs.addTab(lighting_widget, "Lighting / Gobos")
        
        main_layout.addWidget(self.main_tabs, stretch=1)



    def _build_header(self):
        """Build the header bar with logo, DCC info, and renderer."""
        header = QtWidgets.QHBoxLayout()
        header.setSpacing(8)

        # Logo / Title
        logo_label = QtWidgets.QLabel("Crucible Light Studio")
        logo_label.setStyleSheet(
            "color: #e67e22; font-size: 16px; font-weight: bold; "
            "letter-spacing: 1px;"
        )
        header.addWidget(logo_label)

        self.btn_reload = QtWidgets.QPushButton("Reload UI")
        self.btn_reload.setToolTip("Reload Python scripts and UI")
        self.btn_reload.clicked.connect(self._on_reload_ui)
        self.btn_reload.hide()
        
        header.addStretch()
        header.addWidget(self.btn_reload)

        # DCC indicator
        dcc_display = self._dcc_name.upper() if self._dcc_name else "STANDALONE"
        dcc_label = QtWidgets.QLabel(f"DCC: {dcc_display}")
        dcc_label.setStyleSheet("color: #888; font-size: 11px;")
        header.addWidget(dcc_label)



        # Version
        from CrucibleLightStudio import __version__
        ver_label = QtWidgets.QLabel(f"v{__version__}")
        ver_label.setStyleSheet("color: #555; font-size: 10px;")
        header.addWidget(ver_label)

        self._header_layout = header
        return header

    def add_header_widget(self, widget):
        """Add a widget (e.g. Pop Out button) to the right end of the header."""
        if hasattr(self, "_header_layout") and self._header_layout:
            self._header_layout.addWidget(widget)

    # ------------------------------------------------------------------
    # Signal Wiring
    # ------------------------------------------------------------------
    def _connect_signals(self):
        # Asset library → apply to selected light
        self.asset_library.asset_applied.connect(self._on_asset_applied)
        if hasattr(self.asset_library, "assign_to_crucible_light_requested"):
            self.asset_library.assign_to_crucible_light_requested.connect(self._on_assign_to_selected_light)

        self.light_manager.light_selected.connect(self._on_light_selected)
        self.light_manager.request_refresh.connect(self._on_preset_library_refresh)
        
        self.light_manager.gradient_builder_requested.connect(self._on_gradient_builder)

    # ------------------------------------------------------------------
    # Refresh
    # ------------------------------------------------------------------
    def _initial_refresh(self):
        """Load everything on startup."""
        self.asset_library.refresh_library()
        self.light_manager.refresh_lights()


    # ------------------------------------------------------------------
    # Callbacks
    # ------------------------------------------------------------------

    def _on_reload_ui(self):
        """Close window and let the user launch it again from shelf to reload."""
        self.close()
        QtWidgets.QMessageBox.information(
            None, "UI Closed",
            "Scripts are ready to reload. Click the Crucible Light Studio shelf icon again to launch with fresh code."
        )
                
    def _on_gradient_builder(self):
        row = self.light_manager.table.currentRow()
        if row < 0:
            QtWidgets.QMessageBox.information(
                self, "No Light Selected",
                "Please select a light to apply the custom gradient to."
            )
            return
            
        from CrucibleLightStudio.ui.widgets.gradient_builder import GradientBuilderDialog
        dlg = GradientBuilderDialog(self)
        if dlg.exec_() == QtWidgets.QDialog.Accepted:
            tex_path = dlg.get_texture_path()
            item = self.light_manager.table.item(row, 2)
            if item and self._backend:
                prim_path = item.data(QtCore.Qt.UserRole)
                self._backend.set_light_param(prim_path, "texture", tex_path)
                self._backend.set_light_param(prim_path, "color", [1.0, 1.0, 1.0])
                self.light_manager.refresh_lights()
            # Also refresh the library to show the newly saved custom preset
            self.asset_library.refresh_library()
    def _on_assign_to_selected_light(self, asset_dict):
        """Explicitly assign texture to the currently selected light in the Crucible table."""
        if not asset_dict or not isinstance(asset_dict, dict):
            return
        tex_path = asset_dict.get("path", "")
        if not tex_path or tex_path == "__BUILDER__":
            if tex_path == "__BUILDER__":
                self._on_gradient_builder()
            return

        row = self.light_manager.table.currentRow()
        if row < 0:
            QtWidgets.QMessageBox.information(
                self, "No Light Selected",
                "Please select a light in the Crucible Light Studio manager table first,\n"
                "then choose 'Assign to Selected Crucible Light'."
            )
            return

        item = self.light_manager.table.item(row, 2)  # COL_NAME
        if item and self._backend:
            prim_path = item.data(QtCore.Qt.UserRole)
            self._backend.set_light_param(prim_path, "texture", tex_path)
            self._backend.set_light_param(prim_path, "color", [1.0, 1.0, 1.0])
            self.light_manager.refresh_lights()

    def _on_asset_applied(self, category, value):
        """Handle asset applied from the library."""
        if category == "preset":
            import json
            try:
                preset_data = json.loads(value)
            except:
                from CrucibleLightStudio.core import preset_manager
                presets = preset_manager.get_light_presets()
                preset_data = presets.get(value)
                
            if preset_data and self._backend:
                if "lights" in preset_data:
                    # It's a Rig preset
                    for light in preset_data["lights"]:
                        light_type = light.get("light_type", "RectLight")
                        name = light.get("name", "Light")
                        path = self._backend.create_light(light_type, name=name)
                        if path:
                            for param, val in light.items():
                                if param not in ("light_type", "prim_path", "name", "_filepath"):
                                    self._backend.set_light_param(path, param, val)
                else:
                    # Single light preset
                    light_type = preset_data.get("light_type", "RectLight")
                    name = preset_data.get("name", "Light")
                    path = self._backend.create_light(light_type, name=name)
                    if path:
                        # Apply preset parameters
                        for param, val in preset_data.items():
                            if param not in ("light_type", "prim_path", "name", "_filepath"):
                                self._backend.set_light_param(path, param, val)
            self.light_manager.refresh_lights()

        elif category in ("hdri", "gobo", "scrim"):
            if value == "__BUILDER__":
                self._on_gradient_builder()
                return
                
            # Apply texture to the currently selected light
            row = self.light_manager.table.currentRow()
            if row < 0:
                QtWidgets.QMessageBox.information(
                    self, "No Light Selected",
                    "Select a light in the manager first, then "
                    "double-click an asset to apply it."
                )
                return
            item = self.light_manager.table.item(row, 2)  # COL_NAME
            if item and self._backend:
                prim_path = item.data(QtCore.Qt.UserRole)
                self._backend.set_light_param(prim_path, "texture", value)
                self._backend.set_light_param(prim_path, "color", [1.0, 1.0, 1.0])
                self.light_manager.refresh_lights()

    def _on_preset_library_refresh(self):
        """Refresh asset library and switch to Presets tab."""
        self.asset_library.refresh_library()
        self.asset_library.show_presets_tab()

    def _on_light_selected(self, prim_path):
        """Called when a user selects a light row in the Light Manager UI."""
        if self._backend and hasattr(self._backend, "select_light"):
            self._backend.select_light(prim_path)
