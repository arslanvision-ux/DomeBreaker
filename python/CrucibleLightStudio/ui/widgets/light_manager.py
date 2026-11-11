# -*- coding: utf-8 -*-
"""
Crucible Light Studio - Light Manager Widget
==============================================
The core spreadsheet-style light manager. Lists all USD lights on the
stage with inline controls for exposure, color, texture, solo, mute,
and energy-conserving scale.
"""

try:
    from PySide2 import QtWidgets, QtCore, QtGui
except ImportError:
    from PySide6 import QtWidgets, QtCore, QtGui

import logging

logger = logging.getLogger("CrucibleLightStudio.ui.light_manager")


# Column indices
COL_ENABLE = 0
COL_SOLO = 1
COL_NAME = 2
COL_TYPE = 3
COL_ANIM = 4
COL_DELETE = 5
NUM_COLS = 6

HEADERS = ["", "S", "Light Name", "Type", "K", "X"]

class LightPropertiesWidget(QtWidgets.QWidget):
    param_changed = QtCore.Signal(str, object)
    texture_changed = QtCore.Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QtWidgets.QFormLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)
        
        self.exposure = ExposureWidget()
        self.exposure.value_changed.connect(lambda v: self.param_changed.emit("exposure", v))
        
        self.color = ColorButton()
        self.color.color_changed.connect(lambda c: self.param_changed.emit("color", c))
        
        self.temperature = TemperatureWidget()
        self.temperature.enable_changed.connect(lambda v: self.param_changed.emit("use_temperature", v))
        self.temperature.value_changed.connect(lambda v: self.param_changed.emit("temperature", v))
        
        # Texture with dropdown + browse button
        tex_widget = QtWidgets.QWidget()
        tex_lay = QtWidgets.QHBoxLayout(tex_widget)
        tex_lay.setContentsMargins(0, 0, 0, 0)
        tex_lay.setSpacing(4)
        
        self.texture = QtWidgets.QComboBox()
        self.texture.addItem("None")
        self.texture.currentTextChanged.connect(self._on_tex_changed)
        tex_lay.addWidget(self.texture, 1)
        
        self.btn_browse_tex = QtWidgets.QPushButton("...")
        self.btn_browse_tex.setFixedSize(26, 22)
        self.btn_browse_tex.setToolTip("Browse for texture map (.exr, .hdr, .rat, .png, .jpg)")
        self.btn_browse_tex.clicked.connect(self._on_browse_texture)
        tex_lay.addWidget(self.btn_browse_tex)
        
        # Multipliers (expanded max_val to 5.0 for professional artistic range)
        self.diffuse = MultiplierWidget(value=1.0, min_val=0.0, max_val=5.0)
        self.diffuse.value_changed.connect(lambda v: self.param_changed.emit("diffuse", v))
        
        self.specular = MultiplierWidget(value=1.0, min_val=0.0, max_val=5.0)
        self.specular.value_changed.connect(lambda v: self.param_changed.emit("specular", v))
        
        self.sss = MultiplierWidget(value=1.0, min_val=0.0, max_val=5.0)
        self.sss.value_changed.connect(lambda v: self.param_changed.emit("sss", v))
        
        self.volume = MultiplierWidget(value=1.0, min_val=0.0, max_val=5.0)
        self.volume.value_changed.connect(lambda v: self.param_changed.emit("volume", v))
        
        self.fx_mode = QtWidgets.QComboBox()
        self.fx_mode.addItems(["none", "fire", "neon", "tv", "strobe"])
        self.fx_mode.currentTextChanged.connect(lambda v: self.param_changed.emit("fx_mode", v))
        
        layout.addRow("Exposure", self.exposure)
        layout.addRow("Color", self.color)
        layout.addRow("Temperature", self.temperature)
        layout.addRow("Texture", tex_widget)
        
        # Multipliers
        layout.addRow("Diffuse", self.diffuse)
        layout.addRow("Specular", self.specular)
        layout.addRow("SSS", self.sss)
        layout.addRow("Volume", self.volume)
        
        layout.addRow("FX Mode", self.fx_mode)
        
        self.setEnabled(False)

    def _on_browse_texture(self):
        file_path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Select Light Texture", "",
            "Texture Files (*.exr *.hdr *.rat *.png *.jpg *.jpeg *.tif *.tiff);;All Files (*.*)"
        )
        if file_path:
            import os
            tex_name = os.path.basename(file_path)
            idx = self.texture.findText(tex_name)
            if idx >= 0:
                self.texture.setItemData(idx, file_path, QtCore.Qt.UserRole)
                self.texture.setCurrentIndex(idx)
            else:
                self.texture.addItem(tex_name, file_path)
                self.texture.setCurrentIndex(self.texture.count() - 1)
            self.texture_changed.emit(file_path)

    def _on_tex_changed(self, text):
        if text == "None":
            self.texture_changed.emit("")
        else:
            idx = self.texture.findText(text)
            if idx >= 0:
                path = self.texture.itemData(idx, QtCore.Qt.UserRole)
                if path:
                    self.texture_changed.emit(path)

    def update_from_light(self, light_data):
        if not light_data:
            self.setEnabled(False)
            return
            
        self.setEnabled(True)
        
        self.blockSignals(True)
        self.exposure.blockSignals(True)
        self.color.blockSignals(True)
        self.temperature.blockSignals(True)
        self.texture.blockSignals(True)
        self.diffuse.blockSignals(True)
        self.specular.blockSignals(True)
        self.sss.blockSignals(True)
        self.volume.blockSignals(True)
        self.fx_mode.blockSignals(True)
        
        self.exposure.spin.setValue(light_data.exposure)
        self.exposure.slider.setValue(int(light_data.exposure * 10))
        
        self.color._color = light_data.color
        self.color._update_swatch()
        
        self.temperature.cb_enable.setChecked(light_data.use_temperature)
        self.temperature.spin.setValue(int(light_data.temperature))
        self.temperature.slider.setValue(int(light_data.temperature))
        
        self.diffuse.set_value(light_data.diffuse)
        self.specular.set_value(light_data.specular)
        self.sss.set_value(getattr(light_data, "sss", 1.0))
        self.volume.set_value(getattr(light_data, "volume", 1.0))
        
        if hasattr(light_data, "fx_mode"):
            self.fx_mode.setCurrentText(light_data.fx_mode)
            
        self.texture.clear()
        self.texture.addItem("None")
        if light_data.texture_path:
            import os
            self.texture.addItem(os.path.basename(light_data.texture_path))
            self.texture.setItemData(1, light_data.texture_path, QtCore.Qt.UserRole)
            self.texture.setCurrentIndex(1)
        else:
            self.texture.setCurrentIndex(0)
            
        self.exposure.blockSignals(False)
        self.color.blockSignals(False)
        self.temperature.blockSignals(False)
        self.texture.blockSignals(False)
        self.diffuse.blockSignals(False)
        self.specular.blockSignals(False)
        self.sss.blockSignals(False)
        self.volume.blockSignals(False)
        self.fx_mode.blockSignals(False)
        self.blockSignals(False)

class MultiplierWidget(QtWidgets.QWidget):
    """Generic slider + spinbox for multiplier values (Diffuse, Specular, SSS)."""
    value_changed = QtCore.Signal(float)

    def __init__(self, value=1.0, min_val=0.0, max_val=10.0, parent=None):
        super().__init__(parent)
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(4, 0, 4, 0)
        layout.setSpacing(4)

        self.slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.slider.setRange(int(min_val * 100), int(max_val * 100))
        self.slider.setValue(int(value * 100))

        self.spin = QtWidgets.QDoubleSpinBox()
        self.spin.setRange(min_val, max_val)
        self.spin.setSingleStep(0.01)
        self.spin.setDecimals(2)
        self.spin.setValue(value)
        self.spin.setButtonSymbols(QtWidgets.QAbstractSpinBox.NoButtons)
        self.spin.setFixedWidth(55)

        layout.addWidget(self.slider, stretch=1)
        layout.addWidget(self.spin)

        self.slider.valueChanged.connect(self._slider_moved)
        self.spin.valueChanged.connect(self._spin_changed)

    def _slider_moved(self, val):
        self.spin.blockSignals(True)
        self.spin.setValue(val / 100.0)
        self.spin.blockSignals(False)
        self.value_changed.emit(val / 100.0)

    def _spin_changed(self, val):
        self.slider.blockSignals(True)
        self.slider.setValue(int(val * 100))
        self.slider.blockSignals(False)
        self.value_changed.emit(val)
        
    def set_value(self, val):
        self.spin.setValue(val)
        self.slider.setValue(int(val * 100))

    def get_value(self):
        return self.spin.value()

class ExposureWidget(QtWidgets.QWidget):
    """Combined slider + spinbox for exposure control."""

    value_changed = QtCore.Signal(float)

    def __init__(self, value=0.0, parent=None):
        super().__init__(parent)
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(4, 0, 4, 0)
        layout.setSpacing(4)

        self.slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.slider.setRange(-100, 100)
        self.slider.setValue(int(value * 10))

        self.spin = QtWidgets.QDoubleSpinBox()
        self.spin.setRange(-10.0, 10.0)
        self.spin.setSingleStep(0.1)
        self.spin.setDecimals(2)
        self.spin.setValue(value)
        self.spin.setButtonSymbols(QtWidgets.QAbstractSpinBox.NoButtons)
        self.spin.setFixedWidth(55)

        layout.addWidget(self.slider, stretch=1)
        layout.addWidget(self.spin)

        # Bidirectional sync
        self.slider.valueChanged.connect(self._slider_moved)
        self.spin.valueChanged.connect(self._spin_changed)

    def _slider_moved(self, val):
        self.spin.blockSignals(True)
        self.spin.setValue(val / 10.0)
        self.spin.blockSignals(False)
        self.value_changed.emit(val / 10.0)

    def _spin_changed(self, val):
        self.slider.blockSignals(True)
        self.slider.setValue(int(val * 10))
        self.slider.blockSignals(False)
        self.value_changed.emit(val)

    def get_value(self):
        return self.spin.value()


class TemperatureWidget(QtWidgets.QWidget):
    """Combined checkbox and slider + spinbox for temperature control."""

    enable_changed = QtCore.Signal(bool)
    value_changed = QtCore.Signal(float)

    def __init__(self, enabled=False, value=6500, parent=None):
        super().__init__(parent)
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(4, 0, 4, 0)
        layout.setSpacing(4)

        self.cb_enable = QtWidgets.QCheckBox()
        self.cb_enable.setChecked(enabled)
        self.cb_enable.setToolTip("Enable Color Temperature")

        self.slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.slider.setRange(1000, 20000)
        self.slider.setValue(int(value))

        self.spin = QtWidgets.QSpinBox()
        self.spin.setRange(1000, 20000)
        self.spin.setSingleStep(100)
        self.spin.setValue(int(value))
        self.spin.setSuffix("K")
        self.spin.setButtonSymbols(QtWidgets.QAbstractSpinBox.NoButtons)
        self.spin.setFixedWidth(55)

        layout.addWidget(self.cb_enable)
        layout.addWidget(self.slider, stretch=1)
        layout.addWidget(self.spin)

        self.slider.setEnabled(enabled)
        self.spin.setEnabled(enabled)

        # Bidirectional sync
        self.cb_enable.toggled.connect(self._on_toggled)
        self.slider.valueChanged.connect(self._slider_moved)
        self.spin.valueChanged.connect(self._spin_changed)

    def _on_toggled(self, checked):
        self.slider.setEnabled(checked)
        self.spin.setEnabled(checked)
        self.enable_changed.emit(checked)

    def _slider_moved(self, val):
        self.spin.blockSignals(True)
        self.spin.setValue(val)
        self.spin.blockSignals(False)
        self.value_changed.emit(val)

    def _spin_changed(self, val):
        self.slider.blockSignals(True)
        self.slider.setValue(val)
        self.slider.blockSignals(False)
        self.value_changed.emit(val)

    def get_value(self):
        return self.spin.value()


class ColorButton(QtWidgets.QPushButton):
    """Button that shows a color swatch and opens a color picker."""

    color_changed = QtCore.Signal(tuple)

    def __init__(self, color=(1.0, 1.0, 1.0), parent=None):
        super().__init__(parent)
        self._color = color
        self.setFixedSize(36, 22)
        self._update_swatch()
        self.clicked.connect(self._pick_color)

    def _update_swatch(self):
        r = int(min(self._color[0], 1.0) * 255)
        g = int(min(self._color[1], 1.0) * 255)
        b = int(min(self._color[2], 1.0) * 255)
        self.setStyleSheet(
            f"background-color: rgb({r}, {g}, {b}); "
            f"border: 1px solid #555; border-radius: 3px;"
        )

    def _pick_color(self):
        r, g, b = self._color
        initial = QtGui.QColor.fromRgbF(min(r, 1.0), min(g, 1.0),
                                         min(b, 1.0))
        color = QtWidgets.QColorDialog.getColor(
            initial, self, "Pick Light Color",
            QtWidgets.QColorDialog.ShowAlphaChannel
        )
        if color.isValid():
            self._color = (color.redF(), color.greenF(), color.blueF())
            self._update_swatch()
            self.color_changed.emit(self._color)

    def get_color(self):
        return self._color


class LightManagerWidget(QtWidgets.QWidget):
    """
    Spreadsheet-style manager listing all scene lights with inline
    editing capabilities.
    """

    # Signals
    light_selected = QtCore.Signal(str)  # prim_path
    request_refresh = QtCore.Signal()
    gradient_builder_requested = QtCore.Signal()

    def __init__(self, backend=None, parent=None):
        super().__init__(parent)
        self.backend = backend
        self._soloed_path = None
        self._build_ui()

    def _build_ui(self):
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        # ---- Toolbar ----
        toolbar = QtWidgets.QHBoxLayout()
        toolbar.setSpacing(6)

        self.btn_add = QtWidgets.QPushButton("+ Add Light")
        self.btn_add.setObjectName("accent_btn")
        self.btn_add.setToolTip("Create a new light")
        self.btn_add.clicked.connect(self._on_add_light)

        self.btn_remove = QtWidgets.QPushButton("- Remove")
        self.btn_remove.setObjectName("danger_btn")
        self.btn_remove.setToolTip("Delete selected light")
        self.btn_remove.clicked.connect(self._on_remove_light)

        self.btn_duplicate = QtWidgets.QPushButton("Duplicate")
        self.btn_duplicate.setToolTip("Duplicate selected light(s)")
        self.btn_duplicate.clicked.connect(self._on_duplicate_light)

        self.btn_save_preset = QtWidgets.QPushButton("Save Preset")
        self.btn_save_preset.setToolTip("Save selected light(s) as a preset")
        self.btn_save_preset.clicked.connect(self._on_save_preset)

        self.btn_gradient = QtWidgets.QPushButton("Custom Gradient")
        self.btn_gradient.setToolTip("Build a custom procedural gradient scrim")
        self.btn_gradient.clicked.connect(self.gradient_builder_requested.emit)

        self.btn_refresh = QtWidgets.QPushButton("Refresh")
        self.btn_refresh.setToolTip("Re-scan stage for lights")
        self.btn_refresh.clicked.connect(self.refresh_lights)

        toolbar.addWidget(self.btn_add)
        toolbar.addWidget(self.btn_remove)
        toolbar.addWidget(self.btn_duplicate)
        toolbar.addWidget(self.btn_save_preset)
        toolbar.addWidget(self.btn_gradient)
        toolbar.addStretch()
        toolbar.addWidget(self.btn_refresh)
        layout.addLayout(toolbar)

        # ---- Splitter ----
        self.splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)

        # ---- Light Table ----
        self.table = QtWidgets.QTableWidget(0, NUM_COLS)
        self.table.setHorizontalHeaderLabels(HEADERS)
        self.table.verticalHeader().hide()
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)

        # Column widths
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(COL_NAME, QtWidgets.QHeaderView.Stretch)
        for col, width in [
            (COL_ENABLE, 30), (COL_SOLO, 28), (COL_TYPE, 100),
            (COL_ANIM, 45), (COL_DELETE, 45)
        ]:
            header.setSectionResizeMode(col, QtWidgets.QHeaderView.Interactive)
            header.resizeSection(col, width)

        self.table.currentCellChanged.connect(self._on_selection_changed)
        self.table.itemChanged.connect(self._on_item_changed)
        
        self.splitter.addWidget(self.table)
        
        # ---- Properties Panel ----
        self.prop_panel = LightPropertiesWidget()
        self.prop_panel.param_changed.connect(self._on_prop_param_changed)
        self.prop_panel.texture_changed.connect(self._on_prop_texture_changed)
        
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.prop_panel)
        scroll.setMinimumWidth(250)
        self.splitter.addWidget(scroll)
        
        self.splitter.setSizes([500, 250])
        layout.addWidget(self.splitter)

    # ------------------------------------------------------------------
    # Populate
    # ------------------------------------------------------------------
    def refresh_lights(self):
        """Query the backend and rebuild the table."""
        self.table.blockSignals(True)
        try:
            # Preserve multi-selection
            selected_paths = set(self._get_selected_paths())

            self.table.setRowCount(0)
            if self.backend:
                lights = self.backend.discover_lights()
                for ld in lights:
                    self._add_light_row(ld)
            else:
                self._add_demo_lights()

            count = self.table.rowCount()

            # Restore multi-selection
            if selected_paths:
                self.table.clearSelection()
                for i in range(count):
                    item = self.table.item(i, COL_NAME)
                    if item and item.data(QtCore.Qt.UserRole) in selected_paths:
                        self.table.setRangeSelected(
                            QtWidgets.QTableWidgetSelectionRange(i, 0, i, NUM_COLS - 1), True
                        )
        finally:
            self.table.blockSignals(False)

    def _add_demo_lights(self):
        """Add demo rows when no backend is available."""
        from CrucibleLightStudio.core.stage_manager import LightData
        demos = [
            LightData(prim_path="/lights/DomeLight_1",
                       light_type="DomeLight", name="DomeLight_1",
                       exposure=0.0, temperature=6500,
                       use_temperature=True),
            LightData(prim_path="/lights/Key_Light",
                       light_type="RectLight", name="Key_Light",
                       exposure=2.5, color=(1.0, 0.95, 0.9)),
            LightData(prim_path="/lights/Fill_Light",
                       light_type="RectLight", name="Fill_Light",
                       exposure=-1.0, color=(0.8, 0.85, 1.0)),
            LightData(prim_path="/lights/Rim_Light",
                       light_type="DiskLight", name="Rim_Light",
                       exposure=3.0, color=(1.0, 0.7, 0.3)),
        ]
        for ld in demos:
            self._add_light_row(ld)

    # ------------------------------------------------------------------
    # Row Building
    # ------------------------------------------------------------------
    def _add_light_row(self, light_data):
        """Insert a single light as a table row with inline widgets."""
        row = self.table.rowCount()
        self.table.insertRow(row)

        # Store prim path as row data
        path = light_data.prim_path

        # Enable checkbox
        chk = QtWidgets.QCheckBox()
        chk.setChecked(light_data.enabled)
        chk.stateChanged.connect(
            lambda state, p=path: self._on_enable_toggled(p, state))
        chk_container = self._center_widget(chk)
        self.table.setCellWidget(row, COL_ENABLE, chk_container)

        # Solo button
        solo = QtWidgets.QPushButton("S")
        solo.setObjectName("solo_btn")
        solo.setCheckable(True)
        solo.setToolTip("Solo this light (isolate)")
        solo.toggled.connect(
            lambda checked, p=path: self._on_solo_toggled(p, checked))
        self.table.setCellWidget(row, COL_SOLO, solo)

        # Initialize selectable items for all columns so row selection is complete
        for col in range(NUM_COLS):
            if col != COL_NAME:
                col_item = QtWidgets.QTableWidgetItem("")
                col_item.setFlags(QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsSelectable)
                self.table.setItem(row, col, col_item)

        # Name
        name_item = QtWidgets.QTableWidgetItem(light_data.name)
        name_item.setData(QtCore.Qt.UserRole, path)
        name_item.setData(QtCore.Qt.UserRole + 1, light_data)
        self.table.setItem(row, COL_NAME, name_item)

        # Type
        type_combo = QtWidgets.QComboBox()
        type_combo.addItems(["RectLight", "DiskLight", "SphereLight", "CylinderLight", "DistantLight", "DomeLight"])
        # Select current type
        idx = type_combo.findText(light_data.light_type, QtCore.Qt.MatchContains)
        if idx >= 0:
            type_combo.setCurrentIndex(idx)
        else:
            if light_data.light_type:
                type_combo.addItem(light_data.light_type)
                type_combo.setCurrentText(light_data.light_type)
        type_combo.currentTextChanged.connect(
            lambda val, p=path: self._on_type_changed(p, val))
        self.table.setCellWidget(row, COL_TYPE, type_combo)

        # Keyframe Button
        is_kf = self._is_light_keyframed(path)
        btn_anim = QtWidgets.QPushButton("K")
        btn_anim.setToolTip("Keyframe Exposure & Color (Click to set keyframe at current frame)")
        btn_anim.setFixedSize(24, 24)
        btn_anim.setStyleSheet(self._get_k_btn_stylesheet(is_kf))
        btn_anim.clicked.connect(lambda *args, p=path, b=btn_anim: self._on_keyframe_light(p, b))
        self.table.setCellWidget(row, COL_ANIM, self._center_widget(btn_anim))

        # Delete Button
        btn_del = QtWidgets.QPushButton("X")
        btn_del.setToolTip("Delete Light")
        btn_del.setFixedSize(24, 24)
        btn_del.setStyleSheet("""
            QPushButton {
                background-color: #ff4444;
                color: black;
                font-weight: bold;
                border-radius: 3px;
                padding: 0px;
                margin: 0px;
            }
            QPushButton:hover {
                background-color: #ff6666;
            }
        """)
        btn_del.clicked.connect(lambda *args, p=path: self._on_delete_light_by_path(p))
        self.table.setCellWidget(row, COL_DELETE, self._center_widget(btn_del))

        # Row height
        self.table.setRowHeight(row, 30)

    def _center_widget(self, widget):
        """Wrap a widget in a container with centered alignment."""
        container = QtWidgets.QWidget()
        layout = QtWidgets.QHBoxLayout(container)
        layout.addWidget(widget)
        layout.setAlignment(QtCore.Qt.AlignCenter)
        layout.setContentsMargins(0, 0, 0, 0)
        return container
        
    def _get_k_btn_stylesheet(self, is_keyframed):
        if is_keyframed:
            return """
                QPushButton {
                    background-color: #ffd700;
                    color: black;
                    font-weight: bold;
                    border-radius: 3px;
                    padding: 0px;
                }
                QPushButton:hover { background-color: #ffea00; }
            """
        else:
            return """
                QPushButton {
                    background-color: #44aa44;
                    color: white;
                    font-weight: bold;
                    border-radius: 3px;
                    padding: 0px;
                }
                QPushButton:hover { background-color: #55cc55; }
            """

    # ------------------------------------------------------------------
    # Callbacks
    # ------------------------------------------------------------------
    def _get_selected_paths(self):
        """Return a list of prim_paths for all currently selected rows."""
        paths = []
        rows = set()
        for item in self.table.selectedItems():
            rows.add(item.row())
        if self.table.selectionModel():
            for idx in self.table.selectionModel().selectedRows():
                rows.add(idx.row())

        for r in sorted(rows):
            item = self.table.item(r, COL_NAME)
            if item:
                path = item.data(QtCore.Qt.UserRole)
                if path and path not in paths:
                    paths.append(path)
        return paths

    def _is_light_keyframed(self, prim_path):
        if not self.backend: return False
        node = self.backend._find_light_node(prim_path) if hasattr(self.backend, "_find_light_node") else None
        if not node: return False
        try:
            from CrucibleLightStudio.core.backend_houdini import _get_lop_parm
            for param_name in ["exposure", "color", "intensity"]:
                p = _get_lop_parm(node, param_name)
                if p:
                    for sub_p in p.tuple():
                        if len(sub_p.keyframes()) > 0 or sub_p.isTimeDependent():
                            return True
        except Exception:
            pass
        return False

    def _on_keyframe_light(self, prim_path, btn=None):
        paths = self._get_selected_paths()
        if prim_path not in paths or len(paths) <= 1:
            target_paths = [prim_path]
        else:
            target_paths = paths
            
        if self.backend:
            for path in target_paths:
                self.backend.keyframe_light(path)
                
        if btn:
            btn.setStyleSheet(self._get_k_btn_stylesheet(True))

    def _on_save_preset(self):
        import os
        from CrucibleLightStudio.core import preset_manager

        paths = self._get_selected_paths()
        if not paths:
            # If no lights are selected, offer to save all lights currently in the scene
            all_paths = []
            for row in range(self.table.rowCount()):
                item = self.table.item(row, COL_NAME)
                if item:
                    p = item.data(QtCore.Qt.UserRole)
                    if p: all_paths.append(p)
                    
            if not all_paths:
                QtWidgets.QMessageBox.warning(self, "No Lights Available", "There are no lights in the scene to save.")
                return
                
            reply = QtWidgets.QMessageBox.question(
                self, "Save All Lights as Preset",
                f"No specific lights are selected.\n\nWould you like to save all {len(all_paths)} light(s) in the scene into a new preset folder?",
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No
            )
            if reply == QtWidgets.QMessageBox.Yes:
                paths = all_paths
            else:
                return

        default_name = "Practical_Lights_Rig" if len(paths) > 1 else paths[0].rstrip("/").split("/")[-1] + "_Preset"
        name, ok = QtWidgets.QInputDialog.getText(
            self, "Save Presets",
            f"Enter folder name to store {len(paths)} selected light(s):",
            text=default_name
        )
        if not ok or not name.strip():
            return

        folder_name = name.strip()
        
        lights = []
        if self.backend:
            all_lights = self.backend.discover_lights()
            for ld in all_lights:
                if ld.prim_path in paths:
                    lights.append(vars(ld))
                    
        if not lights:
            QtWidgets.QMessageBox.warning(self, "Save Preset Error", "Could not retrieve light data for the selected light(s).")
            return

        # Always save into a dedicated folder containing the selected light(s) and combined rig
        preset_dir = preset_manager.save_preset_folder(folder_name, lights)
        
        # Refresh the UI to show the new preset in the library
        self.request_refresh.emit()
        
        QtWidgets.QMessageBox.information(
            self, "Preset Folder Saved",
            f"Successfully created preset folder:\n'{os.path.basename(preset_dir)}'\n\nContaining {len(lights)} light(s). Available under 'Presets' in the Asset Library!"
        )

    def _on_delete_light_by_path(self, prim_path):
        paths = self._get_selected_paths()
        if prim_path not in paths or len(paths) <= 1:
            target_paths = [prim_path]
            light_name = prim_path.rstrip("/").split("/")[-1]
            msg = f"Are you sure you want to delete '{light_name}'?"
        else:
            target_paths = paths
            msg = f"Are you sure you want to delete {len(target_paths)} selected light(s)?"
            
        reply = QtWidgets.QMessageBox.question(
            self, "Delete Light",
            msg,
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No
        )
        if reply == QtWidgets.QMessageBox.Yes:
            if self.backend:
                for path in target_paths:
                    self.backend.remove_light(path)
            self.refresh_lights()

    def _on_duplicate_light(self):
        paths = self._get_selected_paths()
        if not paths: return

        new_paths = []
        if self.backend:
            for path in paths:
                if hasattr(self.backend, "duplicate_light"):
                    new_p = self.backend.duplicate_light(path)
                    if new_p:
                        new_paths.append(new_p)
            self.refresh_lights()

            # Select newly duplicated lights in table
            if new_paths:
                self.table.clearSelection()
                for r in range(self.table.rowCount()):
                    item = self.table.item(r, COL_NAME)
                    if item and item.data(QtCore.Qt.UserRole) in new_paths:
                        self.table.setRangeSelected(
                            QtWidgets.QTableWidgetSelectionRange(r, 0, r, NUM_COLS - 1), True
                        )

    def _on_add_light(self):
        """Show light type picker and create a new light."""
        types = ["RectLight", "DiskLight", "SphereLight", "CylinderLight",
                 "DistantLight", "DomeLight"]
        choice, ok = QtWidgets.QInputDialog.getItem(
            self, "Add Light", "Light Type:", types, 0, False)
        if ok and choice:
            if self.backend:
                self.backend.create_light(choice)
            self.refresh_lights()

    def _on_remove_light(self):
        """Delete all currently selected lights."""
        paths = self._get_selected_paths()
        if not paths: return

        reply = QtWidgets.QMessageBox.question(
            self, "Delete Light",
            f"Delete {len(paths)} selected light(s)?",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No
        )
        if reply == QtWidgets.QMessageBox.Yes:
            if self.backend:
                for path in paths:
                    self.backend.remove_light(path)
            self.refresh_lights()

    def _on_enable_toggled(self, prim_path, state):
        if not self.backend: return

        paths = self._get_selected_paths()
        if prim_path not in paths:
            paths = [prim_path]

        self.table.blockSignals(True)
        for path in paths:
            self.backend.set_light_enabled(path, bool(state))
        self.table.blockSignals(False)

        # Update table checkbox state in-place for all affected rows
        for r in range(self.table.rowCount()):
            it = self.table.item(r, COL_NAME)
            if it and it.data(QtCore.Qt.UserRole) in paths:
                ld = it.data(QtCore.Qt.UserRole + 1)
                if ld: ld.enabled = bool(state)
                w = self.table.cellWidget(r, COL_ENABLE)
                if w:
                    c = w.findChild(QtWidgets.QCheckBox)
                    if c:
                        c.blockSignals(True)
                        c.setChecked(bool(state))
                        c.blockSignals(False)

    def _on_type_changed(self, prim_path, new_type):
        if not self.backend: return
        
        paths = self._get_selected_paths()
        if prim_path not in paths:
            paths = [prim_path]
            
        self.table.blockSignals(True)
        for path in paths:
            self.backend.set_light_param(path, "type", new_type)
        self.table.blockSignals(False)
        
        # After changing type, the node might have been recreated, changing its prim path. 
        # A full refresh is required to bind to the new nodes correctly.
        QtCore.QTimer.singleShot(0, self.refresh_lights)

    def _on_solo_toggled(self, prim_path, checked):
        if not self.backend:
            return
        if checked:
            self._soloed_path = prim_path
            lights = self.backend.discover_lights()
            for ld in lights:
                self.backend.set_light_enabled(ld.prim_path,
                                                ld.prim_path == prim_path)
        else:
            self._soloed_path = None
            lights = self.backend.discover_lights()
            for ld in lights:
                self.backend.set_light_enabled(ld.prim_path, True)

    def _on_prop_param_changed(self, param, value):
        if not self.backend: return
        paths = self._get_selected_paths()
        for path in paths:
            self.backend.set_light_param(path, param, value)
            
        # Update local light data objects
        for row in range(self.table.rowCount()):
            item = self.table.item(row, COL_NAME)
            if item and item.data(QtCore.Qt.UserRole) in paths:
                ld = item.data(QtCore.Qt.UserRole + 1)
                if ld:
                    if param == "exposure": ld.exposure = value
                    elif param == "color": ld.color = value
                    elif param == "use_temperature": ld.use_temperature = value
                    elif param == "temperature": ld.temperature = value
                    elif param == "diffuse": ld.diffuse = value
                    elif param == "specular": ld.specular = value
                    elif param == "sss": ld.sss = value
                    elif param == "volume": ld.volume = value
                    elif param == "fx_mode": ld.fx_mode = value

    def _on_prop_texture_changed(self, tex_path):
        if not self.backend: return
        paths = self._get_selected_paths()
        for path in paths:
            self.backend.set_light_param(path, "texture", tex_path)
            self.backend.set_light_param(path, "color", [1.0, 1.0, 1.0])
        QtCore.QTimer.singleShot(0, self.refresh_lights)

    def _on_item_selection_changed(self):
        """Handle multi-selection and update active lights in inspector and DCC."""
        paths = self._get_selected_paths()
        if not paths:
            self.prop_panel.update_from_light(None)
            return

        if self.backend:
            if hasattr(self.backend, "select_lights"):
                self.backend.select_lights(paths)
            elif hasattr(self.backend, "select_light"):
                self.backend.select_light(paths[0])

        # Pick primary light for properties panel
        curr_row = self.table.currentRow()
        primary_item = None
        if curr_row >= 0:
            primary_item = self.table.item(curr_row, COL_NAME)
        if not primary_item and paths:
            for r in range(self.table.rowCount()):
                it = self.table.item(r, COL_NAME)
                if it and it.data(QtCore.Qt.UserRole) in paths:
                    primary_item = it
                    break

        if primary_item:
            ld = primary_item.data(QtCore.Qt.UserRole + 1)
            self.prop_panel.update_from_light(ld)
            self.light_selected.emit(primary_item.data(QtCore.Qt.UserRole))

    def _on_selection_changed(self, current_row, current_col, prev_row, prev_col):
        """Update active light for viewport painting."""
        if current_row < 0:
            self.prop_panel.update_from_light(None)
            return
        item = self.table.item(current_row, COL_NAME)
        if item:
            path = item.data(QtCore.Qt.UserRole)
            ld = item.data(QtCore.Qt.UserRole + 1)
            self.prop_panel.update_from_light(ld)
            self.light_selected.emit(path)
            if self.backend:
                hda = self.backend._get_hda()
                if hda:
                    hda.setUserData("active_light_path", path)
                    # Let the backend handle the selection so the transform handles can appear


    def _on_item_changed(self, item):
        if item.column() == COL_NAME:
            new_name = item.text()
            path = item.data(QtCore.Qt.UserRole)
            if self.backend and path:
                self.table.blockSignals(True)
                try:
                    self.backend.set_light_param(path, "name", new_name)
                finally:
                    self.table.blockSignals(False)
                # Refresh immediately because renaming a node changes its path
                self.refresh_lights()
