# -*- coding: utf-8 -*-
"""
Crucible Light Studio - Utilities Toolbar Widget
==================================================
Quick-access buttons for Fog, Lens Effects,
and Light Linking.
"""

try:
    from PySide2 import QtWidgets, QtCore, QtGui
except ImportError:
    from PySide6 import QtWidgets, QtCore, QtGui

import logging

logger = logging.getLogger("CrucibleLightStudio.ui.utilities")


class UtilitiesToolbar(QtWidgets.QWidget):
    """Bottom toolbar with quick-access production utilities."""

    # Signals
    request_fog = QtCore.Signal()
    request_remove_fog = QtCore.Signal()
    request_lens_fx = QtCore.Signal()
    request_light_linking = QtCore.Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._fog_active = False
        self._build_ui()

    def _build_ui(self):
        main_layout = QtWidgets.QVBoxLayout(self)
        main_layout.setContentsMargins(0, 4, 0, 0)
        main_layout.setSpacing(4)

        # Section title
        title = QtWidgets.QLabel("Utilities")
        title.setObjectName("section_title")
        main_layout.addWidget(title)

        # Row 1: Renderer features
        row2 = QtWidgets.QHBoxLayout()
        row2.setSpacing(6)

        self.btn_fog = QtWidgets.QPushButton("Add Fog")
        self.btn_fog.setToolTip(
            "Add atmosphere volume (Karma/Arnold auto-detected)")
        self.btn_fog.setCheckable(True)
        self.btn_fog.clicked.connect(self._on_fog_toggled)

        self.btn_lens = QtWidgets.QPushButton(
            "Lens Effects")
        self.btn_lens.setToolTip("Add bloom, glare, vignette")
        self.btn_lens.clicked.connect(self.request_lens_fx.emit)

        self.btn_linking = QtWidgets.QPushButton(
            "Light Linking")
        self.btn_linking.setToolTip(
            "Open light linking matrix (no extra nodes)")
        self.btn_linking.clicked.connect(self.request_light_linking.emit)

        row2.addWidget(self.btn_fog)
        row2.addWidget(self.btn_lens)
        row2.addWidget(self.btn_linking)
        main_layout.addLayout(row2)

    def _on_fog_toggled(self, checked):
        if checked:
            self.btn_fog.setText("Remove Fog")
            self.request_fog.emit()
        else:
            self.btn_fog.setText("Add Fog")
            self.request_remove_fog.emit()


class LightLinkingDialog(QtWidgets.QDialog):
    """
    Light linking matrix dialog. Edits USD collection relationships
    directly — no extra Solaris nodes needed.
    """

    def __init__(self, backend=None, parent=None):
        super().__init__(parent)
        self.backend = backend
        self.setWindowTitle("Crucible — Light Linking Manager")
        self.resize(700, 500)
        self._build_ui()

    def _build_ui(self):
        layout = QtWidgets.QVBoxLayout(self)

        # Info
        info = QtWidgets.QLabel(
            "Check/uncheck cells to control which lights illuminate "
            "which objects.\nEdits USD collections directly — "
            "no extra lightlinker nodes."
        )
        info.setObjectName("subtle")
        info.setWordWrap(True)
        layout.addWidget(info)

        # Matrix table
        self.matrix = QtWidgets.QTableWidget()
        self.matrix.setAlternatingRowColors(True)
        layout.addWidget(self.matrix)

        # Buttons
        btn_row = QtWidgets.QHBoxLayout()
        self.btn_refresh = QtWidgets.QPushButton("Refresh")
        self.btn_refresh.clicked.connect(self.refresh_matrix)
        self.btn_all = QtWidgets.QPushButton("Link All")
        self.btn_all.clicked.connect(self._link_all)
        self.btn_none = QtWidgets.QPushButton("Unlink All")
        self.btn_none.clicked.connect(self._unlink_all)
        self.btn_close = QtWidgets.QPushButton("Close")
        self.btn_close.clicked.connect(self.close)

        btn_row.addWidget(self.btn_refresh)
        btn_row.addWidget(self.btn_all)
        btn_row.addWidget(self.btn_none)
        btn_row.addStretch()
        btn_row.addWidget(self.btn_close)
        layout.addLayout(btn_row)

        self.refresh_matrix()

    def refresh_matrix(self):
        """Populate the light × object matrix."""
        if not self.backend:
            self._populate_demo()
            return

        lights = self.backend.discover_lights()
        if not lights:
            return

        # Collect actual scene geometry objects, filtering out system prims
        all_objects = set()
        exclude_prefixes = (
            "/Render", "/Materials", "/CrucibleLightStudio",
            "/HoudiniLayerInfo", "/cameras", "/Lights"
        )
        
        try:
            hda = self.backend._get_hda()
            if hda:
                # Get the stage from the node currently providing the output
                out_node = hda.displayNode()
                stage = out_node.stage() if out_node else hda.stage()
                
                if stage:
                    from pxr import UsdGeom
                    for prim in stage.Traverse():
                        path_str = str(prim.GetPath())
                        
                        # Skip system/internal paths
                        if any(path_str.startswith(p) for p in exclude_prefixes):
                            continue
                            
                        # Only include actual renderable geometry (Imageables)
                        if prim.IsA(UsdGeom.Imageable):
                            # Exclude Cameras
                            if prim.IsA(UsdGeom.Camera):
                                continue
                            # Exclude Lights
                            type_name = prim.GetTypeName()
                            if "Light" in type_name:
                                continue
                            
                            # Add to objects list
                            all_objects.add(path_str)
        except Exception as e:
            import logging
            logging.error("Light linking traversal error: %s", e)
        
        # Fallback: query light links if no stage traversal worked
        if not all_objects:
            for ld in lights:
                links = self.backend.get_light_links(ld.prim_path)
                for obj_path in links:
                    if not any(obj_path.startswith(p) for p in exclude_prefixes):
                        all_objects.add(obj_path)

        objects = sorted(all_objects)
        light_names = [ld.name for ld in lights]

        self.matrix.setRowCount(len(lights))
        self.matrix.setColumnCount(len(objects))
        self.matrix.setVerticalHeaderLabels(light_names)

        # Shorten object names for column headers
        short_names = [p.split("/")[-1] for p in objects]
        self.matrix.setHorizontalHeaderLabels(short_names)

        for row, ld in enumerate(lights):
            for col, obj_path in enumerate(objects):
                chk = QtWidgets.QCheckBox()
                chk.setChecked(True)  # Default: all linked
                chk.stateChanged.connect(
                    lambda state, lp=ld.prim_path, op=obj_path:
                        self._on_link_changed(lp, op, bool(state))
                )
                container = QtWidgets.QWidget()
                cl = QtWidgets.QHBoxLayout(container)
                cl.addWidget(chk)
                cl.setAlignment(QtCore.Qt.AlignCenter)
                cl.setContentsMargins(0, 0, 0, 0)
                self.matrix.setCellWidget(row, col, container)

    def _populate_demo(self):
        """Demo data when no backend."""
        lights = ["Key_Light", "Fill_Light", "Rim_Light"]
        objects = ["character", "environment", "props"]

        self.matrix.setRowCount(len(lights))
        self.matrix.setColumnCount(len(objects))
        self.matrix.setVerticalHeaderLabels(lights)
        self.matrix.setHorizontalHeaderLabels(objects)

        for row in range(len(lights)):
            for col in range(len(objects)):
                chk = QtWidgets.QCheckBox()
                chk.setChecked(True)
                container = QtWidgets.QWidget()
                cl = QtWidgets.QHBoxLayout(container)
                cl.addWidget(chk)
                cl.setAlignment(QtCore.Qt.AlignCenter)
                cl.setContentsMargins(0, 0, 0, 0)
                self.matrix.setCellWidget(row, col, container)

    def _on_link_changed(self, light_path, object_path, linked):
        if self.backend:
            self.backend.set_light_link(light_path, object_path, linked)

    def _link_all(self):
        for row in range(self.matrix.rowCount()):
            for col in range(self.matrix.columnCount()):
                w = self.matrix.cellWidget(row, col)
                if w:
                    chk = w.findChild(QtWidgets.QCheckBox)
                    if chk:
                        chk.setChecked(True)

    def _unlink_all(self):
        for row in range(self.matrix.rowCount()):
            for col in range(self.matrix.columnCount()):
                w = self.matrix.cellWidget(row, col)
                if w:
                    chk = w.findChild(QtWidgets.QCheckBox)
                    if chk:
                        chk.setChecked(False)
