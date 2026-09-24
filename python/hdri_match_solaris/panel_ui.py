def _find_crucible_studio_node(stage_node):
    """Find crucible_light_studio HDA node on the stage by type or name."""
    if not stage_node:
        return None
    for child in stage_node.children():
        if child.type().name() == "crucible_light_studio" or child.name().startswith("crucible_light_studio"):
            return child
    return None

try:
    from hdri_match.analysis.hdri_stats import HDRIStats
except ImportError:
    import os, sys
    for cand in [os.environ.get("HDRI_MATCH_PLATE_ROOT", ""), "E:/PROJECTS/HDRI_Match_Plate", "e:/PROJECTS/HDRI_Match_Plate"]:
        if cand and os.path.isdir(cand) and cand not in sys.path:
            sys.path.insert(0, cand)
    try:
        from hdri_match.analysis.hdri_stats import HDRIStats
    except ImportError:
        HDRIStats = None

def from_stats_desc(val):
    if HDRIStats is not None and hasattr(HDRIStats, "cct_to_description"):
        return HDRIStats.cct_to_description(val)
    return ""
"""
DomeBreaker - Python Panel UI for Houdini Solaris.

Provides an interactive calibration interface as a dockable panel inside
Houdini.  The panel allows artists to:
    - Load HDRI and Plate images with thumbnail preview
    - Adjust calibration parameters (EV, Temperature, Tint, Yaw)
    - Run auto-calibration
    - Detect and reposition the sun
    - Create the full LOP network with one click
    - Preview original vs. calibrated HDRI (tone-mapped)

The panel communicates with the LOP network by finding or creating
``hdri_match_*`` nodes in the ``/stage`` context and setting their
parameters directly via the ``hou`` API.

Requires: PySide2 (ships with Houdini 21+)
"""

import os
import sys
import re
import math
import traceback

import hou

try:
    from PySide6 import QtWidgets, QtCore, QtGui
except ImportError:
    from PySide2 import QtWidgets, QtCore, QtGui
import numpy as np

try:
    import hdri_match_solaris.oiio_adapter
except Exception:
    pass

try:
    from hdri_match_solaris import renderer_materials as _rmat
    if _rmat is not None and not hasattr(_rmat, "create_or_update_material_library"):
        import importlib
        _rmat = importlib.reload(_rmat)
except Exception:
    _rmat = None

try:
    from hdri_match_solaris import hdri_room_analyzer as _ana
except Exception:
    _ana = None


def _get_create_or_update_material_library():
    """Safely import or reload create_or_update_material_library from renderer_materials."""
    global _rmat
    try:
        import sys, importlib
        if _rmat is None or not hasattr(_rmat, "create_or_update_material_library"):
            from hdri_match_solaris import renderer_materials
            _rmat = importlib.reload(renderer_materials)
        return getattr(_rmat, "create_or_update_material_library", None)
    except Exception as e:
        print(f"[HDRI Match Solaris] Warning: Could not load create_or_update_material_library: {e}")
        return None

# --- Crucible Light Studio & Drop Target Setup ---
CRUCIBLE_ROOT = os.environ.get("CRUCIBLE_LIGHT_STUDIO_ROOT", r"E:\PROJECTS\CrucibleLightStudio")
CRUCIBLE_PARENT = os.path.dirname(CRUCIBLE_ROOT)
for _p in [CRUCIBLE_PARENT, CRUCIBLE_ROOT]:
    if os.path.exists(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

def _ensure_crucible_environment():
    """Ensure Crucible root is in sys.path and HDA is installed."""
    for _p in [CRUCIBLE_PARENT, CRUCIBLE_ROOT]:
        if os.path.exists(_p) and _p not in sys.path:
            sys.path.insert(0, _p)
    try:
        hda_file = os.path.join(CRUCIBLE_ROOT, "hda", "crucible_light_studio.hda")
        if os.path.exists(hda_file) and hou:
            hou.hda.installFile(hda_file)
    except Exception:
        pass

try:
    from hdri_match_solaris.drop_target import HDRIDropTarget
except Exception:
    class HDRIDropTarget(QtWidgets.QLineEdit):
        file_dropped = QtCore.Signal(str)
        def __init__(self, placeholder="", parent=None):
            super().__init__(parent)
            self.setPlaceholderText(placeholder)

class DropLabel(QtWidgets.QLabel):
    file_dropped = QtCore.Signal(str)
    double_clicked = QtCore.Signal()

    def __init__(self, text, parent=None):
        super().__init__(text, parent)
        self.setAcceptDrops(True)
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.setToolTip("Double-click to open large inspection window | Drag & drop image to replace")

    def mouseDoubleClickEvent(self, event):
        if event.button() == QtCore.Qt.LeftButton:
            self.double_clicked.emit()
        super().mouseDoubleClickEvent(event)
    def dragEnterEvent(self, event):
        mime = event.mimeData()
        if mime.hasUrls() or mime.hasText():
            event.acceptProposedAction()
        else:
            event.ignore()
    def dropEvent(self, event):
        mime = event.mimeData()
        path = None
        if mime.hasUrls():
            for url in mime.urls():
                p = url.toLocalFile()
                if p and os.path.isfile(p):
                    path = p
                    break
        if not path and mime.hasText():
            t = mime.text().strip().strip("\"'")
            if t and os.path.isfile(t):
                path = t
        if path:
            event.acceptProposedAction()
            self.file_dropped.emit(path)
        else:
            event.ignore()


class ClickablePreviewLabel(QtWidgets.QLabel):
    """
    QLabel that emits double_clicked signal on mouse double-click and displays a pointing hand cursor.
    """
    double_clicked = QtCore.Signal()

    def __init__(self, text="", parent=None):
        super().__init__(text, parent)
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.setToolTip("Double-click to open large floating screen view")

    def mouseDoubleClickEvent(self, event):
        if event.button() == QtCore.Qt.LeftButton:
            self.double_clicked.emit()
        super().mouseDoubleClickEvent(event)


class SliderDoubleSpinBox(QtWidgets.QWidget):
    """
    Linked Horizontal Slider and QDoubleSpinBox with smooth interaction.
    Exposes .value(), .setValue(), and .valueChanged(float).
    Supports dynamic range scaling for values exceeding initial upper bound.
    The slider bar covers an intuitive default range, while the spinbox accepts unrestricted
    artist input. Typing or setting values outside current slider bounds automatically
    rescales the slider range to accommodate any dynamic range without clamping.
    """
    valueChanged = QtCore.Signal(float)

    def __init__(self, min_val=0.0, max_val=1.0, step=0.01, default_val=0.0, decimals=2, parent=None):
        super().__init__(parent)
        self._decimals = decimals
        self._scale = float(10 ** decimals)
        self._min_val = float(min_val)
        self._max_val = float(max_val)

        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)

        self.slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.slider.setRange(int(round(self._min_val * self._scale)), int(round(self._max_val * self._scale)))
        self.slider.setSingleStep(max(1, int(round(step * self._scale))))
        self.slider.setPageStep(max(5, int(round(step * 10.0 * self._scale))))
        self.slider.setValue(int(round(default_val * self._scale)))
        self.slider.setStyleSheet("""
            QSlider::groove:horizontal {
                height: 4px;
                background: #232323;
                border-radius: 2px;
            }
            QSlider::sub-page:horizontal {
                background: #e67e22;
                border-radius: 2px;
            }
            QSlider::handle:horizontal {
                background: #cccccc;
                border: 1px solid #181818;
                width: 12px;
                margin-top: -4px;
                margin-bottom: -4px;
                border-radius: 6px;
            }
            QSlider::handle:horizontal:hover {
                background: #ff9d42;
                border-color: #ffb870;
            }
        """)

        self.spin = QtWidgets.QDoubleSpinBox()
        spin_min = min(self._min_val, -1000.0) if self._min_val < 0 else min(0.0, self._min_val)
        spin_max = max(1000.0, self._max_val * 10.0)
        self.spin.setRange(spin_min, spin_max)
        self.spin.setSingleStep(step)
        self.spin.setDecimals(decimals)
        self.spin.setValue(default_val)
        self.spin.setFixedWidth(72)

        lay.addWidget(self.slider, 1)
        lay.addWidget(self.spin, 0)

        self.slider.valueChanged.connect(self._on_slider_changed)
        self.spin.valueChanged.connect(self._on_spin_changed)

    def _on_slider_changed(self, ival):
        val = ival / max(1.0, self._scale)
        if abs(self.spin.value() - val) > (0.5 / max(1.0, self._scale)):
            self.spin.blockSignals(True)
            self.spin.setValue(val)
            self.spin.blockSignals(False)
            self.valueChanged.emit(val)

    def _on_spin_changed(self, val):
        if val > self._max_val:
            self.setRange(self._min_val, max(val * 1.25, val + 1.0))
        elif val < self._min_val:
            self.setRange(min(val, self._min_val - 1.0), self._max_val)
        ival = int(round(val * self._scale))
        if self.slider.value() != ival:
            self.slider.blockSignals(True)
            self.slider.setValue(ival)
            self.slider.blockSignals(False)
        self.valueChanged.emit(val)

    def value(self):
        return float(self.spin.value())

    def setValue(self, val):
        val = float(val)
        if val > self._max_val:
            self.setRange(self._min_val, max(val * 1.25, val + 1.0))
        elif val < self._min_val:
            self.setRange(min(val, self._min_val - 1.0), self._max_val)
        old_val = float(self.spin.value())
        self.spin.blockSignals(True)
        self.spin.setValue(val)
        self.spin.blockSignals(False)
        ival = int(round(val * self._scale))
        self.slider.blockSignals(True)
        self.slider.setValue(ival)
        self.slider.blockSignals(False)
        if abs(old_val - val) > (0.5 / max(1.0, self._scale)):
            self.valueChanged.emit(val)

    def setRange(self, min_val, max_val):
        self._min_val = float(min_val)
        self._max_val = float(max_val)
        spin_min = min(self._min_val, -1000.0) if self._min_val < 0 else min(0.0, self._min_val)
        spin_max = max(1000.0, self._max_val * 10.0)
        self.spin.setRange(spin_min, spin_max)
        self.slider.setRange(int(round(self._min_val * self._scale)), int(round(self._max_val * self._scale)))

    def setSingleStep(self, step):
        self.spin.setSingleStep(step)
        self.slider.setSingleStep(max(1, int(round(step * self._scale))))

    def setDecimals(self, decimals):
        self._decimals = decimals
        self._scale = float(10 ** decimals)
        self.spin.setDecimals(decimals)
        self.setRange(self._min_val, self._max_val)

    def setToolTip(self, tip):
        super().setToolTip(tip)
        self.slider.setToolTip(tip)
        self.spin.setToolTip(tip)

class ColorPickerWidget(QtWidgets.QWidget):
    """
    Visual color swatch + reset button + live R, G, B tint sliders.
    Emits colorChanged(tuple).
    """
    colorChanged = QtCore.Signal(tuple)

    def __init__(self, default_color=(1.0, 1.0, 1.0), label="Color", parent=None):
        super().__init__(parent)
        self._color = list(default_color)
        self._label = label

        main_lay = QtWidgets.QVBoxLayout(self)
        main_lay.setContentsMargins(0, 0, 0, 0)
        main_lay.setSpacing(3)

        top_bar = QtWidgets.QHBoxLayout()
        top_bar.setContentsMargins(0, 0, 0, 0)
        top_bar.setSpacing(6)

        self.btn_swatch = QtWidgets.QPushButton()
        self.btn_swatch.setFixedHeight(22)
        self.btn_swatch.setCursor(QtCore.Qt.PointingHandCursor)
        self.btn_swatch.setToolTip(f"Click to open Color Picker dialog for {self._label}")

        self.btn_toggle_rgb = QtWidgets.QPushButton("▸ RGB")
        self.btn_toggle_rgb.setCheckable(True)
        self.btn_toggle_rgb.setChecked(False)
        self.btn_toggle_rgb.setFixedHeight(22)
        self.btn_toggle_rgb.setToolTip(f"Expand / collapse RGB channel sliders for {self._label}")
        self.btn_toggle_rgb.setStyleSheet(
            "QPushButton { font-size: 10px; font-weight: bold; background: #262626; color: #aaa; "
            "border-radius: 3px; border: 1px solid #3a3a3a; padding: 2px 6px; } "
            "QPushButton:hover { color: #fff; background: #353535; border-color: #e67e22; } "
            "QPushButton:checked { color: #e67e22; background: #2f2720; border-color: #e67e22; }"
        )

        self.btn_reset = QtWidgets.QPushButton("↺")
        self.btn_reset.setToolTip(f"Reset {self._label} to Neutral White [1.0, 1.0, 1.0]")
        self.btn_reset.setFixedSize(22, 22)
        self.btn_reset.setStyleSheet(
            "QPushButton { font-weight: bold; background: #2b2b2b; color: #888; border-radius: 3px; border: 1px solid #3d3d3d; } "
            "QPushButton:hover { color: #fff; background: #3d3d3d; border-color: #e67e22; }"
        )

        top_bar.addWidget(self.btn_swatch, 1)
        top_bar.addWidget(self.btn_toggle_rgb, 0)
        top_bar.addWidget(self.btn_reset, 0)
        main_lay.addLayout(top_bar)

        self.rgb_box = QtWidgets.QWidget()
        self.rgb_box.setVisible(False)  # Collapsed by default
        rgb_lay = QtWidgets.QVBoxLayout(self.rgb_box)
        rgb_lay.setContentsMargins(0, 2, 0, 0)
        rgb_lay.setSpacing(2)

        self.sld_r = SliderDoubleSpinBox(0.0, 2.0, 0.01, default_color[0], decimals=3)
        self.sld_g = SliderDoubleSpinBox(0.0, 2.0, 0.01, default_color[1], decimals=3)
        self.sld_b = SliderDoubleSpinBox(0.0, 2.0, 0.01, default_color[2], decimals=3)

        for ch_tag, sld, color_hex in [("R", self.sld_r, "#e74c3c"), ("G", self.sld_g, "#2ecc71"), ("B", self.sld_b, "#3498db")]:
            row = QtWidgets.QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(4)
            lbl = QtWidgets.QLabel(ch_tag)
            lbl.setFixedWidth(12)
            lbl.setStyleSheet(f"color: {color_hex}; font-weight: bold; font-size: 11px;")
            row.addWidget(lbl)
            row.addWidget(sld, 1)
            rgb_lay.addLayout(row)

        main_lay.addWidget(self.rgb_box)

        self._update_swatch()

        self.btn_swatch.clicked.connect(self._on_choose_color)
        self.btn_toggle_rgb.toggled.connect(self._on_toggle_rgb)
        self.btn_reset.clicked.connect(self._on_reset)
        self.sld_r.valueChanged.connect(self._on_slider_changed)
        self.sld_g.valueChanged.connect(self._on_slider_changed)
        self.sld_b.valueChanged.connect(self._on_slider_changed)

    def _update_swatch(self):
        r_c = int(np.clip(self._color[0] * 255.0, 0, 255))
        g_c = int(np.clip(self._color[1] * 255.0, 0, 255))
        b_c = int(np.clip(self._color[2] * 255.0, 0, 255))
        text_col = "#000000" if (0.299 * r_c + 0.587 * g_c + 0.114 * b_c) > 128 else "#ffffff"
        hex_code = f"#{r_c:02x}{g_c:02x}{b_c:02x}".upper()
        self.btn_swatch.setText(f"{self._label} {hex_code} ({self._color[0]:.2f}, {self._color[1]:.2f}, {self._color[2]:.2f})")
        self.btn_swatch.setStyleSheet(
            f"QPushButton {{ background-color: rgb({r_c}, {g_c}, {b_c}); color: {text_col}; "
            f"border: 1px solid #444; border-radius: 3px; font-size: 10px; font-weight: bold; padding: 2px 4px; }} "
            f"QPushButton:hover {{ border: 1px solid #e67e22; }}"
        )

    def _on_toggle_rgb(self, checked):
        self.rgb_box.setVisible(checked)
        self.btn_toggle_rgb.setText("▾ RGB" if checked else "▸ RGB")

    def _on_choose_color(self):
        initial = QtGui.QColor(
            int(np.clip(self._color[0] * 255.0, 0, 255)),
            int(np.clip(self._color[1] * 255.0, 0, 255)),
            int(np.clip(self._color[2] * 255.0, 0, 255))
        )
        col = QtWidgets.QColorDialog.getColor(initial, self, f"Select {self._label}")
        if col.isValid():
            self.setColor(col.redF(), col.greenF(), col.blueF())

    def _on_slider_changed(self, _):
        self._color = [float(self.sld_r.value()), float(self.sld_g.value()), float(self.sld_b.value())]
        self._update_swatch()
        self.colorChanged.emit(tuple(self._color))

    def _on_reset(self):
        self.setColor(1.0, 1.0, 1.0)

    def setColor(self, r, g=None, b=None):
        if g is None and b is None and isinstance(r, (list, tuple)):
            r, g, b = r[0], r[1], r[2]
        self._color = [float(r), float(g), float(b)]
        for sld, val in [(self.sld_r, r), (self.sld_g, g), (self.sld_b, b)]:
            sld.blockSignals(True)
            sld.setValue(val)
            sld.blockSignals(False)
        self._update_swatch()
        self.colorChanged.emit(tuple(self._color))

    def color(self):
        return tuple(self._color)

    def r(self): return float(self._color[0])
    def g(self): return float(self._color[1])
    def b(self): return float(self._color[2])


class BackgroundBakeWorker(QtCore.QThread):
    """
    Dedicated background worker that executes NumPy image processing and OpenImageIO
    EXR writing completely off the main Qt / Houdini thread to ensure 60 FPS silky-smooth UI
    responsiveness and instantaneous real-time viewport updates without system hogging.
    """
    bakeFinished = QtCore.Signal(str, float, float, float, float, float, float)
    bakeFailed = QtCore.Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._mutex = QtCore.QMutex()
        self._pending_params = None

    def request_bake(self, params):
        """Thread-safe submission of latest parameter snapshot. Replaces any pending stale request."""
        self._mutex.lock()
        self._pending_params = dict(params)
        self._mutex.unlock()
        if not self.isRunning():
            self.start()

    def run(self):
        import OpenImageIO as oiio

        while True:
            self._mutex.lock()
            params = self._pending_params
            self._pending_params = None
            self._mutex.unlock()

            if params is None:
                break

            try:
                img = params["img"].copy()
                h, w = img.shape[:2]

                ev = float(params["ev"])
                black = float(params["black"])
                temp = float(params["temp"])
                tint = float(params["tint"])
                sat = float(params.get("sat", 1.0))
                contrast = float(params.get("contrast", 1.0))
                horizon_en = bool(params["horizon_en"])
                softclip_en = bool(params["softclip_en"])
                sun_remove_en = bool(params["sun_remove_en"])
                ground_proj_en = bool(params.get("ground_proj_en", False))
                bake_ground_warp = bool(params.get("bake_ground_warp", True))
                extract_inpaint_en = bool(params.get("extract_inpaint_en", False))
                extract_count = int(params.get("extract_count", 3))
                extract_thresh = float(params.get("extract_thresh", 3.0))

                # 1. Base Exposure EV offset
                if ev != 0.0:
                    img *= (2.0 ** ev)

                # 2. Black level offset
                if black != 0.0:
                    img += black

                # 3. White Balance (Color Temperature & Tint)
                if temp != 0.0 or tint != 0.0:
                    r_scale = max(0.01, 1.0 + temp + tint)
                    g_scale = max(0.01, 1.0 - tint)
                    b_scale = max(0.01, 1.0 - temp)
                    lum = (r_scale + g_scale + b_scale) / 3.0
                    img[..., 0] *= (r_scale / lum)
                    img[..., 1] *= (g_scale / lum)
                    img[..., 2] *= (b_scale / lum)

                # 3b. Saturation & Contrast in scene-linear space
                if abs(sat - 1.0) > 1e-4:
                    luma = 0.2126 * img[..., 0] + 0.7152 * img[..., 1] + 0.0722 * img[..., 2]
                    img = luma[..., np.newaxis] + sat * (img - luma[..., np.newaxis])
                if abs(contrast - 1.0) > 1e-4:
                    img = 0.18 * (np.maximum(img, 1e-6) / 0.18) ** contrast

                # 3c. Ground Projection / Parallax Warp
                if ground_proj_en and bake_ground_warp:
                    try:
                        from hdri_match.core.projection import GroundProjector
                        t_h = float(params.get("tripod_height", 1.5))
                        g_r = float(params.get("ground_radius", 10.0))
                        g_f = float(params.get("ground_feather", 0.15))
                        img = GroundProjector.warp_equirectangular_ground(img, tripod_height=t_h, ground_radius=g_r, feather=g_f)
                    except Exception as e:
                        print(f"[HDRI Match Worker] Ground projection warp error: {e}")

                # 4. Sun Removal / Sky Inpaint
                if sun_remove_en:
                    sun_u = float(params["sun_u"])
                    sun_v = float(params["sun_v"])
                    sun_r = max(0.005, float(params["sun_radius"]))
                    img = HdriMatchSolarisPanel._inpaint_sun_disc(img, sun_u, sun_v, sun_r)

                # 4b. Multi-Light Extracted Practical Inpaint
                if extract_inpaint_en:
                    try:
                        h_ext, w_ext = img.shape[:2]
                        luma_ext = 0.2126 * img[..., 0] + 0.7152 * img[..., 1] + 0.0722 * img[..., 2]
                        temp_luma = luma_ext.copy()

                        # Mask out sun if Sun Relighting is enabled so practical inpaint doesn't inpaint the sun
                        sun_en = bool(params.get("sun_en", False))
                        if sun_en:
                            s_u = float(params.get("sun_u", 0.5))
                            s_v = float(params.get("sun_v", 0.25))
                            s_rad = float(params.get("sun_radius", 0.05))
                            s_x = int(round(s_u * w_ext))
                            s_y = int(round(s_v * h_ext))
                            s_r_px = int(max(8, round(s_rad * w_ext * 1.5)))
                            y_g, x_g = np.ogrid[:h_ext, :w_ext]
                            d_x = np.abs(x_g - s_x)
                            d_x = np.minimum(d_x, w_ext - d_x)
                            d_y = np.abs(y_g - s_y)
                            temp_luma[(d_x**2 + d_y**2) < (s_r_px**2)] = 0.0

                        valid_luma = temp_luma[temp_luma > 0]
                        median_luma = float(np.median(valid_luma)) if valid_luma.size > 0 else 0.18
                        max_luma = float(np.max(temp_luma)) if temp_luma.size > 0 else 1.0
                        thresh_ext = max(0.15, min(max(0.18, median_luma) * (2.0 ** extract_thresh), max_luma * 0.25))

                        rad_ext = max(4, int(w_ext // 48))
                        for _ in range(min(8, int(extract_count))):
                            max_idx = int(np.argmax(temp_luma))
                            py, px = divmod(max_idx, w_ext)
                            if temp_luma[py, px] < thresh_ext:
                                break
                            u_lt = float(px) / float(w_ext)
                            v_lt = float(py) / float(h_ext)
                            r_lt = max(0.008, float(rad_ext) / float(w_ext))
                            img = HdriMatchSolarisPanel._inpaint_sun_disc(img, u_lt, v_lt, r_lt)
                            y_g, x_g = np.ogrid[:h_ext, :w_ext]
                            d_x = np.abs(x_g - px)
                            d_x = np.minimum(d_x, w_ext - d_x)
                            d_y = np.abs(y_g - py)
                            temp_luma[(d_x**2 + d_y**2) < ((rad_ext * 2)**2)] = 0.0
                    except Exception as e:
                        print(f"[DomeBreaker Worker] Multi-light inpaint error: {e}")

                # 5. Horizon split with color tint
                if horizon_en:
                    height = float(params["horizon_height"])
                    feather = max(0.001, float(params["horizon_feather"]))
                    sky_ev = float(params["sky_ev"])
                    ground_ev = float(params["ground_ev"])

                    sky_rgb = np.array(params["sky_color"], dtype=np.float32)
                    ground_rgb = np.array(params["ground_color"], dtype=np.float32)

                    sky_mult = (2.0 ** sky_ev) * sky_rgb
                    ground_mult = (2.0 ** ground_ev) * ground_rgb

                    y_norm = 1.0 - np.linspace(0.0, 1.0, h, endpoint=False)[:, None, None]
                    sky_weight = np.clip((y_norm - (height - feather / 2.0)) / feather, 0.0, 1.0)
                    color_mask = sky_mult[None, None, :] * sky_weight + ground_mult[None, None, :] * (1.0 - sky_weight)
                    img *= color_mask

                # 6. Highlight Compression (soft clip) - Hue-preserving C1 exponential rolloff
                if softclip_en:
                    thresh_ev = float(params.get("softclip_thresh", 5.0))
                    rolloff_ev = float(params.get("softclip_rolloff", 2.0))
                    t = 0.18 * (2.0 ** thresh_ev)
                    r = 0.18 * (2.0 ** rolloff_ev)

                    luma = np.sum(img * np.array([0.2126, 0.7152, 0.0722], dtype=np.float32), axis=-1, keepdims=True)
                    mask = luma > t
                    if np.any(mask):
                        luma_safe = np.maximum(luma, 1e-8)
                        compressed_luma = np.where(mask, t + r * (1.0 - np.exp(-(luma - t) / max(r, 1e-8))), luma)
                        ratio = compressed_luma / luma_safe
                        img = np.where(mask, img * ratio, img)

                out_path = params["out_path"]
                tmp_path = params["tmp_path"]

                compression = params.get("compression", "rle")
                spec = oiio.ImageSpec(w, h, 3, oiio.HALF)
                spec.attribute("compression", compression)

                out = oiio.ImageOutput.create(tmp_path)
                if out:
                    out.open(tmp_path, spec)
                    out.write_image(img.astype(np.float32))
                    out.close()

                    if os.path.exists(out_path):
                        try: os.remove(out_path)
                        except Exception: pass
                    try:
                        os.replace(tmp_path, out_path)
                    except Exception:
                        out_path = tmp_path

                    self.bakeFinished.emit(out_path, ev, black, temp, tint, sat, contrast)
                else:
                    self.bakeFailed.emit(f"Failed to create OIIO output for {tmp_path}")
            except Exception as e:
                self.bakeFailed.emit(str(e))


class SplatDownloadWorker(QtCore.QThread):
    """Worker thread to download official sample Gaussian Splats in background."""
    progress = QtCore.Signal(int, str)
    finished = QtCore.Signal(str)
    failed = QtCore.Signal(str)

    def __init__(self, dest_dir, parent=None):
        super().__init__(parent)
        self.dest_dir = dest_dir

    def run(self):
        try:
            from hdri_match_solaris.gaussian_splat import download_sample_splat
            path = download_sample_splat(self.dest_dir, progress_callback=lambda p, m: self.progress.emit(p, m))
            self.finished.emit(path)
        except Exception as e:
            self.failed.emit(str(e))


class SplatBakeWorker(QtCore.QThread):
    """Worker thread to bake 360° equirectangular HDRI from Gaussian Splats."""
    bakeProgress = QtCore.Signal(int)
    bakeFinished = QtCore.Signal(str)
    bakeFailed = QtCore.Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._mutex = QtCore.QMutex()
        self._params = None

    def request_bake(self, params):
        self._mutex.lock()
        self._params = dict(params)
        self._mutex.unlock()
        if not self.isRunning():
            self.start()

    def run(self):
        while True:
            self._mutex.lock()
            params = self._params
            self._params = None
            self._mutex.unlock()

            if params is None:
                break

            try:
                import importlib
                try:
                    import hdri_match_solaris.gaussian_splat as _gsm
                    importlib.reload(_gsm)
                except Exception:
                    pass
                from hdri_match_solaris.gaussian_splat import GaussianSplatScene, GaussianSplatBaker
                ply_path = params["ply_path"]
                cam_pos = params["cam_pos"]
                width = params["width"]
                height = params["height"]
                out_path = params["out_path"]
                splat_scale = params.get("splat_scale", 1.8)
                flip_y = params.get("flip_y", True)
                min_dist = params.get("min_dist", 0.35)

                scene = params.get("scene")
                if scene is None or getattr(scene, "filepath", "") != ply_path:
                    scene = GaussianSplatScene.from_ply(ply_path)

                bake_kwargs = {
                    "camera_pos": cam_pos,
                    "width": width,
                    "height": height,
                    "splat_scale": splat_scale,
                    "flip_y": flip_y,
                    "min_dist": min_dist,
                    "inpaint_poles": params.get("inpaint_poles", True),
                    "hdr_expand": True,
                    "hdr_boost": 35.0,
                    "progress_callback": self.bakeProgress.emit,
                }
                # Safe argument inspection fallback
                import inspect
                sig = inspect.signature(GaussianSplatBaker.bake_equirectangular)
                has_varkw = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())
                if not has_varkw:
                    bake_kwargs = {k: v for k, v in bake_kwargs.items() if k in sig.parameters}

                img = GaussianSplatBaker.bake_equirectangular(scene, **bake_kwargs)
                self.bakeProgress.emit(99)
                GaussianSplatBaker.save_exr(img, out_path, pixel_type="float")
                self.bakeProgress.emit(100)
                self.bakeFinished.emit(out_path)
            except Exception as e:
                self.bakeFailed.emit(str(e))


class PlanarBakeWorker(QtCore.QThread):
    """Worker thread to bake high-detail planar surface textures (floor, ceiling, walls) from an HDRI map."""
    bakeProgress = QtCore.Signal(int, str)
    bakeFinished = QtCore.Signal(dict)
    bakeFailed = QtCore.Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._mutex = QtCore.QMutex()
        self._params = None

    def request_bake(self, params):
        self._mutex.lock()
        self._params = dict(params)
        self._mutex.unlock()
        if not self.isRunning():
            self.start()

    def run(self):
        while True:
            self._mutex.lock()
            params = self._params
            self._params = None
            self._mutex.unlock()

            if params is None:
                break

            try:
                from hdri_match_solaris.gaussian_splat import GaussianSplatBaker
                hdri_source = params["hdri_source"]
                room_data = params["room_data"]
                output_dir = params["output_dir"]
                probe_pos = params.get("probe_pos")
                res = int(params.get("resolution", 2048))

                def progress_cb(pct, msg):
                    self.bakeProgress.emit(int(pct), str(msg))

                tex_dict = GaussianSplatBaker.bake_planar_room_textures(
                    hdri_source=hdri_source,
                    room_data=room_data,
                    output_dir=output_dir,
                    probe_pos=probe_pos,
                    resolution_floor=res,
                    resolution_walls=res,
                    progress_callback=progress_cb,
                )
                self.bakeFinished.emit(tex_dict)
            except Exception as e:
                self.bakeFailed.emit(str(e))


class HDRIDebugWindow(QtWidgets.QDialog):
    """
    Dedicated floating window for real-time debug output, error inspection,
    and action trace logs.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("HDRI Match Solaris â Live Debug Console & Error Output")
        self.resize(780, 520)
        self.setMinimumSize(520, 320)
        self.setWindowFlags(self.windowFlags() | QtCore.Qt.Window)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        # Top Bar
        top_bar = QtWidgets.QHBoxLayout()
        top_bar.setSpacing(6)

        self.lbl_status = QtWidgets.QLabel("Status: Ready")
        self.lbl_status.setStyleSheet("font-weight: bold; color: #00ff88; font-size: 12px;")
        top_bar.addWidget(self.lbl_status, 1)

        self.txt_filter = QtWidgets.QLineEdit()
        self.txt_filter.setPlaceholderText("Filter logs (e.g. error, sun, stage)...")
        self.txt_filter.setMaximumWidth(220)
        self.txt_filter.setStyleSheet(
            "background: #1e1e1e; color: #eee; border: 1px solid #444; border-radius: 3px; padding: 3px 6px;"
        )
        self.txt_filter.textChanged.connect(self._apply_filter)
        top_bar.addWidget(self.txt_filter)

        self.btn_copy = QtWidgets.QPushButton("Copy All")
        self.btn_copy.setToolTip("Copy complete debug log to clipboard")
        self.btn_copy.setStyleSheet("padding: 3px 8px;")
        self.btn_copy.clicked.connect(self._copy_log)
        top_bar.addWidget(self.btn_copy)

        self.btn_clear = QtWidgets.QPushButton("Clear")
        self.btn_clear.setToolTip("Clear debug console")
        self.btn_clear.setStyleSheet("padding: 3px 8px;")
        self.btn_clear.clicked.connect(self._clear_log)
        top_bar.addWidget(self.btn_clear)

        layout.addLayout(top_bar)

        # Log Output Box
        self.txt_output = QtWidgets.QTextEdit()
        self.txt_output.setReadOnly(True)
        self.txt_output.setStyleSheet(
            "background-color: #0f1115; color: #e0e0e0; "
            "font-family: Consolas, 'Courier New', monospace; "
            "font-size: 11px; border: 1px solid #282c34; border-radius: 4px; padding: 6px;"
        )
        layout.addWidget(self.txt_output, 1)

        # Bottom Bar
        btm_bar = QtWidgets.QHBoxLayout()
        self.chk_auto_scroll = QtWidgets.QCheckBox("Auto-scroll")
        self.chk_auto_scroll.setChecked(True)
        btm_bar.addWidget(self.chk_auto_scroll)

        self.chk_always_on_top = QtWidgets.QCheckBox("Stay on Top")
        self.chk_always_on_top.toggled.connect(self._toggle_stay_on_top)
        btm_bar.addWidget(self.chk_always_on_top)

        btm_bar.addStretch()

        self.lbl_count = QtWidgets.QLabel("Entries: 0 | Errors: 0")
        self.lbl_count.setStyleSheet("color: #888; font-size: 11px;")
        btm_bar.addWidget(self.lbl_count)

        layout.addLayout(btm_bar)

        self._raw_entries = []

    def append_log(self, text, level="INFO"):
        import datetime
        now = datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]
        color_map = {
            "INFO": "#00c8ff",      # Cyan
            "SUCCESS": "#00ff88",   # Bright Green
            "WARNING": "#ffaa00",   # Amber / Orange
            "ERROR": "#ff3b3b",     # Bright Red
            "DETAIL": "#999999",    # Muted Grey
        }
        color = color_map.get(level.upper(), "#ffffff")
        prefix = f'<span style="color: #666;">[{now}]</span> <b style="color: {color};">[{level.upper()}]</b>'
        escaped = (
            str(text).replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace("\n", "<br/>&nbsp;&nbsp;&nbsp;&nbsp;")
        )
        html = f"{prefix} {escaped}"
        self._raw_entries.append((now, level.upper(), str(text), html))

        flt = self.txt_filter.text().strip().lower()
        if not flt or flt in str(text).lower() or flt in level.lower():
            self.txt_output.append(html)
            if self.chk_auto_scroll.isChecked():
                self.txt_output.verticalScrollBar().setValue(
                    self.txt_output.verticalScrollBar().maximum()
                )

        if level.upper() == "ERROR":
            self.lbl_status.setText("Status: Error detected")
            self.lbl_status.setStyleSheet("font-weight: bold; color: #ff3b3b; font-size: 12px;")
        elif level.upper() == "SUCCESS":
            self.lbl_status.setText("Status: Operation completed")
            self.lbl_status.setStyleSheet("font-weight: bold; color: #00ff88; font-size: 12px;")
        elif level.upper() == "INFO":
            self.lbl_status.setText("Status: Processing...")
            self.lbl_status.setStyleSheet("font-weight: bold; color: #00c8ff; font-size: 12px;")

        err_cnt = sum(1 for e in self._raw_entries if e[1] == "ERROR")
        warn_cnt = sum(1 for e in self._raw_entries if e[1] == "WARNING")
        self.lbl_count.setText(f"Entries: {len(self._raw_entries)} | Errors: {err_cnt} | Warnings: {warn_cnt}")

    def _apply_filter(self, text):
        flt = text.strip().lower()
        self.txt_output.clear()
        for now, lvl, raw, html in self._raw_entries:
            if not flt or flt in raw.lower() or flt in lvl.lower():
                self.txt_output.append(html)
        if self.chk_auto_scroll.isChecked():
            self.txt_output.verticalScrollBar().setValue(
                self.txt_output.verticalScrollBar().maximum()
            )

    def _copy_log(self):
        QtWidgets.QApplication.clipboard().setText(self.txt_output.toPlainText())

    def _clear_log(self):
        self._raw_entries.clear()
        self.txt_output.clear()
        self.lbl_status.setText("Status: Ready")
        self.lbl_status.setStyleSheet("font-weight: bold; color: #00ff88; font-size: 12px;")
        self.lbl_count.setText("Entries: 0 | Errors: 0")

    def _toggle_stay_on_top(self, checked):
        if checked:
            self.setWindowFlags(self.windowFlags() | QtCore.Qt.WindowStaysOnTopHint)
        else:
            self.setWindowFlags(self.windowFlags() & ~QtCore.Qt.WindowStaysOnTopHint)
        self.show()



# ----------------------------------------------------------------------
# Collapsible Section Component
# ----------------------------------------------------------------------


# ----------------------------------------------------------------------
# Large Preview Inspection Dialog
# ----------------------------------------------------------------------

class HDRILargePreviewDialog(QtWidgets.QDialog):
    """
    Floating, resizable popup inspection window for HDRI and Plate previews.
    Allows artists to examine fine image details, scrub EV exposure (-6 to +6)
    to inspect high-dynamic range highlights and shadow details, and toggle view modes.
    """
    def __init__(self, parent_panel=None, initial_mode="hdri"):
        super().__init__(parent_panel)
        self.panel = parent_panel
        self.current_mode = initial_mode
        self.ev_offset = 0.0
        self._cached_pixmap = None

        self.setWindowTitle("DomeBreaker — Large Preview Inspector")
        self.resize(1100, 620)
        self.setMinimumSize(640, 400)
        self.setStyleSheet("""
            QDialog { background-color: #141619; color: #ddd; }
            QLabel { color: #ccc; }
            QPushButton { background-color: #25282d; color: #ddd; border: 1px solid #3d434d; border-radius: 3px; padding: 4px 10px; font-size: 11px; }
            QPushButton:hover { background-color: #323740; border-color: #555e6d; color: #fff; }
            QComboBox { background-color: #202328; color: #ddd; border: 1px solid #3d434d; border-radius: 3px; padding: 3px 8px; font-size: 11px; }
            QSlider::groove:horizontal { height: 4px; background: #2a2e36; border-radius: 2px; }
            QSlider::sub-page:horizontal { background: #e67e22; border-radius: 2px; }
            QSlider::handle:horizontal { background: #e67e22; border: 1px solid #ff9f43; width: 12px; margin-top: -4px; margin-bottom: -4px; border-radius: 6px; }
        """)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(6)

        # Top Control Bar
        top_bar = QtWidgets.QHBoxLayout()
        top_bar.setSpacing(8)

        lbl_mode = QtWidgets.QLabel("View Mode:")
        lbl_mode.setStyleSheet("font-weight: bold; font-size: 11px; color: #aaa;")
        top_bar.addWidget(lbl_mode)

        self.combo_mode = QtWidgets.QComboBox()
        self.combo_mode.addItems(["HDRI Map", "Target Plate", "Split Wipe"])
        self.combo_mode.currentIndexChanged.connect(self._on_mode_changed)
        top_bar.addWidget(self.combo_mode)

        top_bar.addSpacing(15)

        # Quick EV Exposure Inspector Slider
        lbl_ev = QtWidgets.QLabel("Inspector Exposure (EV):")
        lbl_ev.setStyleSheet("font-weight: bold; font-size: 11px; color: #ffaa00;")
        top_bar.addWidget(lbl_ev)

        self.sld_ev = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.sld_ev.setRange(-60, 60)
        self.sld_ev.setValue(0)
        self.sld_ev.setFixedWidth(180)
        self.sld_ev.valueChanged.connect(self._on_ev_changed)
        top_bar.addWidget(self.sld_ev)

        self.lbl_ev_val = QtWidgets.QLabel("+0.0 EV")
        self.lbl_ev_val.setStyleSheet("font-family: Consolas, monospace; font-size: 11px; min-width: 50px;")
        top_bar.addWidget(self.lbl_ev_val)

        self.btn_reset_ev = QtWidgets.QPushButton("Reset EV")
        self.btn_reset_ev.setToolTip("Reset inspector exposure to +0.0 EV")
        self.btn_reset_ev.clicked.connect(lambda: self.sld_ev.setValue(0))
        top_bar.addWidget(self.btn_reset_ev)

        top_bar.addStretch()

        self.btn_fit = QtWidgets.QPushButton("Fit Window")
        self.btn_fit.setCheckable(True)
        self.btn_fit.setChecked(True)
        self.btn_fit.toggled.connect(self.update_image)
        top_bar.addWidget(self.btn_fit)

        self.btn_close = QtWidgets.QPushButton("✕ Close")
        self.btn_close.clicked.connect(self.accept)
        top_bar.addWidget(self.btn_close)

        layout.addLayout(top_bar)

        # Main Image Viewport Area
        self.scroll_area = QtWidgets.QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setStyleSheet("QScrollArea { background-color: #0b0c0e; border: 1px solid #23272f; border-radius: 4px; }")

        self.lbl_image = QtWidgets.QLabel()
        self.lbl_image.setAlignment(QtCore.Qt.AlignCenter)
        self.lbl_image.setStyleSheet("background-color: transparent;")
        self.scroll_area.setWidget(self.lbl_image)

        layout.addWidget(self.scroll_area, 1)

        # Bottom Info Bar
        self.info_bar = QtWidgets.QHBoxLayout()
        self.lbl_info = QtWidgets.QLabel("---")
        self.lbl_info.setStyleSheet("color: #888; font-size: 11px;")
        self.lbl_hint = QtWidgets.QLabel("💡 Drag EV slider to inspect high-dynamic range highlights and deep shadows. Press Esc to close.")
        self.lbl_hint.setStyleSheet("color: #666; font-size: 10px; font-style: italic;")
        self.info_bar.addWidget(self.lbl_info, 1)
        self.info_bar.addWidget(self.lbl_hint, 0)
        layout.addLayout(self.info_bar)

        self.set_mode(initial_mode)

    def set_mode(self, mode):
        self.current_mode = mode
        self.combo_mode.blockSignals(True)
        if mode == "plate":
            self.combo_mode.setCurrentIndex(1)
        elif mode == "wipe":
            self.combo_mode.setCurrentIndex(2)
        else:
            self.combo_mode.setCurrentIndex(0)
        self.combo_mode.blockSignals(False)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._display_pixmap()

    def _on_mode_changed(self, idx):
        modes = ["hdri", "plate", "wipe"]
        self.current_mode = modes[idx]
        self.update_image()

    def _on_ev_changed(self, val):
        self.ev_offset = val / 10.0
        sign = "+" if self.ev_offset >= 0 else ""
        self.lbl_ev_val.setText(f"{sign}{self.ev_offset:.1f} EV")
        self.update_image()

    def update_image(self):
        """Fetch image array, apply inspector EV offset & calibration tonemapping, and update view."""
        if not self.panel:
            return

        import numpy as np
        import os

        target_arr = None
        info_text = ""

        if self.current_mode == "hdri":
            target_arr = getattr(self.panel, '_hdri_full_array', None)
            if target_arr is None:
                target_arr = getattr(self.panel, '_hdri_thumb_raw', None)
            orig_shape = getattr(self.panel, '_hdri_orig_shape', None)
            path = self.panel.txt_hdri.text() if hasattr(self.panel, 'txt_hdri') else ""
            name = os.path.basename(path) if path else "No HDRI"
            shape_str = f"{orig_shape[0]}x{orig_shape[1]}" if orig_shape else ""
            info_text = f"HDRI Map: {name} ({shape_str}) | Inspector EV: {'+' if self.ev_offset>=0 else ''}{self.ev_offset:.1f}"
        elif self.current_mode == "plate":
            target_arr = getattr(self.panel, '_plate_full_array', None)
            if target_arr is None:
                target_arr = getattr(self.panel, '_plate_thumb_raw', None)
            orig_shape = getattr(self.panel, '_plate_orig_shape', None)
            path = self.panel.txt_plate.text() if hasattr(self.panel, 'txt_plate') else ""
            name = os.path.basename(path) if path else "No Plate"
            shape_str = f"{orig_shape[0]}x{orig_shape[1]}" if orig_shape else ""
            info_text = f"Target Plate: {name} ({shape_str}) | Inspector EV: {'+' if self.ev_offset>=0 else ''}{self.ev_offset:.1f}"
        elif self.current_mode == "wipe":
            h_raw = getattr(self.panel, '_hdri_full_array', None)
            if h_raw is None:
                h_raw = getattr(self.panel, '_hdri_thumb_raw', None)
            p_raw = getattr(self.panel, '_plate_full_array', None)
            if p_raw is None:
                p_raw = getattr(self.panel, '_plate_thumb_raw', None)
            ratio = self.panel.sld_wipe.value() / 100.0 if hasattr(self.panel, 'sld_wipe') else 0.5
            info_text = f"Interactive Split Wipe Comparison ({int(ratio*100)}% HDRI | {int((1.0-ratio)*100)}% Plate)"

            if h_raw is not None or p_raw is not None:
                h_img = h_raw.copy() if h_raw is not None else None
                p_img = p_raw.copy() if p_raw is not None else None
                if self.ev_offset != 0.0:
                    if h_img is not None: h_img *= (2.0 ** self.ev_offset)
                    if p_img is not None: p_img *= (2.0 ** self.ev_offset)
                h_u8 = self.panel._tonemap_array(h_img, apply_calib=True) if h_img is not None else None
                p_u8 = self.panel._tonemap_array(p_img, apply_calib=False) if p_img is not None else None

                pix_h = self.panel._arr_to_pixmap(h_u8) if h_u8 is not None else None
                pix_p = self.panel._arr_to_pixmap(p_u8) if p_u8 is not None else None

                tw, th = 1920, 1080
                canvas = QtGui.QPixmap(tw, th)
                canvas.fill(QtCore.Qt.black)
                painter = QtGui.QPainter(canvas)
                split_x = int(ratio * tw)

                if pix_h is not None:
                    h_scaled = pix_h.scaled(tw, th, QtCore.Qt.IgnoreAspectRatio, QtCore.Qt.SmoothTransformation)
                    painter.drawPixmap(0, 0, split_x, th, h_scaled, 0, 0, split_x, th)
                if pix_p is not None:
                    p_scaled = pix_p.scaled(tw, th, QtCore.Qt.IgnoreAspectRatio, QtCore.Qt.SmoothTransformation)
                    painter.drawPixmap(split_x, 0, tw - split_x, th, p_scaled, split_x, 0, tw - split_x, th)

                # Amber divider line
                if 0 <= split_x < tw:
                    painter.setPen(QtGui.QPen(QtGui.QColor(230, 126, 34), 2))
                    painter.drawLine(split_x, 0, split_x, th)

                painter.end()
                self._cached_pixmap = canvas
                self.lbl_info.setText(info_text)
                self._display_pixmap()
                return

        if target_arr is None:
            self.lbl_image.setText(f"No {self.current_mode.upper()} image data available.")
            self._cached_pixmap = None
            self.lbl_info.setText(info_text)
            return

        # Tonemap image
        apply_cal = (self.current_mode == "hdri")
        img = target_arr.copy()
        if self.ev_offset != 0.0:
            img *= (2.0 ** self.ev_offset)

        if hasattr(self.panel, '_tonemap_array'):
            u8 = self.panel._tonemap_array(img, apply_calib=apply_cal)
        else:
            u8 = np.clip(img * 255.0, 0, 255).astype(np.uint8)

        if hasattr(self.panel, '_arr_to_pixmap'):
            self._cached_pixmap = self.panel._arr_to_pixmap(u8)
        else:
            h, w, c = u8.shape
            qimg = QtGui.QImage(u8.data, w, h, c * w, QtGui.QImage.Format_RGB888)
            self._cached_pixmap = QtGui.QPixmap.fromImage(qimg)

        self.lbl_info.setText(info_text)
        self._display_pixmap()

    def _display_pixmap(self):
        if not self._cached_pixmap:
            return
        if self.btn_fit.isChecked():
            self.scroll_area.setWidgetResizable(True)
            view_size = self.scroll_area.viewport().size()
            scaled = self._cached_pixmap.scaled(
                view_size, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation
            )
            self.lbl_image.setPixmap(scaled)
        else:
            self.scroll_area.setWidgetResizable(False)
            self.lbl_image.setPixmap(self._cached_pixmap)
            self.lbl_image.resize(self._cached_pixmap.size())


class HDRIBoundaryLargeViewDialog(QtWidgets.QDialog):
    """
    Large floating popup inspection window for the Visual Boundary Overlay Preview.
    Allows artists to examine room boundaries, ceiling lines, floor corners, and horizon
    in high-resolution across a large or multi-monitor workspace.
    """
    def __init__(self, parent_panel=None):
        super().__init__(parent_panel)
        self.panel = parent_panel
        self._cached_pixmap = None

        self.setWindowTitle("DomeBreaker — Visual Boundary Overlay (Large Inspector)")
        self.resize(1200, 680)
        self.setMinimumSize(640, 380)
        self.setStyleSheet("""
            QDialog { background-color: #141619; color: #ddd; }
            QLabel { color: #ccc; }
            QPushButton { background-color: #25282d; color: #ddd; border: 1px solid #3d434d; border-radius: 3px; padding: 4px 12px; font-size: 11px; }
            QPushButton:hover { background-color: #323740; border-color: #555e6d; color: #fff; }
        """)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(6)

        # Top Control Bar
        top_bar = QtWidgets.QHBoxLayout()
        top_bar.setSpacing(10)

        title_lbl = QtWidgets.QLabel("Visual Boundary Overlay Inspector")
        title_lbl.setStyleSheet("font-weight: bold; font-size: 13px; color: #ffcc44;")
        top_bar.addWidget(title_lbl)

        self.lbl_dims_info = QtWidgets.QLabel("---")
        self.lbl_dims_info.setStyleSheet("color: #9da5b4; font-size: 11px; margin-left: 10px;")
        top_bar.addWidget(self.lbl_dims_info, 1)

        self.btn_fit = QtWidgets.QPushButton("Fit Window")
        self.btn_fit.setCheckable(True)
        self.btn_fit.setChecked(True)
        self.btn_fit.toggled.connect(self._display_pixmap)
        top_bar.addWidget(self.btn_fit)

        self.btn_close = QtWidgets.QPushButton("✕ Close")
        self.btn_close.clicked.connect(self.accept)
        top_bar.addWidget(self.btn_close)

        layout.addLayout(top_bar)

        # Central Image Viewport Area
        self.scroll_area = QtWidgets.QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setStyleSheet("QScrollArea { background-color: #0b0c0e; border: 1px solid #23272f; border-radius: 4px; }")

        self.lbl_image = QtWidgets.QLabel()
        self.lbl_image.setAlignment(QtCore.Qt.AlignCenter)
        self.lbl_image.setStyleSheet("background-color: transparent;")
        self.scroll_area.setWidget(self.lbl_image)

        layout.addWidget(self.scroll_area, 1)

        # Bottom Legend & Status Bar
        bot_bar = QtWidgets.QHBoxLayout()
        bot_bar.setSpacing(12)

        legend_items = [
            ("■ Floor", "#00e5ff"),
            ("■ Ceiling", "#ffb700"),
            ("■ 4 Corners", "#ff007f"),
            ("┅ Horizon", "#bbbbbb"),
        ]
        for name, col in legend_items:
            lbl_leg = QtWidgets.QLabel(name)
            lbl_leg.setStyleSheet(f"color: {col}; font-weight: bold; font-size: 11px;")
            bot_bar.addWidget(lbl_leg)

        bot_bar.addSpacing(20)
        lbl_hint = QtWidgets.QLabel("💡 Live Sync: Sliders adjusted in the main panel update here in real-time.")
        lbl_hint.setStyleSheet("color: #777; font-size: 11px; font-style: italic;")
        bot_bar.addWidget(lbl_hint, 1)

        layout.addLayout(bot_bar)

    def set_overlay_pixmap(self, pixmap, info_text=""):
        self._cached_pixmap = pixmap
        if info_text:
            self.lbl_dims_info.setText(info_text)
        self._display_pixmap()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._display_pixmap()

    def _display_pixmap(self):
        if not self._cached_pixmap or self._cached_pixmap.isNull():
            self.lbl_image.setText("No overlay preview generated yet. Run 'Analyze Room Boundaries' first.")
            return

        if self.btn_fit.isChecked():
            view_size = self.scroll_area.viewport().size()
            scaled = self._cached_pixmap.scaled(
                view_size, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation
            )
            self.lbl_image.setPixmap(scaled)
        else:
            self.lbl_image.setPixmap(self._cached_pixmap)


class CollapsibleSection(QtWidgets.QWidget):
    """
    Production-grade collapsible section widget for Houdini Solaris panels.
    Provides:
    - Interactive header bar with rotating expand/collapse arrow (▼ / ▶)
    - Optional checkable feature toggle checkbox ([x] Feature Name)
    - Content container (self.content_widget) that cleanly hides/shows in QScrollArea
    - Auto-expansion when an enabled feature checkbox is checked
    - Full QGroupBox drop-in compatibility (isChecked, setChecked, toggled, title, setTitle)
    """
    collapsed_toggled = QtCore.Signal(bool)

    def __init__(self, title, checkable=False, checked=False, collapsed=False, parent=None):
        super().__init__(parent)
        self._is_collapsed = collapsed
        self._is_checkable = checkable
        self._title_text = title

        main_lay = QtWidgets.QVBoxLayout(self)
        main_lay.setContentsMargins(0, 2, 0, 2)
        main_lay.setSpacing(0)

        # Header Frame
        self.header = QtWidgets.QFrame()
        self.header.setFrameShape(QtWidgets.QFrame.StyledPanel)
        self.header.setCursor(QtCore.Qt.PointingHandCursor)
        header_lay = QtWidgets.QHBoxLayout(self.header)
        header_lay.setContentsMargins(8, 5, 8, 5)
        header_lay.setSpacing(8)

        # Arrow indicator
        self.lbl_arrow = QtWidgets.QLabel("▼" if not self._is_collapsed else "▶")
        self.lbl_arrow.setStyleSheet("color: #e67e22; font-size: 10px; font-weight: bold;")
        header_lay.addWidget(self.lbl_arrow)

        # Optional Feature Checkbox
        if self._is_checkable:
            self.chk_enable = QtWidgets.QCheckBox()
            self.chk_enable.setChecked(checked)
            self.chk_enable.setToolTip(f"Enable / Disable {title}")
            self.chk_enable.toggled.connect(self._on_check_toggled)
            header_lay.addWidget(self.chk_enable)
        else:
            self.chk_enable = None

        # Title Label
        self.lbl_title = QtWidgets.QLabel(title)
        self.lbl_title.setStyleSheet("color: #e2e4e8; font-weight: bold; font-size: 12px; letter-spacing: 0.3px;")
        header_lay.addWidget(self.lbl_title, 1)

        main_lay.addWidget(self.header)

        # Content Container
        self.content_widget = QtWidgets.QWidget()
        self.content_widget.setStyleSheet(
            "QWidget { background-color: #1a1c20; border-left: 1px solid #2d313a; border-right: 1px solid #2d313a; "
            "border-bottom: 1px solid #2d313a; border-bottom-left-radius: 4px; border-bottom-right-radius: 4px; }"
        )
        main_lay.addWidget(self.content_widget)

        # Install mouse click handler on header
        self.header.mousePressEvent = self._on_header_clicked

        self._update_header_style()
        if self._is_collapsed:
            self.content_widget.hide()

    def _update_header_style(self):
        if self._is_collapsed:
            self.header.setStyleSheet(
                "QFrame { background-color: #25282d; border: 1px solid #383d47; border-radius: 4px; padding: 2px; } "
                "QFrame:hover { background-color: #2f343c; border-color: #4f5663; }"
            )
        else:
            self.header.setStyleSheet(
                "QFrame { background-color: #25282d; border: 1px solid #383d47; border-top-left-radius: 4px; border-top-right-radius: 4px; "
                "border-bottom-left-radius: 0px; border-bottom-right-radius: 0px; padding: 2px; } "
                "QFrame:hover { background-color: #2f343c; border-color: #4f5663; }"
            )

    def _on_header_clicked(self, event):
        if event.button() == QtCore.Qt.LeftButton:
            # Let checkbox handle direct clicks on itself
            if self.chk_enable:
                chk_geom = self.chk_enable.geometry()
                if chk_geom.contains(event.pos()):
                    return
            self.toggle_collapse()

    def _on_check_toggled(self, state):
        # Auto-expand if the feature is activated while collapsed
        if state and self._is_collapsed:
            self.set_collapsed(False)

    def is_collapsed(self):
        return self._is_collapsed

    def set_collapsed(self, collapsed):
        if self._is_collapsed == collapsed:
            return
        self._is_collapsed = collapsed
        self.content_widget.setVisible(not collapsed)
        self.lbl_arrow.setText("▶" if collapsed else "▼")
        self._update_header_style()
        self.collapsed_toggled.emit(collapsed)

    def toggle_collapse(self):
        self.set_collapsed(not self._is_collapsed)

    # QGroupBox drop-in compatibility methods
    def isChecked(self):
        if self.chk_enable:
            return self.chk_enable.isChecked()
        return True

    def setChecked(self, checked):
        if self.chk_enable:
            self.chk_enable.setChecked(checked)

    @property
    def toggled(self):
        if self.chk_enable:
            return self.chk_enable.toggled
        return self.collapsed_toggled

    def isCheckable(self):
        return self._is_checkable

    def setCheckable(self, checkable):
        self._is_checkable = checkable

    def title(self):
        return self._title_text

    def setTitle(self, title):
        self._title_text = title
        self.lbl_title.setText(title)



def _get_state_filepath():
    """Determine cross-platform path for persistent session state JSON."""
    try:
        if 'hou' in sys.modules:
            if hasattr(hou, 'text') and hasattr(hou.text, 'expandString'):
                pref_dir = hou.text.expandString("$HOUDINI_USER_PREF_DIR")
            elif hasattr(hou, 'expandString'):
                pref_dir = hou.expandString("$HOUDINI_USER_PREF_DIR")
            else:
                pref_dir = None
            if pref_dir and os.path.isdir(pref_dir):
                return os.path.join(pref_dir, "domebreaker_state.json").replace(chr(92), "/")
    except Exception:
        pass
    home = os.path.expanduser("~")
    return os.path.join(home, ".domebreaker_state.json").replace(chr(92), "/")


def _resolve_image_path(path):
    """Expand tokens ($HIP, $JOB, $HOUDINI_USER_PREF_DIR) and normalize slashes."""
    if not path:
        return ""
    clean = str(path).strip().strip('"').strip("'").replace("\\", "/")
    if 'hou' in sys.modules:
        try:
            if hasattr(hou, 'text') and hasattr(hou.text, 'expandString'):
                expanded = hou.text.expandString(clean)
            elif hasattr(hou, 'expandString'):
                expanded = hou.expandString(clean)
            else:
                expanded = os.path.expandvars(clean)
        except Exception:
            expanded = os.path.expandvars(clean)
    else:
        expanded = os.path.expandvars(clean)
    return expanded.strip().replace("\\", "/")


def _clean_hdri_basename(path_or_name):
    """Strip any cascading '_calibrated_*' or '_calibrated' suffixes to prevent runaway ping-pong filenames."""
    if not path_or_name:
        return "hdri"
    base = os.path.splitext(os.path.basename(str(path_or_name)))[0]
    clean = re.sub(r'(_calibrated(_[A-Za-z0-9_]+)?)+$', '', base, flags=re.IGNORECASE)
    return clean or "hdri"


def _clean_hdri_path(path):
    """Strip cascading '_calibrated_*' suffixes and resolve back to the clean source file if available."""
    if not path:
        return ""
    clean = re.sub(r'(_calibrated(_[A-Za-z0-9_]+)?)+\.exr$', '.exr', str(path).replace('\\', '/'), flags=re.IGNORECASE)
    return clean


def _ensure_dome_light_latlong(dome_node):
    """Ensure native Solaris dome light uses Lat-Long texture format (required by Arnold)."""
    if dome_node is None:
        return
    for p_name in ["xn__inputstextureformat_06ah", "inputs:texture:format", "texture_format", "format"]:
        p = dome_node.parm(p_name)
        if p is not None:
            try:
                if p.evalAsString() != "latlong":
                    p.set("latlong")
            except Exception:
                pass
    for ctrl_name in ["xn__inputstextureformat_control_1kbh", "inputs:texture:format_control"]:
        ctrl = dome_node.parm(ctrl_name)
        if ctrl is not None:
            try:
                if ctrl.evalAsString() != "set":
                    ctrl.set("set")
            except Exception:
                pass


class HdriMatchSolarisPanel(QtWidgets.QWidget):
    """Main panel widget for HDRI Match Solaris."""

    _clean_hdri_basename = staticmethod(_clean_hdri_basename)
    _clean_hdri_path = staticmethod(_clean_hdri_path)
    _ensure_dome_light_latlong = staticmethod(_ensure_dome_light_latlong)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._initializing = True
        self._restoring_state = False
        if not parent and self.isWindow():
            self.setWindowTitle("DomeBreaker — USD Lighting & Environment Suite")
        self.setMinimumWidth(380)

        self._pipeline = None
        self._hdri_array = None
        self._plate_array = None
        self._hdri_thumb_raw = None
        self._debug_win = HDRIDebugWindow(self)
        self._plate_thumb_raw = None
        self._plate_orig_thumb = None
        self._plate_full_array = None
        self._plate_orig_full = None
        self._plate_orig_shape = None
        self._hdri_orig_shape = None
        self._hdri_full_array = None
        self._hdri_orig_full = None
        self._current_calibrated_path = None
        self._baked_manual = False
        self._baked_ev = 0.0
        self._baked_black = 0.0
        self._baked_temp = 0.0
        self._baked_tint = 0.0
        self._baked_sat = 1.0
        self._baked_contrast = 1.0
        self._bake_timer = QtCore.QTimer()
        self._bake_timer.setSingleShot(True)
        self._bake_timer.timeout.connect(self._trigger_background_bake)
        self._bg_bake_worker = BackgroundBakeWorker(self)
        self._bg_bake_worker.bakeFinished.connect(self._on_bg_bake_finished)
        self._bg_bake_worker.bakeFailed.connect(self._on_bg_bake_failed)
        self._splat_scene = None
        self._splat_download_worker = None
        self._splat_bake_worker = SplatBakeWorker(self)
        self._splat_bake_worker.bakeProgress.connect(self._on_splat_bake_progress)
        self._splat_bake_worker.bakeFinished.connect(self._on_splat_bake_finished)
        self._splat_bake_worker.bakeFailed.connect(self._on_splat_bake_failed)
        self._prop_bake_worker = SplatBakeWorker(self)
        self._prop_bake_worker.bakeProgress.connect(self._on_prop_bake_progress)
        self._prop_bake_worker.bakeFinished.connect(self._on_prop_bake_finished)
        self._prop_bake_worker.bakeFailed.connect(self._on_prop_bake_failed)
        self._planar_bake_worker = PlanarBakeWorker(self)
        self._planar_bake_worker.bakeProgress.connect(self._on_planar_bake_progress)
        self._planar_bake_worker.bakeFinished.connect(self._on_planar_bake_finished)
        self._planar_bake_worker.bakeFailed.connect(self._on_planar_bake_failed)
        self._planar_textures_dict = {}
        self._current_baking_prop_row = -1
        self._plate_orig_shape = None
        self._custom_props = []
        self._updating_custom_props_ui = False
        self._crucible_container = None
        self._crucible_panel = None
        self._floating_crucible_win = None
        self._crucible_fw_layout = None
        self._ana_last_result = None
        self._ana_last_img_input = None

        self._build_ui()
        self._connect_signals()
        # Register scene event callbacks so state is automatically saved on HIP save
        if 'hou' in sys.modules and hasattr(hou, 'hipFile'):
            try:
                hou.hipFile.addEventCallback(self._on_hip_event)
            except Exception:
                pass

        restored = self._restore_state_from_scene(notify=False)
        self._initializing = False
        if not restored:
            self._sync_node()
        self.log("DomeBreaker panel initialized. Ready.", "SUCCESS")

    def closeEvent(self, event):
        """Save entire session state when panel is closed."""
        try:
            self._save_state()
        except Exception:
            pass
        if 'hou' in sys.modules and hasattr(hou, 'hipFile'):
            try:
                hou.hipFile.removeEventCallback(self._on_hip_event)
            except Exception:
                pass
        try:
            super().closeEvent(event)
        except Exception:
            pass

    def hideEvent(self, event):
        """Save session state when panel or tab is hidden."""
        try:
            self._save_state()
        except Exception:
            pass
        try:
            super().hideEvent(event)
        except Exception:
            pass

    def _on_hip_event(self, event_type):
        """Handle Houdini scene events to ensure parameters stay persisted."""
        try:
            if 'hou' in sys.modules and hasattr(hou, 'hipFileEventType'):
                if event_type in (hou.hipFileEventType.BeforeSave, hou.hipFileEventType.AfterSave):
                    self._save_state()
                elif event_type == hou.hipFileEventType.AfterLoad:
                    self._restore_state_from_scene(notify=False)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # UI Construction
    # ------------------------------------------------------------------

    def _build_ui(self):
        """Construct the panel layout."""
        main_layout = QtWidgets.QVBoxLayout(self)
        main_layout.setContentsMargins(6, 6, 6, 6)
        main_layout.setSpacing(4)

        # Title bar
        title_layout = QtWidgets.QHBoxLayout()
        title = QtWidgets.QLabel("⚡ DomeBreaker")
        title.setStyleSheet(
            "font-size: 16px; font-weight: bold; color: #FFaa00; "
            "padding: 4px; background: #2a2a2a; border-radius: 4px;"
        )
        title.setAlignment(QtCore.Qt.AlignCenter)
        
        self.btn_reload = QtWidgets.QPushButton("ð Reload Tool")
        self.btn_reload.setToolTip("Completely reload all Python modules and refresh the panel UI")
        self.btn_reload.setStyleSheet(
            "QPushButton { background-color: #1e334a; color: #00d2ff; font-weight: bold; "
            "border: 1px solid #009be6; border-radius: 4px; padding: 4px 12px; font-size: 12px; } "
            "QPushButton:hover { background-color: #274768; color: #ffffff; border-color: #00c8ff; } "
            "QPushButton:pressed { background-color: #142436; }"
        )
        
        self.btn_read_stage = QtWidgets.QPushButton("📥 Read from Stage")
        self.btn_read_stage.setToolTip("Read existing Dome Light & Sun Light settings from /stage and restore previous session")
        self.btn_read_stage.setStyleSheet(
            "QPushButton { background-color: #1e3a2b; color: #00ffaa; font-weight: bold; "
            "border: 1px solid #00c878; border-radius: 4px; padding: 4px 12px; font-size: 12px; } "
            "QPushButton:hover { background-color: #274d39; color: #ffffff; border-color: #00ffbe; } "
            "QPushButton:pressed { background-color: #12241b; }"
        )
        self.btn_read_stage.clicked.connect(lambda: self._restore_state_from_scene(notify=True))

        self.btn_reset_defaults = QtWidgets.QPushButton("↺ Reset Defaults")
        self.btn_reset_defaults.setToolTip("Reset all parameters, stage nodes, and bakes back to factory defaults to start completely fresh.")
        self.btn_reset_defaults.setStyleSheet(
            "QPushButton { background-color: #3d1e1e; color: #ff7777; font-weight: bold; "
            "border: 1px solid #aa3333; border-radius: 4px; padding: 4px 12px; font-size: 12px; } "
            "QPushButton:hover { background-color: #5c2424; color: #ffffff; border-color: #dd4444; } "
            "QPushButton:pressed { background-color: #261212; }"
        )
        self.btn_reset_defaults.clicked.connect(lambda: self.reset_to_defaults(confirm=True))

        self.btn_toggle_crucible = QtWidgets.QPushButton("🔥 HDRI Library")
        self.btn_toggle_crucible.setCheckable(True)
        self.btn_toggle_crucible.setToolTip("Show / Hide HDRI Library side panel (HDRIs, Gobos, Scrims, Lights, Presets)")
        self.btn_toggle_crucible.setStyleSheet(
            "QPushButton { background-color: #3d2600; color: #ffaa33; font-weight: bold; "
            "border: 1px solid #cc7700; border-radius: 4px; padding: 4px 12px; font-size: 12px; } "
            "QPushButton:hover { background-color: #593700; color: #ffcc66; border-color: #ee8800; } "
            "QPushButton:checked { background-color: #e67e22; color: #ffffff; border-color: #ffa500; }"
        )
        self.btn_toggle_crucible.clicked.connect(self._toggle_crucible_panel)

        self.btn_expand_all = QtWidgets.QPushButton("▼ Expand All")
        self.btn_expand_all.setToolTip("Expand all sections")
        self.btn_expand_all.setStyleSheet(
            "QPushButton { background-color: #24272c; color: #bbb; border: 1px solid #363a42; "
            "border-radius: 3px; padding: 4px 8px; font-size: 11px; font-weight: bold; } "
            "QPushButton:hover { background-color: #2e3239; color: #fff; border-color: #555; }"
        )
        self.btn_expand_all.clicked.connect(self._expand_all_sections)

        self.btn_collapse_all = QtWidgets.QPushButton("▶ Collapse All")
        self.btn_collapse_all.setToolTip("Collapse all sections")
        self.btn_collapse_all.setStyleSheet(
            "QPushButton { background-color: #24272c; color: #bbb; border: 1px solid #363a42; "
            "border-radius: 3px; padding: 4px 8px; font-size: 11px; font-weight: bold; } "
            "QPushButton:hover { background-color: #2e3239; color: #fff; border-color: #555; }"
        )
        self.btn_collapse_all.clicked.connect(self._collapse_all_sections)

        title_layout.addWidget(title, 1)
        title_layout.addWidget(self.btn_expand_all, 0)
        title_layout.addWidget(self.btn_collapse_all, 0)
        title_layout.addWidget(self.btn_toggle_crucible, 0)
        title_layout.addWidget(self.btn_read_stage, 0)
        title_layout.addWidget(self.btn_reset_defaults, 0)
        title_layout.addWidget(self.btn_reload, 0)
        self.btn_read_stage.hide()
        main_layout.addLayout(title_layout)

        # Main horizontal splitter
        self.main_splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        self.main_splitter.setChildrenCollapsible(False)
        self.main_splitter.setHandleWidth(6)
        self.main_splitter.setStyleSheet("""
            QSplitter::handle:horizontal {
                background-color: #2b2b2b;
                width: 6px;
                border-left: 1px solid #3c3c3c;
                border-right: 1px solid #1c1c1c;
            }
            QSplitter::handle:horizontal:hover, QSplitter::handle:horizontal:pressed {
                background-color: #e67e22;
                border-color: #f39c12;
            }
        """)

        # Top-level Tab Widget in the left pane
        self.tabs_main = QtWidgets.QTabWidget()
        self.tabs_main.setStyleSheet("""
            QTabWidget::pane {
                border: 1px solid #2d313a;
                background-color: #1e1f23;
                border-radius: 4px;
                padding: 0px;
            }
            QTabBar::tab {
                background-color: #1a1c20;
                color: #9da5b4;
                padding: 8px 18px;
                margin-right: 2px;
                border-top-left-radius: 5px;
                border-top-right-radius: 5px;
                font-weight: bold;
                font-size: 12px;
            }
            QTabBar::tab:selected {
                background-color: #2c313a;
                color: #ffcc44;
                border-bottom: 2px solid #e67e22;
            }
            QTabBar::tab:hover:!selected {
                background-color: #282c34;
                color: #abb2bf;
            }
        """)

        # ---- Tab 1: DomeBreaker (HDRI / Dome / Lighting) ----
        scroll_dome = QtWidgets.QScrollArea()
        scroll_dome.setWidgetResizable(True)
        scroll_dome_widget = QtWidgets.QWidget()
        self._scroll_layout = QtWidgets.QVBoxLayout(scroll_dome_widget)
        self._scroll_layout.setContentsMargins(2, 2, 2, 2)
        self._scroll_layout.setSpacing(4)
        self._collapsible_sections = []

        self._build_preview_section()
        self._build_file_section()
        self._build_calibration_section()
        self._build_horizon_section()
        self._build_softclip_section()
        self._build_sun_section()
        self._build_ground_projection_section()
        self._build_light_extraction_section()
        self._build_aov_section()
        self._build_lookdev_section()
        self._build_action_section()
        self._build_log_section()

        self._scroll_layout.addStretch()
        scroll_dome.setWidget(scroll_dome_widget)
        self.tabs_main.addTab(scroll_dome, "⚡ DomeBreaker")

        # ---- Tab 2: SplatForge (3DGS Environment & Room Reconstructor) ----
        scroll_splat = QtWidgets.QScrollArea()
        scroll_splat.setWidgetResizable(True)
        scroll_splat_widget = QtWidgets.QWidget()
        self._scroll_layout_splat = QtWidgets.QVBoxLayout(scroll_splat_widget)
        self._scroll_layout_splat.setContentsMargins(2, 2, 2, 2)
        self._scroll_layout_splat.setSpacing(4)

        # Temporarily swap _scroll_layout so _build_gaussian_splat_unified_section
        # appends its CollapsibleSection into the splat tab's layout.
        saved_layout = self._scroll_layout
        self._scroll_layout = self._scroll_layout_splat
        self._build_gaussian_splat_unified_section()
        self._scroll_layout = saved_layout

        self._scroll_layout_splat.addStretch()
        scroll_splat.setWidget(scroll_splat_widget)
        self.tabs_main.addTab(scroll_splat, "🔨 SplatForge")

        # ---- Tab 3: HDRI Room Analyzer ----
        scroll_analyzer = QtWidgets.QScrollArea()
        scroll_analyzer.setWidgetResizable(True)
        scroll_analyzer_widget = QtWidgets.QWidget()
        self._scroll_layout_analyzer = QtWidgets.QVBoxLayout(scroll_analyzer_widget)
        self._scroll_layout_analyzer.setContentsMargins(4, 4, 4, 4)
        self._scroll_layout_analyzer.setSpacing(6)
        self._build_hdri_room_analyzer_tab(self._scroll_layout_analyzer)
        self._scroll_layout_analyzer.addStretch()
        scroll_analyzer.setWidget(scroll_analyzer_widget)
        self.tabs_main.addTab(scroll_analyzer, "📐 HDRI Room Analyzer")

        self.main_splitter.addWidget(self.tabs_main)

        # Crucible side panel (Right pane)
        self._build_crucible_section()
        self.main_splitter.addWidget(self._crucible_container)

        self.main_splitter.setStretchFactor(0, 1)
        self.main_splitter.setStretchFactor(1, 1)
        self.main_splitter.setSizes([550, 450])
        main_layout.addWidget(self.main_splitter, 1)



    def _expand_all_sections(self):
        """Expand all collapsible sections in the panel."""
        for sec in getattr(self, '_collapsible_sections', []):
            sec.set_collapsed(False)

    def _collapse_all_sections(self):
        """Collapse all collapsible sections in the panel."""
        for sec in getattr(self, '_collapsible_sections', []):
            sec.set_collapsed(True)

    def _build_file_section(self):
        """File input section with drag & drop support."""
        sec = CollapsibleSection("Input Files", collapsed=False)
        self.grp_files = sec
        self._collapsible_sections.append(sec)
        lay = QtWidgets.QFormLayout(sec.content_widget)
        lay.setContentsMargins(8, 10, 8, 8)

        hdri_row = QtWidgets.QHBoxLayout()
        self.txt_hdri = HDRIDropTarget("Drop HDRI (.exr, .hdr) here or click ...")
        self.txt_hdri.file_dropped.connect(self._on_hdri_dropped)
        self.btn_hdri = QtWidgets.QPushButton("...")
        self.btn_hdri.setMaximumWidth(30)
        self.btn_clear_hdri = QtWidgets.QPushButton("✕")
        self.btn_clear_hdri.setMaximumWidth(26)
        self.btn_clear_hdri.setToolTip("Remove HDRI map from panel and stage")
        self.btn_clear_hdri.setStyleSheet(
            "QPushButton { font-weight: bold; background-color: #2b2b2b; color: #aaaaaa; border-radius: 3px; }"
            "QPushButton:hover { background-color: #552222; color: #ff6666; }"
        )
        self.btn_clear_hdri.clicked.connect(self.clear_hdri)
        hdri_row.addWidget(self.txt_hdri)
        hdri_row.addWidget(self.btn_hdri)
        hdri_row.addWidget(self.btn_clear_hdri)
        lay.addRow("HDRI:", hdri_row)

        plate_row = QtWidgets.QHBoxLayout()
        self.txt_plate = HDRIDropTarget("Drop Plate image here (optional)")
        self.txt_plate.file_dropped.connect(self._on_plate_dropped)
        self.btn_plate = QtWidgets.QPushButton("...")
        self.btn_plate.setMaximumWidth(30)
        self.btn_clear_plate = QtWidgets.QPushButton("✕")
        self.btn_clear_plate.setMaximumWidth(26)
        self.btn_clear_plate.setToolTip("Remove Plate and revert HDRI calibration to original state")
        self.btn_clear_plate.setStyleSheet(
            "QPushButton { font-weight: bold; background-color: #2b2b2b; color: #aaaaaa; border-radius: 3px; }"
            "QPushButton:hover { background-color: #552222; color: #ff6666; }"
        )
        self.btn_clear_plate.clicked.connect(self.clear_plate)
        plate_row.addWidget(self.txt_plate)
        plate_row.addWidget(self.btn_plate)
        plate_row.addWidget(self.btn_clear_plate)
        lay.addRow("Plate:", plate_row)

        self.combo_input_cs = QtWidgets.QComboBox()
        self.combo_input_cs.addItems(["Linear", "ACEScg", "ACES2065-1", "sRGB", "Rec.709"])
        self.combo_input_cs.setToolTip("Input color space of the raw HDRI map")
        lay.addRow("HDRI Space:", self.combo_input_cs)

        self.combo_plate_cs = QtWidgets.QComboBox()
        self.combo_plate_cs.addItems(["sRGB", "ACEScg", "Linear", "Rec.709", "ACES2065-1", "ACEScc", "ACEScct"])
        self.combo_plate_cs.setToolTip("Input color space of the Target Plate image")
        lay.addRow("Plate Space:", self.combo_plate_cs)

        self._scroll_layout.addWidget(sec)

    def _build_gaussian_splat_unified_section(self):
        """Unified SplatForge master section containing shared file input and workflow tabs."""
        sec = CollapsibleSection("SplatForge — 3DGS Scene & Room Reconstructor", collapsed=False)
        self.grp_splat = sec
        self.grp_splat_lights = sec
        self.grp_splat_arch = sec
        self._collapsible_sections.append(sec)

        main_lay = QtWidgets.QVBoxLayout(sec.content_widget)
        main_lay.setContentsMargins(8, 10, 8, 8)
        main_lay.setSpacing(8)

        # 1. Top Shared Splat Source File Header
        file_form = QtWidgets.QFormLayout()
        file_form.setContentsMargins(0, 0, 0, 0)
        file_form.setSpacing(6)

        # Splat file input
        splat_row = QtWidgets.QHBoxLayout()
        self.txt_splat_file = HDRIDropTarget("Drop .ply Gaussian Splat here or click ...")
        self.txt_splat_file.file_dropped.connect(self._on_splat_file_dropped)
        self.btn_splat_browse = QtWidgets.QPushButton("...")
        self.btn_splat_browse.setMaximumWidth(30)
        self.btn_splat_browse.clicked.connect(self._browse_splat_file)
        splat_row.addWidget(self.txt_splat_file)
        splat_row.addWidget(self.btn_splat_browse)
        file_form.addRow("Splat File:", splat_row)

        # Free sample download button
        btn_dl_row = QtWidgets.QHBoxLayout()
        self.btn_download_splat = QtWidgets.QPushButton("📥 Download Free Sample Splat (NVIDIA Flowers)")
        self.btn_download_splat.setStyleSheet(
            "QPushButton { background-color: #2c3e50; color: #ecf0f1; font-weight: bold; padding: 5px; border-radius: 3px; }"
            "QPushButton:hover { background-color: #34495e; color: #3498db; }"
        )
        self.btn_download_splat.setToolTip("Downloads official NVIDIA ProGraphics sample (flowers_1.ply) into $HIP/splats for instant testing.")
        self.btn_download_splat.clicked.connect(self._download_sample_splat_clicked)
        btn_dl_row.addWidget(self.btn_download_splat)
        file_form.addRow("", btn_dl_row)

        # Splat info label
        self.lbl_splat_info = QtWidgets.QLabel("No Gaussian Splat loaded.")
        self.lbl_splat_info.setStyleSheet("color: #888888; font-size: 11px;")
        file_form.addRow("Info:", self.lbl_splat_info)

        # Global Orientation
        self.chk_splat_flip_y = QtWidgets.QCheckBox("Flip Y (COLMAP standard upright)")
        self.chk_splat_flip_y.setChecked(True)
        self.chk_splat_flip_y.setToolTip("Invert Y coordinate to convert standard COLMAP 3DGS coordinates (+Y down) to Houdini / World upright (+Y up).")
        self.chk_splat_flip_y.toggled.connect(self._on_splat_flip_y_toggled)
        file_form.addRow("Orientation:", self.chk_splat_flip_y)

        main_lay.addLayout(file_form)

        # 2. Modern Tabbed Workflow Interface
        self.tabs_splat = QtWidgets.QTabWidget()
        self.tabs_splat.setStyleSheet("""
            QTabWidget::pane {
                border: 1px solid #2d313a;
                background-color: #1a1c20;
                border-radius: 4px;
                padding: 4px;
            }
            QTabBar::tab {
                background-color: #23272e;
                color: #9da5b4;
                padding: 6px 14px;
                margin-right: 2px;
                border-top-left-radius: 4px;
                border-top-right-radius: 4px;
                font-weight: bold;
                font-size: 11px;
            }
            QTabBar::tab:selected {
                background-color: #2c313a;
                color: #e2e4e8;
                border-bottom: 2px solid #e67e22;
            }
            QTabBar::tab:hover:!selected {
                background-color: #282c34;
                color: #abb2bf;
            }
        """)

        # Tab 1: 360° HDRI Dome
        tab_dome = QtWidgets.QWidget()
        lay_dome = QtWidgets.QFormLayout(tab_dome)
        lay_dome.setContentsMargins(6, 8, 6, 6)
        lay_dome.setSpacing(6)
        self._populate_splat_dome_tab(lay_dome)
        self.tabs_splat.addTab(tab_dome, "🌐 360° HDRI Dome")

        # Tab 2: 3D Lights & Cloud (hidden per user request; initialized headless so attributes remain valid)
        self._tab_lights = QtWidgets.QWidget()
        lay_lights = QtWidgets.QFormLayout(self._tab_lights)
        lay_lights.setContentsMargins(6, 8, 6, 6)
        lay_lights.setSpacing(6)
        self._populate_splat_lights_tab(lay_lights)
        # self.tabs_splat.addTab(self._tab_lights, "💡 3D Lights & Cloud")

        # Tab 3: Room Architecture (USD)
        tab_arch = QtWidgets.QWidget()
        lay_arch = QtWidgets.QFormLayout(tab_arch)
        lay_arch.setContentsMargins(6, 8, 6, 6)
        lay_arch.setSpacing(6)
        self._populate_splat_arch_tab(lay_arch)
        self.tabs_splat.addTab(tab_arch, "🏛️ Room Architecture (USD)")

        # Tab 4: Custom Props & Projection
        tab_props = QtWidgets.QWidget()
        lay_props = QtWidgets.QVBoxLayout(tab_props)
        lay_props.setContentsMargins(6, 8, 6, 6)
        lay_props.setSpacing(6)
        self._populate_splat_custom_props_tab(lay_props)
        self.tabs_splat.addTab(tab_props, "🪑 Custom Props & Projection")

        # Tab 5: USD Points & Geometry (hidden per user request; initialized headless so attributes remain valid)
        self._tab_points = QtWidgets.QWidget()
        lay_points = QtWidgets.QVBoxLayout(self._tab_points)
        lay_points.setContentsMargins(6, 8, 6, 6)
        lay_points.setSpacing(6)
        self._populate_splat_points_tab(lay_points)
        # self.tabs_splat.addTab(self._tab_points, "☁️ USD Points & Geometry")

        main_lay.addWidget(self.tabs_splat)
        self._scroll_layout.addWidget(sec)

    def _populate_splat_dome_tab(self, lay):
        """Populate Tab 1: 360° HDRI Dome baking from Splats."""
        # Probe Position (X, Y, Z)
        probe_row = QtWidgets.QHBoxLayout()
        self.sld_probe_x = SliderDoubleSpinBox(-50.0, 50.0, 0.1, 0.0, decimals=2)
        self.sld_probe_y = SliderDoubleSpinBox(-10.0, 50.0, 0.1, 1.5, decimals=2)
        self.sld_probe_z = SliderDoubleSpinBox(-50.0, 50.0, 0.1, 0.0, decimals=2)
        self.sld_probe_x.valueChanged.connect(self._on_probe_slider_changed)
        self.sld_probe_y.valueChanged.connect(self._on_probe_slider_changed)
        self.sld_probe_z.valueChanged.connect(self._on_probe_slider_changed)
        probe_row.addWidget(QtWidgets.QLabel("X:"))
        probe_row.addWidget(self.sld_probe_x)
        probe_row.addWidget(QtWidgets.QLabel("Y:"))
        probe_row.addWidget(self.sld_probe_y)
        probe_row.addWidget(QtWidgets.QLabel("Z:"))
        probe_row.addWidget(self.sld_probe_z)
        lay.addRow("Probe Pos:", probe_row)

        # Viewport probe buttons
        probe_btns_row = QtWidgets.QHBoxLayout()
        self.btn_create_probe = QtWidgets.QPushButton("📍 Select / Create Probe")
        self.btn_create_probe.setToolTip("Create or select interactive hdri_match_splat_probe camera in /stage.")
        self.btn_create_probe.clicked.connect(self._create_or_select_splat_probe)

        self.btn_snap_probe_cam = QtWidgets.QPushButton("📷 Snap to Camera")
        self.btn_snap_probe_cam.setToolTip("Position probe at current Houdini 3D viewport camera position.")
        self.btn_snap_probe_cam.clicked.connect(self._snap_probe_to_viewport_cam)

        self.btn_snap_probe_center = QtWidgets.QPushButton("🎯 Snap to Room Center")
        self.btn_snap_probe_center.setToolTip("Center probe inside the Gaussian Splat room interior (robust median centroid).")
        self.btn_snap_probe_center.clicked.connect(self._snap_probe_to_splat_center)

        self.btn_frame_probe = QtWidgets.QPushButton("🔍 Frame Viewport")
        self.btn_frame_probe.setToolTip("Focus the Houdini Scene Viewer camera directly on the Splat Probe position.")
        self.btn_frame_probe.clicked.connect(self._frame_probe_in_viewport)

        probe_btns_row.addWidget(self.btn_create_probe)
        probe_btns_row.addWidget(self.btn_snap_probe_cam)
        probe_btns_row.addWidget(self.btn_snap_probe_center)
        probe_btns_row.addWidget(self.btn_frame_probe)
        lay.addRow("", probe_btns_row)

        # Resolution
        self.combo_splat_res = QtWidgets.QComboBox()
        self.combo_splat_res.addItems([
            "1024 x 512 (Fast)",
            "2048 x 1024 (Production)",
            "4096 x 2048 (High-Res)",
            "8192 x 4096 (8K Ultra)",
        ])
        self.combo_splat_res.setCurrentIndex(1)
        lay.addRow("Resolution:", self.combo_splat_res)

        # Splat Scale / Footprint Multiplier
        self.sld_splat_scale = SliderDoubleSpinBox(0.5, 5.0, 0.1, 1.8, decimals=1)
        self.sld_splat_scale.setToolTip("Multiplier on splat footprint to ensure dense, continuous surfaces without gaps (default 1.8x).")
        lay.addRow("Splat Scale:", self.sld_splat_scale)

        # Near Clipping
        self.sld_splat_min_dist = SliderDoubleSpinBox(0.05, 5.0, 0.05, 0.35, decimals=2)
        self.sld_splat_min_dist.setToolTip("Near clipping distance to prevent foreground points from forming large bubbles (default 0.35m).")
        lay.addRow("Near Clip (m):", self.sld_splat_min_dist)

        # Polar Infill (Zenith & Nadir)
        self.chk_splat_inpaint_poles = QtWidgets.QCheckBox("Infill Polar Gaps (Zenith & Nadir)")
        self.chk_splat_inpaint_poles.setChecked(True)
        self.chk_splat_inpaint_poles.setToolTip("Automatically inpaint and diffuse boundary colors into the zenith (top) and nadir (bottom) to eliminate black poles.")
        lay.addRow("Polar Infill:", self.chk_splat_inpaint_poles)

        # Action buttons for HDRI Baking and Viewport Splat Cloud Display
        cloud_btns_row = QtWidgets.QHBoxLayout()
        self.btn_vis_splat_scene = QtWidgets.QPushButton("👁️ Show Splat Cloud")
        self.btn_vis_splat_scene.setStyleSheet(
            "QPushButton { background-color: #2980b9; color: white; font-weight: bold; padding: 7px; border-radius: 4px; }"
            "QPushButton:hover { background-color: #3498db; }"
        )
        self.btn_vis_splat_scene.setToolTip("Toggle or load the 3D Gaussian Splat scene point cloud into Solaris /stage so you can see the room, walls, and probe position in real-time.")
        self.btn_vis_splat_scene.clicked.connect(self._toggle_splat_scene_vis)

        self.btn_display_bakegs = QtWidgets.QPushButton("🔮 Display Native Splats (BakeGS)")
        self.btn_display_bakegs.setStyleSheet(
            "QPushButton { background-color: #8e44ad; color: white; font-weight: bold; padding: 7px; border-radius: 4px; }"
            "QPushButton:hover { background-color: #9b59b6; }"
        )
        self.btn_display_bakegs.setToolTip("Display Gaussian Splats using Houdini's native Bake GSplats reader (bakegsplat SOP). Encodes native GS_Alpha, scale, orient, and spherical harmonics for real-time viewport display and Karma XPU rendering.")
        self.btn_display_bakegs.clicked.connect(self._display_splats_bakegs_clicked)

        cloud_btns_row.addWidget(self.btn_vis_splat_scene)
        cloud_btns_row.addWidget(self.btn_display_bakegs)
        lay.addRow("Viewport Splats:", cloud_btns_row)

        actions_row = QtWidgets.QHBoxLayout()
        self.btn_bake_splat = QtWidgets.QPushButton("🌐 Bake 360° HDRI from Splat")
        self.btn_bake_splat.setStyleSheet(
            "QPushButton { background-color: #16a085; color: white; font-weight: bold; padding: 6px; border-radius: 4px; }"
            "QPushButton:hover { background-color: #1abc9c; }"
        )
        self.btn_bake_splat.setToolTip("Bake equirectangular 360° OpenEXR from the probe position and load directly into Dome Light.")
        self.btn_bake_splat.clicked.connect(self._bake_splat_to_hdri)
        actions_row.addWidget(self.btn_bake_splat)
        lay.addRow("", actions_row)

        # Splat Bake Progress Bar
        self.pbar_splat = QtWidgets.QProgressBar()
        self.pbar_splat.setRange(0, 100)
        self.pbar_splat.setValue(0)
        self.pbar_splat.setTextVisible(True)
        self.pbar_splat.setFormat("Baking 360° HDRI: %p%")
        self.pbar_splat.setFixedHeight(18)
        self.pbar_splat.setStyleSheet("""
            QProgressBar {
                border: 1px solid #2c3e50;
                border-radius: 4px;
                background-color: #121418;
                text-align: center;
                color: #ffffff;
                font-size: 11px;
                font-weight: bold;
            }
            QProgressBar::chunk {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #16a085, stop:1 #1abc9c);
                border-radius: 3px;
            }
        """)
        self.pbar_splat.setVisible(False)
        lay.addRow("", self.pbar_splat)

    def _populate_splat_lights_tab(self, lay):
        """Populate Tab 2: 3D Lights extraction and Viewport Point Cloud."""
        # Quick Action buttons right at the top of section
        lt_actions_row = QtWidgets.QHBoxLayout()
        self.btn_bake_splat_usd = QtWidgets.QPushButton("📦 Bake USD Cloud")
        self.btn_bake_splat_usd.setStyleSheet(
            "QPushButton { background-color: #8e44ad; color: white; font-weight: bold; padding: 7px; border-radius: 4px; }"
            "QPushButton:hover { background-color: #9b59b6; }"
        )
        self.btn_bake_splat_usd.setToolTip("Explicitly bake or re-bake the active Gaussian Splat scene into an optimized native USD point cloud file (.usd).")
        self.btn_bake_splat_usd.clicked.connect(self._bake_splat_usd_clicked)

        self.btn_extract_splat_lights = QtWidgets.QPushButton("💡 Extract USD Lights")
        self.btn_extract_splat_lights.setStyleSheet(
            "QPushButton { background-color: #d35400; color: white; font-weight: bold; padding: 7px; border-radius: 4px; }"
            "QPushButton:hover { background-color: #e67e22; }"
        )
        self.btn_extract_splat_lights.setToolTip("Analyze 3D Gaussian Splats, infer physical dimensions and normals, and spawn USD RectLights in /stage.")
        self.btn_extract_splat_lights.clicked.connect(self._extract_splat_3d_lights)

        self.btn_clear_splat_lights = QtWidgets.QPushButton("🗑️ Clear Splat Lights")
        self.btn_clear_splat_lights.setStyleSheet(
            "QPushButton { background-color: #7f8c8d; color: white; font-weight: bold; padding: 7px; border-radius: 4px; }"
            "QPushButton:hover { background-color: #95a5a6; }"
        )
        self.btn_clear_splat_lights.setToolTip("Remove all generated splat 3D lights and debug visualization from /stage.")
        self.btn_clear_splat_lights.clicked.connect(self._clear_splat_lights_clicked)

        lt_actions_row.addWidget(self.btn_bake_splat_usd)
        lt_actions_row.addWidget(self.btn_extract_splat_lights)
        lt_actions_row.addWidget(self.btn_clear_splat_lights)
        lay.addRow("Actions:", lt_actions_row)

        # Direction & Debug Options
        opt_row = QtWidgets.QHBoxLayout()
        self.chk_splat_scene_vis = QtWidgets.QCheckBox("Auto-Load Splat Cloud in Viewport")
        self.chk_splat_scene_vis.setChecked(True)
        self.chk_splat_scene_vis.setToolTip("Automatically bake and load the 3D Gaussian Splat scene point cloud into Solaris /stage so the full room geometry is visible in real-time.")
        self.chk_splat_scene_vis.toggled.connect(self._on_splat_scene_vis_toggled)

        self.chk_splat_debug_vis = QtWidgets.QCheckBox("Debug Vis in /stage")
        self.chk_splat_debug_vis.setChecked(True)
        self.chk_splat_debug_vis.setToolTip("Create interactive colored 3D debug geometry in Solaris showing cluster splats, centroids, normal arrows, and rect wireframes.")

        self.chk_splat_flip_direction = QtWidgets.QCheckBox("Flip Normal")
        self.chk_splat_flip_direction.setChecked(False)
        self.chk_splat_flip_direction.setToolTip("Invert the inferred light emission normal direction.")

        opt_row.addWidget(self.chk_splat_scene_vis)
        opt_row.addWidget(self.chk_splat_debug_vis)
        opt_row.addWidget(self.chk_splat_flip_direction)
        lay.addRow("Options:", opt_row)

        # Light Type
        self.combo_splat_light_type = QtWidgets.QComboBox()
        self.combo_splat_light_type.addItems([
            "Rectangular Lights (UsdLuxRectLight)",
            "Point / Sphere Lights (UsdLuxSphereLight)"
        ])
        self.combo_splat_light_type.setToolTip("Select type of USD light primitive to instantiate in /stage. Rectangular lights infer physical width, height, and orientation.")
        lay.addRow("Light Type:", self.combo_splat_light_type)

        # Brightness Threshold
        self.sld_splat_luma_thresh = SliderDoubleSpinBox(0.1, 2.0, 0.05, 0.65, decimals=2)
        self.sld_splat_luma_thresh.setToolTip("Luminance cutoff for candidate emissive splats. Higher values capture peak emitters; lower values capture broader ambient fixtures.")
        lay.addRow("Brightness Thresh:", self.sld_splat_luma_thresh)

        # Cluster Distance
        self.sld_splat_cluster_dist = SliderDoubleSpinBox(0.1, 3.0, 0.05, 0.60, decimals=2)
        self.sld_splat_cluster_dist.setToolTip("3D spatial voxel clustering distance in meters. Splats within this distance group into a single continuous physical light source.")
        lay.addRow("Cluster Dist (m):", self.sld_splat_cluster_dist)

        # Min Points & Max Lights row
        count_row = QtWidgets.QHBoxLayout()
        self.sld_splat_min_points = SliderDoubleSpinBox(5.0, 100.0, 1.0, 12.0, decimals=0)
        self.sld_splat_min_points.setToolTip("Minimum number of bright splats required to constitute a valid light source (filters single-point specular noise).")
        self.sld_splat_max_lights = SliderDoubleSpinBox(1.0, 20.0, 1.0, 6.0, decimals=0)
        self.sld_splat_max_lights.setToolTip("Maximum number of dominant USD light fixtures to reconstruct.")
        count_row.addWidget(QtWidgets.QLabel("Min Splats:"))
        count_row.addWidget(self.sld_splat_min_points)
        count_row.addWidget(QtWidgets.QLabel("Max Lights:"))
        count_row.addWidget(self.sld_splat_max_lights)
        lay.addRow("Extraction Limits:", count_row)

        # Calibration Multipliers: Intensity & Color
        cal_row = QtWidgets.QHBoxLayout()
        self.sld_splat_intensity_mult = SliderDoubleSpinBox(0.1, 20.0, 0.1, 1.0, decimals=1)
        self.sld_splat_intensity_mult.setToolTip("Global radiant intensity multiplier for the extracted lights.")
        self.sld_splat_color_mult = SliderDoubleSpinBox(0.1, 5.0, 0.1, 1.0, decimals=1)
        self.sld_splat_color_mult.setToolTip("Color chromaticity/saturation multiplier.")
        cal_row.addWidget(QtWidgets.QLabel("Intensity:"))
        cal_row.addWidget(self.sld_splat_intensity_mult)
        cal_row.addWidget(QtWidgets.QLabel("Color:"))
        cal_row.addWidget(self.sld_splat_color_mult)
        lay.addRow("Calibration:", cal_row)

        # Size Scale Multiplier
        self.sld_splat_size_scale = SliderDoubleSpinBox(0.1, 5.0, 0.1, 1.0, decimals=1)
        self.sld_splat_size_scale.setToolTip("Scale factor applied to inferred rectangular light width and height.")
        lay.addRow("Size Scale:", self.sld_splat_size_scale)

    def _populate_splat_arch_tab(self, lay):
        """Populate Tab 3: Room Architecture (USD) reconstruction."""
        # Action buttons
        actions_row = QtWidgets.QHBoxLayout()
        self.btn_build_room_arch = QtWidgets.QPushButton("🏛️ Analyze & Build Room Architecture")
        self.btn_build_room_arch.setStyleSheet(
            "QPushButton { background-color: #27ae60; color: white; font-weight: bold; padding: 7px; border-radius: 4px; }"
            "QPushButton:hover { background-color: #2ecc71; }"
        )
        self.btn_build_room_arch.setToolTip("Analyze splats to detect floor, ceiling, 4 walls, and window openings, and construct physical USD geometry in /stage/room.")
        self.btn_build_room_arch.clicked.connect(self._build_room_architecture_clicked)

        self.btn_clear_room_arch = QtWidgets.QPushButton("🗑️ Clear Room Architecture")
        self.btn_clear_room_arch.setStyleSheet(
            "QPushButton { background-color: #7f8c8d; color: white; font-weight: bold; padding: 7px; border-radius: 4px; }"
            "QPushButton:hover { background-color: #95a5a6; }"
        )
        self.btn_clear_room_arch.setToolTip("Remove /stage/room geometry and restore stage wiring.")
        self.btn_clear_room_arch.clicked.connect(self._clear_room_architecture_clicked)

        self.btn_assign_room_textures = QtWidgets.QPushButton("🎨 Assign HDRI & Portal Textures")
        self.btn_assign_room_textures.setVisible(False)
        self.btn_assign_room_textures.setStyleSheet(
            "QPushButton { background-color: #2980b9; color: white; font-weight: bold; padding: 7px; border-radius: 4px; }"
            "QPushButton:hover { background-color: #3498db; }"
        )
        self.btn_assign_room_textures.setToolTip(
            "Assign the baked 360° HDRI map (with albedo compression) to interior meshes (floor, ceiling, walls) "
            "and automatically crop and bind HDR textures to window portal lights."
        )
        self.btn_assign_room_textures.clicked.connect(self._assign_room_textures_clicked)

        actions_row.addWidget(self.btn_build_room_arch)
        # Texture & portal assignment is now automated in Analyze & Build Room Architecture
        actions_row.addWidget(self.btn_clear_room_arch)
        lay.addRow("Actions:", actions_row)

        # Status / Dimensions Readout
        self.lbl_arch_status = QtWidgets.QLabel("No room architecture generated yet.")
        self.lbl_arch_status.setStyleSheet("color: #888888; font-size: 11px;")
        lay.addRow("Status:", self.lbl_arch_status)

        # Dimension Sliders (auto-populated by analysis)
        dim_row_1 = QtWidgets.QHBoxLayout()
        self.sld_arch_width = SliderDoubleSpinBox(0.5, 500.0, 0.1, 10.0, decimals=2)
        self.sld_arch_depth = SliderDoubleSpinBox(0.5, 500.0, 0.1, 10.0, decimals=2)
        dim_row_1.addWidget(QtWidgets.QLabel("Width (X):"))
        dim_row_1.addWidget(self.sld_arch_width)
        dim_row_1.addWidget(QtWidgets.QLabel("Depth (Z):"))
        dim_row_1.addWidget(self.sld_arch_depth)
        lay.addRow("Room Size:", dim_row_1)

        dim_row_2 = QtWidgets.QHBoxLayout()
        self.sld_arch_height = SliderDoubleSpinBox(0.5, 100.0, 0.1, 3.0, decimals=2)
        self.sld_arch_floor_y = SliderDoubleSpinBox(-100.0, 100.0, 0.1, 0.0, decimals=2)
        dim_row_2.addWidget(QtWidgets.QLabel("Height (Y):"))
        dim_row_2.addWidget(self.sld_arch_height)
        dim_row_2.addWidget(QtWidgets.QLabel("Floor Y:"))
        dim_row_2.addWidget(self.sld_arch_floor_y)
        lay.addRow("Elevation:", dim_row_2)

        # Architectural Component Toggles
        comp_row_1 = QtWidgets.QHBoxLayout()
        self.chk_arch_floor = QtWidgets.QCheckBox("Floor")
        self.chk_arch_floor.setChecked(True)
        self.chk_arch_floor.setToolTip("Generate floor surface at detected floor height.")

        self.combo_arch_floor_mode = QtWidgets.QComboBox()
        self.combo_arch_floor_mode.addItems([
            "Visible Surface (Projected HDRI)",
            "Shadow Catcher Matte (Invisible)"
        ])
        self.combo_arch_floor_mode.setToolTip(
            "• Visible Surface (Projected HDRI): Floor is fully visible in both viewport AND render, textured with the room's baked HDRI, and receives contact shadows and GI from CG objects.\n"
            "• Shadow Catcher Matte (Invisible): Floor is an invisible matte plane for camera rays that only catches shadows onto the background dome."
        )

        self.chk_arch_ceiling = QtWidgets.QCheckBox("Ceiling")
        self.chk_arch_ceiling.setChecked(True)
        self.chk_arch_ceiling.setToolTip("Generate ceiling quad at detected ceiling height.")

        self.chk_arch_walls = QtWidgets.QCheckBox("Perimeter Walls")
        self.chk_arch_walls.setChecked(True)
        self.chk_arch_walls.setToolTip("Generate 4 perimeter walls (West, East, North, South).")

        comp_row_1.addWidget(self.chk_arch_floor)
        comp_row_1.addWidget(self.combo_arch_floor_mode)
        comp_row_1.addWidget(self.chk_arch_ceiling)
        comp_row_1.addWidget(self.chk_arch_walls)
        lay.addRow("Components:", comp_row_1)

        comp_row_2 = QtWidgets.QHBoxLayout()
        self.chk_arch_windows = QtWidgets.QCheckBox("Cut Window Openings")
        self.chk_arch_windows.setChecked(True)
        self.chk_arch_windows.setToolTip("Cut physical rectangular holes in walls where daylight window apertures are detected.")

        self.chk_arch_portals = QtWidgets.QCheckBox("Window Portal Lights")
        self.chk_arch_portals.setChecked(True)
        self.chk_arch_portals.setToolTip("Place inward-emitting UsdLuxRectLight portals inside the window openings.")

        self.chk_arch_props = QtWidgets.QCheckBox("Interior Props & Columns")
        self.chk_arch_props.setChecked(True)
        self.chk_arch_props.setToolTip("Extract and reconstruct 3D geometry for detected interior props, tables, shelves, and columns.")

        self.combo_arch_proxy_shape = QtWidgets.QComboBox()
        self.combo_arch_proxy_shape.addItems([
            "⚡ Auto 3D Meshes (VDB & Depth Map)",
            "⚡ Top-Down Depth Map Meshes (Counters & Tables)",
            "⚡ OpenVDB Volumetric Meshes (Organic & Fixtures)",
            "Cylinder Columns & Boxes (Primitive Proxy)",
            "Boxes Only (Oriented Bounding Boxes)",
            "Spheres / Rounded (Proxy)",
        ])
        self.combo_arch_proxy_shape.setToolTip(
            "Geometry representation for detected interior props:\n"
            "• ⚡ Auto 3D Meshes: Watertight Depth Map heightfields with floor skirts for tables/desks, OpenVDB volumetric SDF meshes for plants/organic props.\n"
            "• ⚡ Top-Down Depth Map: 2.5D elevation heightfield with vertical floor skirts (ideal for tables & flat counters).\n"
            "• ⚡ OpenVDB Volumetric: True 3D OpenVDB particle SDF meshing (ideal for trees, plants, fixtures, organic sculptures).\n"
            "• Primitive Proxies: Lightweight cylinder, box, or sphere bounding primitives."
        )

        self.lbl_arch_prop_res = QtWidgets.QLabel("Mesh Res:")
        self.sld_arch_prop_res = SliderDoubleSpinBox(0.01, 0.15, 0.005, 0.035, decimals=3)
        self.sld_arch_prop_res.setToolTip("Mesh resolution in meters for reconstructed 3D prop geometry (e.g. 0.035m = 3.5cm voxels).")

        def _update_prop_res_visibility():
            txt = self.combo_arch_proxy_shape.currentText()
            is_mesh = "3D Mesh" in txt or "Meshes" in txt
            self.lbl_arch_prop_res.setEnabled(is_mesh)
            self.sld_arch_prop_res.setEnabled(is_mesh)

        self.combo_arch_proxy_shape.currentIndexChanged.connect(lambda _: _update_prop_res_visibility())

        self.chk_arch_snap_lookdev = QtWidgets.QCheckBox("Snap Lookdev Rig to Floor")
        self.chk_arch_snap_lookdev.setChecked(True)
        self.chk_arch_snap_lookdev.setToolTip("Automatically snap lookdev balls and stand directly onto the detected splat floor.")

        comp_row_2.addWidget(self.chk_arch_windows)
        comp_row_2.addWidget(self.chk_arch_portals)
        comp_row_2.addWidget(self.chk_arch_props)
        comp_row_2.addWidget(self.combo_arch_proxy_shape)
        comp_row_2.addWidget(self.lbl_arch_prop_res)
        comp_row_2.addWidget(self.sld_arch_prop_res)
        comp_row_2.addWidget(self.chk_arch_snap_lookdev)
        lay.addRow("Features:", comp_row_2)

        # HDRI Texture Projection & Material Options
        proj_row = QtWidgets.QHBoxLayout()
        self.chk_arch_project_hdri = QtWidgets.QCheckBox("Project Textures onto Room")
        self.chk_arch_project_hdri.setChecked(True)
        self.chk_arch_project_hdri.setToolTip(
            "Project baked splat textures directly onto the floor, ceiling, and walls. "
            "Both viewport and render will show the textured room with full USD shading and vertex colors."
        )

        self.combo_arch_tex_mode = QtWidgets.QComboBox()
        self.combo_arch_tex_mode.addItems([
            "Planar High-Detail (Floor/Ceiling/Walls)",
            "Equirectangular (Camera Projection)",
        ])
        self.combo_arch_tex_mode.setToolTip(
            "• Planar High-Detail: Rectilinear Cartesian textures baked per wall/surface (zero polar distortion, crystal clear).\n"
            "• Equirectangular: 360° spherical camera projection."
        )
        self.combo_arch_tex_mode.currentIndexChanged.connect(self._on_arch_material_changed)

        self.sld_arch_roughness = SliderDoubleSpinBox(0.0, 1.0, 0.05, 0.85, decimals=2)
        self.sld_arch_roughness.setToolTip("Surface roughness of the projected room material (1.00 = matte diffuse, 0.30 = polished/reflective floor).")
        proj_row.addWidget(self.chk_arch_project_hdri)
        proj_row.addWidget(self.combo_arch_tex_mode)
        proj_row.addWidget(QtWidgets.QLabel("Roughness:"))
        proj_row.addWidget(self.sld_arch_roughness)
        lay.addRow("Projection:", proj_row)

        # Material Mode & Emission Options
        mat_row = QtWidgets.QHBoxLayout()
        self.combo_arch_mat_mode = QtWidgets.QComboBox()
        self.combo_arch_mat_mode.addItems([
            "PBR (Lit Diffuse)",
            "Emissive (Unlit / GI Emitter)",
            "PBR + Emissive Fill",
        ])
        self.combo_arch_mat_mode.setToolTip(
            "Choose USD shader shading model:\n"
            "• PBR (Lit Diffuse): Diffuse surface lit by exterior dome light, distant sun, and window portal rect lights.\n"
            "• Emissive (Unlit / GI Emitter): Directly emits texture radiance (constant unlit projection screen, interior bounces illumine lookdev rig without external lights).\n"
            "• PBR + Emissive Fill: Hybrid shading combining diffuse reflection with emissive fill radiance."
        )
        self.combo_arch_mat_mode.currentIndexChanged.connect(self._on_arch_material_changed)

        self.sld_arch_emissive_mult = SliderDoubleSpinBox(0.0, 20.0, 0.1, 1.0, decimals=2)
        self.sld_arch_emissive_mult.setToolTip("Multiplier for emissive texture radiance (1.0 = baseline HDR intensity).")
        self.sld_arch_emissive_mult.valueChanged.connect(self._on_arch_material_changed)

        mat_row.addWidget(self.combo_arch_mat_mode)
        mat_row.addWidget(QtWidgets.QLabel("Emissive Mult:"))
        mat_row.addWidget(self.sld_arch_emissive_mult)
        lay.addRow("Material:", mat_row)

        # Renderer target selector
        self.combo_arch_renderer_target = QtWidgets.QComboBox()
        self.combo_arch_renderer_target.addItems([
            "All Renderers (Arnold + Karma + Redshift)",
            "Arnold",
            "Karma (MaterialX)",
            "Redshift",
            "USD Preview Only",
        ])
        self.combo_arch_renderer_target.setToolTip(
            "Target renderer for Gaussian Splatting architecture material networks.\n"
            "Creates native shaders wired to renderer-specific surface outputs."
        )
        self.combo_arch_renderer_target.currentIndexChanged.connect(self._on_arch_material_changed)
        lay.addRow("Renderer Target:", self.combo_arch_renderer_target)

        # Planar Texture Baking Controls
        bake_planar_row = QtWidgets.QHBoxLayout()
        self.combo_arch_planar_res = QtWidgets.QComboBox()
        self.combo_arch_planar_res.addItems([
            "8K (8192×8192)",
            "4K (4096×4096)",
            "2K (2048×2048)",
            "1K (1024×1024)",
        ])
        self.combo_arch_planar_res.setToolTip("Resolution per planar rectilinear surface texture map (width & height).")
        self.combo_arch_planar_res.currentIndexChanged.connect(lambda: setattr(self, '_planar_textures_dict', None))

        self.btn_bake_planar = QtWidgets.QPushButton("🎨 Bake Planar Textures (High Detail)")
        self.btn_bake_planar.setVisible(False)
        self.btn_bake_planar.setStyleSheet(
            "QPushButton { background-color: #27ae60; color: white; font-weight: bold; padding: 4px 8px; border-radius: 4px; }"
            "QPushButton:hover { background-color: #2ecc71; }"
        )
        self.btn_bake_planar.setToolTip("Bake distortion-free rectilinear OpenEXR textures for floor, ceiling, and all 4 walls.")
        self.btn_bake_planar.clicked.connect(self._bake_planar_room_textures_clicked)

        bake_planar_row.addWidget(QtWidgets.QLabel("Planar Res:"))
        bake_planar_row.addWidget(self.combo_arch_planar_res)
        lbl_auto_bake = QtWidgets.QLabel("<span style='color: #2ecc71; font-size: 10px; font-weight: bold;'>[Auto-baked on Analyze & Build]</span>")
        lbl_auto_bake.setToolTip("Planar textures are automatically baked at this resolution whenever Analyze & Build Room Architecture is clicked.")
        bake_planar_row.addWidget(lbl_auto_bake)
        bake_planar_row.addStretch()
        lay.addRow("Planar Maps:", bake_planar_row)

        self.pbar_planar = QtWidgets.QProgressBar()
        self.pbar_planar.setRange(0, 100)
        self.pbar_planar.setValue(0)
        self.pbar_planar.setTextVisible(True)
        self.pbar_planar.setFormat("Planar Textures Ready")
        self.pbar_planar.setVisible(False)
        lay.addRow("", self.pbar_planar)

        # Visibility & Rendering Options
        opt_row = QtWidgets.QHBoxLayout()
        self.chk_arch_double_sided = QtWidgets.QCheckBox("Double-Sided Walls")
        self.chk_arch_double_sided.setChecked(True)
        self.chk_arch_double_sided.setToolTip("When checked (default), walls are visible and shaded from both outside and inside, preventing black backfaces.")

        self.chk_arch_shadows = QtWidgets.QCheckBox("Room Casts Shadows")
        self.chk_arch_shadows.setChecked(True)
        self.chk_arch_shadows.setToolTip("When checked (default), walls and room architecture cast shadows realistically into the scene.")

        self.chk_arch_invisible = QtWidgets.QCheckBox("Invisible Room")
        self.chk_arch_invisible.setChecked(False)
        self.chk_arch_invisible.setToolTip("When checked, the room is invisible to render camera (primary rays) for Arnold, Karma, and Redshift.")

        opt_row.addWidget(self.chk_arch_double_sided)
        opt_row.addWidget(self.chk_arch_shadows)
        opt_row.addWidget(self.chk_arch_invisible)
        lay.addRow("Shading:", opt_row)

        self.chk_arch_double_sided.toggled.connect(self._on_arch_shading_toggled)
        self.chk_arch_shadows.toggled.connect(self._on_arch_shading_toggled)
        self.chk_arch_invisible.toggled.connect(self._on_arch_shading_toggled)

        self.sld_arch_portal_intensity = SliderDoubleSpinBox(0.01, 10.0, 0.1, 1.0, decimals=2)
        self.sld_arch_portal_intensity.setToolTip("Intensity multiplier for physical window portal rect lights (default 1.0 matches calibrated HDR texture radiance).")
        self.sld_arch_portal_intensity.valueChanged.connect(self._on_portal_intensity_changed)
        lay.addRow("Portal Intensity:", self.sld_arch_portal_intensity)

        portal_tex_row = QtWidgets.QHBoxLayout()
        self.combo_arch_portal_texture = QtWidgets.QComboBox()
        self.combo_arch_portal_texture.addItems([
            "Cropped Window View (HDRI)",
            "Full HDRI Map",
            "No Texture (Uniform Color)"
        ])
        self.combo_arch_portal_texture.setToolTip(
            "• Cropped Window View (HDRI): Automatically extracts and bakes a perspective-correct HDR texture from the HDRI for each window aperture, matching the exterior view.\n"
            "• Full HDRI Map: Assigns the complete equirectangular HDRI map directly to inputs:texture:file.\n"
            "• No Texture: Pure uniform colored area light."
        )
        self.combo_arch_portal_texture.currentIndexChanged.connect(self._on_portal_texture_mode_changed)
        portal_tex_row.addWidget(self.combo_arch_portal_texture)
        lay.addRow("Portal Texture:", portal_tex_row)

        # Metric Scale Calibration & Interior Bounding
        scale_row = QtWidgets.QHBoxLayout()
        self.sld_arch_scale = SliderDoubleSpinBox(0.001, 100.0, 0.005, 1.0, decimals=4)
        self.sld_arch_scale.setToolTip("Scale factor applied to splat point cloud to calibrate into Houdini meters (default 1.0 = 1 unit is 1 meter).")

        self.btn_arch_autoscale = QtWidgets.QPushButton("📐 Auto-Scale (2.9m Ceiling)")
        self.btn_arch_autoscale.setStyleSheet(
            "QPushButton { background-color: #2980b9; color: white; font-weight: bold; padding: 4px 8px; border-radius: 4px; }"
            "QPushButton:hover { background-color: #3498db; }"
        )
        self.btn_arch_autoscale.setToolTip("Analyze splats to find unscaled ceiling height and automatically compute the scale so ceiling is 2.9m.")
        self.btn_arch_autoscale.clicked.connect(self._auto_scale_room_clicked)

        self.combo_arch_interior_mode = QtWidgets.QComboBox()
        self.combo_arch_interior_mode.addItems([
            "Tight Interior (5%-95%, Discards Window Outliers)",
            "Full Scene Extent (2%-98%)"
        ])
        self.combo_arch_interior_mode.setToolTip("Tight Interior mode discards exterior points visible through large glass windows, ensuring walls match the actual interior room.")
        scale_row.addWidget(QtWidgets.QLabel("Unit Scale:"))
        scale_row.addWidget(self.sld_arch_scale)
        scale_row.addWidget(self.btn_arch_autoscale)
        scale_row.addWidget(QtWidgets.QLabel("Bounds:"))
        scale_row.addWidget(self.combo_arch_interior_mode)
        lay.addRow("Calibration:", scale_row)

        # Lookdev Rig Controls (Ref Balls & Stand)
        ld_row_1 = QtWidgets.QHBoxLayout()
        self.sld_splat_lookdev_radius = SliderDoubleSpinBox(0.05, 1.5, 0.05, 0.15, decimals=2)
        self.sld_splat_lookdev_radius.setToolTip("Radius of the lookdev reference spheres in meters (default 0.15m = 30cm diameter).")
        self.sld_splat_lookdev_height = SliderDoubleSpinBox(0.0, 3.0, 0.1, 1.2, decimals=2)
        self.sld_splat_lookdev_height.setToolTip("Height of the stand mast from floor to sphere mounting bar (default 1.2m chest/eye height).")
        self.sld_splat_lookdev_radius.valueChanged.connect(self._on_splat_lookdev_param_changed)
        self.sld_splat_lookdev_height.valueChanged.connect(self._on_splat_lookdev_param_changed)
        ld_row_1.addWidget(QtWidgets.QLabel("Ball Radius (m):"))
        ld_row_1.addWidget(self.sld_splat_lookdev_radius)
        ld_row_1.addWidget(QtWidgets.QLabel("Stand Height (m):"))
        ld_row_1.addWidget(self.sld_splat_lookdev_height)
        lay.addRow("Lookdev Rig:", ld_row_1)

        ld_row_2 = QtWidgets.QHBoxLayout()
        self.sld_splat_lookdev_scale = SliderDoubleSpinBox(0.2, 10.0, 0.1, 1.0, decimals=2)
        self.sld_splat_lookdev_scale.setToolTip("Global scale multiplier for the entire lookdev verification rig.")
        self.sld_splat_lookdev_scale.valueChanged.connect(self._on_splat_lookdev_param_changed)
        self.chk_splat_lookdev_macbeth = QtWidgets.QCheckBox("Macbeth Chart")
        self.chk_splat_lookdev_macbeth.setChecked(True)
        self.chk_splat_lookdev_macbeth.toggled.connect(self._on_splat_lookdev_param_changed)
        self.chk_splat_lookdev_white = QtWidgets.QCheckBox("White Ball")
        self.chk_splat_lookdev_white.setChecked(True)
        self.chk_splat_lookdev_white.toggled.connect(self._on_splat_lookdev_param_changed)

        self.btn_splat_update_lookdev = QtWidgets.QPushButton("🎯 Update Lookdev Rig")
        self.btn_splat_update_lookdev.setStyleSheet(
            "QPushButton { background-color: #8e44ad; color: white; font-weight: bold; padding: 5px; border-radius: 4px; }"
            "QPushButton:hover { background-color: #9b59b6; }"
        )
        self.btn_splat_update_lookdev.setToolTip("Update or respawn the Lookdev Verification Rig in /stage with current dimensions and tripod stand.")
        self.btn_splat_update_lookdev.clicked.connect(self._update_lookdev_rig_clicked)

        ld_row_2.addWidget(QtWidgets.QLabel("Rig Scale:"))
        ld_row_2.addWidget(self.sld_splat_lookdev_scale)
        ld_row_2.addWidget(self.chk_splat_lookdev_macbeth)
        ld_row_2.addWidget(self.chk_splat_lookdev_white)
        ld_row_2.addWidget(self.btn_splat_update_lookdev)
        lay.addRow("Lookdev Sizing:", ld_row_2)

    def _build_gaussian_splat_section(self):
        """Backwards compatibility stub."""
        pass

    def _build_gaussian_splat_lights_section(self):
        """Backwards compatibility stub."""
        pass

    def _build_gaussian_splat_architecture_section(self):
        """Backwards compatibility stub."""
        pass

    def _populate_splat_custom_props_tab(self, lay):
        """Populate Tab 4: Custom Props & Gaussian Splat Projection Texturing."""
        info_lbl = QtWidgets.QLabel(
            "Load custom 3D models (USD, OBJ, FBX, BGEO, ABC) into the room architecture. "
            "Props automatically snap to the floor, receive projected splat textures, and cast contact shadows in Karma."
        )
        info_lbl.setWordWrap(True)
        info_lbl.setStyleSheet("color: #abb2bf; font-size: 11px; margin-bottom: 4px;")
        lay.addWidget(info_lbl)

        # Action Buttons Row 1: Add, Pick, Remove, Clear
        btn_row = QtWidgets.QHBoxLayout()
        self.btn_add_custom_prop = QtWidgets.QPushButton("➕ Add 3D Models...")
        self.btn_add_custom_prop.setStyleSheet(
            "QPushButton { background-color: #27ae60; color: white; font-weight: bold; padding: 6px; border-radius: 4px; }"
            "QPushButton:hover { background-color: #2ecc71; }"
        )
        self.btn_add_custom_prop.setToolTip("Browse and add one or multiple 3D models (.usd, .obj, .fbx, .bgeo, .abc).")
        self.btn_add_custom_prop.clicked.connect(self._on_add_custom_props_clicked)

        self.btn_pick_stage_prop = QtWidgets.QPushButton("🎯 Pick Stage Prim...")
        self.btn_pick_stage_prop.setStyleSheet(
            "QPushButton { background-color: #2980b9; color: white; font-weight: bold; padding: 6px; border-radius: 4px; }"
            "QPushButton:hover { background-color: #3498db; }"
        )
        self.btn_pick_stage_prop.setToolTip("Pick an existing USD prim or file from the Solaris stage.")
        self.btn_pick_stage_prop.clicked.connect(self._on_pick_stage_prim_clicked)

        self.btn_remove_custom_prop = QtWidgets.QPushButton("🗑️ Remove")
        self.btn_remove_custom_prop.setStyleSheet(
            "QPushButton { background-color: #c0392b; color: white; font-weight: bold; padding: 6px; border-radius: 4px; }"
            "QPushButton:hover { background-color: #e74c3c; }"
        )
        self.btn_remove_custom_prop.setToolTip("Remove selected prop from list.")
        self.btn_remove_custom_prop.clicked.connect(self._on_remove_custom_prop_clicked)

        self.btn_clear_custom_props = QtWidgets.QPushButton("🧹 Clear All")
        self.btn_clear_custom_props.setStyleSheet(
            "QPushButton { background-color: #7f8c8d; color: white; font-weight: bold; padding: 6px; border-radius: 4px; }"
            "QPushButton:hover { background-color: #95a5a6; }"
        )
        self.btn_clear_custom_props.setToolTip("Clear all custom props from the list.")
        self.btn_clear_custom_props.clicked.connect(self._on_clear_custom_props_clicked)

        btn_row.addWidget(self.btn_add_custom_prop)
        btn_row.addWidget(self.btn_pick_stage_prop)
        btn_row.addWidget(self.btn_remove_custom_prop)
        btn_row.addWidget(self.btn_clear_custom_props)
        lay.addLayout(btn_row)

        # Splat 3D Mesh Reconstruction Group (Heightfield / OpenVDB)
        reconstruct_grp = QtWidgets.QGroupBox("⚡ 3D Prop Reconstruction from Gaussian Splats")
        reconstruct_grp_lay = QtWidgets.QFormLayout(reconstruct_grp)
        reconstruct_grp_lay.setContentsMargins(6, 6, 6, 6)
        reconstruct_grp_lay.setSpacing(6)

        rec_opts_row = QtWidgets.QHBoxLayout()
        self.combo_reconstruct_method = QtWidgets.QComboBox()
        self.combo_reconstruct_method.addItems([
            "Auto (Detect Best Method)",
            "Top-Down Depth Heightfield (Tables / Counters)",
            "OpenVDB Volumetric Mesh (Trees / Organic / Sculptures)",
        ])
        self.combo_reconstruct_method.setToolTip(
            "• Top-Down Depth Heightfield: Captures horizontal table/counter surfaces and items on them with watertight side walls.\n"
            "• OpenVDB Volumetric Mesh: Fuses splats into smooth 3D volumetric surfaces for trees, plants, lamps, chairs, and fixtures."
        )

        self.sld_reconstruct_voxel = SliderDoubleSpinBox(0.01, 0.10, 0.005, 0.035, decimals=3)
        self.sld_reconstruct_voxel.setToolTip("Sampling resolution / VDB voxel size in meters (0.02 - 0.05m recommended).")

        rec_opts_row.addWidget(self.combo_reconstruct_method, 1)
        rec_opts_row.addWidget(QtWidgets.QLabel("Voxel/Res (m):"))
        rec_opts_row.addWidget(self.sld_reconstruct_voxel, 0)
        reconstruct_grp_lay.addRow("Method & Res:", rec_opts_row)

        rec_actions_row = QtWidgets.QHBoxLayout()
        self.btn_reconstruct_splat_props = QtWidgets.QPushButton("⚡ Reconstruct 3D Props from Splats")
        self.btn_reconstruct_splat_props.setStyleSheet(
            "QPushButton { background-color: #8e44ad; color: white; font-weight: bold; padding: 6px 12px; border-radius: 4px; }"
            "QPushButton:hover { background-color: #9b59b6; }"
        )
        self.btn_reconstruct_splat_props.setToolTip(
            "Analyze interior Gaussian Splats, reconstruct them into clean 3D polygonal geometry (saved to scenes/props/*.bgeo.sc) and automatically add them to the custom props table."
        )
        self.btn_reconstruct_splat_props.clicked.connect(self._on_reconstruct_splat_props_clicked)
        rec_actions_row.addWidget(self.btn_reconstruct_splat_props)
        reconstruct_grp_lay.addRow("", rec_actions_row)

        lay.addWidget(reconstruct_grp)

        # Table Widget
        self.tbl_custom_props = QtWidgets.QTableWidget(0, 9)
        self.tbl_custom_props.setHorizontalHeaderLabels([
            "On", "Prop Name", "File Path", "Texture", "Pos X", "Pos Z", "Rot Y", "Scale", "Floor Snap"
        ])
        self.tbl_custom_props.horizontalHeader().setStretchLastSection(False)
        self.tbl_custom_props.horizontalHeader().setSectionResizeMode(2, QtWidgets.QHeaderView.Stretch)
        self.tbl_custom_props.horizontalHeader().setSectionResizeMode(3, QtWidgets.QHeaderView.ResizeToContents)
        self.tbl_custom_props.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.tbl_custom_props.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.tbl_custom_props.setStyleSheet("""
            QTableWidget {
                background-color: #1e2227;
                color: #abb2bf;
                gridline-color: #2c313a;
                border: 1px solid #3e4451;
                border-radius: 4px;
                selection-background-color: #2c3e50;
                selection-color: #ffffff;
            }
            QHeaderView::section {
                background-color: #282c34;
                color: #d19a66;
                padding: 4px;
                font-weight: bold;
                border: 1px solid #3e4451;
            }
        """)
        self.tbl_custom_props.setMinimumHeight(130)
        self.tbl_custom_props.itemSelectionChanged.connect(self._on_custom_props_table_selection_changed)
        self.tbl_custom_props.itemChanged.connect(self._on_custom_props_table_item_changed)
        lay.addWidget(self.tbl_custom_props)

        # Transform & Settings Group for Selected Prop
        grp_edit = QtWidgets.QGroupBox("Selected Prop Transform & Splat Projection")
        grp_edit_lay = QtWidgets.QFormLayout(grp_edit)
        grp_edit_lay.setContentsMargins(6, 8, 6, 6)
        grp_edit_lay.setSpacing(6)

        pos_row_1 = QtWidgets.QHBoxLayout()
        self.sld_prop_tx = SliderDoubleSpinBox(-50.0, 50.0, 0.05, 0.0, decimals=2)
        self.sld_prop_tz = SliderDoubleSpinBox(-50.0, 50.0, 0.05, 0.0, decimals=2)
        self.sld_prop_tx.valueChanged.connect(self._on_prop_param_changed)
        self.sld_prop_tz.valueChanged.connect(self._on_prop_param_changed)
        pos_row_1.addWidget(QtWidgets.QLabel("X:"))
        pos_row_1.addWidget(self.sld_prop_tx)
        pos_row_1.addWidget(QtWidgets.QLabel("Z:"))
        pos_row_1.addWidget(self.sld_prop_tz)
        grp_edit_lay.addRow("Position (X/Z):", pos_row_1)

        pos_row_2 = QtWidgets.QHBoxLayout()
        self.sld_prop_ty_offset = SliderDoubleSpinBox(-10.0, 10.0, 0.05, 0.0, decimals=2)
        self.sld_prop_ry = SliderDoubleSpinBox(-180.0, 180.0, 1.0, 0.0, decimals=1)
        self.sld_prop_scale = SliderDoubleSpinBox(0.01, 20.0, 0.05, 1.0, decimals=2)
        self.sld_prop_ty_offset.valueChanged.connect(self._on_prop_param_changed)
        self.sld_prop_ry.valueChanged.connect(self._on_prop_param_changed)
        self.sld_prop_scale.valueChanged.connect(self._on_prop_param_changed)
        pos_row_2.addWidget(QtWidgets.QLabel("Height Offset:"))
        pos_row_2.addWidget(self.sld_prop_ty_offset)
        pos_row_2.addWidget(QtWidgets.QLabel("Rot Y (°):"))
        pos_row_2.addWidget(self.sld_prop_ry)
        pos_row_2.addWidget(QtWidgets.QLabel("Scale:"))
        pos_row_2.addWidget(self.sld_prop_scale)
        grp_edit_lay.addRow("Transform:", pos_row_2)

        opt_row = QtWidgets.QHBoxLayout()
        self.chk_prop_snap_floor = QtWidgets.QCheckBox("Snap to Floor Y")
        self.chk_prop_snap_floor.setChecked(True)
        self.chk_prop_snap_floor.setToolTip("Automatically compute object bounds and rest bottom precisely on floor_y.")
        self.chk_prop_snap_floor.toggled.connect(self._on_prop_param_changed)

        self.chk_prop_project_tex = QtWidgets.QCheckBox("Project Splat Texture / Albedo")
        self.chk_prop_project_tex.setChecked(True)
        self.chk_prop_project_tex.setToolTip("Camera-project the high-resolution baked HDRI/albedo from probe position onto this prop.")
        self.chk_prop_project_tex.toggled.connect(self._on_prop_param_changed)

        self.chk_prop_cast_shadows = QtWidgets.QCheckBox("Cast Karma Shadows")
        self.chk_prop_cast_shadows.setChecked(True)
        self.chk_prop_cast_shadows.setToolTip("Enable contact shadows on the floor and other surfaces.")
        self.chk_prop_cast_shadows.toggled.connect(self._on_prop_param_changed)

        opt_row.addWidget(self.chk_prop_snap_floor)
        opt_row.addWidget(self.chk_prop_project_tex)
        opt_row.addWidget(self.chk_prop_cast_shadows)
        grp_edit_lay.addRow("Behavior:", opt_row)

        # Texture Assignment Controls
        tex_assign_row = QtWidgets.QHBoxLayout()
        self.txt_prop_texture = QtWidgets.QLineEdit()
        self.txt_prop_texture.setPlaceholderText("Projected from splat probe (room_mat) or choose custom texture...")
        self.txt_prop_texture.setToolTip("Path to the texture file (.exr, .hdr, .png, .jpg, .tif) bound to this prop in Solaris.")
        self.txt_prop_texture.textChanged.connect(self._on_prop_param_changed)

        self.btn_browse_prop_texture = QtWidgets.QPushButton("📂 Browse...")
        self.btn_browse_prop_texture.setStyleSheet(
            "QPushButton { background-color: #2980b9; color: white; font-weight: bold; padding: 4px 8px; border-radius: 4px; }"
            "QPushButton:hover { background-color: #3498db; }"
        )
        self.btn_browse_prop_texture.setToolTip("Browse and assign an existing texture map to this prop.")
        self.btn_browse_prop_texture.clicked.connect(self._on_browse_prop_texture_clicked)

        self.btn_clear_prop_texture = QtWidgets.QPushButton("❌")
        self.btn_clear_prop_texture.setStyleSheet(
            "QPushButton { background-color: #7f8c8d; color: white; padding: 4px 6px; border-radius: 4px; }"
            "QPushButton:hover { background-color: #95a5a6; }"
        )
        self.btn_clear_prop_texture.setToolTip("Clear assigned texture map and revert to room projection or clay.")
        self.btn_clear_prop_texture.clicked.connect(self._on_clear_prop_texture_clicked)

        tex_assign_row.addWidget(self.txt_prop_texture)
        tex_assign_row.addWidget(self.btn_browse_prop_texture)
        tex_assign_row.addWidget(self.btn_clear_prop_texture)
        grp_edit_lay.addRow("Texture Map:", tex_assign_row)

        # UV Mode
        self.combo_prop_uv_mode = QtWidgets.QComboBox()
        self.combo_prop_uv_mode.addItems([
            "Spherical Splat Projection (Probe Origin)",
            "Mesh Native UVs (Unwrapped)"
        ])
        self.combo_prop_uv_mode.setToolTip(
            "• Spherical Splat Projection: Generates camera-projected spherical UVs from the Gaussian Splat probe.\n"
            "• Mesh Native UVs: Preserves the 3D model's original unwrapped UVs for standard texture maps."
        )
        self.combo_prop_uv_mode.currentIndexChanged.connect(self._on_prop_param_changed)
        grp_edit_lay.addRow("UV Mapping:", self.combo_prop_uv_mode)

        # Shader Target (Karma MaterialX, Arnold, Redshift, All, USD Preview)
        self.combo_prop_shader_target = QtWidgets.QComboBox()
        self.combo_prop_shader_target.addItems([
            "Match Room / Global Target (Auto)",
            "Karma (MaterialX Standard Surface)",
            "Arnold (Standard Surface)",
            "Redshift (StandardMaterial)",
            "All Renderers",
            "USD Preview Only",
        ])
        self.combo_prop_shader_target.setToolTip(
            "Target renderer shader to build for this prop in Solaris /stage:\n"
            "• Match Room / Global Target (Auto): Inherits the renderer target chosen in Room Architecture / Dome.\n"
            "• Karma (MaterialX): Native mtlxstandard_surface for Karma CPU / GPU viewport and production renders.\n"
            "• Arnold: Native arnold:standard_surface with arnold:image.\n"
            "• Redshift: Native redshift::StandardMaterial.\n"
            "• All Renderers: Authors all shaders and surface outputs simultaneously."
        )
        self.combo_prop_shader_target.currentIndexChanged.connect(self._on_prop_param_changed)
        grp_edit_lay.addRow("Shader Target:", self.combo_prop_shader_target)

        # Splat Texture Baking Controls
        bake_cfg_row = QtWidgets.QHBoxLayout()
        self.combo_prop_bake_source = QtWidgets.QComboBox()
        self.combo_prop_bake_source.addItems([
            "Gaussian Splat (Room Probe View)",
            "Gaussian Splat (Prop Center View)",
            "Active 360° HDRI (Albedo Compression)"
        ])
        self.combo_prop_bake_source.setToolTip("Source viewpoint and data to bake the prop's texture map from.")

        self.combo_prop_bake_res = QtWidgets.QComboBox()
        self.combo_prop_bake_res.addItems([
            "1024 x 512 (Fast)",
            "2048 x 1024 (Production)",
            "4096 x 2048 (High-Res)"
        ])
        self.combo_prop_bake_res.setCurrentIndex(1)
        self.combo_prop_bake_res.setToolTip("Texture resolution for baked OpenEXR map.")

        bake_cfg_row.addWidget(self.combo_prop_bake_source)
        bake_cfg_row.addWidget(self.combo_prop_bake_res)
        grp_edit_lay.addRow("Bake Source:", bake_cfg_row)

        # Dedicated Bake Button
        self.btn_bake_prop_texture = QtWidgets.QPushButton("🔥 Bake Prop Texture from Splat / HDRI")
        self.btn_bake_prop_texture.setStyleSheet(
            "QPushButton { background-color: #d35400; color: white; font-weight: bold; padding: 7px; border-radius: 4px; }"
            "QPushButton:hover { background-color: #e67e22; }"
        )
        self.btn_bake_prop_texture.setToolTip(
            "Bake an equirectangular texture directly from the 3D Gaussian Splat scene or HDRI and automatically bind it to this prop in Solaris /stage."
        )
        self.btn_bake_prop_texture.clicked.connect(self._on_bake_prop_texture_clicked)
        grp_edit_lay.addRow("", self.btn_bake_prop_texture)

        # Prop Bake Progress Bar
        self.pbar_prop_bake = QtWidgets.QProgressBar()
        self.pbar_prop_bake.setRange(0, 100)
        self.pbar_prop_bake.setValue(0)
        self.pbar_prop_bake.setTextVisible(True)
        self.pbar_prop_bake.setFormat("Baking Prop Texture: %p%")
        self.pbar_prop_bake.setFixedHeight(18)
        self.pbar_prop_bake.setStyleSheet("""
            QProgressBar {
                border: 1px solid #2c3e50;
                border-radius: 4px;
                background-color: #121418;
                text-align: center;
                color: #ffffff;
                font-size: 11px;
                font-weight: bold;
            }
            QProgressBar::chunk {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #d35400, stop:1 #e67e22);
                border-radius: 3px;
            }
        """)
        self.pbar_prop_bake.setVisible(False)
        grp_edit_lay.addRow("", self.pbar_prop_bake)

        lay.addWidget(grp_edit)

        # Stage Execution Actions
        actions_box = QtWidgets.QHBoxLayout()
        self.btn_update_stage_props = QtWidgets.QPushButton("📦 Update Props in Stage")
        self.btn_update_stage_props.setStyleSheet(
            "QPushButton { background-color: #8e44ad; color: white; font-weight: bold; padding: 7px; border-radius: 4px; }"
            "QPushButton:hover { background-color: #9b59b6; }"
        )
        self.btn_update_stage_props.setToolTip("Update Solaris /stage custom prop nodes, snap to floor, and project splat texture.")
        self.btn_update_stage_props.clicked.connect(self._update_custom_props_stage_clicked)

        self.btn_clear_stage_props = QtWidgets.QPushButton("🗑️ Clear from Stage")
        self.btn_clear_stage_props.setStyleSheet(
            "QPushButton { background-color: #7f8c8d; color: white; font-weight: bold; padding: 7px; border-radius: 4px; }"
            "QPushButton:hover { background-color: #95a5a6; }"
        )
        self.btn_clear_stage_props.setToolTip("Remove custom props from Solaris /stage.")
        self.btn_clear_stage_props.clicked.connect(self._clear_custom_props_stage_clicked)

        actions_box.addWidget(self.btn_update_stage_props)
        actions_box.addWidget(self.btn_clear_stage_props)
        lay.addLayout(actions_box)

        # Status Label
        self.lbl_props_status = QtWidgets.QLabel("0 custom props loaded.")
        self.lbl_props_status.setStyleSheet("color: #888888; font-size: 11px;")
        lay.addWidget(self.lbl_props_status)

    def _populate_splat_points_tab(self, lay):
        """Populate Tab 5: Convert Gaussian Splats to USD Points and Texture Geometry."""
        info_box = QtWidgets.QLabel(
            "<b>☁️ USD Points & Geometry Texturing</b><br>"
            "Convert 3D Gaussian Splats into renderable USD Points (<code>UsdGeom.Points</code>) with custom point size and density controls. "
            "Render them directly in Karma / Arnold / Redshift as a geometry base model, or texture 3D meshes in <code>/stage</code> from splat data."
        )
        info_box.setWordWrap(True)
        info_box.setStyleSheet(
            "background-color: #1a1e24; color: #abb2bf; padding: 8px; border-left: 3px solid #3498db; border-radius: 4px; font-size: 11px;"
        )
        lay.addWidget(info_box)

        # 1. Splat Source Group
        grp_src = QtWidgets.QGroupBox("📁 Gaussian Splat Source")
        grp_src_lay = QtWidgets.QFormLayout(grp_src)
        grp_src_lay.setContentsMargins(6, 8, 6, 6)
        grp_src_lay.setSpacing(6)

        src_row = QtWidgets.QHBoxLayout()
        self.txt_points_splat_file = QtWidgets.QLineEdit()
        self.txt_points_splat_file.setPlaceholderText("Path to .ply Gaussian Splat file...")
        self.btn_browse_points_splat = QtWidgets.QPushButton("📂 Browse...")
        self.btn_browse_points_splat.clicked.connect(self._on_points_splat_browse_clicked)
        self.btn_use_active_splat = QtWidgets.QPushButton("🔄 Use Active Splat")
        self.btn_use_active_splat.setToolTip("Sync and use the splat currently loaded in the HDRI Dome tab.")
        self.btn_use_active_splat.clicked.connect(self._on_use_active_splat_clicked)
        src_row.addWidget(self.txt_points_splat_file)
        src_row.addWidget(self.btn_browse_points_splat)
        src_row.addWidget(self.btn_use_active_splat)
        grp_src_lay.addRow("Splat File:", src_row)

        self.lbl_points_splat_info = QtWidgets.QLabel("No splat loaded.")
        self.lbl_points_splat_info.setStyleSheet("color: #9da5b4; font-size: 11px;")
        grp_src_lay.addRow("Splat Info:", self.lbl_points_splat_info)

        self.chk_points_flip_y = QtWidgets.QCheckBox("Flip Y/Z (Standard Camera to Houdini Y-up)")
        self.chk_points_flip_y.setChecked(True)
        grp_src_lay.addRow("Coordinate Space:", self.chk_points_flip_y)

        lay.addWidget(grp_src)

        # 2. USD Points Settings Group
        grp_pts = QtWidgets.QGroupBox("☁️ USD Points Generator (Base Model)")
        grp_pts_lay = QtWidgets.QFormLayout(grp_pts)
        grp_pts_lay.setContentsMargins(6, 8, 6, 6)
        grp_pts_lay.setSpacing(6)

        # Houdini Metric Dimensions & Scale Calibration
        scale_box_row = QtWidgets.QHBoxLayout()
        self.sld_points_scale = SliderDoubleSpinBox(0.0001, 100.0, 0.005, 1.0, decimals=4)
        self.sld_points_scale.setToolTip("Scale factor applied to splat point cloud to calibrate into Houdini meters (1.0 = 1 unit is 1 meter).")
        self.sld_points_scale.valueChanged.connect(self._on_points_scale_changed)

        self.combo_points_scale_preset = QtWidgets.QComboBox()
        self.combo_points_scale_preset.addItems([
            "Meters (1.0000) - Standard Houdini Units",
            "Centimeters -> Meters (0.0100)",
            "Millimeters -> Meters (0.0010)",
            "Inches -> Meters (0.0254)",
            "Feet -> Meters (0.3048)",
            "Sync from Tab 3 Room Architecture",
        ])
        self.combo_points_scale_preset.setToolTip("Quick preset unit conversions to Houdini meters.")
        self.combo_points_scale_preset.currentIndexChanged.connect(self._on_points_scale_preset_changed)

        self.btn_points_autoscale = QtWidgets.QPushButton("📐 Auto-Calibrate (2.9m Ceiling)")
        self.btn_points_autoscale.setStyleSheet(
            "QPushButton { background-color: #2980b9; color: white; font-weight: bold; padding: 4px 8px; border-radius: 4px; }"
            "QPushButton:hover { background-color: #3498db; }"
        )
        self.btn_points_autoscale.setToolTip("Analyze raw splat height and automatically calculate the exact multiplier so ceiling/room matches 2.9m.")
        self.btn_points_autoscale.clicked.connect(self._auto_scale_points_clicked)

        scale_box_row.addWidget(self.sld_points_scale)
        scale_box_row.addWidget(self.combo_points_scale_preset)
        scale_box_row.addWidget(self.btn_points_autoscale)
        grp_pts_lay.addRow("Metric Scale (1m):", scale_box_row)

        # Placement / Centering Controls
        place_row = QtWidgets.QHBoxLayout()
        self.chk_points_center_origin = QtWidgets.QCheckBox("Center XZ to Origin (0, 0)")
        self.chk_points_center_origin.setChecked(False)
        self.chk_points_center_origin.setToolTip("Center points horizontally around Houdini world origin (0, 0).")
        self.chk_points_center_origin.toggled.connect(self._on_points_placement_changed)

        self.chk_points_snap_floor = QtWidgets.QCheckBox("Snap Floor to Y=0 (Houdini Grid)")
        self.chk_points_snap_floor.setChecked(False)
        self.chk_points_snap_floor.setToolTip("Align lowest dense floor splats directly on Houdini's ground plane (Y = 0).")
        self.chk_points_snap_floor.toggled.connect(self._on_points_placement_changed)

        place_row.addWidget(self.chk_points_center_origin)
        place_row.addWidget(self.chk_points_snap_floor)
        grp_pts_lay.addRow("Placement:", place_row)

        # Live Dimensions Readout Label
        self.lbl_points_dimensions = QtWidgets.QLabel("Houdini Bounding Box: -")
        self.lbl_points_dimensions.setStyleSheet(
            "color: #2ecc71; font-size: 11px; font-weight: bold; background: #161b22; padding: 4px 6px; border-radius: 4px; border: 1px solid #238636;"
        )
        self.lbl_points_dimensions.setToolTip("Current physical bounding box dimensions in Houdini meters (1 unit = 1.0 meter).")
        grp_pts_lay.addRow("Houdini Size:", self.lbl_points_dimensions)

        # Point Size Controls
        size_row = QtWidgets.QHBoxLayout()
        self.sld_points_size = SliderDoubleSpinBox(0.001, 0.50, 0.005, 0.03, decimals=3)
        self.sld_points_size.setToolTip("Base point width / diameter in meters.")
        size_row.addWidget(self.sld_points_size)
        grp_pts_lay.addRow("Point Size (m):", size_row)

        self.combo_points_size_mode = QtWidgets.QComboBox()
        self.combo_points_size_mode.addItems([
            "Uniform Point Size (Exact Width)",
            "Splat Scale Proportional (3D Covariance)",
            "Opacity-Weighted (Fainter = Smaller)",
        ])
        self.combo_points_size_mode.setToolTip(
            "• Uniform Point Size: Sets constant width for all points.\n"
            "• Splat Scale Proportional: Uses the 3D Gaussian ellipsoid covariance dimensions to scale each point individually.\n"
            "• Opacity-Weighted: Scales down faint splats to avoid haze."
        )
        grp_pts_lay.addRow("Size Mode:", self.combo_points_size_mode)

        # Density Subsampling Controls
        self.sld_points_density = SliderDoubleSpinBox(1.0, 100.0, 1.0, 100.0, decimals=1)
        self.sld_points_density.setToolTip("Subsampling density percentage of splats to include (1% to 100%).")
        grp_pts_lay.addRow("Density (%):", self.sld_points_density)

        self.combo_points_max = QtWidgets.QComboBox()
        self.combo_points_max.addItems([
            "50,000 (Very Fast / Viewport Proxy)",
            "100,000 (Fast)",
            "250,000 (Standard)",
            "500,000 (High-Detail)",
            "1,000,000 (Ultra)",
            "2,000,000 (Film-Res)",
            "Unlimited / All Points",
        ])
        self.combo_points_max.setCurrentIndex(3)  # Default 500,000
        self.combo_points_max.setToolTip("Upper limit cap on total generated USD points.")
        grp_pts_lay.addRow("Max Points:", self.combo_points_max)

        self.sld_points_min_opacity = SliderDoubleSpinBox(0.00, 0.50, 0.01, 0.05, decimals=2)
        self.sld_points_min_opacity.setToolTip("Filter out low-opacity / ghost splats with alpha below this threshold.")
        grp_pts_lay.addRow("Min Opacity:", self.sld_points_min_opacity)

        # Render Appearance Controls
        self.combo_points_render_shape = QtWidgets.QComboBox()
        self.combo_points_render_shape.addItems([
            "Spheres (Karma / Arnold / Redshift)",
            "Discs",
            "Points / Particles",
        ])
        self.combo_points_render_shape.setToolTip("Point render primitive type for Hydra render delegates.")
        grp_pts_lay.addRow("Render Shape:", self.combo_points_render_shape)

        shading_row = QtWidgets.QHBoxLayout()
        self.chk_points_pbr_material = QtWidgets.QCheckBox("Bind PBR Shaded Material")
        self.chk_points_pbr_material.setChecked(True)
        self.chk_points_pbr_material.setToolTip("Create and bind UsdPreviewSurface material mapping displayColor to diffuseColor.")
        shading_row.addWidget(self.chk_points_pbr_material)
        shading_row.addWidget(QtWidgets.QLabel("Roughness:"))
        self.sld_points_roughness = SliderDoubleSpinBox(0.0, 1.0, 0.05, 0.50, decimals=2)
        shading_row.addWidget(self.sld_points_roughness)
        grp_pts_lay.addRow("Shading:", shading_row)

        # Action Buttons
        act_row_1 = QtWidgets.QHBoxLayout()
        self.btn_convert_load_points = QtWidgets.QPushButton("⚡ Convert & Load USD Points in /stage")
        self.btn_convert_load_points.setStyleSheet(
            "QPushButton { background-color: #27ae60; color: white; font-weight: bold; padding: 7px; border-radius: 4px; }"
            "QPushButton:hover { background-color: #2ecc71; }"
        )
        self.btn_convert_load_points.setToolTip("Convert Gaussian Splat into USD Points and load into Solaris /stage as 'splat_points_ref'.")
        self.btn_convert_load_points.clicked.connect(self._convert_and_load_splat_points)

        self.btn_export_standalone_points = QtWidgets.QPushButton("💾 Export Standalone .usd")
        self.btn_export_standalone_points.setStyleSheet(
            "QPushButton { background-color: #2980b9; color: white; font-weight: bold; padding: 7px; border-radius: 4px; }"
            "QPushButton:hover { background-color: #3498db; }"
        )
        self.btn_export_standalone_points.setToolTip("Save the generated USD point cloud file to disk as a geometry base model.")
        self.btn_export_standalone_points.clicked.connect(self._export_standalone_splat_points)

        act_row_1.addWidget(self.btn_convert_load_points)
        act_row_1.addWidget(self.btn_export_standalone_points)
        grp_pts_lay.addRow(act_row_1)

        act_row_2 = QtWidgets.QHBoxLayout()
        self.btn_toggle_points_vis = QtWidgets.QPushButton("👁️ Toggle Points Visibility")
        self.btn_toggle_points_vis.setStyleSheet(
            "QPushButton { background-color: #34495e; color: white; font-weight: bold; padding: 6px; border-radius: 4px; }"
            "QPushButton:hover { background-color: #4a6278; }"
        )
        self.btn_toggle_points_vis.clicked.connect(self._toggle_splat_points_vis)

        self.btn_clear_points_stage = QtWidgets.QPushButton("🗑️ Clear Points from Stage")
        self.btn_clear_points_stage.setStyleSheet(
            "QPushButton { background-color: #7f8c8d; color: white; font-weight: bold; padding: 6px; border-radius: 4px; }"
            "QPushButton:hover { background-color: #95a5a6; }"
        )
        self.btn_clear_points_stage.clicked.connect(self._clear_splat_points)

        act_row_2.addWidget(self.btn_toggle_points_vis)
        act_row_2.addWidget(self.btn_clear_points_stage)
        grp_pts_lay.addRow(act_row_2)

        # Progress bar
        self.pbar_points_convert = QtWidgets.QProgressBar()
        self.pbar_points_convert.setRange(0, 100)
        self.pbar_points_convert.setValue(0)
        self.pbar_points_convert.setTextVisible(True)
        self.pbar_points_convert.setFixedHeight(18)
        self.pbar_points_convert.setStyleSheet("""
            QProgressBar {
                border: 1px solid #2c3e50;
                border-radius: 4px;
                background-color: #121418;
                text-align: center;
                color: #ffffff;
                font-size: 11px;
                font-weight: bold;
            }
            QProgressBar::chunk {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #27ae60, stop:1 #2ecc71);
                border-radius: 3px;
            }
        """)
        self.pbar_points_convert.setVisible(False)
        grp_pts_lay.addRow("", self.pbar_points_convert)

        lay.addWidget(grp_pts)

        # 3. Geometry Texturing from Splats Group
        grp_tex = QtWidgets.QGroupBox("🎨 Texture Geometry from Gaussian Splats")
        grp_tex_lay = QtWidgets.QFormLayout(grp_tex)
        grp_tex_lay.setContentsMargins(6, 8, 6, 6)
        grp_tex_lay.setSpacing(6)

        mesh_row = QtWidgets.QHBoxLayout()
        self.combo_texture_target_mesh = QtWidgets.QComboBox()
        self.combo_texture_target_mesh.setEditable(True)
        self.combo_texture_target_mesh.addItem("/stage/room/walls")
        self.combo_texture_target_mesh.addItem("/stage/room/floor")
        self.combo_texture_target_mesh.addItem("/stage/room/ceiling")
        self.combo_texture_target_mesh.setToolTip("Target USD Mesh primitive path in /stage to receive splat textures/colors.")

        self.btn_refresh_stage_meshes = QtWidgets.QPushButton("🔄 Refresh Meshes")
        self.btn_refresh_stage_meshes.setToolTip("Scan active Solaris stage for all UsdGeom.Mesh primitives.")
        self.btn_refresh_stage_meshes.clicked.connect(self._refresh_stage_meshes)

        mesh_row.addWidget(self.combo_texture_target_mesh)
        mesh_row.addWidget(self.btn_refresh_stage_meshes)
        grp_tex_lay.addRow("Target Mesh:", mesh_row)

        self.combo_texturing_mode = QtWidgets.QComboBox()
        self.combo_texturing_mode.addItems([
            "3D Splat Vertex Colors (displayColor) - No UVs Needed",
            "Spherical Camera Projection (st UVs) from Splat Probe",
        ])
        self.combo_texturing_mode.setToolTip(
            "• 3D Splat Vertex Colors: Directly transfers real 3D splat RGB colors to each mesh vertex.\n"
            "• Spherical Camera Projection: Generates projected 'st' UV coordinates and binds the baked splat HDRI."
        )
        grp_tex_lay.addRow("Texturing Mode:", self.combo_texturing_mode)

        self.sld_texture_radius = SliderDoubleSpinBox(0.01, 1.0, 0.02, 0.15, decimals=2)
        self.sld_texture_radius.setToolTip("Sampling search radius in meters when transferring splat colors to vertices.")
        grp_tex_lay.addRow("Search Radius (m):", self.sld_texture_radius)

        self.btn_apply_mesh_texturing = QtWidgets.QPushButton("🎨 Texture Selected Mesh from Splats")
        self.btn_apply_mesh_texturing.setStyleSheet(
            "QPushButton { background-color: #8e44ad; color: white; font-weight: bold; padding: 7px; border-radius: 4px; }"
            "QPushButton:hover { background-color: #9b59b6; }"
        )
        self.btn_apply_mesh_texturing.setToolTip("Apply 3D splat vertex colors or projected texture onto the target mesh in /stage.")
        self.btn_apply_mesh_texturing.clicked.connect(self._texture_mesh_from_splats)
        grp_tex_lay.addRow("", self.btn_apply_mesh_texturing)

        self.lbl_texturing_status = QtWidgets.QLabel("Ready to texture geometry from splat data.")
        self.lbl_texturing_status.setStyleSheet("color: #888888; font-size: 11px;")
        grp_tex_lay.addRow("", self.lbl_texturing_status)

        lay.addWidget(grp_tex)

    def log_info(self, msg):
        """Standardized info logger."""
        self.log(msg, "INFO")

    def log_warning(self, msg):
        """Standardized warning logger."""
        self.log(msg, "WARNING")

    def log_success(self, msg):
        """Standardized success logger."""
        self.log(msg, "SUCCESS")

    def _on_points_splat_browse_clicked(self):
        """Browse for .ply Gaussian Splat file for points tab."""
        start_dir = os.path.dirname(self.txt_points_splat_file.text().strip()) if self.txt_points_splat_file.text().strip() else ""
        if not start_dir or not os.path.isdir(start_dir):
            start_dir = os.path.dirname(self.txt_splat_file.text().strip()) if hasattr(self, 'txt_splat_file') and self.txt_splat_file.text().strip() else ""
        if not start_dir or not os.path.isdir(start_dir):
            start_dir = os.path.expanduser("~")

        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Select Gaussian Splatting File (.ply)", start_dir, "Gaussian Splat Files (*.ply);;All Files (*)"
        )
        if path:
            self.txt_points_splat_file.setText(path.replace("\\", "/"))
            self._update_points_splat_info(path)

    def _on_use_active_splat_clicked(self):
        """Sync from Tab 1 splat file."""
        if hasattr(self, 'txt_splat_file'):
            ply_path = self.txt_splat_file.text().strip()
            if ply_path and os.path.isfile(ply_path):
                self.txt_points_splat_file.setText(ply_path.replace("\\", "/"))
                self._update_points_splat_info(ply_path)
                return
        self.log_warning("No active splat loaded in Tab 1.")

    def _update_points_splat_info(self, ply_path):
        """Update splat info label and Houdini dimensions in points tab."""
        if not ply_path or not os.path.isfile(ply_path):
            self.lbl_points_splat_info.setText("Invalid splat file.")
            self._update_points_dimensions()
            return
        try:
            sz_mb = os.path.getsize(ply_path) / (1024 * 1024)
            if hasattr(self, '_splat_scene') and self._splat_scene and getattr(self._splat_scene, 'filepath', None) == ply_path:
                c = self._splat_scene.count
                self.lbl_points_splat_info.setText(f"Splats: {c:,} | File Size: {sz_mb:.1f} MB")
            else:
                self.lbl_points_splat_info.setText(f"File Size: {sz_mb:.1f} MB | Ready to load")
        except Exception as e:
            self.lbl_points_splat_info.setText(f"File Size: {sz_mb:.1f} MB")
        self._update_points_dimensions()

    def _on_points_scale_changed(self, val):
        self._update_points_dimensions()

    def _on_points_scale_preset_changed(self, idx):
        preset_scales = {
            0: 1.0,      # Meters
            1: 0.01,     # cm to m
            2: 0.001,    # mm to m
            3: 0.0254,   # in to m
            4: 0.3048,   # ft to m
        }
        if idx in preset_scales:
            self.sld_points_scale.setValue(preset_scales[idx])
        elif idx == 5:
            # Sync from Tab 3 Room Architecture
            if hasattr(self, 'sld_arch_scale'):
                arch_s = float(self.sld_arch_scale.value())
                self.sld_points_scale.setValue(arch_s)
                self.log_info(f"Synchronized scale from Tab 3 Room Architecture: {arch_s:.4f}")
        self._update_points_dimensions()

    def _on_points_placement_changed(self):
        self._update_points_dimensions()

    def _auto_scale_points_clicked(self):
        """Analyze raw splat dimensions and automatically set scale to realistic 2.9m room height."""
        ply_path = self.txt_points_splat_file.text().strip() if hasattr(self, 'txt_points_splat_file') else ""
        if not ply_path or not os.path.isfile(ply_path):
            self.log_warning("Please load a valid 3D Gaussian Splat (.ply) file first.")
            return

        scene = self._ensure_points_splat_scene(ply_path)
        if not scene:
            return

        from hdri_match_solaris import lop_splat
        flip_y = self.chk_points_flip_y.isChecked() if hasattr(self, 'chk_points_flip_y') else True
        dim_info = lop_splat.compute_splat_houdini_dimensions(scene, scene_scale=1.0, flip_y=flip_y)
        if not dim_info:
            return

        sugg = dim_info.get("suggested_scale", 1.0)
        raw_h = dim_info.get("raw_height", 1.0)
        self.sld_points_scale.setValue(sugg)
        self.combo_points_scale_preset.setCurrentIndex(0)
        self._update_points_dimensions()
        msg = f"Auto-scale set to {sugg:.4f} (Raw height {raw_h:.2f}m -> 2.90m realistic ceiling)."
        self.log_info(msg)
        if hou.isUIAvailable():
            hou.ui.displayMessage(msg, title="Scale Calibrated to Houdini Meters")

    def _update_points_dimensions(self):
        """Update live dimensions readout in Houdini meters."""
        if not hasattr(self, 'lbl_points_dimensions'):
            return
        ply_path = self.txt_points_splat_file.text().strip() if hasattr(self, 'txt_points_splat_file') else ""
        if not ply_path or not os.path.isfile(ply_path):
            self.lbl_points_dimensions.setText("Houdini Bounding Box: Select a splat file to view dimensions.")
            return

        if not hasattr(self, '_splat_scene') or not self._splat_scene or getattr(self._splat_scene, 'filepath', None) != ply_path:
            self.lbl_points_dimensions.setText("Houdini Bounding Box: Ready to calculate upon load/conversion.")
            return

        from hdri_match_solaris import lop_splat
        scale_val = float(self.sld_points_scale.value()) if hasattr(self, 'sld_points_scale') else 1.0
        flip_y = self.chk_points_flip_y.isChecked() if hasattr(self, 'chk_points_flip_y') else True
        center_orig = self.chk_points_center_origin.isChecked() if hasattr(self, 'chk_points_center_origin') else False
        snap_floor = self.chk_points_snap_floor.isChecked() if hasattr(self, 'chk_points_snap_floor') else False

        dim = lop_splat.compute_splat_houdini_dimensions(
            self._splat_scene,
            scene_scale=scale_val,
            flip_y=flip_y,
            center_to_origin=center_orig,
            snap_floor_y0=snap_floor,
        )
        if dim:
            w, h, d = dim["width"], dim["height"], dim["depth"]
            cx, cy, cz = dim["centroid"]
            warning_tag = ""
            if dim["raw_height"] > 6.0 and abs(scale_val - 1.0) < 1e-4:
                warning_tag = " ⚠️ (Unscaled photogrammetry units! Click Auto-Calibrate)"
            txt = f"W: {w:.2f}m × H: {h:.2f}m × D: {d:.2f}m | Centroid: ({cx:.2f}, {cy:.2f}, {cz:.2f})m (1 unit = 1m){warning_tag}"
            self.lbl_points_dimensions.setText(txt)
            if warning_tag:
                self.lbl_points_dimensions.setStyleSheet("color: #e67e22; font-size: 11px; font-weight: bold; background: #161b22; padding: 4px 6px; border-radius: 4px; border: 1px solid #d35400;")
            else:
                self.lbl_points_dimensions.setStyleSheet("color: #2ecc71; font-size: 11px; font-weight: bold; background: #161b22; padding: 4px 6px; border-radius: 4px; border: 1px solid #238636;")

    def _get_points_export_params(self):
        """Extract points generation parameters from UI widgets."""
        size_mode_map = {
            0: "uniform",
            1: "scale_proportional",
            2: "opacity_weighted",
        }
        size_mode = size_mode_map.get(self.combo_points_size_mode.currentIndex(), "uniform")

        max_pts_map = {
            0: 50000,
            1: 100000,
            2: 250000,
            3: 500000,
            4: 1000000,
            5: 2000000,
            6: None,
        }
        max_points = max_pts_map.get(self.combo_points_max.currentIndex(), 500000)

        shape_map = {
            0: "sphere",
            1: "disc",
            2: "point",
        }
        render_shape = shape_map.get(self.combo_points_render_shape.currentIndex(), "sphere")

        return {
            "point_size": float(self.sld_points_size.value()),
            "size_mode": size_mode,
            "density_pct": float(self.sld_points_density.value()),
            "max_points": max_points,
            "min_opacity": float(self.sld_points_min_opacity.value()),
            "render_mode": render_shape,
            "create_material": self.chk_points_pbr_material.isChecked(),
            "roughness": float(self.sld_points_roughness.value()),
            "flip_y": self.chk_points_flip_y.isChecked(),
            "scene_scale": float(self.sld_points_scale.value()) if hasattr(self, 'sld_points_scale') else 1.0,
            "center_to_origin": self.chk_points_center_origin.isChecked() if hasattr(self, 'chk_points_center_origin') else False,
            "snap_floor_y0": self.chk_points_snap_floor.isChecked() if hasattr(self, 'chk_points_snap_floor') else False,
        }

    def _ensure_points_splat_scene(self, ply_path=None):
        """Ensure GaussianSplatScene is loaded for points tab."""
        if not ply_path:
            ply_path = self.txt_points_splat_file.text().strip()
        if not ply_path or not os.path.isfile(ply_path):
            if hasattr(self, 'txt_splat_file') and os.path.isfile(self.txt_splat_file.text().strip()):
                ply_path = self.txt_splat_file.text().strip()
                self.txt_points_splat_file.setText(ply_path)

        if not ply_path or not os.path.isfile(ply_path):
            self.log_error("Please select a valid Gaussian Splat (.ply) file first.")
            if hou.isUIAvailable():
                hou.ui.displayMessage("Please select a valid Gaussian Splat (.ply) file first.", severity=hou.severityType.Warning)
            return None

        if hasattr(self, '_splat_scene') and self._splat_scene and getattr(self._splat_scene, 'filepath', None) == ply_path:
            return self._splat_scene

        from hdri_match_solaris.gaussian_splat import GaussianSplatScene
        self.log_info(f"Loading Gaussian Splatting scene: {os.path.basename(ply_path)}...")
        self._splat_scene = GaussianSplatScene.from_ply(ply_path)
        self._update_points_splat_info(ply_path)
        return self._splat_scene

    def _convert_and_load_splat_points(self):
        """Convert Gaussian Splats to USD Points and load directly into Solaris /stage."""
        scene = self._ensure_points_splat_scene()
        if not scene:
            return

        stage_node = self._get_stage_node()
        if not stage_node:
            self.log_error("Solaris /stage network node not found.")
            return

        params = self._get_points_export_params()

        # Destination USD file path
        ply_path = self.txt_points_splat_file.text().strip()
        base_name = os.path.splitext(os.path.basename(ply_path))[0]
        safe_base = "".join(c if c.isalnum() or c in ("_", "-") else "_" for c in base_name)

        out_dir = os.path.join(os.path.dirname(hou.hipFile.path()), "hdri_match", "splats_usd").replace("\\", "/")
        if not os.path.isdir(os.path.dirname(out_dir)):
            out_dir = os.path.expanduser("~/hdri_match_splats_usd").replace("\\", "/")
        os.makedirs(out_dir, exist_ok=True)
        usd_path = os.path.join(out_dir, f"{safe_base}_points.usd").replace("\\", "/")

        self.btn_convert_load_points.setEnabled(False)
        self.btn_convert_load_points.setText("⚡ Converting to USD Points...")
        QtWidgets.QApplication.processEvents()

        try:
            from hdri_match_solaris import lop_splat
            lop_splat.export_splat_points_usd(
                scene=scene,
                output_usd_path=usd_path,
                point_size=params["point_size"],
                size_mode=params["size_mode"],
                density_pct=params["density_pct"],
                max_points=params["max_points"],
                min_opacity=params["min_opacity"],
                render_mode=params["render_mode"],
                create_material=params["create_material"],
                roughness=params["roughness"],
                flip_y=params["flip_y"],
                scene_scale=params["scene_scale"],
                center_to_origin=params["center_to_origin"],
                snap_floor_y0=params["snap_floor_y0"],
            )

            ref_node = lop_splat.load_splat_points_in_stage(stage_node, usd_path)
            self.log_success(f"USD Points loaded into /stage: {usd_path}")

            if hou.isUIAvailable():
                hou.ui.displayMessage(
                    f"Successfully generated and loaded USD Points!\n\n"
                    f"Points File: {usd_path}\n"
                    f"Stage Node: /stage/splat_points_ref\n"
                    f"Scale: {params['scene_scale']:.4f}x (Houdini Meters)\n"
                    f"Mode: {params['size_mode']} | Shape: {params['render_mode']}",
                    title="USD Points Generated"
                )
        except Exception as e:
            self.log_error(f"Failed to generate USD points: {e}")
            if hou.isUIAvailable():
                hou.ui.displayMessage(f"Failed to generate USD points: {e}", severity=hou.severityType.Error)
        finally:
            self.btn_convert_load_points.setEnabled(True)
            self.btn_convert_load_points.setText("⚡ Convert & Load USD Points in /stage")

    def _export_standalone_splat_points(self):
        """Export standalone USD Points file to a custom path on disk."""
        scene = self._ensure_points_splat_scene()
        if not scene:
            return

        ply_path = self.txt_points_splat_file.text().strip()
        base_name = os.path.splitext(os.path.basename(ply_path))[0]
        safe_base = "".join(c if c.isalnum() or c in ("_", "-") else "_" for c in base_name)
        default_file = f"{safe_base}_points.usd"

        save_path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Export USD Points Geometry (.usd, .usda, .usdc)", default_file, "USD Files (*.usd *.usda *.usdc);;All Files (*)"
        )
        if not save_path:
            return

        save_path = save_path.replace("\\", "/")
        params = self._get_points_export_params()

        try:
            from hdri_match_solaris import lop_splat
            lop_splat.export_splat_points_usd(
                scene=scene,
                output_usd_path=save_path,
                point_size=params["point_size"],
                size_mode=params["size_mode"],
                density_pct=params["density_pct"],
                max_points=params["max_points"],
                min_opacity=params["min_opacity"],
                render_mode=params["render_mode"],
                create_material=params["create_material"],
                roughness=params["roughness"],
                flip_y=params["flip_y"],
                scene_scale=params["scene_scale"],
                center_to_origin=params["center_to_origin"],
                snap_floor_y0=params["snap_floor_y0"],
            )
            self.log_success(f"Exported standalone USD Points to: {save_path}")
            if hou.isUIAvailable():
                hou.ui.displayMessage(
                    f"Successfully exported standalone USD Points!\n\n{save_path}",
                    title="USD Points Exported"
                )
        except Exception as e:
            self.log_error(f"Failed to export USD points: {e}")
            if hou.isUIAvailable():
                hou.ui.displayMessage(f"Failed to export USD points: {e}", severity=hou.severityType.Error)

    def _toggle_splat_points_vis(self):
        """Toggle visibility of splat points geometry in Solaris /stage."""
        stage_node = self._get_stage_node()
        if not stage_node:
            return
        from hdri_match_solaris import lop_splat
        ref_node = stage_node.node("splat_points_ref")
        if ref_node:
            is_bypassed = ref_node.isBypassed()
            lop_splat.set_splat_points_vis(stage_node, visible=is_bypassed)
            state_str = "Visible" if is_bypassed else "Hidden"
            self.log_info(f"Splat points visibility: {state_str}")
        else:
            self._convert_and_load_splat_points()

    def _clear_splat_points(self):
        """Clear splat points geometry node from Solaris /stage."""
        stage_node = self._get_stage_node()
        if not stage_node:
            return
        from hdri_match_solaris import lop_splat
        removed = lop_splat.clear_splat_points_from_stage(stage_node)
        if removed:
            self.log_info("Removed splat points geometry from /stage.")

    def _refresh_stage_meshes(self):
        """Scan active Solaris stage for all UsdGeom.Mesh primitives."""
        stage_node = self._get_stage_node()
        if not stage_node:
            return
        from hdri_match_solaris import lop_splat
        meshes = lop_splat.get_stage_mesh_prims(stage_node)
        self.combo_texture_target_mesh.clear()
        if meshes:
            for m in meshes:
                self.combo_texture_target_mesh.addItem(m)
            self.lbl_texturing_status.setText(f"Found {len(meshes)} mesh(es) in stage.")
        else:
            # Fallback defaults
            self.combo_texture_target_mesh.addItem("/stage/room/walls")
            self.combo_texture_target_mesh.addItem("/stage/room/floor")
            self.combo_texture_target_mesh.addItem("/stage/room/ceiling")
            self.lbl_texturing_status.setText("No meshes detected in stage. Using default room paths.")

    def _texture_mesh_from_splats(self):
        """Apply splat vertex colors or projected texture onto the selected mesh."""
        stage_node = self._get_stage_node()
        if not stage_node:
            self.log_error("Solaris /stage node not found.")
            return

        mesh_path = self.combo_texture_target_mesh.currentText().strip()
        if not mesh_path:
            self.log_warning("Please select or enter a target mesh primitive path.")
            return

        mode_idx = self.combo_texturing_mode.currentIndex()
        from hdri_match_solaris import lop_splat

        if mode_idx == 0:
            # 3D Splat Vertex Colors
            scene = self._ensure_points_splat_scene()
            if not scene:
                return
            params = self._get_points_export_params()
            radius = float(self.sld_texture_radius.value())
            res = lop_splat.apply_splat_vertex_colors_to_mesh(
                stage_node,
                mesh_path,
                scene,
                flip_y=params["flip_y"],
                scene_scale=params["scene_scale"],
                center_to_origin=params["center_to_origin"],
                snap_floor_y0=params["snap_floor_y0"],
                search_radius=radius,
            )
            if res.get("status") == "success":
                msg = f"Transferred splat vertex colors to {res.get('vertex_count', 0)} vertices on '{mesh_path}'."
                self.log_success(msg)
                self.lbl_texturing_status.setText(msg)
            else:
                err = res.get("message", "Unknown error")
                self.log_error(f"Texturing failed: {err}")
                self.lbl_texturing_status.setText(f"Error: {err}")
        else:
            # Spherical Projection from Splat Probe
            hdri_path = getattr(self, '_last_baked_hdri', '')
            if not hdri_path or not os.path.isfile(hdri_path):
                if hasattr(self, 'txt_hdri_file'):
                    hdri_path = self.txt_hdri_file.text().strip()
            if not hdri_path or not os.path.isfile(hdri_path):
                self.log_error("No baked or active HDRI found. Please bake 360° HDRI in Tab 1 first.")
                self.lbl_texturing_status.setText("Error: Bake 360° HDRI in Tab 1 first.")
                return

            probe_pos = (
                float(self.sld_probe_x.value()) if hasattr(self, 'sld_probe_x') else 0.0,
                float(self.sld_probe_y.value()) if hasattr(self, 'sld_probe_y') else 1.5,
                float(self.sld_probe_z.value()) if hasattr(self, 'sld_probe_z') else 0.0,
            )
            res = lop_splat.apply_splat_projection_to_mesh(
                stage_node, mesh_path, hdri_path, probe_pos=probe_pos
            )
            if res.get("status") == "success":
                msg = f"Projected splat texture onto '{mesh_path}' using probe origin {probe_pos}."
                self.log_success(msg)
                self.lbl_texturing_status.setText(msg)
            else:
                err = res.get("message", "Unknown error")
                self.log_error(f"Projection failed: {err}")
                self.lbl_texturing_status.setText(f"Error: {err}")

    def _get_custom_props_data(self):
        """Return list of dicts representing all custom props."""
        return [dict(p) for p in getattr(self, '_custom_props', [])]

    def _set_custom_props_data(self, props_list):
        """Populate custom props table and data model from list of prop dicts."""
        self._custom_props = []
        if not hasattr(self, 'tbl_custom_props'):
            return
        self._updating_custom_props_ui = True
        try:
            self.tbl_custom_props.setRowCount(0)
            for p in (props_list or []):
                self._add_custom_prop_row(p)
            self._update_custom_props_status_label()
        finally:
            self._updating_custom_props_ui = False

    def _add_custom_prop_row(self, prop):
        """Add a prop dict to _custom_props and insert a row in tbl_custom_props."""
        if not hasattr(self, 'tbl_custom_props'):
            return
        p = dict(prop)
        p.setdefault("name", f"prop_{len(self._custom_props) + 1}")
        p.setdefault("file_path", "")
        p.setdefault("texture_path", "")
        p.setdefault("uv_mode", "project_splat")
        p.setdefault("tx", 0.0)
        p.setdefault("ty", 0.0)
        p.setdefault("tz", 0.0)
        p.setdefault("rx", 0.0)
        p.setdefault("ry", 0.0)
        p.setdefault("rz", 0.0)
        p.setdefault("scale", 1.0)
        p.setdefault("snap_floor", True)
        p.setdefault("project_texture", True)
        p.setdefault("cast_shadows", True)
        p.setdefault("enabled", True)

        self._custom_props.append(p)
        row = self.tbl_custom_props.rowCount()
        self.tbl_custom_props.insertRow(row)

        chk_item = QtWidgets.QTableWidgetItem()
        chk_item.setFlags(QtCore.Qt.ItemIsUserCheckable | QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsSelectable)
        chk_item.setCheckState(QtCore.Qt.Checked if p["enabled"] else QtCore.Qt.Unchecked)
        self.tbl_custom_props.setItem(row, 0, chk_item)

        name_item = QtWidgets.QTableWidgetItem(str(p["name"]))
        name_item.setFlags(QtCore.Qt.ItemIsEditable | QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsSelectable)
        self.tbl_custom_props.setItem(row, 1, name_item)

        fp_item = QtWidgets.QTableWidgetItem(os.path.basename(p["file_path"]) if p["file_path"] else "")
        fp_item.setToolTip(p["file_path"])
        fp_item.setFlags(QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsSelectable)
        self.tbl_custom_props.setItem(row, 2, fp_item)

        tex_p = p.get("texture_path", "").strip()
        if tex_p:
            tex_str = os.path.basename(tex_p)
            tex_tip = tex_p
        elif p.get("project_texture", True):
            tex_str = "(Room Splat HDRI)"
            tex_tip = "Camera-projected from room splat/HDRI probe"
        else:
            tex_str = "(None - Clay)"
            tex_tip = "No texture assigned; rendered as USD clay"
        tex_item = QtWidgets.QTableWidgetItem(tex_str)
        tex_item.setToolTip(tex_tip)
        tex_item.setFlags(QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsSelectable)
        self.tbl_custom_props.setItem(row, 3, tex_item)

        tx_item = QtWidgets.QTableWidgetItem(f"{float(p['tx']):.2f}")
        tx_item.setFlags(QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsSelectable)
        self.tbl_custom_props.setItem(row, 4, tx_item)

        tz_item = QtWidgets.QTableWidgetItem(f"{float(p['tz']):.2f}")
        tz_item.setFlags(QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsSelectable)
        self.tbl_custom_props.setItem(row, 5, tz_item)

        ry_item = QtWidgets.QTableWidgetItem(f"{float(p['ry']):.1f}°")
        ry_item.setFlags(QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsSelectable)
        self.tbl_custom_props.setItem(row, 6, ry_item)

        s_item = QtWidgets.QTableWidgetItem(f"{float(p['scale']):.2f}")
        s_item.setFlags(QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsSelectable)
        self.tbl_custom_props.setItem(row, 7, s_item)

        snap_item = QtWidgets.QTableWidgetItem("Yes" if p["snap_floor"] else "No")
        snap_item.setFlags(QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsSelectable)
        self.tbl_custom_props.setItem(row, 8, snap_item)

    def _update_custom_props_status_label(self):
        if not hasattr(self, 'lbl_props_status'):
            return
        total = len(self._custom_props)
        active = len([p for p in self._custom_props if p.get("enabled", True)])
        self.lbl_props_status.setText(f"{total} custom props ({active} active).")
        self.lbl_props_status.setStyleSheet("color: #abb2bf; font-size: 11px;")

    def _on_custom_props_table_selection_changed(self):
        """Update transform sliders when a table row is selected."""
        if getattr(self, '_updating_custom_props_ui', False):
            return
        row = self.tbl_custom_props.currentRow()
        if row < 0 or row >= len(self._custom_props):
            return
        p = self._custom_props[row]
        self._updating_custom_props_ui = True
        try:
            self.sld_prop_tx.setValue(float(p.get("tx", 0.0)))
            self.sld_prop_tz.setValue(float(p.get("tz", 0.0)))
            self.sld_prop_ty_offset.setValue(float(p.get("ty", 0.0)))
            self.sld_prop_ry.setValue(float(p.get("ry", 0.0)))
            self.sld_prop_scale.setValue(float(p.get("scale", 1.0)))
            self.chk_prop_snap_floor.setChecked(bool(p.get("snap_floor", True)))
            self.chk_prop_project_tex.setChecked(bool(p.get("project_texture", True)))
            self.chk_prop_cast_shadows.setChecked(bool(p.get("cast_shadows", True)))
            if hasattr(self, 'txt_prop_texture'):
                self.txt_prop_texture.setText(p.get("texture_path", ""))
            if hasattr(self, 'combo_prop_uv_mode'):
                uv_mode = p.get("uv_mode", "project_splat")
                self.combo_prop_uv_mode.setCurrentIndex(1 if uv_mode == "mesh_uv" else 0)
            if hasattr(self, 'combo_prop_shader_target'):
                target_map = {"auto": 0, "karma": 1, "arnold": 2, "redshift": 3, "all": 4, "preview": 5}
                p_t = str(p.get("renderer_target", "auto")).lower()
                self.combo_prop_shader_target.setCurrentIndex(target_map.get(p_t, 0))
        finally:
            self._updating_custom_props_ui = False

    def _on_prop_param_changed(self):
        """Update selected prop data from sliders."""
        if getattr(self, '_updating_custom_props_ui', False):
            return
        row = self.tbl_custom_props.currentRow()
        if row < 0 or row >= len(self._custom_props):
            return
        p = self._custom_props[row]
        p["tx"] = float(self.sld_prop_tx.value())
        p["tz"] = float(self.sld_prop_tz.value())
        p["ty"] = float(self.sld_prop_ty_offset.value())
        p["ry"] = float(self.sld_prop_ry.value())
        p["scale"] = float(self.sld_prop_scale.value())
        p["snap_floor"] = self.chk_prop_snap_floor.isChecked()
        p["project_texture"] = self.chk_prop_project_tex.isChecked()
        p["cast_shadows"] = self.chk_prop_cast_shadows.isChecked()
        if hasattr(self, 'txt_prop_texture'):
            p["texture_path"] = self.txt_prop_texture.text().strip()
        if hasattr(self, 'combo_prop_uv_mode'):
            p["uv_mode"] = "mesh_uv" if self.combo_prop_uv_mode.currentIndex() == 1 else "project_splat"
        if hasattr(self, 'combo_prop_shader_target'):
            targets = ["auto", "karma", "arnold", "redshift", "all", "preview"]
            p["renderer_target"] = targets[self.combo_prop_shader_target.currentIndex()]

        self._updating_custom_props_ui = True
        try:
            item_tex = self.tbl_custom_props.item(row, 3)
            if item_tex:
                tex_p = p.get("texture_path", "").strip()
                if tex_p:
                    item_tex.setText(os.path.basename(tex_p))
                    item_tex.setToolTip(tex_p)
                elif p.get("project_texture", True):
                    item_tex.setText("(Room Splat HDRI)")
                    item_tex.setToolTip("Camera-projected from room splat/HDRI probe")
                else:
                    item_tex.setText("(None - Clay)")
                    item_tex.setToolTip("No texture assigned")
            item_tx = self.tbl_custom_props.item(row, 4)
            if item_tx: item_tx.setText(f"{p['tx']:.2f}")
            item_tz = self.tbl_custom_props.item(row, 5)
            if item_tz: item_tz.setText(f"{p['tz']:.2f}")
            item_ry = self.tbl_custom_props.item(row, 6)
            if item_ry: item_ry.setText(f"{p['ry']:.1f}°")
            item_s = self.tbl_custom_props.item(row, 7)
            if item_s: item_s.setText(f"{p['scale']:.2f}")
            item_snap = self.tbl_custom_props.item(row, 8)
            if item_snap: item_snap.setText("Yes" if p["snap_floor"] else "No")
        finally:
            self._updating_custom_props_ui = False

    def _on_custom_props_table_item_changed(self, item):
        """Handle inline editing of table items (checkbox toggle, name change)."""
        if getattr(self, '_updating_custom_props_ui', False):
            return
        row = item.row()
        col = item.column()
        if row < 0 or row >= len(self._custom_props):
            return
        p = self._custom_props[row]
        if col == 0:
            p["enabled"] = (item.checkState() == QtCore.Qt.Checked)
            self._update_custom_props_status_label()
        elif col == 1:
            p["name"] = item.text().strip().replace(" ", "_")

    def _on_add_custom_props_clicked(self):
        """Open file dialog to add 3D models to custom props list."""
        last_dir = ""
        ply_path = self.txt_splat_file.text().strip() if hasattr(self, 'txt_splat_file') else ""
        if ply_path and os.path.isfile(ply_path):
            last_dir = os.path.dirname(ply_path)
        elif 'hou' in sys.modules and hasattr(hou, 'expandString'):
            last_dir = hou.expandString("$HIP")

        files, _ = QtWidgets.QFileDialog.getOpenFileNames(
            self,
            "Select 3D Models for Room",
            last_dir,
            "3D Geometry Files (*.usd *.usda *.usdc *.obj *.fbx *.bgeo *.bgeo.sc *.abc);;All Files (*.*)"
        )
        if not files:
            return

        for f in files:
            clean_f = f.replace("\\", "/")
            base_name = os.path.splitext(os.path.basename(clean_f))[0]
            sanitized = "".join(c if c.isalnum() or c == '_' else '_' for c in base_name)

            # Auto-detect if asset is modeled in centimeters (e.g. Asset_Manager assets)
            default_scale = 1.0
            try:
                if 'hou' in sys.modules and hasattr(hou, 'Geometry'):
                    g = hou.Geometry()
                    g.loadFromFile(clean_f)
                    bbox = g.boundingBox()
                    max_dim = max(bbox.sizevec())
                    if max_dim > 10.0:  # e.g. 40cm table, 70cm boombox
                        default_scale = 0.01
            except Exception:
                pass

            # Auto-discover textures (e.g. Asset_Manager /textures/ folder)
            tex_file = ""
            cand_dirs = [
                os.path.join(os.path.dirname(clean_f), "textures"),
                os.path.join(os.path.dirname(os.path.dirname(clean_f)), "textures"),
                os.path.dirname(clean_f),
            ]
            for cd in cand_dirs:
                if cd and os.path.isdir(cd):
                    for fn in os.listdir(cd):
                        fl = fn.lower()
                        if fl.endswith((".exr", ".hdr", ".png", ".jpg", ".jpeg", ".tif")) and ("diff" in fl or "basecolor" in fl or "albedo" in fl):
                            tex_file = os.path.join(cd, fn).replace("\\", "/")
                            break
                    if tex_file:
                        break

            prop = {
                "name": sanitized,
                "file_path": clean_f,
                "tx": 0.0,
                "ty": 0.0,
                "tz": 0.0,
                "rx": 0.0,
                "ry": 0.0,
                "rz": 0.0,
                "scale": default_scale,
                "snap_floor": True,
                "project_texture": bool(not tex_file),
                "texture_path": tex_file,
                "uv_mode": "mesh_uv" if tex_file else "project_splat",
                "cast_shadows": True,
                "enabled": True,
            }
            self._add_custom_prop_row(prop)

        self._update_custom_props_status_label()
        if self.tbl_custom_props.rowCount() > 0:
            self.tbl_custom_props.selectRow(self.tbl_custom_props.rowCount() - 1)
        self.log(f"Added {len(files)} custom 3D model(s) to props list.", "INFO")

    def _on_pick_stage_prim_clicked(self):
        """Pick or enter a USD prim or stage geometry path."""
        text, ok = QtWidgets.QInputDialog.getText(
            self,
            "Add Custom Prop",
            "Enter 3D model file path or stage reference (.usd, .obj, .fbx, .bgeo):"
        )
        if not ok or not text.strip():
            return
        clean_path = text.strip().replace("\\", "/")
        base_name = os.path.splitext(os.path.basename(clean_path))[0] or "custom_prop"
        sanitized = "".join(c if c.isalnum() or c == '_' else '_' for c in base_name)

        default_scale = 1.0
        try:
            if 'hou' in sys.modules and hasattr(hou, 'Geometry') and os.path.isfile(clean_path):
                g = hou.Geometry()
                g.loadFromFile(clean_path)
                bbox = g.boundingBox()
                max_dim = max(bbox.sizevec())
                if max_dim > 10.0:
                    default_scale = 0.01
        except Exception:
            pass

        tex_file = ""
        cand_dirs = [
            os.path.join(os.path.dirname(clean_path), "textures"),
            os.path.join(os.path.dirname(os.path.dirname(clean_path)), "textures"),
            os.path.dirname(clean_path),
        ]
        for cd in cand_dirs:
            if cd and os.path.isdir(cd):
                for fn in os.listdir(cd):
                    fl = fn.lower()
                    if fl.endswith((".exr", ".hdr", ".png", ".jpg", ".jpeg", ".tif")) and ("diff" in fl or "basecolor" in fl or "albedo" in fl):
                        tex_file = os.path.join(cd, fn).replace("\\", "/")
                        break
                if tex_file:
                    break

        prop = {
            "name": sanitized,
            "file_path": clean_path,
            "tx": 0.0,
            "ty": 0.0,
            "tz": 0.0,
            "rx": 0.0,
            "ry": 0.0,
            "rz": 0.0,
            "scale": default_scale,
            "snap_floor": True,
            "project_texture": bool(not tex_file),
            "texture_path": tex_file,
            "uv_mode": "mesh_uv" if tex_file else "project_splat",
            "cast_shadows": True,
            "enabled": True,
        }
        self._add_custom_prop_row(prop)
        self._update_custom_props_status_label()
        if self.tbl_custom_props.rowCount() > 0:
            self.tbl_custom_props.selectRow(self.tbl_custom_props.rowCount() - 1)
        self.log(f"Added custom prop '{sanitized}' ({clean_path}).", "INFO")

    def _on_remove_custom_prop_clicked(self):
        """Remove selected prop from table and list."""
        row = self.tbl_custom_props.currentRow()
        if row < 0 or row >= len(self._custom_props):
            return
        removed = self._custom_props.pop(row)
        self._updating_custom_props_ui = True
        try:
            self.tbl_custom_props.removeRow(row)
            self._update_custom_props_status_label()
        finally:
            self._updating_custom_props_ui = False
        self.log(f"Removed custom prop '{removed.get('name')}'.", "INFO")

    def _on_clear_custom_props_clicked(self):
        """Clear all custom props."""
        if not self._custom_props:
            return
        self._custom_props.clear()
        self._updating_custom_props_ui = True
        try:
            self.tbl_custom_props.setRowCount(0)
            self._update_custom_props_status_label()
        finally:
            self._updating_custom_props_ui = False
        self.log("Cleared all custom props from list.", "INFO")

    def _on_populate_splat_props_clicked(self):
        """Redirect legacy populate button to advanced 3D prop reconstruction."""
        self._on_reconstruct_splat_props_clicked()

    def _on_reconstruct_splat_props_clicked(self):
        """Analyze interior Gaussian Splats and reconstruct them into clean 3D polygonal geometry."""
        splat_file = self.txt_splat_file.text().strip() if hasattr(self, 'txt_splat_file') else ""
        if not splat_file or not os.path.isfile(splat_file):
            self.log("Please load a valid .ply Gaussian Splat file first.", "ERROR")
            return

        from hdri_match_solaris.gaussian_splat import GaussianSplatScene
        from hdri_match_solaris.splat_prop_reconstructor import (
            cluster_interior_splats,
            reconstruct_heightfield_prop,
            reconstruct_vdb_prop,
        )

        try:
            self.btn_reconstruct_splat_props.setEnabled(False)
            self.btn_reconstruct_splat_props.setText("⏳ Analyzing & Reconstructing...")
            QtWidgets.QApplication.processEvents()

            # 1. Load splat positions
            scene = getattr(self, '_splat_scene', None)
            if not scene or getattr(scene, 'positions', None) is None:
                scene = GaussianSplatScene.from_ply(splat_file)
                self._splat_scene = scene

            flip_y = self.chk_splat_flip_y.isChecked() if hasattr(self, 'chk_splat_flip_y') else True
            scale_val = float(self.sld_arch_scale.value()) if hasattr(self, 'sld_arch_scale') else 1.0

            pts = scene.positions.copy()
            if flip_y:
                pts[:, 1] = -pts[:, 1]
            pts *= scale_val

            # 2. Query room elevation & bounds
            floor_y = float(self.sld_arch_floor_y.value()) if hasattr(self, 'sld_arch_floor_y') else 0.0
            ceil_y = float(self.sld_arch_ceil_y.value()) if hasattr(self, 'sld_arch_ceil_y') else floor_y + 2.9

            room_bounds = None
            if hasattr(self, '_last_room_data') and self._last_room_data:
                rd = self._last_room_data
                room_bounds = (
                    float(rd.get("x_min", -5.0)),
                    float(rd.get("x_max", 5.0)),
                    float(rd.get("z_min", -5.0)),
                    float(rd.get("z_max", 5.0)),
                )

            # 3. Cluster interior splats
            voxel_param = float(self.sld_reconstruct_voxel.value()) if hasattr(self, 'sld_reconstruct_voxel') else 0.035
            clusters = cluster_interior_splats(
                pts,
                floor_y=floor_y,
                ceil_y=ceil_y,
                room_bounds=room_bounds,
                vox_size=0.15,
                min_splats=600,
            )

            if not clusters:
                self.log("No discrete interior prop clusters found. Ensure the room has furniture or props inside.", "WARNING")
                return

            chosen_method = self.combo_reconstruct_method.currentText() if hasattr(self, 'combo_reconstruct_method') else "Auto"

            out_dir = "E:/PROJECTS/HDRI_MATCH_SOLARIS/scenes/props"
            os.makedirs(out_dir, exist_ok=True)

            existing_names = {p.get("name") for p in self._custom_props}
            reconstructed_count = 0

            for c in clusters:
                method = c["suggested_method"]
                if "Heightfield" in chosen_method:
                    method = "heightfield"
                elif "OpenVDB" in chosen_method:
                    method = "vdb"

                c_name = f"{c['name']}_{method}"
                if c_name in existing_names:
                    c_name = f"{c_name}_{c['id']}"

                out_mesh_file = os.path.join(out_dir, f"{c_name}.bgeo.sc").replace("\\", "/")

                # Reconstruct
                if method == "heightfield":
                    res_file = reconstruct_heightfield_prop(
                        c["points"],
                        floor_y=floor_y,
                        grid_res=max(0.015, voxel_param),
                        skirt_to_floor=True,
                        output_path=out_mesh_file,
                    )
                else:
                    res_file = reconstruct_vdb_prop(
                        c["points"],
                        voxel_size=max(0.015, voxel_param),
                        radius_scale=1.2,
                        smooth_iterations=1,
                        adaptivity=0.01,
                        output_path=out_mesh_file,
                    )

                if res_file and os.path.isfile(res_file):
                    prop_item = {
                        "name": c_name,
                        "file_path": res_file,
                        "tx": float(c["center"][0]),
                        "ty": 0.0,
                        "tz": float(c["center"][2]),
                        "rx": 0.0,
                        "ry": 0.0,
                        "rz": 0.0,
                        "scale": 1.0,
                        "snap_floor": True,
                        "project_texture": True,
                        "uv_mode": "project_splat",
                        "texture_path": "",
                        "cast_shadows": True,
                        "enabled": True,
                    }
                    self._add_custom_prop_entry(prop_item)
                    reconstructed_count += 1

            if reconstructed_count > 0:
                self.log(f"Successfully reconstructed {reconstructed_count} 3D props from Gaussian Splats!", "SUCCESS")
                self._update_custom_props_stage_clicked()
            else:
                self.log("Reconstruction completed but no valid meshes were generated.", "WARNING")

        except Exception as e:
            self.log(f"Prop reconstruction error: {e}", "ERROR")
            traceback.print_exc()
        finally:
            self.btn_reconstruct_splat_props.setEnabled(True)
            self.btn_reconstruct_splat_props.setText("⚡ Reconstruct 3D Props from Splats")

    def _update_custom_props_stage_clicked(self):
        """Update or create custom props in Solaris /stage."""
        stage_node = self._get_stage_node()
        if not stage_node:
            self.log("Solaris /stage node not available.", "ERROR")
            return

        props_data = self._get_custom_props_data()
        if not props_data:
            self.log("No custom props in list to update.", "WARNING")
            return

        try:
            self.btn_update_stage_props.setEnabled(False)
            self.btn_update_stage_props.setText("Updating Stage...")
            from hdri_match_solaris import lop_custom_props

            room_data = getattr(self, '_last_room_data', None)
            if room_data and "center" in room_data and "floor_y" in room_data:
                probe_pos = (float(room_data["center"][0]), float(room_data["floor_y"]) + 1.45, float(room_data["center"][2]))
                floor_y = float(room_data["floor_y"])
            else:
                probe_pos = (0.0, 1.45, 0.0)
                floor_y = float(self.sld_arch_floor_y.value()) if hasattr(self, 'sld_arch_floor_y') else 0.0

            # Find HDRI texture path
            hdri_tex = self.txt_hdri.text().strip() if hasattr(self, 'txt_hdri') else ""
            if not hdri_tex or not os.path.isfile(hdri_tex):
                hip_dir = hou.expandString("$HIP") if 'hou' in sys.modules and hasattr(hou, 'expandString') else "."
                ply_path = self.txt_splat_file.text().strip() if hasattr(self, 'txt_splat_file') else ""
                base_name = os.path.splitext(os.path.basename(ply_path))[0] if ply_path else ""
                cand = os.path.join(hip_dir, "hdri_match", f"{base_name}_splat_baked_hdri.exr").replace(chr(92), "/")
                if os.path.isfile(cand):
                    hdri_tex = cand
                elif getattr(self, '_albedo_texture_path', None) and os.path.isfile(self._albedo_texture_path):
                    hdri_tex = self._albedo_texture_path.replace(chr(92), "/")
                elif getattr(self, '_last_hdri_texture', None) and os.path.isfile(self._last_hdri_texture):
                    hdri_tex = self._last_hdri_texture.replace(chr(92), "/")
                else:
                    # Check existing domelight or cache
                    for d_node in stage_node.children():
                        if "domelight" in d_node.type().name().lower() or d_node.name() in ("hdri_dome", "dome"):
                            for p_name in ["xn__inputstexturefile_06a", "texturefile"]:
                                p_val = d_node.parm(p_name)
                                if p_val and p_val.eval() and os.path.isfile(p_val.eval()):
                                    hdri_tex = p_val.eval().replace(chr(92), "/")
                                    break
                            if hdri_tex: break
                    if not hdri_tex:
                        cache_cands = [
                            "E:/PROJECTS/HDRI_MATCH_SOLARIS/SPLATS/Reception room/splatforge_cache/textures/hdri_dome.exr",
                            "E:/PROJECTS/HDRI_MATCH_SOLARIS/SPLATS/Reception room/splatforge_cache/textures/hdri_dome_2048x1024.exr",
                            "E:/PROJECTS/HDRI_MATCH_SOLARIS/hdri_match/planar_textures_4096/floor_albedo.exr",
                        ]
                        for cc in cache_cands:
                            if os.path.isfile(cc):
                                hdri_tex = cc
                                break

            # Determine renderer shader target (Karma MaterialX, Arnold, Redshift, All, USD Preview)
            renderer_target = "all"
            if hasattr(self, 'combo_prop_shader_target'):
                _p_idx = self.combo_prop_shader_target.currentIndex()
                _prop_targets = ["auto", "karma", "arnold", "redshift", "all", "preview"]
                if 0 <= _p_idx < len(_prop_targets) and _prop_targets[_p_idx] != "auto":
                    renderer_target = _prop_targets[_p_idx]
            if renderer_target == "all" or (hasattr(self, 'combo_prop_shader_target') and self.combo_prop_shader_target.currentIndex() == 0):
                if hasattr(self, 'combo_arch_renderer_target'):
                    _a_idx = self.combo_arch_renderer_target.currentIndex()
                    _arch_targets = ["all", "karma", "arnold", "redshift", "preview"]
                    if 0 <= _a_idx < len(_arch_targets):
                        renderer_target = _arch_targets[_a_idx]
                elif hasattr(self, 'combo_renderer_target'):
                    _r_idx = self.combo_renderer_target.currentIndex()
                    _render_targets = ["all", "arnold", "karma", "redshift", "preview"]
                    if 0 <= _r_idx < len(_render_targets):
                        renderer_target = _render_targets[_r_idx]

            res_node = lop_custom_props.setup_custom_props_nodes(
                stage_node,
                props_data,
                probe_pos=probe_pos,
                floor_y=floor_y,
                hdri_texture=hdri_tex,
                renderer_target=renderer_target,
                mat_mode="pbr",
                wire_into_stream=True,
            )
            act_count = len([p for p in props_data if p.get("enabled", True)])
            self.lbl_props_status.setText(f"{act_count} active props updated in /stage/room/custom_props.")
            self.lbl_props_status.setStyleSheet("color: #2ecc71; font-weight: bold; font-size: 11px;")
            self.log(f"Updated {act_count} custom props in /stage/room/custom_props with splat projection & floor snap.", "SUCCESS")
        except Exception as e:
            self.log_error(f"Failed to update custom props in stage: {e}", e)
        finally:
            self.btn_update_stage_props.setEnabled(True)
            self.btn_update_stage_props.setText("📦 Update Props in Stage")

    def _clear_custom_props_stage_clicked(self):
        """Remove custom props nodes from Solaris /stage."""
        stage_node = self._get_stage_node()
        if not stage_node:
            return
        from hdri_match_solaris import lop_custom_props
        lop_custom_props.clear_custom_props_nodes(stage_node)
        self.lbl_props_status.setText("Custom props cleared from /stage.")
        self.lbl_props_status.setStyleSheet("color: #888888; font-size: 11px;")
        self.log("Removed custom props LOP nodes from /stage.", "INFO")

    def _on_browse_prop_texture_clicked(self):
        """Browse for an existing texture map (.exr, .hdr, .png, .jpg, .tif) for selected prop."""
        row = self.tbl_custom_props.currentRow()
        if row < 0 or row >= len(self._custom_props):
            if self._custom_props:
                self.tbl_custom_props.selectRow(0)
                row = 0
            else:
                self.log("Please add at least one custom prop to the list first.", "WARNING")
                return
        p = self._custom_props[row]
        last_dir = ""
        cur_tex = p.get("texture_path", "")
        if cur_tex and os.path.isfile(cur_tex):
            last_dir = os.path.dirname(cur_tex)
        elif p.get("file_path") and os.path.isfile(p["file_path"]):
            last_dir = os.path.dirname(p["file_path"])
        elif 'hou' in sys.modules and hasattr(hou, 'expandString'):
            last_dir = hou.expandString("$HIP")

        file_path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            f"Select Texture Map for {p.get('name', 'Prop')}",
            last_dir,
            "Texture Files (*.exr *.hdr *.png *.jpg *.jpeg *.tif *.tiff *.tx *.rat);;All Files (*.*)"
        )
        if not file_path:
            return
        clean_p = file_path.replace("\\", "/")
        p["texture_path"] = clean_p
        if hasattr(self, 'txt_prop_texture'):
            self.txt_prop_texture.setText(clean_p)
        self._on_prop_param_changed()
        self._update_custom_props_stage_clicked()
        self.log(f"Assigned texture '{os.path.basename(clean_p)}' to prop '{p.get('name')}'.", "SUCCESS")

    def _on_clear_prop_texture_clicked(self):
        """Clear custom texture for selected prop and revert to default/room projection."""
        row = self.tbl_custom_props.currentRow()
        if row < 0 or row >= len(self._custom_props):
            return
        p = self._custom_props[row]
        p["texture_path"] = ""
        if hasattr(self, 'txt_prop_texture'):
            self.txt_prop_texture.setText("")
        self._on_prop_param_changed()
        self._update_custom_props_stage_clicked()
        self.log(f"Cleared texture for prop '{p.get('name')}'.", "INFO")

    def _on_bake_prop_texture_clicked(self):
        """Bake equirectangular texture from Gaussian Splat points or HDRI and assign to selected prop."""
        row = self.tbl_custom_props.currentRow()
        if row < 0 or row >= len(self._custom_props):
            if self._custom_props:
                self.tbl_custom_props.selectRow(0)
                row = 0
            else:
                self.log("Please add at least one 3D prop to the list first.", "WARNING")
                return

        prop = self._custom_props[row]
        p_name = prop.get("name", "prop").strip().replace(" ", "_")

        ply_path = self.txt_splat_file.text().strip() if hasattr(self, 'txt_splat_file') else ""
        hdri_path = self.txt_hdri.text().strip() if hasattr(self, 'txt_hdri') else ""

        # Case 1: Neither Splat nor HDRI loaded
        if (not ply_path or not os.path.isfile(ply_path)) and (not hdri_path or not os.path.isfile(hdri_path)):
            dlg = QtWidgets.QMessageBox(self)
            dlg.setWindowTitle("Bake Prop Texture - Missing Splat / HDRI")
            dlg.setText("No 3D Gaussian Splat (.ply) or 360° HDRI is currently loaded to bake textures from.")
            dlg.setInformativeText("Choose an option to supply splats or textures for this prop:")

            btn_sample = dlg.addButton("🌟 Load Sample Splat (Room)", QtWidgets.QMessageBox.ActionRole)
            btn_browse_splat = dlg.addButton("📂 Browse .ply Splat...", QtWidgets.QMessageBox.ActionRole)
            btn_browse_hdri = dlg.addButton("🌐 Browse 360° HDRI...", QtWidgets.QMessageBox.ActionRole)
            btn_browse_tex = dlg.addButton("🎨 Pick Prop Texture...", QtWidgets.QMessageBox.ActionRole)
            btn_cancel = dlg.addButton("Cancel", QtWidgets.QMessageBox.RejectRole)
            dlg.exec_()

            clicked = dlg.clickedButton()
            if clicked == btn_sample:
                cand_splats = [
                    "tests/splats/Reception room/scene.ply",
                    "tests/splats/Apartment/scene.ply",
                    "tests/splats/flowers_1/flowers_1.ply",
                ]
                hip_dir = hou.expandString("$HIP") if 'hou' in sys.modules and hasattr(hou, 'expandString') else "."
                found_sample = ""
                for cs in cand_splats:
                    full = os.path.join(hip_dir, cs).replace("\\", "/")
                    if os.path.isfile(full):
                        found_sample = full
                        break
                    proj_cand = os.path.abspath(cs).replace("\\", "/")
                    if os.path.isfile(proj_cand):
                        found_sample = proj_cand
                        break
                if found_sample:
                    self.txt_splat_file.setText(found_sample)
                    self._load_splat_scene(found_sample)
                    ply_path = found_sample
                    self.log(f"Loaded sample splat: {found_sample}", "SUCCESS")
                else:
                    self.log("Sample splat file not found. Please browse for a .ply file.", "WARNING")
                    return
            elif clicked == btn_browse_splat:
                self._browse_splat_file()
                ply_path = self.txt_splat_file.text().strip()
                if not ply_path or not os.path.isfile(ply_path):
                    return
            elif clicked == btn_browse_hdri:
                self._browse_hdri()
                hdri_path = self.txt_hdri.text().strip()
                if not hdri_path or not os.path.isfile(hdri_path):
                    return
            elif clicked == btn_browse_tex:
                self._on_browse_prop_texture_clicked()
                return
            else:
                return

        # Check bake source selection
        bake_source = self.combo_prop_bake_source.currentText() if hasattr(self, 'combo_prop_bake_source') else "Probe"

        # If user explicitly chose HDRI or no splat is available, use HDRI albedo
        if "HDRI" in bake_source or (not ply_path and hdri_path):
            if not hdri_path or not os.path.isfile(hdri_path):
                self.log("Valid 360° HDRI file is required for HDRI baking mode.", "WARNING")
                return
            try:
                from hdri_match_solaris.gaussian_splat import GaussianSplatBaker
                import numpy as np
                stem, ext = os.path.splitext(hdri_path)
                cand_albedo = f"{stem}_albedo{ext}" if "_albedo" not in stem else hdri_path
                if not os.path.isfile(cand_albedo):
                    self.log(f"Generating albedo compression map from {os.path.basename(hdri_path)}...", "INFO")
                    raw_arr = GaussianSplatBaker.load_exr(hdri_path)
                    if raw_arr is not None:
                        albedo_arr = np.clip(raw_arr / (1.0 + raw_arr * 0.35) * 0.70, 0.0, 0.85)
                        GaussianSplatBaker.save_exr(albedo_arr, cand_albedo)
                prop["texture_path"] = cand_albedo.replace("\\", "/")
                prop["project_texture"] = True
                if hasattr(self, 'txt_prop_texture'):
                    self.txt_prop_texture.setText(prop["texture_path"])
                self._on_prop_param_changed()
                self._update_custom_props_stage_clicked()
                self.log(f"Assigned HDRI albedo texture '{os.path.basename(cand_albedo)}' to prop '{p_name}'.", "SUCCESS")
                return
            except Exception as e:
                self.log_error(f"Failed to process HDRI for prop: {e}", e)
                return

        # Gaussian Splat Baking Mode
        if not ply_path or not os.path.isfile(ply_path):
            self.log("Please load a valid .ply Gaussian Splat file.", "WARNING")
            return

        res_text = self.combo_prop_bake_res.currentText() if hasattr(self, 'combo_prop_bake_res') else "2048"
        if "1024" in res_text:
            width, height = 1024, 512
        elif "4096" in res_text:
            width, height = 4096, 2048
        else:
            width, height = 2048, 1024

        # Viewpoint camera coordinate in splat space
        scale_val = float(self.sld_arch_scale.value()) if hasattr(self, 'sld_arch_scale') else 1.0
        scale_inv = 1.0 / max(1e-5, scale_val)
        if "Prop Center" in bake_source:
            cam_pos = (
                float(prop.get("tx", 0.0)) * scale_inv,
                (float(prop.get("ty", 0.0)) + 0.35) * scale_inv,
                float(prop.get("tz", 0.0)) * scale_inv
            )
        else:
            if getattr(self, '_last_room_data', None):
                c = self._last_room_data.get("center", [0.0, 0.0, 0.0])
                cam_pos = (float(c[0]) * scale_inv, (float(self._last_room_data.get("floor_y", 0.0)) + 1.45) * scale_inv, float(c[2]) * scale_inv)
            elif getattr(self, '_splat_scene', None) and self._splat_scene.center is not None:
                cam_pos = tuple(self._splat_scene.center)
            else:
                cam_pos = (0.0, 1.5 * scale_inv, 0.0)

        hip_dir = hou.expandString("$HIP") if 'hou' in sys.modules and hasattr(hou, 'expandString') else "."
        if not hip_dir or hip_dir == "." or "houdini_temp" in hip_dir:
            hip_dir = os.path.expanduser("~")
        out_dir = os.path.join(hip_dir, "hdri_match", "props_baked").replace("\\", "/")
        os.makedirs(out_dir, exist_ok=True)

        # 1. Local 3D Gaussian Splat Surface Baking (True object texture, NOT outward panorama)
        if ("Prop Center" in bake_source or "Surface" in bake_source) and getattr(self, '_splat_scene', None):
            try:
                from hdri_match_solaris.gaussian_splat import GaussianSplatBaker
                albedo_path = os.path.join(out_dir, f"{p_name}_splat_baked_albedo.exr").replace("\\", "/")
                self.log(f"Baking authentic surface texture for '{p_name}' directly from 3D Gaussian splats...", "INFO")
                res_px = min(1024, max(256, width))
                baked_tex = GaussianSplatBaker.bake_prop_surface_texture(
                    self._splat_scene,
                    prop,
                    albedo_path,
                    scene_scale=scale_val,
                    flip_y=self.chk_splat_flip_y.isChecked() if hasattr(self, 'chk_splat_flip_y') else True,
                    res=res_px,
                )
                if baked_tex and os.path.isfile(baked_tex):
                    prop["texture_path"] = baked_tex
                    prop["project_texture"] = True
                    prop["uv_mode"] = "mesh_uv"
                    if hasattr(self, 'txt_prop_texture'):
                        self.txt_prop_texture.setText(baked_tex)
                    self._on_prop_param_changed()
                    self._update_custom_props_stage_clicked()
                    self.log(f"Assigned authentic splat surface texture '{os.path.basename(baked_tex)}' to '{p_name}'.", "SUCCESS")
                    return
            except Exception as ex_surf:
                self.log(f"Surface bake note: {ex_surf}, falling back to panorama bake...", "WARNING")

        out_path = os.path.join(out_dir, f"{p_name}_splat_baked.exr").replace("\\", "/")

        self.btn_bake_prop_texture.setEnabled(False)
        self.btn_bake_prop_texture.setText(f"🔥 Baking '{p_name}' Texture (0%)...")
        if hasattr(self, 'pbar_prop_bake'):
            self.pbar_prop_bake.setValue(0)
            self.pbar_prop_bake.setFormat(f"Baking '{p_name}' Texture: 0%")
            self.pbar_prop_bake.setVisible(True)

        self.log(f"Baking splat texture for prop '{p_name}' ({width}x{height}) from {ply_path} at {cam_pos}...", "INFO")
        self._current_baking_prop_row = row

        params = {
            "ply_path": ply_path,
            "cam_pos": cam_pos,
            "width": width,
            "height": height,
            "out_path": out_path,
            "scene": getattr(self, '_splat_scene', None),
            "splat_scale": float(self.sld_splat_scale.value()) if hasattr(self, 'sld_splat_scale') else 1.8,
            "min_dist": float(self.sld_splat_min_dist.value()) if hasattr(self, 'sld_splat_min_dist') else 0.20,
            "flip_y": self.chk_splat_flip_y.isChecked() if hasattr(self, 'chk_splat_flip_y') else True,
        }
        self._prop_bake_worker.request_bake(params)

    def _on_prop_bake_progress(self, pct):
        if hasattr(self, 'pbar_prop_bake'):
            self.pbar_prop_bake.setValue(pct)
            self.pbar_prop_bake.setFormat(f"Baking Prop Texture: {pct}%")
        self.btn_bake_prop_texture.setText(f"🔥 Baking Prop Texture ({pct}%)...")

    def _on_prop_bake_finished(self, out_path):
        self.btn_bake_prop_texture.setEnabled(True)
        self.btn_bake_prop_texture.setText("🔥 Bake Prop Texture from Splat / HDRI")
        if hasattr(self, 'pbar_prop_bake'):
            self.pbar_prop_bake.setValue(100)
            self.pbar_prop_bake.setFormat("Prop Texture Baked (100%)")

        row = getattr(self, '_current_baking_prop_row', -1)
        if row < 0 or row >= len(self._custom_props):
            row = self.tbl_custom_props.currentRow()
        if row < 0 or row >= len(self._custom_props):
            row = 0

        # Perform albedo compression on baked EXR so it behaves as physical surface diffuse
        albedo_path = out_path
        try:
            from hdri_match_solaris.gaussian_splat import GaussianSplatBaker
            import numpy as np
            stem, ext = os.path.splitext(out_path)
            albedo_path = f"{stem}_albedo{ext}"
            raw_arr = GaussianSplatBaker.load_exr(out_path)
            if raw_arr is not None:
                albedo_arr = np.clip(raw_arr / (1.0 + raw_arr * 0.35) * 0.70, 0.0, 0.85)
                GaussianSplatBaker.save_exr(albedo_arr, albedo_path)
        except Exception as ex:
            print(f"[HDRI Match] Prop albedo compression note: {ex}")
            albedo_path = out_path

        if 0 <= row < len(self._custom_props):
            prop = self._custom_props[row]
            prop["texture_path"] = albedo_path.replace("\\", "/")
            prop["project_texture"] = True
            if hasattr(self, 'txt_prop_texture'):
                self.txt_prop_texture.setText(prop["texture_path"])
            self._on_prop_param_changed()
            self._update_custom_props_stage_clicked()
            p_name = prop.get("name", "prop")
            self.log(f"Successfully baked splat texture for '{p_name}': {albedo_path}", "SUCCESS")
            if hou.isUIAvailable():
                hou.ui.displayMessage(f"Prop texture successfully baked and assigned to '{p_name}':\n{albedo_path}", title="Prop Splat Texture Baked")

    def _on_prop_bake_failed(self, error_msg):
        self.btn_bake_prop_texture.setEnabled(True)
        self.btn_bake_prop_texture.setText("🔥 Bake Prop Texture from Splat / HDRI")
        if hasattr(self, 'pbar_prop_bake'):
            self.pbar_prop_bake.setValue(0)
            self.pbar_prop_bake.setFormat("Bake Failed")
        self.log_error("Prop texture bake failed", error_msg)
        if hou.isUIAvailable():
            hou.ui.displayMessage(f"Prop texture bake failed:\n{error_msg}", severity=hou.severityType.Error)

    def _auto_scale_room_clicked(self):
        """Analyze splats to find unscaled ceiling height and automatically compute the scale for 2.9m ceiling."""
        ply_path = self.txt_splat_file.text().strip() if hasattr(self, 'txt_splat_file') else ""
        if not ply_path or not os.path.isfile(ply_path):
            self.log("Please load a valid 3D Gaussian Splat (.ply) file first.", "WARNING")
            return
        try:
            from hdri_match_solaris import gaussian_splat
            if not hasattr(self, '_splat_scene') or not self._splat_scene or getattr(self._splat_scene, 'filepath', '') != ply_path:
                self._splat_scene = gaussian_splat.GaussianSplatScene.from_ply(ply_path)
            flip_y = self.chk_splat_flip_y.isChecked() if hasattr(self, 'chk_splat_flip_y') else True
            raw_room = self._splat_scene.analyze_room_architecture(flip_y=flip_y, scene_scale=1.0, extract_props=False)
            if not raw_room:
                return
            raw_h = raw_room.get("raw_height", raw_room.get("height", 3.0))
            if raw_h > 0.1:
                target_scale = float(2.9 / raw_h)
                self.sld_arch_scale.setValue(target_scale)
                msg = f"Auto-scale set to {target_scale:.4f} (Raw height {raw_h:.2f}m -> 2.90m Ceiling)"
                self.lbl_arch_status.setText(msg)
                self.lbl_arch_status.setStyleSheet("color: #2980b9; font-weight: bold; font-size: 11px;")
                self.log(msg, "INFO")
                # If room architecture already exists in /stage, automatically rebuild at calibrated scale
                stage_node = self._get_stage_node()
                if stage_node and stage_node.node("splat_room_architecture"):
                    self._build_room_architecture_clicked()
        except Exception as e:
            self.log_error(f"Auto-scale failed: {e}", e)

    def _build_room_architecture_clicked(self):
        """Analyze 3D Gaussian Splats and build USD Room Architecture in /stage."""
        ply_path = self.txt_splat_file.text().strip()
        if not ply_path or not os.path.isfile(ply_path):
            self.log("Please load a valid 3D Gaussian Splat (.ply) file first.", "WARNING")
            return

        stage_node = self._get_stage_node()
        if not stage_node:
            self.log("Solaris /stage not available.", "ERROR")
            return

        # Ensure conflicting legacy projection nodes (hdri_match_projection, hdri_match_materials) are disabled
        for p_name in ("hdri_match_projection", "hdri_match_materials"):
            pn = stage_node.node(p_name)
            if pn:
                pn.bypass(True)
        if hasattr(self, 'grp_ground_proj'):
            self.grp_ground_proj.setChecked(False)

        try:
            import importlib
            from hdri_match_solaris import gaussian_splat, lop_splat
            importlib.reload(gaussian_splat)
            importlib.reload(lop_splat)

            self.btn_build_room_arch.setEnabled(False)
            self.btn_build_room_arch.setText("Analyzing Architecture...")
            self.log("Analyzing 3D Gaussian Splat room architecture...", "INFO")

            if not hasattr(self, '_splat_scene') or not self._splat_scene or getattr(self._splat_scene, 'filepath', '') != ply_path:
                self._splat_scene = gaussian_splat.GaussianSplatScene.from_ply(ply_path)

            flip_y = self.chk_splat_flip_y.isChecked() if hasattr(self, 'chk_splat_flip_y') else True
            scale_val = float(self.sld_arch_scale.value()) if hasattr(self, 'sld_arch_scale') else 1.0
            int_mode = "tight" if (hasattr(self, 'combo_arch_interior_mode') and self.combo_arch_interior_mode.currentIndex() == 0) else "full"
            build_props = self.chk_arch_props.isChecked() if hasattr(self, 'chk_arch_props') else True
            proxy_shape_sel = self.combo_arch_proxy_shape.currentText() if hasattr(self, 'combo_arch_proxy_shape') else "⚡ Auto 3D Meshes (VDB & Depth Map)"
            if "Depth Map" in proxy_shape_sel:
                proxy_shape_mode = "mesh_depth"
            elif "OpenVDB" in proxy_shape_sel:
                proxy_shape_mode = "mesh_vdb"
            elif "3D Mesh" in proxy_shape_sel or "Meshes" in proxy_shape_sel:
                proxy_shape_mode = "mesh_auto"
            elif "Boxes Only" in proxy_shape_sel:
                proxy_shape_mode = "box"
            elif "Sphere" in proxy_shape_sel:
                proxy_shape_mode = "sphere"
            else:
                proxy_shape_mode = "auto"

            room_data = self._splat_scene.analyze_room_architecture(
                flip_y=flip_y,
                scene_scale=scale_val,
                interior_mode=int_mode,
                extract_props=build_props,
                proxy_shape_mode=proxy_shape_mode,
            )
            if not room_data:
                self.log("Failed to extract architectural boundaries from splats.", "ERROR")
                self.btn_build_room_arch.setEnabled(True)
                self.btn_build_room_arch.setText("🏛️ Analyze & Build Room Architecture")
                return

            # Auto-scale check: if scale was 1.0 but ceiling is unscaled photogrammetry units (>6m), auto-calibrate to standard 2.9m ceiling
            raw_h = room_data.get("raw_height", room_data.get("height", 3.0))
            if abs(scale_val - 1.0) < 1e-4 and raw_h > 6.0:
                auto_scale = float(2.9 / raw_h)
                self.log(
                    f"[Scale Auto-Correction] Splats were reconstructed in unscaled photogrammetry units (ceiling height {raw_h:.1f}m). "
                    f"Automatically calibrated scale to {auto_scale:.4f} for a realistic 2.90m ceiling.",
                    "WARNING"
                )
                self.sld_arch_scale.setValue(auto_scale)
                scale_val = auto_scale
                room_data = self._splat_scene.analyze_room_architecture(
                    flip_y=flip_y,
                    scene_scale=scale_val,
                    interior_mode=int_mode,
                    extract_props=build_props,
                    proxy_shape_mode=proxy_shape_mode,
                )

            # If 3D prop meshing is selected, run reconstruction on detected prop clusters
            if build_props and room_data.get("props") and proxy_shape_mode.startswith("mesh_"):
                from hdri_match_solaris.splat_prop_reconstructor import reconstruct_prop_cluster
                voxel_res = float(self.sld_arch_prop_res.value()) if hasattr(self, 'sld_arch_prop_res') else 0.035
                mesh_method = "heightfield" if proxy_shape_mode == "mesh_depth" else ("vdb" if proxy_shape_mode == "mesh_vdb" else "auto")
                floor_y_val = float(room_data.get("floor_y", 0.0))

                hip_dir = hou.expandString("$HIP") if 'hou' in sys.modules and hasattr(hou, 'expandString') else "."
                if not hip_dir or hip_dir == "." or "houdini_temp" in hip_dir:
                    hip_dir = "E:/PROJECTS/HDRI_MATCH_SOLARIS"
                out_props_dir = os.path.join(hip_dir, "scenes", "props").replace("\\", "/")
                os.makedirs(out_props_dir, exist_ok=True)

                self.log(f"Reconstructing {len(room_data['props'])} interior 3D prop meshes ({proxy_shape_sel}, res: {voxel_res}m)...", "INFO")
                for p in room_data["props"]:
                    try:
                        m_file = reconstruct_prop_cluster(
                            prop=p,
                            method=mesh_method,
                            floor_y=floor_y_val,
                            voxel_res=voxel_res,
                            output_dir=out_props_dir,
                        )
                        if m_file and os.path.isfile(m_file):
                            p["mesh_file"] = m_file
                            p["shape"] = "mesh"
                            self.log(f"  ✓ Prop '{p.get('name')}' reconstructed -> {os.path.basename(m_file)}", "INFO")
                    except Exception as ex_m:
                        self.log(f"Warning: Prop mesh calculation for '{p.get('name')}' failed: {ex_m}. Falling back to proxy primitive.", "WARNING")

            self._last_room_data = room_data

            # Update UI dimension sliders
            self.sld_arch_width.setValue(room_data["width"])
            self.sld_arch_depth.setValue(room_data["depth"])
            self.sld_arch_height.setValue(room_data["height"])
            self.sld_arch_floor_y.setValue(room_data["floor_y"])

            # Build USD Geometry
            build_floor = self.chk_arch_floor.isChecked()
            floor_mode = "visible" if self.combo_arch_floor_mode.currentIndex() == 0 else "matte"
            build_ceiling = self.chk_arch_ceiling.isChecked()
            build_walls = self.chk_arch_walls.isChecked()
            cut_windows = self.chk_arch_windows.isChecked()
            build_portals = self.chk_arch_portals.isChecked()
            snap_lookdev = self.chk_arch_snap_lookdev.isChecked()
            double_sided = self.chk_arch_double_sided.isChecked()
            room_shadows = self.chk_arch_shadows.isChecked()
            room_invisible = self.chk_arch_invisible.isChecked() if hasattr(self, 'chk_arch_invisible') else False
            portal_int = float(self.sld_arch_portal_intensity.value())
            portal_tex_sel = self.combo_arch_portal_texture.currentText()
            portal_tex_mode = "cropped" if "Cropped" in portal_tex_sel else ("full" if "Full" in portal_tex_sel else "none")
            project_hdri = self.chk_arch_project_hdri.isChecked()
            roughness = float(self.sld_arch_roughness.value())

            probe_pos = (float(self.sld_probe_x.value()), float(self.sld_probe_y.value()), float(self.sld_probe_z.value()))

            # 1. Resolve or auto-bake HDRI texture path
            hdri_tex = ""
            calibrated_path = getattr(self, '_current_calibrated_path', None)
            if calibrated_path and os.path.isfile(calibrated_path):
                hdri_tex = calibrated_path.replace("\\", "/")
            elif hasattr(self, 'txt_hdri') and self.txt_hdri.text().strip() and os.path.isfile(self.txt_hdri.text().strip()):
                hdri_tex = self.txt_hdri.text().strip().replace("\\", "/")

            hip_dir = hou.expandString("$HIP") if 'hou' in sys.modules and hasattr(hou, 'expandString') else "."
            if not hip_dir or hip_dir == "." or "houdini_temp" in hip_dir:
                hip_dir = os.path.expanduser("~")
            base_name = os.path.splitext(os.path.basename(ply_path))[0] if ply_path else "scene"

            if not hdri_tex or not os.path.isfile(hdri_tex):
                cand_dirs = [
                    os.path.join(hip_dir, "hdri_match"),
                    os.path.join(hip_dir, "scenes", "hdri_match"),
                    os.path.join(os.path.dirname(ply_path), "hdri_match") if ply_path else "",
                    os.path.join(hip_dir, "tests", "hdri_match"),
                    os.path.join(hip_dir, "scenes"),
                    "E:/PROJECTS/HDRI_MATCH_SOLARIS/scenes/hdri_match",
                    "E:/PROJECTS/HDRI_MATCH_SOLARIS/hdri_match",
                ]
                cand_names = [
                    f"{base_name}_splat_baked_hdri.exr",
                    f"{base_name}_splat_baked_hdri_calibrated_A.exr",
                    f"{base_name}_splat_baked_hdri_calibrated_B.exr",
                    f"{base_name}_splat_baked_hdri_albedo.exr",
                    f"{base_name}_splat_baked.exr",
                    "scene_splat_baked_hdri.exr",
                    "scene_splat_baked_hdri_calibrated_A.exr",
                    "flowers_1_splat_baked_hdri_calibrated_A.exr",
                ]
                for cd in cand_dirs:
                    if not cd or not os.path.isdir(cd):
                        continue
                    for cn in cand_names:
                        full = os.path.join(cd, cn).replace("\\", "/")
                        if os.path.isfile(full):
                            hdri_tex = full
                            break
                    if hdri_tex:
                        break

            # If still not found on disk, auto-bake 360° environment map from splats at probe position
            if (not hdri_tex or not os.path.isfile(hdri_tex)) and getattr(self, '_splat_scene', None):
                try:
                    from hdri_match_solaris.gaussian_splat import GaussianSplatBaker
                    out_dir = os.path.join(hip_dir, "hdri_match").replace("\\", "/")
                    os.makedirs(out_dir, exist_ok=True)
                    out_path = os.path.join(out_dir, f"{base_name}_splat_baked_hdri.exr").replace("\\", "/")
                    b_w, b_h = 4096, 2048
                    if hasattr(self, 'combo_splat_res'):
                        r_txt = self.combo_splat_res.currentText()
                        if "8192" in r_txt or "8k" in r_txt.lower():
                            b_w, b_h = 8192, 4096
                        elif "4096" in r_txt:
                            b_w, b_h = 4096, 2048
                        elif "2048" in r_txt:
                            b_w, b_h = 2048, 1024
                    self.log(f"Auto-baking 360° environment HDRI ({b_w}x{b_h}) from splats at probe {probe_pos}...", "INFO")
                    fast_img = GaussianSplatBaker.bake_equirectangular(
                        self._splat_scene,
                        camera_pos=probe_pos,
                        width=b_w,
                        height=b_h,
                        splat_scale=1.8,
                        flip_y=flip_y,
                        hdr_expand=True,
                        hdr_boost=35.0,
                    )
                    GaussianSplatBaker.save_exr(fast_img, out_path, pixel_type="float")
                    if os.path.isfile(out_path):
                        hdri_tex = out_path
                        self.log(f"Auto-baked 360° environment HDRI from splats: {hdri_tex}", "SUCCESS")
                except Exception as ex_bake:
                    self.log(f"Could not auto-bake splat HDRI: {ex_bake}", "WARNING")

            # 1b. Auto-bake authentic surface textures for detected props if missing
            if build_props and room_data.get("props") and getattr(self, '_splat_scene', None):
                try:
                    from hdri_match_solaris.gaussian_splat import GaussianSplatBaker
                    props_baked_dir = os.path.join(hip_dir, "hdri_match", "props_baked").replace("\\", "/")
                    os.makedirs(props_baked_dir, exist_ok=True)
                    for prop_item in room_data.get("props", []):
                        p_name = prop_item.get("name", "prop")
                        p_tex_path = os.path.join(props_baked_dir, f"{p_name}_splat_baked_albedo.exr").replace("\\", "/")
                        if not os.path.isfile(p_tex_path):
                            self.log(f"Auto-baking authentic surface texture for '{p_name}' from 3D splats...", "INFO")
                            GaussianSplatBaker.bake_prop_surface_texture(
                                self._splat_scene,
                                prop_item,
                                p_tex_path,
                                scene_scale=scale_val,
                                flip_y=flip_y,
                                res=256,
                            )
                except Exception as ex_pbake:
                    self.log(f"Prop auto-bake warning: {ex_pbake}", "DETAIL")

            # 2. Automatically create/update native Solaris Dome Light (/stage/hdri_dome) with HDRI texture
            dome_node = stage_node.node("hdri_dome")
            if dome_node is None:
                dome_node = stage_node.createNode("domelight", "hdri_dome")
                self.log("Created native Solaris /stage/hdri_dome (domelight)", "SUCCESS")
            else:
                self.log("Reusing existing /stage/hdri_dome (domelight)", "DETAIL")
            dome_node.bypass(False)
            arch_targets = ["all", "arnold", "karma", "redshift", "preview"]
            arch_target = "all"
            if hasattr(self, 'combo_arch_renderer_target'):
                _idx = self.combo_arch_renderer_target.currentIndex()
                if 0 <= _idx < len(arch_targets):
                    arch_target = arch_targets[_idx]
            if arch_target == "arnold":
                _ensure_dome_light_latlong(dome_node)

            if hdri_tex and os.path.isfile(hdri_tex):
                clean_tex = hdri_tex.replace("\\", "/")
                tex_parm = dome_node.parm("xn__inputstexturefile_r3ah")
                if tex_parm:
                    tex_parm.set(clean_tex)

                # Sync UI field and preview in Tab 1 (only display clean source, never overwrite with calibrated)
                if hasattr(self, 'txt_hdri'):
                    current_txt = self.txt_hdri.text().strip().replace("\\", "/")
                    raw_candidate = _clean_hdri_path(clean_tex)
                    # If txt_hdri is empty or missing on disk, set it to the raw candidate
                    if not current_txt or not os.path.isfile(_resolve_image_path(current_txt)):
                        target_to_show = raw_candidate if os.path.isfile(_resolve_image_path(raw_candidate)) else clean_tex
                        self.txt_hdri.setText(target_to_show)
                        try:
                            self._load_hdri_preview(target_to_show)
                        except Exception:
                            pass
                    elif "_calibrated" in os.path.basename(current_txt):
                        # Self-heal corrupted txt_hdri if it was pointing to a calibrated file
                        if os.path.isfile(_resolve_image_path(raw_candidate)):
                            self.txt_hdri.setText(raw_candidate)
                            try:
                                self._load_hdri_preview(raw_candidate)
                            except Exception:
                                pass

            # Configure dome light exposure & orientation
            exp_parm = dome_node.parm("xn__inputsexposure_vya")
            if exp_parm:
                exp_parm.set(float(self.sld_ev.value()) if hasattr(self, 'sld_ev') else 0.0)

            ry_parm = dome_node.parm("ry")
            if ry_parm:
                ry_parm.set(float(self.sld_yaw.value()) if hasattr(self, 'sld_yaw') else 0.0)

            solaris_probe = (float(room_data["center"][0]), float(room_data["floor_y"]) + 1.45, float(room_data["center"][2]))

            # Material Mode & Emissive Multiplier
            mat_mode = "pbr"
            if hasattr(self, 'combo_arch_mat_mode'):
                idx = self.combo_arch_mat_mode.currentIndex()
                if idx == 1:
                    mat_mode = "emissive"
                elif idx == 2:
                    mat_mode = "pbr_emissive"
            emissive_mult = float(self.sld_arch_emissive_mult.value()) if hasattr(self, 'sld_arch_emissive_mult') else 1.0

            # Texture mapping mode: Planar High-Detail vs Equirectangular
            use_planar = True
            if hasattr(self, 'combo_arch_tex_mode'):
                use_planar = (self.combo_arch_tex_mode.currentIndex() == 0)

            # Auto-resolve or bake planar textures if Planar mode is active
            pres = 8192
            if use_planar and project_hdri:
                res_txt = self.combo_arch_planar_res.currentText() if hasattr(self, 'combo_arch_planar_res') else "8K"
                if "8K" in res_txt or "8192" in res_txt:
                    pres = 8192
                elif "4K" in res_txt or "4096" in res_txt:
                    pres = 4096
                elif "1K" in res_txt or "1024" in res_txt:
                    pres = 1024
                else:
                    pres = 2048

                planar_dir = os.path.join(hip_dir, "hdri_match", f"planar_textures_{pres}").replace("\\", "/")
                os.makedirs(planar_dir, exist_ok=True)

                needed_surfaces = ["floor", "ceiling", "wall_north", "wall_south", "wall_east", "wall_west"]
                existing_planar = {}
                for sname in needed_surfaces:
                    for sfx in ["_diffuse.exr", "_albedo.exr"]:
                        fpath = os.path.join(planar_dir, f"{sname}{sfx}").replace("\\", "/")
                        if os.path.isfile(fpath):
                            existing_planar[sname] = fpath
                            break

                if len(existing_planar) == 6:
                    self._planar_textures_dict = existing_planar
                    self.log(f"Loaded existing {len(existing_planar)} planar surface textures ({pres}x{pres}).", "INFO")
                elif hdri_tex and os.path.isfile(hdri_tex):
                    try:
                        self.log(f"Auto-baking high-detail planar surface textures ({pres}x{pres}) via multi-threading...", "INFO")
                        if hasattr(self, 'pbar_planar'):
                            self.pbar_planar.setVisible(True)
                            self.pbar_planar.setValue(25)
                            self.pbar_planar.setFormat(f"Baking Planar Textures ({pres}x{pres})...")
                        from hdri_match_solaris.gaussian_splat import GaussianSplatBaker
                        self._planar_textures_dict = GaussianSplatBaker.bake_planar_room_textures(
                            hdri_source=hdri_tex,
                            room_data=room_data,
                            output_dir=planar_dir,
                            probe_pos=solaris_probe,
                            resolution_floor=pres,
                            resolution_walls=pres,
                        )
                        if hasattr(self, 'pbar_planar'):
                            self.pbar_planar.setValue(100)
                            self.pbar_planar.setFormat(f"Planar Ready ({pres}x{pres})")
                        self.log(f"Auto-baked {len(self._planar_textures_dict)} planar textures ({pres}x{pres}) successfully.", "SUCCESS")
                    except Exception as ex_pln:
                        self.log_error(f"Planar auto-bake warning: {ex_pln}", ex_pln)

            _arch_targets = ["all", "arnold", "karma", "redshift", "preview"]
            arch_renderer_target = "all"
            if hasattr(self, 'combo_arch_renderer_target'):
                _a_idx = self.combo_arch_renderer_target.currentIndex()
                if 0 <= _a_idx < len(_arch_targets):
                    arch_renderer_target = _arch_targets[_a_idx]

            node = lop_splat.build_usd_room_architecture(
                stage_node,
                room_data,
                build_floor=build_floor,
                floor_mode=floor_mode,
                build_ceiling=build_ceiling,
                build_walls=build_walls,
                cut_windows=cut_windows,
                build_portals=build_portals,
                build_props=build_props,
                project_hdri=project_hdri,
                hdri_texture=hdri_tex,
                probe_pos=solaris_probe,
                roughness=roughness,
                snap_lookdev_to_floor=snap_lookdev,
                double_sided=double_sided,
                room_shadows=room_shadows,
                room_invisible=room_invisible,
                portal_intensity_mult=portal_int,
                portal_texture_mode=portal_tex_mode,
                mat_mode=mat_mode,
                emissive_mult=emissive_mult,
                use_planar_textures=(use_planar and bool(self._planar_textures_dict)),
                planar_textures_dict=self._planar_textures_dict,
                renderer_target=arch_renderer_target,
            )

            # Synchronize detected columns/props into Tab 4 Custom Props table if table is empty
            if build_props and room_data.get("props") and hasattr(self, '_custom_props') and not self._custom_props:
                try:
                    self._on_populate_splat_props_clicked()
                except Exception:
                    pass

            # Ensure lookdev rig is updated with user's lookdev rig settings and proper stand height
            if snap_lookdev:
                try:
                    from hdri_match_solaris import lop_lookdev
                    lookdev_r = float(self.sld_splat_lookdev_radius.value()) if hasattr(self, 'sld_splat_lookdev_radius') else (float(self.sld_lookdev_radius.value()) if hasattr(self, 'sld_lookdev_radius') else 0.15)
                    lookdev_h = float(self.sld_splat_lookdev_height.value()) if hasattr(self, 'sld_splat_lookdev_height') else (float(self.sld_lookdev_height.value()) if hasattr(self, 'sld_lookdev_height') else 1.2)
                    lookdev_s = float(self.sld_splat_lookdev_scale.value()) if hasattr(self, 'sld_splat_lookdev_scale') else 1.0
                    inc_white = self.chk_splat_lookdev_white.isChecked() if hasattr(self, 'chk_splat_lookdev_white') else (self.chk_lookdev_white.isChecked() if hasattr(self, 'chk_lookdev_white') else True)
                    inc_macbeth = self.chk_splat_lookdev_macbeth.isChecked() if hasattr(self, 'chk_splat_lookdev_macbeth') else True
                    inc_stand = True

                    lop_lookdev.create_lookdev_rig_node(
                        stage_node,
                        "hdri_match_lookdev",
                        pos=(room_data["center"][0], room_data["floor_y"], room_data["center"][2]),
                        radius=lookdev_r,
                        stand_height=lookdev_h,
                        rig_scale=lookdev_s,
                        include_white=inc_white,
                        include_macbeth=inc_macbeth,
                        include_stand=inc_stand,
                        lookdev_en=True,
                    )
                except Exception as ex_ld:
                    print(f"[HDRI Match] Lookdev setup note: {ex_ld}")

            # Ensure any active custom props are synchronized into /stage/room/custom_props
            try:
                from hdri_match_solaris import lop_custom_props
                props_data = self._get_custom_props_data() if hasattr(self, '_get_custom_props_data') else []
                if props_data:
                    lop_custom_props.setup_custom_props_nodes(
                        stage_node,
                        props_data,
                        probe_pos=solaris_probe,
                        floor_y=room_data["floor_y"],
                        hdri_texture=hdri_tex,
                        renderer_target=arch_renderer_target,
                        mat_mode=mat_mode,
                        wire_into_stream=True,
                    )
            except Exception as ex_cp:
                print(f"[HDRI Match] Custom props setup note: {ex_cp}")

            # Deterministically wire all stage nodes in linear acyclic order
            try:
                from hdri_match_solaris.lop_splat import wire_solaris_stage_stream
                wire_solaris_stage_stream(stage_node)
            except Exception as ex_w:
                print(f"[HDRI Match] Final stage wire note: {ex_w}")

            win_count = len(room_data.get("windows", []))
            props_list = room_data.get("props", []) if build_props else []
            col_count = sum(1 for p in props_list if "column" in p.get("name", "").lower() or p.get("shape") == "cylinder")
            prop_info = f" | {len(props_list)} Props ({col_count} Columns)" if props_list else ""
            planar_info = f" | Planar {pres//1024}K" if (use_planar and getattr(self, '_planar_textures_dict', None)) else ""
            portal_info = f" | {win_count} Portals Textured" if win_count > 0 else ""
            dome_status = f"HDRI Dome ({os.path.basename(hdri_tex)})" if hdri_tex else "HDRI Dome Active"

            self.lbl_arch_status.setText(
                f"Built Room: {room_data['width']:.1f}m × {room_data['depth']:.1f}m × {room_data['height']:.1f}m | {dome_status}{portal_info}{prop_info}{planar_info}"
            )
            self.lbl_arch_status.setStyleSheet("color: #2ecc71; font-weight: bold; font-size: 11px;")
            self.log(
                f"Built USD Room Architecture: W={room_data['width']:.2f}m, D={room_data['depth']:.2f}m, H={room_data['height']:.2f}m with {win_count} window portals, {len(props_list)} props/columns, and {pres//1024}K planar textures.",
                "SUCCESS"
            )
        except Exception as e:
            self.log_error(f"Failed to build room architecture: {e}", e)
        finally:
            self.btn_build_room_arch.setEnabled(True)
            self.btn_build_room_arch.setText("🏛️ Analyze & Build Room Architecture")

    def _on_portal_intensity_changed(self, val):
        """Live update physical portal light intensities in /stage without full scene rebuild."""
        stage_node = self._get_stage_node()
        if not stage_node or not hou:
            return
        mult = float(val)
        updated = 0
        for child in stage_node.children():
            if child.name().startswith("portal_") and "light" in child.type().name():
                p = child.parm("xn__inputsintensity_i0a")
                if p:
                    tex_p = child.parm("xn__inputstexturefile_r3ah")
                    has_tex = bool(tex_p and tex_p.eval())
                    base = 5.0 if has_tex else 8.0
                    p.set(max(0.01, base * mult))
                    updated += 1
        if updated > 0:
            self.log(f"Live updated {updated} physical portal lights intensity (multiplier={mult:.2f})", "DETAIL")

    def _on_arch_material_changed(self, *args):
        """Live update room architecture materials when material mode or emissive mult changes."""
        if getattr(self, '_restoring_state', False) or getattr(self, '_initializing', False):
            return
        stage_node = self._get_stage_node()
        if not stage_node or not hou:
            return

        if hasattr(self, 'combo_arch_renderer_target'):
            _a_idx = self.combo_arch_renderer_target.currentIndex()
            _a_target = ["all", "arnold", "karma", "redshift", "preview"][_a_idx] if 0 <= _a_idx < 5 else "all"
            if _a_target == "all":
                if hasattr(self, 'sld_arch_roughness'):
                    self.sld_arch_roughness.setValue(1.0)
            elif _a_target == "arnold":
                dome_node = stage_node.node("hdri_dome")
                if dome_node:
                    _ensure_dome_light_latlong(dome_node)

        arch_node = stage_node.node("splat_room_architecture")
        if not getattr(self, '_last_room_data', None) and getattr(self, '_splat_scene', None):
            try:
                flip_y = self.chk_splat_flip_y.isChecked() if hasattr(self, 'chk_splat_flip_y') else True
                scale_val = float(self.sld_arch_scale.value()) if hasattr(self, 'sld_arch_scale') else 1.0
                int_mode = "tight" if (hasattr(self, 'combo_arch_interior_mode') and self.combo_arch_interior_mode.currentIndex() == 0) else "full"
                build_props = self.chk_arch_props.isChecked() if hasattr(self, 'chk_arch_props') else True
                self._last_room_data = self._splat_scene.analyze_room_architecture(
                    flip_y=flip_y,
                    scene_scale=scale_val,
                    interior_mode=int_mode,
                    extract_props=build_props,
                )
            except Exception:
                pass
        if arch_node and getattr(self, '_last_room_data', None):
            self._build_room_architecture_clicked()
        proj_node = stage_node.node("hdri_match_projection")
        if arch_node:
            if proj_node:
                proj_node.bypass(True)
        elif proj_node and not proj_node.isBypassed():
            self._create_or_update_ground_projection(notify_ui=False)

    def _on_arch_shading_toggled(self, *args):
        """Live update room architecture shading and visibility flags (Double-Sided, Shadows, Invisible) in /stage."""
        stage_node = self._get_stage_node()
        if not stage_node or not hou:
            return
        arch_node = stage_node.node("splat_room_architecture")
        if not arch_node or arch_node.isBypassed():
            return
        try:
            ds = self.chk_arch_double_sided.isChecked()
            sh = self.chk_arch_shadows.isChecked()
            inv = self.chk_arch_invisible.isChecked() if hasattr(self, 'chk_arch_invisible') else False

            changed = False
            for pname, pval in [("double_sided", ds), ("room_shadows", sh), ("room_invisible", inv)]:
                p = arch_node.parm(pname)
                if p and p.eval() != pval:
                    p.set(pval)
                    changed = True
            if changed:
                arch_node.cook(force=True)
                self.log(f"Live updated Room Shading: Double-Sided={ds}, Shadows={sh}, Invisible={inv}", "DETAIL")
            elif not arch_node.parm("double_sided"):
                # Node didn't have spare parameters yet, rebuild via _build_room_architecture_clicked
                self._build_room_architecture_clicked()
        except Exception as e:
            self.log_error(f"Failed to update room shading: {e}", e)

    def _on_portal_texture_mode_changed(self, *args):
        """Live update physical portal light textures (Cropped / Full / None) in /stage."""
        stage_node = self._get_stage_node()
        if not stage_node or not hou:
            return
        room_data = getattr(self, '_last_room_data', None)
        windows = room_data.get("windows", []) if room_data else []
        if not windows:
            return
        try:
            from hdri_match_solaris import lop_splat
            portal_tex_sel = self.combo_arch_portal_texture.currentText()
            portal_tex_mode = "cropped" if "Cropped" in portal_tex_sel else ("full" if "Full" in portal_tex_sel else "none")
            portal_int = float(self.sld_arch_portal_intensity.value()) if hasattr(self, 'sld_arch_portal_intensity') else 1.0
            hdri_tex = getattr(self, '_current_calibrated_path', "") or (self.txt_hdri.text().strip() if hasattr(self, 'txt_hdri') else "")
            c = room_data.get("center", [0.0, 1.45, 0.0])
            probe_pos = (float(c[0]), float(room_data.get("floor_y", 0.0)) + 1.45, float(c[2]))
            lop_splat.spawn_portal_lights(
                stage_node,
                windows,
                cam_pos=probe_pos,
                hdri_texture=hdri_tex,
                portal_intensity_mult=portal_int,
                portal_texture_mode=portal_texture_mode,
                clear_existing=True,
            )
            lop_splat.wire_solaris_stage_stream(stage_node)
            self.log(f"Updated portal lights texture mode: {portal_tex_sel}", "INFO")
        except Exception as e:
            self.log_error(f"Failed to update portal texture mode: {e}", e)

    def _on_splat_lookdev_param_changed(self, *args):
        """Live update existing Lookdev Rig spare parameters in /stage without full node rebuild."""
        stage_node = self._get_stage_node()
        if not stage_node or not hou:
            return
        ld_node = stage_node.node("hdri_match_lookdev")
        if not ld_node or ld_node.isBypassed():
            return
        try:
            lookdev_r = float(self.sld_splat_lookdev_radius.value()) if hasattr(self, 'sld_splat_lookdev_radius') else 0.15
            lookdev_h = float(self.sld_splat_lookdev_height.value()) if hasattr(self, 'sld_splat_lookdev_height') else 1.2
            lookdev_s = float(self.sld_splat_lookdev_scale.value()) if hasattr(self, 'sld_splat_lookdev_scale') else 1.0
            inc_white = self.chk_splat_lookdev_white.isChecked() if hasattr(self, 'chk_splat_lookdev_white') else True
            inc_macbeth = self.chk_splat_lookdev_macbeth.isChecked() if hasattr(self, 'chk_splat_lookdev_macbeth') else True

            changed = False
            for pname, pval in [
                ("sphere_radius", lookdev_r),
                ("stand_height", lookdev_h),
                ("rig_scale", lookdev_s),
                ("include_white", inc_white),
                ("include_macbeth", inc_macbeth),
            ]:
                p = ld_node.parm(pname)
                if p and p.eval() != pval:
                    p.set(pval)
                    changed = True
            if changed:
                ld_node.cook(force=True)
        except Exception:
            pass

    def _bake_planar_room_textures_clicked(self):
        """Bake high-resolution planar rectilinear textures for floor, ceiling, and walls."""
        if not getattr(self, '_last_room_data', None):
            if getattr(self, '_splat_scene', None):
                flip_y = self.chk_splat_flip_y.isChecked() if hasattr(self, 'chk_splat_flip_y') else True
                scale_val = float(self.sld_arch_scale.value()) if hasattr(self, 'sld_arch_scale') else 1.0
                int_mode = "tight" if (hasattr(self, 'combo_arch_interior_mode') and self.combo_arch_interior_mode.currentIndex() == 0) else "full"
                build_props = self.chk_arch_props.isChecked() if hasattr(self, 'chk_arch_props') else True
                proxy_shape_sel = self.combo_arch_proxy_shape.currentText() if hasattr(self, 'combo_arch_proxy_shape') else "⚡ Auto 3D Meshes (VDB & Depth Map)"
                if "Depth Map" in proxy_shape_sel:
                    proxy_shape_mode = "mesh_depth"
                elif "OpenVDB" in proxy_shape_sel:
                    proxy_shape_mode = "mesh_vdb"
                elif "3D Mesh" in proxy_shape_sel or "Meshes" in proxy_shape_sel:
                    proxy_shape_mode = "mesh_auto"
                elif "Boxes Only" in proxy_shape_sel:
                    proxy_shape_mode = "box"
                elif "Sphere" in proxy_shape_sel:
                    proxy_shape_mode = "sphere"
                else:
                    proxy_shape_mode = "auto"
                self._last_room_data = self._splat_scene.analyze_room_architecture(
                    flip_y=flip_y,
                    scene_scale=scale_val,
                    interior_mode=int_mode,
                    extract_props=build_props,
                    proxy_shape_mode=proxy_shape_mode,
                )
            else:
                self.log("Please load a Gaussian Splat (.ply) and analyze room architecture first.", "WARNING")
                if hou.isUIAvailable():
                    hou.ui.displayMessage("No room architecture data available. Please load a splat PLY or build room architecture first.", title="HDRI Match")
                return

        room_data = self._last_room_data

        # Determine HDRI source
        hdri_source = getattr(self, '_current_calibrated_path', None)
        if not hdri_source or not os.path.isfile(hdri_source):
            if hasattr(self, 'txt_hdri') and self.txt_hdri.text().strip() and os.path.isfile(self.txt_hdri.text().strip()):
                hdri_source = self.txt_hdri.text().strip()

        hip_dir = hou.expandString("$HIP") if 'hou' in sys.modules and hasattr(hou, 'expandString') else "."
        if not hip_dir or hip_dir == "." or "houdini_temp" in hip_dir:
            hip_dir = os.path.expanduser("~")
        ply_path = self.txt_splat_file.text().strip() if hasattr(self, 'txt_splat_file') else ""
        base_name = os.path.splitext(os.path.basename(ply_path))[0] if ply_path else "scene"

        if not hdri_source or not os.path.isfile(hdri_source):
            cand_dirs = [
                os.path.join(hip_dir, "hdri_match"),
                os.path.join(hip_dir, "scenes", "hdri_match"),
                os.path.join(os.path.dirname(ply_path), "hdri_match") if ply_path else "",
                os.path.join(hip_dir, "scenes"),
            ]
            for cd in cand_dirs:
                if not cd or not os.path.isdir(cd):
                    continue
                for cname in [f"{base_name}_splat_baked_hdri.exr", f"{base_name}_splat_baked_hdri_calibrated_A.exr", "scene_splat_baked_hdri.exr"]:
                    full = os.path.join(cd, cname).replace("\\", "/")
                    if os.path.isfile(full):
                        hdri_source = full
                        break
                if hdri_source:
                    break

        res_txt = self.combo_arch_planar_res.currentText() if hasattr(self, 'combo_arch_planar_res') else "2K"
        if "8K" in res_txt or "8192" in res_txt:
            res_val = 8192
        elif "4K" in res_txt or "4096" in res_txt:
            res_val = 4096
        elif "1K" in res_txt or "1024" in res_txt:
            res_val = 1024
        else:
            res_val = 2048

        # If no HDRI file exists or if 8K planar is requested and HDRI is lower resolution, auto-bake 360 HDRI from splats
        bake_w = 8192 if res_val == 8192 else 4096
        bake_h = 4096 if res_val == 8192 else 2048
        need_bake_hdri = (not hdri_source or not os.path.isfile(hdri_source))
        if not need_bake_hdri and res_val == 8192 and getattr(self, '_splat_scene', None):
            try:
                import OpenImageIO as oiio
                inp = oiio.ImageInput.open(hdri_source)
                if inp:
                    cur_w = inp.spec().width
                    inp.close()
                    if cur_w < 8192:
                        need_bake_hdri = True
            except Exception:
                pass

        if need_bake_hdri and getattr(self, '_splat_scene', None):
            try:
                from hdri_match_solaris.gaussian_splat import GaussianSplatBaker
                out_dir = os.path.join(hip_dir, "hdri_match").replace("\\", "/")
                os.makedirs(out_dir, exist_ok=True)
                out_hdri = os.path.join(out_dir, f"{base_name}_splat_baked_hdri_{bake_w}.exr" if res_val == 8192 else f"{base_name}_splat_baked_hdri.exr").replace("\\", "/")
                probe_pos = (float(self.sld_probe_x.value()), float(self.sld_probe_y.value()), float(self.sld_probe_z.value())) if hasattr(self, 'sld_probe_x') else (0.0, 1.5, 0.0)
                flip_y = self.chk_splat_flip_y.isChecked() if hasattr(self, 'chk_splat_flip_y') else True
                self.log(f"Auto-baking 360° master HDRI ({bake_w}x{bake_h}) for planar projection...", "INFO")
                fast_img = GaussianSplatBaker.bake_equirectangular(
                    self._splat_scene,
                    camera_pos=probe_pos,
                    width=bake_w,
                    height=bake_h,
                    splat_scale=1.8,
                    flip_y=flip_y,
                    hdr_expand=True,
                    hdr_boost=35.0,
                )
                GaussianSplatBaker.save_exr(fast_img, out_hdri, pixel_type="float")
                if os.path.isfile(out_hdri):
                    hdri_source = out_hdri
            except Exception as e_bake:
                self.log(f"Auto-bake HDRI failed: {e_bake}", "WARNING")

        if not hdri_source or not os.path.isfile(hdri_source):
            self.log("Please bake or provide a 360° HDRI before baking planar textures.", "WARNING")
            if hou.isUIAvailable():
                hou.ui.displayMessage("No 360° HDRI available to sample planar textures from. Please bake HDRI first.", title="Planar Bake")
            return

        out_planar_dir = os.path.join(hip_dir, "hdri_match", "planar_textures").replace("\\", "/")
        probe_pos = (float(room_data["center"][0]), float(room_data["floor_y"]) + 1.45, float(room_data["center"][2]))

        self.btn_bake_planar.setEnabled(False)
        self.btn_bake_planar.setText(f"🎨 Baking Planar ({res_val}x{res_val})...")
        if hasattr(self, 'pbar_planar'):
            self.pbar_planar.setVisible(True)
            self.pbar_planar.setValue(0)
            self.pbar_planar.setFormat("Starting Planar Texture Bake...")

        hdri_inpainted, was_inp = self._get_inpainted_hdri_source(hdri_source, planar_dir=out_planar_dir)
        params = {
            "hdri_source": hdri_inpainted,
            "room_data": room_data,
            "output_dir": out_planar_dir,
            "probe_pos": probe_pos,
            "resolution": res_val,
        }
        self._planar_bake_worker.request_bake(params)

    def _on_planar_bake_progress(self, pct, name):
        if hasattr(self, 'pbar_planar'):
            self.pbar_planar.setValue(pct)
            self.pbar_planar.setFormat(f"Baking {name}: {pct}%")
        if hasattr(self, 'btn_bake_planar'):
            self.btn_bake_planar.setText(f"🎨 Baking {name} ({pct}%)...")

    def _on_planar_bake_finished(self, tex_dict):
        self._planar_textures_dict = dict(tex_dict)
        if hasattr(self, 'btn_bake_planar'):
            self.btn_bake_planar.setEnabled(True)
            self.btn_bake_planar.setText("🎨 Bake Planar Textures (High Detail)")
        if hasattr(self, 'pbar_planar'):
            self.pbar_planar.setValue(100)
            self.pbar_planar.setFormat("Planar Textures Ready (100%)")
        self.log(f"Successfully baked {len(tex_dict)} planar rectilinear textures: {list(tex_dict.keys())}", "SUCCESS")
        # Automatically update room architecture on stage with the new planar textures
        self._build_room_architecture_clicked()
        if hou.isUIAvailable():
            hou.ui.displayMessage(f"High-detail planar textures baked successfully ({len(tex_dict)} surfaces) and applied to USD Room Architecture!", title="Planar Textures Baked")

    def _on_planar_bake_failed(self, err_msg):
        if hasattr(self, 'btn_bake_planar'):
            self.btn_bake_planar.setEnabled(True)
            self.btn_bake_planar.setText("🎨 Bake Planar Textures (High Detail)")
        if hasattr(self, 'pbar_planar'):
            self.pbar_planar.setValue(0)
            self.pbar_planar.setFormat("Planar Bake Failed")
        self.log_error("Planar texture bake failed", err_msg)
        if hou.isUIAvailable():
            hou.ui.displayMessage(f"Planar texture bake failed:\n{err_msg}", severity=hou.severityType.Error)

    def _update_lookdev_rig_clicked(self):
        """Update or create the Lookdev Verification Rig with live UI parameters at the physical room center."""
        stage_node = self._get_stage_node()
        if not stage_node:
            self.log("Solaris /stage not available.", "ERROR")
            return
        try:
            from hdri_match_solaris import lop_lookdev

            # Resolve physical room center and floor Y
            room_data = getattr(self, '_last_room_data', None)
            if not room_data and getattr(self, '_splat_scene', None):
                try:
                    flip_y = self.chk_splat_flip_y.isChecked() if hasattr(self, 'chk_splat_flip_y') else True
                    scale_val = float(self.sld_arch_scale.value()) if hasattr(self, 'sld_arch_scale') else 1.0
                    int_mode = "tight" if (hasattr(self, 'combo_arch_interior_mode') and self.combo_arch_interior_mode.currentIndex() == 0) else "full"
                    build_props = self.chk_arch_props.isChecked() if hasattr(self, 'chk_arch_props') else True
                    proxy_shape_sel = self.combo_arch_proxy_shape.currentText() if hasattr(self, 'combo_arch_proxy_shape') else "⚡ Auto 3D Meshes (VDB & Depth Map)"
                    if "Depth Map" in proxy_shape_sel:
                        proxy_shape_mode = "mesh_depth"
                    elif "OpenVDB" in proxy_shape_sel:
                        proxy_shape_mode = "mesh_vdb"
                    elif "3D Mesh" in proxy_shape_sel or "Meshes" in proxy_shape_sel:
                        proxy_shape_mode = "mesh_auto"
                    elif "Boxes Only" in proxy_shape_sel:
                        proxy_shape_mode = "box"
                    elif "Sphere" in proxy_shape_sel:
                        proxy_shape_mode = "sphere"
                    else:
                        proxy_shape_mode = "auto"
                    room_data = self._splat_scene.analyze_room_architecture(
                        flip_y=flip_y,
                        scene_scale=scale_val,
                        interior_mode=int_mode,
                        extract_props=build_props,
                        proxy_shape_mode=proxy_shape_mode,
                    )
                    self._last_room_data = room_data
                except Exception:
                    pass

            if room_data and "center" in room_data:
                pos_x = float(room_data["center"][0])
                pos_z = float(room_data["center"][2])
                floor_y = float(self.sld_arch_floor_y.value()) if hasattr(self, 'sld_arch_floor_y') else float(room_data.get("floor_y", 0.0))
            else:
                ld_existing = stage_node.node("hdri_match_lookdev")
                if ld_existing and ld_existing.parm("pos_x") and (abs(ld_existing.parm("pos_x").eval()) > 1e-4 or abs(ld_existing.parm("pos_z").eval()) > 1e-4):
                    pos_x = float(ld_existing.parm("pos_x").eval())
                    pos_z = float(ld_existing.parm("pos_z").eval())
                    floor_y = float(ld_existing.parm("floor_y").eval())
                elif hasattr(self, 'sld_arch_floor_y'):
                    pos_x = 0.0
                    pos_z = 0.0
                    floor_y = float(self.sld_arch_floor_y.value())
                else:
                    pos_x = float(self.sld_probe_x.value()) if hasattr(self, 'sld_probe_x') else 0.0
                    pos_z = float(self.sld_probe_z.value()) if hasattr(self, 'sld_probe_z') else 0.0
                    floor_y = 0.0

            lookdev_r = float(self.sld_splat_lookdev_radius.value()) if hasattr(self, 'sld_splat_lookdev_radius') else 0.15
            lookdev_h = float(self.sld_splat_lookdev_height.value()) if hasattr(self, 'sld_splat_lookdev_height') else 1.2
            lookdev_s = float(self.sld_splat_lookdev_scale.value()) if hasattr(self, 'sld_splat_lookdev_scale') else 1.0
            inc_white = self.chk_splat_lookdev_white.isChecked() if hasattr(self, 'chk_splat_lookdev_white') else True
            inc_macbeth = self.chk_splat_lookdev_macbeth.isChecked() if hasattr(self, 'chk_splat_lookdev_macbeth') else True

            ld_node = lop_lookdev.create_lookdev_rig_node(
                stage_node,
                "hdri_match_lookdev",
                pos=(pos_x, floor_y, pos_z),
                radius=lookdev_r,
                stand_height=lookdev_h,
                rig_scale=lookdev_s,
                include_white=inc_white,
                include_macbeth=inc_macbeth,
                include_stand=True,
                lookdev_en=True,
            )
            self._merge_light_networks(notify_ui=False)
            if ld_node:
                ld_node.bypass(False)
                ld_node.cook(force=True)
                ld_node.setDisplayFlag(True)
            if hasattr(self, 'chk_arch_snap_lookdev'):
                self.chk_arch_snap_lookdev.setChecked(True)
            self.log(
                f"Updated Lookdev Rig: Stand Height={lookdev_h:.2f}m, Ball Radius={lookdev_r:.2f}m, Scale={lookdev_s:.2f}x at Room Center=({pos_x:.2f}, {floor_y:.2f}, {pos_z:.2f})",
                "SUCCESS"
            )
        except Exception as e:
            self.log_error(f"Failed to update lookdev rig: {e}", e)

    def _clear_room_architecture_clicked(self):
        """Remove USD Room Architecture from /stage."""
        stage_node = self._get_stage_node()
        if not stage_node:
            return
        from hdri_match_solaris import lop_splat
        res = lop_splat.clear_usd_room_architecture(stage_node)
        if res:
            self.lbl_arch_status.setText("Room architecture cleared.")
            self.lbl_arch_status.setStyleSheet("color: #888888; font-size: 11px;")
            self.log("Removed /stage/room architecture node.", "INFO")

    def _assign_room_textures_clicked(self):
        """Assign baked 360° HDRI (with albedo compression) to interior meshes and apply cropped textures to portal lights."""
        stage_node = self._get_stage_node()
        if not stage_node:
            self.log("Solaris /stage node not available.", "ERROR")
            return

        # 1. Locate the baked HDRI file
        hdri_path = self.txt_hdri.text().strip() if hasattr(self, 'txt_hdri') else ""
        ply_path = self.txt_splat_file.text().strip() if hasattr(self, 'txt_splat_file') else ""

        if not hdri_path or not os.path.isfile(hdri_path):
            hip_dir = hou.expandString("$HIP") if 'hou' in sys.modules and hasattr(hou, 'expandString') else "."
            base_name = os.path.splitext(os.path.basename(ply_path))[0] if ply_path else ""
            candidates = [
                os.path.join(hip_dir, "hdri_match", f"{base_name}_splat_baked_hdri.exr"),
                os.path.join(hip_dir, "scenes", "restaurant_cock_n_hen_hdri.exr"),
                "E:/PROJECTS/HDRI_MATCH_SOLARIS/scenes/restaurant_cock_n_hen_hdri.exr",
            ]
            for cand in candidates:
                if cand and os.path.isfile(cand):
                    hdri_path = cand.replace(chr(92), "/")
                    break

        if not hdri_path or not os.path.isfile(hdri_path):
            if hou.isUIAvailable():
                hdri_path, _ = QtWidgets.QFileDialog.getOpenFileName(
                    self, "Select Baked HDRI EXR Map",
                    os.path.dirname(ply_path) if ply_path else ".",
                    "OpenEXR / HDR Files (*.exr *.hdr);;All Files (*.*)"
                )
            if not hdri_path or not os.path.isfile(hdri_path):
                self.log("Please select or bake a 360° HDRI map first.", "WARNING")
                return
            if hasattr(self, 'txt_hdri'):
                self.txt_hdri.setText(hdri_path)

        try:
            self.btn_assign_room_textures.setEnabled(False)
            self.btn_assign_room_textures.setText("Assigning Textures...")
            self.log(f"Assigning baked HDRI ({os.path.basename(hdri_path)}) and portal textures to room...", "INFO")

            from hdri_match_solaris import gaussian_splat, lop_splat

            # Ensure room data is available
            room_data = getattr(self, '_last_room_data', None)
            if not room_data:
                if not hasattr(self, '_splat_scene') or not self._splat_scene:
                    if ply_path and os.path.isfile(ply_path):
                        self._splat_scene = gaussian_splat.GaussianSplatScene.from_ply(ply_path)
                if hasattr(self, '_splat_scene') and self._splat_scene:
                    flip_y = self.chk_splat_flip_y.isChecked() if hasattr(self, 'chk_splat_flip_y') else True
                    scale_val = float(self.sld_arch_scale.value()) if hasattr(self, 'sld_arch_scale') else 1.0
                    int_mode = "tight" if (hasattr(self, 'combo_arch_interior_mode') and self.combo_arch_interior_mode.currentIndex() == 0) else "full"
                    room_data = self._splat_scene.analyze_room_architecture(
                        flip_y=flip_y,
                        scene_scale=scale_val,
                        interior_mode=int_mode,
                        extract_props=False,
                    )
                    self._last_room_data = room_data

            if not room_data:
                self.log("Could not obtain room architectural dimensions. Please build room architecture first.", "WARNING")
                return

            portal_tex_sel = self.combo_arch_portal_texture.currentText() if hasattr(self, 'combo_arch_portal_texture') else "Cropped"
            portal_tex_mode = "cropped" if "Cropped" in portal_tex_sel else ("full" if "Full" in portal_tex_sel else "none")
            portal_int = float(self.sld_arch_portal_intensity.value()) if hasattr(self, 'sld_arch_portal_intensity') else 1.0
            roughness = float(self.sld_arch_roughness.value()) if hasattr(self, 'sld_arch_roughness') else 0.85
            floor_mode = "visible" if (hasattr(self, 'combo_arch_floor_mode') and self.combo_arch_floor_mode.currentIndex() == 0) else "matte"

            res = lop_splat.assign_baked_hdri_and_portals(
                stage_node,
                hdri_path=hdri_path,
                room_data=room_data,
                portal_texture_mode=portal_tex_mode,
                portal_intensity_mult=portal_int,
                roughness=roughness,
                floor_mode=floor_mode,
            )

            # Update custom props with baked albedo texture
            try:
                from hdri_match_solaris import lop_custom_props
                props_data = self._get_custom_props_data() if hasattr(self, '_get_custom_props_data') else []
                if props_data:
                    solaris_probe = (float(room_data["center"][0]), float(room_data["floor_y"]) + 1.45, float(room_data["center"][2]))
                    _arch_target = "all"
                    if hasattr(self, 'combo_arch_renderer_target'):
                        _a_idx = self.combo_arch_renderer_target.currentIndex()
                        _arch_targets = ["all", "karma", "arnold", "redshift", "preview"]
                        if 0 <= _a_idx < len(_arch_targets):
                            _arch_target = _arch_targets[_a_idx]
                    lop_custom_props.setup_custom_props_nodes(
                        stage_node,
                        props_data,
                        probe_pos=solaris_probe,
                        floor_y=room_data["floor_y"],
                        hdri_texture=res.get("albedo_texture", hdri_path),
                        renderer_target=_arch_target,
                        mat_mode="pbr",
                        wire_into_stream=True,
                    )
            except Exception as ex_cp:
                print(f"[HDRI Match] Custom props texture assignment note: {ex_cp}")

            p_count = res.get("portals_textured", 0)
            albedo_file = os.path.basename(res.get("albedo_texture", hdri_path))
            msg = f"Assigned {albedo_file} to room surfaces and applied textures to {p_count} window portal lights."
            self.lbl_arch_status.setText(msg)
            self.lbl_arch_status.setStyleSheet("color: #3498db; font-weight: bold; font-size: 11px;")
            self.log(msg, "SUCCESS")
            if hou.isUIAvailable():
                hou.ui.displayMessage(
                    f"HDRI & Portal Textures Successfully Assigned:\n\n• Room Albedo: {res.get('albedo_texture')}\n• Portal Lights Textured: {p_count}",
                    title="Textures Assigned"
                )
        except Exception as e:
            self.log_error(f"Failed to assign room textures: {e}", e)
        finally:
            self.btn_assign_room_textures.setEnabled(True)
            self.btn_assign_room_textures.setText("🎨 Assign HDRI & Portal Textures")

    def _on_splat_file_dropped(self, path):
        if path and (path.lower().endswith(".ply") or path.lower().endswith(".splat")):
            self.txt_splat_file.setText(path)
            self._load_splat_scene(path)

    def _browse_splat_file(self):
        start_dir = hou.expandString("$HIP") if 'hou' in sys.modules and hasattr(hou, 'expandString') else os.path.expanduser("~")
        p, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Select 3D Gaussian Splat File", start_dir,
            "Gaussian Splat Files (*.ply *.splat);;All Files (*.*)"
        )
        if p:
            self.txt_splat_file.setText(p)
            self._load_splat_scene(p)

    def _load_splat_scene(self, path):
        """Parse Gaussian Splat header and show bounds/centroid."""
        try:
            from hdri_match_solaris.gaussian_splat import GaussianSplatScene
            self.log(f"Loading Gaussian Splat: {path}", "INFO")
            self._splat_scene = GaussianSplatScene.from_ply(path)
            cnt = self._splat_scene.count
            center = list(self._splat_scene.center)
            min_b = list(self._splat_scene.min_bound)
            max_b = list(self._splat_scene.max_bound)
            if hasattr(self, 'chk_splat_flip_y') and self.chk_splat_flip_y.isChecked():
                center[1] = -center[1]
                center[2] = -center[2]
                y_min = min(-min_b[1], -max_b[1])
                y_max = max(-min_b[1], -max_b[1])
                z_min = min(-min_b[2], -max_b[2])
                z_max = max(-min_b[2], -max_b[2])
                min_b[1], max_b[1] = y_min, y_max
                min_b[2], max_b[2] = z_min, z_max

            self.lbl_splat_info.setText(
                f"Splats: {cnt:,} | Interior Center: [{center[0]:.2f}, {center[1]:.2f}, {center[2]:.2f}] | Radius: {self._splat_scene.radius:.2f}m"
            )
            self.lbl_splat_info.setStyleSheet("color: #2ecc71; font-weight: bold; font-size: 11px;")
            
            # Adapt probe slider ranges to fit scene bounds
            pad = max(10.0, float(self._splat_scene.radius) * 0.2)
            self.sld_probe_x.setRange(float(min_b[0] - pad), float(max_b[0] + pad))
            self.sld_probe_y.setRange(float(min_b[1] - pad), float(max_b[1] + pad))
            self.sld_probe_z.setRange(float(min_b[2] - pad), float(max_b[2] + pad))

            self.sld_probe_x.setValue(float(center[0]))
            self.sld_probe_y.setValue(float(center[1]))
            self.sld_probe_z.setValue(float(center[2]))
            self._on_probe_slider_changed()
            self.log(f"Splat scene loaded: {cnt:,} points. Center set to ({center[0]:.2f}, {center[1]:.2f}, {center[2]:.2f})", "SUCCESS")
        except Exception as e:
            self.lbl_splat_info.setText(f"Load error: {e}")
            self.lbl_splat_info.setStyleSheet("color: #e74c3c; font-size: 11px;")
            self.log_error(f"Failed to load splat {path}", e)

    def _download_sample_splat_clicked(self):
        """Download official NVIDIA flowers_1.ply in background."""
        hip_dir = hou.expandString("$HIP") if 'hou' in sys.modules and hasattr(hou, 'expandString') else "."
        if not hip_dir or hip_dir == "." or "houdini_temp" in hip_dir:
            hip_dir = os.path.expanduser("~")
        dest_dir = os.path.join(hip_dir, "splats").replace(chr(92), "/")

        self.btn_download_splat.setEnabled(False)
        self.btn_download_splat.setText("Connecting...")
        self.log(f"Starting sample splat download to {dest_dir}...", "INFO")

        self._splat_download_worker = SplatDownloadWorker(dest_dir, self)
        self._splat_download_worker.progress.connect(self._on_splat_download_progress)
        self._splat_download_worker.finished.connect(self._on_splat_download_finished)
        self._splat_download_worker.failed.connect(self._on_splat_download_failed)
        self._splat_download_worker.start()

    def _on_splat_download_progress(self, percent, msg):
        self.btn_download_splat.setText(f"📥 {percent}% - {msg}")

    def _on_splat_download_finished(self, ply_path):
        self.btn_download_splat.setEnabled(True)
        self.btn_download_splat.setText("📥 Download Free Sample Splat (NVIDIA Flowers)")
        self.txt_splat_file.setText(ply_path)
        self._load_splat_scene(ply_path)
        self.log(f"Sample splat ready: {ply_path}", "SUCCESS")
        if hou.isUIAvailable():
            hou.ui.displayMessage(f"Sample Gaussian Splat downloaded and ready:\n{ply_path}", title="Splat Download Complete")

    def _on_splat_download_failed(self, error_msg):
        self.btn_download_splat.setEnabled(True)
        self.btn_download_splat.setText("📥 Download Free Sample Splat (Retry)")
        self.log_error("Sample splat download failed", error_msg)
        if hou.isUIAvailable():
            hou.ui.displayMessage(f"Failed to download sample splat:\n{error_msg}", severity=hou.severityType.Error)

    def _create_or_select_splat_probe(self):
        """Find or create hdri_match_splat_probe in /stage and select it in Houdini."""
        stage_node = self._get_stage_node()
        if not stage_node:
            return
        from hdri_match_solaris import lop_splat
        pos = (float(self.sld_probe_x.value()), float(self.sld_probe_y.value()), float(self.sld_probe_z.value()))
        probe = lop_splat.get_or_create_splat_probe(stage_node, initial_pos=pos)
        if probe:
            probe.setSelected(True, clear_all_selected=True)
            self.log(f"Selected Splat Probe in Solaris: {probe.path()}", "INFO")

    def _snap_probe_to_viewport_cam(self):
        """Snap probe coordinates to current viewport camera."""
        stage_node = self._get_stage_node()
        if not stage_node:
            return
        from hdri_match_solaris import lop_splat
        pos = lop_splat.snap_probe_to_active_viewport_camera(stage_node)
        if pos:
            self.sld_probe_x.setValue(pos[0])
            self.sld_probe_y.setValue(pos[1])
            self.sld_probe_z.setValue(pos[2])
            self.log(f"Splat Probe snapped to viewport camera at ({pos[0]:.2f}, {pos[1]:.2f}, {pos[2]:.2f})", "SUCCESS")

    def _snap_probe_to_splat_center(self):
        """Snap probe to the robust median interior centroid of the splat model."""
        if hasattr(self, '_splat_scene') and self._splat_scene and self._splat_scene.count > 0:
            c = list(self._splat_scene.center)
            if hasattr(self, 'chk_splat_flip_y') and self.chk_splat_flip_y.isChecked():
                c[1] = -c[1]
                c[2] = -c[2]
            self.sld_probe_x.setValue(float(c[0]))
            self.sld_probe_y.setValue(float(c[1]))
            self.sld_probe_z.setValue(float(c[2]))
            self._on_probe_slider_changed()
            self.log(f"Probe snapped to splat interior room center at ({c[0]:.2f}, {c[1]:.2f}, {c[2]:.2f})", "SUCCESS")
            stage_node = self._get_stage_node()
            if stage_node:
                from hdri_match_solaris import lop_splat
                lop_splat.focus_viewport_on_probe(stage_node, tuple(c))

    def _frame_probe_in_viewport(self):
        """Frame active Scene Viewer viewport camera on the probe position."""
        stage_node = self._get_stage_node()
        if stage_node:
            from hdri_match_solaris import lop_splat
            pos = (float(self.sld_probe_x.value()), float(self.sld_probe_y.value()), float(self.sld_probe_z.value()))
            lop_splat.focus_viewport_on_probe(stage_node, pos)
            self.log("Framed viewport camera on Splat Probe.", "INFO")

    def _on_splat_flip_y_toggled(self, checked):
        """Update probe ranges and center when orientation toggle changes."""
        if hasattr(self, '_splat_scene') and self._splat_scene and self._splat_scene.count > 0:
            center = list(self._splat_scene.center)
            min_b = list(self._splat_scene.min_bound)
            max_b = list(self._splat_scene.max_bound)
            if checked:
                center[1] = -center[1]
                center[2] = -center[2]
                y_min = min(-min_b[1], -max_b[1])
                y_max = max(-min_b[1], -max_b[1])
                z_min = min(-min_b[2], -max_b[2])
                z_max = max(-min_b[2], -max_b[2])
                min_b[1], max_b[1] = y_min, y_max
                min_b[2], max_b[2] = z_min, z_max

            pad = max(10.0, float(self._splat_scene.radius) * 0.2)
            self.sld_probe_x.setRange(float(min_b[0] - pad), float(max_b[0] + pad))
            self.sld_probe_y.setRange(float(min_b[1] - pad), float(max_b[1] + pad))
            self.sld_probe_z.setRange(float(min_b[2] - pad), float(max_b[2] + pad))
            self.lbl_splat_info.setText(
                f"Splats: {self._splat_scene.count:,} | Interior Center: [{center[0]:.2f}, {center[1]:.2f}, {center[2]:.2f}] | Radius: {self._splat_scene.radius:.2f}m"
            )
            self.sld_probe_x.setValue(float(center[0]))
            self.sld_probe_y.setValue(float(center[1]))
            self.sld_probe_z.setValue(float(center[2]))
            self._on_probe_slider_changed()

    def _on_probe_slider_changed(self, *args):
        """Update probe node position in stage as sliders move."""
        if getattr(self, '_restoring_state', False):
            return
        stage_node = self._get_stage_node()
        if stage_node:
            from hdri_match_solaris import lop_splat
            pos = (float(self.sld_probe_x.value()), float(self.sld_probe_y.value()), float(self.sld_probe_z.value()))
            lop_splat.set_probe_position(stage_node, pos)

    def _bake_splat_to_hdri(self):
        """Bake 360° equirectangular HDRI from Gaussian Splat at probe position."""
        ply_path = self.txt_splat_file.text().strip()
        if not ply_path or not os.path.isfile(ply_path):
            self.log_error("Please load or download a valid .ply Gaussian Splat file first.")
            if hou.isUIAvailable():
                hou.ui.displayMessage("Please load or download a valid .ply Gaussian Splat file first.", severity=hou.severityType.Warning)
            return

        res_text = self.combo_splat_res.currentText()
        if "8192" in res_text or "8k" in res_text.lower():
            width, height = 8192, 4096
        elif "4096" in res_text:
            width, height = 4096, 2048
        elif "1024" in res_text:
            width, height = 1024, 512
        else:
            width, height = 2048, 1024

        cam_pos = (float(self.sld_probe_x.value()), float(self.sld_probe_y.value()), float(self.sld_probe_z.value()))

        hip_dir = hou.expandString("$HIP") if 'hou' in sys.modules and hasattr(hou, 'expandString') else "."
        if not hip_dir or hip_dir == "." or "houdini_temp" in hip_dir:
            hip_dir = os.path.expanduser("~")
        out_dir = os.path.join(hip_dir, "hdri_match").replace(chr(92), "/")
        os.makedirs(out_dir, exist_ok=True)
        base_name = os.path.splitext(os.path.basename(ply_path))[0]
        out_path = os.path.join(out_dir, f"{base_name}_splat_baked_hdri.exr").replace(chr(92), "/")

        self.btn_bake_splat.setEnabled(False)
        self.btn_bake_splat.setText("🌐 Baking 360° HDRI (0%)...")
        if hasattr(self, 'pbar_splat'):
            self.pbar_splat.setValue(0)
            self.pbar_splat.setFormat("Baking 360° HDRI: 0%")
            self.pbar_splat.setVisible(True)
        self.log(f"Baking 360° HDRI ({width}x{height}) from {ply_path} at position {cam_pos}...", "INFO")

        params = {
            "ply_path": ply_path,
            "cam_pos": cam_pos,
            "width": width,
            "height": height,
            "out_path": out_path,
            "scene": self._splat_scene,
            "splat_scale": float(self.sld_splat_scale.value()) if hasattr(self, 'sld_splat_scale') else 1.8,
            "min_dist": float(self.sld_splat_min_dist.value()) if hasattr(self, 'sld_splat_min_dist') else 0.35,
            "flip_y": self.chk_splat_flip_y.isChecked() if hasattr(self, 'chk_splat_flip_y') else True,
            "inpaint_poles": self.chk_splat_inpaint_poles.isChecked() if hasattr(self, 'chk_splat_inpaint_poles') else True,
        }
        self._splat_bake_worker.request_bake(params)

    def _on_splat_bake_progress(self, pct):
        if hasattr(self, 'pbar_splat'):
            self.pbar_splat.setValue(pct)
            self.pbar_splat.setFormat(f"Baking 360° HDRI: {pct}%")
        self.btn_bake_splat.setText(f"🌐 Baking 360° HDRI ({pct}%)...")

    def _on_splat_bake_finished(self, out_path):
        self.btn_bake_splat.setEnabled(True)
        self.btn_bake_splat.setText("🌐 Bake 360° HDRI from Splat")
        if hasattr(self, 'pbar_splat'):
            self.pbar_splat.setValue(100)
            self.pbar_splat.setFormat("360° HDRI Baked Successfully (100%)")
        # Invalidate internal cached array so newly baked resolution (e.g. 8K) is loaded fresh
        self._hdri_full_array = None
        self._hdri_orig_full = None
        clean_path = os.path.normpath(out_path).replace("\\", "/")

        # Ensure ground/room projection is NOT enabled by this splat bake (splats have their own room architecture)
        if hasattr(self, 'grp_ground_proj'):
            self.grp_ground_proj.setChecked(False)

        # Bypass any legacy projection nodes in /stage so /environment is never created
        stage_node = self._get_stage_node()
        if stage_node:
            for p_name in ("hdri_match_projection", "hdri_match_materials"):
                pn = stage_node.node(p_name)
                if pn:
                    pn.bypass(True)

        # Update HDRI text field without firing accidental cascading projection triggers
        self.txt_hdri.blockSignals(True)
        self.txt_hdri.setText(clean_path)
        self.txt_hdri.blockSignals(False)

        self._load_hdri_preview(clean_path)
        self._sync_node()
        if 'hou' in sys.modules:
            try:
                hou.hscript("texcache -c")
                hou.hscript("glcache -c")
            except Exception:
                pass
        self.log(f"360° HDRI successfully baked from Gaussian Splat: {clean_path}", "SUCCESS")
        if hou.isUIAvailable():
            hou.ui.displayMessage(f"360° HDRI successfully baked and applied to Dome Light:\n{out_path}", title="Splat HDRI Baked")

    def _on_splat_bake_failed(self, error_msg):
        self.btn_bake_splat.setEnabled(True)
        self.btn_bake_splat.setText("🌐 Bake 360° HDRI from Splat")
        if hasattr(self, 'pbar_splat'):
            self.pbar_splat.setValue(0)
            self.pbar_splat.setFormat("Bake Failed")
        self.log_error("Splat bake failed", error_msg)
        if hou.isUIAvailable():
            hou.ui.displayMessage(f"Splat HDRI bake failed:\n{error_msg}", severity=hou.severityType.Error)

    def _extract_splat_3d_lights(self):
        """
        Analyze 3D Gaussian Splats, perform spatial clustering and PCA normal estimation,
        and instantiate native USD RectLights (or SphereLights) in /stage.
        """
        if not self._splat_scene:
            ply_path = self.txt_splat_file.text().strip()
            if ply_path and os.path.isfile(ply_path):
                from hdri_match_solaris.gaussian_splat import GaussianSplatScene
                self.log(f"Loading Gaussian Splat: {ply_path}", "INFO")
                self._splat_scene = GaussianSplatScene.from_ply(ply_path)
            else:
                self.log_error("Please load or select a valid Gaussian Splat (.ply) file first.")
                if hou.isUIAvailable():
                    hou.ui.displayMessage("Please load or select a valid Gaussian Splat (.ply) file first.", severity=hou.severityType.Warning)
                return

        stage_node = self._get_stage_node()
        if not stage_node:
            self.log_error("No active Solaris /stage node found.")
            return

        light_type_str = self.combo_splat_light_type.currentText() if hasattr(self, "combo_splat_light_type") else "Rectangular"
        light_type = "rect" if "Rectangular" in light_type_str else "sphere"

        luma_thresh = float(self.sld_splat_luma_thresh.value()) if hasattr(self, "sld_splat_luma_thresh") else 0.65
        cluster_dist = float(self.sld_splat_cluster_dist.value()) if hasattr(self, "sld_splat_cluster_dist") else 0.60
        min_points = int(self.sld_splat_min_points.value()) if hasattr(self, "sld_splat_min_points") else 12
        max_lights = int(self.sld_splat_max_lights.value()) if hasattr(self, "sld_splat_max_lights") else 6
        intensity_mult = float(self.sld_splat_intensity_mult.value()) if hasattr(self, "sld_splat_intensity_mult") else 1.0
        color_mult = float(self.sld_splat_color_mult.value()) if hasattr(self, "sld_splat_color_mult") else 1.0
        size_scale = float(self.sld_splat_size_scale.value()) if hasattr(self, "sld_splat_size_scale") else 1.0
        flip_direction = self.chk_splat_flip_direction.isChecked() if hasattr(self, "chk_splat_flip_direction") else False
        flip_y = self.chk_splat_flip_y.isChecked() if hasattr(self, "chk_splat_flip_y") else True
        create_debug_vis = self.chk_splat_debug_vis.isChecked() if hasattr(self, "chk_splat_debug_vis") else True

        self.log(f"Extracting 3D {light_type.upper()} lights from splat (thresh={luma_thresh}, cluster_dist={cluster_dist}m, max={max_lights})...", "INFO")

        try:
            from hdri_match_solaris import lop_splat

            clusters = self._splat_scene.extract_rectangular_lights(
                luma_thresh=luma_thresh,
                cluster_dist=cluster_dist,
                min_points=min_points,
                max_lights=max_lights,
                intensity_mult=intensity_mult,
                color_mult=color_mult,
                size_scale=size_scale,
                flip_direction=flip_direction,
                flip_y=flip_y,
            )

            if not clusters:
                self.log("No distinct high-radiance light clusters found in splat. Try lowering the Brightness Threshold.", "WARNING")
                if hou.isUIAvailable():
                    hou.ui.displayMessage("No distinct light clusters found.\nTry lowering the Brightness Threshold or increasing Cluster Distance.", severity=hou.severityType.Warning)
                return

            spawned_nodes = lop_splat.spawn_3d_splat_lights(
                stage_node,
                clusters,
                light_type=light_type,
                prim_path_prefix="/lights/splats",
                clear_existing=True,
                create_debug_vis=create_debug_vis,
            )

            # Automatically bake & load full splat scene point cloud into Solaris if requested
            if hasattr(self, 'chk_splat_scene_vis') and self.chk_splat_scene_vis.isChecked():
                self._ensure_splat_usd_loaded(visible=True)

            summary_lines = []
            for c in clusters:
                pos_str = f"({c.centroid[0]:.2f}, {c.centroid[1]:.2f}, {c.centroid[2]:.2f})"
                dim_str = f"{c.width:.2f}m x {c.height:.2f}m"
                temp_str = f"{int(c.temperature)}K" if c.temperature else "N/A"
                summary_lines.append(f"• Light {c.cluster_id:02d}: Pos {pos_str} | Size {dim_str} | Color {temp_str} | Int {c.intensity:.1f} | Conf {int(c.confidence*100)}%")

            self.log(f"Extracted {len(spawned_nodes)} native USD {light_type.capitalize()}Lights in Solaris stage:\n" + "\n".join(summary_lines), "SUCCESS")
            if hou.isUIAvailable():
                hou.ui.displayMessage(
                    f"Successfully reconstructed {len(spawned_nodes)} native USD lights in /stage!\n\n" +
                    "\n".join(summary_lines) +
                    ("\n\nInteractive 3D debug visualization geometry created at /debug/splat_lights_vis." if create_debug_vis else ""),
                    title="USD Lights Reconstructed"
                )

        except Exception as e:
            self.log_error(f"Failed to extract 3D lights from Gaussian Splat", e)
            if hou.isUIAvailable():
                hou.ui.displayMessage(f"Light extraction failed:\n{e}", severity=hou.severityType.Error)

    def _clear_splat_lights_clicked(self):
        """Remove all splat lights and debug visualization from /stage."""
        stage_node = self._get_stage_node()
        if not stage_node:
            return
        from hdri_match_solaris import lop_splat
        removed = lop_splat.clear_splat_lights(stage_node)
        self.log(f"Cleared {removed} splat light and debug nodes from /stage.", "INFO")

    def _on_splat_scene_vis_toggled(self, checked):
        """Called when 'Auto-Load Splat Cloud in Viewport' checkbox is toggled."""
        self._ensure_splat_usd_loaded(visible=checked)

    def _ensure_splat_usd_loaded(self, visible=True, force_bake=False):
        """
        Bake (if needed or forced) and load/show or hide the USD point cloud in /stage.
        Returns the reference node or None.
        """
        stage_node = self._get_stage_node()
        if not stage_node:
            self.log_error("No active Solaris /stage node found.")
            return None

        import importlib
        try:
            import hdri_match_solaris.lop_splat as lop_splat
            importlib.reload(lop_splat)
        except Exception:
            from hdri_match_solaris import lop_splat

        # If hiding
        if not visible:
            ref_node = stage_node.node("splat_scene_cloud")
            if ref_node:
                ref_node.bypass(True)
                self.log("Splat Scene Point Cloud is now HIDDEN in viewport.", "INFO")
                if hasattr(self, 'btn_vis_splat_scene'):
                    self.btn_vis_splat_scene.setText("👁️ Show Splat Cloud")
                    self.btn_vis_splat_scene.setStyleSheet(
                        "QPushButton { background-color: #2980b9; color: white; font-weight: bold; padding: 7px; border-radius: 4px; }"
                        "QPushButton:hover { background-color: #3498db; }"
                    )
            return ref_node

        # Visible requested: Ensure USD file exists or bake it
        ply_path = self.txt_splat_file.text().strip()
        if not ply_path or not os.path.isfile(ply_path):
            self.log_error("Please select a valid Gaussian Splat (.ply) file first.")
            if hou.isUIAvailable():
                hou.ui.displayMessage("Please select or load a valid Gaussian Splat (.ply) file first.", severity=hou.severityType.Warning)
            return None

        try:
            hip_dir = hou.expandString("$HIP") if 'hou' in sys.modules and hasattr(hou, 'expandString') else "."
            if not hip_dir or hip_dir == "." or "houdini_temp" in hip_dir:
                hip_dir = os.path.expanduser("~")
            scenes_dir = os.path.join(hip_dir, "scenes").replace("\\", "/")
            os.makedirs(scenes_dir, exist_ok=True)

            base = os.path.splitext(os.path.basename(ply_path))[0]
            if base.lower() == "scene":
                parent_name = os.path.basename(os.path.dirname(ply_path))
                if parent_name:
                    base = parent_name
            # Strictly sanitize filename: NO SPACES (spaces break USD LOP file pattern parsing)
            safe_base = "".join([c if c.isalnum() or c in "._-" else "_" for c in base]).strip().replace(" ", "_")
            while "__" in safe_base:
                safe_base = safe_base.replace("__", "_")
            usd_path = os.path.join(scenes_dir, f"{safe_base}_splats_pointcloud.usd").replace("\\", "/")

            if not os.path.isfile(usd_path) or force_bake:
                self.log(f"Baking native USD point cloud from {ply_path}...", "INFO")
                if not self._splat_scene or getattr(self._splat_scene, 'filepath', None) != ply_path:
                    from hdri_match_solaris.gaussian_splat import GaussianSplatScene
                    self._splat_scene = GaussianSplatScene.from_ply(ply_path)

                flip_y = self.chk_splat_flip_y.isChecked() if hasattr(self, 'chk_splat_flip_y') else True
                lop_splat.export_splat_point_cloud_usd(self._splat_scene, usd_path, max_points=300000, flip_y=flip_y)
                self.log(f"Successfully baked USD point cloud: {usd_path}", "SUCCESS")

            ref_node = lop_splat.set_splat_scene_vis(stage_node, visible=True, usd_path=usd_path)
            if ref_node:
                try:
                    ref_node.cook(force=True)
                except Exception:
                    pass
            self.log(f"Splat Scene Point Cloud loaded in Solaris viewport: {usd_path}", "SUCCESS")
            if hasattr(self, 'btn_vis_splat_scene'):
                self.btn_vis_splat_scene.setText("👁️ Hide Splat Cloud")
                self.btn_vis_splat_scene.setStyleSheet(
                    "QPushButton { background-color: #27ae60; color: white; font-weight: bold; padding: 7px; border-radius: 4px; }"
                    "QPushButton:hover { background-color: #2ecc71; }"
                )
            if hasattr(self, 'chk_splat_scene_vis') and not self.chk_splat_scene_vis.isChecked():
                self.chk_splat_scene_vis.blockSignals(True)
                self.chk_splat_scene_vis.setChecked(True)
                self.chk_splat_scene_vis.blockSignals(False)

            if hou.isUIAvailable():
                hou.ui.setStatusMessage(f"Splat Cloud active in Solaris: {os.path.basename(usd_path)}", severity=hou.severityType.Message)

            return ref_node

        except Exception as e:
            self.log_error(f"Failed to bake or load Splat USD point cloud", e)
            if hou.isUIAvailable():
                hou.ui.displayMessage(f"Failed to load Splat Point Cloud:\n{e}", severity=hou.severityType.Error)
            return None

    def _toggle_splat_scene_vis(self):
        """Toggle splat point cloud visibility in /stage."""
        stage_node = self._get_stage_node()
        if not stage_node:
            self.log_error("Cannot toggle splat cloud: /stage node not found.")
            return

        ply_path = self.txt_splat_file.text().strip()
        if not ply_path or not os.path.isfile(ply_path):
            self.log_error("Please select a valid Gaussian Splat (.ply) file first.")
            if hou.isUIAvailable():
                hou.ui.displayMessage("Please select or load a valid Gaussian Splat (.ply) file first.", severity=hou.severityType.Warning)
            return

        ref_node = stage_node.node("splat_scene_cloud")
        if ref_node:
            is_bypassed = ref_node.isBypassed()
            self._ensure_splat_usd_loaded(visible=is_bypassed)
        else:
            self._ensure_splat_usd_loaded(visible=True)

    def _bake_splat_usd_clicked(self):
        """Explicitly bake or re-bake the USD point cloud."""
        ply_path = self.txt_splat_file.text().strip()
        if not ply_path or not os.path.isfile(ply_path):
            self.log_error("Please select a valid Gaussian Splat (.ply) file first.")
            if hou.isUIAvailable():
                hou.ui.displayMessage("Please select or load a valid Gaussian Splat (.ply) file first.", severity=hou.severityType.Warning)
            return

        ref_node = self._ensure_splat_usd_loaded(visible=True, force_bake=True)
        if ref_node and hou.isUIAvailable():
            usd_path = ref_node.parm("filepath1").eval() if ref_node.parm("filepath1") else ""
            hou.ui.displayMessage(
                f"Successfully baked 3D Gaussian Splat scene into USD point cloud!\n\n"
                f"USD File:\n{usd_path}\n\n"
                f"Referenced into Solaris /stage as 'splat_scene_cloud'.",
                title="Splat Point Cloud Baked"
            )

    def _display_splats_bakegs_clicked(self):
        """
        Display Gaussian Splats using Houdini's native Bake GSplats reader (bakegsplat SOP).
        Creates /obj/native_splats_reader and connects to Solaris /stage/splat_scene_bakegs.
        """
        ply_path = self.txt_splat_file.text().strip()
        if not ply_path or not os.path.isfile(ply_path):
            self.log_error("Please select a valid Gaussian Splat (.ply) file first.")
            if hou.isUIAvailable():
                hou.ui.displayMessage("Please select or load a valid Gaussian Splat (.ply) file first.", severity=hou.severityType.Warning)
            return

        stage_node = self._get_stage_node()
        if not stage_node:
            self.log_error("No active Solaris /stage node found.")
            return

        # Check if bakegsplat is available in this Houdini session (Houdini 21+)
        has_bakegs = "bakegsplat" in hou.sopNodeTypeCategory().nodeTypes()
        if not has_bakegs:
            msg = (
                "Houdini native 'Bake GSplats' (bakegsplat SOP) is available in Houdini 21+.\n\n"
                "Loading native USD Point Cloud into Solaris /stage instead."
            )
            self.log(msg, "WARNING")
            if hou.isUIAvailable():
                hou.ui.displayMessage(msg, severity=hou.severityType.Warning)
            self._ensure_splat_usd_loaded(visible=True)
            return

        try:
            obj_net = hou.node("/obj")
            if not obj_net:
                self.log_error("Cannot find /obj network.")
                return

            geo_node = obj_net.node("native_splats_reader")

            def _configure_splat_sop_nodes(gnode):
                """Helper to configure bake_gs and scale_to_scene with exact studio settings."""
                b_sop = gnode.node("bake_gs")
                if b_sop:
                    if b_sop.parm("linearize"):
                        b_sop.parm("linearize").set(1)
                    if b_sop.parm("gsplat"):
                        b_sop.parm("gsplat").set(1)
                    if b_sop.parm("sphcoeff"):
                        b_sop.parm("sphcoeff").set(0)
                    if b_sop.parm("deleteattrib"):
                        b_sop.parm("deleteattrib").set(1)
                    if b_sop.parm("noshadowcast"):
                        b_sop.parm("noshadowcast").set(1)

                x_sop = gnode.node("scale_to_scene")
                if x_sop:
                    if x_sop.parmTuple("r"):
                        x_sop.parmTuple("r").set((-180.0, 0.0, 0.0))
                    else:
                        if x_sop.parm("rx"):
                            x_sop.parm("rx").set(-180.0)
                        if x_sop.parm("ry"):
                            x_sop.parm("ry").set(0.0)
                        if x_sop.parm("rz"):
                            x_sop.parm("rz").set(0.0)
                    if x_sop.parmTuple("t"):
                        x_sop.parmTuple("t").set((0.0, 0.0, 0.0))
                    if x_sop.parmTuple("s"):
                        x_sop.parmTuple("s").set((1.0, 1.0, 1.0))
                    scale_val = 1.0
                    if hasattr(self, 'sld_arch_scale') and self.sld_arch_scale.value() > 0:
                        scale_val = self.sld_arch_scale.value()
                    if x_sop.parm("scale"):
                        x_sop.parm("scale").set(scale_val)

            existing_lop = stage_node.node("splat_scene_bakegs")
            if geo_node:
                _configure_splat_sop_nodes(geo_node)

            if existing_lop:
                btn_txt = getattr(self, 'btn_display_bakegs', None).text() if hasattr(self, 'btn_display_bakegs') else ""
                if "Hide" in btn_txt:
                    existing_lop.bypass(True)
                    is_visible = False
                else:
                    existing_lop.bypass(False)
                    is_visible = True
                    try:
                        existing_lop.cook(force=True)
                    except Exception:
                        pass

                from hdri_match_solaris import lop_splat
                lop_splat.wire_solaris_stage_stream(stage_node)

                self.log(f"Native Splats (BakeGS) {'SHOWN' if is_visible else 'HIDDEN'} in viewport.", "INFO")
                if hasattr(self, 'btn_display_bakegs'):
                    if is_visible:
                        self.btn_display_bakegs.setText("🔮 Hide Native Splats (BakeGS)")
                        self.btn_display_bakegs.setStyleSheet(
                            "QPushButton { background-color: #27ae60; color: white; font-weight: bold; padding: 7px; border-radius: 4px; }"
                            "QPushButton:hover { background-color: #2ecc71; }"
                        )
                    else:
                        self.btn_display_bakegs.setText("🔮 Display Native Splats (BakeGS)")
                        self.btn_display_bakegs.setStyleSheet(
                            "QPushButton { background-color: #8e44ad; color: white; font-weight: bold; padding: 7px; border-radius: 4px; }"
                            "QPushButton:hover { background-color: #9b59b6; }"
                        )
                return

            self.log(f"Setting up native Houdini 21 Bake GSplats reader for: {os.path.basename(ply_path)}...", "INFO")
            if not geo_node:
                geo_node = obj_net.createNode("geo", "native_splats_reader")
                geo_node.setColor(hou.Color((0.55, 0.45, 0.85)))

            # Clear or update SOP nodes inside geo_node
            file_sop = geo_node.node("ply_source")
            if not file_sop:
                file_sop = geo_node.createNode("file", "ply_source")
            file_sop.parm("file").set(ply_path.replace("\\", "/"))

            bake_sop = geo_node.node("bake_gs")
            if not bake_sop:
                bake_sop = geo_node.createNode("bakegsplat", "bake_gs")
            bake_sop.setInput(0, file_sop)

            xform_sop = geo_node.node("scale_to_scene")
            if not xform_sop:
                xform_sop = geo_node.createNode("xform", "scale_to_scene")
            xform_sop.setInput(0, bake_sop)

            _configure_splat_sop_nodes(geo_node)

            out_sop = geo_node.node("OUT_splats")
            if not out_sop:
                out_sop = geo_node.createNode("null", "OUT_splats")
            out_sop.setInput(0, xform_sop)
            out_sop.setDisplayFlag(True)
            out_sop.setRenderFlag(True)
            geo_node.layoutChildren()

            # Import into Solaris /stage
            sop_imp = stage_node.node("splat_scene_bakegs")
            if not sop_imp:
                sop_imp = stage_node.createNode("sopimport", "splat_scene_bakegs")
                sop_imp.setColor(hou.Color((0.55, 0.45, 0.85)))
            sop_imp.parm("soppath").set(out_sop.path())
            if sop_imp.parm("primpath"):
                sop_imp.parm("primpath").set("/stage/splat_scene_bakegs")
            if sop_imp.parm("importstyle"):
                sop_imp.parm("importstyle").set("flatten")
            sop_imp.bypass(False)
            try:
                sop_imp.cook(force=True)
            except Exception:
                pass

            from hdri_match_solaris import lop_splat
            lop_splat.wire_solaris_stage_stream(stage_node)

            self.log(f"Native Splats (BakeGS) loaded in Solaris viewport & /stage: {sop_imp.path()}", "SUCCESS")
            if hasattr(self, 'btn_display_bakegs'):
                self.btn_display_bakegs.setText("🔮 Hide Native Splats (BakeGS)")
                self.btn_display_bakegs.setStyleSheet(
                    "QPushButton { background-color: #27ae60; color: white; font-weight: bold; padding: 7px; border-radius: 4px; }"
                    "QPushButton:hover { background-color: #2ecc71; }"
                )
            if hou.isUIAvailable():
                hou.ui.setStatusMessage("Native Gaussian Splats active in Solaris viewport & Karma XPU.", severity=hou.severityType.Message)

        except Exception as e:
            self.log_error("Failed to display native Gaussian Splats with bakegsplat", e)
            if hou.isUIAvailable():
                hou.ui.displayMessage(f"Failed to load native splats:\n{e}", severity=hou.severityType.Error)


    def _build_calibration_section(self):
        """Calibration parameter section."""
        sec = CollapsibleSection("Calibration", collapsed=False)
        self.grp_cal = sec
        self._collapsible_sections.append(sec)
        lay = QtWidgets.QFormLayout(sec.content_widget)
        lay.setContentsMargins(8, 10, 8, 8)

        self.chk_auto_cal = QtWidgets.QCheckBox("Auto-Calibrate from Plate")
        self.chk_auto_cal.setChecked(True)
        lay.addRow(self.chk_auto_cal)

        self.chk_protect_sun = QtWidgets.QCheckBox("Protect Sun")
        self.chk_protect_sun.setChecked(True)
        lay.addRow(self.chk_protect_sun)

        self.combo_sky_mode = QtWidgets.QComboBox()
        self.combo_sky_mode.addItem("Sky Only (Top 40%)", "top_40")
        self.combo_sky_mode.addItem("Entire Frame (Full Image)", "off")
        self.combo_sky_mode.setToolTip(
            "Controls the image region used to compute exposure and chromaticity balance:\n"
            "• Sky Only (Top 40%): Samples the ambient sky dome (recommended for daytime exterior plates).\n"
            "• Entire Frame: Samples all pixels including ground, buildings, and foliage (recommended for overcast, interior, or ground-dominated plates)."
        )
        lay.addRow("Sky Mode:", self.combo_sky_mode)

        self.sld_ev = SliderDoubleSpinBox(-10.0, 10.0, 0.1, 0.0, decimals=2)
        lay.addRow("EV Offset:", self.sld_ev)

        self.sld_black = SliderDoubleSpinBox(-0.5, 0.5, 0.001, 0.0, decimals=4)
        lay.addRow("Black Offset:", self.sld_black)

        self.sld_temp = SliderDoubleSpinBox(-1.0, 1.0, 0.01, 0.0, decimals=3)
        lay.addRow("Temperature:", self.sld_temp)

        self.sld_tint = SliderDoubleSpinBox(-1.0, 1.0, 0.01, 0.0, decimals=3)
        lay.addRow("Tint:", self.sld_tint)

        self.sld_yaw = SliderDoubleSpinBox(0.0, 360.0, 1.0, 0.0, decimals=1)
        lay.addRow("Yaw (deg):", self.sld_yaw)

        self.sld_sat = SliderDoubleSpinBox(0.0, 3.0, 0.01, 1.0, decimals=2)
        self.sld_sat.setToolTip("Scene-linear chroma saturation scale (1.0 = neutral, 0.0 = monochrome).")
        lay.addRow("Saturation:", self.sld_sat)

        self.sld_contrast = SliderDoubleSpinBox(0.2, 2.5, 0.01, 1.0, decimals=2)
        self.sld_contrast.setToolTip("Scene-linear contrast power pivot around 0.18 middle-grey.")
        lay.addRow("Contrast:", self.sld_contrast)

        self.btn_reset_cal = QtWidgets.QPushButton("↺ Reset Calibration to Original")
        self.btn_reset_cal.setToolTip("Reset EV, Black, Temp, and Tint to 0.0 and revert HDRI dome light back to original state")
        self.btn_reset_cal.setStyleSheet(
            "QPushButton { font-weight: bold; background-color: #333333; color: #e0e0e0; padding: 5px; border-radius: 4px; }"
            "QPushButton:hover { background-color: #444444; color: #ffffff; }"
        )
        self.btn_reset_cal.clicked.connect(lambda: self.reset_calibration(revert_reason="User clicked Reset Calibration"))
        lay.addRow(self.btn_reset_cal)

        self._scroll_layout.addWidget(sec)

    def _build_horizon_section(self):
        """Horizon split controls with sliders and full color tint controls for Sky and Ground."""
        sec = CollapsibleSection("Horizon Split", checkable=True, checked=False, collapsed=True)
        self.grp_horizon = sec
        self._collapsible_sections.append(sec)
        lay = QtWidgets.QFormLayout(sec.content_widget)
        lay.setContentsMargins(8, 10, 8, 8)

        self.sld_horizon_height = SliderDoubleSpinBox(0.0, 1.0, 0.01, 0.5, decimals=2)
        lay.addRow("Height:", self.sld_horizon_height)

        self.sld_horizon_feather = SliderDoubleSpinBox(0.0, 1.0, 0.01, 0.1, decimals=2)
        lay.addRow("Feather:", self.sld_horizon_feather)

        self.sld_sky_ev = SliderDoubleSpinBox(-10.0, 10.0, 0.1, 0.0, decimals=2)
        lay.addRow("Sky EV:", self.sld_sky_ev)

        self.col_sky = ColorPickerWidget(default_color=(1.0, 1.0, 1.0), label="Sky")
        lay.addRow("Sky Color:", self.col_sky)

        self.sld_ground_ev = SliderDoubleSpinBox(-10.0, 10.0, 0.1, 0.0, decimals=2)
        lay.addRow("Ground EV:", self.sld_ground_ev)

        self.col_ground = ColorPickerWidget(default_color=(1.0, 1.0, 1.0), label="Ground")
        lay.addRow("Ground Color:", self.col_ground)

        self._scroll_layout.addWidget(sec)

    def _build_softclip_section(self):
        """Soft-clip controls."""
        sec = CollapsibleSection("Highlight Compression (Soft-Clip)", checkable=True, checked=False, collapsed=True)
        self.grp_softclip = sec
        self._collapsible_sections.append(sec)
        lay = QtWidgets.QFormLayout(sec.content_widget)
        lay.setContentsMargins(8, 10, 8, 8)

        self.sld_softclip_thresh = SliderDoubleSpinBox(0.0, 20.0, 0.5, 5.0, decimals=1)
        lay.addRow("Threshold (EV):", self.sld_softclip_thresh)

        self.sld_softclip_rolloff = SliderDoubleSpinBox(0.0, 20.0, 0.5, 2.0, decimals=1)
        lay.addRow("Rolloff (EV):", self.sld_softclip_rolloff)

        self._scroll_layout.addWidget(sec)

    def _build_sun_section(self):
        """Sun detection and relighting controls."""
        sec = CollapsibleSection("Sun Relighting", checkable=True, checked=False, collapsed=True)
        self.grp_sun = sec
        self._collapsible_sections.append(sec)
        lay = QtWidgets.QFormLayout(sec.content_widget)
        lay.setContentsMargins(8, 10, 8, 8)

        self.chk_auto_detect_sun = QtWidgets.QCheckBox("Auto-Detect Sun")
        self.chk_auto_detect_sun.setChecked(True)
        lay.addRow(self.chk_auto_detect_sun)

        self.chk_remove_sun = QtWidgets.QCheckBox("Remove Sun from Dome Map (Sky Infill)")
        self.chk_remove_sun.setChecked(True)
        self.chk_remove_sun.setToolTip(
            "Inpaints/cleans the bright sun disk from the HDRI dome map using surrounding sky color,\n"
            "ensuring the Distant Light is the sole source of direct sunlight and eliminating double shadows."
        )
        lay.addRow(self.chk_remove_sun)

        # Physical Sun Clipping Reconstruction & Atmospheric Extinction
        self.chk_sun_reconstruct = QtWidgets.QCheckBox("☀️ Physical Sun Clipping Reconstruction")
        self.chk_sun_reconstruct.setChecked(True)
        self.chk_sun_reconstruct.setToolTip(
            "Detects if the sun was clipped during on-set HDRI bracket exposure capture.\n"
            "Restores the true physical direct solar illuminance and angular penumbra size\n"
            "based on solar elevation angle and atmospheric air mass."
        )
        lay.addRow(self.chk_sun_reconstruct)

        self.sld_sun_turbidity = SliderDoubleSpinBox(1.5, 6.0, step=0.1, default_val=3.0, decimals=1)
        self.sld_sun_turbidity.setToolTip(
            "Atmospheric turbidity factor (aerosol optical depth):\n"
            "• 1.5 - 2.0: Clean clear mountain air\n"
            "• 2.5 - 3.5: Standard rural / suburban clear sky\n"
            "• 4.0 - 6.0: Hazy, humid, or urban smog"
        )
        lay.addRow("Air Turbidity:", self.sld_sun_turbidity)

        self.sld_sun_reconstruct_blend = SliderDoubleSpinBox(0.0, 100.0, step=5.0, default_val=100.0, decimals=0)
        self.sld_sun_reconstruct_blend.setToolTip(
            "Blend between measured HDRI flux (0%) and 100% physical reconstructed solar flux."
        )
        lay.addRow("Reconstruct Blend (%):", self.sld_sun_reconstruct_blend)

        self.cmb_sun_radiometry_mode = QtWidgets.QComboBox()
        self.cmb_sun_radiometry_mode.addItems(["Relative (VFX / Dome Balanced)", "Absolute Photometric (Lux)"])
        self.cmb_sun_radiometry_mode.setToolTip(
            "Radiometry Scaling Mode:\n"
            "• Relative (VFX / Dome Balanced) [Default]: Scales direct sun intensity relative to the HDRI's\n"
            "  diffuse sky illuminance using the physical Sun-to-Sky ratio (typically 4:1 to 8:1).\n"
            "  Maintains natural daylight shadow contrast without blowing out 0 EV camera exposures.\n"
            "• Absolute Photometric (Lux): Computes real-world terrestrial solar flux (~80,000 lux).\n"
            "  Requires physical camera exposure compensation (EV -14 to -16) or scaled dome light."
        )
        lay.addRow("Radiometry Mode:", self.cmb_sun_radiometry_mode)

        self.lbl_sun_clipping_status = QtWidgets.QLabel("Clipping: Not Analyzed")
        self.lbl_sun_clipping_status.setStyleSheet("color: #aaaaaa; font-weight: bold;")
        lay.addRow("Clipping Status:", self.lbl_sun_clipping_status)

        self.lbl_sun_physical_flux = QtWidgets.QLabel("Measured: — | Expected: —")
        self.lbl_sun_physical_flux.setStyleSheet("color: #888888; font-size: 11px;")
        lay.addRow("Radiometry:", self.lbl_sun_physical_flux)

        self.sld_sun_u = SliderDoubleSpinBox(0.0, 1.0, step=0.01, default_val=0.5, decimals=2)
        lay.addRow("Target U:", self.sld_sun_u)

        self.sld_sun_v = SliderDoubleSpinBox(0.0, 1.0, step=0.01, default_val=0.25, decimals=2)
        lay.addRow("Target V:", self.sld_sun_v)

        self.sld_sun_radius = SliderDoubleSpinBox(0.001, 0.2, step=0.005, default_val=0.03, decimals=3)
        lay.addRow("Radius:", self.sld_sun_radius)

        self.sld_sun_angle = SliderDoubleSpinBox(0.1, 10.0, step=0.1, default_val=0.53, decimals=2)
        lay.addRow("Angular Size:", self.sld_sun_angle)

        # Physical Radiometry: Intensity & Exposure
        self.sld_sun_intensity = SliderDoubleSpinBox(0.0, 100.0, step=0.1, default_val=1.0, decimals=2)
        self.sld_sun_intensity.setToolTip("Direct sunlight illuminance in scene-linear units (lux) calculated from net sun disk flux.")
        lay.addRow("Sun Intensity:", self.sld_sun_intensity)

        self.sld_sun_exposure = SliderDoubleSpinBox(-10.0, 10.0, step=0.1, default_val=0.0, decimals=2)
        self.sld_sun_exposure.setToolTip("Sun exposure offset in EV stops.")
        lay.addRow("Sun Exposure (EV):", self.sld_sun_exposure)

        # Color Temperature (CCT)
        self.chk_sun_use_cct = QtWidgets.QCheckBox("Use Color Temperature")
        self.chk_sun_use_cct.setChecked(True)
        lay.addRow(self.chk_sun_use_cct)

        self.sld_sun_cct = SliderDoubleSpinBox(1500.0, 25000.0, step=50.0, default_val=5500.0, decimals=0)
        self.sld_sun_cct.setToolTip("Correlated Color Temperature in Kelvin derived from the sun disk chromaticity.")
        lay.addRow("Color Temp (K):", self.sld_sun_cct)

        self.lbl_sun_cct_desc = QtWidgets.QLabel("Daylight (Direct Sun)")
        self.lbl_sun_cct_desc.setStyleSheet("color: #00c8ff; font-style: italic;")
        lay.addRow("CCT Class:", self.lbl_sun_cct_desc)

        self.btn_extract_sun = QtWidgets.QPushButton("☀️ Extract Sun Values from HDRI")
        self.btn_extract_sun.setStyleSheet("padding: 5px; font-weight: bold;")
        self.btn_extract_sun.setToolTip(
            "Samples the sun disk in the HDRI to calculate physical Correlated Color Temperature (Kelvin)\n"
            "and integrated direct net flux (intensity), automatically populating these controls."
        )
        lay.addRow(self.btn_extract_sun)

        self._scroll_layout.addWidget(sec)

    def _build_ground_projection_section(self):
        """Ground projection, room box, and local IBL parallax controls."""
        sec = CollapsibleSection("Ground & Room Projection / Parallax (Local IBL)", checkable=True, checked=False, collapsed=True)
        self.grp_ground_proj = sec
        self._collapsible_sections.append(sec)
        lay = QtWidgets.QFormLayout(sec.content_widget)
        lay.setContentsMargins(8, 10, 8, 8)

        # Mode selector
        self.combo_proj_mode = QtWidgets.QComboBox()
        self.combo_proj_mode.addItems(["Ground Plane (Disc)", "Interior Room Box (Cube)"])
        self.combo_proj_mode.setToolTip(
            "Select projection geometry:\n"
            "• Ground Plane (Disc): Infinite/finite ground disc for outdoor environments.\n"
            "• Interior Room Box (Cube): 6-sided perspective-correct room box for interior environments."
        )
        lay.addRow("Projection Mode:", self.combo_proj_mode)

        # Common Tripod Height
        self.sld_tripod_height = SliderDoubleSpinBox(0.1, 50.0, 0.1, 1.5, decimals=2)
        self.sld_tripod_height.setToolTip("Height of camera nodal point above the ground/floor plane in meters.")
        lay.addRow("Tripod Height (m):", self.sld_tripod_height)

        # ------------------------------------------------------------------
        # Disc Controls
        # ------------------------------------------------------------------
        self.widget_disc_controls = QtWidgets.QWidget()
        disc_lay = QtWidgets.QFormLayout(self.widget_disc_controls)
        disc_lay.setContentsMargins(0, 0, 0, 0)

        self.sld_ground_radius = SliderDoubleSpinBox(1.0, 200.0, 1.0, 10.0, decimals=1)
        self.sld_ground_radius.setToolTip("Radius of the finite local ground plane in meters.")
        disc_lay.addRow("Ground Radius (m):", self.sld_ground_radius)

        self.sld_ground_feather = SliderDoubleSpinBox(0.01, 1.0, 0.01, 0.15, decimals=2)
        self.sld_ground_feather.setToolTip("Edge feather blend fraction into the distant horizon.")
        disc_lay.addRow("Feather:", self.sld_ground_feather)

        self.combo_disc_proj_method = QtWidgets.QComboBox()
        self.combo_disc_proj_method.addItems([
            "Top View (Planar Rectilinear) [Recommended]",
            "Spherical (Camera Radial)",
        ])
        self.combo_disc_proj_method.setToolTip(
            "• Top View (Planar Rectilinear): High-detail rectilinear top-down camera projection.\n"
            "  Eliminates all radial spoke distortion, blur, and stretching.\n"
            "• Spherical: Direct 360° equirectangular mapping (radial camera rays)."
        )
        disc_lay.addRow("Projection Method:", self.combo_disc_proj_method)

        self.sld_ground_roughness = SliderDoubleSpinBox(0.0, 1.0, 0.05, 1.0, decimals=2)
        self.sld_ground_roughness.setToolTip(
            "Surface roughness of the ground projection mesh:\n"
            "• 1.00 [Default]: 100% matte / diffuse (zero specular reflection for Arnold, Redshift, Karma).\n"
            "• < 1.00: Adds specular reflection (e.g. wet tarmac, polished floor)."
        )
        disc_lay.addRow("Ground Roughness:", self.sld_ground_roughness)

        self.chk_bake_ground_warp = QtWidgets.QCheckBox("Bake Ground Warp into Dome EXR")
        self.chk_bake_ground_warp.setChecked(True)
        self.chk_bake_ground_warp.setToolTip(
            "Directly warps the equirectangular panorama with ground plane perspective,\n"
            "allowing standard Dome Lights in Karma, Arnold, or Redshift to render ground parallax without extra scene geometry."
        )
        disc_lay.addRow(self.chk_bake_ground_warp)

        lay.addRow(self.widget_disc_controls)

        # ------------------------------------------------------------------
        # Room Box Controls
        # ------------------------------------------------------------------
        self.widget_room_controls = QtWidgets.QWidget()
        room_lay = QtWidgets.QFormLayout(self.widget_room_controls)
        room_lay.setContentsMargins(0, 0, 0, 0)

        # Room dimension presets
        room_pre_box = QtWidgets.QHBoxLayout()
        room_pre_box.setSpacing(4)
        btn_rm_studio = QtWidgets.QPushButton("Studio (12×15m)")
        btn_rm_studio.setStyleSheet("padding: 2px 5px; font-size: 11px;")
        btn_rm_studio.clicked.connect(lambda: self._apply_room_preset(12.0, 15.0, 5.0, 1.5))

        btn_rm_living = QtWidgets.QPushButton("Living (8×10m)")
        btn_rm_living.setStyleSheet("padding: 2px 5px; font-size: 11px;")
        btn_rm_living.clicked.connect(lambda: self._apply_room_preset(8.0, 10.0, 3.5, 1.5))

        btn_rm_office = QtWidgets.QPushButton("Office (5×6m)")
        btn_rm_office.setStyleSheet("padding: 2px 5px; font-size: 11px;")
        btn_rm_office.clicked.connect(lambda: self._apply_room_preset(5.0, 6.0, 2.8, 1.3))

        btn_rm_hall = QtWidgets.QPushButton("Hall (3×10m)")
        btn_rm_hall.setStyleSheet("padding: 2px 5px; font-size: 11px;")
        btn_rm_hall.clicked.connect(lambda: self._apply_room_preset(3.0, 10.0, 3.0, 1.5))

        room_pre_box.addWidget(btn_rm_studio)
        room_pre_box.addWidget(btn_rm_living)
        room_pre_box.addWidget(btn_rm_office)
        room_pre_box.addWidget(btn_rm_hall)
        room_lay.addRow("Room Presets:", room_pre_box)

        align_btn_box = QtWidgets.QHBoxLayout()
        btn_align_room = QtWidgets.QPushButton("📐 Auto-Fit Room Box Dimensions")
        btn_align_room.setStyleSheet("background-color: #2b3a4a; color: #58a6ff; font-weight: bold; padding: 4px; border-radius: 4px;")
        btn_align_room.setToolTip("Automatically analyze the equirectangular panorama to solve physical room width, depth, and height without rotating yaw.")
        btn_align_room.clicked.connect(self._on_auto_align_room_to_hdri)
        align_btn_box.addWidget(btn_align_room, 2)

        btn_reset_yaw = QtWidgets.QPushButton("🔄 Reset Yaw (0°)")
        btn_reset_yaw.setStyleSheet("background-color: #333333; color: #e6e6e6; font-weight: bold; padding: 4px; border-radius: 4px;")
        btn_reset_yaw.setToolTip("Reset room orientation (yaw) to 0.0° to align directly with the native HDRI panorama coordinates.")
        btn_reset_yaw.clicked.connect(self._on_reset_room_yaw)
        align_btn_box.addWidget(btn_reset_yaw, 1)
        room_lay.addRow("", align_btn_box)

        self.sld_room_width = SliderDoubleSpinBox(1.0, 200.0, 0.5, 8.0, decimals=2)
        self.sld_room_width.setToolTip("Room dimension along X-axis in meters.")
        room_lay.addRow("Room Width (m) [X]:", self.sld_room_width)

        self.sld_room_depth = SliderDoubleSpinBox(1.0, 200.0, 0.5, 10.0, decimals=2)
        self.sld_room_depth.setToolTip("Room dimension along Z-axis in meters.")
        room_lay.addRow("Room Depth (m) [Z]:", self.sld_room_depth)

        self.sld_room_height = SliderDoubleSpinBox(1.0, 50.0, 0.25, 3.5, decimals=2)
        self.sld_room_height.setToolTip("Room height along Y-axis in meters (floor to ceiling).")
        room_lay.addRow("Room Height (m) [Y]:", self.sld_room_height)

        self.sld_cam_offset_x = SliderDoubleSpinBox(-50.0, 50.0, 0.1, 0.0, decimals=2)
        self.sld_cam_offset_x.setToolTip("Camera lateral offset from room center along X in meters.")
        room_lay.addRow("Camera Offset X (m):", self.sld_cam_offset_x)

        self.sld_cam_offset_z = SliderDoubleSpinBox(-50.0, 50.0, 0.1, 0.0, decimals=2)
        self.sld_cam_offset_z.setToolTip("Camera forward/backward offset from room center along Z in meters.")
        room_lay.addRow("Camera Offset Z (m):", self.sld_cam_offset_z)

        # Face toggles
        faces_box = QtWidgets.QHBoxLayout()
        faces_box.setSpacing(10)
        self.chk_room_floor = QtWidgets.QCheckBox("Floor")
        self.chk_room_floor.setChecked(True)
        self.chk_room_ceiling = QtWidgets.QCheckBox("Ceiling")
        self.chk_room_ceiling.setChecked(True)
        self.chk_room_walls = QtWidgets.QCheckBox("Walls")
        self.chk_room_walls.setChecked(True)
        faces_box.addWidget(self.chk_room_floor)
        faces_box.addWidget(self.chk_room_ceiling)
        faces_box.addWidget(self.chk_room_walls)
        faces_box.addStretch()
        room_lay.addRow("Include Faces:", faces_box)

        self.sld_room_subdivs = SliderDoubleSpinBox(4.0, 64.0, 2.0, 16.0, decimals=0)
        self.sld_room_subdivs.setToolTip("Subdivisions per room face for perspective-correct UV curvature and vertex color resolution.")
        room_lay.addRow("Face Subdivisions:", self.sld_room_subdivs)

        self.chk_room_double_sided = QtWidgets.QCheckBox("Double-Sided Geometry")
        self.chk_room_double_sided.setChecked(False)
        self.chk_room_double_sided.setToolTip("When disabled (default), inward-facing normals allow the camera to look into the room from outside.")
        room_lay.addRow(self.chk_room_double_sided)

        self.chk_room_shadows = QtWidgets.QCheckBox("Room Mesh Casts Shadows")
        self.chk_room_shadows.setChecked(False)
        self.chk_room_shadows.setToolTip(
            "When unchecked (recommended for lookdev), sunlight, dome fill, and window practicals pass through the\n"
            "room walls and ceiling to illuminate interior meshes. When checked, the room acts as an opaque shadow-blocking enclosure."
        )
        room_lay.addRow(self.chk_room_shadows)

        self.chk_room_invisible = QtWidgets.QCheckBox("Invisible Room")
        self.chk_room_invisible.setChecked(False)
        self.chk_room_invisible.setToolTip(
            "When checked, the room mesh is invisible to camera/primary rays in Arnold, Karma, and Redshift renders.\n"
            "The background/dome environment remains visible through the room, while room reflections, emissive lighting,\n"
            "and GI bounces continue to illuminate your scene."
        )
        room_lay.addRow(self.chk_room_invisible)

        # Texture mapping mode: Planar High-Detail vs Equirectangular
        self.combo_ground_tex_mode = QtWidgets.QComboBox()
        self.combo_ground_tex_mode.addItems([
            "Planar High-Detail (Floor / Ceiling / 4 Walls)",
            "Equirectangular (Spherical Camera Projection)",
        ])
        self.combo_ground_tex_mode.setToolTip(
            "• Planar High-Detail: Rectilinear Cartesian textures baked per surface (floor, ceiling, and all 4 walls) with zero polar distortion, crystal clear.\n"
            "• Equirectangular: 360° spherical camera projection mapping the whole HDRI panorama onto room geometry."
        )
        room_lay.addRow("Texture Mapping:", self.combo_ground_tex_mode)

        # Planar Texture Baking Controls
        self.widget_ground_planar_opts = QtWidgets.QWidget()
        pln_lay = QtWidgets.QHBoxLayout(self.widget_ground_planar_opts)
        pln_lay.setContentsMargins(0, 0, 0, 0)
        self.combo_ground_planar_res = QtWidgets.QComboBox()
        self.combo_ground_planar_res.addItems([
            "8K (8192×8192)",
            "4K (4096×4096)",
            "2K (2048×2048)",
            "1K (1024×1024)",
        ])
        self.combo_ground_planar_res.setCurrentIndex(1)  # Default 4K
        self.combo_ground_planar_res.setToolTip("Resolution per planar rectilinear surface texture map (width & height).")

        self.btn_bake_ground_planar = QtWidgets.QPushButton("🎨 Bake Planar Textures")
        self.btn_bake_ground_planar.setStyleSheet(
            "QPushButton { background-color: #27ae60; color: white; font-weight: bold; padding: 3px 8px; border-radius: 4px; }"
            "QPushButton:hover { background-color: #2ecc71; }"
        )
        self.btn_bake_ground_planar.setToolTip("Bake distortion-free rectilinear OpenEXR textures for floor, ceiling, and all 4 walls.")

        self.lbl_ground_auto_bake = QtWidgets.QLabel("<span style='color: #2ecc71; font-size: 10px; font-weight: bold;'>[Auto-baked on Build]</span>")
        self.lbl_ground_auto_bake.setToolTip("Planar textures are automatically baked at this resolution whenever Build Solaris USD Projection Mesh is clicked.")

        pln_lay.addWidget(QtWidgets.QLabel("Res:"))
        pln_lay.addWidget(self.combo_ground_planar_res)
        pln_lay.addWidget(self.btn_bake_ground_planar)
        pln_lay.addWidget(self.lbl_ground_auto_bake)
        pln_lay.addStretch()
        room_lay.addRow("Planar Maps:", self.widget_ground_planar_opts)

        self.pbar_ground_planar = QtWidgets.QProgressBar()
        self.pbar_ground_planar.setRange(0, 100)
        self.pbar_ground_planar.setValue(0)
        self.pbar_ground_planar.setTextVisible(True)
        self.pbar_ground_planar.setFormat("Planar Textures Ready")
        self.pbar_ground_planar.setVisible(False)
        room_lay.addRow("", self.pbar_ground_planar)

        lay.addRow(self.widget_room_controls)

        # Ground / Room Projection Material Options
        gmat_row = QtWidgets.QHBoxLayout()
        self.combo_ground_mat_mode = QtWidgets.QComboBox()
        self.combo_ground_mat_mode.addItems([
            "PBR (Lit Diffuse)",
            "Emissive (Unlit / Projection Screen)",
            "PBR + Emissive Fill",
        ])
        self.combo_ground_mat_mode.setToolTip(
            "Material mode for the USD ground plane / room box mesh:\n"
            "• PBR (Lit Diffuse): Diffuse surface lit by external lights.\n"
            "• Emissive (Unlit / Projection Screen): Directly emits texture radiance without external lighting.\n"
            "• PBR + Emissive Fill: Hybrid diffuse reflection with emissive fill radiance."
        )
        self.sld_ground_emissive_mult = SliderDoubleSpinBox(0.0, 20.0, 0.1, 1.0, decimals=2)
        self.sld_ground_emissive_mult.setToolTip("Multiplier for ground mesh emissive texture radiance.")
        gmat_row.addWidget(self.combo_ground_mat_mode)
        gmat_row.addWidget(QtWidgets.QLabel("Emissive Mult:"))
        gmat_row.addWidget(self.sld_ground_emissive_mult)
        lay.addRow("Projection Material:", gmat_row)

        # Renderer Target Selector
        self.combo_renderer_target = QtWidgets.QComboBox()
        self.combo_renderer_target.addItems([
            "All Renderers (Arnold + Karma + Redshift)",
            "Arnold",
            "Karma (MaterialX)",
            "Redshift",
            "USD Preview Only",
        ])
        self.combo_renderer_target.setToolTip(
            "Select which renderer-native shader graphs to build inside the USD material:\n"
            "• All Renderers: Arnold Standard Surface + Karma MaterialX + Redshift StandardMaterial + UsdPreviewSurface viewport fallback.\n"
            "• Single renderer: Native shader + UsdPreviewSurface fallback only.\n"
            "• USD Preview Only: UsdPreviewSurface only (lightweight, GL viewport)."
        )
        lay.addRow("Renderer Target:", self.combo_renderer_target)

        # Action Button
        self.btn_create_ground_geometry = QtWidgets.QPushButton("📐 Build Solaris USD Projection Mesh")
        self.btn_create_ground_geometry.setStyleSheet(
            "QPushButton { background-color: #1a3a5a; color: #00d2ff; font-weight: bold; padding: 8px; font-size: 12px; border: 1px solid #2e6b9e; border-radius: 4px; }"
            "QPushButton:hover { background-color: #265585; color: #ffffff; }"
        )
        self.btn_create_ground_geometry.setToolTip(
            "Generates a 3D ground plane or interior room box projection mesh (/environment/ground_dome) in the Solaris USD stage."
        )
        lay.addRow(self.btn_create_ground_geometry)

        self.combo_proj_mode.currentIndexChanged.connect(self._on_proj_mode_changed)
        self.combo_ground_tex_mode.currentIndexChanged.connect(self._on_ground_tex_mode_changed)
        self.combo_ground_planar_res.currentIndexChanged.connect(lambda: setattr(self, '_ground_planar_textures', None))
        self.btn_bake_ground_planar.clicked.connect(self._bake_ground_planar_textures_clicked)
        self._on_proj_mode_changed(0)
        self._on_ground_tex_mode_changed(0)

        self._scroll_layout.addWidget(sec)

    def _on_proj_mode_changed(self, index):
        """Toggle visibility between ground disc controls and interior room box controls."""
        is_room = (index == 1)
        if hasattr(self, 'widget_disc_controls'):
            self.widget_disc_controls.setVisible(not is_room)
        if hasattr(self, 'widget_room_controls'):
            self.widget_room_controls.setVisible(is_room)
        if not getattr(self, '_restoring_state', False) and not getattr(self, '_initializing', False):
            if is_room and hasattr(self, 'chk_snap_to_room'):
                self.chk_snap_to_room.setChecked(True)
            if hasattr(self, '_sync_node'):
                self._sync_node()

    def _on_ground_tex_mode_changed(self, index):
        """Toggle visibility between planar texture baking options and equirectangular camera projection."""
        is_planar = (index == 0)
        if hasattr(self, 'widget_ground_planar_opts'):
            self.widget_ground_planar_opts.setVisible(is_planar)
        if hasattr(self, 'pbar_ground_planar') and not is_planar:
            self.pbar_ground_planar.setVisible(False)
        if not getattr(self, '_restoring_state', False):
            if hasattr(self, '_sync_node'):
                self._sync_node()

    def _on_renderer_target_changed(self, index=0):
        """Instantly switch shaders in /stage/hdri_match_materials to match the selected renderer target without re-baking textures."""
        if getattr(self, '_restoring_state', False) or getattr(self, '_initializing', False):
            return
        stage_node = self._get_stage_node()
        if not stage_node:
            return

        target_token = ["all", "arnold", "karma", "redshift", "preview"][index] if 0 <= index < 5 else "all"
        if target_token == "all":
            if hasattr(self, 'sld_ground_roughness'):
                self.sld_ground_roughness.setValue(1.0)
            if hasattr(self, 'sld_arch_roughness'):
                self.sld_arch_roughness.setValue(1.0)
        elif target_token == "arnold":
            dome_node = stage_node.node("hdri_dome")
            if dome_node:
                _ensure_dome_light_latlong(dome_node)

        matlib_node = stage_node.node("hdri_match_materials")
        if matlib_node is None:
            if hasattr(self, '_sync_node'):
                self._sync_node()
            return

        p = self._collect_parms()
        calibrated_path = getattr(self, '_current_calibrated_path', None)
        if calibrated_path and os.path.isfile(calibrated_path):
            p["hdri_texture"] = calibrated_path.replace(chr(92), "/")
        else:
            p["hdri_texture"] = p.get("hdri_path", "").replace(chr(92), "/")

        pres = 4096
        res_txt = p.get("ground_planar_res", "4K")
        if "8K" in res_txt or "8192" in res_txt:
            pres = 8192
        elif "4K" in res_txt or "4096" in res_txt:
            pres = 4096
        elif "1K" in res_txt or "1024" in res_txt:
            pres = 1024
        else:
            pres = 2048

        hip_dir = hou.expandString("$HIP") if 'hou' in sys.modules and hasattr(hou, 'expandString') else "."
        if not hip_dir or hip_dir == "." or "houdini_temp" in hip_dir:
            hip_dir = os.path.expanduser("~")

        hdri_tex_path = p.get("hdri_texture", "")
        hdri_base = os.path.splitext(os.path.basename(hdri_tex_path))[0] if hdri_tex_path else "room"
        import re
        hdri_clean_base = re.sub(r'(_inpainted)+$', '', hdri_base)
        planar_dir = os.path.join(hip_dir, "hdri_match", f"planar_textures_{hdri_clean_base}_{pres}").replace("\\", "/")

        existing_planar = getattr(self, '_ground_planar_textures', None)
        if not isinstance(existing_planar, dict) or not existing_planar:
            existing_planar = {}
            if os.path.isdir(planar_dir):
                needed_surfaces = ["floor", "ceiling", "wall_north", "wall_south", "wall_east", "wall_west", "ground"]
                for sname in needed_surfaces:
                    for sfx in ["_diffuse.exr", "_albedo.exr", ".exr"]:
                        fpath = os.path.join(planar_dir, f"{sname}{sfx}").replace("\\", "/")
                        if os.path.isfile(fpath) and os.path.getsize(fpath) > 0:
                            existing_planar[sname] = fpath
                            break
            if existing_planar:
                self._ground_planar_textures = existing_planar

        p["planar_textures"] = existing_planar

        try:
            matlib_func = _get_create_or_update_material_library()
            if matlib_func:
                matlib_func(stage_node, p)
                matlib_node.cook(force=True)
                target_label = self.combo_renderer_target.currentText() if hasattr(self, 'combo_renderer_target') else "Renderer"
                self.log(f"Switched Material Library shaders to '{target_label}' instantly (reusing existing baked textures).", "SUCCESS")
                if hou.isUIAvailable():
                    hou.ui.triggerUpdate()
        except Exception as ex_mat:
            self.log_error(f"Renderer target shader update warning: {ex_mat}", ex_mat)

        if hasattr(self, '_sync_node'):
            self._sync_node()

    def _get_inpainted_hdri_source(self, hdri_path, planar_dir=None):
        """Prepare an in-memory numpy array (and optional cached EXR) with practical lights and sun disc
        cleanly painted out so that planar baked textures do not double-illuminate practical fixtures."""
        from hdri_match_solaris.gaussian_splat import GaussianSplatBaker
        hdri_arr = None
        if hdri_path and os.path.isfile(hdri_path):
            try:
                hdri_arr = GaussianSplatBaker.load_exr(hdri_path)
            except Exception:
                hdri_arr = None

        if hdri_arr is None:
            return hdri_path, False

        has_modifications = False

        # 1. Inpaint primary sun if sun relighting and sun removal are enabled
        sun_en = self.grp_sun.isChecked() if hasattr(self, 'grp_sun') else False
        sun_remove = self.chk_remove_sun.isChecked() if hasattr(self, 'chk_remove_sun') else True
        if sun_en and sun_remove and hasattr(self, 'sld_sun_u') and hasattr(self, 'sld_sun_v'):
            s_u = float(self.sld_sun_u.value())
            s_v = float(self.sld_sun_v.value())
            s_rad = float(self.sld_sun_radius.value())
            hdri_arr = HdriMatchSolarisPanel._inpaint_sun_disc(hdri_arr, s_u, s_v, s_rad)
            has_modifications = True

        # 2. Inpaint ONLY actively extracted practical lights (if inpainting is enabled)
        extract_inpaint_chk = hasattr(self, 'chk_extract_inpaint') and self.chk_extract_inpaint.isChecked()
        extract_en = (hasattr(self, 'grp_extract') and self.grp_extract.isChecked()) or (hasattr(self, 'chk_extract_en') and self.chk_extract_en.isChecked())
        extracted = getattr(self, '_extracted_practicals_data', None)
        if extract_inpaint_chk and extract_en and extracted:
            for lt in extracted:
                p_px = lt.get("px")
                p_py = lt.get("py")
                p_hw = lt.get("hw")
                p_hh = lt.get("hh")
                if p_px is not None and p_py is not None and p_hw is not None and p_hh is not None:
                    hdri_arr = HdriMatchSolarisPanel._inpaint_practical_fixture(hdri_arr, p_px, p_py, p_hw, p_hh)
                    has_modifications = True

        if has_modifications and planar_dir:
            try:
                os.makedirs(planar_dir, exist_ok=True)
                hdri_base = os.path.splitext(os.path.basename(hdri_path))[0]
                import re
                hdri_clean_base = re.sub(r'(_inpainted)+$', '', hdri_base)
                inpainted_file = os.path.join(planar_dir, f"{hdri_clean_base}_inpainted.exr").replace("\\", "/")
                need_save = True
                if os.path.isfile(inpainted_file) and os.path.getsize(inpainted_file) > 0:
                    if os.path.isfile(hdri_path) and os.path.getmtime(inpainted_file) >= os.path.getmtime(hdri_path):
                        need_save = False
                if need_save:
                    GaussianSplatBaker.save_exr(hdri_arr, inpainted_file)
                self._current_calibrated_path = inpainted_file
            except Exception as ex_save:
                print(f"[HDRI Match] Inpainted EXR save notice: {ex_save}")

        return (hdri_arr if has_modifications else hdri_path), has_modifications

    def _bake_ground_planar_textures_clicked(self):
        """Bake high-resolution planar rectilinear textures for floor, ceiling, and walls in Ground & Room projection."""
        hdri_path = getattr(self, '_current_calibrated_path', None)
        if not hdri_path or not os.path.isfile(hdri_path):
            if hasattr(self, 'txt_hdri') and self.txt_hdri.text().strip() and os.path.isfile(self.txt_hdri.text().strip()):
                hdri_path = self.txt_hdri.text().strip()

        if not hdri_path or not os.path.isfile(hdri_path):
            stage_node = self._get_stage_node()
            if stage_node:
                dome_node = stage_node.node("hdri_dome")
                if dome_node:
                    for p_name in ["inputs:texture:file", "xn__inputstexturefile_r3ah", "texture"]:
                        p_parm = dome_node.parm(p_name)
                        if p_parm and p_parm.evalAsString():
                            cand = p_parm.evalAsString().replace("\\", "/")
                            if os.path.isfile(cand):
                                hdri_path = cand
                                break

        if not hdri_path or not os.path.isfile(hdri_path):
            self.log("Please select or load an HDRI texture first before baking planar textures.", "WARNING")
            if hou.isUIAvailable():
                hou.ui.displayMessage("No valid HDRI image available to bake planar textures from. Please load an HDRI first.", title="DomeBreaker")
            return

        res_txt = self.combo_ground_planar_res.currentText() if hasattr(self, 'combo_ground_planar_res') else "4K"
        if "8K" in res_txt or "8192" in res_txt:
            pres = 8192
        elif "4K" in res_txt or "4096" in res_txt:
            pres = 4096
        elif "1K" in res_txt or "1024" in res_txt:
            pres = 1024
        else:
            pres = 2048

        hip_dir = hou.expandString("$HIP") if 'hou' in sys.modules and hasattr(hou, 'expandString') else "."
        if not hip_dir or hip_dir == "." or "houdini_temp" in hip_dir:
            hip_dir = os.path.expanduser("~")

        hdri_base = os.path.splitext(os.path.basename(hdri_path))[0]
        planar_dir = os.path.join(hip_dir, "hdri_match", f"planar_textures_{hdri_base}_{pres}").replace("\\", "/")
        os.makedirs(planar_dir, exist_ok=True)

        room_w = float(self.sld_room_width.value()) if hasattr(self, 'sld_room_width') else 8.0
        room_d = float(self.sld_room_depth.value()) if hasattr(self, 'sld_room_depth') else 10.0
        room_h = float(self.sld_room_height.value()) if hasattr(self, 'sld_room_height') else 3.5
        tripod_h = float(self.sld_tripod_height.value()) if hasattr(self, 'sld_tripod_height') else 1.5
        cam_ox = float(self.sld_cam_offset_x.value()) if hasattr(self, 'sld_cam_offset_x') else 0.0
        cam_oz = float(self.sld_cam_offset_z.value()) if hasattr(self, 'sld_cam_offset_z') else 0.0

        room_data = {
            "center": [0.0, room_h * 0.5, 0.0],
            "floor_y": 0.0,
            "ceil_y": room_h,
            "height": room_h,
            "x_min": -room_w * 0.5,
            "x_max": room_w * 0.5,
            "z_min": -room_d * 0.5,
            "z_max": room_d * 0.5,
            "width": room_w,
            "depth": room_d,
        }
        probe_pos = (cam_ox, tripod_h, cam_oz)

        try:
            from hdri_match_solaris.gaussian_splat import GaussianSplatBaker
            hdri_source, was_inpainted = self._get_inpainted_hdri_source(hdri_path, planar_dir=planar_dir)
            inpaint_note = " (with practical lights painted out)" if was_inpainted else ""
            self.log(f"Baking 6 planar rectilinear surface textures ({pres}x{pres}) from {os.path.basename(hdri_path)}{inpaint_note}...", "INFO")
            if hasattr(self, 'pbar_ground_planar'):
                self.pbar_ground_planar.setVisible(True)
                self.pbar_ground_planar.setValue(10)
                self.pbar_ground_planar.setFormat(f"Baking Planar Maps ({pres}x{pres})...")

            def _on_progress(pct, msg):
                if hasattr(self, 'pbar_ground_planar'):
                    self.pbar_ground_planar.setValue(pct)
                    self.pbar_ground_planar.setFormat(msg)

            self._ground_planar_textures = GaussianSplatBaker.bake_planar_room_textures(
                hdri_source=hdri_source,
                room_data=room_data,
                output_dir=planar_dir,
                probe_pos=probe_pos,
                resolution_floor=pres,
                resolution_walls=pres,
                progress_callback=_on_progress,
            )
            if hasattr(self, 'pbar_ground_planar'):
                self.pbar_ground_planar.setValue(100)
                self.pbar_ground_planar.setFormat(f"Planar Ready ({pres}x{pres})")
            self.log(f"Baked {len(self._ground_planar_textures)} planar textures ({pres}x{pres}) successfully!", "SUCCESS")
            self._create_or_update_ground_projection(notify_ui=True)
        except Exception as ex:
            self.log_error(f"Planar texture bake failed: {ex}", ex)
            if hou.isUIAvailable():
                hou.ui.displayMessage(f"Planar bake failed:\n{ex}", severity=hou.severityType.Error)

    def _build_light_extraction_section(self):
        """Multi-light practical extraction and auto-inpainting."""
        sec = CollapsibleSection("Multi-Light Extraction & Auto-Inpainting", checkable=True, checked=False, collapsed=True)
        self.grp_extract = sec
        self._collapsible_sections.append(sec)
        lay = QtWidgets.QFormLayout(sec.content_widget)
        lay.setContentsMargins(8, 10, 8, 8)

        # Hotspot Scanner & Detection
        scan_box = QtWidgets.QHBoxLayout()
        scan_box.setSpacing(6)

        self.btn_analyze_hotspots = QtWidgets.QPushButton("🔍 Analyze Hotspots")
        self.btn_analyze_hotspots.setStyleSheet(
            "QPushButton { font-weight: bold; background-color: #2b3a4a; color: #66ccff; padding: 4px 8px; border-radius: 3px; }"
            "QPushButton:hover { background-color: #384e66; color: #ffffff; }"
        )
        self.btn_analyze_hotspots.setToolTip("Analyze HDRI to scan, detect, and count all potential bright practical hotspot emitters above threshold.")
        scan_box.addWidget(self.btn_analyze_hotspots)

        self.btn_apply_detected_count = QtWidgets.QPushButton("Set Count")
        self.btn_apply_detected_count.setEnabled(False)
        self.btn_apply_detected_count.setStyleSheet(
            "QPushButton { font-weight: bold; background-color: #2a4030; color: #88ee99; padding: 4px 8px; border-radius: 3px; }"
            "QPushButton:hover { background-color: #385540; color: #ffffff; }"
            "QPushButton:disabled { background-color: #222; color: #555; }"
        )
        self.btn_apply_detected_count.setToolTip("Set Emitter Count to the total number of detected hotspots.")
        scan_box.addWidget(self.btn_apply_detected_count)
        lay.addRow("Hotspot Scanner:", scan_box)

        self.lbl_hotspot_info = QtWidgets.QLabel("Click 'Analyze Hotspots' to scan HDRI")
        self.lbl_hotspot_info.setStyleSheet("color: #888; font-size: 11px; font-style: italic;")
        self.lbl_hotspot_info.setWordWrap(True)
        lay.addRow("", self.lbl_hotspot_info)

        self.sld_extract_count = SliderDoubleSpinBox(1.0, 64.0, 1.0, 8.0, decimals=0)
        self.sld_extract_count.setToolTip("Maximum number of practical high-intensity emitters to extract into USD stage lights (up to 64).")
        lay.addRow("Emitter Count:", self.sld_extract_count)

        self.sld_extract_dist = SliderDoubleSpinBox(0.5, 100.0, 0.5, 10.0, decimals=1)
        self.sld_extract_dist.setToolTip("Distance in meters of practical RectLights from center (used in outdoor/ground mode or manual override).")
        lay.addRow("Distance (m):", self.sld_extract_dist)

        self.chk_snap_to_room = QtWidgets.QCheckBox("Snap to Room Box Walls & Ceiling (Interior Mode)")
        self.chk_snap_to_room.setChecked(True)
        self.chk_snap_to_room.setToolTip(
            "When in Interior Room Box mode, automatically raytraces light directions to room walls and ceiling,\n"
            "positioning rect lights directly on room surfaces and sizing them to the exact physical window/fixture scale."
        )
        lay.addRow(self.chk_snap_to_room)

        self.sld_extract_intensity = SliderDoubleSpinBox(0.0, 100.0, 0.1, 1.0, decimals=2)
        self.sld_extract_intensity.setToolTip("Global intensity multiplier for all extracted practical rect lights.")
        lay.addRow("Intensity Multiplier:", self.sld_extract_intensity)

        self.sld_extract_exposure = SliderDoubleSpinBox(-6.0, 6.0, 0.1, 0.0, decimals=2)
        self.sld_extract_exposure.setToolTip("Exposure offset in EV stops applied to extracted practical rect lights.")
        lay.addRow("Exposure Offset (EV):", self.sld_extract_exposure)

        self.sld_extract_range = SliderDoubleSpinBox(1.0, 24.0, 0.5, 8.0, decimals=1)
        self.sld_extract_range.setToolTip(
            "Dynamic range in EV stops below peak emitter to analyze for hotspots.\n"
            "• Lower (2-5 EV): Captures only the brightest key emitters (e.g. direct sun or main exterior opening).\n"
            "• Higher (6-12 EV): Also captures interior fixtures (e.g. ceiling lights, skylights) in high dynamic range scenes."
        )
        lay.addRow("Exposure Range (EV):", self.sld_extract_range)

        self.sld_extract_thresh = SliderDoubleSpinBox(0.5, 16.0, 0.5, 3.0, decimals=1)
        self.sld_extract_thresh.setToolTip(
            "Noise floor threshold in EV stops above ambient median luminance.\n"
            "Rejects noise, ambient bleed, and surface floor bounces below this cutoff."
        )
        lay.addRow("Min Threshold (EV):", self.sld_extract_thresh)

        self.chk_extract_inpaint = QtWidgets.QCheckBox("Inpaint Extracted Lights from Dome Map")
        self.chk_extract_inpaint.setChecked(True)
        self.chk_extract_inpaint.setToolTip(
            "Removes extracted emitters from the HDRI dome map using surrounding sky/fill inpainting,\n"
            "preventing double-specular highlights, incorrect shadow overlap, and fireflies."
        )
        lay.addRow(self.chk_extract_inpaint)

        self.btn_extract_lights = QtWidgets.QPushButton("💡 Extract Practical Lights to USD Stage")
        self.btn_extract_lights.setStyleSheet(
            "QPushButton { font-weight: bold; background-color: #2b3a4a; color: #66ccff; padding: 6px; border-radius: 4px; }"
            "QPushButton:hover { background-color: #384e66; color: #ffffff; }"
        )
        self.btn_extract_lights.setToolTip("Extract bright practical emitters into native UsdLuxSphereLight nodes under /lights/extracted.")
        lay.addRow(self.btn_extract_lights)

        self._scroll_layout.addWidget(sec)

    def _build_aov_section(self):
        """AOV Light Groups on Physical Lights (Karma, Arnold, Redshift)."""
        sec = CollapsibleSection("AOV Light Groups (Karma / Arnold / Redshift)", checkable=True, checked=True, collapsed=True)
        self.grp_aov = sec
        self._collapsible_sections.append(sec)
        lay = QtWidgets.QFormLayout(sec.content_widget)
        lay.setContentsMargins(8, 10, 8, 8)

        self.chk_aov_lightgroups = QtWidgets.QCheckBox("Enable Light Groups on Physical Lights")
        self.chk_aov_lightgroups.setChecked(True)
        self.chk_aov_lightgroups.setToolTip(
            "Enables and sets the native LPE Tag (Karma), AOV Light Group (Arnold), and AOV Light Group (Redshift)\n"
            "directly on the physical light nodes in the stage (hdri_dome, hdri_sun, practicals)."
        )
        lay.addRow(self.chk_aov_lightgroups)

        self.txt_dome_lg = QtWidgets.QLineEdit("lg_dome")
        self.txt_dome_lg.setToolTip("Light group tag assigned to the HDRI Dome Light.")
        lay.addRow("Dome Light Group:", self.txt_dome_lg)

        self.txt_sun_lg = QtWidgets.QLineEdit("lg_sun")
        self.txt_sun_lg.setToolTip("Light group tag assigned to the Distant Sun Light.")
        lay.addRow("Sun Light Group:", self.txt_sun_lg)

        self.txt_practicals_lg = QtWidgets.QLineEdit("lg_practicals")
        self.txt_practicals_lg.setToolTip("Light group tag assigned to extracted Practical Rect Lights.")
        lay.addRow("Practicals Light Group:", self.txt_practicals_lg)

        self.btn_setup_aovs = QtWidgets.QPushButton("🏷️ Assign Light Groups to Stage Lights")
        self.btn_setup_aovs.setStyleSheet("padding: 6px; font-weight: bold; background: #1a3344; color: #4db8ff; border-radius: 4px;")
        self.btn_setup_aovs.setToolTip("Sets the AOV Light Group and LPE Tag parameters on all active lights in /stage for Karma, Arnold, and Redshift.")
        self.btn_setup_aovs.clicked.connect(self._apply_light_groups_to_stage)
        lay.addRow(self.btn_setup_aovs)

        self._scroll_layout.addWidget(sec)

    def _apply_room_preset(self, w, d, h, tripod):
        """Apply a physical dimension preset to the interior room box."""
        for attr, val in [('sld_room_width', w), ('sld_room_depth', d), ('sld_room_height', h), ('sld_tripod_height', tripod)]:
            if hasattr(self, attr):
                widget = getattr(self, attr)
                widget.blockSignals(True)
                widget.setValue(float(val))
                widget.blockSignals(False)
        self._sync_node()

    def _apply_lookdev_preset(self, radius, spacing, height, stand):
        """Apply a physical scale preset to the lookdev calibration rig."""
        if hasattr(self, 'sld_lookdev_radius'):
            self.sld_lookdev_radius.setValue(float(radius))
        if hasattr(self, 'sld_lookdev_spacing'):
            self.sld_lookdev_spacing.setValue(float(spacing))
        if hasattr(self, 'sld_lookdev_height'):
            self.sld_lookdev_height.setValue(float(height))
        if hasattr(self, 'chk_lookdev_stand'):
            self.chk_lookdev_stand.setChecked(bool(stand))
        self._sync_node()

    def _build_lookdev_section(self):
        """Lookdev calibration rig controls (Chrome & 18% Grey balls, stand, white ball)."""
        sec = CollapsibleSection("Lookdev Calibration Rig (Chrome & 18% Grey)", checkable=True, checked=True, collapsed=True)
        self.grp_lookdev = sec
        self._collapsible_sections.append(sec)
        lay = QtWidgets.QFormLayout(sec.content_widget)
        lay.setContentsMargins(8, 10, 8, 8)

        # Scale Presets
        presets_box = QtWidgets.QHBoxLayout()
        presets_box.setSpacing(4)
        btn_pre_vfx = QtWidgets.QPushButton("🎬 VFX Standard (30cm)")
        btn_pre_vfx.setStyleSheet("padding: 2px 5px; font-size: 11px;")
        btn_pre_vfx.setToolTip("Industry VFX Standard: 15cm radius (30cm / 12in diameter) calibration balls on a 1.0m stand.")
        btn_pre_vfx.clicked.connect(lambda: self._apply_lookdev_preset(0.15, 0.45, 1.0, True))

        btn_pre_macro = QtWidgets.QPushButton("🔍 Tabletop (12cm)")
        btn_pre_macro.setStyleSheet("padding: 2px 5px; font-size: 11px;")
        btn_pre_macro.setToolTip("Compact setup: 6cm radius (12cm diameter) balls at 0.4m height for table / prop lookdev.")
        btn_pre_macro.clicked.connect(lambda: self._apply_lookdev_preset(0.06, 0.20, 0.40, True))

        btn_pre_floor = QtWidgets.QPushButton("📐 Floor (30cm)")
        btn_pre_floor.setStyleSheet("padding: 2px 5px; font-size: 11px;")
        btn_pre_floor.setToolTip("Resting directly on ground/floor plane at Y = radius.")
        btn_pre_floor.clicked.connect(lambda: self._apply_lookdev_preset(0.15, 0.45, 0.15, False))

        presets_box.addWidget(btn_pre_vfx)
        presets_box.addWidget(btn_pre_macro)
        presets_box.addWidget(btn_pre_floor)
        lay.addRow("Scale Presets:", presets_box)

        self.sld_lookdev_radius = SliderDoubleSpinBox(0.02, 2.0, 0.01, 0.15, decimals=3)
        self.sld_lookdev_radius.setToolTip("Radius of lookdev calibration balls in meters (0.15m = 30cm / 12in diameter, VFX industry standard).")
        lay.addRow("Ball Radius (m):", self.sld_lookdev_radius)

        self.sld_lookdev_spacing = SliderDoubleSpinBox(0.05, 5.0, 0.05, 0.45, decimals=2)
        self.sld_lookdev_spacing.setToolTip("Center-to-center distance between chrome and grey spheres in meters.")
        lay.addRow("Ball Spacing (m):", self.sld_lookdev_spacing)

        self.sld_lookdev_height = SliderDoubleSpinBox(0.0, 10.0, 0.05, 1.0, decimals=2)
        self.sld_lookdev_height.setToolTip("Height of ball centers above the floor in meters (1.0m chest/tripod stand height).")
        lay.addRow("Stand Height (m):", self.sld_lookdev_height)

        self.sld_lookdev_pos_x = SliderDoubleSpinBox(-50.0, 50.0, 0.1, 0.0, decimals=2)
        self.sld_lookdev_pos_x.setToolTip("Rig horizontal offset along X axis in meters.")
        lay.addRow("Position X (m):", self.sld_lookdev_pos_x)

        self.sld_lookdev_pos_z = SliderDoubleSpinBox(-50.0, 50.0, 0.1, 0.0, decimals=2)
        self.sld_lookdev_pos_z.setToolTip("Rig depth offset along Z axis in meters.")
        lay.addRow("Position Z (m):", self.sld_lookdev_pos_z)

        opts_box = QtWidgets.QHBoxLayout()
        self.chk_lookdev_stand = QtWidgets.QCheckBox("Include Stand Post")
        self.chk_lookdev_stand.setChecked(True)
        self.chk_lookdev_stand.setToolTip("Generates a slender dark-metal calibration stand and crossbar beneath the balls.")
        self.chk_lookdev_white = QtWidgets.QCheckBox("Include Matte White Ball")
        self.chk_lookdev_white.setChecked(False)
        self.chk_lookdev_white.setToolTip("Adds a 90% diffuse matte white ball alongside chrome & grey.")
        opts_box.addWidget(self.chk_lookdev_stand)
        opts_box.addWidget(self.chk_lookdev_white)
        opts_box.addStretch()
        lay.addRow("Rig Options:", opts_box)

        self.btn_create_lookdev_rig = QtWidgets.QPushButton("🔮 Add / Update Lookdev Spheres")
        self.btn_create_lookdev_rig.setStyleSheet(
            "QPushButton { font-weight: bold; background-color: #3b2a4a; color: #cc99ff; padding: 6px; border-radius: 4px; }"
            "QPushButton:hover { background-color: #4f3866; color: #ffffff; }"
        )
        self.btn_create_lookdev_rig.setToolTip("Creates or updates the /stage/hdri_match_lookdev Python LOP node on the USD stage.")
        self.btn_create_lookdev_rig.clicked.connect(self._add_lookdev)
        lay.addRow(self.btn_create_lookdev_rig)

        self._scroll_layout.addWidget(sec)

    def _build_action_section(self):
        """Action buttons."""
        sec = CollapsibleSection("Actions", collapsed=False)
        self.grp_actions = sec
        self._collapsible_sections.append(sec)
        lay = QtWidgets.QVBoxLayout(sec.content_widget)
        lay.setContentsMargins(8, 10, 8, 8)

        self.btn_create_network = QtWidgets.QPushButton(
            "Create Full LOP Network")
        self.btn_create_network.setStyleSheet(
            "background-color: #d86c00; color: white; "
            "font-weight: bold; padding: 8px; font-size: 13px;"
        )
        lay.addWidget(self.btn_create_network)

        self.btn_merge_networks = QtWidgets.QPushButton("🔀 Merge Networks into Single Flow")
        self.btn_merge_networks.setStyleSheet(
            "background-color: #2e6b9e; color: white; font-weight: bold; padding: 6px; font-size: 11px;"
        )
        self.btn_merge_networks.setToolTip("Wire hdri_dome, hdri_sun, crucible_light_studio, and lookdev into a single continuous stream in /stage.")
        self.btn_merge_networks.setVisible(False)  # Hidden as network auto-merges automatically
        lay.addWidget(self.btn_merge_networks)

        row_bake = QtWidgets.QHBoxLayout()
        self.btn_bake_hdri = QtWidgets.QPushButton("💾 Bake Calibrated HDRI to $HIP")
        self.btn_bake_hdri.setStyleSheet(
            "background-color: #0088cc; color: white; "
            "font-weight: bold; padding: 6px;"
        )
        self.btn_bake_hdri.setToolTip("Process Horizon Split & Soft-Clip, save calibrated EXR to scene $HIP folder, and update Dome Light.")
        self.btn_bake_hdri.setVisible(False)  # Hidden: automatically baked on 'Create Full LOP Network'
        self.chk_auto_bake = QtWidgets.QCheckBox("⚡ Live Viewport Sync")
        self.chk_auto_bake.setChecked(True)
        self.chk_auto_bake.setToolTip("Automatically bake calibrated HDRI in a background worker thread and update viewport Dome Light in real-time.")
        row_bake.addWidget(self.btn_bake_hdri)
        row_bake.addWidget(self.chk_auto_bake)
        lay.addLayout(row_bake)

        # Hidden individual action buttons (their actions trigger automatically when their corresponding section checkboxes are enabled)
        self.btn_calibrate = QtWidgets.QPushButton("Run Calibration")
        self.btn_calibrate.setVisible(False)

        self.btn_add_lookdev = QtWidgets.QPushButton("Add Lookdev Spheres")
        self.btn_add_lookdev.setVisible(False)

        self._btn_legacy_extract = QtWidgets.QPushButton("Extract Lights")
        self._btn_legacy_extract.setVisible(False)

        self.btn_detect_sun = QtWidgets.QPushButton("Detect Sun")
        self.btn_detect_sun.setVisible(False)

        self.btn_action_projection = QtWidgets.QPushButton("📐 Build Solaris USD Projection Mesh")
        self.btn_action_projection.setStyleSheet(
            "QPushButton { background-color: #1a334d; color: #00d2ff; font-weight: bold; padding: 7px; font-size: 11px; border-radius: 4px; border: 1px solid #2e6b9e; }"
            "QPushButton:hover { background-color: #264d73; color: #ffffff; }"
        )
        self.btn_action_projection.setToolTip("Generates or updates the 3D ground plane or interior room box projection mesh in /stage.")
        self.btn_action_projection.setVisible(True)
        self.btn_action_projection.clicked.connect(self._create_or_update_ground_projection)
        lay.addWidget(self.btn_action_projection)

        self.btn_action_aovs = QtWidgets.QPushButton("🏷️ Light Groups")
        self.btn_action_aovs.setVisible(False)
        self.btn_action_aovs.clicked.connect(self._apply_light_groups_to_stage)

        self._scroll_layout.addWidget(sec)

    def _build_log_section(self):
        """Collapsible real-time debug console and output log."""
        sec = CollapsibleSection("Debug Log & Output Console", collapsed=True)
        self.grp_log = sec
        self._collapsible_sections.append(sec)
        lay = QtWidgets.QVBoxLayout(sec.content_widget)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(4)

        # Toolbar
        bar = QtWidgets.QHBoxLayout()
        self.lbl_log_status = QtWidgets.QLabel("Status: Ready")
        self.lbl_log_status.setStyleSheet("color: #00ff88; font-size: 11px; font-weight: bold;")
        bar.addWidget(self.lbl_log_status, 1)

        self.btn_reload_console = QtWidgets.QPushButton("ð Reload")
        self.btn_reload_console.setToolTip("Reload Python modules and refresh the tool")
        self.btn_reload_console.setStyleSheet("font-size: 10px; font-weight: bold; padding: 3px 8px; background: #1e334a; color: #00d2ff; border-radius: 3px;")

        self.btn_popout_log = QtWidgets.QPushButton("ð Pop Out Window")
        self.btn_popout_log.setToolTip("Open separate floating debug & error window")
        self.btn_popout_log.setStyleSheet("font-size: 10px; font-weight: bold; padding: 3px 8px; background: #2b3a4a; border-radius: 3px;")

        self.btn_copy_log = QtWidgets.QPushButton("Copy")
        self.btn_copy_log.setToolTip("Copy entire log to clipboard")
        self.btn_copy_log.setStyleSheet("font-size: 10px; padding: 3px 6px;")

        self.btn_clear_log = QtWidgets.QPushButton("Clear")
        self.btn_clear_log.setToolTip("Clear log console")
        self.btn_clear_log.setStyleSheet("font-size: 10px; padding: 3px 6px;")

        self.chk_auto_popout = QtWidgets.QCheckBox("Auto-popup on Error")
        self.chk_auto_popout.setToolTip("Automatically open the floating window when any button encounters an error")
        self.chk_auto_popout.setChecked(True)
        self.chk_auto_popout.setStyleSheet("font-size: 10px; color: #ffaa00;")

        bar.addWidget(self.btn_reload_console)
        bar.addWidget(self.btn_popout_log)
        bar.addWidget(self.btn_copy_log)
        bar.addWidget(self.btn_clear_log)
        bar.addWidget(self.chk_auto_popout)
        lay.addLayout(bar)

        # Log Text Box
        self.txt_log = QtWidgets.QTextEdit()
        self.txt_log.setReadOnly(True)
        self.txt_log.setMinimumHeight(130)
        self.txt_log.setMaximumHeight(200)
        self.txt_log.setStyleSheet(
            "background-color: #0f1115; color: #ddd; font-family: Consolas, 'Courier New', monospace; "
            "font-size: 11px; border: 1px solid #282c34; border-radius: 4px; padding: 4px;"
        )
        lay.addWidget(self.txt_log)
        self._scroll_layout.addWidget(sec)


    def _build_preview_section(self):
        """Dual / Split view preview section for HDRI map and target Plate."""
        sec = CollapsibleSection("Live Split Preview", collapsed=False)
        self.grp_preview = sec
        self._collapsible_sections.append(sec)
        lay = QtWidgets.QVBoxLayout(sec.content_widget)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(4)

        # View Mode selector toolbar
        mode_row = QtWidgets.QHBoxLayout()
        mode_lbl = QtWidgets.QLabel("View:")
        mode_lbl.setStyleSheet("color: #aaa; font-size: 11px; font-weight: normal;")
        self.combo_preview_mode = QtWidgets.QComboBox()
        self.combo_preview_mode.addItems([
            "Side-by-Side Split",
            "Wipe Comparison (Interactive Split)",
            "HDRI Only",
            "Plate Only"
        ])
        self.btn_popout_preview = QtWidgets.QPushButton("⧉ Pop Out")
        self.btn_popout_preview.setToolTip("Open large resizable preview inspector (or double-click any preview image)")
        self.btn_popout_preview.setStyleSheet(
            "QPushButton { background-color: #24272c; color: #bbb; border: 1px solid #363a42; "
            "border-radius: 3px; padding: 2px 8px; font-size: 11px; font-weight: bold; } "
            "QPushButton:hover { background-color: #2e3239; color: #fff; border-color: #555; }"
        )
        self.btn_popout_preview.clicked.connect(lambda: self._open_large_preview())

        mode_row.addWidget(mode_lbl)
        mode_row.addWidget(self.combo_preview_mode, 1)
        mode_row.addWidget(self.btn_popout_preview, 0)
        lay.addLayout(mode_row)

        # Stacked widget for preview layouts
        self.preview_stack = QtWidgets.QStackedWidget()

        # Page 0: Side-by-Side Split
        sbs_widget = QtWidgets.QWidget()
        sbs_layout = QtWidgets.QHBoxLayout(sbs_widget)
        sbs_layout.setContentsMargins(0, 0, 0, 0)
        sbs_layout.setSpacing(6)

        # Left: HDRI Card
        hdri_card = QtWidgets.QVBoxLayout()
        hdri_card.setSpacing(2)
        lbl_h_title = QtWidgets.QLabel("HDRI Map")
        lbl_h_title.setStyleSheet("color: #ffaa00; font-weight: bold; font-size: 11px;")
        lbl_h_title.setAlignment(QtCore.Qt.AlignCenter)
        self.lbl_hdri_preview = DropLabel("No HDRI loaded (Drop here)")
        self.lbl_hdri_preview.file_dropped.connect(self._on_hdri_dropped)
        self.lbl_hdri_preview.double_clicked.connect(lambda: self._open_large_preview("hdri"))
        self.lbl_hdri_preview.setAlignment(QtCore.Qt.AlignCenter)
        self.lbl_hdri_preview.setMinimumHeight(110)
        self.lbl_hdri_preview.setMaximumHeight(140)
        self.lbl_hdri_preview.setStyleSheet(
            "background-color: #151515; border: 1px solid #333; border-radius: 4px; color: #777;"
        )
        self.lbl_hdri_info = QtWidgets.QLabel("---")
        self.lbl_hdri_info.setAlignment(QtCore.Qt.AlignCenter)
        self.lbl_hdri_info.setStyleSheet("color: #888; font-size: 10px;")
        hdri_card.addWidget(lbl_h_title)
        hdri_card.addWidget(self.lbl_hdri_preview, 1)
        hdri_card.addWidget(self.lbl_hdri_info)
        sbs_layout.addLayout(hdri_card, 1)

        # Right: Plate Card
        plate_card = QtWidgets.QVBoxLayout()
        plate_card.setSpacing(2)
        lbl_p_title = QtWidgets.QLabel("Target Plate")
        lbl_p_title.setStyleSheet("color: #00bbff; font-weight: bold; font-size: 11px;")
        lbl_p_title.setAlignment(QtCore.Qt.AlignCenter)
        self.lbl_plate_preview = DropLabel("No Plate loaded (Drop here)")
        self.lbl_plate_preview.file_dropped.connect(self._on_plate_dropped)
        self.lbl_plate_preview.double_clicked.connect(lambda: self._open_large_preview("plate"))
        self.lbl_plate_preview.setAlignment(QtCore.Qt.AlignCenter)
        self.lbl_plate_preview.setMinimumHeight(110)
        self.lbl_plate_preview.setMaximumHeight(140)
        self.lbl_plate_preview.setStyleSheet(
            "background-color: #151515; border: 1px solid #333; border-radius: 4px; color: #777;"
        )
        self.lbl_plate_info = QtWidgets.QLabel("---")
        self.lbl_plate_info.setAlignment(QtCore.Qt.AlignCenter)
        self.lbl_plate_info.setStyleSheet("color: #888; font-size: 10px;")
        plate_card.addWidget(lbl_p_title)
        plate_card.addWidget(self.lbl_plate_preview, 1)
        plate_card.addWidget(self.lbl_plate_info)
        sbs_layout.addLayout(plate_card, 1)

        self.preview_stack.addWidget(sbs_widget)

        # Page 1: Wipe Comparison (Interactive Split Slider)
        wipe_widget = QtWidgets.QWidget()
        wipe_layout = QtWidgets.QVBoxLayout(wipe_widget)
        wipe_layout.setContentsMargins(0, 0, 0, 0)
        wipe_layout.setSpacing(3)

        self.lbl_wipe_preview = QtWidgets.QLabel("Load HDRI and Plate for Split Wipe")
        self.lbl_wipe_preview.setAlignment(QtCore.Qt.AlignCenter)
        self.lbl_wipe_preview.setMinimumHeight(110)
        self.lbl_wipe_preview.setMaximumHeight(145)
        self.lbl_wipe_preview.setCursor(QtCore.Qt.PointingHandCursor)
        self.lbl_wipe_preview.setToolTip("Double-click to open large Split Wipe inspector")
        self.lbl_wipe_preview.setStyleSheet(
            "background-color: #151515; border: 1px solid #ffaa00; border-radius: 4px; color: #888;"
        )
        self.lbl_wipe_preview.mouseDoubleClickEvent = lambda e: self._open_large_preview("wipe")
        wipe_layout.addWidget(self.lbl_wipe_preview, 1)

        wipe_ctrl = QtWidgets.QHBoxLayout()
        wipe_ctrl.setSpacing(4)
        lbl_w_h = QtWidgets.QLabel("HDRI")
        lbl_w_h.setStyleSheet("color: #ffaa00; font-size: 10px; font-weight: bold;")
        self.sld_wipe = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.sld_wipe.setRange(0, 100)
        self.sld_wipe.setValue(50)
        self.sld_wipe.setStyleSheet("QSlider::handle:horizontal { background: #ffaa00; }")
        lbl_w_p = QtWidgets.QLabel("Plate")
        lbl_w_p.setStyleSheet("color: #00bbff; font-size: 10px; font-weight: bold;")
        self.lbl_wipe_ratio = QtWidgets.QLabel("50% | 50%")
        self.lbl_wipe_ratio.setStyleSheet("color: #aaa; font-size: 10px; min-width: 55px;")
        self.lbl_wipe_ratio.setAlignment(QtCore.Qt.AlignCenter)

        wipe_ctrl.addWidget(lbl_w_h)
        wipe_ctrl.addWidget(self.sld_wipe, 1)
        wipe_ctrl.addWidget(lbl_w_p)
        wipe_ctrl.addWidget(self.lbl_wipe_ratio)
        wipe_layout.addLayout(wipe_ctrl)

        self.preview_stack.addWidget(wipe_widget)

        # Page 2: Single Full HDRI Preview
        single_h_widget = QtWidgets.QWidget()
        sh_lay = QtWidgets.QVBoxLayout(single_h_widget)
        sh_lay.setContentsMargins(0, 0, 0, 0)
        self.lbl_single_hdri = QtWidgets.QLabel("No HDRI loaded")
        self.lbl_single_hdri.setAlignment(QtCore.Qt.AlignCenter)
        self.lbl_single_hdri.setMinimumHeight(110)
        self.lbl_single_hdri.setMaximumHeight(150)
        self.lbl_single_hdri.setCursor(QtCore.Qt.PointingHandCursor)
        self.lbl_single_hdri.setToolTip("Double-click to open large HDRI inspector")
        self.lbl_single_hdri.setStyleSheet("background-color: #151515; border: 1px solid #333; border-radius: 4px; color: #777;")
        self.lbl_single_hdri.mouseDoubleClickEvent = lambda e: self._open_large_preview("hdri")
        sh_lay.addWidget(self.lbl_single_hdri)
        self.preview_stack.addWidget(single_h_widget)

        # Page 3: Single Full Plate Preview
        single_p_widget = QtWidgets.QWidget()
        sp_lay = QtWidgets.QVBoxLayout(single_p_widget)
        sp_lay.setContentsMargins(0, 0, 0, 0)
        self.lbl_single_plate = QtWidgets.QLabel("No Plate loaded")
        self.lbl_single_plate.setAlignment(QtCore.Qt.AlignCenter)
        self.lbl_single_plate.setMinimumHeight(110)
        self.lbl_single_plate.setMaximumHeight(150)
        self.lbl_single_plate.setCursor(QtCore.Qt.PointingHandCursor)
        self.lbl_single_plate.setToolTip("Double-click to open large Plate inspector")
        self.lbl_single_plate.setStyleSheet("background-color: #151515; border: 1px solid #333; border-radius: 4px; color: #777;")
        self.lbl_single_plate.mouseDoubleClickEvent = lambda e: self._open_large_preview("plate")
        sp_lay.addWidget(self.lbl_single_plate)
        self.preview_stack.addWidget(single_p_widget)

        lay.addWidget(self.preview_stack)
        self._scroll_layout.addWidget(sec)

    # ------------------------------------------------------------------
    # Signal connections
    # ------------------------------------------------------------------

    def _apply_input_colorspace(self, notify_ui=False):
        """Transform raw HDRI and Plate arrays using selected Color Spaces into Linear scene space."""
        hdri_cs = self.combo_input_cs.currentText().strip() if hasattr(self, 'combo_input_cs') else "Linear"
        plate_cs = self.combo_plate_cs.currentText().strip() if hasattr(self, 'combo_plate_cs') else "sRGB"
        try:
            import numpy as np
            import os
            from hdri_match.core.colorspace import ColorSpaceManager
            if not hasattr(self, '_csm') or self._csm is None:
                self._csm = ColorSpaceManager()

            # 1. Transform HDRI thumbnail & full array
            if hasattr(self, '_hdri_orig_thumb') and self._hdri_orig_thumb is not None:
                self._hdri_thumb_raw = self._csm.transform_image(
                    self._hdri_orig_thumb.copy(), hdri_cs, "Linear"
                )
                base_luma = 0.2126 * self._hdri_thumb_raw[..., 0] + 0.7152 * self._hdri_thumb_raw[..., 1] + 0.0722 * self._hdri_thumb_raw[..., 2]
                p98 = float(np.percentile(base_luma, 98))
                self._hdri_base_norm = (0.9 / max(1e-5, p98))

            if hasattr(self, '_hdri_orig_full') and self._hdri_orig_full is not None:
                self._hdri_full_array = self._csm.transform_image(
                    self._hdri_orig_full.copy(), hdri_cs, "Linear"
                )

            # 2. Transform Plate thumbnail & full array
            if hasattr(self, '_plate_orig_thumb') and self._plate_orig_thumb is not None:
                self._plate_thumb_raw = self._csm.transform_image(
                    self._plate_orig_thumb.copy(), plate_cs, "Linear"
                )

            if hasattr(self, '_plate_orig_full') and self._plate_orig_full is not None:
                self._plate_full_array = self._csm.transform_image(
                    self._plate_orig_full.copy(), plate_cs, "Linear"
                )

            # 3. Refresh UI previews immediately
            self._update_all_previews()

            # 4. Re-run calibration if Auto-Calibrate is checked and both images are present (not restoring)
            if not getattr(self, '_restoring_state', False):
                if hasattr(self, 'chk_auto_cal') and self.chk_auto_cal.isChecked():
                    hdri_path = self.txt_hdri.text().strip()
                    plate_path = self.txt_plate.text().strip()
                    if hdri_path and os.path.isfile(hdri_path) and plate_path and os.path.isfile(plate_path):
                        self.log(f"Color spaces updated (HDRI: '{hdri_cs}', Plate: '{plate_cs}'): re-running calibration...", "INFO")
                        self._run_calibration()
                elif hasattr(self, 'chk_auto_bake') and self.chk_auto_bake.isChecked():
                    self._schedule_auto_bake()

            self._sync_node()
            if notify_ui:
                self.log(f"Color Spaces applied: HDRI ({hdri_cs}) | Plate ({plate_cs})", "SUCCESS")
        except Exception as e:
            self.log_error(f"Error applying color spaces (HDRI: '{hdri_cs}', Plate: '{plate_cs}')", e)

    def _on_input_cs_changed(self, idx):
        """Handle HDRI Input Space dropdown change."""
        if getattr(self, '_restoring_state', False):
            return
        self._apply_input_colorspace(notify_ui=True)

    def _on_plate_cs_changed(self, idx):
        """Handle Plate Space dropdown change."""
        if getattr(self, '_restoring_state', False):
            return
        self._apply_input_colorspace(notify_ui=True)

    def _on_sky_mode_changed(self, idx):
        """Re-run calibration if Auto-Calibrate is checked when Sky Mode changes."""
        self._sync_node()
        if hasattr(self, 'chk_auto_cal') and self.chk_auto_cal.isChecked():
            hdri_path = self.txt_hdri.text().strip()
            plate_path = self.txt_plate.text().strip()
            if hdri_path and os.path.isfile(hdri_path) and plate_path and os.path.isfile(plate_path):
                mode_label = self.combo_sky_mode.currentText()
                self.log(f"Sky Mode changed to '{mode_label}': re-running calibration...", "INFO")
                self._run_calibration()


    # ------------------------------------------------------------------
    # Crucible Light Studio Integration Methods
    # ------------------------------------------------------------------

    def _build_crucible_section(self):
        """Build the embedded Crucible Light Studio panel container."""
        self._crucible_container = QtWidgets.QFrame()
        self._crucible_container.setFrameShape(QtWidgets.QFrame.StyledPanel)
        self._crucible_container.setStyleSheet(
            "QFrame { background: #18191c; border-left: 1px solid #333; }"
        )
        c_layout = QtWidgets.QVBoxLayout(self._crucible_container)
        c_layout.setContentsMargins(4, 4, 4, 4)
        c_layout.setSpacing(4)

        # Pop Out button directly on Crucible Header
        self.btn_popout_crucible = QtWidgets.QPushButton("⧉ Pop Out")
        self.btn_popout_crucible.setToolTip("Open HDRI Library in a floating window")
        self.btn_popout_crucible.setStyleSheet(
            "QPushButton { background-color: #2a2a2a; color: #bbb; border: 1px solid #444; "
            "border-radius: 3px; padding: 2px 8px; font-size: 11px; } "
            "QPushButton:hover { background-color: #3a3a3a; color: #fff; border-color: #666; }"
        )
        self.btn_popout_crucible.clicked.connect(self._popout_crucible_window)

        # Hidden stubs for compatibility
        self.btn_crucible_merge = QtWidgets.QPushButton("🔀 Merge Flow")
        self.btn_crucible_merge.hide()
        self.btn_close_crucible = QtWidgets.QPushButton("✕")
        self.btn_close_crucible.hide()

        # Embedded Crucible Panel (Single unified header)
        self._crucible_panel = None
        self._floating_crucible_win = None
        try:
            _ensure_crucible_environment()
            from CrucibleLightStudio.ui.main_panel import CrucibleLightStudioPanel
            self._crucible_panel = CrucibleLightStudioPanel()
            if hasattr(self._crucible_panel, "add_header_widget"):
                self._crucible_panel.add_header_widget(self.btn_popout_crucible)
            elif hasattr(self._crucible_panel, "_header_layout") and self._crucible_panel._header_layout:
                self._crucible_panel._header_layout.addWidget(self.btn_popout_crucible)
            c_layout.addWidget(self._crucible_panel, 1)
            self.log("HDRI Library integrated successfully into side panel.", "SUCCESS")
        except Exception as e:
            err_msg = QtWidgets.QLabel(f"Failed to load Crucible Light Studio:\n{e}")
            err_msg.setStyleSheet("color: #ff6666; background: #2a1111; padding: 8px; border-radius: 4px;")
            err_msg.setWordWrap(True)
            c_layout.addWidget(err_msg, 1)
            self.log(f"Crucible Light Studio load error: {e}", "ERROR")

        # Initially hidden by default
        self._crucible_container.hide()

    def _toggle_crucible_panel(self):
        """Toggle visibility of Crucible side panel."""
        is_visible = not self._crucible_container.isVisible()
        self._set_crucible_visible(is_visible)

    def _set_crucible_visible(self, visible):
        """Set visibility of Crucible panel and update button state."""
        self._crucible_container.setVisible(visible)
        self.btn_toggle_crucible.setChecked(visible)
        if visible:
            self.setMinimumWidth(850)
            total_w = max(self.width(), 950)
            self.main_splitter.setSizes([450, total_w - 450])
            if self._crucible_panel and hasattr(self._crucible_panel, "light_manager"):
                try:
                    self._crucible_panel.light_manager.refresh_lights()
                except Exception:
                    pass
        else:
            self.setMinimumWidth(380)

    def _popout_crucible_window(self):
        """Pop out Crucible Studio into a separate floating window or dock back in."""
        if self._floating_crucible_win and self._floating_crucible_win.isVisible():
            self._dock_in_crucible()
        else:
            self._undock_crucible()

    def _undock_crucible(self):
        if not self._crucible_panel:
            return
        if not self._floating_crucible_win:
            parent_w = hou.qt.mainWindow() if (hou and hasattr(hou, "qt")) else None
            self._floating_crucible_win = QtWidgets.QDialog(parent_w)
            self._floating_crucible_win.setWindowTitle("HDRI Library - Floating")
            self._floating_crucible_win.resize(900, 700)
            fw_layout = QtWidgets.QVBoxLayout(self._floating_crucible_win)
            fw_layout.setContentsMargins(4, 4, 4, 4)

            dock_bar = QtWidgets.QHBoxLayout()
            btn_dock = QtWidgets.QPushButton("⬇ Dock into HDRI Match Panel")
            btn_dock.setStyleSheet(
                "font-weight: bold; padding: 4px 12px; background: #274768; color: #00d2ff; border-radius: 4px;"
            )
            btn_dock.clicked.connect(self._dock_in_crucible)
            dock_bar.addWidget(btn_dock)
            dock_bar.addStretch()
            fw_layout.addLayout(dock_bar)

            self._crucible_fw_layout = fw_layout
            self._floating_crucible_win.finished.connect(lambda res: self._dock_in_crucible())

        self._crucible_container.layout().removeWidget(self._crucible_panel)
        self._crucible_fw_layout.addWidget(self._crucible_panel, 1)
        self._crucible_container.hide()
        self.btn_popout_crucible.setText("⬇ Dock In")
        self._floating_crucible_win.show()
        self._floating_crucible_win.raise_()

    def _dock_in_crucible(self):
        if not self._crucible_panel or not self._floating_crucible_win:
            return
        self._crucible_fw_layout.removeWidget(self._crucible_panel)
        self._crucible_container.layout().addWidget(self._crucible_panel, 1)
        self._floating_crucible_win.hide()
        self.btn_popout_crucible.setText("⧉ Pop Out")
        self._set_crucible_visible(True)

    # ------------------------------------------------------------------
    # HDRI Room Analyzer Tab & Workflow
    # ------------------------------------------------------------------

    def _build_hdri_room_analyzer_tab(self, parent_layout):
        """Construct the 360 HDRI Room Dimension Analyzer tab layout."""
        # Top banner / intro
        intro_card = QtWidgets.QFrame()
        intro_card.setStyleSheet(
            "QFrame { background-color: #1e222a; border: 1px solid #2d3340; border-radius: 6px; padding: 6px; }"
        )
        intro_lay = QtWidgets.QVBoxLayout(intro_card)
        intro_lay.setContentsMargins(6, 6, 6, 6)
        intro_lay.setSpacing(4)

        title_lbl = QtWidgets.QLabel("📐 360° HDRI Room Dimension Analyzer")
        title_lbl.setStyleSheet("font-size: 14px; font-weight: bold; color: #00d2ff;")
        intro_lay.addWidget(title_lbl)

        desc_lbl = QtWidgets.QLabel(
            "Automatically detects 3D room boundaries (Floor, Ceiling, 4 Wall Corners) and solves exact "
            "physical metric dimensions (Width, Depth, Height, Camera Offsets, and Orientation) directly from "
            "the 2D equirectangular panorama.\n"
            "• Scale is anchored to camera tripod height.\n"
            "• Completely non-destructive until you click 'Apply to DomeBreaker Room Box'."
        )
        desc_lbl.setWordWrap(True)
        desc_lbl.setStyleSheet("color: #abb2bf; font-size: 11px; line-height: 1.4;")
        intro_lay.addWidget(desc_lbl)
        parent_layout.addWidget(intro_card)

        # 1. Camera Scale Anchor Group
        grp_anchor = QtWidgets.QGroupBox("1. Metric Scale Anchor (Camera / Tripod Height)")
        grp_anchor.setStyleSheet(
            "QGroupBox { font-weight: bold; color: #ffcc44; border: 1px solid #3d4452; "
            "border-radius: 5px; margin-top: 8px; padding-top: 14px; background: #1a1c22; }"
            "QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; }"
        )
        anchor_lay = QtWidgets.QFormLayout(grp_anchor)
        anchor_lay.setContentsMargins(8, 12, 8, 8)
        anchor_lay.setSpacing(6)

        self.sld_ana_tripod_height = SliderDoubleSpinBox(0.4, 5.0, 0.05, 1.50, decimals=2)
        self.sld_ana_tripod_height.setToolTip(
            "Fundamental metric scale anchor: Known or estimated optical center height of the 360° camera above the floor in meters."
        )
        anchor_lay.addRow("Tripod Height (m):", self.sld_ana_tripod_height)

        # Preset buttons row
        pre_row = QtWidgets.QHBoxLayout()
        pre_row.setSpacing(4)
        for label, val in [("Seated (1.1m)", 1.10), ("Tripod (1.5m)", 1.50), ("Eye-Level (1.65m)", 1.65), ("Tall (1.85m)", 1.85)]:
            btn_pre = QtWidgets.QPushButton(label)
            btn_pre.setStyleSheet(
                "QPushButton { background: #252830; color: #ddd; border: 1px solid #3d4452; "
                "border-radius: 3px; padding: 3px 6px; font-size: 11px; } "
                "QPushButton:hover { background: #323845; color: #fff; border-color: #00d2ff; }"
            )
            btn_pre.clicked.connect(lambda _, v=val: self.sld_ana_tripod_height.setValue(v))
            pre_row.addWidget(btn_pre)
        anchor_lay.addRow("Quick Presets:", pre_row)

        self.sld_ana_yaw_offset = SliderDoubleSpinBox(-180.0, 180.0, 1.0, 0.0, decimals=1)
        self.sld_ana_yaw_offset.setToolTip("User orientation fine-tune angle in degrees.")
        anchor_lay.addRow("Yaw Fine-Tune (°):", self.sld_ana_yaw_offset)
        parent_layout.addWidget(grp_anchor)

        # 2. Action & Status Section
        grp_action = QtWidgets.QGroupBox("2. Automatic Boundary Extraction")
        grp_action.setStyleSheet(
            "QGroupBox { font-weight: bold; color: #ffcc44; border: 1px solid #3d4452; "
            "border-radius: 5px; margin-top: 8px; padding-top: 14px; background: #1a1c22; }"
            "QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; }"
        )
        act_lay = QtWidgets.QVBoxLayout(grp_action)
        act_lay.setContentsMargins(8, 12, 8, 8)
        act_lay.setSpacing(6)

        act_btn_row = QtWidgets.QHBoxLayout()
        act_btn_row.setSpacing(6)

        self.btn_analyze_hdri_room = QtWidgets.QPushButton("🔍 Analyze Room Boundaries")
        self.btn_analyze_hdri_room.setStyleSheet(
            "QPushButton { background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #0088b3, stop:1 #005573); "
            "color: #ffffff; font-weight: bold; font-size: 12px; border: 1px solid #00bbee; "
            "border-radius: 5px; padding: 8px 12px; } "
            "QPushButton:hover { background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #00a0d4, stop:1 #006b91); border-color: #33d4ff; } "
            "QPushButton:pressed { background: #00445c; }"
        )
        self.btn_analyze_hdri_room.clicked.connect(self._on_analyze_hdri_room_clicked)
        act_btn_row.addWidget(self.btn_analyze_hdri_room, 1)

        self.btn_auto_align_room = QtWidgets.QPushButton("📐 Auto-Align Room to HDRI")
        self.btn_auto_align_room.setStyleSheet(
            "QPushButton { background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #2980b9, stop:1 #1a5276); "
            "color: #ffffff; font-weight: bold; font-size: 12px; border: 1px solid #3498db; "
            "border-radius: 5px; padding: 8px 12px; } "
            "QPushButton:hover { background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #3498db, stop:1 #2471a3); border-color: #5dade2; } "
            "QPushButton:pressed { background: #154360; }"
        )
        self.btn_auto_align_room.setToolTip("Analyze the panorama and auto-align room dimensions and yaw orientation in the scene directly.")
        self.btn_auto_align_room.clicked.connect(self._on_auto_align_room_to_hdri)
        act_btn_row.addWidget(self.btn_auto_align_room, 1)

        act_lay.addLayout(act_btn_row)

        status_row = QtWidgets.QHBoxLayout()
        self.lbl_ana_status = QtWidgets.QLabel("Ready. Load an HDRI map and click Analyze or Auto-Align.")
        self.lbl_ana_status.setStyleSheet("color: #9da5b4; font-size: 11px;")
        self.lbl_ana_confidence = QtWidgets.QLabel("Confidence: —")
        self.lbl_ana_confidence.setStyleSheet("color: #00ffaa; font-weight: bold; font-size: 11px;")
        status_row.addWidget(self.lbl_ana_status, 1)
        status_row.addWidget(self.lbl_ana_confidence, 0)
        act_lay.addLayout(status_row)
        parent_layout.addWidget(grp_action)

        # 3. Visual Analysis Preview Section
        grp_preview = QtWidgets.QGroupBox("3. Visual Boundary Overlay Preview")
        grp_preview.setStyleSheet(
            "QGroupBox { font-weight: bold; color: #ffcc44; border: 1px solid #3d4452; "
            "border-radius: 5px; margin-top: 8px; padding-top: 14px; background: #1a1c22; }"
            "QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; }"
        )
        prev_lay = QtWidgets.QVBoxLayout(grp_preview)
        prev_lay.setContentsMargins(8, 12, 8, 8)
        prev_lay.setSpacing(6)

        self.lbl_ana_preview = ClickablePreviewLabel(
            "Boundary overlay preview will appear here upon analysis\n(💡 Double-click to open enlarged screen view)"
        )
        self.lbl_ana_preview.setAlignment(QtCore.Qt.AlignCenter)
        self.lbl_ana_preview.setMinimumHeight(180)
        self.lbl_ana_preview.setMaximumHeight(260)
        self.lbl_ana_preview.setStyleSheet(
            "QLabel { background-color: #0e1014; border: 1px solid #282c34; border-radius: 4px; color: #5c6370; font-size: 11px; }"
            "QLabel:hover { border-color: #ffcc44; }"
        )
        self.lbl_ana_preview.double_clicked.connect(self._open_large_boundary_overlay)
        prev_lay.addWidget(self.lbl_ana_preview)

        # Color legend & Enlarge button
        legend_row = QtWidgets.QHBoxLayout()
        legend_row.setSpacing(8)
        legend_items = [
            ("■ Floor", "#00e5ff"),
            ("■ Ceiling", "#ffb700"),
            ("■ 4 Corners", "#ff007f"),
            ("┅ Horizon", "#bbbbbb"),
        ]
        for name, col in legend_items:
            lbl_leg = QtWidgets.QLabel(name)
            lbl_leg.setStyleSheet(f"color: {col}; font-weight: bold; font-size: 11px;")
            legend_row.addWidget(lbl_leg)
        legend_row.addStretch()

        self.btn_ana_enlarge = QtWidgets.QPushButton("⛶ Enlarge View")
        self.btn_ana_enlarge.setToolTip("Open large floating screen view of the boundary overlay (or double-click the preview)")
        self.btn_ana_enlarge.setStyleSheet(
            "QPushButton { background-color: #2c313a; color: #abb2bf; border: 1px solid #3e4451; "
            "border-radius: 3px; padding: 2px 8px; font-size: 10px; font-weight: bold; }"
            "QPushButton:hover { background-color: #3e4451; color: #fff; border-color: #ffcc44; }"
        )
        self.btn_ana_enlarge.clicked.connect(self._open_large_boundary_overlay)
        legend_row.addWidget(self.btn_ana_enlarge)

        prev_lay.addLayout(legend_row)
        parent_layout.addWidget(grp_preview)

        # 4. Solved Dimensions & Fine-Tuning Section
        grp_dims = QtWidgets.QGroupBox("4. Solved Dimensions & Fine-Tuning (Meters & Yaw)")
        grp_dims.setStyleSheet(
            "QGroupBox { font-weight: bold; color: #ffcc44; border: 1px solid #3d4452; "
            "border-radius: 5px; margin-top: 8px; padding-top: 14px; background: #1a1c22; }"
            "QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; }"
        )
        dims_lay = QtWidgets.QFormLayout(grp_dims)
        dims_lay.setContentsMargins(8, 12, 8, 8)
        dims_lay.setSpacing(6)

        self.sld_ana_width = SliderDoubleSpinBox(1.0, 100.0, 0.1, 8.0, decimals=2)
        self.sld_ana_width.setToolTip("Solved Room dimension along X (Width) in meters.")
        dims_lay.addRow("Room Width (m) [X]:", self.sld_ana_width)

        self.sld_ana_depth = SliderDoubleSpinBox(1.0, 100.0, 0.1, 10.0, decimals=2)
        self.sld_ana_depth.setToolTip("Solved Room dimension along Z (Depth) in meters.")
        dims_lay.addRow("Room Depth (m) [Z]:", self.sld_ana_depth)

        self.sld_ana_height = SliderDoubleSpinBox(1.0, 30.0, 0.1, 3.5, decimals=2)
        self.sld_ana_height.setToolTip("Solved Floor-to-Ceiling Room Height (Y) in meters.")
        dims_lay.addRow("Room Height (m) [Y]:", self.sld_ana_height)

        self.sld_ana_cam_x = SliderDoubleSpinBox(-50.0, 50.0, 0.1, 0.0, decimals=2)
        self.sld_ana_cam_x.setToolTip("Lateral camera offset from room center along X in meters.")
        dims_lay.addRow("Camera Offset X (m):", self.sld_ana_cam_x)

        self.sld_ana_cam_z = SliderDoubleSpinBox(-50.0, 50.0, 0.1, 0.0, decimals=2)
        self.sld_ana_cam_z.setToolTip("Forward/back camera offset from room center along Z in meters.")
        dims_lay.addRow("Camera Offset Z (m):", self.sld_ana_cam_z)

        self.sld_ana_yaw = SliderDoubleSpinBox(0.0, 360.0, 1.0, 0.0, decimals=1)
        self.sld_ana_yaw.setToolTip("Detected room orientation (yaw) angle in degrees to align walls with HDRI panorama.")
        dims_lay.addRow("Room Yaw / Alignment (°):", self.sld_ana_yaw)

        parent_layout.addWidget(grp_dims)

        # Connect live slider adjustments to interactive overlay update
        self.sld_ana_tripod_height.valueChanged.connect(self._on_analyzer_slider_changed)
        self.sld_ana_yaw_offset.valueChanged.connect(self._on_analyzer_slider_changed)
        self.sld_ana_width.valueChanged.connect(self._on_analyzer_slider_changed)
        self.sld_ana_depth.valueChanged.connect(self._on_analyzer_slider_changed)
        self.sld_ana_height.valueChanged.connect(self._on_analyzer_slider_changed)
        self.sld_ana_yaw.valueChanged.connect(self._on_analyzer_slider_changed)

        # 5. Apply to DomeBreaker Room Box Action
        grp_apply = QtWidgets.QGroupBox("5. Apply to Solaris Scene")
        grp_apply.setStyleSheet(
            "QGroupBox { font-weight: bold; color: #ffcc44; border: 1px solid #3d4452; "
            "border-radius: 5px; margin-top: 8px; padding-top: 14px; background: #1a1c22; }"
            "QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; }"
        )
        apply_lay = QtWidgets.QVBoxLayout(grp_apply)
        apply_lay.setContentsMargins(8, 12, 8, 8)
        apply_lay.setSpacing(8)

        # 3-Step Workflow Guidance Banner
        banner_guide = QtWidgets.QFrame()
        banner_guide.setStyleSheet(
            "QFrame { background: #1f232b; border: 1px solid #e67e22; border-radius: 6px; padding: 8px; }"
        )
        banner_lay = QtWidgets.QVBoxLayout(banner_guide)
        banner_lay.setContentsMargins(8, 8, 8, 8)
        banner_lay.setSpacing(4)

        lbl_guide_title = QtWidgets.QLabel("📋 Required Workflow Order for Perfect Alignment:")
        lbl_guide_title.setStyleSheet("font-weight: bold; color: #ffaa00; font-size: 11px;")
        banner_lay.addWidget(lbl_guide_title)

        lbl_guide_steps = QtWidgets.QLabel(
            "<b>Step 1:</b> Click <b>🔍 Analyze Room Boundaries</b> (solves physical room dimensions).<br>"
            "<b>Step 2:</b> Click <b>📐 Auto-Align Room to HDRI</b> (computes true camera offset & orientation).<br>"
            "<b>Step 3:</b> Click <b>👉 Apply Solved Dimensions & Align Room Box</b> below to transfer to Solaris & snap practical lights!"
        )
        lbl_guide_steps.setStyleSheet("color: #dcdfe4; font-size: 11px; line-height: 145%;")
        banner_lay.addWidget(lbl_guide_steps)
        apply_lay.addWidget(banner_guide)

        self.btn_apply_to_room_box = QtWidgets.QPushButton("👉 Apply Solved Dimensions & Align Room Box")
        self.btn_apply_to_room_box.setEnabled(True)
        self.btn_apply_to_room_box.setToolTip(
            "Step 3: Transfers the solved Width, Depth, Height, Camera Offsets, and Tripod Height directly into "
            "DomeBreaker, activates Interior Room Box mode, and snaps all practical lights directly to room surfaces.\n\n"
            "Note: Make sure to run Step 1 (Analyze) and Step 2 (Auto-Align) first!"
        )
        self.btn_apply_to_room_box.setStyleSheet(
            "QPushButton { background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #d35400, stop:1 #963b00); "
            "color: #ffffff; font-weight: bold; font-size: 13px; border: 1px solid #e67e22; "
            "border-radius: 5px; padding: 10px 18px; } "
            "QPushButton:hover { background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #e67e22, stop:1 #b34700); border-color: #f39c12; } "
            "QPushButton:disabled { background: #2b2b2b; color: #666; border-color: #3a3a3a; } "
            "QPushButton:pressed { background: #7a3000; }"
        )
        self.btn_apply_to_room_box.clicked.connect(self._apply_analyzer_to_room_box)
        apply_lay.addWidget(self.btn_apply_to_room_box)

        # Quick navigation button to DomeBreaker tab
        self.btn_goto_dome_tab = QtWidgets.QPushButton("⚡ View in DomeBreaker Tab")
        self.btn_goto_dome_tab.setStyleSheet(
            "QPushButton { background: #252830; color: #ffcc44; font-weight: bold; "
            "border: 1px solid #3d4452; border-radius: 4px; padding: 5px 10px; font-size: 11px; } "
            "QPushButton:hover { background: #323845; color: #ffe066; border-color: #ffaa00; }"
        )
        self.btn_goto_dome_tab.clicked.connect(lambda: self.tabs_main.setCurrentIndex(0))
        apply_lay.addWidget(self.btn_goto_dome_tab)

        apply_tip = QtWidgets.QLabel(
            "Transfers the solved Width, Depth, Height, Camera Offsets, and Tripod Height directly into the "
            "DomeBreaker Room Box controls, automatically activates Interior Room Box mode, and updates the "
            "Solaris USD projection prim at /stage/hdri_match_projection."
        )
        apply_tip.setWordWrap(True)
        apply_tip.setStyleSheet("color: #7f848e; font-size: 10px;")
        apply_lay.addWidget(apply_tip)
        parent_layout.addWidget(grp_apply)

    def _on_analyze_hdri_room_clicked(self):
        """Run Manhattan-world 360 HDRI room analysis."""
        img_input = None
        if getattr(self, '_hdri_orig_full', None) is not None:
            img_input = self._hdri_orig_full
        else:
            path = self.txt_hdri.text().strip() if hasattr(self, 'txt_hdri') else ""
            if path:
                path_exp = _resolve_image_path(path)
                if os.path.isfile(path_exp):
                    img_input = path_exp

        if img_input is None:
            self.lbl_ana_status.setText("⚠️ Please load an HDRI map first.")
            self.log("HDRI Room Analyzer: No valid HDRI loaded to analyze.", "WARNING")
            return

        tripod_h = self.sld_ana_tripod_height.value()
        yaw_off = self.sld_ana_yaw_offset.value()

        self.lbl_ana_status.setText("Analyzing equirectangular panorama...")
        QtWidgets.QApplication.processEvents()

        try:
            from hdri_match_solaris import hdri_room_analyzer as _ana
            res = _ana.analyze_hdri_room(img_input, tripod_height=tripod_h, user_yaw_offset=yaw_off)
            self._ana_last_result = res
            self._ana_last_img_input = img_input

            # Update sliders without triggering repetitive overlay recomputation
            self.sld_ana_width.blockSignals(True)
            self.sld_ana_depth.blockSignals(True)
            self.sld_ana_height.blockSignals(True)
            self.sld_ana_cam_x.blockSignals(True)
            self.sld_ana_cam_z.blockSignals(True)

            self.sld_ana_width.setValue(res["room_width"])
            self.sld_ana_depth.setValue(res["room_depth"])
            self.sld_ana_height.setValue(res["room_height"])
            self.sld_ana_cam_x.setValue(res["cam_offset_x"])
            self.sld_ana_cam_z.setValue(res["cam_offset_z"])

            self.sld_ana_width.blockSignals(False)
            self.sld_ana_depth.blockSignals(False)
            self.sld_ana_height.blockSignals(False)
            self.sld_ana_cam_x.blockSignals(False)
            self.sld_ana_cam_z.blockSignals(False)

            conf = res.get("confidence", 85)
            self.lbl_ana_confidence.setText(f"Confidence: {conf}%")
            self.lbl_ana_status.setText(
                f"✓ Detected: {res['room_width']:.2f}m(W) × {res['room_depth']:.2f}m(D) × {res['room_height']:.2f}m(H) [Yaw: {res['yaw']:.1f}°]"
            )

            # Update overlay preview
            self._update_analyzer_overlay()
            self.btn_apply_to_room_box.setEnabled(True)

            self.log(
                f"HDRI Room Analysis complete: W={res['room_width']:.2f}m, D={res['room_depth']:.2f}m, "
                f"H={res['room_height']:.2f}m (Confidence {conf}%)",
                "SUCCESS"
            )
        except Exception as e:
            self.lbl_ana_status.setText(f"Error: {e}")
            self.log_error(f"HDRI Room Analysis failed: {traceback.format_exc()}")

    def _on_analyzer_slider_changed(self):
        """Live update visual overlay preview when sliders are adjusted."""
        if getattr(self, '_ana_last_result', None) is None:
            return
        self._update_analyzer_overlay()

    def _update_analyzer_overlay(self):
        """Render annotated visual preview with cyan floor, amber ceiling, and magenta corners."""
        img_input = getattr(self, '_ana_last_img_input', None)
        if img_input is None:
            if getattr(self, '_hdri_orig_full', None) is not None:
                img_input = self._hdri_orig_full
            elif hasattr(self, 'txt_hdri') and self.txt_hdri.text().strip():
                p = _resolve_image_path(self.txt_hdri.text().strip())
                if os.path.isfile(p):
                    img_input = p
        if img_input is None:
            return

        try:
            from hdri_match_solaris import hdri_room_analyzer as _ana
            import io

            w = self.sld_ana_width.value()
            d = self.sld_ana_depth.value()
            h = self.sld_ana_height.value()
            tripod = self.sld_ana_tripod_height.value()
            yaw = self.sld_ana_yaw_offset.value()
            if getattr(self, '_ana_last_result', None):
                yaw = (self._ana_last_result.get("yaw", 0.0) + yaw) % 360.0

            curves = _ana.compute_room_overlay_curves(
                room_width=w,
                room_depth=d,
                room_height=h,
                tripod_height=tripod,
                yaw_deg=yaw,
                num_points=1280,
            )

            res_for_overlay = {
                "floor_y_curve": curves["floor_y_curve"],
                "ceil_y_curve": curves["ceil_y_curve"],
                "corners_x_norm": curves["corners_x_norm"],
            }

            pil_overlay = _ana.generate_room_analysis_overlay(img_input, res_for_overlay, preview_w=1280, preview_h=640)
            buf = io.BytesIO()
            pil_overlay.save(buf, format="PNG")
            buf.seek(0)
            qimg = QtGui.QImage.fromData(buf.getvalue())
            pixmap = QtGui.QPixmap.fromImage(qimg)
            self._ana_last_overlay_pixmap = pixmap

            lbl_w = max(320, self.lbl_ana_preview.width() - 8)
            lbl_h = max(160, self.lbl_ana_preview.height() - 8)
            scaled = pixmap.scaled(lbl_w, lbl_h, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation)
            self.lbl_ana_preview.setPixmap(scaled)

            # Live update large preview window if open
            if hasattr(self, '_boundary_large_win') and self._boundary_large_win and self._boundary_large_win.isVisible():
                info_text = f"Room: W={w:.2f}m | D={d:.2f}m | H={h:.2f}m | Yaw={yaw:.1f}° | Tripod={tripod:.2f}m"
                self._boundary_large_win.set_overlay_pixmap(pixmap, info_text)
        except Exception:
            pass

    def _open_large_boundary_overlay(self):
        """Open the large floating inspection window for the Visual Boundary Overlay Preview."""
        if not hasattr(self, '_boundary_large_win') or self._boundary_large_win is None:
            self._boundary_large_win = HDRIBoundaryLargeViewDialog(self)

        self._update_analyzer_overlay()
        if hasattr(self, '_ana_last_overlay_pixmap') and self._ana_last_overlay_pixmap:
            w = self.sld_ana_width.value()
            d = self.sld_ana_depth.value()
            h = self.sld_ana_height.value()
            tripod = self.sld_ana_tripod_height.value()
            yaw = self.sld_ana_yaw_offset.value()
            if getattr(self, '_ana_last_result', None):
                yaw = (self._ana_last_result.get("yaw", 0.0) + yaw) % 360.0
            info_text = f"Room: W={w:.2f}m | D={d:.2f}m | H={h:.2f}m | Yaw={yaw:.1f}° | Tripod={tripod:.2f}m"
            self._boundary_large_win.set_overlay_pixmap(self._ana_last_overlay_pixmap, info_text)
        else:
            self._boundary_large_win.set_overlay_pixmap(None, "No analysis run yet. Click 'Analyze Room Boundaries' to generate overlay.")

        self._boundary_large_win.show()
        self._boundary_large_win.raise_()
        self._boundary_large_win.activateWindow()

    def _apply_analyzer_to_room_box(self):
        """Transfer solved room dimensions and camera offsets into DomeBreaker, snapping practical lights."""
        # 0. Check that room analysis has been executed
        if getattr(self, '_ana_last_result', None) is None:
            msg = (
                "HDRI Room Analysis Required!\n\n"
                "To ensure the room box and practical lights snap to their exact physical locations, "
                "please follow the required 3-step workflow:\n\n"
                "  1. Click '🔍 Analyze Room Boundaries' (detects room dimensions)\n"
                "  2. Click '📐 Auto-Align Room to HDRI' (computes camera offsets & orientation)\n"
                "  3. Click '👉 Apply Solved Dimensions & Align Room Box'\n\n"
                "Would you like to run Step 1 and Step 2 automatically now?"
            )
            if 'hou' in sys.modules and hasattr(hou, 'isUIAvailable') and hou.isUIAvailable():
                choice = hou.ui.displayMessage(
                    msg,
                    buttons=("Run Steps 1 & 2 Now", "Cancel"),
                    severity=hou.severityType.Warning,
                    title="Room Analysis Required"
                )
                if choice == 0:
                    self.log("Step 1: Running HDRI Room Boundary Analysis...", "INFO")
                    self._on_analyze_hdri_room_clicked()
                    if getattr(self, '_ana_last_result', None) is None:
                        return
                    self.log("Step 2: Running Auto-Align Room Box...", "INFO")
                    self._on_auto_align_room_to_hdri()
                else:
                    return
            else:
                self.log(
                    "HDRI Room Analysis Required: Please run Step 1 (Analyze Room Boundaries) "
                    "and Step 2 (Auto-Align Room to HDRI) first.",
                    "WARNING"
                )
                return

        w = self.sld_ana_width.value()
        d = self.sld_ana_depth.value()
        h = self.sld_ana_height.value()
        tripod = self.sld_ana_tripod_height.value()
        off_x = self.sld_ana_cam_x.value()
        off_z = self.sld_ana_cam_z.value()

        # 1. Enable Ground & Room Projection Section
        if hasattr(self, 'grp_ground_proj'):
            self.grp_ground_proj.setChecked(True)
            if hasattr(self.grp_ground_proj, 'set_collapsed'):
                self.grp_ground_proj.set_collapsed(False)

        # 2. Switch projection mode to Interior Room Box (Cube)
        if hasattr(self, 'combo_proj_mode'):
            idx_room = self.combo_proj_mode.findText("Interior Room Box", QtCore.Qt.MatchContains)
            if idx_room >= 0:
                self.combo_proj_mode.setCurrentIndex(idx_room)

        # 3. Apply dimensions to DomeBreaker sliders
        if hasattr(self, 'sld_tripod_height'):
            self.sld_tripod_height.setValue(tripod)
        if hasattr(self, 'sld_room_width'):
            self.sld_room_width.setValue(w)
        if hasattr(self, 'sld_room_depth'):
            self.sld_room_depth.setValue(d)
        if hasattr(self, 'sld_room_height'):
            self.sld_room_height.setValue(h)
        if hasattr(self, 'sld_cam_offset_x'):
            self.sld_cam_offset_x.setValue(off_x)
        if hasattr(self, 'sld_cam_offset_z'):
            self.sld_cam_offset_z.setValue(off_z)

        if hasattr(self, 'sld_yaw'):
            self.sld_yaw.setValue(0.0)

        # 4. Sync node parameters and update stage geometry
        self._sync_node()
        self._create_or_update_ground_projection(notify_ui=True)

        self.lbl_ana_status.setText(f"✓ Applied to DomeBreaker: {w:.2f}m × {d:.2f}m × {h:.2f}m (Room Box active)")
        self.log(
            f"Transferred Room Dimensions to DomeBreaker: {w:.2f}m(W) x {d:.2f}m(D) x {h:.2f}m(H), "
            f"Tripod: {tripod:.2f}m, Offsets: ({off_x:.2f}, {off_z:.2f})m, Yaw: 0.0°.",
            "SUCCESS"
        )
        if 'hou' in sys.modules and hasattr(hou, 'isUIAvailable') and hou.isUIAvailable():
            hou.ui.displayMessage(
                f"Room Box and Practical Lights successfully aligned!\n\n"
                f"• Dimensions: {w:.2f}m (W) × {d:.2f}m (D) × {h:.2f}m (H)\n"
                f"• Camera Offset: ({off_x:.2f}m, {off_z:.2f}m)\n"
                f"• Tripod Height: {tripod:.2f}m\n\n"
                f"Practical lights are now snapped to the room surfaces matching the painted-out inpaint areas.",
                severity=hou.severityType.Message,
                title="Room Box Aligned"
            )

    def _on_auto_align_room_to_hdri(self):
        """Analyze interior HDRI and automatically orient and scale room box to match physical architecture."""
        img_input = None
        if getattr(self, '_hdri_orig_full', None) is not None:
            img_input = self._hdri_orig_full
        else:
            path = self.txt_hdri.text().strip() if hasattr(self, 'txt_hdri') else ""
            if path:
                path_exp = _resolve_image_path(path)
                if os.path.isfile(path_exp):
                    img_input = path_exp

        if img_input is None:
            self.log("Auto-Align Room: No valid HDRI loaded to analyze.", "WARNING")
            if 'hou' in sys.modules and hasattr(hou, 'isUIAvailable') and hou.isUIAvailable():
                hou.ui.displayMessage("Please load an HDRI map first before auto-aligning room box.", severity=hou.severityType.Warning)
            return

        tripod_h = float(self.sld_tripod_height.value()) if hasattr(self, 'sld_tripod_height') else 1.5
        yaw_off = float(self.sld_ana_yaw_offset.value()) if hasattr(self, 'sld_ana_yaw_offset') else 0.0

        self.log("Auto-aligning room box to interior HDRI panorama...", "INFO")
        try:
            from hdri_match_solaris import hdri_room_analyzer as _ana
            res = _ana.analyze_hdri_room(img_input, tripod_height=tripod_h, user_yaw_offset=yaw_off)
            self._ana_last_result = res
            self._ana_last_img_input = img_input

            # Enable Ground & Room Projection Section
            if hasattr(self, 'grp_ground_proj'):
                self.grp_ground_proj.setChecked(True)
                if hasattr(self.grp_ground_proj, 'set_collapsed'):
                    self.grp_ground_proj.set_collapsed(False)

            # Switch mode to room_box
            if hasattr(self, 'combo_proj_mode'):
                idx_room = self.combo_proj_mode.findText("Interior Room Box", QtCore.Qt.MatchContains)
                if idx_room >= 0:
                    self.combo_proj_mode.setCurrentIndex(idx_room)

            # Apply to room sliders
            if hasattr(self, 'sld_room_width'):
                self.sld_room_width.setValue(res["room_width"])
            if hasattr(self, 'sld_room_depth'):
                self.sld_room_depth.setValue(res["room_depth"])
            if hasattr(self, 'sld_room_height'):
                self.sld_room_height.setValue(res["room_height"])
            if hasattr(self, 'sld_cam_offset_x'):
                self.sld_cam_offset_x.setValue(res["cam_offset_x"])
            if hasattr(self, 'sld_cam_offset_z'):
                self.sld_cam_offset_z.setValue(res["cam_offset_z"])
            if hasattr(self, 'sld_yaw'):
                self.sld_yaw.setValue(0.0)

            self._sync_node()
            self._create_or_update_ground_projection(notify_ui=True)
            conf = res.get("confidence", 80)
            self.log(
                f"Auto-fitted Room Box to HDRI: {res['room_width']:.2f}m(W) × {res['room_depth']:.2f}m(D) × {res['room_height']:.2f}m(H), "
                f"Yaw: 0.0° (native alignment)",
                "SUCCESS"
            )
        except Exception as e:
            self.log_error("Auto-align room box failed", e)

    def _on_reset_room_yaw(self):
        """Reset room yaw and DomeLight rotation back to 0.0° native alignment."""
        if hasattr(self, 'sld_yaw'):
            self.sld_yaw.setValue(0.0)
        self._sync_node()
        self._create_or_update_ground_projection(notify_ui=True)
        self.log("Reset Room Box Yaw to 0.0° (native HDRI panorama alignment).", "SUCCESS")

    def _on_hdri_dropped(self, path):
        """Handle file dropped directly onto HDRI target or preview."""
        clean_path = os.path.normpath(path).replace("\\", "/")
        self.txt_hdri.setText(clean_path)
        self.log(f"HDRI loaded via drop: {os.path.basename(clean_path)}", "SUCCESS")

    def _on_plate_dropped(self, path):
        """Handle file dropped directly onto Plate target or preview."""
        clean_path = os.path.normpath(path).replace("\\", "/")
        self.txt_plate.setText(clean_path)
        self.log(f"Plate loaded via drop: {os.path.basename(clean_path)}", "SUCCESS")

    def _on_crucible_hdri_selected(self, asset_dict):
        """Called when an HDRI asset in Crucible's HDRI grid is clicked or double-clicked."""
        if not asset_dict or not isinstance(asset_dict, dict):
            return
        fpath = asset_dict.get("path")
        if fpath and os.path.isfile(fpath):
            clean_path = os.path.normpath(fpath).replace("\\", "/")
            self.txt_hdri.setText(clean_path)
            self._on_hdri_text_changed(clean_path)
            self.log(f"Crucible HDRI loaded as match target: {os.path.basename(clean_path)}", "SUCCESS")

    def _on_crucible_asset_applied(self, category, path_or_json):
        """Called when any asset is applied from Crucible's library."""
        if category == "hdri":
            clean_path = os.path.normpath(path_or_json).replace("\\", "/")
            self.txt_hdri.setText(clean_path)
            self._on_hdri_text_changed(clean_path)
            self.log(f"Crucible HDRI applied as match target: {os.path.basename(clean_path)}", "SUCCESS")

    def _connect_signals(self):
        self.btn_reload.clicked.connect(self._reload_tool)
        if hasattr(self, "btn_reload_console"):
            self.btn_reload_console.clicked.connect(self._reload_tool)
        self.btn_hdri.clicked.connect(self._browse_hdri)
        self.btn_plate.clicked.connect(self._browse_plate)
        self.btn_create_network.clicked.connect(self._create_full_network)
        self.btn_calibrate.clicked.connect(self._run_calibration)
        self.btn_add_lookdev.clicked.connect(self._add_lookdev)
        self.btn_extract_lights.clicked.connect(self._extract_lights)
        self.btn_detect_sun.clicked.connect(self._detect_sun)
        self.btn_popout_log.clicked.connect(self._popout_log_window)
        self.btn_copy_log.clicked.connect(self._copy_log)
        self.btn_clear_log.clicked.connect(self._clear_log)

        # Live preview connections
        self.combo_preview_mode.currentIndexChanged.connect(self._on_preview_mode_changed)
        self.sld_wipe.valueChanged.connect(self._update_all_previews)
        self.txt_hdri.textChanged.connect(self._on_hdri_text_changed)
        self.txt_plate.textChanged.connect(self._on_plate_text_changed)

        # Auto-sync native stage lights and live preview when calibration parameters change
        for w in [self.sld_ev, self.sld_black, self.sld_temp, self.sld_tint, self.sld_yaw,
                  self.sld_sat, self.sld_contrast,
                  self.sld_horizon_height, self.sld_horizon_feather, self.sld_sky_ev, self.sld_ground_ev,
                  self.sld_softclip_thresh, self.sld_softclip_rolloff,
                  self.sld_sun_u, self.sld_sun_v, self.sld_sun_radius, self.sld_sun_angle,
                  self.sld_sun_intensity, self.sld_sun_exposure, self.sld_sun_cct,
                  self.sld_extract_count, self.sld_extract_dist, self.sld_extract_range, self.sld_extract_thresh,
                  self.sld_extract_intensity, self.sld_extract_exposure]:
            w.valueChanged.connect(self._sync_node)
            w.valueChanged.connect(self._update_all_previews)

        # 3D Ground and Room Projection parameters: connected directly to _sync_node for instant 60 FPS viewport updates
        for proj_w in [self.sld_tripod_height, self.sld_ground_radius, self.sld_ground_feather, self.sld_ground_roughness]:
            proj_w.valueChanged.connect(self._sync_node)

        for extra_attr in ['sld_room_width', 'sld_room_depth', 'sld_room_height', 'sld_cam_offset_x', 'sld_cam_offset_z',
                           'sld_room_subdivs', 'sld_ground_emissive_mult',
                           'sld_lookdev_radius', 'sld_lookdev_spacing', 'sld_lookdev_height', 'sld_lookdev_pos_x', 'sld_lookdev_pos_z']:
            if hasattr(self, extra_attr):
                w = getattr(self, extra_attr)
                w.valueChanged.connect(self._sync_node)

        if hasattr(self, 'combo_ground_mat_mode'):
            self.combo_ground_mat_mode.currentIndexChanged.connect(self._on_renderer_target_changed)
        if hasattr(self, 'combo_renderer_target'):
            self.combo_renderer_target.currentIndexChanged.connect(self._on_renderer_target_changed)
        if hasattr(self, 'combo_disc_proj_method'):
            self.combo_disc_proj_method.currentIndexChanged.connect(self._sync_node)
        if hasattr(self, 'combo_ground_tex_mode'):
            self.combo_ground_tex_mode.currentIndexChanged.connect(self._sync_node)
        if hasattr(self, 'combo_ground_planar_res'):
            self.combo_ground_planar_res.currentIndexChanged.connect(self._sync_node)

        if hasattr(self, 'col_sky'):
            self.col_sky.colorChanged.connect(self._sync_node)
            self.col_sky.colorChanged.connect(self._update_all_previews)
            self.col_sky.colorChanged.connect(self._schedule_auto_bake)
        if hasattr(self, 'col_ground'):
            self.col_ground.colorChanged.connect(self._sync_node)
            self.col_ground.colorChanged.connect(self._update_all_previews)
            self.col_ground.colorChanged.connect(self._schedule_auto_bake)

        self.sld_sun_cct.valueChanged.connect(
            lambda val: hasattr(self, 'lbl_sun_cct_desc') and self.lbl_sun_cct_desc.setText(
                from_stats_desc(val) if 'from_stats_desc' in globals() else f"{int(val)} K"
            )
        )

        # Wire Crucible Studio signals (strictly separate HDRI Match loading from Crucible light assignment)
        if self._crucible_panel:
            try:
                if hasattr(self._crucible_panel, "asset_library"):
                    if hasattr(self._crucible_panel.asset_library, "send_to_match_requested"):
                        self._crucible_panel.asset_library.send_to_match_requested.connect(self._on_crucible_hdri_selected)
            except Exception as e:
                self.log(f"Warning connecting Crucible signals: {e}", "WARNING")
        if hasattr(self, "btn_merge_networks"):
            self.btn_merge_networks.clicked.connect(lambda: self._merge_light_networks(notify_ui=True))
        self.btn_extract_sun.clicked.connect(lambda: self._extract_and_apply_sun_values())
        self.btn_create_ground_geometry.clicked.connect(self._create_or_update_ground_projection)
        if hasattr(self, "btn_analyze_hotspots"):
            self.btn_analyze_hotspots.clicked.connect(lambda: self._analyze_hdri_hotspots(notify_ui=True))
        if hasattr(self, "btn_apply_detected_count"):
            self.btn_apply_detected_count.clicked.connect(self._apply_detected_hotspot_count)
        if hasattr(self, "sld_extract_range"):
            self.sld_extract_range.valueChanged.connect(lambda _val: self._analyze_hdri_hotspots(notify_ui=False))
        if hasattr(self, "sld_extract_thresh"):
            self.sld_extract_thresh.valueChanged.connect(lambda _val: self._analyze_hdri_hotspots(notify_ui=False))

        for w in [self.chk_auto_cal, self.chk_protect_sun, self.chk_auto_detect_sun, self.chk_remove_sun, self.chk_sun_use_cct,
                  self.chk_bake_ground_warp, self.chk_extract_inpaint]:
            w.toggled.connect(self._sync_node)

        if hasattr(self, 'combo_proj_mode'):
            self.combo_proj_mode.currentIndexChanged.connect(self._on_proj_mode_changed)
        for w in [getattr(self, 'chk_room_floor', None), getattr(self, 'chk_room_ceiling', None),
                  getattr(self, 'chk_room_walls', None), getattr(self, 'chk_room_double_sided', None),
                  getattr(self, 'chk_room_shadows', None), getattr(self, 'chk_room_invisible', None),
                  getattr(self, 'chk_arch_invisible', None),
                  getattr(self, 'chk_snap_to_room', None), getattr(self, 'grp_lookdev', None),
                  getattr(self, 'chk_lookdev_stand', None), getattr(self, 'chk_lookdev_white', None)]:
            if w is not None:
                w.toggled.connect(self._sync_node)

        if hasattr(self, 'chk_sun_reconstruct'):
            self.chk_sun_reconstruct.toggled.connect(self._on_sun_reconstruct_toggled)
        if hasattr(self, 'sld_sun_turbidity'):
            self.sld_sun_turbidity.valueChanged.connect(lambda _v: self._on_sun_reconstruct_changed())
        if hasattr(self, 'sld_sun_reconstruct_blend'):
            self.sld_sun_reconstruct_blend.valueChanged.connect(lambda _v: self._on_sun_reconstruct_changed())
        if hasattr(self, 'cmb_sun_radiometry_mode'):
            self.cmb_sun_radiometry_mode.currentIndexChanged.connect(self._on_sun_radiometry_mode_changed)

        if hasattr(self, 'btn_setup_aovs'):
            self.btn_setup_aovs.clicked.connect(self._apply_light_groups_to_stage)
        if hasattr(self, 'grp_aov'):
            self.grp_aov.toggled.connect(lambda _b: self._apply_light_groups_to_stage(notify_ui=False))
        if hasattr(self, 'chk_aov_lightgroups'):
            self.chk_aov_lightgroups.toggled.connect(lambda _b: self._apply_light_groups_to_stage(notify_ui=False))
        if hasattr(self, 'txt_dome_lg'):
            self.txt_dome_lg.textChanged.connect(lambda _t: self._apply_light_groups_to_stage(notify_ui=False))
        if hasattr(self, 'txt_sun_lg'):
            self.txt_sun_lg.textChanged.connect(lambda _t: self._apply_light_groups_to_stage(notify_ui=False))
        if hasattr(self, 'txt_practicals_lg'):
            self.txt_practicals_lg.textChanged.connect(lambda _t: self._apply_light_groups_to_stage(notify_ui=False))

        self.combo_input_cs.currentIndexChanged.connect(self._on_input_cs_changed)
        if hasattr(self, 'combo_plate_cs'):
            self.combo_plate_cs.currentIndexChanged.connect(self._on_plate_cs_changed)
        self.combo_sky_mode.currentIndexChanged.connect(self._on_sky_mode_changed)

        for w in [self.grp_horizon, self.grp_softclip, self.grp_sun, self.grp_ground_proj, self.grp_extract]:
            w.toggled.connect(self._sync_node)
            w.toggled.connect(self._update_all_previews)
        self.chk_remove_sun.toggled.connect(self._update_all_previews)

        if hasattr(self, 'btn_bake_hdri'):
            self.btn_bake_hdri.clicked.connect(lambda: self._bake_calibrated_hdri(notify_ui=True))

        for w in [self.sld_horizon_height, self.sld_horizon_feather, self.sld_sky_ev, self.sld_ground_ev,
                  self.sld_softclip_thresh, self.sld_softclip_rolloff,
                  self.sld_ev, self.sld_black, self.sld_temp, self.sld_tint,
                  self.sld_sat, self.sld_contrast,
                  self.sld_sun_u, self.sld_sun_v, self.sld_sun_radius,
                  self.sld_tripod_height, self.sld_ground_radius, self.sld_ground_feather, self.sld_ground_roughness, self.chk_bake_ground_warp,
                  self.sld_extract_count, self.sld_extract_range, self.sld_extract_thresh, self.chk_extract_inpaint,
                  self.grp_horizon, self.grp_softclip, self.grp_sun, self.grp_ground_proj, self.grp_extract, self.chk_remove_sun]:
            if hasattr(w, 'valueChanged'):
                w.valueChanged.connect(self._schedule_auto_bake)
            elif hasattr(w, 'toggled'):
                w.toggled.connect(self._schedule_auto_bake)

    def _get_stage_lights(self):
        """Find existing native domelight and distantlight in stage."""
        try:
            stage_node = self._get_stage_node()
            dome_node = stage_node.node("hdri_dome")
            if dome_node is None:
                for child in stage_node.children():
                    if child.type().name().startswith("domelight"):
                        dome_node = child
                        break

            sun_node = stage_node.node("hdri_sun")
            if sun_node is None:
                for child in stage_node.children():
                    if child.type().name().startswith("distantlight"):
                        sun_node = child
                        break

            return dome_node, sun_node
        except Exception:
            return None, None

    def _sync_node(self, *args):
        """Instantly update native domelight and distantlight parameters in /stage at 60 FPS."""
        if getattr(self, '_restoring_state', False) or getattr(self, '_initializing', False):
            return
        try:
            dome_node, sun_node = self._get_stage_lights()
            p = self._collect_parms()

            # 1. Update native Dome Light
            if dome_node is not None:
                if p.get("renderer_target") == "arnold" or p.get("arch_renderer_target") == "arnold":
                    _ensure_dome_light_latlong(dome_node)
                # Texture path: use calibrated EXR if horizon/softclip, sun removal, practical inpaint, or manual bake is active
                tex_to_use = p.get("hdri_path", "").replace(chr(92), "/")
                calibrated_path = getattr(self, '_current_calibrated_path', None)
                is_using_baked = False
                sun_remove_active = (p.get("sun_en") and p.get("sun_remove"))
                practical_inpaint_active = (p.get("extract_en") and p.get("extract_inpaint"))
                has_spatial_calib = (
                    p.get("horizon_en") or
                    p.get("softclip_en") or
                    sun_remove_active or
                    practical_inpaint_active or
                    getattr(self, '_baked_manual', False)
                )
                if has_spatial_calib and calibrated_path:
                    import os
                    if os.path.isfile(calibrated_path):
                        tex_to_use = calibrated_path.replace(chr(92), "/")
                        is_using_baked = True

                if tex_to_use:
                    tex_parm = dome_node.parm("xn__inputstexturefile_r3ah")
                    if tex_parm and tex_parm.eval() != tex_to_use:
                        tex_parm.set(tex_to_use)

                # Exposure EV: if using baked map, only apply delta relative to baked EV
                cur_ev = float(p.get("ev", 0.0))
                baked_ev = getattr(self, '_baked_ev', 0.0) if is_using_baked else 0.0
                delta_ev = cur_ev - baked_ev

                exp_parm = dome_node.parm("xn__inputsexposure_vya")
                if exp_parm:
                    exp_parm.set(delta_ev)

                # Yaw rotation (ry)
                ry_parm = dome_node.parm("ry")
                if ry_parm:
                    ry_parm.set(float(p.get("yaw", 0.0)))

                # Color Temperature: if using baked map, only apply delta relative to baked temp
                cur_temp = float(p.get("temp", 0.0))
                baked_temp = getattr(self, '_baked_temp', 0.0) if is_using_baked else 0.0
                delta_temp = cur_temp - baked_temp

                enable_temp_parm = dome_node.parm("xn__inputsenableColorTemperature_omb")
                temp_parm = dome_node.parm("xn__inputscolorTemperature_wcb")
                if enable_temp_parm and temp_parm:
                    if abs(delta_temp) > 0.001:
                        enable_temp_parm.set(1)
                        # Map -1..+1 to 4000K..9000K (0.0 is 6500K neutral)
                        kelvin = 6500.0 - delta_temp * 2500.0
                        temp_parm.set(float(kelvin))
                    else:
                        enable_temp_parm.set(0)

                # Tint (Color multiplier): if using baked map, only apply delta relative to baked tint
                cur_tint = float(p.get("tint", 0.0))
                baked_tint = getattr(self, '_baked_tint', 0.0) if is_using_baked else 0.0
                delta_tint = cur_tint - baked_tint

                cr = dome_node.parm("xn__inputscolor_ztar")
                cg = dome_node.parm("xn__inputscolor_ztag")
                cb = dome_node.parm("xn__inputscolor_ztab")
                if cr and cg and cb:
                    if abs(delta_tint) > 0.001:
                        r_tint = max(0.01, 1.0 - 0.5 * delta_tint)
                        g_tint = max(0.01, 1.0 + delta_tint)
                        b_tint = max(0.01, 1.0 - 0.5 * delta_tint)
                        cr.set(r_tint)
                        cg.set(g_tint)
                        cb.set(b_tint)
                    else:
                        cr.set(1.0)
                        cg.set(1.0)
                        cb.set(1.0)

            # 2. Update native Sun Light (Distant Light)
            stage_node = self._get_stage_node()
            if sun_node is None and stage_node and dome_node is not None and p.get("sun_en", False):
                sun_node = stage_node.createNode("distantlight", "hdri_sun")
                self.log("Created native Solaris /stage/hdri_sun (distantlight)", "DETAIL")
                self._merge_light_networks(notify_ui=False)

            if sun_node is not None:
                if p.get("sun_en", False):
                    sun_node.bypass(False)
                    angle_parm = sun_node.parm("xn__inputsangle_zta")
                    if angle_parm:
                        angle_parm.set(float(p.get("sun_angle", 0.53)))

                    # Set Intensity
                    int_parm = sun_node.parm("xn__inputsintensity_i0a")
                    if int_parm is None:
                        int_parm = sun_node.parm("inputs:intensity")
                    if int_parm:
                        int_parm.set(float(p.get("sun_intensity", 1.0)))

                    # Set Exposure (EV)
                    exp_parm = sun_node.parm("xn__inputsexposure_vya")
                    if exp_parm is None:
                        exp_parm = sun_node.parm("inputs:exposure")
                    if exp_parm:
                        exp_parm.set(float(p.get("sun_exposure", 0.0)))

                    # Set Color Temperature
                    use_cct = bool(p.get("sun_use_cct", True))
                    cct_en_parm = sun_node.parm("xn__inputsenableColorTemperature_omb")
                    if cct_en_parm is None:
                        cct_en_parm = sun_node.parm("inputs:enableColorTemperature")
                    if cct_en_parm:
                        cct_en_parm.set(1 if use_cct else 0)

                    cct_parm = sun_node.parm("xn__inputscolorTemperature_wcb")
                    if cct_parm is None:
                        cct_parm = sun_node.parm("inputs:colorTemperature")
                    if cct_parm:
                        cct_parm.set(float(p.get("sun_cct", 5500.0)))

                    # Compute orientation from U, V matching USD DomeLight equirectangular projection
                    # Ground-truth verified with Karma CPU path-traced shadow alignment (<0.5 deg diff)
                    u = float(p.get("sun_u", 0.5))
                    v = float(p.get("sun_v", 0.25))
                    yaw = float(p.get("yaw", 0.0))

                    # Elevation: v=0 is Zenith (rx=-90), v=0.5 is Horizon (rx=0), v=1 is Nadir (rx=+90)
                    rx = (v - 0.5) * 180.0

                    # Azimuth: u=0.5 is Center (ry=0), u=0.75 is Right/East (ry=-90), u=0.25 is Left/West (ry=+90)
                    ry = (0.5 - u) * 360.0 + yaw
                    ry = (ry + 180.0) % 360.0 - 180.0

                    rx_parm = sun_node.parm("rx")
                    ry_parm = sun_node.parm("ry")
                    if rx_parm:
                        rx_parm.set(rx)
                    if ry_parm:
                        ry_parm.set(ry)
                else:
                    sun_node.bypass(True)

            # 3. Lookdev calibration rig (60 FPS interactive sync)
            if stage_node:
                lookdev_node = stage_node.node("hdri_match_lookdev")
                lookdev_en = bool(p.get("lookdev_en", False))
                if lookdev_en:
                    from hdri_match_solaris import lop_lookdev
                    if lookdev_node is None and dome_node is not None:
                        lookdev_node = lop_lookdev.create_lookdev_rig_node(
                            stage_node,
                            "hdri_match_lookdev",
                            pos=(float(p.get("lookdev_pos_x", 0.0)), 0.0, float(p.get("lookdev_pos_z", 0.0))),
                            radius=float(p.get("lookdev_radius", 0.15)),
                            stand_height=float(p.get("lookdev_height", 1.0)),
                            spacing=float(p.get("lookdev_spacing", 0.45)),
                            include_white=bool(p.get("lookdev_white", False)),
                            include_stand=bool(p.get("lookdev_stand", True)),
                            include_macbeth=False,
                            lookdev_en=True,
                        )
                        self.log("Created /stage/hdri_match_lookdev node", "DETAIL")
                        self._merge_light_networks(notify_ui=False)
                    if lookdev_node is not None:
                        lookdev_node.bypass(False)
                        has_spare = bool(lookdev_node.parm("sphere_radius"))
                        if not has_spare:
                            lookdev_node = lop_lookdev.create_lookdev_rig_node(
                                stage_node,
                                "hdri_match_lookdev",
                                pos=(float(p.get("lookdev_pos_x", 0.0)), 0.0, float(p.get("lookdev_pos_z", 0.0))),
                                radius=float(p.get("lookdev_radius", 0.15)),
                                stand_height=float(p.get("lookdev_height", 1.0)),
                                spacing=float(p.get("lookdev_spacing", 0.45)),
                                include_white=bool(p.get("lookdev_white", False)),
                                include_stand=bool(p.get("lookdev_stand", True)),
                                include_macbeth=False,
                                lookdev_en=True,
                            )
                        else:
                            parms_to_sync = [
                                ("pos_x", float(p.get("lookdev_pos_x", 0.0))),
                                ("pos_z", float(p.get("lookdev_pos_z", 0.0))),
                                ("stand_height", float(p.get("lookdev_height", 1.0))),
                                ("sphere_radius", float(p.get("lookdev_radius", 0.15))),
                                ("spacing", float(p.get("lookdev_spacing", 0.45))),
                                ("include_stand", bool(p.get("lookdev_stand", True))),
                                ("include_white", bool(p.get("lookdev_white", False))),
                                ("lookdev_en", True),
                            ]
                            changed = False
                            for pname, pval in parms_to_sync:
                                parm = lookdev_node.parm(pname)
                                if parm is not None:
                                    cur_v = parm.eval()
                                    if isinstance(pval, bool):
                                        cur_v = bool(cur_v)
                                    if cur_v != pval:
                                        parm.set(pval)
                                        changed = True
                            cur_inputs = lookdev_node.inputs()
                            needs_wire = (len(cur_inputs) == 0 or cur_inputs[0] is None)
                            if needs_wire:
                                self._merge_light_networks(notify_ui=False)
                            if changed or needs_wire:
                                lookdev_node.cook(force=True)
                elif lookdev_node is not None:
                    # Do not bypass if Splat Room Architecture has lookdev enabled!
                    splat_arch_node = stage_node.node("splat_room_architecture")
                    has_splat_ld = (splat_arch_node is not None and not splat_arch_node.isBypassed() and hasattr(self, 'chk_arch_snap_lookdev') and self.chk_arch_snap_lookdev.isChecked())
                    if not has_splat_ld:
                        lookdev_node.bypass(True)
                    else:
                        lookdev_node.bypass(False)

                # 4. Ground & Room Projection Mesh (60 FPS real-time sync)
                proj_node = stage_node.node("hdri_match_projection")
                ground_proj_en = p.get("ground_proj_en", False)

                # CRITICAL: If Splat Room Architecture is active, NEVER allow hdri_match_projection
                # to build a duplicate room box at /environment/ground_dome!
                splat_arch_node = stage_node.node("splat_room_architecture")
                has_splat_arch = (splat_arch_node is not None and not splat_arch_node.isBypassed())
                if has_splat_arch:
                    ground_proj_en = False
                    matlib_node = stage_node.node("hdri_match_materials")
                    if matlib_node and stage_node.node("splatforge_materials"):
                        matlib_node.bypass(True)

                if ground_proj_en:
                    calibrated_path = getattr(self, '_current_calibrated_path', None)
                    if calibrated_path and os.path.isfile(calibrated_path):
                        p["hdri_texture"] = calibrated_path.replace(chr(92), "/")
                    else:
                        p["hdri_texture"] = p.get("hdri_path", "").replace(chr(92), "/")

                    # Auto-create if enabled and not yet present on stage so artist gets instant live viewport feedback
                    if proj_node is None:
                        proj_node = stage_node.createNode("pythonscript", "hdri_match_projection")
                        self.log("Created /stage/hdri_match_projection for real-time viewport feedback", "DETAIL")

                    if proj_node is not None:
                        proj_node.bypass(False)
                        _ensure_projection_node_parms(proj_node)
                        _update_proj_node_values(proj_node, p)

                        new_proj_code = _gen_ground_projection_code(p)
                        if proj_node.parm("python").eval() != new_proj_code:
                            proj_node.parm("python").set(new_proj_code)

                        cur_inputs = proj_node.inputs()
                        needs_wire = (len(cur_inputs) == 0 or cur_inputs[0] is None)
                        if needs_wire:
                            self._merge_light_networks(notify_ui=False)

                        proj_node.cook(force=True)
                        if hou.isUIAvailable():
                            hou.ui.triggerUpdate()
                elif proj_node is not None:
                    proj_node.bypass(True)

                # 5. Synchronize existing native practical RectLights (distance, scale, yaw rotation, and room snapping)
                practical_nodes = [c for c in stage_node.children() if c.name().startswith("practical_")]
                extract_en = p.get("extract_en", False)
                cur_ev = float(p.get("ev", 0.0))
                yaw = float(p.get("yaw", 0.0))
                dist_val = float(p.get("extract_dist", 10.0))
                proj_mode = p.get("proj_mode", "ground_disc")
                snap_to_room = bool(p.get("snap_to_room", True))
                room_w = float(p.get("room_width", 8.0))
                room_d = float(p.get("room_depth", 10.0))
                room_h = float(p.get("room_height", 3.5))
                c_x = float(p.get("cam_offset_x", 0.0))
                c_y = float(p.get("tripod_height", 1.5))
                c_z = float(p.get("cam_offset_z", 0.0))

                for p_node in practical_nodes:
                    if not extract_en:
                        p_node.bypass(True)
                        continue
                    p_node.bypass(False)

                    # Update exposure
                    exp_parm = p_node.parm("xn__inputsexposure_vya")
                    if exp_parm and abs(exp_parm.eval() - cur_ev) > 1e-4:
                        exp_parm.set(cur_ev)

                    # Update 3D position and dimensions dynamically from distance and yaw
                    phi_str = p_node.userData("phi_native")
                    el_str = p_node.userData("el_native")
                    tx_str = p_node.userData("theta_x")
                    ty_str = p_node.userData("theta_y")
                    if phi_str and el_str and tx_str and ty_str:
                        phi_val = float(phi_str)
                        el_val = float(el_str)
                        th_x = float(tx_str)
                        th_y = float(ty_str)

                        if proj_mode == "room_box" and snap_to_room:
                            (px, py, pz), eff_dist, new_w, new_h, normal, (new_rx, new_ry, new_rz) = _compute_room_light_placement(
                                phi_val, el_val, th_x, th_y,
                                room_w, room_d, room_h, c_x, c_y, c_z,
                                yaw=yaw
                            )
                            is_snapped = True
                        else:
                            az_eff = phi_val + math.radians(yaw)
                            cos_el = math.cos(el_val)
                            sin_el = math.sin(el_val)
                            dx = -cos_el * math.sin(az_eff)
                            dy = sin_el
                            dz = cos_el * math.cos(az_eff)
                            px = dx * dist_val
                            py = dy * dist_val
                            pz = dz * dist_val
                            new_w = max(0.2, 2.0 * dist_val * math.tan(th_x / 2.0))
                            new_h = max(0.2, 2.0 * dist_val * math.tan(th_y / 2.0))
                            new_rx = 0.0
                            new_ry = 0.0
                            new_rz = 0.0
                            is_snapped = False

                        tx_p = p_node.parm("tx")
                        ty_p = p_node.parm("ty")
                        tz_p = p_node.parm("tz")
                        rx_p = p_node.parm("rx")
                        ry_p = p_node.parm("ry")
                        rz_p = p_node.parm("rz")
                        w_p = p_node.parm("xn__inputswidth_zta")
                        h_p = p_node.parm("xn__inputsheight_mva")
                        look_p = p_node.parm("lookatenable")

                        if is_snapped:
                            if look_p and look_p.eval() != 0:
                                look_p.set(0)
                            if rx_p and abs(rx_p.eval() - new_rx) > 1e-3:
                                rx_p.set(new_rx)
                            if ry_p and abs(ry_p.eval() - new_ry) > 1e-3:
                                ry_p.set(new_ry)
                            if rz_p and abs(rz_p.eval() - new_rz) > 1e-3:
                                rz_p.set(new_rz)
                        else:
                            if look_p and look_p.eval() != 1:
                                look_p.set(1)
                            if p_node.parm("lookatpositionx"):
                                p_node.parm("lookatpositionx").set(0.0)
                            if p_node.parm("lookatpositiony"):
                                p_node.parm("lookatpositiony").set(0.75)
                            if p_node.parm("lookatpositionz"):
                                p_node.parm("lookatpositionz").set(0.0)

                        if tx_p and abs(tx_p.eval() - px) > 1e-3:
                            tx_p.set(px)
                        if ty_p and abs(ty_p.eval() - py) > 1e-3:
                            ty_p.set(py)
                        if tz_p and abs(tz_p.eval() - pz) > 1e-3:
                            tz_p.set(pz)
                        if w_p and abs(w_p.eval() - new_w) > 1e-3:
                            w_p.set(new_w)
                        if h_p and abs(h_p.eval() - new_h) > 1e-3:
                            h_p.set(new_h)

                        # Sync intensity & exposure
                        int_p = p_node.parm("xn__inputsintensity_i0a")
                        if int_p and hasattr(self, 'sld_extract_intensity'):
                            ext_int = float(self.sld_extract_intensity.value())
                            if abs(int_p.eval() - ext_int) > 1e-3:
                                int_p.set(ext_int)
                        exp_p = p_node.parm("xn__inputsexposure_vya")
                        if exp_p:
                            ext_exp = (float(self.sld_extract_exposure.value()) if hasattr(self, 'sld_extract_exposure') else 0.0) + (float(self.sld_ev.value()) if hasattr(self, 'sld_ev') else 0.0)
                            if abs(exp_p.eval() - ext_exp) > 1e-3:
                                exp_p.set(ext_exp)

            # Persist session state across all tiers (session, hip, stage, disk)
            self._save_state(dome_node)

        except Exception:
            pass

    # ------------------------------------------------------------------
    # Reloading
    # ------------------------------------------------------------------


    def _schedule_auto_bake(self, *args):
        """Ultra-fast debounced trigger (120ms) to bake calibrated HDRI in background thread."""
        if getattr(self, '_restoring_state', False):
            return
        if hasattr(self, 'chk_auto_bake') and self.chk_auto_bake.isChecked():
            if hasattr(self, '_bake_timer'):
                self._bake_timer.start(120)

    def _trigger_background_bake(self):
        """Assemble parameters and dispatch asynchronous background bake without freezing Houdini."""
        hdri_path = self.txt_hdri.text().strip()
        if not hdri_path:
            return

        # Self-heal txt_hdri if it was pointing to a calibrated file
        clean_raw_path = _clean_hdri_path(hdri_path)
        if clean_raw_path != hdri_path and os.path.isfile(_resolve_image_path(clean_raw_path)):
            hdri_path = clean_raw_path
            self.txt_hdri.blockSignals(True)
            self.txt_hdri.setText(clean_raw_path)
            self.txt_hdri.blockSignals(False)

        arr = getattr(self, '_hdri_full_array', None)
        if arr is None:
            try:
                from hdri_match.io.loader import load_exr_to_numpy
                arr = load_exr_to_numpy(hdri_path)
                if arr is not None:
                    self._hdri_full_array = arr[..., :3].astype(np.float32)
                    arr = self._hdri_full_array
            except Exception as e:
                self.log_error("Could not load HDRI for background bake", e)
                return

        if arr is None:
            return

        horizon_en = hasattr(self, 'grp_horizon') and self.grp_horizon.isChecked()
        softclip_en = hasattr(self, 'grp_softclip') and self.grp_softclip.isChecked()
        sun_en = hasattr(self, 'grp_sun') and self.grp_sun.isChecked()
        sun_remove_en = sun_en and hasattr(self, 'chk_remove_sun') and self.chk_remove_sun.isChecked()

        # Determine export destination in $HIP
        hip_dir = hou.expandString("$HIP") or "."
        if not hip_dir or hip_dir == "." or "houdini_temp" in hip_dir:
            hip_dir = os.path.expanduser("~")
        out_dir = os.path.join(hip_dir, "hdri_match").replace(chr(92), "/")
        os.makedirs(out_dir, exist_ok=True)
        base_name = _clean_hdri_basename(hdri_path)

        slot = getattr(self, '_bake_slot', 0)
        next_slot = 1 - slot
        self._bake_slot = next_slot
        slot_tag = "A" if next_slot == 1 else "B"
        calibrated_path = os.path.join(out_dir, f"{base_name}_calibrated_{slot_tag}.exr").replace(chr(92), "/")
        tmp_path = os.path.join(out_dir, f"{base_name}_calibrated_tmp.exr").replace(chr(92), "/")

        params = {
            "img": arr,
            "ev": float(self.sld_ev.value()),
            "black": float(self.sld_black.value()),
            "temp": float(self.sld_temp.value()),
            "tint": float(self.sld_tint.value()),
            "sat": float(self.sld_sat.value()) if hasattr(self, 'sld_sat') else 1.0,
            "contrast": float(self.sld_contrast.value()) if hasattr(self, 'sld_contrast') else 1.0,
            "ground_proj_en": self.grp_ground_proj.isChecked() if hasattr(self, 'grp_ground_proj') else False,
            "tripod_height": float(self.sld_tripod_height.value()) if hasattr(self, 'sld_tripod_height') else 1.5,
            "ground_radius": float(self.sld_ground_radius.value()) if hasattr(self, 'sld_ground_radius') else 10.0,
            "ground_feather": float(self.sld_ground_feather.value()) if hasattr(self, 'sld_ground_feather') else 0.15,
            "bake_ground_warp": self.chk_bake_ground_warp.isChecked() if hasattr(self, 'chk_bake_ground_warp') else True,
            "extract_inpaint_en": (self.grp_extract.isChecked() and self.chk_extract_inpaint.isChecked()) if hasattr(self, 'grp_extract') else False,
            "extract_count": int(self.sld_extract_count.value()) if hasattr(self, 'sld_extract_count') else 3,
            "extract_dist": float(self.sld_extract_dist.value()) if hasattr(self, 'sld_extract_dist') else 20.0,
            "extract_range": float(self.sld_extract_range.value()) if hasattr(self, 'sld_extract_range') else 8.0,
            "extract_thresh": float(self.sld_extract_thresh.value()) if hasattr(self, 'sld_extract_thresh') else 3.0,
            "horizon_en": horizon_en,
            "horizon_height": float(self.sld_horizon_height.value()),
            "horizon_feather": float(self.sld_horizon_feather.value()),
            "sky_ev": float(self.sld_sky_ev.value()),
            "ground_ev": float(self.sld_ground_ev.value()),
            "sky_color": list(self.col_sky.color()) if hasattr(self, 'col_sky') else [1.0, 1.0, 1.0],
            "ground_color": list(self.col_ground.color()) if hasattr(self, 'col_ground') else [1.0, 1.0, 1.0],
            "softclip_en": softclip_en,
            "softclip_thresh": float(self.sld_softclip_thresh.value()),
            "softclip_rolloff": float(self.sld_softclip_rolloff.value()),
            "sun_remove_en": sun_remove_en,
            "sun_u": float(self.sld_sun_u.value()),
            "sun_v": float(self.sld_sun_v.value()),
            "sun_radius": float(self.sld_sun_radius.value()),
            "out_path": calibrated_path,
            "tmp_path": tmp_path,
            "compression": "rle"
        }

        self._bg_bake_worker.request_bake(params)

    def _on_bg_bake_finished(self, calibrated_path, ev, black, temp, tint, sat=1.0, contrast=1.0):
        """Slot called on the main thread when background EXR bake completes."""
        self._current_calibrated_path = calibrated_path
        self._baked_ev = ev
        self._baked_black = black
        self._baked_temp = temp
        self._baked_tint = tint
        self._baked_sat = sat
        self._baked_contrast = contrast

        dome_node, _ = self._get_stage_lights()
        if dome_node:
            tex_parm = dome_node.parm("xn__inputstexturefile_r3ah")
            if tex_parm and tex_parm.eval() != calibrated_path:
                tex_parm.set(calibrated_path)

        self._sync_node()
        try:
            hou.ui.triggerUpdate()
        except Exception:
            pass

    def _on_bg_bake_failed(self, error_msg):
        """Slot called if background bake fails."""
        self.log_error(f"Live background bake error: {error_msg}")

    @staticmethod
    def compute_physical_sun_sky_ratio(elevation_deg, turbidity=3.0):
        """Compute physical direct solar illuminance to diffuse sky illuminance ratio on Earth
        based on Kasten-Young optical air mass and Perez/Preetham atmospheric scattering models.
        
        Returns:
            dict with:
                'direct_lux': Direct normal solar illuminance in photometric lux
                'diffuse_sky_lux': Diffuse horizontal sky illuminance in photometric lux
                'sun_sky_ratio': Dimensionless physical direct-to-diffuse ratio (typically 4:1 to 8:1)
        """
        el = max(0.5, min(90.0, float(elevation_deg)))
        t = max(1.5, min(6.0, float(turbidity)))
        am = 1.0 / (math.sin(math.radians(el)) + 0.50572 * ((el + 6.07995) ** -1.6364))
        e0 = 127000.0
        extinction = 0.09 * t
        direct_lux = e0 * math.exp(-extinction * am)

        sin_el = math.sin(math.radians(el))
        diffuse_sky_lux = (12000.0 + 1500.0 * (t - 1.5)) * (sin_el ** 0.8) + 1200.0 * t * sin_el
        diffuse_sky_lux = max(100.0, diffuse_sky_lux)
        sun_sky_ratio = direct_lux / diffuse_sky_lux

        return {
            "direct_lux": direct_lux,
            "diffuse_sky_lux": diffuse_sky_lux,
            "sun_sky_ratio": sun_sky_ratio,
        }

    @staticmethod
    def compute_physical_sun_irradiance(elevation_deg, turbidity=3.0):
        """Compute physical direct solar illuminance at ground level in scene-linear lux
        using extraterrestrial solar constant (127,000 lux) and Kasten-Young air mass atmospheric extinction.
        """
        el = max(0.1, min(90.0, float(elevation_deg)))
        e0 = 127000.0
        am = 1.0 / (math.sin(math.radians(el)) + 0.50572 * ((el + 6.07995) ** -1.6364))
        extinction = 0.09 * float(turbidity)
        direct_lux = e0 * math.exp(-extinction * am)
        return direct_lux

    @staticmethod
    def detect_sun_clipping(measured_lux, elevation_deg, turbidity=3.0, sky_diffuse=1.0, mode="relative"):
        """Detect whether captured sun disk is clipped, calculating missing EV stops and physical target flux.
        
        Args:
            measured_lux: Direct normal illuminance measured from sun disk (in scene units)
            elevation_deg: Solar elevation angle above horizon (0.1 to 90 degrees)
            turbidity: Atmospheric turbidity (1.5 to 6.0)
            sky_diffuse: Ambient diffuse sky illuminance on ground from HDRI dome (in scene units)
            mode: 'relative' (VFX scene-linear normalized to dome) or 'absolute' (photometric lux)
        """
        r_info = HdriMatchSolarisPanel.compute_physical_sun_sky_ratio(elevation_deg, turbidity)
        sun_sky_ratio = r_info["sun_sky_ratio"]
        direct_lux = r_info["direct_lux"]

        measured = max(1e-4, float(measured_lux))
        sky_eff = max(1e-4, float(sky_diffuse))

        # In relative mode (VFX lookdev standard):
        # Target direct intensity = sky_diffuse * sun_sky_ratio
        # This keeps the dome light (intensity 1.0) and sun light in physically realistic balance without blowout!
        expected_rel = sky_eff * sun_sky_ratio
        target_expected = direct_lux if mode == "absolute" else expected_rel

        ratio = target_expected / measured
        is_clipped = ratio > 1.20
        ev_stops_lost = math.log2(max(1.0, ratio)) if is_clipped else 0.0
        return {
            "is_clipped": is_clipped,
            "ratio": ratio,
            "ev_stops_lost": ev_stops_lost,
            "physical_lux": target_expected,
            "relative_target": expected_rel,
            "absolute_lux": direct_lux,
            "sun_sky_ratio": sun_sky_ratio,
            "measured_lux": measured,
            "sky_diffuse": sky_eff,
            "mode": mode,
        }

    @staticmethod
    def _apply_light_group_to_node(node, lg_name, enabled=True):
        """Assign AOV light group / LPE tag to a physical light node for Karma, Arnold, and Redshift."""
        if node is None:
            return
        tag = (lg_name or "").strip() if enabled else ""
        mode = "set" if (enabled and tag) else "none"

        # 1. Karma LPE Tag (Light folder -> LPE Tag)
        ctrl_k = node.parm("xn__inputskarmalightlpetag_control_xpbff")
        val_k = node.parm("xn__inputskarmalightlpetag_wcbff")
        if ctrl_k:
            try: ctrl_k.set(mode)
            except Exception: pass
        if val_k:
            try: val_k.set(tag)
            except Exception: pass

        # 2. Arnold AOV Light Group (Contribution folder -> AOV Light Group)
        ctrl_a = node.parm("arnoldaov_control")
        val_a = node.parm("xn__primvarsarnoldaov_t3ag")
        if ctrl_a:
            try: ctrl_a.set(mode)
            except Exception: pass
        if val_a:
            try: val_a.set(tag)
            except Exception: pass

        # 3. Redshift AOV Light Group (Light Group folder -> AOV Light Group)
        ctrl_r = node.parm("xn__redshiftlightRSL_lightGroup_control_4xbf")
        val_r = node.parm("xn__redshiftlightRSL_lightGroup_3kbf")
        if ctrl_r:
            try: ctrl_r.set(mode)
            except Exception: pass
        if val_r:
            try: val_r.set(tag)
            except Exception: pass

    @staticmethod
    def compute_solar_angular_diameter(turbidity=3.0):
        """Compute effective solar angular diameter in degrees including circumsolar aureole broadening."""
        t = max(1.5, min(6.0, float(turbidity)))
        angle = 0.533 * (1.0 + 0.25 * (t - 2.0))
        return max(0.533, min(2.5, angle))

    @staticmethod
    def _extract_sun_parameters(image, u0, v0, radius):
        """Extract physical radiometric values (CCT, net illuminance, core tint) from the sun disk.

        Returns:
            dict with:
                'intensity': float (net direct flux/illuminance in scene-linear units)
                'cct': float (Correlated Color Temperature in Kelvin)
                'cct_desc': str (Human-readable description, e.g. 'Daylight (Direct Sun)')
                'tint': list of 3 floats (normalized RGB)
                'core_rgb': list of 3 floats (average core radiance)
                'peak_radiance': float
        """
        if image is None or radius <= 0.0:
            return {
                "intensity": 1.0,
                "cct": 5500.0,
                "cct_desc": "Daylight (Direct Sun)",
                "tint": [1.0, 1.0, 1.0],
                "core_rgb": [1.0, 1.0, 1.0],
                "peak_radiance": 1.0,
            }
        import numpy as np
        from hdri_match.analysis.hdri_stats import HDRIStats

        h, w = image.shape[:2]
        x0 = int(round(u0 * w))
        y0 = int(round(v0 * h))
        cos_lat = max(0.05, float(np.cos((v0 - 0.5) * np.pi)))

        # Shift x to center (seam-proof)
        shift = w // 2 - x0
        rolled = np.roll(image, shift, axis=1)
        center_x = w // 2

        rx = int(np.ceil(3.0 * radius * w / cos_lat))
        ry = int(np.ceil(3.0 * radius * h))
        x_min = max(0, center_x - rx)
        x_max = min(w, center_x + rx + 1)
        y_min = max(0, y0 - ry)
        y_max = min(h, y0 + ry + 1)

        sub = rolled[y_min:y_max, x_min:x_max]
        if sub.size == 0:
            return {"intensity": 1.0, "cct": 5500.0, "cct_desc": "Daylight", "tint": [1.0, 1.0, 1.0]}

        sub_ys, sub_xs = np.meshgrid(
            (np.arange(y_min, y_max) - y0) / h,
            (np.arange(x_min, x_max) - center_x) / w,
            indexing='ij'
        )
        sub_dist = np.sqrt((sub_xs * cos_lat)**2 + sub_ys**2)

        # Solid angle per pixel: d_omega = cos(lat) * 2 * pi^2 / (h * w)
        sub_lat = (np.arange(y_min, y_max) + 0.5) / h
        sub_cos_lat = np.cos((sub_lat - 0.5) * np.pi).astype(np.float32)
        d_omega_sub = np.tile(sub_cos_lat[:, None] * (2.0 * np.pi**2 / (h * w)), (1, sub.shape[1])).astype(np.float32)

        # Ambient sky ring
        r_ring_in = radius * 1.5
        r_ring_out = radius * 2.5
        ring_mask = (sub_dist >= r_ring_in) & (sub_dist <= r_ring_out)
        if np.sum(ring_mask) > 10:
            sky_ambient = np.median(sub[ring_mask], axis=0)
        else:
            sky_ambient = np.zeros(3, dtype=np.float32)

        disk_mask = sub_dist <= radius
        if np.sum(disk_mask) == 0:
            disk_mask = sub_dist <= max(radius, 0.01)

        disk_pixels = sub[disk_mask]
        disk_domega = d_omega_sub[disk_mask]

        # Net direct flux (irradiance on perpendicular surface)
        net_radiance = np.maximum(0.0, disk_pixels - sky_ambient)
        flux_rgb = np.sum(net_radiance * disk_domega[:, None], axis=0)
        total_illuminance = float(0.2126 * flux_rgb[0] + 0.7152 * flux_rgb[1] + 0.0722 * flux_rgb[2])

        # Core weighted color for CCT & chromaticity
        weights = np.maximum(0.0, net_radiance.sum(axis=-1))
        if np.sum(weights) > 1e-6:
            core_rgb = np.average(net_radiance, weights=weights + 1e-6, axis=0)
        else:
            core_rgb = np.mean(disk_pixels, axis=0)

        cct = float(HDRIStats.rgb_to_cct(float(core_rgb[0]), float(core_rgb[1]), float(core_rgb[2])))
        desc = HDRIStats.cct_to_description(cct)
        max_c = max(1e-6, float(np.max(core_rgb)))
        tint = [float(core_rgb[0] / max_c), float(core_rgb[1] / max_c), float(core_rgb[2] / max_c)]

        peak = float(np.max(disk_pixels))

        # Compute diffuse sky illuminance from the upper hemisphere (with sun masked out)
        try:
            stride_y = max(1, h // 64)
            stride_x = max(1, w // 128)
            sub_hemi = image[:h // 2:stride_y, ::stride_x]
            sh, sw = sub_hemi.shape[:2]
            lats = (np.arange(sh) + 0.5) / sh * (np.pi * 0.5)
            cos_w = np.cos(lats)
            sin_w = np.sin(lats)
            domega_h = sin_w * (0.5 * np.pi / sh) * (2.0 * np.pi / sw)
            lum_hemi = 0.2126 * sub_hemi[..., 0] + 0.7152 * sub_hemi[..., 1] + 0.0722 * sub_hemi[..., 2]

            ys_grid, xs_grid = np.meshgrid(
                (np.arange(0, h // 2, stride_y) - y0) / h,
                (np.arange(0, w, stride_x) - center_x) / w,
                indexing='ij'
            )
            xs_grid = (xs_grid + 0.5) % 1.0 - 0.5
            hemi_dist = np.sqrt((xs_grid * cos_lat)**2 + ys_grid**2)
            sky_mask = hemi_dist > max(radius * 1.5, 0.03)

            denom = float(np.sum(sky_mask * (cos_w * domega_h)[:, None]))
            if denom > 1e-4:
                diffuse_sky = float(np.sum((lum_hemi * sky_mask) * (cos_w * domega_h)[:, None]) / denom * np.pi)
            else:
                diffuse_sky = max(0.05, float(0.2126 * sky_ambient[0] + 0.7152 * sky_ambient[1] + 0.0722 * sky_ambient[2]) * np.pi)
        except Exception:
            diffuse_sky = max(0.05, float(0.2126 * sky_ambient[0] + 0.7152 * sky_ambient[1] + 0.0722 * sky_ambient[2]) * np.pi)

        return {
            "intensity": max(0.01, total_illuminance),
            "sky_illuminance": max(0.01, diffuse_sky),
            "sky_ambient": [float(c) for c in sky_ambient],
            "cct": cct,
            "cct_desc": desc,
            "tint": tint,
            "core_rgb": [float(c) for c in core_rgb],
            "peak_radiance": peak,
        }

    def _extract_and_apply_sun_values(self, arr=None):
        """Extract CCT and net illuminance from the current sun disk and apply to UI sliders."""
        try:
            import os
            if arr is None:
                hdri_path = self.txt_hdri.text()
                if not hdri_path or not os.path.isfile(hdri_path):
                    self.log_error("Please load an HDRI first to extract sun values.")
                    return
                from hdri_match.io.loader import load_exr_to_numpy
                arr = load_exr_to_numpy(hdri_path)

            u = float(self.sld_sun_u.value())
            v = float(self.sld_sun_v.value())
            r = float(self.sld_sun_radius.value())

            data = self._extract_sun_parameters(arr, u, v, r)
            el_deg = max(0.1, (0.5 - v) * 180.0)
            turbidity = float(self.sld_sun_turbidity.value()) if hasattr(self, 'sld_sun_turbidity') else 3.0
            reconstruct_blend = (float(self.sld_sun_reconstruct_blend.value()) / 100.0) if hasattr(self, 'sld_sun_reconstruct_blend') else 1.0
            reconstruct_en = self.chk_sun_reconstruct.isChecked() if hasattr(self, 'chk_sun_reconstruct') else False

            mode = "absolute" if (hasattr(self, 'cmb_sun_radiometry_mode') and self.cmb_sun_radiometry_mode.currentIndex() == 1) else "relative"
            sky_diffuse = data.get("sky_illuminance", 1.0)

            clip_info = self.detect_sun_clipping(data["intensity"], el_deg, turbidity, sky_diffuse=sky_diffuse, mode=mode)
            self._last_sun_clip_info = clip_info

            if hasattr(self, 'lbl_sun_clipping_status'):
                if clip_info["is_clipped"]:
                    self.lbl_sun_clipping_status.setText(f"🚨 Clipped by +{clip_info['ev_stops_lost']:.1f} EV stops ({clip_info['ratio']:.1f}× under)")
                    self.lbl_sun_clipping_status.setStyleSheet("color: #ffaa00; font-weight: bold;")
                else:
                    self.lbl_sun_clipping_status.setText("✅ Sun Unclipped (Physical)")
                    self.lbl_sun_clipping_status.setStyleSheet("color: #00ff88; font-weight: bold;")

            if hasattr(self, 'lbl_sun_physical_flux'):
                if mode == "absolute":
                    self.lbl_sun_physical_flux.setText(
                        f"Measured: {clip_info['measured_lux']:.1f} | Expected: {clip_info['physical_lux']:.0f} lux (Absolute)"
                    )
                else:
                    self.lbl_sun_physical_flux.setText(
                        f"Ratio: {clip_info['sun_sky_ratio']:.1f}:1 | Measured: {clip_info['measured_lux']:.2f} | Expected: {clip_info['physical_lux']:.2f}"
                    )

            # Apply intensity (reconstructed or raw)
            if reconstruct_en and clip_info["is_clipped"]:
                final_intensity = (1.0 - reconstruct_blend) * clip_info["measured_lux"] + reconstruct_blend * clip_info["physical_lux"]
                target_angle = self.compute_solar_angular_diameter(turbidity)
                if hasattr(self, 'sld_sun_angle'):
                    self.sld_sun_angle.setValue(round(target_angle, 2))
            else:
                final_intensity = data["intensity"]

            self.sld_sun_intensity.setValue(round(final_intensity, 2))
            self.sld_sun_cct.setValue(round(data["cct"], 0))
            if hasattr(self, 'lbl_sun_cct_desc'):
                self.lbl_sun_cct_desc.setText(data["cct_desc"])

            msg = (f"Extracted Sun Values: Intensity={final_intensity:.2f} (Target={clip_info['physical_lux']:.2f}, "
                   f"Sun-to-Sky Ratio={clip_info['sun_sky_ratio']:.1f}:1, ΔEV={clip_info['ev_stops_lost']:.1f}) | "
                   f"CCT={data['cct']:.0f} K ({data['cct_desc']}) | Peak Radiance={data['peak_radiance']:.1f}")
            self.log(msg, "SUCCESS")
            self._sync_node()
        except Exception as e:
            self.log_error("Failed to extract sun values", e)

    @staticmethod
    def _inpaint_sun_disc(image, u0, v0, radius):
        """Inpaint/clean the sun disc from an equirectangular latlong panorama by fitting
        a 2D planar sky gradient to the annular ring surrounding the sun and blending seamlessly."""
        if image is None or radius <= 0.0:
            return image
        try:
            import numpy as np
            h, w = image.shape[:2]
            x0 = int(round(u0 * w))
            y0 = int(round(v0 * h))
            cos_lat = max(0.05, float(np.cos((v0 - 0.5) * np.pi)))

            # Shift x so the sun is centered at w // 2 (seam-proof wrapping)
            shift = w // 2 - x0
            rolled = np.roll(image, shift, axis=1)
            center_x = w // 2

            rx = int(np.ceil(3.0 * radius * w / cos_lat))
            ry = int(np.ceil(3.0 * radius * h))
            x_min = max(0, center_x - rx)
            x_max = min(w, center_x + rx + 1)
            y_min = max(0, y0 - ry)
            y_max = min(h, y0 + ry + 1)

            sub = rolled[y_min:y_max, x_min:x_max].copy()
            if sub.size == 0:
                return image

            sub_ys, sub_xs = np.meshgrid(
                (np.arange(y_min, y_max) - y0) / h,
                (np.arange(x_min, x_max) - center_x) / w,
                indexing='ij'
            )
            sub_dist = np.sqrt((sub_xs * cos_lat)**2 + sub_ys**2)

            r_ring_in = radius * 1.6
            r_ring_out = radius * 2.6
            ring_mask = (sub_dist >= r_ring_in) & (sub_dist <= r_ring_out)
            ring_colors = sub[ring_mask]
            if ring_colors.shape[0] < 10:
                return image

            # Outlier rejection on ring (exclude flare / hot specular highlights > 90th percentile)
            ring_luma = 0.2126 * ring_colors[:, 0] + 0.7152 * ring_colors[:, 1] + 0.0722 * ring_colors[:, 2]
            p90 = np.percentile(ring_luma, 90)
            valid_ring = ring_luma <= max(1e-4, p90 * 1.5)
            if np.sum(valid_ring) < 10:
                valid_ring = np.ones(ring_colors.shape[0], dtype=bool)

            ring_xs_val = sub_xs[ring_mask][valid_ring]
            ring_ys_val = sub_ys[ring_mask][valid_ring]
            ring_col_val = ring_colors[valid_ring]

            A = np.column_stack([np.ones_like(ring_xs_val), ring_xs_val, ring_ys_val])
            coeffs = np.linalg.lstsq(A, ring_col_val, rcond=None)[0]

            inpaint_zone = sub_dist <= 1.8 * radius
            eval_xs = sub_xs[inpaint_zone]
            eval_ys = sub_ys[inpaint_zone]
            eval_A = np.column_stack([np.ones_like(eval_xs), eval_xs, eval_ys])
            modeled = np.clip(eval_A @ coeffs, 0.0, None)

            z_dist = sub_dist[inpaint_zone]
            t = np.clip((z_dist - 1.15 * radius) / (0.5 * radius), 0.0, 1.0)
            w_blend = t * t * (3.0 - 2.0 * t)

            sub[inpaint_zone] = (1.0 - w_blend[:, None]) * modeled + w_blend[:, None] * sub[inpaint_zone]
            rolled[y_min:y_max, x_min:x_max] = sub
            return np.roll(rolled, -shift, axis=1)
        except Exception:
            return image

    @staticmethod
    def _resize_image_linear(img, target_w, target_h):
        """Pure numpy bilinear interpolation for HDR floating point images."""
        if img is None:
            return None
        ch, cw = img.shape[:2]
        if ch == target_h and cw == target_w:
            return img.copy()
        import numpy as np
        y_coords = np.linspace(0, ch - 1, target_h)
        x_coords = np.linspace(0, cw - 1, target_w)
        y0 = np.floor(y_coords).astype(int)
        y1 = np.minimum(y0 + 1, ch - 1)
        wy = (y_coords - y0)[:, None, None]
        x0 = np.floor(x_coords).astype(int)
        x1 = np.minimum(x0 + 1, cw - 1)
        wx = (x_coords - x0)[None, :, None]
        c00 = img[y0][:, x0]
        c01 = img[y0][:, x1]
        c10 = img[y1][:, x0]
        c11 = img[y1][:, x1]
        top = (1.0 - wx) * c00 + wx * c01
        bot = (1.0 - wx) * c10 + wx * c11
        return ((1.0 - wy) * top + wy * bot).astype(np.float32)

    @staticmethod
    def _inpaint_practical_fixture(image, px, py, hw, hh, u=None, v=None, hw_norm=None, hh_norm=None):
        """Inpaint/remove an extracted practical light fixture from an equirectangular panorama
        using latitude-corrected spherical distance, outlier-rejected background ring sampling,
        and safely bounded planar reconstruction to prevent pitch-black borders or 3D distortion."""
        if image is None or hw <= 0 or hh <= 0:
            return image
        try:
            import numpy as np
            import math
            h, w = image.shape[:2]

            if u is not None and v is not None:
                px = int(round(u * w))
                py = int(round(v * h))
            if hw_norm is not None and hh_norm is not None:
                hw = max(4, int(round(hw_norm * w)))
                hh = max(4, int(round(hh_norm * h)))

            u0 = (px + 0.5) / float(w)
            v0 = (py + 0.5) / float(h)

            lat_rad = (0.5 - v0) * math.pi
            cos_lat = max(0.08, float(math.cos(lat_rad)))

            # Shift x so the fixture is centered at w // 2 (seam-proof wrapping)
            shift = w // 2 - px
            rolled = np.roll(image, shift, axis=1)
            cx = w // 2

            # hw and hh are already measured in equirectangular image pixels.
            # Do NOT divide by cos_lat, which causes extreme horizontal elongation and horseshoe warping near poles.
            pad_mult = 1.35
            rx_px = min(w // 4, max(8, int(np.ceil(pad_mult * float(hw)))))
            ry_px = min(h // 4, max(8, int(np.ceil(pad_mult * float(hh)))))
            hw_bound = min(w // 4, max(4, int(hw)))
            hh_bound = min(h // 4, max(4, int(hh)))

            x_min = max(0, cx - rx_px)
            x_max = min(w, cx + rx_px + 1)
            y_min = max(0, py - ry_px)
            y_max = min(h, py + ry_px + 1)

            sub = rolled[y_min:y_max, x_min:x_max].copy()
            sy, sx = sub.shape[:2]
            if sy < 5 or sx < 5:
                return image

            cur_cx = cx - x_min
            cur_cy = py - y_min

            ys_rel = np.arange(sy) - cur_cy
            xs_rel = np.arange(sx) - cur_cx
            grid_y, grid_x = np.meshgrid(ys_rel, xs_rel, indexing='ij')

            # Metric distance conforming to rectangular footprint
            p_exp = 2.6
            dx_norm = np.abs(grid_x) / float(hw_bound)
            dy_norm = np.abs(grid_y) / float(hh_bound)
            dist_conformal = (dx_norm ** p_exp + dy_norm ** p_exp) ** (1.0 / p_exp)

            # Sample annular background ring surrounding the fixture
            r_in = 1.05
            r_out = 1.35
            ring_mask = (dist_conformal >= r_in) & (dist_conformal <= r_out)
            ring_pixels = sub[ring_mask]

            if ring_pixels.shape[0] < 12:
                return image

            # Outlier rejection on background ring:
            # Exclude bright fixture bleed, flare, or specular reflections (> 85th percentile * 1.3)
            ring_luma = 0.2126 * ring_pixels[:, 0] + 0.7152 * ring_pixels[:, 1] + 0.0722 * ring_pixels[:, 2]
            p85 = float(np.percentile(ring_luma, 85))
            valid_ring = ring_luma <= max(1e-4, p85 * 1.3)
            if np.sum(valid_ring) < 8:
                valid_ring = np.ones(ring_pixels.shape[0], dtype=bool)

            clean_ring = ring_pixels[valid_ring]
            ring_xs_val = grid_x[ring_mask][valid_ring]
            ring_ys_val = grid_y[ring_mask][valid_ring]

            # Compute safe bounds for modeled background:
            # Must NEVER be zero/black, and must never blow out
            bg_min = np.percentile(clean_ring, 10, axis=0) * 0.85
            bg_max = np.percentile(clean_ring, 90, axis=0) * 1.25
            bg_median = np.median(clean_ring, axis=0)

            # Fit 2D planar gradient across clean ring pixels
            A = np.column_stack([np.ones_like(ring_xs_val), ring_xs_val, ring_ys_val])
            try:
                coeffs = np.linalg.lstsq(A, clean_ring, rcond=None)[0]
            except Exception:
                coeffs = None

            inpaint_zone = dist_conformal <= 1.25
            eval_xs = grid_x[inpaint_zone]
            eval_ys = grid_y[inpaint_zone]

            if coeffs is not None:
                eval_A = np.column_stack([np.ones_like(eval_xs), eval_xs, eval_ys])
                modeled = eval_A @ coeffs
                modeled = np.clip(modeled, np.maximum(0.0, bg_min), bg_max)
            else:
                modeled = np.broadcast_to(bg_median, (eval_xs.shape[0], 3)).copy()

            # Smooth C1 Hermite smoothstep blend across the boundary
            z_dist = dist_conformal[inpaint_zone]
            t = np.clip((z_dist - 0.90) / 0.35, 0.0, 1.0)
            w_blend = t * t * (3.0 - 2.0 * t)

            sub[inpaint_zone] = (1.0 - w_blend[:, None]) * modeled + w_blend[:, None] * sub[inpaint_zone]
            rolled[y_min:y_max, x_min:x_max] = sub
            return np.roll(rolled, -shift, axis=1)
        except Exception:
            return image

    def _bake_calibrated_hdri(self, notify_ui=False, force_bake=False):
        """Process Exposure, White Balance, Black Offset, Horizon Split, and Soft-Clip on HDRI,
        write calibrated EXR to $HIP, and update Dome Light."""
        hdri_path = self.txt_hdri.text().strip()
        if not hdri_path:
            return None

        # Self-heal txt_hdri if it was pointing to a calibrated file
        clean_raw_path = _clean_hdri_path(hdri_path)
        if clean_raw_path != hdri_path and os.path.isfile(_resolve_image_path(clean_raw_path)):
            hdri_path = clean_raw_path
            self.txt_hdri.blockSignals(True)
            self.txt_hdri.setText(clean_raw_path)
            self.txt_hdri.blockSignals(False)

        # Get base full array
        arr = getattr(self, '_hdri_full_array', None)
        if arr is None:
            try:
                from hdri_match.io.loader import load_exr_to_numpy
                raw_loaded = load_exr_to_numpy(hdri_path)
                if raw_loaded is not None:
                    self._hdri_orig_full = raw_loaded[..., :3].astype(np.float32)
                    input_cs = self.combo_input_cs.currentText().strip() if hasattr(self, 'combo_input_cs') else "Linear"
                    from hdri_match.core.colorspace import ColorSpaceManager
                    if not hasattr(self, '_csm') or self._csm is None:
                        self._csm = ColorSpaceManager()
                    self._hdri_full_array = self._csm.transform_image(
                        self._hdri_orig_full.copy(), input_cs, "Linear"
                    )
                    arr = self._hdri_full_array
            except Exception as e:
                self.log_error("Could not load HDRI for baking", e)
                return None

        if arr is None:
            return None

        try:
            import os
            import numpy as np
            img = arr.copy()
            h, w = img.shape[:2]

            ev = float(self.sld_ev.value())
            black = float(self.sld_black.value())
            temp = float(self.sld_temp.value())
            tint = float(self.sld_tint.value())
            sat = float(self.sld_sat.value()) if hasattr(self, 'sld_sat') else 1.0
            contrast = float(self.sld_contrast.value()) if hasattr(self, 'sld_contrast') else 1.0
            horizon_en = hasattr(self, 'grp_horizon') and self.grp_horizon.isChecked()
            softclip_en = hasattr(self, 'grp_softclip') and self.grp_softclip.isChecked()
            sun_en = hasattr(self, 'grp_sun') and self.grp_sun.isChecked()
            sun_remove_en = sun_en and hasattr(self, 'chk_remove_sun') and self.chk_remove_sun.isChecked()
            ground_proj_en = (hasattr(self, 'grp_ground_proj') and self.grp_ground_proj.isChecked() and
                              hasattr(self, 'chk_bake_ground_warp') and self.chk_bake_ground_warp.isChecked())
            extract_inpaint_chk = hasattr(self, 'chk_extract_inpaint') and self.chk_extract_inpaint.isChecked()
            cached_practicals = getattr(self, '_extracted_practicals_data', None)
            extract_en = (hasattr(self, 'grp_extract') and self.grp_extract.isChecked()) or (hasattr(self, 'chk_extract_en') and self.chk_extract_en.isChecked())
            extract_inpaint_en = extract_inpaint_chk and extract_en and (cached_practicals is not None and len(cached_practicals) > 0)

            has_spatial_mods = (horizon_en or softclip_en or sun_remove_en or ground_proj_en or extract_inpaint_en)
            has_mods = (
                has_spatial_mods or
                abs(ev) > 1e-4 or
                abs(black) > 1e-5 or
                abs(temp) > 1e-4 or
                abs(tint) > 1e-4 or
                abs(sat - 1.0) > 1e-4 or
                abs(contrast - 1.0) > 1e-4
            )

            dome_node, _ = self._get_stage_lights()
            if not has_mods and not notify_ui:
                # No modifications enabled: dome light uses original HDRI
                self._current_calibrated_path = None
                self._baked_manual = False
                self._baked_ev = 0.0
                self._baked_black = 0.0
                self._baked_temp = 0.0
                self._baked_tint = 0.0
                self._baked_sat = 1.0
                self._baked_contrast = 1.0
                if dome_node:
                    tex_parm = dome_node.parm("xn__inputstexturefile_r3ah")
                    if tex_parm:
                        tex_parm.set(hdri_path.replace(chr(92), "/"))
                self._sync_node()
                self.log("All modifications disabled: Dome Light reset to original HDRI.", "INFO")
                return hdri_path

            if not has_spatial_mods and not notify_ui and not force_bake:
                # Auto-bake is only active for spatial modifications (horizon/softclip/ground_proj/inpaint)
                return None

            # 1. Base Exposure EV offset
            if ev != 0.0:
                img *= (2.0 ** ev)

            # 2. Black level offset
            if black != 0.0:
                img += black

            # 3. White Balance (Color Temperature & Tint)
            if temp != 0.0 or tint != 0.0:
                r_scale = max(0.01, 1.0 + temp + tint)
                g_scale = max(0.01, 1.0 - tint)
                b_scale = max(0.01, 1.0 - temp)
                lum = (r_scale + g_scale + b_scale) / 3.0
                img[..., 0] *= (r_scale / lum)
                img[..., 1] *= (g_scale / lum)
                img[..., 2] *= (b_scale / lum)

            # 3b. Saturation & Contrast in scene-linear space
            sat = float(self.sld_sat.value()) if hasattr(self, 'sld_sat') else 1.0
            contrast = float(self.sld_contrast.value()) if hasattr(self, 'sld_contrast') else 1.0
            if abs(sat - 1.0) > 1e-4:
                luma = 0.2126 * img[..., 0] + 0.7152 * img[..., 1] + 0.0722 * img[..., 2]
                img = luma[..., np.newaxis] + sat * (img - luma[..., np.newaxis])
            if abs(contrast - 1.0) > 1e-4:
                img = 0.18 * (np.maximum(img, 1e-6) / 0.18) ** contrast

            # 3c. Ground Projection / Parallax Warp
            if hasattr(self, 'grp_ground_proj') and self.grp_ground_proj.isChecked() and self.chk_bake_ground_warp.isChecked():
                try:
                    from hdri_match.core.projection import GroundProjector
                    t_h = float(self.sld_tripod_height.value())
                    g_r = float(self.sld_ground_radius.value())
                    g_f = float(self.sld_ground_feather.value())
                    img = GroundProjector.warp_equirectangular_ground(img, tripod_height=t_h, ground_radius=g_r, feather=g_f)
                except Exception as e:
                    self.log(f"Warning in ground projection warp: {e}", "WARNING")

            # 4. Sun Removal / Sky Inpaint
            if sun_remove_en:
                sun_u = float(self.sld_sun_u.value())
                sun_v = float(self.sld_sun_v.value())
                sun_r = max(0.005, float(self.sld_sun_radius.value()))
                img = self._inpaint_sun_disc(img, sun_u, sun_v, sun_r)

            # 4b. Multi-Light Extracted Practical Inpaint
            if extract_inpaint_en:
                try:
                    h_ext, w_ext = img.shape[:2]
                    cached_data = getattr(self, '_extracted_practicals_data', None)
                    if cached_data and len(cached_data) > 0:
                        for lt in cached_data:
                            u_c = lt.get("u")
                            v_c = lt.get("v")
                            hw_n = lt.get("hw_norm")
                            hh_n = lt.get("hh_norm")
                            if u_c is not None and v_c is not None and hw_n is not None and hh_n is not None:
                                p_px = int(round(u_c * w_ext))
                                p_py = int(round(v_c * h_ext))
                                p_hw = max(4, int(round(hw_n * w_ext)))
                                p_hh = max(4, int(round(hh_n * h_ext)))
                            else:
                                p_px = lt.get("px")
                                p_py = lt.get("py")
                                p_hw = lt.get("hw")
                                p_hh = lt.get("hh")
                            if p_px is not None and p_py is not None and p_hw is not None and p_hh is not None:
                                img = self._inpaint_practical_fixture(img, p_px, p_py, p_hw, p_hh)
                except Exception as e:
                    self.log(f"Warning in multi-light inpaint: {e}", "WARNING")

            # 5. Horizon split with color tint
            if horizon_en:
                height = float(self.sld_horizon_height.value())
                feather = max(0.001, float(self.sld_horizon_feather.value()))
                sky_ev = float(self.sld_sky_ev.value())
                ground_ev = float(self.sld_ground_ev.value())

                sky_rgb = np.array(self.col_sky.color(), dtype=np.float32) if hasattr(self, 'col_sky') else np.array([1.0, 1.0, 1.0], dtype=np.float32)
                ground_rgb = np.array(self.col_ground.color(), dtype=np.float32) if hasattr(self, 'col_ground') else np.array([1.0, 1.0, 1.0], dtype=np.float32)

                sky_mult = (2.0 ** sky_ev) * sky_rgb
                ground_mult = (2.0 ** ground_ev) * ground_rgb

                y_norm = 1.0 - np.linspace(0.0, 1.0, h, endpoint=False)[:, None, None]
                sky_weight = np.clip((y_norm - (height - feather / 2.0)) / feather, 0.0, 1.0)
                color_mask = sky_mult[None, None, :] * sky_weight + ground_mult[None, None, :] * (1.0 - sky_weight)
                img *= color_mask

            # 6. Highlight Compression (soft clip) - Hue-preserving C1 exponential rolloff
            if softclip_en:
                thresh_ev = float(self.sld_softclip_thresh.value())
                rolloff_ev = float(self.sld_softclip_rolloff.value())
                t = 0.18 * (2.0 ** thresh_ev)
                r = 0.18 * (2.0 ** rolloff_ev)

                luma = np.sum(img * np.array([0.2126, 0.7152, 0.0722], dtype=np.float32), axis=-1, keepdims=True)
                mask = luma > t
                if np.any(mask):
                    luma_safe = np.maximum(luma, 1e-8)
                    compressed_luma = np.where(mask, t + r * (1.0 - np.exp(-(luma - t) / max(r, 1e-8))), luma)
                    ratio = compressed_luma / luma_safe
                    img = np.where(mask, img * ratio, img)

            # Determine export destination in $HIP
            hip_dir = hou.expandString("$HIP") or "."
            if not hip_dir or hip_dir == "." or "houdini_temp" in hip_dir:
                # Default to user home / hdri_match if scene is untitled
                hip_dir = os.path.expanduser("~")
            out_dir = os.path.join(hip_dir, "hdri_match").replace(chr(92), "/")
            os.makedirs(out_dir, exist_ok=True)
            base_name = _clean_hdri_basename(hdri_path)

            # Use alternating ping-pong filenames (A / B) so Karma never reads an active write target
            slot = getattr(self, '_bake_slot', 0)
            next_slot = 1 - slot
            self._bake_slot = next_slot
            slot_tag = "A" if next_slot == 1 else "B"
            calibrated_path = os.path.join(out_dir, f"{base_name}_calibrated_{slot_tag}.exr").replace(chr(92), "/")
            tmp_path = os.path.join(out_dir, f"{base_name}_calibrated_tmp.exr").replace(chr(92), "/")

            import OpenImageIO as oiio
            out_spec = oiio.ImageSpec(w, h, 3, oiio.HALF)
            out_spec.attribute("compression", "zip")

            out = oiio.ImageOutput.create(tmp_path)
            if out:
                out.open(tmp_path, out_spec)
                out.write_image(img.astype(np.float32))
                out.close()

                # Atomically replace slot target
                if os.path.exists(calibrated_path):
                    try:
                        os.remove(calibrated_path)
                    except Exception:
                        pass
                try:
                    os.replace(tmp_path, calibrated_path)
                except Exception:
                    calibrated_path = tmp_path

                self._current_calibrated_path = calibrated_path
                self._baked_ev = ev
                self._baked_black = black
                self._baked_temp = temp
                self._baked_tint = tint
                self._baked_sat = sat
                self._baked_contrast = contrast
                self._baked_manual = True

                self.log(f"Calibrated HDRI baked to $HIP: {calibrated_path}", "SUCCESS")

                # Repoint dome light texture parameter (USD automatically updates render delegate)
                if dome_node:
                    tex_parm = dome_node.parm("xn__inputstexturefile_r3ah")
                    if tex_parm:
                        tex_parm.set(calibrated_path)

                # Sync deltas to native dome light parameters
                self._sync_node()

                # Automatically bake / update ground projection with the calibrated HDRI texture
                stage_node = self._get_stage_node()
                ground_baked = False
                has_splat_arch = (stage_node is not None and stage_node.node("splat_room_architecture") is not None and not stage_node.node("splat_room_architecture").isBypassed())
                should_bake_ground = (
                    not has_splat_arch and (
                        (hasattr(self, 'grp_ground_proj') and self.grp_ground_proj.isChecked()) or
                        (stage_node is not None and stage_node.node("hdri_match_projection") is not None and not stage_node.node("hdri_match_projection").isBypassed())
                    )
                )
                if should_bake_ground:
                    try:
                        self._create_or_update_ground_projection(notify_ui=False)
                        ground_baked = True
                        self.log("Ground projection baked and updated with calibrated HDRI texture.", "SUCCESS")
                    except Exception as ge:
                        self.log_error("Could not update ground projection after bake", ge)

                if notify_ui and hou.isUIAvailable():
                    if ground_baked:
                        msg = "Calibrated HDRI baked to:\n" + str(calibrated_path) + "\n\nDome Light & Ground Projection updated!"
                    else:
                        msg = "Calibrated HDRI baked to:\n" + str(calibrated_path) + "\n\nDome Light updated!"
                    hou.ui.displayMessage(msg, title="DomeBreaker")
                return calibrated_path
            else:
                self.log_error(f"Failed to create OpenImageIO output for {calibrated_path}")
                return None
        except Exception as e:
            self.log_error("Error baking calibrated HDRI", e)
            return None


    # ------------------------------------------------------------------
    # Multi-Tier Session State Persistence & Restoration
    # ------------------------------------------------------------------

    def _save_state(self, dome_node=None, sync_disk=False):
        """
        Save complete UI session state across all persistence tiers:
        1. Active Houdini session memory (hou.session)
        2. Root node userData (hou.node("/").userData)
        3. USD Stage node userData (/stage.userData)
        4. USD Stage Dome Light (dome_node.userData)
        5. Persistent user preferences file on disk ($HOUDINI_USER_PREF_DIR/domebreaker_state.json)
        """
        try:
            if getattr(self, '_restoring_state', False) or getattr(self, '_initializing', False):
                return

            import json, os, re, sys
            state = self._collect_parms()

            # Ensure saved hdri_path is ALWAYS the original raw HDRI, never a baked map
            saved_hdri = state.get("hdri_path", "").replace(chr(92), "/")
            if "_calibrated" in os.path.basename(saved_hdri):
                candidate = _clean_hdri_path(saved_hdri)
                cand_exp = _resolve_image_path(candidate)
                if os.path.isfile(cand_exp):
                    state["hdri_path"] = candidate

            state["_current_calibrated_path"] = getattr(self, '_current_calibrated_path', None)
            state["_baked_manual"] = getattr(self, '_baked_manual', False)
            state["_baked_ev"] = getattr(self, '_baked_ev', 0.0)
            state["_baked_black"] = getattr(self, '_baked_black', 0.0)
            state["_baked_temp"] = getattr(self, '_baked_temp', 0.0)
            state["_baked_tint"] = getattr(self, '_baked_tint', 0.0)
            state["_baked_sat"] = getattr(self, '_baked_sat', 1.0)
            state["_baked_contrast"] = getattr(self, '_baked_contrast', 1.0)
            state["auto_bake"] = self.chk_auto_bake.isChecked() if hasattr(self, 'chk_auto_bake') else True

            state_json = json.dumps(state, indent=2)

            # Tier 1: Houdini Session memory
            if 'hou' in sys.modules and hasattr(hou, 'session'):
                try:
                    hou.session.domebreaker_session_state = state_json
                except Exception:
                    pass

            # Tier 2: Root node userData (persisted with .hip file)
            if 'hou' in sys.modules and hasattr(hou, 'node'):
                try:
                    root = hou.node("/")
                    if root is not None:
                        root.setUserData("domebreaker_state", state_json)
                except Exception:
                    pass

            # Tier 3: USD Stage nodes
            stage_node = self._get_stage_node()
            if stage_node is not None:
                try:
                    stage_node.setUserData("domebreaker_state", state_json)
                except Exception:
                    pass

            if dome_node is None:
                dome_node, _ = self._get_stage_lights()
            if dome_node is not None:
                try:
                    dome_node.setUserData("hdri_match_state", state_json)
                except Exception:
                    pass

            # Tier 4: Persistent preferences on disk
            try:
                state_file = _get_state_filepath()
                os.makedirs(os.path.dirname(state_file), exist_ok=True)
                with open(state_file, "w", encoding="utf-8") as f:
                    f.write(state_json)
            except Exception:
                pass

        except Exception:
            pass

    def _save_state_to_scene(self, dome_node=None):
        """Backward-compatible alias for _save_state."""
        self._save_state(dome_node)

    def _restore_state(self, state, src_desc="State Dict"):
        """Restore UI state directly from a dictionary."""
        return self._restore_state_from_scene(notify=False, state_dict=state)

    def _restore_state_from_scene(self, notify=False, state_dict=None):
        """
        Restore complete UI session state and reload HDRI/plate maps from:
        1. /stage/hdri_dome userData ("hdri_match_state")
        2. /stage node userData ("domebreaker_state")
        3. Root node userData ("domebreaker_state")
        4. Active Houdini session memory (hou.session.domebreaker_session_state)
        5. Persistent user preferences file on disk ($HOUDINI_USER_PREF_DIR/domebreaker_state.json)
        """
        try:
            import json, os, re, sys

            state = {}
            src_desc = ""

            if state_dict is not None and isinstance(state_dict, dict):
                state = dict(state_dict)
                src_desc = "Explicit State Dictionary"
            else:
                candidate_sources = []

            # 1. Try dome_node userData
            dome_node, sun_node = self._get_stage_lights()
            if dome_node is not None:
                raw_data = dome_node.userData("hdri_match_state")
                if raw_data:
                    try:
                        candidate_sources.append(("USD Stage dome light", json.loads(raw_data)))
                    except Exception:
                        pass

            # 2. Try stage node userData
            stage_node = self._get_stage_node()
            if stage_node is not None:
                raw_data = stage_node.userData("domebreaker_state")
                if raw_data:
                    try:
                        candidate_sources.append(("USD Stage node", json.loads(raw_data)))
                    except Exception:
                        pass

            # 3. Try root node userData (from saved HIP file)
            if 'hou' in sys.modules and hasattr(hou, 'node'):
                try:
                    root = hou.node("/")
                    if root is not None:
                        raw_data = root.userData("domebreaker_state")
                        if raw_data:
                            candidate_sources.append(("HIP scene session", json.loads(raw_data)))
                except Exception:
                    pass

            # 4. Try hou.session in-memory
            if 'hou' in sys.modules and hasattr(hou, 'session'):
                try:
                    raw_data = getattr(hou.session, "domebreaker_session_state", None)
                    if raw_data:
                        candidate_sources.append(("Houdini active session memory", json.loads(raw_data)))
                    elif hasattr(hou.session, "hdri_match_state"):
                        candidate_sources.append(("Houdini active session memory", json.loads(hou.session.hdri_match_state)))
                except Exception:
                    pass

            # 5. Try user preference JSON file on disk
            try:
                state_file = _get_state_filepath()
                if os.path.isfile(state_file):
                    with open(state_file, "r", encoding="utf-8") as f:
                        candidate_sources.append(("Saved preferences", json.load(f)))
            except Exception:
                pass

            if not state:
                # Select best candidate: prioritize the first source that has a valid hdri_path
                for desc, cand in candidate_sources:
                    if cand and cand.get("hdri_path"):
                        state = cand
                        src_desc = desc
                        break

                if not state and candidate_sources:
                    src_desc, state = candidate_sources[0]

            if not state:
                if notify:
                    self.log("No saved DomeBreaker session or state found to restore.", "WARNING")
                return False

            self._restoring_state = True
            if hasattr(self, '_bake_timer') and self._bake_timer.isActive():
                self._bake_timer.stop()

            # Overlay live node parameters if dome light exists in /stage so external adjustments take precedence
            if dome_node is not None:
                tex_parm = dome_node.parm("xn__inputstexturefile_r3ah") or dome_node.parm("inputs:texture:file")
                if tex_parm and tex_parm.eval():
                    live_tex = tex_parm.eval().replace(chr(92), "/")
                    is_baked_tex = (
                        "_calibrated" in os.path.basename(live_tex) or
                        live_tex == state.get("_current_calibrated_path")
                    )
                    if not state.get("hdri_path"):
                        if not is_baked_tex:
                            state["hdri_path"] = live_tex
                        else:
                            clean_cand = _clean_hdri_path(live_tex)
                            if os.path.isfile(_resolve_image_path(clean_cand)):
                                state["hdri_path"] = clean_cand

                exp_parm = dome_node.parm("xn__inputsexposure_vya") or dome_node.parm("inputs:exposure")
                if exp_parm:
                    baked_ev = float(state.get("_baked_ev", 0.0))
                    state["ev"] = float(exp_parm.eval()) + baked_ev

                ry_parm = dome_node.parm("ry")
                if ry_parm:
                    state["yaw"] = float(ry_parm.eval())

                if sun_node is not None:
                    state["sun_en"] = not sun_node.isBypassed()
                    int_parm = sun_node.parm("xn__inputsintensity_i0a") or sun_node.parm("inputs:intensity")
                    if int_parm:
                        state["sun_intensity"] = float(int_parm.eval())
                    s_exp = sun_node.parm("xn__inputsexposure_vya") or sun_node.parm("inputs:exposure")
                    if s_exp:
                        state["sun_exposure"] = float(s_exp.eval())
                    cct_en = sun_node.parm("xn__inputsenableColorTemperature_omb") or sun_node.parm("inputs:enableColorTemperature")
                    if cct_en:
                        state["sun_use_cct"] = bool(cct_en.eval())
                    cct_p = sun_node.parm("xn__inputscolorTemperature_wcb") or sun_node.parm("inputs:colorTemperature")
                    if cct_p:
                        state["sun_cct"] = float(cct_p.eval())

                    s_rx = sun_node.parm("rx")
                    s_ry = sun_node.parm("ry")
                    if s_rx and s_ry:
                        rx_val = s_rx.eval()
                        ry_val = s_ry.eval()
                        state["sun_v"] = (rx_val / 180.0) + 0.5
                        dome_yaw = state.get("yaw", 0.0)
                        rel_ry = ry_val - dome_yaw
                        rel_ry = (rel_ry + 180.0) % 360.0 - 180.0
                        state["sun_u"] = 0.5 - (rel_ry / 360.0)

            # Self-heal any corrupted hdri_path that might have saved a _calibrated file
            saved_hdri = state.get("hdri_path", "").replace(chr(92), "/")
            if saved_hdri and ("_calibrated" in os.path.basename(saved_hdri)):
                candidate = _clean_hdri_path(saved_hdri)
                cand_exp = _resolve_image_path(candidate)
                if os.path.isfile(cand_exp):
                    saved_hdri = candidate
                    state["hdri_path"] = candidate

            # Block signals on all widgets to prevent premature triggers during restore
            widgets_to_block = [
                self.txt_hdri, self.txt_plate,
                self.sld_ev, self.sld_black, self.sld_temp, self.sld_tint, self.sld_yaw,
                self.sld_sat, self.sld_contrast,
                self.grp_horizon, self.sld_horizon_height, self.sld_horizon_feather,
                self.sld_sky_ev, self.sld_ground_ev,
                self.grp_softclip, self.sld_softclip_thresh, self.sld_softclip_rolloff,
                self.grp_sun, self.sld_sun_u, self.sld_sun_v, self.sld_sun_radius,
                self.sld_sun_angle, self.sld_sun_intensity, self.sld_sun_exposure,
                self.sld_sun_cct,
                self.grp_ground_proj, self.sld_tripod_height, self.sld_ground_radius, self.sld_ground_feather,
                self.sld_ground_roughness,
                self.grp_extract, self.sld_extract_count, self.sld_extract_dist, self.sld_extract_range, self.sld_extract_thresh
            ]
            for opt_name in ['chk_auto_detect_sun', 'chk_remove_sun', 'chk_sun_use_cct',
                             'chk_sun_reconstruct', 'sld_sun_turbidity', 'sld_sun_reconstruct_blend',
                             'cmb_sun_radiometry_mode', 'grp_aov', 'chk_aov_lightgroups',
                             'txt_dome_lg', 'txt_sun_lg', 'txt_practicals_lg',
                             'combo_input_cs', 'combo_plate_cs', 'chk_auto_cal', 'chk_protect_sun',
                             'combo_sky_mode', 'chk_auto_bake', 'chk_bake_ground_warp', 'chk_extract_inpaint',
                             'sld_room_width', 'sld_room_depth', 'sld_room_height',
                             'sld_cam_offset_x', 'sld_cam_offset_z', 'chk_room_floor',
                             'chk_room_ceiling', 'chk_room_walls', 'sld_room_subdivs', 'chk_room_double_sided',
                              'chk_room_shadows', 'chk_room_invisible', 'chk_arch_invisible', 'sld_extract_intensity', 'sld_extract_exposure',
                              'chk_snap_to_room', 'grp_lookdev', 'sld_lookdev_radius', 'sld_lookdev_spacing',
                              'sld_lookdev_height', 'sld_lookdev_pos_x', 'sld_lookdev_pos_z',
                              'chk_lookdev_stand', 'chk_lookdev_white',
                              'combo_arch_renderer_target',
                              'combo_arch_mat_mode', 'sld_arch_emissive_mult', 'combo_arch_tex_mode', 'combo_arch_planar_res',
                              'combo_ground_mat_mode', 'sld_ground_emissive_mult',
                              'combo_ground_tex_mode', 'combo_ground_planar_res']:
                if hasattr(self, opt_name):
                    widgets_to_block.append(getattr(self, opt_name))

            for w in widgets_to_block:
                w.blockSignals(True)

            try:
                # 1. Restore internal bake cache
                self._current_calibrated_path = state.get("_current_calibrated_path", None)
                self._baked_manual = state.get("_baked_manual", False)
                self._baked_ev = float(state.get("_baked_ev", 0.0))
                self._baked_black = float(state.get("_baked_black", 0.0))
                self._baked_temp = float(state.get("_baked_temp", 0.0))
                self._baked_tint = float(state.get("_baked_tint", 0.0))
                self._baked_sat = float(state.get("_baked_sat", 1.0))
                self._baked_contrast = float(state.get("_baked_contrast", 1.0))

                # 2. Update text edits
                hdri_path = state.get("hdri_path", "").replace(chr(92), "/")
                if hdri_path:
                    self.txt_hdri.setText(hdri_path)

                plate_path = state.get("plate_path", "").replace(chr(92), "/")
                if plate_path:
                    self.txt_plate.setText(plate_path)

                # 3. Update Calibration sliders
                if "ev" in state:
                    self.sld_ev.setValue(float(state["ev"]))
                if "black" in state:
                    self.sld_black.setValue(float(state["black"]))
                if "temp" in state:
                    t_val = float(state["temp"])
                    # Guard against legacy Kelvin 6500.0 value erroneously saved in hip file state
                    if t_val > 50.0 or t_val < -50.0:
                        t_val = 0.0
                    self.sld_temp.setRange(-1.0, 1.0)
                    self.sld_temp.setValue(t_val)
                if "tint" in state:
                    self.sld_tint.setRange(-1.0, 1.0)
                    self.sld_tint.setValue(float(state["tint"]))
                if "yaw" in state:
                    self.sld_yaw.setValue(float(state["yaw"]))
                if "sat" in state and hasattr(self, 'sld_sat'):
                    self.sld_sat.setValue(float(state["sat"]))
                if "contrast" in state and hasattr(self, 'sld_contrast'):
                    self.sld_contrast.setValue(float(state["contrast"]))

                # 4. Horizon split
                if "horizon_en" in state:
                    self.grp_horizon.setChecked(bool(state["horizon_en"]))
                if "horizon_h" in state:
                    self.sld_horizon_height.setValue(float(state["horizon_h"]))
                if "horizon_f" in state:
                    self.sld_horizon_feather.setValue(float(state["horizon_f"]))
                if "sky_ev" in state:
                    self.sld_sky_ev.setValue(float(state["sky_ev"]))
                if "sky_color" in state and hasattr(self, 'col_sky'):
                    c = state["sky_color"]
                    if isinstance(c, (list, tuple)) and len(c) >= 3:
                        self.col_sky.setColor(c[0], c[1], c[2])
                if "ground_ev" in state:
                    self.sld_ground_ev.setValue(float(state["ground_ev"]))
                if "ground_color" in state and hasattr(self, 'col_ground'):
                    c = state["ground_color"]
                    if isinstance(c, (list, tuple)) and len(c) >= 3:
                        self.col_ground.setColor(c[0], c[1], c[2])

                # 5. Soft clip
                if "softclip_en" in state:
                    self.grp_softclip.setChecked(bool(state["softclip_en"]))
                if "softclip_t" in state:
                    self.sld_softclip_thresh.setValue(float(state["softclip_t"]))
                if "softclip_r" in state:
                    self.sld_softclip_rolloff.setValue(float(state["softclip_r"]))

                # 6. Sun relighting & Physical Sun Reconstruction
                if "sun_en" in state:
                    self.grp_sun.setChecked(bool(state["sun_en"]))
                if "sun_auto" in state and hasattr(self, 'chk_auto_detect_sun'):
                    self.chk_auto_detect_sun.setChecked(bool(state["sun_auto"]))
                if "sun_remove" in state and hasattr(self, 'chk_remove_sun'):
                    self.chk_remove_sun.setChecked(bool(state["sun_remove"]))
                if "sun_u" in state:
                    self.sld_sun_u.setValue(float(state["sun_u"]))
                if "sun_v" in state:
                    self.sld_sun_v.setValue(float(state["sun_v"]))
                if "sun_radius" in state:
                    self.sld_sun_radius.setValue(float(state["sun_radius"]))
                if "sun_angle" in state:
                    self.sld_sun_angle.setValue(float(state["sun_angle"]))
                if "sun_intensity" in state and hasattr(self, 'sld_sun_intensity'):
                    self.sld_sun_intensity.setValue(float(state["sun_intensity"]))
                if "sun_exposure" in state and hasattr(self, 'sld_sun_exposure'):
                    self.sld_sun_exposure.setValue(float(state["sun_exposure"]))
                if "sun_use_cct" in state and hasattr(self, 'chk_sun_use_cct'):
                    self.chk_sun_use_cct.setChecked(bool(state["sun_use_cct"]))
                if "sun_cct" in state and hasattr(self, 'sld_sun_cct'):
                    self.sld_sun_cct.setValue(float(state["sun_cct"]))
                if "sun_reconstruct_en" in state and hasattr(self, 'chk_sun_reconstruct'):
                    self.chk_sun_reconstruct.setChecked(bool(state["sun_reconstruct_en"]))
                if "sun_turbidity" in state and hasattr(self, 'sld_sun_turbidity'):
                    self.sld_sun_turbidity.setValue(float(state["sun_turbidity"]))
                if "sun_reconstruct_blend" in state and hasattr(self, 'sld_sun_reconstruct_blend'):
                    self.sld_sun_reconstruct_blend.setValue(float(state["sun_reconstruct_blend"]))
                if "sun_radiometry_mode" in state and hasattr(self, 'cmb_sun_radiometry_mode'):
                    idx = self.cmb_sun_radiometry_mode.findText(state["sun_radiometry_mode"])
                    if idx >= 0:
                        self.cmb_sun_radiometry_mode.setCurrentIndex(idx)

                # 7. Production AOVs & Light Groups
                if "aov_en" in state and hasattr(self, 'grp_aov'):
                    self.grp_aov.setChecked(bool(state["aov_en"]))
                if "aov_lightgroups" in state and hasattr(self, 'chk_aov_lightgroups'):
                    self.chk_aov_lightgroups.setChecked(bool(state["aov_lightgroups"]))
                if "lg_dome" in state and hasattr(self, 'txt_dome_lg'):
                    self.txt_dome_lg.setText(str(state["lg_dome"]))
                if "lg_sun" in state and hasattr(self, 'txt_sun_lg'):
                    self.txt_sun_lg.setText(str(state["lg_sun"]))
                if "lg_practicals" in state and hasattr(self, 'txt_practicals_lg'):
                    self.txt_practicals_lg.setText(str(state["lg_practicals"]))

                # 8. Ground Projection & Room Box
                if "ground_proj_en" in state:
                    self.grp_ground_proj.setChecked(bool(state["ground_proj_en"]))
                if "proj_mode" in state and hasattr(self, 'combo_proj_mode'):
                    idx = 1 if state["proj_mode"] == "room_box" else 0
                    self.combo_proj_mode.setCurrentIndex(idx)
                    if hasattr(self, '_on_proj_mode_changed'):
                        self._on_proj_mode_changed(idx)
                if "tripod_height" in state:
                    self.sld_tripod_height.setValue(float(state["tripod_height"]))
                if "ground_radius" in state:
                    self.sld_ground_radius.setValue(float(state["ground_radius"]))
                if "ground_feather" in state:
                    self.sld_ground_feather.setValue(float(state["ground_feather"]))
                if "ground_roughness" in state and hasattr(self, 'sld_ground_roughness'):
                    self.sld_ground_roughness.setValue(float(state["ground_roughness"]))
                if "bake_ground_warp" in state and hasattr(self, 'chk_bake_ground_warp'):
                    self.chk_bake_ground_warp.setChecked(bool(state["bake_ground_warp"]))
                if "room_width" in state and hasattr(self, 'sld_room_width'):
                    self.sld_room_width.setValue(float(state["room_width"]))
                if "room_depth" in state and hasattr(self, 'sld_room_depth'):
                    self.sld_room_depth.setValue(float(state["room_depth"]))
                if "room_height" in state and hasattr(self, 'sld_room_height'):
                    self.sld_room_height.setValue(float(state["room_height"]))
                if "cam_offset_x" in state and hasattr(self, 'sld_cam_offset_x'):
                    self.sld_cam_offset_x.setValue(float(state["cam_offset_x"]))
                if "cam_offset_z" in state and hasattr(self, 'sld_cam_offset_z'):
                    self.sld_cam_offset_z.setValue(float(state["cam_offset_z"]))
                if "room_floor" in state and hasattr(self, 'chk_room_floor'):
                    self.chk_room_floor.setChecked(bool(state["room_floor"]))
                if "room_ceiling" in state and hasattr(self, 'chk_room_ceiling'):
                    self.chk_room_ceiling.setChecked(bool(state["room_ceiling"]))
                if "room_walls" in state and hasattr(self, 'chk_room_walls'):
                    self.chk_room_walls.setChecked(bool(state["room_walls"]))
                if "room_subdivs" in state and hasattr(self, 'sld_room_subdivs'):
                    self.sld_room_subdivs.setValue(float(state["room_subdivs"]))
                if "room_double_sided" in state and hasattr(self, 'chk_room_double_sided'):
                    self.chk_room_double_sided.setChecked(bool(state["room_double_sided"]))
                if "room_shadows" in state and hasattr(self, 'chk_room_shadows'):
                    self.chk_room_shadows.setChecked(bool(state["room_shadows"]))
                if "room_invisible" in state and hasattr(self, 'chk_room_invisible'):
                    self.chk_room_invisible.setChecked(bool(state["room_invisible"]))
                if "arch_invisible" in state and hasattr(self, 'chk_arch_invisible'):
                    self.chk_arch_invisible.setChecked(bool(state["arch_invisible"]))
                if "arch_mat_mode" in state and hasattr(self, 'combo_arch_mat_mode'):
                    self.combo_arch_mat_mode.setCurrentIndex(int(state["arch_mat_mode"]))
                if "arch_renderer_target_idx" in state and hasattr(self, 'combo_arch_renderer_target'):
                    self.combo_arch_renderer_target.setCurrentIndex(int(state["arch_renderer_target_idx"]))
                elif "arch_renderer_target" in state and hasattr(self, 'combo_arch_renderer_target'):
                    art = str(state["arch_renderer_target"]).lower()
                    _m = {"all": 0, "arnold": 1, "karma": 2, "redshift": 3, "preview": 4}
                    self.combo_arch_renderer_target.setCurrentIndex(_m.get(art, 0))
                if "arch_emissive_mult" in state and hasattr(self, 'sld_arch_emissive_mult'):
                    self.sld_arch_emissive_mult.setValue(float(state["arch_emissive_mult"]))
                if "arch_tex_mode" in state and hasattr(self, 'combo_arch_tex_mode'):
                    self.combo_arch_tex_mode.setCurrentIndex(int(state["arch_tex_mode"]))
                if "arch_planar_res" in state and hasattr(self, 'combo_arch_planar_res'):
                    idx = self.combo_arch_planar_res.findText(str(state["arch_planar_res"]))
                    if idx >= 0:
                        self.combo_arch_planar_res.setCurrentIndex(idx)
                if "ground_mat_mode_idx" in state and hasattr(self, 'combo_ground_mat_mode'):
                    self.combo_ground_mat_mode.setCurrentIndex(int(state["ground_mat_mode_idx"]))
                elif "ground_mat_mode" in state and hasattr(self, 'combo_ground_mat_mode'):
                    gm = str(state["ground_mat_mode"]).lower()
                    idx = 1 if "emissive" in gm and "pbr" not in gm else (2 if "pbr_emissive" in gm or "fill" in gm else 0)
                    self.combo_ground_mat_mode.setCurrentIndex(idx)
                if "ground_emissive_mult" in state and hasattr(self, 'sld_ground_emissive_mult'):
                    self.sld_ground_emissive_mult.setValue(float(state["ground_emissive_mult"]))
                if "renderer_target_idx" in state and hasattr(self, 'combo_renderer_target'):
                    self.combo_renderer_target.setCurrentIndex(int(state["renderer_target_idx"]))
                if "ground_tex_mode_idx" in state and hasattr(self, 'combo_ground_tex_mode'):
                    self.combo_ground_tex_mode.setCurrentIndex(int(state["ground_tex_mode_idx"]))
                elif "ground_tex_mode" in state and hasattr(self, 'combo_ground_tex_mode'):
                    gtm = str(state["ground_tex_mode"]).lower()
                    self.combo_ground_tex_mode.setCurrentIndex(0 if "planar" in gtm else 1)
                if "ground_planar_res" in state and hasattr(self, 'combo_ground_planar_res'):
                    idx = self.combo_ground_planar_res.findText(str(state["ground_planar_res"]))
                    if idx >= 0:
                        self.combo_ground_planar_res.setCurrentIndex(idx)
                elif "ground_planar_res_idx" in state and hasattr(self, 'combo_ground_planar_res'):
                    self.combo_ground_planar_res.setCurrentIndex(int(state["ground_planar_res_idx"]))
                if "disc_proj_method_idx" in state and hasattr(self, 'combo_disc_proj_method'):
                    self.combo_disc_proj_method.setCurrentIndex(int(state["disc_proj_method_idx"]))
                elif "disc_proj_method" in state and hasattr(self, 'combo_disc_proj_method'):
                    self.combo_disc_proj_method.setCurrentIndex(0 if state["disc_proj_method"] == "top_view" else 1)

                # 9. Multi-Light Extraction
                if "extract_en" in state:
                    self.grp_extract.setChecked(bool(state["extract_en"]))
                if "extract_count" in state:
                    self.sld_extract_count.setValue(int(state["extract_count"]))
                if "extract_range" in state and hasattr(self, "sld_extract_range"):
                    self.sld_extract_range.setValue(float(state["extract_range"]))
                if "extract_thresh" in state:
                    self.sld_extract_thresh.setValue(float(state["extract_thresh"]))
                if "extract_dist" in state and hasattr(self, 'sld_extract_dist'):
                    self.sld_extract_dist.setValue(float(state["extract_dist"]))
                if "extract_intensity" in state and hasattr(self, 'sld_extract_intensity'):
                    self.sld_extract_intensity.setValue(float(state["extract_intensity"]))
                if "extract_exposure" in state and hasattr(self, 'sld_extract_exposure'):
                    self.sld_extract_exposure.setValue(float(state["extract_exposure"]))
                if "extract_inpaint" in state and hasattr(self, 'chk_extract_inpaint'):
                    self.chk_extract_inpaint.setChecked(bool(state["extract_inpaint"]))
                if "snap_to_room" in state and hasattr(self, 'chk_snap_to_room'):
                    self.chk_snap_to_room.setChecked(bool(state["snap_to_room"]))

                # 9b. Lookdev Rig
                if "lookdev_en" in state and hasattr(self, 'grp_lookdev'):
                    self.grp_lookdev.setChecked(bool(state["lookdev_en"]))
                if "lookdev_radius" in state and hasattr(self, 'sld_lookdev_radius'):
                    self.sld_lookdev_radius.setValue(float(state["lookdev_radius"]))
                if "lookdev_spacing" in state and hasattr(self, 'sld_lookdev_spacing'):
                    self.sld_lookdev_spacing.setValue(float(state["lookdev_spacing"]))
                if "lookdev_height" in state and hasattr(self, 'sld_lookdev_height'):
                    self.sld_lookdev_height.setValue(float(state["lookdev_height"]))
                if "lookdev_pos_x" in state and hasattr(self, 'sld_lookdev_pos_x'):
                    self.sld_lookdev_pos_x.setValue(float(state["lookdev_pos_x"]))
                if "lookdev_pos_z" in state and hasattr(self, 'sld_lookdev_pos_z'):
                    self.sld_lookdev_pos_z.setValue(float(state["lookdev_pos_z"]))
                if "lookdev_stand" in state and hasattr(self, 'chk_lookdev_stand'):
                    self.chk_lookdev_stand.setChecked(bool(state["lookdev_stand"]))
                if "lookdev_white" in state and hasattr(self, 'chk_lookdev_white'):
                    self.chk_lookdev_white.setChecked(bool(state["lookdev_white"]))

                # 10. Color Space, Auto Cal, Protect Sun, Sky mode & auto bake
                if "input_cs" in state and hasattr(self, 'combo_input_cs'):
                    idx = self.combo_input_cs.findText(state["input_cs"])
                    if idx >= 0:
                        self.combo_input_cs.setCurrentIndex(idx)
                if "plate_cs" in state and hasattr(self, 'combo_plate_cs'):
                    idx = self.combo_plate_cs.findText(state["plate_cs"])
                    if idx >= 0:
                        self.combo_plate_cs.setCurrentIndex(idx)
                if "auto_cal" in state and hasattr(self, 'chk_auto_cal'):
                    self.chk_auto_cal.setChecked(bool(state["auto_cal"]))
                if "protect_sun" in state and hasattr(self, 'chk_protect_sun'):
                    self.chk_protect_sun.setChecked(bool(state["protect_sun"]))
                if "sky_mode" in state and hasattr(self, 'combo_sky_mode'):
                    idx = self.combo_sky_mode.findData(state["sky_mode"])
                    if idx >= 0:
                        self.combo_sky_mode.setCurrentIndex(idx)
                if "auto_bake" in state and hasattr(self, 'chk_auto_bake'):
                    self.chk_auto_bake.setChecked(bool(state["auto_bake"]))

                # 11. Load previews while _restoring_state = True (auto_cal and auto_bake will NOT fire)
                if hdri_path:
                    exp_hdri = _resolve_image_path(hdri_path)
                    if exp_hdri and os.path.isfile(exp_hdri):
                        self._load_hdri_preview(exp_hdri)
                    elif os.path.isfile(hdri_path):
                        self._load_hdri_preview(hdri_path)
                    else:
                        self.log(f"HDRI file '{hdri_path}' from {src_desc} not found on disk.", "WARNING")

                if plate_path:
                    exp_plate = _resolve_image_path(plate_path)
                    if exp_plate and os.path.isfile(exp_plate):
                        self._load_plate_preview(exp_plate)
                    elif os.path.isfile(plate_path):
                        self._load_plate_preview(plate_path)

                if "custom_props" in state and hasattr(self, '_set_custom_props_data'):
                    self._set_custom_props_data(state["custom_props"])

            finally:
                for w in widgets_to_block:
                    w.blockSignals(False)

            self._restoring_state = False

            # Stop any timers that might have been queued
            if hasattr(self, '_bake_timer') and self._bake_timer.isActive():
                self._bake_timer.stop()

            # Restore collapsed/expanded sections state
            collapsed_map = state.get("collapsed_sections", {})
            if isinstance(collapsed_map, dict):
                for sec in getattr(self, '_collapsible_sections', []):
                    if sec.title() in collapsed_map:
                        sec.set_collapsed(bool(collapsed_map[sec.title()]))

            # Restore main tab index (DomeBreaker vs 3DGS)
            if "main_tab_idx" in state and hasattr(self, 'tabs_main'):
                self.tabs_main.setCurrentIndex(int(state["main_tab_idx"]))

            # 12. Sync stage nodes and previews once
            self._sync_node()
            self._update_all_previews()

            msg = f"Successfully restored HDRI session from {src_desc}."
            self.log(msg, "SUCCESS")

            if self._crucible_panel and hasattr(self._crucible_panel, "light_manager"):
                try:
                    self._crucible_panel.light_manager.refresh_lights()
                except Exception:
                    pass

            return True
        except Exception as err:
            self._restoring_state = False
            self.log_error("Error restoring stage state", err)
            return False

    def reset_to_defaults(self, confirm=True):
        """Reset all DomeBreaker settings, stage overrides, and cached bakes back to factory defaults."""
        if confirm and hou.isUIAvailable():
            res = QtWidgets.QMessageBox.question(
                self,
                "Reset to Defaults",
                "Are you sure you want to reset all parameters, stage nodes, and bakes back to factory defaults?\n\n"
                "This will clear all calibrations, remove practical lights, and restore the original HDRI.",
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                QtWidgets.QMessageBox.No
            )
            if res != QtWidgets.QMessageBox.Yes:
                return

        self.log("Action: Reset to Defaults initiated...", "INFO")

        # Stop any background bake
        if hasattr(self, '_bake_timer') and self._bake_timer.isActive():
            self._bake_timer.stop()

        # Block signals during batch widget reset
        self._restoring_state = True
        widgets_to_block = [
            self.sld_ev, self.sld_black, self.sld_temp, self.sld_tint, self.sld_yaw,
            self.sld_sat, self.sld_contrast,
            self.grp_horizon, self.sld_horizon_height, self.sld_horizon_feather,
            self.sld_sky_ev, self.sld_ground_ev,
            self.grp_softclip, self.sld_softclip_thresh, self.sld_softclip_rolloff,
            self.grp_sun, self.sld_sun_u, self.sld_sun_v, self.sld_sun_radius,
            self.sld_sun_angle, self.sld_sun_intensity, self.sld_sun_exposure,
            self.sld_sun_cct,
            self.grp_ground_proj, self.sld_tripod_height, self.sld_ground_radius, self.sld_ground_feather,
            self.sld_ground_roughness,
            self.grp_extract, self.sld_extract_count, self.sld_extract_dist, self.sld_extract_range, self.sld_extract_thresh,
            self.sld_extract_intensity, self.sld_extract_exposure
        ]
        for opt_name in ['chk_auto_detect_sun', 'chk_remove_sun', 'chk_sun_use_cct',
                         'chk_sun_reconstruct', 'sld_sun_turbidity', 'sld_sun_reconstruct_blend',
                         'cmb_sun_radiometry_mode', 'grp_aov', 'chk_aov_lightgroups',
                         'txt_dome_lg', 'txt_sun_lg', 'txt_practicals_lg',
                         'combo_input_cs', 'combo_plate_cs', 'chk_auto_cal', 'chk_protect_sun',
                         'combo_sky_mode', 'combo_proj_mode', 'chk_auto_bake', 'chk_bake_ground_warp', 'chk_extract_inpaint',
                         'sld_room_width', 'sld_room_depth', 'sld_room_height',
                         'sld_cam_offset_x', 'sld_cam_offset_z', 'chk_room_floor',
                         'chk_room_ceiling', 'chk_room_walls', 'sld_room_subdivs', 'chk_room_double_sided',
                         'chk_room_shadows', 'chk_room_invisible', 'chk_arch_invisible', 'chk_snap_to_room', 'grp_lookdev', 'sld_lookdev_radius', 'sld_lookdev_spacing',
                         'sld_lookdev_height', 'sld_lookdev_pos_x', 'sld_lookdev_pos_z',
                         'chk_lookdev_stand', 'chk_lookdev_white',
                         'combo_arch_renderer_target', 'combo_arch_mat_mode', 'sld_arch_emissive_mult',
                         'combo_arch_tex_mode', 'combo_arch_planar_res',
                         'combo_ground_mat_mode', 'sld_ground_emissive_mult',
                         'combo_ground_tex_mode', 'combo_ground_planar_res', 'combo_renderer_target']:
            if hasattr(self, opt_name):
                widgets_to_block.append(getattr(self, opt_name))

        for w in widgets_to_block:
            w.blockSignals(True)

        try:
            # 1. Reset Internal Cache
            self._current_calibrated_path = None
            self._baked_manual = False
            self._baked_ev = 0.0
            self._baked_black = 0.0
            self._baked_temp = 0.0
            self._baked_tint = 0.0
            self._baked_sat = 1.0
            self._baked_contrast = 1.0

            # 2. Calibration Section
            self.sld_ev.setRange(-10.0, 10.0)
            self.sld_ev.setValue(0.0)
            self.sld_black.setRange(-0.5, 0.5)
            self.sld_black.setValue(0.0)
            self.sld_temp.setRange(-1.0, 1.0)
            self.sld_temp.setValue(0.0)
            self.sld_tint.setRange(-1.0, 1.0)
            self.sld_tint.setValue(0.0)
            if hasattr(self, 'sld_sat'):
                self.sld_sat.setRange(0.0, 3.0)
                self.sld_sat.setValue(1.0)
            if hasattr(self, 'sld_contrast'):
                self.sld_contrast.setRange(0.2, 2.5)
                self.sld_contrast.setValue(1.0)
            self.sld_yaw.setValue(0.0)
            if hasattr(self, 'combo_input_cs'):
                self.combo_input_cs.setCurrentIndex(0)
            if hasattr(self, 'combo_plate_cs'):
                self.combo_plate_cs.setCurrentIndex(0)
            if hasattr(self, 'chk_auto_cal'):
                self.chk_auto_cal.setChecked(False)
            if hasattr(self, 'chk_protect_sun'):
                self.chk_protect_sun.setChecked(False)
            if hasattr(self, 'chk_auto_bake'):
                self.chk_auto_bake.setChecked(True)

            # 3. Horizon Section
            if hasattr(self, 'grp_horizon'):
                self.grp_horizon.setChecked(False)
            self.sld_horizon_height.setValue(0.0)
            self.sld_horizon_feather.setValue(0.1)
            self.sld_sky_ev.setValue(0.0)
            self.sld_ground_ev.setValue(0.0)
            if hasattr(self, 'col_sky'):
                self.col_sky.setColor([1.0, 1.0, 1.0])
            if hasattr(self, 'col_ground'):
                self.col_ground.setColor([1.0, 1.0, 1.0])

            # 4. Softclip Section
            if hasattr(self, 'grp_softclip'):
                self.grp_softclip.setChecked(False)
            self.sld_softclip_thresh.setValue(10.0)
            self.sld_softclip_rolloff.setValue(0.5)

            # 5. Sun Section
            if hasattr(self, 'grp_sun'):
                self.grp_sun.setChecked(True)
            self.chk_auto_detect_sun.setChecked(True)
            if hasattr(self, 'chk_remove_sun'):
                self.chk_remove_sun.setChecked(False)
            if hasattr(self, 'sld_sun_intensity'):
                self.sld_sun_intensity.setValue(1.0)
            if hasattr(self, 'sld_sun_exposure'):
                self.sld_sun_exposure.setValue(0.0)
            self.sld_sun_angle.setValue(0.53)
            if hasattr(self, 'chk_sun_use_cct'):
                self.chk_sun_use_cct.setChecked(True)
            if hasattr(self, 'sld_sun_cct'):
                self.sld_sun_cct.setValue(5500.0)
            if hasattr(self, 'chk_sun_reconstruct'):
                self.chk_sun_reconstruct.setChecked(True)
            if hasattr(self, 'sld_sun_turbidity'):
                self.sld_sun_turbidity.setValue(3.0)
            if hasattr(self, 'sld_sun_reconstruct_blend'):
                self.sld_sun_reconstruct_blend.setValue(100.0)
            if hasattr(self, 'cmb_sun_radiometry_mode'):
                self.cmb_sun_radiometry_mode.setCurrentIndex(0)

            # 6. Ground Projection / Room Box Section
            if hasattr(self, 'combo_proj_mode'):
                self.combo_proj_mode.setCurrentIndex(0)
            if hasattr(self, 'widget_disc_controls'):
                self.widget_disc_controls.setVisible(True)
            if hasattr(self, 'widget_room_controls'):
                self.widget_room_controls.setVisible(False)
            if hasattr(self, 'grp_ground_proj'):
                self.grp_ground_proj.setChecked(False)
            self.sld_tripod_height.setValue(1.5)
            self.sld_ground_radius.setValue(10.0)
            self.sld_ground_feather.setValue(0.15)
            if hasattr(self, 'sld_ground_roughness'):
                self.sld_ground_roughness.setValue(1.0)
            if hasattr(self, 'chk_bake_ground_warp'):
                self.chk_bake_ground_warp.setChecked(True)
            if hasattr(self, 'sld_room_width'):
                self.sld_room_width.setValue(8.0)
            if hasattr(self, 'sld_room_depth'):
                self.sld_room_depth.setValue(10.0)
            if hasattr(self, 'sld_room_height'):
                self.sld_room_height.setValue(3.5)
            if hasattr(self, 'sld_cam_offset_x'):
                self.sld_cam_offset_x.setValue(0.0)
            if hasattr(self, 'sld_cam_offset_z'):
                self.sld_cam_offset_z.setValue(0.0)
            if hasattr(self, 'chk_room_floor'):
                self.chk_room_floor.setChecked(True)
            if hasattr(self, 'chk_room_ceiling'):
                self.chk_room_ceiling.setChecked(True)
            if hasattr(self, 'chk_room_walls'):
                self.chk_room_walls.setChecked(True)
            if hasattr(self, 'sld_room_subdivs'):
                self.sld_room_subdivs.setValue(16.0)
            if hasattr(self, 'chk_room_double_sided'):
                self.chk_room_double_sided.setChecked(False)
            if hasattr(self, 'chk_room_shadows'):
                self.chk_room_shadows.setChecked(False)
            if hasattr(self, 'chk_room_invisible'):
                self.chk_room_invisible.setChecked(False)
            if hasattr(self, 'chk_arch_invisible'):
                self.chk_arch_invisible.setChecked(False)
            if hasattr(self, 'combo_renderer_target'):
                self.combo_renderer_target.setCurrentIndex(0)
            if hasattr(self, 'combo_ground_tex_mode'):
                self.combo_ground_tex_mode.setCurrentIndex(0)
            if hasattr(self, 'combo_ground_planar_res'):
                self.combo_ground_planar_res.setCurrentIndex(1)
            if hasattr(self, 'combo_arch_renderer_target'):
                self.combo_arch_renderer_target.setCurrentIndex(0)
            if hasattr(self, 'combo_arch_mat_mode'):
                self.combo_arch_mat_mode.setCurrentIndex(0)
            if hasattr(self, 'sld_arch_emissive_mult'):
                self.sld_arch_emissive_mult.setValue(1.0)
            if hasattr(self, 'tabs_main'):
                self.tabs_main.setCurrentIndex(0)

            # 7. Multi-Light Extraction Section
            if hasattr(self, 'grp_extract'):
                self.grp_extract.setChecked(False)
            self.sld_extract_count.setValue(3)
            self.sld_extract_dist.setValue(10.0)
            if hasattr(self, 'chk_snap_to_room'):
                self.chk_snap_to_room.setChecked(True)
            if hasattr(self, 'sld_extract_intensity'):
                self.sld_extract_intensity.setValue(1.0)
            if hasattr(self, 'sld_extract_exposure'):
                self.sld_extract_exposure.setValue(0.0)
            self.sld_extract_range.setValue(8.0)
            self.sld_extract_thresh.setValue(3.0)
            if hasattr(self, 'chk_extract_inpaint'):
                self.chk_extract_inpaint.setChecked(True)

            # 8. AOVs Section
            if hasattr(self, 'grp_aov'):
                self.grp_aov.setChecked(True)
            if hasattr(self, 'chk_aov_lightgroups'):
                self.chk_aov_lightgroups.setChecked(True)
            if hasattr(self, 'txt_dome_lg'):
                self.txt_dome_lg.setText("lg_dome")
            if hasattr(self, 'txt_sun_lg'):
                self.txt_sun_lg.setText("lg_sun")
            if hasattr(self, 'txt_practicals_lg'):
                self.txt_practicals_lg.setText("lg_practicals")

            # 9. Lookdev Section
            if hasattr(self, 'grp_lookdev'):
                self.grp_lookdev.setChecked(True)
            if hasattr(self, 'sld_lookdev_radius'):
                self.sld_lookdev_radius.setValue(0.15)
            if hasattr(self, 'sld_lookdev_spacing'):
                self.sld_lookdev_spacing.setValue(0.45)
            if hasattr(self, 'sld_lookdev_height'):
                self.sld_lookdev_height.setValue(1.0)
            if hasattr(self, 'sld_lookdev_pos_x'):
                self.sld_lookdev_pos_x.setValue(0.0)
            if hasattr(self, 'sld_lookdev_pos_z'):
                self.sld_lookdev_pos_z.setValue(0.0)
            if hasattr(self, 'chk_lookdev_stand'):
                self.chk_lookdev_stand.setChecked(True)
            if hasattr(self, 'chk_lookdev_white'):
                self.chk_lookdev_white.setChecked(False)

        finally:
            for w in widgets_to_block:
                w.blockSignals(False)
            self._restoring_state = False

        # 10. Clean up / stage reset
        try:
            stage_node = self._get_stage_node()
            if stage_node:
                # Remove practical lights
                for child in list(stage_node.children()):
                    if child.name().startswith("practical_"):
                        child.destroy()
                # Bypass / hide ground projection node and material library
                proj_node = stage_node.node("hdri_match_projection")
                if proj_node:
                    proj_node.bypass(True)
                matlib_node = stage_node.node("hdri_match_materials")
                if matlib_node:
                    matlib_node.bypass(True)

                # Reset dome light texture to raw original HDRI
                dome_node = stage_node.node("hdri_dome")
                if dome_node:
                    raw_hdri = self.txt_hdri.text().strip()
                    if raw_hdri:
                        tex_p = dome_node.parm("xn__inputstexturefile_r3ah") or dome_node.parm("inputs:texture:file")
                        if tex_p:
                            tex_p.set(raw_hdri)
                    exp_p = dome_node.parm("xn__inputsexposure_vya") or dome_node.parm("inputs:exposure")
                    if exp_p:
                        exp_p.set(0.0)
                    ry_p = dome_node.parm("ry")
                    if ry_p:
                        ry_p.set(0.0)

                # Reset sun light
                sun_node = stage_node.node("hdri_sun")
                if sun_node:
                    int_p = sun_node.parm("xn__inputsintensity_i0a") or sun_node.parm("inputs:intensity")
                    if int_p:
                        int_p.set(1.0)
                    exp_p = sun_node.parm("xn__inputsexposure_vya") or sun_node.parm("inputs:exposure")
                    if exp_p:
                        exp_p.set(0.0)

            # Re-sync node and clean persist state
            self._sync_node()
            self._save_state()
            self._update_all_previews()
            self.log("All DomeBreaker settings reset to factory defaults. Stage cleaned.", "SUCCESS")
            if hou.isUIAvailable():
                hou.ui.triggerUpdate()
        except Exception as e:
            self.log(f"Warning during stage reset: {e}", "WARNING")

    def _reload_tool(self):
        """
        Completely reload all hdri_match & solaris python modules,
        and automatically refresh the Houdini Python Panel in-place so all
        changes take effect instantly.
        """
        self.log("Action: Reload Tool initiated...", "INFO")
        try:
            self._save_state()
        except Exception:
            pass
        import importlib

        modules_to_reload = [
            "hdri_match_solaris.oiio_adapter",
            "hdri_match_solaris.drop_target",
            "hdri_match_solaris.pipeline",
            "hdri_match_solaris.sun_detector",
            "hdri_match_solaris.light_extractor",
            "hdri_match_solaris.gaussian_splat",
            "hdri_match_solaris.lop_splat",
            "hdri_match_solaris.panel_ui",
            "hdri_match_solaris",
            "CrucibleLightStudio.core.backend_houdini",
            "CrucibleLightStudio.core.preset_manager",
            "CrucibleLightStudio.ui.widgets.asset_library",
            "CrucibleLightStudio.ui.widgets.light_manager",
            "CrucibleLightStudio.ui.widgets.gradient_builder",
            "CrucibleLightStudio.ui.main_panel",
            "hdri_match.io.loader",
            "hdri_match.analysis.hdri_stats",
            "hdri_match.core.pipeline",
            "hdri_match.core.projection",
            "hdri_match.core.inpaint",
            "hdri_match",
        ]
        _ensure_crucible_environment()

        reloaded = []
        for mod_name in modules_to_reload:
            if mod_name in sys.modules:
                try:
                    importlib.reload(sys.modules[mod_name])
                    reloaded.append(mod_name)
                except Exception as e:
                    self.log(f"Warning reloading {mod_name}: {e}", "WARNING")

        self.log(f"Reloaded {len(reloaded)} modules: {', '.join(reloaded)}", "SUCCESS")

        # In Houdini GUI, safely refresh the active pane tab via singleShot
        if hou.isUIAvailable():
            target_interfaces = ["domebreaker_panel", "hdri_match_panel"]
            interface_name = None
            for iface in target_interfaces:
                if iface in hou.pypanel.interfaces():
                    interface_name = iface
                    break
            if interface_name:
                def _do_refresh():
                    try:
                        interface = hou.pypanel.interfaces()[interface_name]
                        desk = hou.ui.curDesktop()
                        # Search docked tabs
                        for pane in desk.paneTabs():
                            if pane.type() == hou.paneTabType.PythonPanel:
                                act = pane.activeInterface()
                                if act and act.name() in target_interfaces:
                                    pane.setActiveInterface(interface)
                                    return
                        # Search floating panels
                        for fp in desk.floatingPanels():
                            for p in fp.panes():
                                for tab in p.tabs():
                                    if tab.type() == hou.paneTabType.PythonPanel:
                                        act = tab.activeInterface()
                                        if act and act.name() in target_interfaces:
                                            tab.setActiveInterface(interface)
                                            return
                    except Exception:
                        pass
                QtCore.QTimer.singleShot(50, _do_refresh)
            hou.ui.setStatusMessage("DomeBreaker tool reloaded successfully.", severity=hou.severityType.Message)
        else:
            print("[DomeBreaker] Tool and modules reloaded successfully.")

    def _reload_modules(self):
        """Alias for _reload_tool for backward compatibility."""
        self._reload_tool()

    # ------------------------------------------------------------------
    # File browsing & Preview Handling
    # ------------------------------------------------------------------

    def _browse_hdri(self):
        self.log("Opening file browser for HDRI...", "INFO")
        if hou.isUIAvailable():
            path = hou.ui.selectFile(
                title="Select HDRI File",
                file_type=hou.fileType.Image,
                pattern="*.exr *.hdr *.hdri",
            )
            if path:
                path = hou.expandString(path)
                self.txt_hdri.setText(path)
                self.log(f"HDRI path selected: {path}", "SUCCESS")
                self._load_hdri_preview(path)
            else:
                self.log("HDRI file selection cancelled.", "DETAIL")
        else:
            self.log("Browse HDRI called in non-UI mode.", "DETAIL")

    def _browse_plate(self):
        if hou.isUIAvailable():
            path = hou.ui.selectFile(
                title="Select Plate File",
                file_type=hou.fileType.Image,
                pattern="*.exr *.jpg *.jpeg *.png *.tif *.tiff",
            )
            if path:
                path = hou.expandString(path)
                self.txt_plate.setText(path)
        else:
            print("Browse Plate requested in non-UI mode.")

    def _on_hdri_text_changed(self, text):
        clean_text = text.strip()
        self._load_hdri_preview(clean_text)
        self._sync_node()
        self._save_state()

    def _on_plate_text_changed(self, text):
        clean_text = text.strip()
        self._load_plate_preview(clean_text)
        self._sync_node()
        self._save_state()

    def _on_preview_mode_changed(self, idx):
        self.preview_stack.setCurrentIndex(idx)
        self._update_all_previews()

    def _load_hdri_preview(self, path=None):
        if path is None:
            path = self.txt_hdri.text()
        path = _resolve_image_path(path)
        if path and ("_calibrated" in os.path.basename(path)):
            candidate = _clean_hdri_path(path)
            cand_exp = _resolve_image_path(candidate)
            if os.path.isfile(cand_exp):
                path = cand_exp
        if not path or not os.path.isfile(path):
            self.lbl_hdri_preview.setPixmap(QtGui.QPixmap())
            self.lbl_hdri_preview.setText("No HDRI loaded")
            self.lbl_single_hdri.setPixmap(QtGui.QPixmap())
            self.lbl_single_hdri.setText("No HDRI loaded")
            self.lbl_hdri_info.setText("---")
            self._hdri_thumb_raw = None
            self._update_all_previews()
            return

        try:
            if not getattr(self, '_restoring_state', False):
                self._current_calibrated_path = None
                self._baked_manual = False
                self._baked_ev = 0.0
                self._baked_black = 0.0
                self._baked_temp = 0.0
                self._baked_tint = 0.0
                self._baked_sat = 1.0
                self._baked_contrast = 1.0
            self.log(f"Loading HDRI: {path}...", "INFO")
            arr = None
            try:
                from hdri_match.io.loader import load_exr_to_numpy
                arr = load_exr_to_numpy(path)
            except Exception:
                try:
                    from hdri_match_solaris.oiio_adapter import load_image_with_oiio
                    arr = load_image_with_oiio(path)
                except Exception as e:
                    self.log_error(f"Error loading HDRI preview from {path}: {e}")
                    return

            if arr is None:
                self.log_error(f"Failed reading HDRI array from {path}")
                return

            h, w = arr.shape[:2]
            target_h, target_w = 240, 480
            try:
                import cv2
                thumb = cv2.resize(arr[..., :3], (target_w, target_h), interpolation=cv2.INTER_AREA)
                self._hdri_orig_thumb = thumb.astype(np.float32)
            except Exception:
                try:
                    from PIL import Image
                    p_img = Image.fromarray(np.clip(arr[..., :3] * 255.0, 0, 255).astype(np.uint8))
                    resample_filter = Image.Resampling.LANCZOS if hasattr(Image, "Resampling") else Image.LANCZOS
                    p_img = p_img.resize((target_w, target_h), resample_filter)
                    self._hdri_orig_thumb = (np.array(p_img, dtype=np.float32) / 255.0)
                except Exception:
                    y_idx = np.linspace(0, h - 1, target_h).astype(int)
                    x_idx = np.linspace(0, w - 1, target_w).astype(int)
                    self._hdri_orig_thumb = arr[y_idx][:, x_idx, :3].astype(np.float32)
            self._hdri_orig_full = arr[..., :3].astype(np.float32)
            self._hdri_orig_shape = (w, h)
            self._apply_input_colorspace(notify_ui=False)
            self.lbl_hdri_info.setText(f"{w}x{h} | {os.path.basename(path)}")
            self.log(f"HDRI loaded successfully ({w}x{h}, {os.path.basename(path)})", "SUCCESS")

            # Auto-analyze practical hotspots on HDRI load
            if not getattr(self, '_restoring_state', False) and hasattr(self, 'lbl_hotspot_info'):
                try:
                    self._analyze_hdri_hotspots(notify_ui=False)
                except Exception:
                    pass

            # Auto-detect sun and extract physical values on HDRI load (skip if restoring existing state)
            if not getattr(self, '_restoring_state', False) and hasattr(self, 'chk_auto_detect_sun') and self.chk_auto_detect_sun.isChecked():
                try:
                    from hdri_match.analysis.hdri_stats import HDRIStats
                    pos = HDRIStats.compute_sun_position(arr)
                    u, v = pos["uv"]
                    self.sld_sun_u.setValue(float(u))
                    self.sld_sun_v.setValue(float(v))
                    self._extract_and_apply_sun_values(arr)
                except Exception:
                    pass
        except Exception as e:
            self.lbl_hdri_info.setText(f"Error: {e}")
            self.log_error(f"Error loading HDRI preview from {path}", e)

    def clear_hdri(self):
        """Remove loaded HDRI, clear UI preview images, and revert dome light texture."""
        self.txt_hdri.blockSignals(True)
        self.txt_hdri.setText("")
        self.txt_hdri.blockSignals(False)
        self.lbl_hdri_preview.setPixmap(QtGui.QPixmap())
        self.lbl_hdri_preview.setText("No HDRI loaded")
        self.lbl_single_hdri.setPixmap(QtGui.QPixmap())
        self.lbl_single_hdri.setText("No HDRI loaded")
        self.lbl_hdri_info.setText("---")
        self._hdri_thumb_raw = None
        self._hdri_orig_thumb = None
        self._hdri_full_array = None
        self._hdri_orig_full = None
        self._current_calibrated_path = None
        self.reset_calibration(revert_reason="HDRI removed")

        # Reset Stage Dome Light texture
        try:
            dome_node, _ = self._get_stage_lights()
            if dome_node is not None:
                tex_p = dome_node.parm("xn__inputstexturefile_r3ah") or dome_node.parm("inputs:texture:file")
                if tex_p is not None:
                    tex_p.set("")
        except Exception:
            pass

        self._sync_node()
        self._save_state()
        self._update_all_previews()
        self.log("HDRI removed from panel and stage.", "INFO")

    def clear_plate(self):
        # Remove target plate and revert HDRI calibration back to original state.
        self.txt_plate.blockSignals(True)
        self.txt_plate.setText("")
        self.txt_plate.blockSignals(False)
        self.lbl_plate_preview.setPixmap(QtGui.QPixmap())
        self.lbl_plate_preview.setText("No Plate loaded")
        self.lbl_single_plate.setPixmap(QtGui.QPixmap())
        self.lbl_single_plate.setText("No Plate loaded")
        self.lbl_plate_info.setText("---")
        self._plate_thumb_raw = None
        self._plate_orig_thumb = None
        self._plate_full_array = None
        self._plate_orig_full = None
        self._plate_orig_shape = None
        self.reset_calibration(revert_reason="Plate removed")
        self._sync_node()
        self._save_state()
        self._update_all_previews()
        self.log("Target Plate removed.", "INFO")

    def reset_calibration(self, revert_reason="User reset"):
        # Reset calibration parameters (EV, Black, Temp, Tint) and revert HDRI dome light back to original state.
        cal_widgets = [self.sld_ev, self.sld_black, self.sld_temp, self.sld_tint]
        if hasattr(self, 'sld_sat'): cal_widgets.append(self.sld_sat)
        if hasattr(self, 'sld_contrast'): cal_widgets.append(self.sld_contrast)
        for w in cal_widgets:
            w.blockSignals(True)
        self.sld_ev.setRange(-10.0, 10.0)
        self.sld_ev.setValue(0.0)
        self.sld_black.setRange(-0.5, 0.5)
        self.sld_black.setValue(0.0)
        self.sld_temp.setRange(-1.0, 1.0)
        self.sld_temp.setValue(0.0)
        self.sld_tint.setRange(-1.0, 1.0)
        self.sld_tint.setValue(0.0)
        if hasattr(self, 'sld_sat'):
            self.sld_sat.setValue(1.0)
        if hasattr(self, 'sld_contrast'):
            self.sld_contrast.setValue(1.0)
        for w in cal_widgets:
            w.blockSignals(False)

        # Clear bake tracking
        self._current_calibrated_path = None
        self._baked_manual = False
        self._baked_ev = 0.0
        self._baked_black = 0.0
        self._baked_temp = 0.0
        self._baked_tint = 0.0
        self._baked_sat = 1.0
        self._baked_contrast = 1.0

        # Reset Stage Dome Light parameters back to original uncalibrated HDRI
        hdri_path = self.txt_hdri.text().strip()
        dome_node, _ = self._get_stage_lights()
        if dome_node is not None and hdri_path:
            clean_hdri = hdri_path.replace(chr(92), "/")
            tex_parm = dome_node.parm("xn__inputstexturefile_r3ah")
            if tex_parm:
                tex_parm.set(clean_hdri)
            exp_parm = dome_node.parm("xn__inputsexposure_vya")
            if exp_parm:
                exp_parm.set(0.0)
            enable_temp_parm = dome_node.parm("xn__inputsenableColorTemperature_omb")
            if enable_temp_parm:
                enable_temp_parm.set(0)
            temp_parm = dome_node.parm("xn__inputscolorTemperature_wcb")
            if temp_parm:
                temp_parm.set(6500.0)
            cr = dome_node.parm("xn__inputscolor_ztar")
            cg = dome_node.parm("xn__inputscolor_ztag")
            cb = dome_node.parm("xn__inputscolor_ztab")
            if cr and cg and cb:
                cr.set(1.0)
                cg.set(1.0)
                cb.set(1.0)

        # If spatial features (horizon/softclip/sun removal) are active, bake them with neutral calibration
        horizon_en = hasattr(self, "grp_horizon") and self.grp_horizon.isChecked()
        softclip_en = hasattr(self, "grp_softclip") and self.grp_softclip.isChecked()
        sun_en = hasattr(self, "grp_sun") and self.grp_sun.isChecked()
        sun_remove_en = sun_en and hasattr(self, "chk_remove_sun") and self.chk_remove_sun.isChecked()
        if horizon_en or softclip_en or sun_remove_en:
            self._bake_calibrated_hdri(notify_ui=False)
        else:
            self._sync_node()

        self._update_all_previews()
        msg = f"{revert_reason}: HDRI reverted back to original state." if revert_reason else "HDRI reverted back to original state."
        self.log(msg, "SUCCESS")

    def _load_plate_preview(self, path=None):
        if path is None:
            path = self.txt_plate.text()
        path = _resolve_image_path(path)
        if not path or not os.path.isfile(path):
            had_plate = (getattr(self, "_plate_thumb_raw", None) is not None or
                         getattr(self, "_plate_orig_thumb", None) is not None or
                         getattr(self, "_plate_full_array", None) is not None or
                         getattr(self, "_plate_orig_full", None) is not None)
            self.lbl_plate_preview.setText("No Plate loaded")
            self.lbl_single_plate.setText("No Plate loaded")
            self.lbl_plate_info.setText("---")
            self._plate_thumb_raw = None
            self._plate_orig_thumb = None
            self._plate_full_array = None
            self._plate_orig_full = None
            self._plate_orig_shape = None
            self._update_all_previews()
            if had_plate and not getattr(self, "_restoring_state", False):
                self.reset_calibration(revert_reason="Plate removed")
            return

        try:
            self.log(f"Loading Target Plate: {path}...", "INFO")
            arr = None
            try:
                from hdri_match.io.loader import load_exr_to_numpy
                arr = load_exr_to_numpy(path)
            except Exception:
                pass
            if arr is None:
                try:
                    from hdri_match_solaris.oiio_adapter import load_image_with_oiio
                    arr = load_image_with_oiio(path)
                except Exception:
                    pass
            if arr is None:
                self.log_error(f"Failed reading Plate array from {path}")
                return

            h, w = arr.shape[:2]
            self._plate_orig_shape = (w, h)
            self._plate_orig_full = arr[..., :3].astype(np.float32)

            # Generate high-quality anti-aliased thumbnail for dock widgets (480x270 or aspect-correct)
            target_w = 480
            target_h = max(1, int(round(target_w * (h / float(w)))))
            try:
                import cv2
                thumb = cv2.resize(arr[..., :3], (target_w, target_h), interpolation=cv2.INTER_AREA)
                self._plate_orig_thumb = thumb.astype(np.float32)
            except Exception:
                try:
                    from PIL import Image
                    p_img = Image.fromarray(np.clip(arr[..., :3] * 255.0, 0, 255).astype(np.uint8))
                    resample_filter = Image.Resampling.LANCZOS if hasattr(Image, "Resampling") else Image.LANCZOS
                    p_img = p_img.resize((target_w, target_h), resample_filter)
                    self._plate_orig_thumb = (np.array(p_img, dtype=np.float32) / 255.0)
                except Exception:
                    y_idx = np.linspace(0, h - 1, target_h).astype(int)
                    x_idx = np.linspace(0, w - 1, target_w).astype(int)
                    self._plate_orig_thumb = arr[y_idx][:, x_idx, :3].astype(np.float32)

            # Auto-suggest plate color space based on file format if not restoring existing state
            if hasattr(self, 'combo_plate_cs') and not getattr(self, '_restoring_state', False):
                ext = os.path.splitext(path)[1].lower()
                target_cs = "sRGB" if ext in ('.jpg', '.jpeg', '.png', '.bmp', '.tif', '.tiff') else "ACEScg"
                idx = self.combo_plate_cs.findText(target_cs)
                if idx >= 0 and self.combo_plate_cs.currentIndex() != idx:
                    self.combo_plate_cs.blockSignals(True)
                    self.combo_plate_cs.setCurrentIndex(idx)
                    self.combo_plate_cs.blockSignals(False)

            self._apply_input_colorspace(notify_ui=False)
            self.lbl_plate_info.setText(f"{w}x{h} | {os.path.basename(path)}")
            self.log(f"Plate loaded successfully ({w}x{h}, {os.path.basename(path)})", "SUCCESS")

            # If Auto-Calibrate from Plate is checked and an HDRI is loaded, automatically calibrate (not restoring)
            if not getattr(self, '_restoring_state', False):
                if hasattr(self, 'chk_auto_cal') and self.chk_auto_cal.isChecked():
                    hdri_current = self.txt_hdri.text().strip()
                    if hdri_current and os.path.isfile(hdri_current):
                        self.log("Auto-Calibrate from Plate enabled: running calibration...", "INFO")
                        self._run_calibration()
        except Exception as e:
            self.lbl_plate_info.setText(f"Error: {e}")
            self.log_error(f"Error loading Plate preview from {path}", e)

    def _tonemap_array(self, arr, apply_calib=False):
        if arr is None:
            return None
        img = arr.copy()
        if apply_calib:
            # 1. Exposure EV offset
            ev = float(self.sld_ev.value())
            if ev != 0.0:
                img *= (2.0 ** ev)

            # 2. Black offset
            black = float(self.sld_black.value())
            if black != 0.0:
                img += black

            # 3. Temperature & Tint
            temp = float(self.sld_temp.value())
            tint = float(self.sld_tint.value())
            if temp != 0.0 or tint != 0.0:
                r_scale = max(0.01, 1.0 + temp + tint)
                g_scale = max(0.01, 1.0 - tint)
                b_scale = max(0.01, 1.0 - temp)
                lum = (r_scale + g_scale + b_scale) / 3.0
                img[..., 0] *= (r_scale / lum)
                img[..., 1] *= (g_scale / lum)
                img[..., 2] *= (b_scale / lum)

            # 3b. Saturation & Contrast in scene-linear space
            sat = float(self.sld_sat.value()) if hasattr(self, 'sld_sat') else 1.0
            contrast = float(self.sld_contrast.value()) if hasattr(self, 'sld_contrast') else 1.0
            if abs(sat - 1.0) > 1e-4:
                luma = 0.2126 * img[..., 0] + 0.7152 * img[..., 1] + 0.0722 * img[..., 2]
                img = luma[..., np.newaxis] + sat * (img - luma[..., np.newaxis])
            if abs(contrast - 1.0) > 1e-4:
                img = 0.18 * (np.maximum(img, 1e-6) / 0.18) ** contrast

            # 4. Sun Removal (Sky Infill) preview - inpaint at native (sun_u, sun_v) BEFORE yaw rotation
            if (hasattr(self, 'grp_sun') and self.grp_sun.isChecked() and
                hasattr(self, 'chk_remove_sun') and self.chk_remove_sun.isChecked()):
                sun_u = float(self.sld_sun_u.value())
                sun_v = float(self.sld_sun_v.value())
                sun_r = max(0.005, float(self.sld_sun_radius.value()))
                img = self._inpaint_sun_disc(img, sun_u, sun_v, sun_r)

            # 5. Horizon Split preview with color tint
            if hasattr(self, 'grp_horizon') and self.grp_horizon.isChecked():
                h = img.shape[0]
                height = float(self.sld_horizon_height.value())
                feather = max(0.001, float(self.sld_horizon_feather.value()))
                sky_ev = float(self.sld_sky_ev.value())
                ground_ev = float(self.sld_ground_ev.value())

                sky_rgb = np.array(self.col_sky.color(), dtype=np.float32) if hasattr(self, 'col_sky') else np.array([1.0, 1.0, 1.0], dtype=np.float32)
                ground_rgb = np.array(self.col_ground.color(), dtype=np.float32) if hasattr(self, 'col_ground') else np.array([1.0, 1.0, 1.0], dtype=np.float32)

                sky_mult = (2.0 ** sky_ev) * sky_rgb
                ground_mult = (2.0 ** ground_ev) * ground_rgb

                y_norm = 1.0 - np.linspace(0.0, 1.0, h, endpoint=False)[:, None, None]
                sky_weight = np.clip((y_norm - (height - feather / 2.0)) / feather, 0.0, 1.0)
                color_mask = sky_mult[None, None, :] * sky_weight + ground_mult[None, None, :] * (1.0 - sky_weight)
                img *= color_mask

            # 6. Highlight Compression (soft clip) preview - Hue-preserving C1 exponential rolloff
            if hasattr(self, 'grp_softclip') and self.grp_softclip.isChecked():
                thresh_ev = float(self.sld_softclip_thresh.value())
                rolloff_ev = float(self.sld_softclip_rolloff.value())
                t = 0.18 * (2.0 ** thresh_ev)
                r = 0.18 * (2.0 ** rolloff_ev)

                luma = np.sum(img * np.array([0.2126, 0.7152, 0.0722], dtype=np.float32), axis=-1, keepdims=True)
                mask = luma > t
                if np.any(mask):
                    luma_safe = np.maximum(luma, 1e-8)
                    compressed_luma = np.where(mask, t + r * (1.0 - np.exp(-(luma - t) / max(r, 1e-8))), luma)
                    ratio = compressed_luma / luma_safe
                    img = np.where(mask, img * ratio, img)

            # 7. Yaw rotation (wrap horizontally in real-time) - rotates entire environment including sun mask
            yaw = float(self.sld_yaw.value())
            if yaw != 0.0:
                shift_x = int(round((yaw % 360.0) / 360.0 * img.shape[1]))
                if shift_x != 0:
                    img = np.roll(img, shift_x, axis=1)

            # Apply base reference normalization so EV brightens/darkens predictably
            norm = getattr(self, '_hdri_base_norm', None)
            if norm is None:
                base_luma = 0.2126 * img[..., 0] + 0.7152 * img[..., 1] + 0.0722 * img[..., 2]
                if base_luma.size > 200000:
                    step = int(math.ceil(math.sqrt(base_luma.size / 100000.0)))
                    p98 = float(np.percentile(base_luma[::step, ::step], 98))
                else:
                    p98 = float(np.percentile(base_luma, 98))
                norm = (0.9 / max(1e-5, p98))
            img *= norm
        else:
            # Neutral reference tone-mapping
            luma = 0.2126 * img[..., 0] + 0.7152 * img[..., 1] + 0.0722 * img[..., 2]
            if luma.size > 200000:
                step = int(math.ceil(math.sqrt(luma.size / 100000.0)))
                p98 = float(np.percentile(luma[::step, ::step], 98))
            else:
                p98 = float(np.percentile(luma, 98))
            if p98 > 1e-6:
                img *= (0.9 / p98)

        np.nan_to_num(img, copy=False, nan=0.0, posinf=0.0, neginf=0.0)
        np.clip(img, 0.0, 1.0, out=img)
        np.power(img, 1.0 / 2.2, out=img)
        return (img * 255.0).astype(np.uint8)

    def _arr_to_pixmap(self, u8_arr):
        if u8_arr is None:
            return None
        h, w = u8_arr.shape[:2]
        u8_contig = np.ascontiguousarray(u8_arr)
        qimg = QtGui.QImage(u8_contig.data, w, h, w * 3, QtGui.QImage.Format_RGB888)
        return QtGui.QPixmap.fromImage(qimg)

    def _open_large_preview(self, mode=None):
        """Open or raise the large floating preview inspection window."""
        if mode is None:
            curr_text = self.combo_preview_mode.currentText() if hasattr(self, 'combo_preview_mode') else ""
            if "Plate" in curr_text:
                mode = "plate"
            elif "Wipe" in curr_text:
                mode = "wipe"
            else:
                mode = "hdri"

        if not hasattr(self, '_large_preview_dialog') or self._large_preview_dialog is None:
            self._large_preview_dialog = HDRILargePreviewDialog(self, initial_mode=mode)
        else:
            self._large_preview_dialog.set_mode(mode)

        self._large_preview_dialog.update_image()
        self._large_preview_dialog.show()
        self._large_preview_dialog.raise_()
        self._large_preview_dialog.activateWindow()

    def _update_all_previews(self, *args):
        hdri_u8 = self._tonemap_array(self._hdri_thumb_raw, apply_calib=True)
        plate_u8 = self._tonemap_array(self._plate_thumb_raw, apply_calib=False)

        pix_h = self._arr_to_pixmap(hdri_u8)
        pix_p = self._arr_to_pixmap(plate_u8)

        # 1. Update Side-by-Side previews
        if pix_h:
            self.lbl_hdri_preview.setPixmap(pix_h.scaled(
                self.lbl_hdri_preview.size(), QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation
            ))
            self.lbl_single_hdri.setPixmap(pix_h.scaled(
                self.lbl_single_hdri.size(), QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation
            ))
        elif not self.txt_hdri.text():
            self.lbl_hdri_preview.setText("No HDRI loaded")
            self.lbl_single_hdri.setText("No HDRI loaded")

        if pix_p:
            self.lbl_plate_preview.setPixmap(pix_p.scaled(
                self.lbl_plate_preview.size(), QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation
            ))
            self.lbl_single_plate.setPixmap(pix_p.scaled(
                self.lbl_single_plate.size(), QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation
            ))
        elif not self.txt_plate.text():
            self.lbl_plate_preview.setText("No Plate loaded")
            self.lbl_single_plate.setText("No Plate loaded")

        # 2. Update Split Wipe preview
        if pix_h is not None or pix_p is not None:
            ratio = self.sld_wipe.value() / 100.0
            self.lbl_wipe_ratio.setText(f"{int(ratio*100)}% | {int((1.0-ratio)*100)}%")
            tw, th = 340, 130
            canvas = QtGui.QPixmap(tw, th)
            canvas.fill(QtCore.Qt.black)
            painter = QtGui.QPainter(canvas)
            split_x = int(ratio * tw)

            if pix_h is not None:
                h_scaled = pix_h.scaled(tw, th, QtCore.Qt.IgnoreAspectRatio, QtCore.Qt.SmoothTransformation)
                painter.drawPixmap(0, 0, split_x, th, h_scaled, 0, 0, split_x, th)
            if pix_p is not None:
                p_scaled = pix_p.scaled(tw, th, QtCore.Qt.IgnoreAspectRatio, QtCore.Qt.SmoothTransformation)
                painter.drawPixmap(split_x, 0, tw - split_x, th, p_scaled, split_x, 0, tw - split_x, th)

            if 0 < split_x < tw:
                painter.setPen(QtGui.QPen(QtGui.QColor(255, 170, 0), 2))
                painter.drawLine(split_x, 0, split_x, th)

            painter.end()
            self.lbl_wipe_preview.setPixmap(canvas)
        else:
            self.lbl_wipe_preview.setText("Load HDRI and Plate for Split Wipe")

        # 3. Live refresh large preview inspector if open
        if hasattr(self, '_large_preview_dialog') and self._large_preview_dialog and self._large_preview_dialog.isVisible():
            self._large_preview_dialog.update_image()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get_stage_node(self):
        """Return the /stage LOP network, creating it if needed."""
        stage = hou.node("/stage")
        if stage is None:
            stage = hou.node("/obj").createNode("lopnet", "stage")
        return stage

    def _collect_parms(self):
        """Collect all panel parameters into a dictionary."""
        return {
            "hdri_path": self.txt_hdri.text().replace("\\", "/"),
            "plate_path": self.txt_plate.text().replace("\\", "/"),
            "input_cs": self.combo_input_cs.currentText(),
            "plate_cs": self.combo_plate_cs.currentText() if hasattr(self, 'combo_plate_cs') else "sRGB",
            "auto_cal": self.chk_auto_cal.isChecked(),
            "protect_sun": self.chk_protect_sun.isChecked(),
            "sky_mode": self.combo_sky_mode.currentData() or ("top_40" if "top" in self.combo_sky_mode.currentText().lower() else "off"),
            "ev": self.sld_ev.value(),
            "black": self.sld_black.value(),
            "temp": self.sld_temp.value(),
            "tint": self.sld_tint.value(),
            "yaw": self.sld_yaw.value(),
            "horizon_en": self.grp_horizon.isChecked(),
            "horizon_h": self.sld_horizon_height.value(),
            "horizon_f": self.sld_horizon_feather.value(),
            "sky_ev": self.sld_sky_ev.value(),
            "sky_color": list(self.col_sky.color()) if hasattr(self, 'col_sky') else [1.0, 1.0, 1.0],
            "ground_ev": self.sld_ground_ev.value(),
            "ground_color": list(self.col_ground.color()) if hasattr(self, 'col_ground') else [1.0, 1.0, 1.0],
            "softclip_en": self.grp_softclip.isChecked(),
            "softclip_t": self.sld_softclip_thresh.value(),
            "softclip_r": self.sld_softclip_rolloff.value(),
            "sun_en": self.grp_sun.isChecked(),
            "sun_auto": self.chk_auto_detect_sun.isChecked(),
            "sun_remove": self.chk_remove_sun.isChecked() if hasattr(self, 'chk_remove_sun') else False,
            "sun_u": self.sld_sun_u.value(),
            "sun_v": self.sld_sun_v.value(),
            "sun_radius": self.sld_sun_radius.value(),
            "sun_angle": self.sld_sun_angle.value(),
            "sun_intensity": self.sld_sun_intensity.value() if hasattr(self, 'sld_sun_intensity') else 1.0,
            "sun_exposure": self.sld_sun_exposure.value() if hasattr(self, 'sld_sun_exposure') else 0.0,
            "sun_use_cct": self.chk_sun_use_cct.isChecked() if hasattr(self, 'chk_sun_use_cct') else True,
            "sun_cct": self.sld_sun_cct.value() if hasattr(self, 'sld_sun_cct') else 5500.0,
            "sun_reconstruct_en": self.chk_sun_reconstruct.isChecked() if hasattr(self, 'chk_sun_reconstruct') else True,
            "sun_turbidity": self.sld_sun_turbidity.value() if hasattr(self, 'sld_sun_turbidity') else 3.0,
            "sun_reconstruct_blend": self.sld_sun_reconstruct_blend.value() if hasattr(self, 'sld_sun_reconstruct_blend') else 100.0,
            "sun_radiometry_mode": self.cmb_sun_radiometry_mode.currentText() if hasattr(self, 'cmb_sun_radiometry_mode') else "Relative (VFX / Dome Balanced)",
            "aov_en": self.grp_aov.isChecked() if hasattr(self, 'grp_aov') else True,
            "aov_lightgroups": self.chk_aov_lightgroups.isChecked() if hasattr(self, 'chk_aov_lightgroups') else True,
            "lg_dome": self.txt_dome_lg.text().strip() if hasattr(self, 'txt_dome_lg') else "lg_dome",
            "lg_sun": self.txt_sun_lg.text().strip() if hasattr(self, 'txt_sun_lg') else "lg_sun",
            "lg_practicals": self.txt_practicals_lg.text().strip() if hasattr(self, 'txt_practicals_lg') else "lg_practicals",
            "sat": self.sld_sat.value() if hasattr(self, 'sld_sat') else 1.0,
            "contrast": self.sld_contrast.value() if hasattr(self, 'sld_contrast') else 1.0,
            "ground_proj_en": self.grp_ground_proj.isChecked() if hasattr(self, 'grp_ground_proj') else False,
            "proj_mode": "room_box" if (hasattr(self, 'combo_proj_mode') and self.combo_proj_mode.currentIndex() == 1) else "ground_disc",
            "tripod_height": self.sld_tripod_height.value() if hasattr(self, 'sld_tripod_height') else 1.5,
            "ground_radius": self.sld_ground_radius.value() if hasattr(self, 'sld_ground_radius') else 10.0,
            "ground_feather": self.sld_ground_feather.value() if hasattr(self, 'sld_ground_feather') else 0.15,
            "disc_proj_method": "top_view" if (not hasattr(self, 'combo_disc_proj_method') or self.combo_disc_proj_method.currentIndex() == 0) else "spherical",
            "disc_proj_method_idx": self.combo_disc_proj_method.currentIndex() if hasattr(self, 'combo_disc_proj_method') else 0,
            "ground_roughness": 1.0 if (hasattr(self, 'combo_renderer_target') and self.combo_renderer_target.currentIndex() == 0) else (self.sld_ground_roughness.value() if hasattr(self, 'sld_ground_roughness') else 1.0),
            "bake_ground_warp": self.chk_bake_ground_warp.isChecked() if hasattr(self, 'chk_bake_ground_warp') else True,
            "room_width": self.sld_room_width.value() if hasattr(self, 'sld_room_width') else 8.0,
            "room_depth": self.sld_room_depth.value() if hasattr(self, 'sld_room_depth') else 10.0,
            "room_height": self.sld_room_height.value() if hasattr(self, 'sld_room_height') else 3.5,
            "cam_offset_x": self.sld_cam_offset_x.value() if hasattr(self, 'sld_cam_offset_x') else 0.0,
            "cam_offset_z": self.sld_cam_offset_z.value() if hasattr(self, 'sld_cam_offset_z') else 0.0,
            "room_floor": self.chk_room_floor.isChecked() if hasattr(self, 'chk_room_floor') else True,
            "room_ceiling": self.chk_room_ceiling.isChecked() if hasattr(self, 'chk_room_ceiling') else True,
            "room_walls": self.chk_room_walls.isChecked() if hasattr(self, 'chk_room_walls') else True,
            "room_subdivs": int(self.sld_room_subdivs.value()) if hasattr(self, 'sld_room_subdivs') else 16,
            "room_double_sided": self.chk_room_double_sided.isChecked() if hasattr(self, 'chk_room_double_sided') else False,
            "room_shadows": self.chk_room_shadows.isChecked() if hasattr(self, 'chk_room_shadows') else False,
            "room_invisible": self.chk_room_invisible.isChecked() if hasattr(self, 'chk_room_invisible') else False,
            "arch_invisible": self.chk_arch_invisible.isChecked() if hasattr(self, 'chk_arch_invisible') else False,
            "extract_en": self.grp_extract.isChecked() if hasattr(self, 'grp_extract') else False,
            "extract_count": self.sld_extract_count.value() if hasattr(self, 'sld_extract_count') else 3,
            "extract_dist": self.sld_extract_dist.value() if hasattr(self, 'sld_extract_dist') else 20.0,
            "extract_intensity": self.sld_extract_intensity.value() if hasattr(self, 'sld_extract_intensity') else 1.0,
            "extract_exposure": self.sld_extract_exposure.value() if hasattr(self, 'sld_extract_exposure') else 0.0,
            "extract_range": self.sld_extract_range.value() if hasattr(self, 'sld_extract_range') else 8.0,
            "extract_thresh": self.sld_extract_thresh.value() if hasattr(self, 'sld_extract_thresh') else 3.0,
            "extract_inpaint": self.chk_extract_inpaint.isChecked() if hasattr(self, 'chk_extract_inpaint') else True,
            "snap_to_room": self.chk_snap_to_room.isChecked() if hasattr(self, 'chk_snap_to_room') else True,
            "lookdev_en": self.grp_lookdev.isChecked() if hasattr(self, 'grp_lookdev') else True,
            "lookdev_radius": self.sld_lookdev_radius.value() if hasattr(self, 'sld_lookdev_radius') else 0.15,
            "lookdev_spacing": self.sld_lookdev_spacing.value() if hasattr(self, 'sld_lookdev_spacing') else 0.45,
            "lookdev_height": self.sld_lookdev_height.value() if hasattr(self, 'sld_lookdev_height') else 1.0,
            "lookdev_pos_x": self.sld_lookdev_pos_x.value() if hasattr(self, 'sld_lookdev_pos_x') else 0.0,
            "lookdev_pos_z": self.sld_lookdev_pos_z.value() if hasattr(self, 'sld_lookdev_pos_z') else 0.0,
            "lookdev_stand": self.chk_lookdev_stand.isChecked() if hasattr(self, 'chk_lookdev_stand') else True,
            "lookdev_white": self.chk_lookdev_white.isChecked() if hasattr(self, 'chk_lookdev_white') else False,
            "arch_mat_mode": self.combo_arch_mat_mode.currentIndex() if hasattr(self, 'combo_arch_mat_mode') else 0,
            "arch_emissive_mult": self.sld_arch_emissive_mult.value() if hasattr(self, 'sld_arch_emissive_mult') else 1.0,
            "arch_tex_mode": self.combo_arch_tex_mode.currentIndex() if hasattr(self, 'combo_arch_tex_mode') else 0,
            "arch_planar_res": self.combo_arch_planar_res.currentText() if hasattr(self, 'combo_arch_planar_res') else "2K",
            "arch_renderer_target": ["all", "arnold", "karma", "redshift", "preview"][self.combo_arch_renderer_target.currentIndex()] if hasattr(self, 'combo_arch_renderer_target') else "all",
            "arch_renderer_target_idx": self.combo_arch_renderer_target.currentIndex() if hasattr(self, 'combo_arch_renderer_target') else 0,
            "mat_mode": "emissive" if (hasattr(self, 'combo_ground_mat_mode') and self.combo_ground_mat_mode.currentIndex() == 1) else ("pbr_emissive" if (hasattr(self, 'combo_ground_mat_mode') and self.combo_ground_mat_mode.currentIndex() == 2) else "pbr"),
            "emissive_mult": self.sld_ground_emissive_mult.value() if hasattr(self, 'sld_ground_emissive_mult') else 1.0,
            "ground_mat_mode": "emissive" if (hasattr(self, 'combo_ground_mat_mode') and self.combo_ground_mat_mode.currentIndex() == 1) else ("pbr_emissive" if (hasattr(self, 'combo_ground_mat_mode') and self.combo_ground_mat_mode.currentIndex() == 2) else "pbr"),
            "ground_mat_mode_idx": self.combo_ground_mat_mode.currentIndex() if hasattr(self, 'combo_ground_mat_mode') else 0,
            "ground_emissive_mult": self.sld_ground_emissive_mult.value() if hasattr(self, 'sld_ground_emissive_mult') else 1.0,
            "ground_tex_mode": "planar" if (hasattr(self, 'combo_ground_tex_mode') and self.combo_ground_tex_mode.currentIndex() == 0) else "equirect",
            "ground_tex_mode_idx": self.combo_ground_tex_mode.currentIndex() if hasattr(self, 'combo_ground_tex_mode') else 0,
            "ground_planar_res": self.combo_ground_planar_res.currentText() if hasattr(self, 'combo_ground_planar_res') else "4K",
            "ground_planar_res_idx": self.combo_ground_planar_res.currentIndex() if hasattr(self, 'combo_ground_planar_res') else 1,
            "use_planar": (hasattr(self, 'combo_ground_tex_mode') and self.combo_ground_tex_mode.currentIndex() == 0),
            "planar_textures": getattr(self, '_ground_planar_textures', {}),
            "renderer_target": ["all", "arnold", "karma", "redshift", "preview"][self.combo_renderer_target.currentIndex()] if hasattr(self, 'combo_renderer_target') else "all",
            "renderer_target_idx": self.combo_renderer_target.currentIndex() if hasattr(self, 'combo_renderer_target') else 0,
            "custom_props": self._get_custom_props_data() if hasattr(self, '_get_custom_props_data') else [],
            "collapsed_sections": {sec.title(): sec.is_collapsed() for sec in getattr(self, '_collapsible_sections', [])},
            "main_tab_idx": self.tabs_main.currentIndex() if hasattr(self, 'tabs_main') else 0,
        }

    # ------------------------------------------------------------------
    # Network creation
    # ------------------------------------------------------------------

    def _create_full_network(self):
        """Create the complete native HDRI Match Solaris LOP light network based on active checked boxes."""
        self.log("Action: Create Full LOP Network initiated...", "INFO")
        p = self._collect_parms()
        calibrated_path = getattr(self, '_current_calibrated_path', None)
        if calibrated_path and os.path.isfile(calibrated_path):
            p["hdri_texture"] = calibrated_path.replace(chr(92), "/")
        else:
            p["hdri_texture"] = p.get("hdri_path", "").replace(chr(92), "/")
        if not p["hdri_path"]:
            self.log_error("Please select an HDRI file first.")
            if hou.isUIAvailable():
                hou.ui.displayMessage("Please select an HDRI file first.", severity=hou.severityType.Warning)
            return

        try:
            stage_node = self._get_stage_node()
            self.log(f"Target stage context: {stage_node.path()}", "DETAIL")

            for n in stage_node.children():
                n.setSelected(False)

            created_features = ["Dome Light"]

            # 1. Native Solaris Dome Light (always created)
            dome_node = stage_node.node("hdri_dome")
            if dome_node is None:
                dome_node = stage_node.createNode("domelight", "hdri_dome")
                self.log("Created native Solaris /stage/hdri_dome (domelight)", "DETAIL")
            else:
                self.log("Reusing existing /stage/hdri_dome (domelight)", "DETAIL")
            dome_node.bypass(False)
            if p.get("renderer_target") == "arnold" or p.get("arch_renderer_target") == "arnold":
                _ensure_dome_light_latlong(dome_node)

            # 2. Native Solaris Sun Light (Distant Light) - based on Sun Relighting checkbox
            sun_en = bool(p.get("sun_en", False))
            sun_node = stage_node.node("hdri_sun")
            if sun_en:
                if sun_node is None:
                    sun_node = stage_node.createNode("distantlight", "hdri_sun")
                    self.log("Created native Solaris /stage/hdri_sun (distantlight)", "DETAIL")
                else:
                    self.log("Reusing existing /stage/hdri_sun (distantlight)", "DETAIL")
                sun_node.bypass(False)
                if hasattr(self, 'chk_auto_detect_sun') and self.chk_auto_detect_sun.isChecked():
                    self._detect_sun(notify_ui=False)
                created_features.append("Sun Light")
            else:
                if sun_node is not None:
                    sun_node.bypass(True)

            # 3. Practical Lights (Multi-Light Extraction) - based on Multi-Light Extraction checkbox
            extract_en = bool(p.get("extract_en", False))
            if extract_en:
                self._create_native_practical_lights(p)
                practical_nodes = [c for c in stage_node.children() if c.name().startswith("practical_")]
                for pn in practical_nodes:
                    pn.bypass(False)
                if practical_nodes:
                    created_features.append(f"Practical Lights ({len(practical_nodes)})")
            else:
                practical_nodes = [c for c in stage_node.children() if c.name().startswith("practical_")]
                for pn in practical_nodes:
                    pn.bypass(True)

            # 3b. Bake Calibrated HDRI Map to $HIP (with practical lights inpainted to eliminate double illumination)
            calibrated_path = self._bake_calibrated_hdri(notify_ui=False, force_bake=True)
            if calibrated_path and os.path.isfile(calibrated_path):
                p["hdri_texture"] = calibrated_path.replace(chr(92), "/")
                self._current_calibrated_path = calibrated_path
                created_features.append("Calibrated Inpainted HDRI")
                if dome_node:
                    tex_parm = dome_node.parm("xn__inputstexturefile_r3ah")
                    if tex_parm:
                        tex_parm.set(calibrated_path)

            # 4. Ground / Room Box Projection Mesh & Material Library
            ground_proj_en = bool(p.get("ground_proj_en", False))
            proj_node = stage_node.node("hdri_match_projection")
            matlib_node = stage_node.node("hdri_match_materials")
            if ground_proj_en:
                self._create_or_update_ground_projection(notify_ui=False)
                proj_mode = p.get("proj_mode", "ground_disc")
                mode_lbl = "Room Box" if proj_mode == "room_box" else "Ground Disc"
                created_features.append(f"Projection Mesh ({mode_lbl})")
                if stage_node.node("hdri_match_materials") is not None and not stage_node.node("hdri_match_materials").isBypassed():
                    created_features.append("Material Library")
            else:
                if proj_node is not None:
                    proj_node.bypass(True)
                if matlib_node is not None:
                    matlib_node.bypass(True)

            # 5. Lookdev Calibration Rig - based on Lookdev checkbox
            lookdev_en = bool(p.get("lookdev_en", False))
            lookdev_node = stage_node.node("hdri_match_lookdev")
            if lookdev_en:
                from hdri_match_solaris import lop_lookdev
                lookdev_node = lop_lookdev.create_lookdev_rig_node(
                    stage_node,
                    "hdri_match_lookdev",
                    pos=(float(p.get("lookdev_pos_x", 0.0)), 0.0, float(p.get("lookdev_pos_z", 0.0))),
                    radius=float(p.get("lookdev_radius", 0.15)),
                    stand_height=float(p.get("lookdev_height", 1.0)),
                    spacing=float(p.get("lookdev_spacing", 0.45)),
                    include_white=bool(p.get("lookdev_white", False)),
                    include_stand=bool(p.get("lookdev_stand", True)),
                    include_macbeth=False,
                    lookdev_en=True,
                )
                if lookdev_node is not None:
                    lookdev_node.bypass(False)
                    lookdev_node.cook(force=True)
                created_features.append("Lookdev Spheres")
            else:
                if lookdev_node is not None:
                    lookdev_node.bypass(True)

            # 6. AOV Light Groups - based on AOV Light Groups checkbox
            aov_en = bool(p.get("aov_lightgroups", False))
            self._apply_light_groups_to_stage(notify_ui=False, enabled=aov_en)
            if aov_en:
                created_features.append("AOV Light Groups")

            # Instantly evaluate and push all parameters to native stage lights
            self._sync_node()

            # Merge all active nodes into a single continuous stream
            self._merge_light_networks(notify_ui=False)

            summary_str = ", ".join(created_features)
            self.log(f"Native Solaris network created with active features: {summary_str}", "SUCCESS")

            if hou.isUIAvailable():
                pane = hou.ui.paneTabOfType(hou.paneTabType.NetworkEditor)
                if pane:
                    pane.setPwd(stage_node)

                hou.ui.displayMessage(
                    f"Created active Solaris LOP network:\n• " + "\n• ".join(created_features),
                    title="DomeBreaker")
            else:
                print(f"[HDRI Match Solaris] Native light network created: {summary_str}")

        except Exception as e:
            self.log_error("Error creating native light network", e)
            if hou.isUIAvailable():
                hou.ui.displayMessage(f"Error creating network:\n{e}", severity=hou.severityType.Error)

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _merge_light_networks(self, notify_ui=True):
        """Merge HDRI Match (dome + sun), Crucible Light Studio, and lookdev spheres
        into a single, clean, continuous flow in /stage.
        Pipeline flow: hdri_dome ➔ hdri_sun ➔ [extracted/practicals] ➔ hdri_match_projection ➔ hdri_match_lookdev ➔ crucible_light_studio ➔ [downstream/camera]
        """
        self.log("Action: Merging HDRI Match and Crucible networks into single flow...", "INFO")
        try:
            stage_node = self._get_stage_node()
            if not stage_node:
                return

            dome_node = stage_node.node("hdri_dome")
            sun_node = stage_node.node("hdri_sun")
            proj_node = stage_node.node("hdri_match_projection")
            matlib_node = stage_node.node("hdri_match_materials")
            crucible_node = _find_crucible_studio_node(stage_node)
            lookdev_node = stage_node.node("hdri_match_lookdev")
            aov_node = stage_node.node("hdri_match_aovs")
            if aov_node is not None:
                try: aov_node.destroy()
                except Exception: pass
                aov_node = None

            # Find any other extracted light nodes
            extracted_nodes = []
            for child in stage_node.children():
                if child not in (dome_node, sun_node, proj_node, matlib_node, crucible_node, lookdev_node):
                    if child.name().startswith("emitter_") or child.name().startswith("light_extract") or child.name().startswith("practical_"):
                        extracted_nodes.append(child)

            current_tail = None

            # 1. Dome Light
            if dome_node and not dome_node.isBypassed():
                current_tail = dome_node
            elif dome_node:
                current_tail = dome_node

            # 2. Sun Light
            if sun_node and not sun_node.isBypassed():
                if current_tail and sun_node != current_tail:
                    sun_node.setInput(0, current_tail)
                current_tail = sun_node

            # 3. Extracted Lights (if any)
            for ext_node in extracted_nodes:
                if not ext_node.isBypassed():
                    if current_tail and ext_node != current_tail:
                        ext_node.setInput(0, current_tail)
                    current_tail = ext_node

            # 4. Ground / Room Box Projection Mesh
            if proj_node and not proj_node.isBypassed():
                if current_tail and proj_node != current_tail:
                    proj_node.setInput(0, current_tail)
                current_tail = proj_node

            # 4b. Material Library (interactive VOP shaders for room meshes)
            if matlib_node and not matlib_node.isBypassed():
                if current_tail and matlib_node != current_tail:
                    matlib_node.setInput(0, current_tail)
                current_tail = matlib_node

            # 5. Lookdev Spheres
            if lookdev_node and not lookdev_node.isBypassed():
                if current_tail and lookdev_node != current_tail:
                    lookdev_node.setInput(0, current_tail)
                current_tail = lookdev_node

            # 6. Crucible Light Studio (HDA containing all Crucible studio lights - placed at bottom of stream)
            if crucible_node and not crucible_node.isBypassed():
                if current_tail and crucible_node != current_tail:
                    crucible_node.setInput(0, current_tail)
                current_tail = crucible_node

            # Re-wire any downstream stage nodes (e.g. camera, usd_rop, render settings) to connect to current_tail
            if current_tail:
                chain_nodes = {n for n in (dome_node, sun_node, proj_node, matlib_node, lookdev_node, crucible_node) if n is not None}.union(set(extracted_nodes))
                for child in stage_node.children():
                    if child not in chain_nodes and child != current_tail:
                        # If child was receiving input from any upstream node in our chain, rewire it to current_tail
                        if child.inputs() and child.inputs()[0] in chain_nodes and child.inputs()[0] != current_tail:
                            try:
                                child.setInput(0, current_tail)
                            except Exception:
                                pass

                try:
                    current_tail.setDisplayFlag(True)
                    current_tail.setRenderFlag(True)
                except Exception:
                    pass

            try:
                stage_node.layoutChildren()
            except Exception:
                pass

            active_summary_nodes = [n for n in [dome_node, sun_node, proj_node, matlib_node, lookdev_node, crucible_node] if n is not None and not n.isBypassed()]
            flow_summary = " ➔ ".join([n.name() for n in active_summary_nodes])
            self.log(f"Single continuous stage flow merged successfully: {flow_summary}", "SUCCESS")

            if notify_ui and hou.isUIAvailable():
                hou.ui.displayMessage(
                    f"Networks merged into single flow:\n{flow_summary}",
                    title="Merged Single Flow"
                )
        except Exception as e:
            self.log_error("Failed to merge networks into single flow", e)

    def _run_calibration(self):
        """Run calibration and update native stage lights & UI preview."""
        if getattr(self, '_restoring_state', False):
            return
        self.log("Action: Run Calibration initiated...", "INFO")
        p = self._collect_parms()
        if not p["hdri_path"]:
            self.log_error("Please select an HDRI file first.")
            if hou.isUIAvailable():
                hou.ui.displayMessage("Please select an HDRI file first.", severity=hou.severityType.Warning)
            return

        try:
            hdri_path = p["hdri_path"]
            plate_path = p.get("plate_path", "")

            # If auto-calibrate is checked and plate is provided, compute exposure/color match from plate
            if p.get("auto_cal", False) and plate_path and os.path.isfile(plate_path):
                self.log("Computing chromaticity and exposure calibration from plate...", "INFO")
                import numpy as np
                from hdri_match.core.pipeline import CalibrationPipeline
                pipeline = CalibrationPipeline()
                pipeline.state.apply_exposure_match = True
                pipeline.state.sky_mode = p.get("sky_mode", "top_40")
                hdri_cs = p.get("input_cs", "Linear")
                plate_cs = p.get("plate_cs", "sRGB")
                pipeline.load_inputs(hdri_path, plate_path,
                                    input_space=hdri_cs, working_space="Linear")

                # If plate color space differs from HDRI input_cs, transform plate_array with plate_cs
                if plate_cs != hdri_cs:
                    try:
                        from hdri_match.io.loader import load_exr_to_numpy
                        raw_p = load_exr_to_numpy(plate_path)
                        if raw_p is not None:
                            ext = os.path.splitext(plate_path)[1].lower()
                            if ext in ('.jpg', '.jpeg', '.png', '.bmp', '.tga', '.tif', '.tiff'):
                                raw_p = np.power(np.clip(raw_p, 0.0, 1.0), 2.2)
                            pipeline.state.plate_array = pipeline.colorspace_manager.transform_image(
                                raw_p, plate_cs, "Linear"
                            )
                            pipeline.state.plate_proxy = pipeline._create_proxy(pipeline.state.plate_array)
                    except Exception as e:
                        self.log(f"Plate colorspace transform note: {e}", "DETAIL")

                pipeline.compute_calibration(protect_sun=p.get("protect_sun", True))

                cal_ev = float(pipeline.state.ev_offset)
                cal_black = float(pipeline.state.black_offset)

                # Derive Temperature and Tint from illuminants
                cal_temp = 0.0
                cal_tint = 0.0
                hdri_ill = pipeline.state.hdri_illuminant
                plate_ill = pipeline.state.plate_illuminant
                if hdri_ill is not None and plate_ill is not None:
                    scale = plate_ill / np.clip(hdri_ill, 1e-6, None)
                    scale_norm = scale / scale[1]  # G=1
                    R_target = float(scale_norm[0])
                    B_target = float(scale_norm[2])
                    S = R_target + B_target
                    cal_tint = float((S - 2.0) / (S + 1.0))
                    cal_temp = float(1.0 - B_target * (1.0 - cal_tint))
                    cal_temp = max(-1.0, min(1.0, cal_temp))
                    cal_tint = max(-1.0, min(1.0, cal_tint))

                # Update sliders (which triggers _sync_node and _update_all_previews automatically)
                self.sld_ev.setValue(cal_ev)
                self.sld_black.setValue(cal_black)
                self.sld_temp.setValue(cal_temp)
                self.sld_tint.setValue(cal_tint)
                self.log(f"Auto-calibrated from plate: EV={cal_ev:+.2f}, Black={cal_black:+.4f}, Temp={cal_temp:+.3f}, Tint={cal_tint:+.3f}", "SUCCESS")

            # Check if advanced image processing (horizon split, softclip, ground proj, inpaint) requires baking a calibrated EXR
            has_spatial = (
                p.get("horizon_en", False) or
                p.get("softclip_en", False) or
                (p.get("sun_en", False) and p.get("sun_remove", False)) or
                (p.get("ground_proj_en", False) and p.get("bake_ground_warp", True)) or
                (p.get("extract_en", False) and p.get("extract_inpaint", True))
            )
            if has_spatial or getattr(self, "_baked_manual", False):
                self._bake_calibrated_hdri(notify_ui=False)
            else:
                # Ensure dome light uses current HDRI texture
                dome_node, _ = self._get_stage_lights()
                if dome_node:
                    tex_parm = dome_node.parm("xn__inputstexturefile_r3ah")
                    if tex_parm and tex_parm.eval() != hdri_path.replace(chr(92), "/"):
                        tex_parm.set(hdri_path.replace(chr(92), "/"))

            # Sync native light parameters and refresh previews
            self._sync_node()
            self._update_all_previews()

            self.log("Calibration complete! Native Solaris DomeLight & Sun Light updated.", "SUCCESS")
            if hou.isUIAvailable():
                hou.ui.displayMessage("Calibration complete! Native Solaris lights updated in /stage.",
                                      title="DomeBreaker")
            else:
                print("[HDRI Match Solaris] Calibration complete.")
        except Exception as e:
            self.log_error(f"Calibration error: {e}", e)
            if hou.isUIAvailable():
                hou.ui.displayMessage(f"Calibration error: {e}", severity=hou.severityType.Error)

    def _add_lookdev(self):
        """Add lookdev spheres to the network on explicit user button click."""
        self.log("Action: Add Lookdev Spheres initiated...", "INFO")
        try:
            stage_node = self._get_stage_node()
            if hasattr(self, 'grp_lookdev') and self.grp_lookdev:
                self.grp_lookdev.setChecked(True)

            p = self._collect_parms()
            from hdri_match_solaris import lop_lookdev
            lookdev_node = lop_lookdev.create_lookdev_rig_node(
                stage_node,
                "hdri_match_lookdev",
                pos=(float(p.get("lookdev_pos_x", 0.0)), 0.0, float(p.get("lookdev_pos_z", 0.0))),
                radius=float(p.get("lookdev_radius", 0.15)),
                stand_height=float(p.get("lookdev_height", 1.0)),
                spacing=float(p.get("lookdev_spacing", 0.45)),
                include_white=bool(p.get("lookdev_white", False)),
                include_stand=bool(p.get("lookdev_stand", True)),
                include_macbeth=False,
                lookdev_en=True,
            )
            self._merge_light_networks(notify_ui=False)
            if lookdev_node is not None:
                lookdev_node.bypass(False)
                lookdev_node.cook(force=True)
                lookdev_node.setDisplayFlag(True)
            stage_node.layoutChildren()
            self.log("Lookdev spheres created and display flag set successfully.", "SUCCESS")
        except Exception as e:
            self.log_error("Lookdev spheres error", e)
            if hou.isUIAvailable():
                hou.ui.displayMessage(f"Lookdev error:\n{e}", severity=hou.severityType.Error)

    @staticmethod
    def _compute_recommended_hotspot_parameters(arr, sun_mask=None):
        """Intelligently analyze HDRI luminance histogram and spatial clustering to determine
        optimal Exposure Range (EV) and Min Threshold (EV) for practical light detection."""
        import numpy as np

        luma = 0.2126 * arr[..., 0] + 0.7152 * arr[..., 1] + 0.0722 * arr[..., 2]
        H, W = luma.shape[:2]

        if sun_mask is not None:
            active_luma = luma[~sun_mask]
        else:
            active_luma = luma

        if active_luma.size == 0 or np.all(active_luma <= 0):
            return 4.0, 2.5

        max_luma = float(np.max(active_luma))
        valid = active_luma[active_luma > 0]
        median_luma = float(np.median(valid)) if valid.size > 0 else 0.18
        floor_ref = max(0.01, median_luma)
        # Available dynamic range between median ambient floor and maximum highlight
        delta_ev = float(np.log2(max(1e-4, max_luma) / floor_ref)) if max_luma > floor_ref else 0.0

        # Dynamic range adaptive threshold:
        # For true HDR maps (delta_ev >= 3.5), 2.5 EV above ambient cleanly rejects diffuse noise.
        # For lower dynamic range maps (e.g. SDR splat bakes with delta_ev < 3.0), scale threshold proportionally
        # so that bright fixtures and windows are accurately detected rather than cut off.
        if delta_ev >= 3.5:
            rec_thresh_ev = 2.5
        elif delta_ev > 1.0:
            rec_thresh_ev = max(0.5, delta_ev * 0.45)
        else:
            rec_thresh_ev = max(0.2, delta_ev * 0.3)

        base_thresh = floor_ref * (2.0 ** rec_thresh_ev)
        # Ensure base_thresh is strictly lower than max_luma so candidates can be detected
        if base_thresh >= max_luma * 0.95:
            base_thresh = floor_ref + (max_luma - floor_ref) * 0.35
            rec_thresh_ev = max(0.1, float(np.log2(max(1e-4, base_thresh) / floor_ref)))

        # Multi-scale peak detection downsampled
        step_y = max(1, H // 128)
        step_x = max(1, W // 256)
        ds = luma[::step_y, ::step_x].copy()
        if sun_mask is not None:
            ds_sun = sun_mask[::step_y, ::step_x]
            ds[ds_sun] = 0.0
        dh, dw = ds.shape

        cands = []
        temp = ds.copy()
        for _ in range(48):
            pk = float(np.max(temp))
            if pk < base_thresh:
                break
            candidates_idx = int(np.argmax(temp))
            cy, cx = divmod(candidates_idx, dw)
            u = (cx + 0.5) / dw
            v = (cy + 0.5) / dh
            az = (0.5 - u) * 360.0
            el = 90.0 - v * 180.0
            cands.append((pk, az, el))
            r = 3
            temp[max(0, cy-r):min(dh, cy+r+1), max(0, cx-r):min(dw, cx+r+1)] = 0.0

        # Group candidates into distinct 3D spatial direction clusters
        clusters = []
        for pk, az, el in cands:
            found = False
            for cl in clusters:
                d_az = abs(az - cl['az'])
                d_az = min(d_az, 360.0 - d_az)
                d_el = abs(el - cl['el'])
                if d_az < 30.0 and d_el < 25.0:
                    cl['peaks'].append(pk)
                    found = True
                    break
            if not found:
                clusters.append({'peaks': [pk], 'az': az, 'el': el, 'max_p': pk})

        clusters.sort(key=lambda c: c['max_p'], reverse=True)

        if len(clusters) == 0:
            rec_range_ev = 4.0
        elif len(clusters) == 1:
            rec_range_ev = 2.5
        else:
            # Cliff detection: distinguish real fixtures/apertures from secondary reflections
            # Real fixtures / skylights / outdoor portals have significant luminance above ambient floor (>= base_thresh * 1.5)
            # and are located in the upper hemisphere or at the horizon (el >= -15 deg).
            primary_clusters = [clusters[0]]
            for idx in range(1, len(clusters)):
                cl = clusters[idx]
                drop_from_prev = float(np.log2(clusters[idx - 1]['max_p'] / max(1e-4, cl['max_p'])))
                # If candidate is a floor reflection (el < -15 deg) or near ambient floor, treat as cliff
                if cl['el'] < -15.0 or cl['max_p'] < base_thresh * 1.5:
                    break
                # If drop from prev is steep (> 3.5 EV) AND the current cluster is already dim (< 5% of max or < median * 4)
                if drop_from_prev > 3.5 and (cl['max_p'] < max_luma * 0.05 and cl['max_p'] < floor_ref * 4.0):
                    break
                primary_clusters.append(cl)

            if len(primary_clusters) <= 1:
                rec_range_ev = 2.5
            else:
                lowest_primary = primary_clusters[-1]['max_p']
                fixture_span = float(np.log2(max_luma / max(1e-4, lowest_primary)))
                rec_range_ev = round(float(np.clip(fixture_span + 1.2, 2.5, 8.0)), 1)

        return rec_range_ev, rec_thresh_ev


    @staticmethod
    def _hdri_local_background(luma, pool=16, passes=3):
        """Large-radius LOCAL ambient estimate for an equirectangular luma map.

        Wraps horizontally (U is cyclic) and clamps vertically (V terminates at the poles)
        so no seam artefact is introduced at azimuth 0/360.
        Pure numpy - cv2 and scipy are NOT available in Houdini's Python environment.
        """
        import numpy as np

        H, W = luma.shape
        ph = max(1, H // pool)
        pw = max(1, W // pool)
        Hc, Wc = ph * pool, pw * pool
        small = luma[:Hc, :Wc].reshape(ph, pool, pw, pool).mean(axis=(1, 3)).astype(np.float32)
        for _ in range(passes):
            h_up = np.pad(small, ((0, 0), (1, 1)), mode="wrap")
            small = (h_up[:, :-2] + h_up[:, 1:-1] + h_up[:, 2:]) / 3.0
            v_up = np.pad(small, ((1, 1), (0, 0)), mode="edge")
            small = (v_up[:-2] + v_up[1:-1] + v_up[2:]) / 3.0
        bg = np.repeat(np.repeat(small, pool, axis=0), pool, axis=1)
        if bg.shape != luma.shape:
            out = np.empty_like(luma)
            out[:bg.shape[0], :bg.shape[1]] = bg
            out[bg.shape[0]:, :] = bg[-1:, :]
            out[:, bg.shape[1]:] = out[:, bg.shape[1] - 1:bg.shape[1]]
            bg = out
        return bg

    @staticmethod
    def _detect_practical_hotspots(arr, thresh_ev=2.5, range_ev=8.0, sun_mask=None, max_candidates=64,
                                   min_contrast_ev=2.5, merge_deg=20.0, supp_scale=2.0,
                                   dominance_ev=None, work_w=512):
        """Detect distinct practical light emitters in an equirectangular HDRI.

        Uses connected-component luminance segmentation and non-maximum suppression (NMS)
        so that large apertures (e.g. hangar entrance portals), ceiling skylights, and fluorescent
        fixtures are cleanly segmented with accurate rectangular footprints (hw, hh) and zero keystone warping.

        Returns (primary_hotspots, secondary_hotspots, primary_thresh, base_thresh).
        Pure numpy: cv2 and scipy are NOT available inside Houdini's Python 3 environment.
        """
        import numpy as np

        H, W = arr.shape[:2]
        luma = (0.2126 * arr[..., 0] + 0.7152 * arr[..., 1] + 0.0722 * arr[..., 2]).astype(np.float32)
        if sun_mask is not None:
            luma = luma.copy()
            luma[sun_mask] = 0.0

        valid = luma[luma > 0]
        median_luma = float(np.median(valid)) if valid.size > 0 else 0.18
        max_luma = float(np.max(luma)) if luma.size > 0 else 1.0
        p95_luma = float(np.percentile(valid, 95.0)) if valid.size > 0 else median_luma * 4.0

        # Ambient floor cutoff: stops above median interior luma
        base_thresh = max(0.01, median_luma) * (2.0 ** float(thresh_ev))
        if base_thresh >= max_luma * 0.95:
            base_thresh = max(0.01, median_luma + (max_luma - median_luma) * 0.35)

        # Adaptive emitter threshold
        th = max(base_thresh, min(p95_luma, max_luma * 0.1))
        th = max(th, median_luma * (2.0 ** 2.0))

        step = max(1, int(round(W / float(work_w))))
        l_s = luma[::step, ::step]
        if sun_mask is not None:
            sm_s = sun_mask[::step, ::step]
            mask_s = (l_s >= th) & (~sm_s)
        else:
            mask_s = (l_s >= th)

        dh, dw = l_s.shape
        lab_s = np.zeros_like(mask_s, dtype=int)
        cur_lbl = 0
        comps = []

        for y in range(dh):
            for x in range(dw):
                if mask_s[y, x] and lab_s[y, x] == 0:
                    cur_lbl += 1
                    q = [(y, x)]
                    lab_s[y, x] = cur_lbl
                    pts = []
                    while q:
                        cy, cx = q.pop(0)
                        pts.append((cy, cx))
                        for ny, nx in ((cy-1, cx), (cy+1, cx), (cy, (cx-1)%dw), (cy, (cx+1)%dw)):
                            if 0 <= ny < dh and mask_s[ny, nx] and lab_s[ny, nx] == 0:
                                lab_s[ny, nx] = cur_lbl
                                q.append((ny, nx))
                    if len(pts) >= 4:
                        ys = [p[0] for p in pts]
                        xs = np.array([p[1] for p in pts])
                        if np.max(xs) - np.min(xs) > dw * 0.7:
                            xs_shifted = (xs + dw // 2) % dw
                            bw = (np.max(xs_shifted) - np.min(xs_shifted) + 1) * step
                            cx_p = int(((int(np.mean(xs_shifted)) - dw // 2) % dw) * step)
                        else:
                            bw = (np.max(xs) - np.min(xs) + 1) * step
                            cx_p = int(np.mean(xs)) * step
                        bh = (max(ys) - min(ys) + 1) * step
                        cy_p = int(np.mean(ys)) * step
                        pk = max(l_s[p[0], p[1]] for p in pts)
                        u = (cx_p + 0.5) / W
                        v = (cy_p + 0.5) / H
                        az = (0.5 - u) * 360.0
                        el = 90.0 - v * 180.0
                        comps.append({
                            'val': float(pk),
                            'px': cx_p,
                            'py': cy_p,
                            'az': az,
                            'el': el,
                            'hw': max(4, bw // 2 + 4),
                            'hh': max(4, bh // 2 + 4),
                            'pts': len(pts)
                        })

        comps.sort(key=lambda c: c['val'], reverse=True)

        ref_peak = comps[0]['val'] if comps else 0.0
        effective_window_ev = float(range_ev) if dominance_ev is None else min(float(range_ev), float(dominance_ev))
        primary_thresh = max(base_thresh, ref_peak * (2.0 ** (-effective_window_ev))) if ref_peak > 0.0 else base_thresh

        # Non-maximum suppression: merge smaller components whose centers fall inside a larger component
        merged_prim = []
        for c in comps:
            is_floor = c['el'] < -15.0
            is_primary = (c['val'] >= primary_thresh) and (not is_floor or c['val'] >= max_luma * 0.3)
            if not is_primary:
                continue
            inside = False
            for m in merged_prim:
                dx = abs(c['px'] - m['px'])
                dx = min(dx, W - dx)
                dy = abs(c['py'] - m['py'])
                if dx <= m['hw'] and dy <= m['hh']:
                    inside = True
                    break
            if not inside:
                merged_prim.append(c)

        primary_hotspots = []
        secondary_hotspots = []
        for i, c in enumerate(merged_prim[:int(max_candidates)]):
            item = {
                "index": i + 1,
                "val": c["val"],
                "px": c["px"],
                "py": c["py"],
                "az": c["az"],
                "el": c["el"],
                "hw": c["hw"],
                "hh": c["hh"],
                "is_primary": True,
            }
            primary_hotspots.append(item)

        # Retain remaining non-overlapping components as secondary reflections
        needed_px = {p['px'] for p in primary_hotspots}
        for c in comps:
            if c['px'] not in needed_px:
                item = {
                    "index": len(primary_hotspots) + len(secondary_hotspots) + 1,
                    "val": c["val"],
                    "px": c["px"],
                    "py": c["py"],
                    "az": c["az"],
                    "el": c["el"],
                    "hw": c["hw"],
                    "hh": c["hh"],
                    "is_primary": False,
                }
                secondary_hotspots.append(item)

        return primary_hotspots, secondary_hotspots, primary_thresh, base_thresh

    def _analyze_hdri_hotspots(self, notify_ui=False):
        """Analyze the HDRI to detect and count potential bright practical hotspot emitters."""
        try:
            arr = getattr(self, '_hdri_orig_full', None)
            if arr is None:
                path = self.txt_hdri.text().strip()
                if path and os.path.isfile(path):
                    from hdri_match.io.loader import load_exr_to_numpy
                    arr = load_exr_to_numpy(path)
                    if arr is not None:
                        self._hdri_orig_full = arr[..., :3].astype(np.float32)
            if arr is None:
                if notify_ui:
                    self.log("Please load a valid HDRI file first to analyze hotspots.", "WARNING")
                return 0

            import numpy as np
            H, W = arr.shape[:2]

            # 1. Mask primary sun if Sun Relighting is active so we only count practical lights
            sun_mask = None
            sun_en = self.grp_sun.isChecked() if hasattr(self, 'grp_sun') else False
            if sun_en:
                s_u = float(self.sld_sun_u.value())
                s_v = float(self.sld_sun_v.value())
                s_rad = float(self.sld_sun_radius.value())
                s_x = int(round(s_u * W))
                s_y = int(round(s_v * H))
                s_r = int(max(8, round(s_rad * W * 1.5)))
                y_g, x_g = np.ogrid[:H, :W]
                dx = np.abs(x_g - s_x)
                dx = np.minimum(dx, W - dx)
                dy = np.abs(y_g - s_y)
                sun_mask = (dx**2 + dy**2) < (s_r**2)

            if notify_ui:
                # User explicitly clicked 'Analyze Hotspots': Auto-tune Exposure Range & Min Threshold to scene DR
                rec_range, rec_thresh = self._compute_recommended_hotspot_parameters(arr, sun_mask=sun_mask)
                if hasattr(self, 'sld_extract_range') and hasattr(self, 'sld_extract_thresh'):
                    self.sld_extract_range.blockSignals(True)
                    self.sld_extract_thresh.blockSignals(True)
                    self.sld_extract_range.setValue(rec_range)
                    self.sld_extract_thresh.setValue(rec_thresh)
                    self.sld_extract_range.blockSignals(False)
                    self.sld_extract_thresh.blockSignals(False)
                    self.log(f"Auto-calibrated hotspot parameters: Exposure Range = {rec_range:.1f} EV, Min Threshold = {rec_thresh:.1f} EV", "INFO")

            range_ev = float(self.sld_extract_range.value()) if hasattr(self, 'sld_extract_range') else 8.0
            thresh_ev = float(self.sld_extract_thresh.value()) if hasattr(self, 'sld_extract_thresh') else 3.0
            prim, sec, p_th, b_th = self._detect_practical_hotspots(arr, thresh_ev=thresh_ev, range_ev=range_ev, sun_mask=sun_mask)

            all_hotspots = prim + sec
            count = len(prim) if len(prim) > 0 else len(sec)
            self._detected_hotspot_count = count
            self._detected_hotspots = all_hotspots

            # Update UI labels and button
            if hasattr(self, 'lbl_hotspot_info'):
                if len(prim) > 0:
                    peak_str = f"{prim[0]['val']:.1f}"
                    limit_notice = " (64 max)" if count > 64 else ""
                    sec_info = f" <span style='color:#888;'>| {len(sec)} dim reflection{'s' if len(sec) != 1 else ''} noted</span>" if len(sec) > 0 else ""
                    self.lbl_hotspot_info.setText(
                        f"<span style='color:#66ccff; font-weight:bold;'>Detected {len(prim)} primary light fixture{'s' if len(prim) != 1 else ''}{limit_notice}</span> "
                        f"(Peak: {peak_str}, Cutoff: {p_th:.1f}, Range: {range_ev:.1f} EV){sec_info}"
                    )
                    top_prim = [f"  [Primary #{h['index']:02d}] Peak {h['val']:.1f} | Az: {h['az']:+.0f}° | El: {h['el']:+.0f}° | Size: {h['hw']*2}x{h['hh']*2}px" for h in prim[:12]]
                    lines = [f"Detected {len(prim)} primary practical light fixture(s) (Cutoff: {p_th:.1f} Luma, Range: {range_ev:.1f} EV):"] + top_prim
                    if len(sec) > 0:
                        top_sec = [f"  [Secondary #{h['index']:02d}] Peak {h['val']:.1f} | Az: {h['az']:+.0f}° | El: {h['el']:+.0f}° (Below {range_ev:.1f} EV cutoff - reflections/fill)" for h in sec[:6]]
                        lines += ["", f"Secondary Reflections / Fill ({len(sec)} detected):"] + top_sec
                    self.lbl_hotspot_info.setToolTip(chr(10).join(lines))
                elif len(sec) > 0:
                    peak_str = f"{sec[0]['val']:.1f}"
                    self.lbl_hotspot_info.setText(
                        f"<span style='color:#e67e22;'>Detected {len(sec)} dim emitter{'s' if len(sec) != 1 else ''}</span> (Peak: {peak_str}, Cutoff: {b_th:.1f}). Increase Exposure Range (EV) to include."
                    )
                    top_sec = [f"  #{h['index']:02d}: Peak {h['val']:.1f} | Az: {h['az']:+.0f}° | El: {h['el']:+.0f}°" for h in sec[:6]]
                    self.lbl_hotspot_info.setToolTip(chr(10).join([f"Dim hotspots (Cutoff: {b_th:.1f}):"] + top_sec))
                else:
                    self.lbl_hotspot_info.setText(
                        f"<span style='color:#e67e22;'>No hotspots detected</span> above {thresh_ev:.1f} EV (Cutoff: {b_th:.2f}). Increase Exposure Range or lower Min Threshold."
                    )
                    self.lbl_hotspot_info.setToolTip("Try increasing Exposure Range (EV) or lowering Min Threshold (EV) to detect dimmer fixtures.")

            if hasattr(self, 'btn_apply_detected_count'):
                self.btn_apply_detected_count.setEnabled(count > 0)
                btn_lbl = f"Set Count ({min(64, count)})" if count > 0 else "Set Count"
                self.btn_apply_detected_count.setText(btn_lbl)

            if notify_ui:
                if len(prim) > 0:
                    self.log(f"Analyzed HDRI Hotspots: Found {len(prim)} primary light fixture(s) (Peak: {prim[0]['val']:.1f}) and {len(sec)} secondary reflections.", "SUCCESS")
                elif len(sec) > 0:
                    self.log(f"Analyzed HDRI Hotspots: Found {len(sec)} dim candidate emitters (Peak: {sec[0]['val']:.1f}).", "INFO")
                else:
                    self.log(f"Analyzed HDRI Hotspots: Found 0 emitters above cutoff {b_th:.2f}. Try lowering Threshold (EV).", "WARNING")

            return count
        except Exception as e:
            if hasattr(self, 'lbl_hotspot_info'):
                self.lbl_hotspot_info.setText(f"Analysis error: {e}")
            self.log_error("Error analyzing HDRI hotspots", e)
            return 0

    def _apply_detected_hotspot_count(self):
        """Set Emitter Count slider to the total number of detected hotspots."""
        count = getattr(self, '_detected_hotspot_count', 0)
        if count > 0 and hasattr(self, 'sld_extract_count'):
            self.sld_extract_count.setValue(float(min(64, count)))
            self.log(f"Emitter Count set to {count} (detected hotspots).", "INFO")

    def _extract_lights(self):
        """Extract practical lights from the HDRI into native Solaris RectLight nodes with baked HDR textures."""
        self.log("Action: Extract Practical RectLights initiated...", "INFO")
        if hasattr(self, 'grp_extract'):
            self.grp_extract.setChecked(True)
        p = self._collect_parms()
        if not p["hdri_path"]:
            self.log_error("Please load an HDRI first.")
            if hou.isUIAvailable():
                hou.ui.displayMessage("Please load an HDRI first.", severity=hou.severityType.Warning)
            return

        try:
            count = self._create_native_practical_lights(p)
            # If Inpaint Extracted Lights from Dome Map is enabled, bake calibrated HDRI to update dome texture
            if p.get("extract_inpaint", True):
                self.log("Inpainting extracted light emitters from Dome Light texture map...", "INFO")
                self._bake_calibrated_hdri(notify_ui=False)

            self.log(f"Created {count} native RectLight nodes in /stage with baked HDR hotspot textures!", "SUCCESS")
            if hou.isUIAvailable():
                hou.ui.displayMessage(
                    f"Successfully created {count} native RectLight nodes in /stage!\n\n"
                    "• Node Type: Native Solaris RectLight ('light' LOP)\n"
                    "• Texture: Baked floating-point HDR image patch from HDRI\n"
                    "• Orientation: Aimed directly at center (0, 0, 0)\n"
                    "• Position: Placed along true 3D spherical rays from HDRI\n"
                    "• You can interactively select, scale, and adjust them in the viewport.",
                    title="DomeBreaker"
                )
            else:
                print(f"[DomeBreaker] Created {count} native RectLight nodes in /stage.")
        except Exception as e:
            self.log_error("Native practical light extraction failed", e)
            if hou.isUIAvailable():
                hou.ui.displayMessage(f"Light extraction error:\n{e}", severity=hou.severityType.Error)

    def _create_native_practical_lights(self, p):
        """Detect hotspots in HDRI, bake cropped HDR textures, and create native Solaris RectLight nodes."""
        import math
        import numpy as np
        from hdri_match.io.loader import load_exr_to_numpy
        from hdri_match_solaris.oiio_adapter import save_image_with_oiio

        hdri_path = p.get("hdri_path", "").strip()
        arr = getattr(self, '_hdri_full_array', None)
        if arr is None:
            arr = load_exr_to_numpy(hdri_path)
            if arr is not None:
                self._hdri_full_array = arr[..., :3].astype(np.float32)
                arr = self._hdri_full_array
        if arr is None:
            raise ValueError(f"Could not load HDRI from {hdri_path}")

        H, W = arr.shape[:2]

        # 1. Mask primary sun if Sun Relighting is active so we only extract practical lights
        sun_mask = None
        if p.get("sun_en", False):
            s_u = float(p.get("sun_u", 0.5))
            s_v = float(p.get("sun_v", 0.25))
            s_rad = float(p.get("sun_radius", 0.05))
            s_x = int(round(s_u * W))
            s_y = int(round(s_v * H))
            s_r = int(max(8, round(s_rad * W * 1.5)))
            y_g, x_g = np.ogrid[:H, :W]
            dx = np.abs(x_g - s_x)
            dx = np.minimum(dx, W - dx)
            dy = np.abs(y_g - s_y)
            sun_mask = (dx**2 + dy**2) < (s_r**2)

        range_ev = float(p.get("extract_range", 8.0))
        thresh_ev = float(p.get("extract_thresh", 3.0))
        prim, sec, p_th, b_th = self._detect_practical_hotspots(arr, thresh_ev=thresh_ev, range_ev=range_ev, sun_mask=sun_mask)
        candidates = prim + sec
        if not candidates:
            self.log("No hotspots found to extract as practical lights.", "WARNING")
            return 0

        # Output textures directory
        hip_dir = hou.expandString("$HIP") or "."
        if not hip_dir or hip_dir == "." or "houdini_temp" in hip_dir:
            hip_dir = os.path.expanduser("~")
        tex_dir = os.path.join(hip_dir, "hdri_match", "textures").replace(chr(92), "/")
        os.makedirs(tex_dir, exist_ok=True)
        base_name = _clean_hdri_basename(hdri_path)

        extract_count = int(p.get("extract_count", 3))
        selected_emitters = candidates[:min(extract_count, len(candidates))]
        extracted_data = []

        for i, item in enumerate(selected_emitters):
            px, py = item["px"], item["py"]
            hw, hh = item["hw"], item["hh"]

            # Roll horizontally so px is at center
            shift = W // 2 - px
            rolled_arr = np.roll(arr, shift, axis=1)
            cx = W // 2

            x0 = max(0, cx - hw)
            x1 = min(W, cx + hw + 1)
            y0 = max(0, py - hh)
            y1 = min(H, py + hh + 1)
            crop = rolled_arr[y0:y1, x0:x1, :3].copy()

            # Feather crop borders smoothly (flat in center, smooth sine falloff at outer rim)
            ch, cw = crop.shape[:2]
            if ch > 2 and cw > 2:
                fy = np.sin(np.linspace(0, np.pi, ch)) ** 0.3
                fx = np.sin(np.linspace(0, np.pi, cw)) ** 0.3
                feather = (fy[:, None] * fx[None, :])[:, :, None]
                crop = crop * feather

            # Ensure proper resolution: if extracted crop is smaller than 256x256,
            # upscale smoothly with high-precision bilinear interpolation so the rect light's
            # projected texture is crisp and artifact-free in reflections.
            min_res = 256
            if max(cw, ch) < min_res and max(cw, ch) > 0:
                scale = float(min_res) / float(max(cw, ch))
                target_w = max(16, int(round(cw * scale)))
                target_h = max(16, int(round(ch * scale)))
                crop_to_save = HdriMatchSolarisPanel._resize_image_linear(crop, target_w, target_h)
            else:
                crop_to_save = crop

            tex_file = os.path.join(tex_dir, f"{base_name}_emitter_{i+1:02d}.exr").replace(chr(92), "/")
            save_image_with_oiio(crop_to_save, tex_file)
            self.log(f"Baked HDR emitter texture: {tex_file} ({crop_to_save.shape[1]}x{crop_to_save.shape[0]})", "DETAIL")

            # Physical coordinates and dimensions in meters
            dist = float(p.get("extract_dist", 10.0))
            u = (float(px) + 0.5) / float(W)
            v = (float(py) + 0.5) / float(H)

            # USD DomeLight latlong mapping (matches projection.py GroundProjector):
            # v=0 is Zenith (+Y), v=0.5 is Horizon (Y=0), v=1 is Nadir (-Y)
            # u=0.5 is -Z (Center/Front), u=0.75 is +X (Right), u=0.25 is -X (Left), u=0.0/1.0 is +Z (Back)
            el_rad = (0.5 - v) * math.pi
            phi_rad = u * 2.0 * math.pi
            yaw = float(p.get("yaw", 0.0))

            cos_el = math.cos(el_rad)
            sin_el = math.sin(el_rad)

            # Equirectangular cosine metric tensor correction for physical dimensions
            theta_x = (float(cw) / float(W)) * 2.0 * math.pi * cos_el
            theta_y = (float(ch) / float(H)) * math.pi

            proj_mode = p.get("proj_mode", "ground_disc")
            snap_to_room = bool(p.get("snap_to_room", True))

            if proj_mode == "room_box" and snap_to_room:
                room_w = float(p.get("room_width", 8.0))
                room_d = float(p.get("room_depth", 10.0))
                room_h = float(p.get("room_height", 3.5))
                c_x = float(p.get("cam_offset_x", 0.0))
                c_y = float(p.get("tripod_height", 1.5))
                c_z = float(p.get("cam_offset_z", 0.0))
                pos, eff_dist, width, height, normal, rot = _compute_room_light_placement(
                    phi_rad, el_rad, theta_x, theta_y,
                    room_w, room_d, room_h, c_x, c_y, c_z,
                    yaw=yaw
                )
                rx, ry, rz = rot
                is_snapped = True
            else:
                eff_dist = dist
                width = max(0.2, 2.0 * eff_dist * math.tan(theta_x / 2.0))
                height = max(0.2, 2.0 * eff_dist * math.tan(theta_y / 2.0))
                az_eff = phi_rad + math.radians(yaw)
                dx_w = -cos_el * math.sin(az_eff)
                dy_w = sin_el
                dz_w = cos_el * math.cos(az_eff)
                pos = (dx_w * eff_dist, dy_w * eff_dist, dz_w * eff_dist)
                # Level horizontal orientation with zero roll (W_y = 0.0) aimed towards stage center
                rx = -math.degrees(el_rad)
                ry = (math.degrees(az_eff) + 180.0) % 360.0 - 180.0
                rz = 0.0
                is_snapped = False
            extracted_data.append({
                "name": f"practical_{i+1:02d}",
                "tex": tex_file,
                "pos": pos,
                "rx": rx,
                "ry": ry,
                "rz": rz,
                "width": float(width),
                "height": float(height),
                "phi_native": phi_rad,
                "el_native": el_rad,
                "theta_x": theta_x,
                "theta_y": theta_y,
                "dist": dist,
                "px": px,
                "py": py,
                "hw": hw,
                "hh": hh,
                "u": u,
                "v": v,
                "hw_norm": float(hw) / float(W),
                "hh_norm": float(hh) / float(H),
                "is_snapped": is_snapped,
            })

        stage_node = self._get_stage_node()

        # Remove obsolete PythonScript LOP if it exists
        old_lop = stage_node.node("hdri_match_lights")
        if old_lop is not None:
            old_lop.destroy()

        # Clean up obsolete practical light nodes if count reduced
        needed_names = {lt["name"] for lt in extracted_data}
        for child in list(stage_node.children()):
            if child.name().startswith("practical_") and child.name() not in needed_names:
                child.destroy()

        # Create / Update native light nodes
        created_nodes = []
        for lt in extracted_data:
            node_name = lt["name"]
            l_node = stage_node.node(node_name)
            if l_node is None:
                l_node = stage_node.createNode("light", node_name)
                self.log(f"Created native Solaris RectLight: /stage/{node_name}", "DETAIL")

            l_node.parm("lighttype").set("UsdLuxRectLight")
            l_node.parm("primpath").set(f"/lights/practicals/{node_name}")
            l_node.parm("tx").set(lt["pos"][0])
            l_node.parm("ty").set(lt["pos"][1])
            l_node.parm("tz").set(lt["pos"][2])

            if lt.get("is_snapped", False):
                # Snapped to room: flush with wall/ceiling, lookat disabled
                l_node.parm("lookatenable").set(0)
                l_node.parm("rx").set(lt["rx"])
                l_node.parm("ry").set(lt["ry"])
                l_node.parm("rz").set(lt["rz"])
            else:
                # Free-floating: Native LookAt aiming pointing -Z axis toward scene center
                l_node.parm("lookatenable").set(1)
                l_node.parm("lookatpositionx").set(0.0)
                l_node.parm("lookatpositiony").set(0.75)
                l_node.parm("lookatpositionz").set(0.0)
                l_node.parm("rx").set(0.0)
                l_node.parm("ry").set(0.0)
                l_node.parm("rz").set(0.0)

            # Dimensions
            l_node.parm("xn__inputswidth_control_06a").set("set")
            l_node.parm("xn__inputswidth_zta").set(lt["width"])
            l_node.parm("xn__inputsheight_control_n8a").set("set")
            l_node.parm("xn__inputsheight_mva").set(lt["height"])

            # Baked HDR Texture
            l_node.parm("xn__inputstexturefile_control_shbh").set("set")
            l_node.parm("xn__inputstexturefile_r3ah").set(lt["tex"])

            # Physical Intensity & Exposure
            ext_intensity = float(p.get("extract_intensity", 1.0))
            ext_exposure = float(p.get("extract_exposure", 0.0)) + float(p.get("ev", 0.0))
            l_node.parm("xn__inputsintensity_control_jeb").set("set")
            l_node.parm("xn__inputsintensity_i0a").set(ext_intensity)
            l_node.parm("xn__inputsexposure_control_wcb").set("set")
            l_node.parm("xn__inputsexposure_vya").set(ext_exposure)

            # Pure white multiplier to let texture color project accurately
            l_node.parm("xn__inputscolor_control_06a").set("set")
            l_node.parm("xn__inputscolor_ztar").set(1.0)
            l_node.parm("xn__inputscolor_ztag").set(1.0)
            l_node.parm("xn__inputscolor_ztab").set(1.0)

            # Store native spherical coordinates and angular extents in node userData for live distance/yaw sync
            l_node.setUserData("phi_native", str(lt["phi_native"]))
            l_node.setUserData("el_native", str(lt["el_native"]))
            l_node.setUserData("theta_x", str(lt["theta_x"]))
            l_node.setUserData("theta_y", str(lt["theta_y"]))
            l_node.setUserData("dist", str(lt["dist"]))

            created_nodes.append(l_node)

        self._extracted_practicals_data = extracted_data

        # Wire the practical lights in a continuous linear chain after hdri_sun
        sun_node = stage_node.node("hdri_sun")
        dome_node = stage_node.node("hdri_dome")
        head = sun_node or dome_node
        prev = head
        for p_node in created_nodes:
            if prev and p_node != prev:
                p_node.setInput(0, prev)
            prev = p_node

        # Reconnect downstream nodes (ground_projection, lookdev, crucible) to the tail of practicals
        tail = prev
        proj_node = stage_node.node("hdri_match_projection")
        lookdev_node = stage_node.node("hdri_match_lookdev")
        crucible_node = _find_crucible_studio_node(stage_node)

        if proj_node and proj_node != tail:
            proj_node.setInput(0, tail)
            tail = proj_node
        if lookdev_node and lookdev_node != tail:
            lookdev_node.setInput(0, tail)
            tail = lookdev_node
        if crucible_node and crucible_node != tail:
            crucible_node.setInput(0, tail)
            tail = crucible_node

        # Re-wire any downstream stage nodes (e.g. camera, usd_rop, render settings) to the new tail
        if tail:
            chain_nodes = {n for n in (sun_node, dome_node, proj_node, lookdev_node, crucible_node) if n is not None}.union(set(created_nodes))
            for child in stage_node.children():
                if child not in chain_nodes and child != tail:
                    if child.inputs() and child.inputs()[0] in chain_nodes and child.inputs()[0] != tail:
                        try:
                            child.setInput(0, tail)
                        except Exception:
                            pass

        stage_node.layoutChildren()
        if tail:
            tail.setDisplayFlag(True)

        return len(created_nodes)

    def _create_or_update_ground_projection(self, *args, notify_ui=True, **kwargs):
        """Create or update the USD ground plane / dome projection geometry node in Solaris."""
        if args and isinstance(args[0], bool):
            notify_ui = True
        self.log("Action: Create Ground Projection USD Geometry initiated...", "INFO")

        # Automatically ensure group box is enabled so subsequent syncs do not bypass it
        if hasattr(self, 'grp_ground_proj') and not self.grp_ground_proj.isChecked():
            self.grp_ground_proj.setChecked(True)

        p = self._collect_parms()
        p["ground_proj_en"] = True

        # Check for HDRI path fallback from existing dome light if text field was empty
        if not p.get("hdri_path"):
            stage_node = self._get_stage_node()
            if stage_node:
                dome_node = stage_node.node("hdri_dome")
                if dome_node:
                    for p_name in ["inputs:texture:file", "xn__inputstexturefile_r3ah", "texture"]:
                        p_parm = dome_node.parm(p_name)
                        if p_parm and p_parm.evalAsString():
                            p["hdri_path"] = p_parm.evalAsString().replace("\\", "/")
                            if hasattr(self, 'txt_hdri'):
                                self.txt_hdri.setText(p["hdri_path"])
                            break

        calibrated_path = getattr(self, '_current_calibrated_path', None)
        if calibrated_path and os.path.isfile(calibrated_path):
            p["hdri_texture"] = calibrated_path.replace(chr(92), "/")
        else:
            p["hdri_texture"] = p.get("hdri_path", "").replace(chr(92), "/")

        # If room_box mode and planar texture mode is active, ensure planar textures exist
        if p.get("proj_mode") == "room_box" and p.get("use_planar"):
            pres = 4096
            res_txt = p.get("ground_planar_res", "4K")
            if "8K" in res_txt or "8192" in res_txt:
                pres = 8192
            elif "4K" in res_txt or "4096" in res_txt:
                pres = 4096
            elif "1K" in res_txt or "1024" in res_txt:
                pres = 1024
            else:
                pres = 2048

            hip_dir = hou.expandString("$HIP") if 'hou' in sys.modules and hasattr(hou, 'expandString') else "."
            if not hip_dir or hip_dir == "." or "houdini_temp" in hip_dir:
                hip_dir = os.path.expanduser("~")

            hdri_tex_path = p.get("hdri_texture", "")
            hdri_base = os.path.splitext(os.path.basename(hdri_tex_path))[0] if hdri_tex_path else "room"
            planar_dir = os.path.join(hip_dir, "hdri_match", f"planar_textures_{hdri_base}_{pres}").replace("\\", "/")
            os.makedirs(planar_dir, exist_ok=True)

            needed_surfaces = ["floor", "ceiling", "wall_north", "wall_south", "wall_east", "wall_west"]
            existing_planar = {}
            for sname in needed_surfaces:
                for sfx in ["_diffuse.exr", "_albedo.exr"]:
                    fpath = os.path.join(planar_dir, f"{sname}{sfx}").replace("\\", "/")
                    if os.path.isfile(fpath):
                        existing_planar[sname] = fpath
                        break

            hdri_source, was_inpainted = self._get_inpainted_hdri_source(hdri_tex_path, planar_dir=planar_dir)
            if was_inpainted and getattr(self, '_current_calibrated_path', None):
                hdri_tex_path = self._current_calibrated_path
                p["hdri_texture"] = hdri_tex_path

            # If all 6 surfaces exist on disk and no inpainting was newly applied, reuse them. Otherwise re-bake!
            re_bake = (len(existing_planar) < 6) or was_inpainted
            if not re_bake:
                self._ground_planar_textures = existing_planar
                p["planar_textures"] = existing_planar
                self.log(f"Reusing existing 6 baked planar surface textures ({pres}x{pres}). No re-bake needed.", "INFO")
            elif hdri_tex_path and os.path.isfile(hdri_tex_path):
                try:
                    inpaint_note = " (with practical lights painted out)" if was_inpainted else ""
                    self.log(f"Auto-baking high-detail planar surface textures ({pres}x{pres}) for room box{inpaint_note}...", "INFO")
                    if hasattr(self, 'pbar_ground_planar'):
                        self.pbar_ground_planar.setVisible(True)
                        self.pbar_ground_planar.setValue(20)
                        self.pbar_ground_planar.setFormat(f"Baking Planar Maps ({pres}x{pres})...")

                    room_w = float(p.get("room_width", 8.0))
                    room_d = float(p.get("room_depth", 10.0))
                    room_h = float(p.get("room_height", 3.5))
                    tripod_h = float(p.get("tripod_height", 1.5))
                    cam_ox = float(p.get("cam_offset_x", 0.0))
                    cam_oz = float(p.get("cam_offset_z", 0.0))

                    room_data = {
                        "center": [0.0, room_h * 0.5, 0.0],
                        "floor_y": 0.0,
                        "ceil_y": room_h,
                        "height": room_h,
                        "x_min": -room_w * 0.5,
                        "x_max": room_w * 0.5,
                        "z_min": -room_d * 0.5,
                        "z_max": room_d * 0.5,
                        "width": room_w,
                        "depth": room_d,
                    }
                    probe_pos = (cam_ox, tripod_h, cam_oz)

                    def _on_prog(pct, msg):
                        if hasattr(self, 'pbar_ground_planar'):
                            self.pbar_ground_planar.setValue(pct)
                            self.pbar_ground_planar.setFormat(msg)

                    from hdri_match_solaris.gaussian_splat import GaussianSplatBaker
                    bake_source = hdri_source if was_inpainted else hdri_tex_path
                    self._ground_planar_textures = GaussianSplatBaker.bake_planar_room_textures(
                        hdri_source=bake_source,
                        room_data=room_data,
                        output_dir=planar_dir,
                        probe_pos=probe_pos,
                        resolution_floor=pres,
                        resolution_walls=pres,
                        progress_callback=_on_prog,
                    )
                    p["planar_textures"] = self._ground_planar_textures
                    if hasattr(self, 'pbar_ground_planar'):
                        self.pbar_ground_planar.setValue(100)
                        self.pbar_ground_planar.setFormat(f"Planar Ready ({pres}x{pres})")
                    self.log(f"Auto-baked 6 planar textures ({pres}x{pres}) successfully.", "SUCCESS")
                except Exception as ex_pln:
                    self.log_error(f"Planar auto-bake warning: {ex_pln}", ex_pln)
            else:
                p["planar_textures"] = getattr(self, '_ground_planar_textures', {})

        # If ground_disc mode and top_view projection method is active, ensure ground_diffuse.exr exists
        if p.get("proj_mode", "ground_disc") == "ground_disc" and p.get("disc_proj_method", "top_view") == "top_view":
            pres = 4096
            res_txt = p.get("ground_planar_res", "4K")
            if "8K" in res_txt or "8192" in res_txt:
                pres = 8192
            elif "4K" in res_txt or "4096" in res_txt:
                pres = 4096
            elif "1K" in res_txt or "1024" in res_txt:
                pres = 1024
            else:
                pres = 2048

            hip_dir = hou.expandString("$HIP") if 'hou' in sys.modules and hasattr(hou, 'expandString') else "."
            if not hip_dir or hip_dir == "." or "houdini_temp" in hip_dir:
                hip_dir = os.path.expanduser("~")

            hdri_tex_path = p.get("hdri_texture", "")
            hdri_base = os.path.splitext(os.path.basename(hdri_tex_path))[0] if hdri_tex_path else "ground"
            planar_dir = os.path.join(hip_dir, "hdri_match", f"planar_textures_{hdri_base}_{pres}").replace("\\", "/")
            os.makedirs(planar_dir, exist_ok=True)
            ground_diff_exr = os.path.join(planar_dir, "ground_diffuse.exr").replace("\\", "/")

            hdri_source, was_inpainted = self._get_inpainted_hdri_source(hdri_tex_path, planar_dir=planar_dir)
            if was_inpainted and getattr(self, '_current_calibrated_path', None):
                hdri_tex_path = self._current_calibrated_path
                p["hdri_texture"] = hdri_tex_path

            re_bake_ground = not (os.path.isfile(ground_diff_exr) and os.path.getsize(ground_diff_exr) > 0)
            if not re_bake_ground:
                self.log(f"Reusing existing baked planar ground texture ({pres}x{pres}). No re-bake needed.", "INFO")
            elif hdri_tex_path and os.path.isfile(hdri_tex_path):
                try:
                    inpaint_note = " (with practical lights painted out)" if was_inpainted else ""
                    self.log(f"Baking Top-View Planar Ground Texture ({pres}x{pres}){inpaint_note}...", "INFO")
                    from hdri_match_solaris.gaussian_splat import GaussianSplatBaker
                    GaussianSplatBaker.bake_planar_ground_texture(
                        hdri_source=hdri_source,
                        output_path=ground_diff_exr,
                        ground_radius=float(p.get("ground_radius", 10.0)),
                        tripod_height=float(p.get("tripod_height", 1.5)),
                        yaw=0.0,
                        resolution=pres,
                    )
                    self.log(f"Baked Top-View Planar Ground Texture successfully: {os.path.basename(ground_diff_exr)}", "SUCCESS")
                except Exception as ex_gb:
                    self.log_error(f"Ground planar bake warning: {ex_gb}", ex_gb)

            if os.path.isfile(ground_diff_exr):
                if not isinstance(p.get("planar_textures"), dict):
                    p["planar_textures"] = {}
                p["planar_textures"]["ground"] = ground_diff_exr
                p["ground_planar_texture"] = ground_diff_exr
                self._ground_planar_textures = p["planar_textures"]

        try:
            stage_node = self._get_stage_node()
            proj_node = stage_node.node("hdri_match_projection")
            if proj_node is None:
                proj_node = stage_node.createNode("pythonscript", "hdri_match_projection")
                self.log("Created /stage/hdri_match_projection node", "DETAIL")

            proj_node.bypass(False)
            _ensure_projection_node_parms(proj_node)
            _update_proj_node_values(proj_node, p)
            proj_node.parm("python").set(_gen_ground_projection_code(p))

            # Create or update Material Library LOP node with interactive VOP shaders assigned to room meshes
            matlib_func = _get_create_or_update_material_library()
            if matlib_func:
                matlib_func(stage_node, p)
                self.log("Created / updated /stage/hdri_match_materials with interactive VOP shaders.", "DETAIL")
            else:
                self.log("Warning: create_or_update_material_library could not be loaded from renderer_materials.", "WARNING")

            # Merge into continuous stream so proj_node and matlib_node have input and upstream nodes feed through them
            self._merge_light_networks(notify_ui=False)

            self.log("Cooking ground projection geometry and materials on USD stage...", "DETAIL")
            proj_node.cook(force=True)
            matlib_node = stage_node.node("hdri_match_materials")
            if matlib_node:
                matlib_node.cook(force=True)
            if hou.isUIAvailable():
                hou.ui.triggerUpdate()
            tex_name = os.path.basename(p['hdri_texture']) if p.get('hdri_texture') else 'HDRI'
            mode_name = "Interior Room Box" if p.get("proj_mode") == "room_box" else "Ground Projection Disc"
            self.log(f"{mode_name} mesh created/updated at /environment/ground_dome using {tex_name}!", "SUCCESS")
            if notify_ui:
                if hou.isUIAvailable():
                    hou.ui.displayMessage(f"{mode_name} mesh created successfully at /environment/ground_dome using {tex_name}!",
                                          title="DomeBreaker")
                else:
                    print(f"[HDRI Match] {mode_name} mesh created using {tex_name}.")
        except Exception as e:
            self.log_error("Ground projection geometry creation failed", e)
            if notify_ui and hou.isUIAvailable():
                hou.ui.displayMessage(f"Ground projection error:\n{e}", severity=hou.severityType.Error)

    def _jump_to_material_library(self):
        """Focus on and select the hdri_match_materials Material Library node in Houdini's Network Editor."""
        try:
            import hou
            stage_node = self._get_stage_node()
            if not stage_node:
                self.log("Cannot locate /stage network in current Houdini scene.", "WARNING")
                return

            matlib = stage_node.node("hdri_match_materials")
            if matlib is None:
                self.log("Material Library not yet generated. Building projection mesh and materials...", "INFO")
                self._create_or_update_ground_projection(notify_ui=False)
                matlib = stage_node.node("hdri_match_materials")

            if matlib:
                matlib.setSelected(True, clear_all_selected=True)
                for pane in hou.ui.currentPaneTabs():
                    if pane.type() == hou.paneTabType.NetworkEditor:
                        pane.setCurrentNode(matlib)
                        pane.homeToSelection()
                        break
                self.log("Focused on /stage/hdri_match_materials in Network Editor", "SUCCESS")
                if hou.isUIAvailable():
                    hou.ui.setStatusMessage("Selected /stage/hdri_match_materials — tweak shaders in Parameter Pane or double-click to dive inside.")
        except Exception as e:
            self.log_error("Failed to focus Material Library", e)

    def _on_sun_radiometry_mode_changed(self, idx=0):
        """Update slider ranges and re-evaluate sun radiometry when switching between Relative and Absolute lux."""
        is_absolute = (idx == 1)
        if hasattr(self, 'sld_sun_intensity'):
            if is_absolute:
                self.sld_sun_intensity.setRange(0.0, 250000.0)
                self.sld_sun_intensity.setSingleStep(100.0)
            else:
                self.sld_sun_intensity.setRange(0.0, 200.0)
                self.sld_sun_intensity.setSingleStep(0.1)
        self._on_sun_reconstruct_changed()

    def _on_sun_reconstruct_toggled(self, checked):
        """Slot called when sun clipping reconstruction is toggled."""
        self._on_sun_reconstruct_changed()

    def _on_sun_reconstruct_changed(self):
        """Update sun intensity and angular diameter based on reconstruction settings."""
        clip_info = getattr(self, '_last_sun_clip_info', None)
        turbidity = float(self.sld_sun_turbidity.value()) if hasattr(self, 'sld_sun_turbidity') else 3.0
        reconstruct_en = self.chk_sun_reconstruct.isChecked() if hasattr(self, 'chk_sun_reconstruct') else False
        blend = (float(self.sld_sun_reconstruct_blend.value()) / 100.0) if hasattr(self, 'sld_sun_reconstruct_blend') else 1.0
        mode = "absolute" if (hasattr(self, 'cmb_sun_radiometry_mode') and self.cmb_sun_radiometry_mode.currentIndex() == 1) else "relative"

        if clip_info:
            v = float(self.sld_sun_v.value()) if hasattr(self, 'sld_sun_v') else 0.25
            el_deg = max(0.1, (0.5 - v) * 180.0)
            sky_diffuse = clip_info.get("sky_diffuse", 1.0)
            updated_clip = self.detect_sun_clipping(clip_info["measured_lux"], el_deg, turbidity, sky_diffuse=sky_diffuse, mode=mode)
            self._last_sun_clip_info = updated_clip

            if hasattr(self, 'lbl_sun_clipping_status'):
                if updated_clip["is_clipped"]:
                    self.lbl_sun_clipping_status.setText(f"🚨 Clipped by +{updated_clip['ev_stops_lost']:.1f} EV stops ({updated_clip['ratio']:.1f}× under)")
                    self.lbl_sun_clipping_status.setStyleSheet("color: #ffaa00; font-weight: bold;")
                else:
                    self.lbl_sun_clipping_status.setText("✅ Sun Unclipped (Physical)")
                    self.lbl_sun_clipping_status.setStyleSheet("color: #00ff88; font-weight: bold;")
            if hasattr(self, 'lbl_sun_physical_flux'):
                if mode == "absolute":
                    self.lbl_sun_physical_flux.setText(
                        f"Measured: {updated_clip['measured_lux']:.1f} | Expected: {updated_clip['physical_lux']:.0f} lux (Absolute)"
                    )
                else:
                    self.lbl_sun_physical_flux.setText(
                        f"Ratio: {updated_clip['sun_sky_ratio']:.1f}:1 | Measured: {updated_clip['measured_lux']:.2f} | Expected: {updated_clip['physical_lux']:.2f}"
                    )

            if reconstruct_en and updated_clip["is_clipped"]:
                target_intensity = (1.0 - blend) * updated_clip["measured_lux"] + blend * updated_clip["physical_lux"]
                target_angle = self.compute_solar_angular_diameter(turbidity)
                self.sld_sun_intensity.setValue(round(target_intensity, 2))
                if hasattr(self, 'sld_sun_angle'):
                    self.sld_sun_angle.setValue(round(target_angle, 2))
            else:
                self.sld_sun_intensity.setValue(round(updated_clip["measured_lux"], 2))
                if hasattr(self, 'sld_sun_angle'):
                    self.sld_sun_angle.setValue(0.53)
        self._sync_node()

    def _apply_light_groups_to_stage(self, *args, notify_ui=True, **kwargs):
        """Enable and apply AOV Light Groups / LPE Tags directly on all physical stage lights for Karma, Arnold, and Redshift."""
        if args and isinstance(args[0], bool):
            notify_ui = True
        stage_node = self._get_stage_node()
        if not stage_node:
            return

        # Ensure legacy python aov manifest node is removed if present
        aov_node = stage_node.node("hdri_match_aovs")
        if aov_node is not None:
            try:
                aov_node.destroy()
                self.log("Removed legacy /stage/hdri_match_aovs python node", "DETAIL")
            except Exception:
                pass

        p = self._collect_parms()
        enabled = bool(p.get("aov_en", True) and p.get("aov_lightgroups", True))

        dome_lg = p.get("lg_dome", "lg_dome")
        sun_lg = p.get("lg_sun", "lg_sun")
        practicals_lg = p.get("lg_practicals", "lg_practicals")

        applied_count = 0
        dome_node = stage_node.node("hdri_dome")
        if dome_node:
            self._apply_light_group_to_node(dome_node, dome_lg, enabled=enabled)
            applied_count += 1

        sun_node = stage_node.node("hdri_sun")
        if sun_node:
            self._apply_light_group_to_node(sun_node, sun_lg, enabled=enabled)
            applied_count += 1

        for child in stage_node.children():
            cname = child.name()
            if cname.startswith("practical_") or cname.startswith("emitter_"):
                self._apply_light_group_to_node(child, practicals_lg, enabled=enabled)
                applied_count += 1
            elif "crucible" in cname or "crucible" in child.type().name():
                self._apply_light_group_to_node(child, "lg_crucible", enabled=enabled)
                applied_count += 1

        status_msg = f"AOV Light Groups {'applied to' if enabled else 'cleared on'} {applied_count} physical lights (Karma, Arnold, Redshift)."
        self.log(status_msg, "SUCCESS")
        if notify_ui:
            if hou.isUIAvailable():
                hou.ui.displayMessage(status_msg, title="DomeBreaker")
            else:
                print(f"[HDRI Match] {status_msg}")

    def _detect_sun(self, notify_ui=True):
        """Detect the sun position in the HDRI."""
        self.log("Action: Detect Sun initiated...", "INFO")
        hdri_path = self.txt_hdri.text()
        if not hdri_path:
            self.log_error("Please load an HDRI first.")
            if notify_ui and hou.isUIAvailable():
                hou.ui.displayMessage("Please load an HDRI first.", severity=hou.severityType.Warning)
            return

        try:
            self.log(f"Analyzing equirectangular HDRI for solar centroid: {hdri_path}...", "DETAIL")
            from hdri_match.io.loader import load_exr_to_numpy
            from hdri_match.analysis.hdri_stats import HDRIStats

            arr = load_exr_to_numpy(hdri_path)
            pos = HDRIStats.compute_sun_position(arr)
            u, v = pos["uv"]
            self.sld_sun_u.setValue(float(u))
            self.sld_sun_v.setValue(float(v))
            self._extract_and_apply_sun_values(arr)
            az = pos.get("azimuth_deg", pos.get("azimuth", 0.0))
            el = pos.get("elevation_deg", pos.get("elevation", 0.0))
            msg = f"Sun detected at UV=({u:.4f}, {v:.4f}) | Azimuth: {az:.1f}° | Elevation: {el:.1f}°"
            self.log(msg, "SUCCESS")
            if notify_ui and hou.isUIAvailable():
                hou.ui.displayMessage(msg, title="Sun Detection")
            else:
                print("[HDRI Match Sun] " + msg)
        except Exception as e:
            self.log_error("Sun detection error", e)
            if hou.isUIAvailable():
                hou.ui.displayMessage(f"Sun detection error:\n{e}", severity=hou.severityType.Error)


# =====================================================================
# Code generation functions (module-level for cleanliness)
# =====================================================================


    # ------------------------------------------------------------------
    # Debug Console & Output Window Handlers
    # ------------------------------------------------------------------

    def log(self, text, level="INFO"):
        """Log a formatted message to docked console and floating debug window."""
        import datetime
        now = datetime.datetime.now().strftime("%H:%M:%S")
        color_map = {
            "INFO": "#00c8ff",      # Cyan
            "SUCCESS": "#00ff88",   # Bright Green
            "WARNING": "#ffaa00",   # Amber
            "ERROR": "#ff3b3b",     # Bright Red
            "DETAIL": "#888888",    # Muted Grey
        }
        color = color_map.get(level.upper(), "#ffffff")
        prefix = f'<span style="color: #666;">[{now}]</span> <b style="color: {color};">[{level.upper()}]</b>'
        escaped = (
            str(text).replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace("\n", "<br/>&nbsp;&nbsp;&nbsp;&nbsp;")
        )
        html = f"{prefix} {escaped}"

        # Docked console
        if hasattr(self, "txt_log") and self.txt_log:
            self.txt_log.append(html)
            self.txt_log.verticalScrollBar().setValue(self.txt_log.verticalScrollBar().maximum())

        # Floating window
        if hasattr(self, "_debug_win") and self._debug_win:
            self._debug_win.append_log(text, level)

        # Status header
        if hasattr(self, "lbl_log_status") and self.lbl_log_status:
            if level.upper() == "ERROR":
                self.lbl_log_status.setText("Status: Error")
                self.lbl_log_status.setStyleSheet("color: #ff3b3b; font-size: 11px; font-weight: bold;")
            elif level.upper() == "SUCCESS":
                self.lbl_log_status.setText("Status: Success")
                self.lbl_log_status.setStyleSheet("color: #00ff88; font-size: 11px; font-weight: bold;")
            elif level.upper() == "INFO":
                self.lbl_log_status.setText("Status: Processing...")
                self.lbl_log_status.setStyleSheet("color: #00c8ff; font-size: 11px; font-weight: bold;")

        # Also print to terminal
        print(f"[HDRI Match {level.upper()}] {text}")

    def log_error(self, title, exc=None):
        """Log an exception or error with full traceback and auto-open floating window."""
        import traceback
        tb = traceback.format_exc() if exc else ""
        err_msg = f"{title}: {exc}" if exc else title
        self.log(err_msg, "ERROR")
        if hasattr(self, 'grp_log') and hasattr(self.grp_log, 'set_collapsed'):
            self.grp_log.set_collapsed(False)
        if tb and tb.strip() != "NoneType: None":
            self.log(tb, "DETAIL")
        if hasattr(self, "chk_auto_popout") and self.chk_auto_popout.isChecked():
            self._popout_log_window()

    def _popout_log_window(self):
        """Show and bring to front the floating debug output window."""
        if hasattr(self, "_debug_win") and self._debug_win:
            self._debug_win.show()
            self._debug_win.raise_()
            self._debug_win.activateWindow()

    def _copy_log(self):
        if hasattr(self, "txt_log") and self.txt_log:
            QtWidgets.QApplication.clipboard().setText(self.txt_log.toPlainText())
            self.log("Docked debug log copied to clipboard.", "DETAIL")

    def _clear_log(self):
        if hasattr(self, "txt_log") and self.txt_log:
            self.txt_log.clear()
            self.lbl_log_status.setText("Status: Ready")
            self.lbl_log_status.setStyleSheet("color: #00ff88; font-size: 11px; font-weight: bold;")
        if hasattr(self, "_debug_win") and self._debug_win:
            self._debug_win._clear_log()


# ---------------------------------------------------------------------------
# USD Code Generators
# ---------------------------------------------------------------------------

def _gen_calibrate_code(p):
    """Generate Python code for the calibrate Python LOP node."""
    lines = [
        'import os, sys, numpy as np',
        'import hou',
        '',
        'root = os.environ.get("HDRI_MATCH_PLATE_ROOT", "")',
        'if root and root not in sys.path:',
        '    sys.path.insert(0, root)',
        '',
        'import hdri_match_solaris.oiio_adapter',
        'from hdri_match.core.pipeline import CalibrationPipeline',
        'import hdri_match_solaris.oiio_adapter',
        'from hdri_match.io.loader import load_exr_to_numpy',
        'from hdri_match.io.exporter import save_numpy_to_image',
        'from pxr import UsdLux, UsdGeom, Gf, Sdf',
        '',
        'node = hou.pwd()',
        'stage = node.editableStage()',
        '',
        'hdri_path = r"{0}"'.format(p["hdri_path"]),
        'plate_path = r"{0}"'.format(p["plate_path"]),
        '',
        'pipeline = CalibrationPipeline()',
        'pipeline.load_inputs(hdri_path, plate_path,',
        '    input_space="{0}", working_space="Linear")'.format(p["input_cs"]),
        '',
        'pipeline.state.apply_exposure_match = {0}'.format(p["auto_cal"]),
        'pipeline.state.sky_mode = "{0}"'.format(p["sky_mode"]),
        'pipeline.state.ev_offset = {0}'.format(p["ev"]),
        'pipeline.state.black_offset = {0}'.format(p["black"]),
        'pipeline.state.temperature = {0}'.format(p["temp"]),
        'pipeline.state.tint = {0}'.format(p["tint"]),
        'pipeline.state.hdri_yaw = {0}'.format(p["yaw"]),
        'pipeline.state.horizon_enable = {0}'.format(p["horizon_en"]),
        'pipeline.state.horizon_height = {0}'.format(p["horizon_h"]),
        'pipeline.state.horizon_feather = {0}'.format(p["horizon_f"]),
        'pipeline.state.sky_ev_offset = {0}'.format(p["sky_ev"]),
        'pipeline.state.ground_ev_offset = {0}'.format(p["ground_ev"]),
        'pipeline.state.softclip_enable = {0}'.format(p["softclip_en"]),
        'pipeline.state.softclip_threshold = {0}'.format(p["softclip_t"]),
        'pipeline.state.softclip_rolloff = {0}'.format(p["softclip_r"]),
        '',
        'if {0} and plate_path and os.path.isfile(plate_path):'.format(
            p["auto_cal"]),
        '    pipeline.compute_calibration(protect_sun={0})'.format(
            p["protect_sun"]),
        '',
        'calibrated = pipeline.process_hdri(use_proxy=False)',
        '',
        'hip = hou.expandString("$HIP")',
        'out_dir = os.path.join(hip, "hdri_match")',
        'os.makedirs(out_dir, exist_ok=True)',
        'base = os.path.splitext(os.path.basename(hdri_path))[0]',
        'output_path = os.path.join(out_dir, base + "_calibrated.exr")',
        'save_numpy_to_image(calibrated, output_path)',
        'print("[HDRI Match] Calibrated HDRI ->", output_path)',
        '',
        'scope = stage.GetPrimAtPath("/lights")',
        'if not scope.IsValid():',
        '    UsdGeom.Scope.Define(stage, "/lights")',
        '',
        'dome = UsdLux.DomeLight.Define(stage, "/lights/hdri_dome")',
        'dome.CreateTextureFileAttr().Set(output_path.replace(chr(92), "/"))',
        'dome.CreateTextureFormatAttr().Set("latlong")',
        'dome.CreateExposureAttr().Set(float(pipeline.state.ev_offset))',
        '',
        'if abs({0}) > 0.01:'.format(p["yaw"]),
        '    xfm = UsdGeom.Xformable(dome.GetPrim())',
        '    xfm.ClearXformOpOrder()',
        '    xfm.AddRotateYOp(UsdGeom.XformOp.PrecisionFloat).Set('
        'float({0}))'.format(p["yaw"]),
        '',
        '# Store metadata',
        'ns = "hdriMatch"',
        'for k, v in {"ev_offset": float(pipeline.state.ev_offset),',
        '              "temperature": float(pipeline.state.temperature),',
        '              "tint": float(pipeline.state.tint),',
        '              "hdri_yaw": float(pipeline.state.hdri_yaw),',
        '              "calibrated_path": output_path,',
        '              "source_hdri": hdri_path}.items():',
        '    attr_name = ns + ":" + k',
        '    if isinstance(v, float):',
        '        dome.GetPrim().CreateAttribute('
        'attr_name, Sdf.ValueTypeNames.Float).Set(v)',
        '    elif isinstance(v, str):',
        '        dome.GetPrim().CreateAttribute('
        'attr_name, Sdf.ValueTypeNames.String).Set(v)',
        '',
        'print("[HDRI Match] DomeLight created at /lights/hdri_dome")',
    ]
    return "\n".join(lines)


def _gen_sun_code(p):
    """Generate Python code for the sun relighting Python LOP node."""
    lines = [
        'import os, sys, math, numpy as np',
        'import hou',
        'from pxr import UsdLux, UsdGeom, Gf, Sdf',
        '',
        'root = os.environ.get("HDRI_MATCH_PLATE_ROOT", "")',
        'if root and root not in sys.path:',
        '    sys.path.insert(0, root)',
        '',
        'node = hou.pwd()',
        'stage = node.editableStage()',
        '',
        'dome_prim = stage.GetPrimAtPath("/lights/hdri_dome")',
        'hdri_path = ""',
        'if dome_prim.IsValid():',
        '    attr = dome_prim.GetAttribute("hdriMatch:calibrated_path")',
        '    if attr.IsValid():',
        '        hdri_path = attr.Get()',
        '',
        'if not hdri_path or not os.path.isfile(hdri_path):',
        '    raise hou.NodeError("No calibrated HDRI found.")',
        '',
        'from hdri_match.io.loader import load_exr_to_numpy',
        'from hdri_match.io.exporter import save_numpy_to_image',
        'hdri_array = load_exr_to_numpy(hdri_path)',
        '',
        'source_u, source_v = 0.5, 0.25',
        'if {0}:'.format(p["sun_auto"]),
        '    from hdri_match.calibration.sun_detection import SunDetector',
        '    result = SunDetector.detect_sun(hdri_array)',
        '    if result is not None:',
        '        source_u, source_v = result[0], result[1]',
        '',
        'from hdri_match.analysis.sun_relighter import SunRelighter',
        'relit = SunRelighter.relight(hdri_array,',
        '    source_u=source_u, source_v=source_v,',
        '    target_u={0}, target_v={1},'.format(p["sun_u"], p["sun_v"]),
        '    radius_norm={0},'.format(p["sun_radius"]),
        '    feather_norm={0})'.format(p["sun_radius"] * 0.5),
        '',
        'hip = hou.expandString("$HIP")',
        'out_dir = os.path.join(hip, "hdri_match")',
        'os.makedirs(out_dir, exist_ok=True)',
        'base = os.path.splitext(os.path.basename(hdri_path))[0]'
        '.replace("_calibrated", "")',
        'output_path = os.path.join(out_dir, base + "_sun_relit.exr")',
        'save_numpy_to_image(relit, output_path)',
        '',
        'if dome_prim.IsValid():',
        '    dome = UsdLux.DomeLight(dome_prim)',
        '    dome.GetTextureFileAttr().Set('
        'output_path.replace(chr(92), "/"))',
        '',
        'az = (0.5 - {0}) * 360.0'.format(p["sun_u"]),
        'el = 90.0 - ({0} * 180.0)'.format(p["sun_v"]),
        '',
        'sun = UsdLux.DistantLight.Define(stage, "/lights/sun_direct")',
        'sun.CreateAngleAttr().Set(float({0}))'.format(p["sun_angle"]),
        'sun.CreateIntensityAttr().Set(1.0)',
        '',
        'h, w = hdri_array.shape[:2]',
        'sy, sx = int(source_v * h), int(source_u * w)',
        'r = max(2, int({0} * min(h, w) * 0.5))'.format(p["sun_radius"]),
        'patch = hdri_array[max(0,sy-r):min(h,sy+r),'
        ' max(0,sx-r):min(w,sx+r), :3]',
        'if patch.size > 0:',
        '    sc = np.mean(patch, axis=(0,1))',
        '    sl = max(1e-6, 0.2126*sc[0]+0.7152*sc[1]+0.0722*sc[2])',
        '    sc = sc / sl',
        '    sun.CreateColorAttr().Set(Gf.Vec3f('
        'float(sc[0]),float(sc[1]),float(sc[2])))',
        '',
        'az_r = math.radians(az)',
        'el_r = math.radians(el)',
        'dx = math.cos(el_r)*math.sin(az_r)',
        'dy = math.sin(el_r)',
        'dz = math.cos(el_r)*math.cos(az_r)',
        'eye = Gf.Vec3d(dx*1000, dy*1000, dz*1000)',
        'up = Gf.Vec3d(0,1,0) if abs(el)<85 else Gf.Vec3d(0,0,1)',
        'mat = Gf.Matrix4d()',
        'mat.SetLookAt(eye, Gf.Vec3d(0,0,0), up)',
        'xfm = UsdGeom.Xformable(sun.GetPrim())',
        'xfm.ClearXformOpOrder()',
        'xfm.AddTransformOp().Set(mat.GetInverse())',
        '',
        'print("[HDRI Match] Sun relit -> /lights/sun_direct")',
    ]
    return "\n".join(lines)


def _compute_room_light_placement(az_or_phi, el_rad, theta_x, theta_y, room_w, room_d, room_h, cam_x=0.0, cam_y=1.5, cam_z=0.0, yaw=0.0, margin=0.08):
    """Raytrace an emitter direction from camera inside a room box in local room space,
    find physical wall/ceiling surface intersection, distance, aperture size, surface normal,
    and then transform position and Euler rotation angles into world space matching the room's yaw rotation."""
    import math
    cos_el = math.cos(el_rad)
    sin_el = math.sin(el_rad)
    # Local direction inside the room box (OpenUSD latlong convention: u=0.25 is -X/West, u=0.75 is +X/East, u=0.5 is -Z/North)
    dx = -cos_el * math.sin(az_or_phi)
    dy = sin_el
    dz = cos_el * math.cos(az_or_phi)

    x_min, x_max = -room_w * 0.5, room_w * 0.5
    z_min, z_max = -room_d * 0.5, room_d * 0.5
    y_min, y_max = 0.0, room_h

    candidates = []

    # Ceiling (+Y)
    if dy > 1e-6:
        ty = (y_max - cam_y) / dy
        if ty > 1e-4:
            px = cam_x + ty * dx
            pz = cam_z + ty * dz
            if x_min - 1e-3 <= px <= x_max + 1e-3 and z_min - 1e-3 <= pz <= z_max + 1e-3:
                candidates.append((ty, "ceiling", (0.0, -1.0, 0.0), (-90.0, 0.0, 0.0), px, y_max, pz))

    # Floor (-Y)
    if dy < -1e-6:
        ty = (y_min - cam_y) / dy
        if ty > 1e-4:
            px = cam_x + ty * dx
            pz = cam_z + ty * dz
            if x_min - 1e-3 <= px <= x_max + 1e-3 and z_min - 1e-3 <= pz <= z_max + 1e-3:
                candidates.append((ty, "floor", (0.0, 1.0, 0.0), (90.0, 0.0, 0.0), px, y_min, pz))

    # Left wall (-X, West)
    if dx < -1e-6:
        tx = (x_min - cam_x) / dx
        if tx > 1e-4:
            py = cam_y + tx * dy
            pz = cam_z + tx * dz
            if y_min - 1e-3 <= py <= y_max + 1e-3 and z_min - 1e-3 <= pz <= z_max + 1e-3:
                candidates.append((tx, "left", (1.0, 0.0, 0.0), (0.0, -90.0, 0.0), x_min, py, pz))

    # Right wall (+X, East)
    if dx > 1e-6:
        tx = (x_max - cam_x) / dx
        if tx > 1e-4:
            py = cam_y + tx * dy
            pz = cam_z + tx * dz
            if y_min - 1e-3 <= py <= y_max + 1e-3 and z_min - 1e-3 <= pz <= z_max + 1e-3:
                candidates.append((tx, "right", (-1.0, 0.0, 0.0), (0.0, 90.0, 0.0), x_max, py, pz))

    # Back wall (-Z, North)
    if dz < -1e-6:
        tz = (z_min - cam_z) / dz
        if tz > 1e-4:
            px = cam_x + tz * dx
            py = cam_y + tz * dy
            if x_min - 1e-3 <= px <= x_max + 1e-3 and y_min - 1e-3 <= py <= y_max + 1e-3:
                candidates.append((tz, "back", (0.0, 0.0, 1.0), (0.0, 180.0, 0.0), px, py, z_min))

    # Front wall (+Z, South)
    if dz > 1e-6:
        tz = (z_max - cam_z) / dz
        if tz > 1e-4:
            px = cam_x + tz * dx
            py = cam_y + tz * dy
            if x_min - 1e-3 <= px <= x_max + 1e-3 and y_min - 1e-3 <= py <= y_max + 1e-3:
                candidates.append((tz, "front", (0.0, 0.0, -1.0), (0.0, 0.0, 0.0), px, py, z_max))

    psi = math.radians(yaw)
    cos_y = math.cos(psi)
    sin_y = math.sin(psi)

    if not candidates:
        fallback_dist = max(1.0, min(room_w, room_d) * 0.45)
        loc_x = dx * fallback_dist
        loc_y = cam_y + dy * fallback_dist
        loc_z = dz * fallback_dist
        w_x = loc_x * cos_y + loc_z * sin_y
        w_y = loc_y
        w_z = -loc_x * sin_y + loc_z * cos_y
        return (
            (w_x, w_y, w_z),
            fallback_dist,
            max(0.2, 2.0 * fallback_dist * math.tan(theta_x / 2.0)),
            max(0.2, 2.0 * fallback_dist * math.tan(theta_y / 2.0)),
            (0.0, 0.0, 0.0),
            (0.0, yaw, 0.0)
        )

    candidates.sort(key=lambda c: c[0])
    best_t, stype, normal, loc_rot, hit_x, hit_y, hit_z = candidates[0]

    raw_w = max(0.2, 2.0 * best_t * math.tan(theta_x / 2.0))
    raw_h = max(0.2, 2.0 * best_t * math.tan(theta_y / 2.0))

    if stype == "ceiling":
        max_w = max(0.2, room_w - 0.2)
        max_h = max(0.2, room_d - 0.2)
        width = min(raw_w, max_w)
        height = min(raw_h, max_h)
        pos_x = max(x_min + width * 0.5 + 0.05, min(x_max - width * 0.5 - 0.05, hit_x)) if width < room_w - 0.1 else 0.0
        pos_z = max(z_min + height * 0.5 + 0.05, min(z_max - height * 0.5 - 0.05, hit_z)) if height < room_d - 0.1 else 0.0
        pos_y = y_max - margin
        loc_pos = (pos_x, pos_y, pos_z)
    elif stype in ("left", "right"):
        max_w = max(0.2, room_d - 0.2)
        max_h = max(0.2, room_h - 0.2)
        width = min(raw_w, max_w)
        height = min(raw_h, max_h)
        pos_x = (x_min + margin) if stype == "left" else (x_max - margin)
        pos_y = max(0.1 + height * 0.5, min(y_max - height * 0.5 - 0.05, hit_y))
        pos_z = max(z_min + width * 0.5 + 0.05, min(z_max - width * 0.5 - 0.05, hit_z)) if width < room_d - 0.1 else 0.0
        loc_pos = (pos_x, pos_y, pos_z)
    elif stype in ("back", "front"):
        max_w = max(0.2, room_w - 0.2)
        max_h = max(0.2, room_h - 0.2)
        width = min(raw_w, max_w)
        height = min(raw_h, max_h)
        pos_x = max(x_min + width * 0.5 + 0.05, min(x_max - width * 0.5 - 0.05, hit_x)) if width < room_w - 0.1 else 0.0
        pos_y = max(0.1 + height * 0.5, min(y_max - height * 0.5 - 0.05, hit_y))
        pos_z = (z_min + margin) if stype == "back" else (z_max - margin)
        loc_pos = (pos_x, pos_y, pos_z)
    else:  # floor
        max_w = max(0.2, room_w - 0.2)
        max_h = max(0.2, room_d - 0.2)
        width = min(raw_w, max_w)
        height = min(raw_h, max_h)
        pos_x = max(x_min + width * 0.5 + 0.05, min(x_max - width * 0.5 - 0.05, hit_x))
        pos_z = max(z_min + height * 0.5 + 0.05, min(z_max - height * 0.5 - 0.05, hit_z))
        loc_pos = (pos_x, margin, pos_z)

    # Transform local position to world coordinates matching /environment/ground_dome rotation
    world_px = loc_pos[0] * cos_y + loc_pos[2] * sin_y
    world_py = loc_pos[1]
    world_pz = -loc_pos[0] * sin_y + loc_pos[2] * cos_y
    world_pos = (world_px, world_py, world_pz)

    # Transform normal vector to world coordinates
    norm_x = normal[0] * cos_y + normal[2] * sin_y
    norm_y = normal[1]
    norm_z = -normal[0] * sin_y + normal[2] * cos_y
    world_normal = (norm_x, norm_y, norm_z)

    # Transform Euler rotation angles for Solaris RectLight
    if stype == "ceiling":
        world_rot = (-90.0, yaw, 0.0)
    elif stype == "floor":
        world_rot = (90.0, yaw, 0.0)
    else:
        loc_ry = loc_rot[1]
        world_ry = (loc_ry + yaw + 180.0) % 360.0 - 180.0
        world_rot = (0.0, world_ry, 0.0)

    return world_pos, best_t, width, height, world_normal, world_rot


def _gen_lookdev_code(p=None):
    """Generate Python code for the lookdev spheres Python LOP node."""
    if p is None:
        p = {}
    radius = float(p.get("lookdev_radius", 0.15))
    spacing = float(p.get("lookdev_spacing", 0.45))
    height = float(p.get("lookdev_height", 1.0))
    pos_x = float(p.get("lookdev_pos_x", 0.0))
    pos_z = float(p.get("lookdev_pos_z", 0.0))
    include_stand = bool(p.get("lookdev_stand", True))
    include_white = bool(p.get("lookdev_white", False))
    lookdev_en = bool(p.get("lookdev_en", True))

    lines = [
        'import math',
        'import hou',
        'from pxr import UsdGeom, UsdShade, Gf, Sdf',
        '',
        'node = hou.pwd()',
        'stage = node.editableStage()',
        '',
        f'lookdev_en = {lookdev_en}',
        f'radius = float({radius})',
        f'spacing = float({spacing})',
        f'height = float({height})',
        f'pos_x = float({pos_x})',
        f'pos_z = float({pos_z})',
        f'include_stand = {include_stand}',
        f'include_white = {include_white}',
        '',
        'if not lookdev_en:',
        '    if stage.GetPrimAtPath("/lookdev").IsValid():',
        '        stage.RemovePrim("/lookdev")',
        'else:',
        '    for path in ["/lookdev", "/lookdev/materials", "/lookdev/spheres", "/lookdev/stand"]:',
        '        if not stage.GetPrimAtPath(path).IsValid():',
        '            UsdGeom.Scope.Define(stage, path)',
        '',
        '    # 1. Materials',
        '    # Helper to setup multi-renderer surface outputs',
        '    def _wire_mtl(mtl, shd):',
        '        if not shd.GetOutput("surface"):',
        '            shd.CreateOutput("surface", Sdf.ValueTypeNames.Token)',
        '        s_api = shd.ConnectableAPI()',
        '        mtl.CreateSurfaceOutput().ConnectToSource(s_api, "surface")',
        '        mtl.CreateSurfaceOutput("karma").ConnectToSource(s_api, "surface")',
        '        mtl.CreateSurfaceOutput("arnold").ConnectToSource(s_api, "surface")',
        '        mtl.CreateSurfaceOutput("redshift").ConnectToSource(s_api, "surface")',
        '',
        '    # Chrome Material (Mirror Metal)',
        '    chrome_mtl = UsdShade.Material.Define(stage, "/lookdev/materials/chrome_mtl")',
        '    chrome_shd = UsdShade.Shader.Define(stage, "/lookdev/materials/chrome_mtl/shader")',
        '    chrome_shd.CreateIdAttr("UsdPreviewSurface")',
        '    chrome_shd.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(0.95, 0.95, 0.95))',
        '    chrome_shd.CreateInput("metallic", Sdf.ValueTypeNames.Float).Set(1.0)',
        '    chrome_shd.CreateInput("roughness", Sdf.ValueTypeNames.Float).Set(0.001)',
        '    chrome_shd.CreateInput("ior", Sdf.ValueTypeNames.Float).Set(100.0)',
        '    _wire_mtl(chrome_mtl, chrome_shd)',
        '',
        '    # Grey Material (18% Scene-Linear Neutral Grey)',
        '    grey_mtl = UsdShade.Material.Define(stage, "/lookdev/materials/grey_mtl")',
        '    grey_shd = UsdShade.Shader.Define(stage, "/lookdev/materials/grey_mtl/shader")',
        '    grey_shd.CreateIdAttr("UsdPreviewSurface")',
        '    grey_shd.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(0.18, 0.18, 0.18))',
        '    grey_shd.CreateInput("metallic", Sdf.ValueTypeNames.Float).Set(0.0)',
        '    grey_shd.CreateInput("roughness", Sdf.ValueTypeNames.Float).Set(0.6)',
        '    _wire_mtl(grey_mtl, grey_shd)',
        '',
        '    # Stand Material (Dark Matte Anodized Metal)',
        '    stand_mtl = UsdShade.Material.Define(stage, "/lookdev/materials/stand_mtl")',
        '    stand_shd = UsdShade.Shader.Define(stage, "/lookdev/materials/stand_mtl/shader")',
        '    stand_shd.CreateIdAttr("UsdPreviewSurface")',
        '    stand_shd.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(0.08, 0.08, 0.08))',
        '    stand_shd.CreateInput("metallic", Sdf.ValueTypeNames.Float).Set(0.8)',
        '    stand_shd.CreateInput("roughness", Sdf.ValueTypeNames.Float).Set(0.35)',
        '    _wire_mtl(stand_mtl, stand_shd)',
        '',
        '    # 2. Reference Spheres (High-Density Smooth Polygonal Meshes with Vertex Normals)',
        '    def _build_smooth_sphere(stage_obj, prim_path, rad, rings=48, sectors=96):',
        '        p = stage_obj.GetPrimAtPath(prim_path)',
        '        if p.IsValid() and p.GetTypeName() != "Mesh":',
        '            stage_obj.RemovePrim(prim_path)',
        '        m = UsdGeom.Mesh.Define(stage_obj, prim_path)',
        '        rv = float(rad)',
        '        pts = []',
        '        nrms = []',
        '        uv_arr = []',
        '        for r_i in range(rings + 1):',
        '            phi = math.pi * float(r_i) / float(rings)',
        '            y_v = rv * math.cos(phi)',
        '            ny_v = math.cos(phi)',
        '            sin_p = math.sin(phi)',
        '            v_coord = 1.0 - (float(r_i) / float(rings))',
        '            for s_i in range(sectors + 1):',
        '                theta = 2.0 * math.pi * float(s_i) / float(sectors)',
        '                x_v = rv * sin_p * math.cos(theta)',
        '                z_v = rv * sin_p * math.sin(theta)',
        '                nx_v = sin_p * math.cos(theta)',
        '                nz_v = sin_p * math.sin(theta)',
        '                u_coord = float(s_i) / float(sectors)',
        '                pts.append(Gf.Vec3f(float(x_v), float(y_v), float(z_v)))',
        '                nrms.append(Gf.Vec3f(float(nx_v), float(ny_v), float(nz_v)))',
        '                uv_arr.append(Gf.Vec2f(float(u_coord), float(v_coord)))',
        '        fc = []',
        '        fi = []',
        '        for r_i in range(rings):',
        '            for s_i in range(sectors):',
        '                p0 = r_i * (sectors + 1) + s_i',
        '                p1 = (r_i + 1) * (sectors + 1) + s_i',
        '                p2 = (r_i + 1) * (sectors + 1) + (s_i + 1)',
        '                p3 = r_i * (sectors + 1) + (s_i + 1)',
        '                fc.append(4)',
        '                # Counter-clockwise winding so geometric normals point outward matching vertex normals',
        '                fi.extend([p0, p3, p2, p1])',
        '        m.GetPointsAttr().Set(pts)',
        '        m.GetFaceVertexCountsAttr().Set(fc)',
        '        m.GetFaceVertexIndicesAttr().Set(fi)',
        '        m.GetNormalsAttr().Set(nrms)',
        '        m.SetNormalsInterpolation(UsdGeom.Tokens.vertex)',
        '        m.CreateOrientationAttr().Set(UsdGeom.Tokens.rightHanded)',
        '        m.CreateDoubleSidedAttr().Set(True)',
        '        st_pv = UsdGeom.PrimvarsAPI(m).CreatePrimvar("st", Sdf.ValueTypeNames.TexCoord2fArray, UsdGeom.Tokens.vertex)',
        '        st_pv.Set(uv_arr)',
        '        m.GetExtentAttr().Set([Gf.Vec3f(-rv, -rv, -rv), Gf.Vec3f(rv, rv, rv)])',
        '        m.GetPrim().CreateAttribute("sphere_radius", Sdf.ValueTypeNames.Float).Set(rv)',
        '        m.GetPrim().CreateAttribute("radius", Sdf.ValueTypeNames.Double).Set(float(rv))',
        '        return m',
        '',
        '    # Chrome Ball',
        '    chrome = _build_smooth_sphere(stage, "/lookdev/spheres/chrome", radius)',
        '    xfm_ch = UsdGeom.Xformable(chrome.GetPrim())',
        '    xfm_ch.ClearXformOpOrder()',
        '    xfm_ch.AddTranslateOp().Set(Gf.Vec3d(pos_x - spacing * 0.5, height, pos_z))',
        '    UsdShade.MaterialBindingAPI.Apply(chrome.GetPrim()).Bind(chrome_mtl)',
        '',
        '    # Grey Ball',
        '    grey = _build_smooth_sphere(stage, "/lookdev/spheres/grey", radius)',
        '    xfm_gr = UsdGeom.Xformable(grey.GetPrim())',
        '    xfm_gr.ClearXformOpOrder()',
        '    xfm_gr.AddTranslateOp().Set(Gf.Vec3d(pos_x + spacing * 0.5, height, pos_z))',
        '    UsdShade.MaterialBindingAPI.Apply(grey.GetPrim()).Bind(grey_mtl)',
        '',
        '    # Optional White Ball',
        '    if include_white:',
        '        white_mtl = UsdShade.Material.Define(stage, "/lookdev/materials/white_mtl")',
        '        white_shd = UsdShade.Shader.Define(stage, "/lookdev/materials/white_mtl/shader")',
        '        white_shd.CreateIdAttr("UsdPreviewSurface")',
        '        white_shd.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(0.9, 0.9, 0.9))',
        '        white_shd.CreateInput("metallic", Sdf.ValueTypeNames.Float).Set(0.0)',
        '        white_shd.CreateInput("roughness", Sdf.ValueTypeNames.Float).Set(0.9)',
        '        _wire_mtl(white_mtl, white_shd)',
        '        white = _build_smooth_sphere(stage, "/lookdev/spheres/white", radius)',
        '        xfm_wh = UsdGeom.Xformable(white.GetPrim())',
        '        xfm_wh.ClearXformOpOrder()',
        '        xfm_wh.AddTranslateOp().Set(Gf.Vec3d(pos_x + spacing * 1.5, height, pos_z))',
        '        UsdShade.MaterialBindingAPI.Apply(white.GetPrim()).Bind(white_mtl)',
        '    elif stage.GetPrimAtPath("/lookdev/spheres/white").IsValid():',
        '        stage.RemovePrim("/lookdev/spheres/white")',
        '',
        '    # 3. Calibration Stand / Tripod Post',
        '    if include_stand and height > radius * 1.2:',
        '        post_h = max(0.05, height - radius * 0.8)',
        '        post = UsdGeom.Cylinder.Define(stage, "/lookdev/stand/post")',
        '        post.GetRadiusAttr().Set(max(0.01, radius * 0.1))',
        '        post.GetHeightAttr().Set(post_h)',
        '        post.GetAxisAttr().Set("Y")',
        '        xfm_p = UsdGeom.Xformable(post.GetPrim())',
        '        xfm_p.ClearXformOpOrder()',
        '        xfm_p.AddTranslateOp().Set(Gf.Vec3d(pos_x, post_h * 0.5, pos_z))',
        '        UsdShade.MaterialBindingAPI.Apply(post.GetPrim()).Bind(stand_mtl)',
        '',
        '        base = UsdGeom.Cylinder.Define(stage, "/lookdev/stand/base")',
        '        base.GetRadiusAttr().Set(max(0.1, radius * 1.2))',
        '        base.GetHeightAttr().Set(0.02)',
        '        base.GetAxisAttr().Set("Y")',
        '        xfm_b = UsdGeom.Xformable(base.GetPrim())',
        '        xfm_b.ClearXformOpOrder()',
        '        xfm_b.AddTranslateOp().Set(Gf.Vec3d(pos_x, 0.01, pos_z))',
        '        UsdShade.MaterialBindingAPI.Apply(base.GetPrim()).Bind(stand_mtl)',
        '',
        '        bar_len = spacing + radius * 1.5',
        '        cbar = UsdGeom.Cylinder.Define(stage, "/lookdev/stand/crossbar")',
        '        cbar.GetRadiusAttr().Set(max(0.008, radius * 0.08))',
        '        cbar.GetHeightAttr().Set(bar_len)',
        '        cbar.GetAxisAttr().Set("X")',
        '        xfm_c = UsdGeom.Xformable(cbar.GetPrim())',
        '        xfm_c.ClearXformOpOrder()',
        '        xfm_c.AddTranslateOp().Set(Gf.Vec3d(pos_x, height - radius * 0.8, pos_z))',
        '        UsdShade.MaterialBindingAPI.Apply(cbar.GetPrim()).Bind(stand_mtl)',
        '    else:',
        '        if stage.GetPrimAtPath("/lookdev/stand").IsValid():',
        '            stage.RemovePrim("/lookdev/stand")',
        '            UsdGeom.Scope.Define(stage, "/lookdev/stand")',
        '',
        '    # Remove legacy ground plane if present from earlier runs',
        '    if stage.GetPrimAtPath("/lookdev/ground_plane").IsValid():',
        '        stage.RemovePrim("/lookdev/ground_plane")',
        '    if stage.GetPrimAtPath("/lookdev/materials/ground_mtl").IsValid():',
        '        stage.RemovePrim("/lookdev/materials/ground_mtl")',
        '',
        f'    print(f"[HDRI Match] Reference balls created: radius={radius}m, spacing={spacing}m, height={height}m")',
    ]
    return "\n".join(lines)



def _gen_light_extract_code(p):
    """Generate Python code for the practical light extraction Python LOP node in Solaris."""
    hdri_path = (p.get("hdri_path", "")).replace(chr(92), "/")
    extract_count = int(p.get("extract_count", 3))
    extract_thresh = float(p.get("extract_thresh", 3.0))
    sun_en = bool(p.get("sun_en", False))
    sun_u = float(p.get("sun_u", 0.5))
    sun_v = float(p.get("sun_v", 0.25))
    sun_radius = float(p.get("sun_radius", 0.05))
    ev = float(p.get("ev", 0.0))
    yaw = float(p.get("yaw", 0.0))

    lines = [
        'import os, sys, math, numpy as np',
        'import hou',
        'from pxr import UsdLux, UsdGeom, Gf',
        '',
        'root = os.environ.get("HDRI_MATCH_PLATE_ROOT", "")',
        'if root and root not in sys.path:',
        '    sys.path.insert(0, root)',
        '',
        'node = hou.pwd()',
        'stage = node.editableStage()',
        'if not stage:',
        '    raise hou.NodeError("No editable stage available.")',
        '',
        'hdri_path = r"{0}"'.format(hdri_path),
        'if not os.path.isfile(hdri_path):',
        '    dome_prim = stage.GetPrimAtPath("/lights/hdri_dome")',
        '    if dome_prim.IsValid():',
        '        attr = dome_prim.GetAttribute("inputs:texture:file")',
        '        if attr.IsValid() and attr.Get():',
        '            tex = str(attr.Get().path if hasattr(attr.Get(), "path") else attr.Get())',
        '            if os.path.isfile(tex):',
        '                hdri_path = tex',
        '',
        'from hdri_match.io.loader import load_exr_to_numpy',
        'arr = load_exr_to_numpy(hdri_path)',
        'if arr is None:',
        '    print("[DomeBreaker] Warning: Unable to load HDRI for light extraction: " + str(hdri_path))',
        'else:',
        '    h, w = arr.shape[:2]',
        '    luma = 0.2126 * arr[..., 0] + 0.7152 * arr[..., 1] + 0.0722 * arr[..., 2]',
        '    temp_luma = luma.copy()',
        '',
        '    # 1. Mask out primary sun if Sun Relighting is active to extract only practical lights',
        '    if {0}:'.format(sun_en),
        '        sun_x = int(round({0} * w))'.format(sun_u),
        '        sun_y = int(round({0} * h))'.format(sun_v),
        '        sun_r_px = int(max(8, round({0} * w * 1.5)))'.format(sun_radius),
        '        y_idx, x_idx = np.ogrid[:h, :w]',
        '        dx = np.abs(x_idx - sun_x)',
        '        dx = np.minimum(dx, w - dx)',
        '        dy = np.abs(y_idx - sun_y)',
        '        temp_luma[(dx**2 + dy**2) < (sun_r_px**2)] = 0.0',
        '',
        '    # 2. Adaptive threshold relative to median luminance',
        '    valid_luma = temp_luma[temp_luma > 0]',
        '    median_luma = float(np.median(valid_luma)) if valid_luma.size > 0 else 0.18',
        '    max_luma = float(np.max(temp_luma)) if temp_luma.size > 0 else 1.0',
        '    threshold = max(0.15, min(max(0.18, median_luma) * (2.0 ** {0}), max_luma * 0.25))'.format(extract_thresh),
        '',
        '    # 3. Ensure scopes and clear previous extracted practical lights',
        '    for sc in ["/lights", "/lights/extracted"]:',
        '        if not stage.GetPrimAtPath(sc).IsValid():',
        '            UsdGeom.Scope.Define(stage, sc)',
        '    extracted_scope = stage.GetPrimAtPath("/lights/extracted")',
        '    if extracted_scope.IsValid():',
        '        for child in list(extracted_scope.GetChildren()):',
        '            stage.RemovePrim(child.GetPath())',
        '',
        '    lights = []',
        '    max_count = {0}'.format(extract_count),
        '    ev_mult = 2.0 ** {0}'.format(ev),
        '    yaw_val = {0}'.format(yaw),
        '    dist = 15.0  # Studio practical distance in meters',
        '',
        '    for i in range(max_count):',
        '        max_idx = int(np.argmax(temp_luma))',
        '        py, px = divmod(max_idx, w)',
        '        val = float(temp_luma[py, px])',
        '        if val < threshold:',
        '            break',
        '',
        '        # Measure emitter core radius (half-max falloff)',
        '        r_core = max(4, w // 96)',
        '        r_px = r_core',
        '        for step_r in range(r_core, max(r_core + 2, w // 24), 2):',
        '            y0, y1 = max(0, py - step_r), min(h, py + step_r + 1)',
        '            x0, x1 = max(0, px - step_r), min(w, px + step_r + 1)',
        '            sub = luma[y0:y1, x0:x1]',
        '            if np.mean(sub) < val * 0.35:',
        '                r_px = step_r',
        '                break',
        '',
        '        # Sample normalized RGB color over core',
        '        cy0, cy1 = max(0, py - r_core), min(h, py + r_core + 1)',
        '        cx0, cx1 = max(0, px - r_core), min(w, px + r_core + 1)',
        '        core_rgb = np.mean(arr[cy0:cy1, cx0:cx1, :3], axis=(0, 1))',
        '        max_c = max(1e-5, float(np.max(core_rgb)))',
        '        norm_color = (core_rgb / max_c).tolist()',
        '',
        '        # Angular radius and solid angle',
        '        theta_rad = (float(r_px) / float(w)) * 2.0 * math.pi',
        '        solid_angle = max(1e-4, 2.0 * math.pi * (1.0 - math.cos(theta_rad)))',
        '',
        '        # Distance-compensated radiant intensity: I = E * D^2 = L_peak * solid_angle * dist^2',
        '        phys_intensity = max(15.0, float(val * solid_angle * (dist ** 2) * ev_mult))',
        '        sphere_radius = max(0.25, float(dist * math.sin(theta_rad)))',
        '',
        '        # Spherical direction vector with yaw rotation matching dome',
        '        u = (float(px) + 0.5) / float(w)',
        '        v = (float(py) + 0.5) / float(h)',
        '        az = (0.5 - u) * 360.0',
        '        el = 90.0 - (v * 180.0)',
        '        az_eff = az + yaw_val',
        '        az_r = math.radians(az_eff)',
        '        el_r = math.radians(el)',
        '        dx = math.cos(el_r) * math.sin(az_r)',
        '        dy = math.sin(el_r)',
        '        dz = math.cos(el_r) * math.cos(az_r)',
        '',
        '        pos_x = dx * dist',
        '        pos_y = max(0.25, dy * dist) if dy < 0 else dy * dist',
        '        pos_z = dz * dist',
        '',
        '        lights.append({',
        '            "name": "emitter_{0:02d}".format(i + 1),',
        '            "color": norm_color,',
        '            "intensity": phys_intensity,',
        '            "radius": sphere_radius,',
        '            "pos": (pos_x, pos_y, pos_z)',
        '        })',
        '',
        '        # Mask out current emitter with horizontal seam wrapping',
        '        mask_r = max(r_px * 2, w // 32)',
        '        y_idx, x_idx = np.ogrid[:h, :w]',
        '        dx = np.abs(x_idx - px)',
        '        dx = np.minimum(dx, w - dx)',
        '        dy = np.abs(y_idx - py)',
        '        temp_luma[(dx**2 + dy**2) < (mask_r**2)] = 0.0',
        '',
        '    # Create native UsdLux.SphereLight prims',
        '    for lt in lights:',
        '        path = "/lights/extracted/" + lt["name"]',
        '        s = UsdLux.SphereLight.Define(stage, path)',
        '        s.CreateIntensityAttr().Set(float(lt["intensity"]))',
        '        s.CreateColorAttr().Set(Gf.Vec3f(*[float(c) for c in lt["color"][:3]]))',
        '        s.CreateRadiusAttr().Set(float(lt["radius"]))',
        '        s.CreateTreatAsPointAttr().Set(False)',
        '        s.CreateNormalizeAttr().Set(False)',
        '        xfm = UsdGeom.Xformable(s.GetPrim())',
        '        xfm.ClearXformOpOrder()',
        '        xfm.AddTranslateOp().Set(Gf.Vec3d(*[float(v) for v in lt["pos"]]))',
        '',
        '    print("[DomeBreaker] Extracted {0} practical lights into /lights/extracted/".format(len(lights)))',
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Panel factory function (called by Houdini .pypanel)
# ---------------------------------------------------------------------------

def _ensure_projection_node_parms(node):
    """Ensure spare parameters exist on the hdri_match_projection pythonscript LOP node
    for zero-overhead, 60 FPS parameter tweaks."""
    if node is None:
        return
    if node.parm("room_w") is not None:
        return
    try:
        ptg = node.parmTemplateGroup()
        ptg.addParmTemplate(hou.StringParmTemplate("proj_mode", "Projection Mode", 1, default_value=["room_box"]))
        ptg.addParmTemplate(hou.FloatParmTemplate("room_w", "Room Width", 1, default_value=[8.0]))
        ptg.addParmTemplate(hou.FloatParmTemplate("room_d", "Room Depth", 1, default_value=[10.0]))
        ptg.addParmTemplate(hou.FloatParmTemplate("room_h", "Room Height", 1, default_value=[3.5]))
        ptg.addParmTemplate(hou.FloatParmTemplate("tripod_h", "Tripod Height", 1, default_value=[1.5]))
        ptg.addParmTemplate(hou.FloatParmTemplate("cam_x", "Camera Offset X", 1, default_value=[0.0]))
        ptg.addParmTemplate(hou.FloatParmTemplate("cam_z", "Camera Offset Z", 1, default_value=[0.0]))
        ptg.addParmTemplate(hou.IntParmTemplate("subdivs", "Face Subdivisions", 1, default_value=[16]))
        ptg.addParmTemplate(hou.ToggleParmTemplate("inc_floor", "Include Floor", default_value=True))
        ptg.addParmTemplate(hou.ToggleParmTemplate("inc_ceil", "Include Ceiling", default_value=True))
        ptg.addParmTemplate(hou.ToggleParmTemplate("inc_walls", "Include Walls", default_value=True))
        ptg.addParmTemplate(hou.ToggleParmTemplate("double_sided", "Double Sided", default_value=False))
        ptg.addParmTemplate(hou.ToggleParmTemplate("room_shadows", "Room Shadows", default_value=False))
        ptg.addParmTemplate(hou.ToggleParmTemplate("room_invisible", "Invisible Room", default_value=False))
        ptg.addParmTemplate(hou.FloatParmTemplate("ground_r", "Ground Radius", 1, default_value=[10.0]))
        ptg.addParmTemplate(hou.FloatParmTemplate("feather", "Feather", 1, default_value=[0.15]))
        ptg.addParmTemplate(hou.FloatParmTemplate("yaw", "Dome Yaw", 1, default_value=[0.0]))
        ptg.addParmTemplate(hou.FloatParmTemplate("roughness", "Ground Roughness", 1, default_value=[1.0]))
        ptg.addParmTemplate(hou.StringParmTemplate("disc_proj_method", "Disc Proj Method", 1, default_value=["top_view"]))
        ptg.addParmTemplate(hou.StringParmTemplate("tex_file", "Texture File", 1, default_value=[""]))
        ptg.addParmTemplate(hou.ToggleParmTemplate("use_planar", "Use Planar", default_value=False))
        ptg.addParmTemplate(hou.StringParmTemplate("mat_mode", "Material Mode", 1, default_value=["pbr"]))
        ptg.addParmTemplate(hou.FloatParmTemplate("emissive_mult", "Emissive Mult", 1, default_value=[1.0]))
        ptg.addParmTemplate(hou.StringParmTemplate("renderer_target", "Renderer Target", 1, default_value=["all"]))
        node.setParmTemplateGroup(ptg)
    except Exception as e:
        print(f"[HDRI Match] Note adding spare parameters to projection node: {e}")


def _update_proj_node_values(proj_node, p):
    """Set spare parameter values on hdri_match_projection without rewriting python script code."""
    if proj_node is None:
        return
    _ensure_projection_node_parms(proj_node)

    def _set_f(pname, val):
        parm = proj_node.parm(pname)
        if parm is not None and abs(parm.eval() - float(val)) > 1e-5:
            parm.set(float(val))

    def _set_i(pname, val):
        parm = proj_node.parm(pname)
        if parm is not None and parm.eval() != int(val):
            parm.set(int(val))

    def _set_s(pname, val):
        parm = proj_node.parm(pname)
        if parm is not None and parm.eval() != str(val):
            parm.set(str(val))

    proj_mode = p.get("proj_mode", p.get("ground_mode", "ground_disc"))
    _set_s("proj_mode", proj_mode)
    _set_f("room_w", p.get("room_width", 8.0))
    _set_f("room_d", p.get("room_depth", 10.0))
    _set_f("room_h", p.get("room_height", 3.5))
    _set_f("tripod_h", p.get("tripod_height", 1.5))
    _set_f("cam_x", p.get("cam_offset_x", 0.0))
    _set_f("cam_z", p.get("cam_offset_z", 0.0))
    _set_i("subdivs", max(2, int(p.get("room_subdivs", 16))))
    _set_i("inc_floor", 1 if p.get("room_floor", True) else 0)
    _set_i("inc_ceil", 1 if p.get("room_ceiling", True) else 0)
    _set_i("inc_walls", 1 if p.get("room_walls", True) else 0)
    _set_i("double_sided", 1 if p.get("room_double_sided", False) else 0)
    _set_i("room_shadows", 1 if p.get("room_shadows", False) else 0)
    _set_i("room_invisible", 1 if p.get("room_invisible", False) else 0)
    _set_f("ground_r", p.get("ground_radius", 10.0))
    _set_f("feather", p.get("ground_feather", 0.15))
    _set_f("yaw", p.get("yaw", 0.0))
    roughness_to_set = 1.0 if ("all" in str(p.get("renderer_target", "all")).lower()) else float(p.get("ground_roughness", 1.0))
    _set_f("roughness", roughness_to_set)
    _set_s("disc_proj_method", p.get("disc_proj_method", "top_view"))

    tex_file = (p.get("hdri_texture") or p.get("hdri_path", "")).replace(chr(92), "/")
    _set_s("tex_file", tex_file)
    use_planar = bool(p.get("use_planar", p.get("ground_tex_mode", "planar") == "planar"))
    _set_i("use_planar", 1 if use_planar else 0)
    _set_s("mat_mode", p.get("ground_mat_mode", "pbr"))
    _set_f("emissive_mult", p.get("ground_emissive_mult", 1.0))
    _set_s("renderer_target", p.get("renderer_target", "all"))


def _gen_ground_projection_code(p):
    """Generate Python LOP code for creating the USD Ground Projection or Room Box Mesh with HDRI Material, Vertex Colors, and UVs."""
    proj_mode = p.get("proj_mode", p.get("ground_mode", "ground_disc"))
    tex_file = (p.get("hdri_texture") or p.get("hdri_path", "")).replace(chr(92), "/")
    yaw = float(p.get("yaw", 0.0))
    mat_mode = p.get("ground_mat_mode", "pbr")
    emissive_mult = float(p.get("ground_emissive_mult", 1.0))
    renderer_target = p.get("renderer_target", "all")

    use_planar = bool(p.get("use_planar", p.get("ground_tex_mode", "planar") == "planar"))
    planar_tex_dict = p.get("planar_textures", {})

    if proj_mode == "room_box":
        room_w = float(p.get("room_width", 8.0))
        room_d = float(p.get("room_depth", 10.0))
        room_h = float(p.get("room_height", 3.5))
        tripod_h = float(p.get("tripod_height", 1.5))
        cam_x = float(p.get("cam_offset_x", 0.0))
        cam_z = float(p.get("cam_offset_z", 0.0))
        subdivs = max(2, int(p.get("room_subdivs", 16)))
        inc_floor = bool(p.get("room_floor", True))
        inc_ceil = bool(p.get("room_ceiling", True))
        inc_walls = bool(p.get("room_walls", True))
        double_sided = bool(p.get("room_double_sided", False))
        room_shadows = bool(p.get("room_shadows", False))
        room_invisible = bool(p.get("room_invisible", False))
        roughness = 1.0 if ("all" in str(renderer_target).lower()) else float(p.get("ground_roughness", 1.0))
        ior_val = 1.0 if roughness >= 0.999 else 1.5
        spec_val = 0.0 if roughness >= 0.999 else max(0.0, min(1.0, 1.0 - roughness))

        lines = [
            'import os, sys, math, numpy as np',
            'import hou',
            'from pxr import Usd, UsdGeom, UsdShade, Gf, Vt, Sdf',
            '',
            'for cand in [os.environ.get("HDRI_MATCH_PLATE_ROOT", ""), "E:/PROJECTS/HDRI_Match_Plate", "e:/PROJECTS/HDRI_Match_Plate"]:',
            '    if cand and os.path.isdir(cand) and cand not in sys.path:',
            '        sys.path.insert(0, cand)',
            'try:',
            '    from hdri_match.io.loader import load_exr_to_numpy',
            'except Exception:',
            '    load_exr_to_numpy = None',
            '',
            'node = hou.pwd()',
            'stage = node.editableStage()',
            '',
            'W = float(node.parm("room_w").eval() if node.parm("room_w") else 8.0)',
            'D = float(node.parm("room_d").eval() if node.parm("room_d") else 10.0)',
            'H = float(node.parm("room_h").eval() if node.parm("room_h") else 3.5)',
            'tripod_height = float(node.parm("tripod_h").eval() if node.parm("tripod_h") else 1.5)',
            'cam_x = float(node.parm("cam_x").eval() if node.parm("cam_x") else 0.0)',
            'cam_y = tripod_height',
            'cam_z = float(node.parm("cam_z").eval() if node.parm("cam_z") else 0.0)',
            'N = max(2, int(node.parm("subdivs").eval() if node.parm("subdivs") else 16))',
            'inc_floor = bool(node.parm("inc_floor").eval() if node.parm("inc_floor") else True)',
            'inc_ceil = bool(node.parm("inc_ceil").eval() if node.parm("inc_ceil") else True)',
            'inc_walls = bool(node.parm("inc_walls").eval() if node.parm("inc_walls") else True)',
            'double_sided = bool(node.parm("double_sided").eval() if node.parm("double_sided") else False)',
            'room_shadows = bool(node.parm("room_shadows").eval() if node.parm("room_shadows") else False)',
            'room_invisible = bool(node.parm("room_invisible").eval() if node.parm("room_invisible") else False)',
            'yaw = float(node.parm("yaw").eval() if node.parm("yaw") else 0.0)',
            f'tex_file = (node.parm("tex_file").eval() if node.parm("tex_file") else r"{tex_file}") or r"{tex_file}"',
            'use_planar = bool(node.parm("use_planar").eval() if node.parm("use_planar") else False)',
            f'planar_textures = {repr(planar_tex_dict)}',
            'roughness_val = float(node.parm("roughness").eval() if node.parm("roughness") else 1.0)',
            'mat_mode_val = str(node.parm("mat_mode").eval() if node.parm("mat_mode") else "pbr")',
            'emissive_mult_val = float(node.parm("emissive_mult").eval() if node.parm("emissive_mult") else 1.0)',
            'renderer_target = str(node.parm("renderer_target").eval() if node.parm("renderer_target") else "all")',
            '',
            '# Deactivate ground disc if switching to room box',
            'disc_prim = stage.GetPrimAtPath("/environment/ground_dome/ground_plane")',
            'if disc_prim.IsValid():',
            '    disc_prim.SetActive(False)',
            '',
            '# Ensure /environment is a Scope and /environment/ground_dome is a transformable Xform',
            'if not stage.GetPrimAtPath("/environment").IsValid():',
            '    UsdGeom.Scope.Define(stage, "/environment")',
            'gd_prim = stage.GetPrimAtPath("/environment/ground_dome")',
            'if not gd_prim.IsValid():',
            '    gd_xform = UsdGeom.Xform.Define(stage, "/environment/ground_dome")',
            'else:',
            '    if gd_prim.GetTypeName() != "Xform":',
            '        gd_prim.SetTypeName("Xform")',
            '    gd_xform = UsdGeom.Xform(gd_prim)',
            'for path in ["/environment/ground_dome/mats", "/environment/ground_dome/walls"]:',
            '    if not stage.GetPrimAtPath(path).IsValid():',
            '        UsdGeom.Scope.Define(stage, path)',
            'xfm = UsdGeom.XformCommonAPI(gd_xform)',
            'xfm.SetRotate(Gf.Vec3f(0.0, yaw, 0.0))',
            '',
            '# Cached HDRI array in hou.session for zero disk I/O on 60 FPS slider tweaks',
            'if not hasattr(hou.session, "_hdri_match_cache"):',
            '    hou.session._hdri_match_cache = {}',
            '',
            'hdri_arr = None',
            'if tex_file and os.path.isfile(tex_file):',
            '    try:',
            '        mtime = os.path.getmtime(tex_file)',
            '        cached = hou.session._hdri_match_cache.get(tex_file)',
            '        if cached and cached[0] == mtime:',
            '            hdri_arr = cached[1]',
            '        elif load_exr_to_numpy is not None:',
            '            hdri_arr = load_exr_to_numpy(tex_file)',
            '            hou.session._hdri_match_cache[tex_file] = (mtime, hdri_arr)',
            '    except Exception:',
            '        hdri_arr = None',
            '',
            'x_min, x_max = -W * 0.5, W * 0.5',
            'z_min, z_max = -D * 0.5, D * 0.5',
            'y_min, y_max = 0.0, H',
            'img_h, img_w = (hdri_arr.shape[:2]) if hdri_arr is not None else (512, 1024)',
            '',
            '# Precompute grid parameters and topology for resolution N',
            'ti = np.linspace(0.0, 1.0, N + 1, dtype=np.float32)',
            'tj = np.linspace(0.0, 1.0, N + 1, dtype=np.float32)',
            'TI, TJ = np.meshgrid(ti, tj)',
            'inv_TI = 1.0 - TI',
            'inv_TJ = 1.0 - TJ',
            '',
            'i_grid, j_grid = np.meshgrid(np.arange(N), np.arange(N))',
            'idx0 = j_grid * (N + 1) + i_grid',
            'idx1 = idx0 + 1',
            'idx2 = (j_grid + 1) * (N + 1) + i_grid + 1',
            'idx3 = (j_grid + 1) * (N + 1) + i_grid',
            'quad_indices = np.stack([idx0, idx1, idx2, idx3], axis=-1).flatten().astype(np.int32)',
            'f_cnts = Vt.IntArray.FromNumpy(np.full(N * N, 4, dtype=np.int32))',
            'f_idxs = Vt.IntArray.FromNumpy(quad_indices)',
            '',
            'ti0 = np.arange(N, dtype=np.float32) / float(N)',
            'tj0 = np.arange(N, dtype=np.float32) / float(N)',
            'TI0, TJ0 = np.meshgrid(ti0, tj0)',
            'TI1 = TI0 + (1.0 / float(N))',
            'TJ1 = TJ0 + (1.0 / float(N))',
            'uv_quads = np.stack([',
            '    np.stack([TI0, TJ0], axis=-1),',
            '    np.stack([TI1, TJ0], axis=-1),',
            '    np.stack([TI1, TJ1], axis=-1),',
            '    np.stack([TI0, TJ1], axis=-1),',
            '], axis=2).reshape(-1, 2).astype(np.float32)',
            'planar_f_uvs = Vt.Vec2fArray.FromNumpy(uv_quads)',
            '',
            'def build_rect_grid_mesh(mesh_path, corners, normal, is_planar=True):',
            '    P00, P10, P11, P01 = corners',
            '    P_grid = (',
            '        inv_TI[:, :, None] * inv_TJ[:, :, None] * P00 +',
            '        TI[:, :, None] * inv_TJ[:, :, None] * P10 +',
            '        TI[:, :, None] * TJ[:, :, None] * P11 +',
            '        inv_TI[:, :, None] * TJ[:, :, None] * P01',
            '    )',
            '    flat_pts = P_grid.reshape(-1, 3).astype(np.float32)',
            '    pts = Vt.Vec3fArray.FromNumpy(flat_pts)',
            '    nrms = Vt.Vec3fArray.FromNumpy(np.tile(normal.astype(np.float32), (len(flat_pts), 1)))',
            '',
            '    dx = flat_pts[:, 0] - cam_x',
            '    dy = flat_pts[:, 1] - cam_y',
            '    dz = flat_pts[:, 2] - cam_z',
            '    dist = np.maximum(np.sqrt(dx*dx + dy*dy + dz*dz), 1e-6)',
            '    vx = dx / dist',
            '    vy = dy / dist',
            '    vz = dz / dist',
            '    u = (np.arctan2(-vx, vz) / (2.0 * np.pi)) % 1.0',
            '    v = 0.5 - (np.arcsin(np.clip(vy, -1.0, 1.0)) / np.pi)',
            '',
            '    if hdri_arr is not None:',
            '        px_x = np.clip((u * (img_w - 1)).astype(np.int32), 0, img_w - 1)',
            '        px_y = np.clip((v * (img_h - 1)).astype(np.int32), 0, img_h - 1)',
            '        sampled = np.nan_to_num(hdri_arr[px_y, px_x, :3]).astype(np.float32)',
            '        cols = Vt.Vec3fArray.FromNumpy(sampled)',
            '    else:',
            '        cols = Vt.Vec3fArray.FromNumpy(np.full((len(flat_pts), 3), 0.5, dtype=np.float32))',
            '',
            '    p_mesh = stage.GetPrimAtPath(mesh_path)',
            '    if not p_mesh.IsValid():',
            '        m = UsdGeom.Mesh.Define(stage, mesh_path)',
            '        m.GetPrim().SetActive(True)',
            '        m.CreatePointsAttr(pts)',
            '        m.CreateFaceVertexCountsAttr(f_cnts)',
            '        m.CreateFaceVertexIndicesAttr(f_idxs)',
            '        m.CreateNormalsAttr(nrms)',
            '        m.SetNormalsInterpolation(UsdGeom.Tokens.vertex)',
            '        m.CreateDoubleSidedAttr().Set(double_sided)',
            '        m.CreateSubdivisionSchemeAttr().Set(UsdGeom.Tokens.none)',
            '        pv = UsdGeom.PrimvarsAPI(m.GetPrim())',
            '        if is_planar:',
            '            pv.CreatePrimvar("st", Sdf.ValueTypeNames.TexCoord2fArray, UsdGeom.Tokens.faceVarying).Set(planar_f_uvs)',
            '            pv.CreatePrimvar("uv", Sdf.ValueTypeNames.TexCoord2fArray, UsdGeom.Tokens.faceVarying).Set(planar_f_uvs)',
            '        else:',
            '            u_p = u[quad_indices].reshape(-1, 4)',
            '            v_p = v[quad_indices].reshape(-1, 4)',
            '            u_diff = np.max(u_p, axis=1) - np.min(u_p, axis=1)',
            '            wrap_mask = u_diff > 0.5',
            '            if np.any(wrap_mask):',
            '                u_p[wrap_mask] = np.where(u_p[wrap_mask] < 0.5, u_p[wrap_mask] + 1.0, u_p[wrap_mask])',
            '            u_flat = u_p.flatten().astype(np.float32)',
            '            v_flat = (1.0 - v_p.flatten()).astype(np.float32)',
            '            s_uvs = Vt.Vec2fArray.FromNumpy(np.stack([u_flat, v_flat], axis=-1))',
            '            pv.CreatePrimvar("st", Sdf.ValueTypeNames.TexCoord2fArray, UsdGeom.Tokens.faceVarying).Set(s_uvs)',
            '            pv.CreatePrimvar("uv", Sdf.ValueTypeNames.TexCoord2fArray, UsdGeom.Tokens.faceVarying).Set(s_uvs)',
            '        m.CreateDisplayColorPrimvar(UsdGeom.Tokens.vertex).Set(cols)',
            '        m.CreateDisplayOpacityPrimvar(UsdGeom.Tokens.vertex).Set(Vt.FloatArray.FromNumpy(np.ones(len(flat_pts), dtype=np.float32)))',
            '        xf = UsdGeom.XformCommonAPI(m)',
            '        xf.SetRotate(Gf.Vec3f(0.0, yaw, 0.0))',
            '    else:',
            '        m = UsdGeom.Mesh(p_mesh)',
            '        if not p_mesh.IsActive():',
            '            p_mesh.SetActive(True)',
            '        m.GetPointsAttr().Set(pts)',
            '        m.GetNormalsAttr().Set(nrms)',
            '        pv = UsdGeom.PrimvarsAPI(p_mesh)',
            '        if not is_planar:',
            '            u_p = u[quad_indices].reshape(-1, 4)',
            '            v_p = v[quad_indices].reshape(-1, 4)',
            '            u_diff = np.max(u_p, axis=1) - np.min(u_p, axis=1)',
            '            wrap_mask = u_diff > 0.5',
            '            if np.any(wrap_mask):',
            '                u_p[wrap_mask] = np.where(u_p[wrap_mask] < 0.5, u_p[wrap_mask] + 1.0, u_p[wrap_mask])',
            '            u_flat = u_p.flatten().astype(np.float32)',
            '            v_flat = (1.0 - v_p.flatten()).astype(np.float32)',
            '            s_uvs = Vt.Vec2fArray.FromNumpy(np.stack([u_flat, v_flat], axis=-1))',
            '            pv_st = pv.GetPrimvar("st")',
            '            if pv_st: pv_st.Set(s_uvs)',
            '            else: pv.CreatePrimvar("st", Sdf.ValueTypeNames.TexCoord2fArray, UsdGeom.Tokens.faceVarying).Set(s_uvs)',
            '            pv_uv = pv.GetPrimvar("uv")',
            '            if pv_uv: pv_uv.Set(s_uvs)',
            '            else: pv.CreatePrimvar("uv", Sdf.ValueTypeNames.TexCoord2fArray, UsdGeom.Tokens.faceVarying).Set(s_uvs)',
            '        pv_col = pv.GetPrimvar("displayColor")',
            '        if pv_col: pv_col.Set(cols)',
            '        else: m.CreateDisplayColorPrimvar(UsdGeom.Tokens.vertex).Set(cols)',
            '    return m',
            '',
            '# ---- Native multi-renderer material function ----',
        ]
        # Embed the full multi-renderer create_native_material() function
        # from renderer_materials.py, respecting the selected renderer target.
        if _rmat is not None:
            mat_fn_lines = _rmat.gen_create_material_function()
            lines.extend(mat_fn_lines)
        else:
            lines.extend([
                'def create_native_material(stage, mat_path, tex_file_path, roughness_val=0.85, mat_mode_val="pbr", emissive_mult_val=1.0, st_varname="st", renderer_target="all"):',
                '    """Fallback: UsdPreviewSurface-only material."""',
                '    if "all" in str(renderer_target).lower():',
                '        roughness_val = 1.0',
                '    _mat = UsdShade.Material.Define(stage, mat_path)',
                '    _ups = UsdShade.Shader.Define(stage, mat_path.AppendChild("PBRShader"))',
                '    _ups.CreateIdAttr("UsdPreviewSurface")',
                '    _ups_out = _ups.CreateOutput("surface", Sdf.ValueTypeNames.Token)',
                '    _ups.CreateInput("roughness", Sdf.ValueTypeNames.Float).Set(roughness_val)',
                '    if tex_file_path and os.path.isfile(tex_file_path):',
                '        _tex = UsdShade.Shader.Define(stage, mat_path.AppendChild("TextureSampler"))',
                '        _tex.CreateIdAttr("UsdUVTexture")',
                '        _tex.CreateInput("file", Sdf.ValueTypeNames.Asset).Set(Sdf.AssetPath(tex_file_path))',
                '        _tex.CreateInput("sourceColorSpace", Sdf.ValueTypeNames.Token).Set("raw")',
                '        _ups.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).ConnectToSource(_tex.ConnectableAPI(), "rgb")',
                '    else:',
                '        _ups.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(0.5, 0.5, 0.5))',
                '    _mat.CreateSurfaceOutput().ConnectToSource(_ups_out)',
                '    return _mat',
            ])
        lines.extend([
            '',
            'def apply_surface_attrs(prim):',
            '    """Apply shadow, emissive, and render visibility attributes to a room mesh prim if changed."""',
            '    target_karma_vis = "diffuse reflect refract" if (room_invisible and not room_shadows) else ("diffuse reflect refract shadow" if room_invisible else ("primary diffuse reflect refract" if not room_shadows else "*"))',
            '    cur_k_vis = prim.GetAttribute("primvars:karma:object:rendervisibility").Get() if prim.HasAttribute("primvars:karma:object:rendervisibility") else None',
            '    if cur_k_vis != target_karma_vis:',
            '        prim.CreateAttribute("primvars:karma:object:rendervisibility", Sdf.ValueTypeNames.String, False).Set(target_karma_vis)',
            '    target_a_cam = False if room_invisible else True',
            '    cur_a_cam = prim.GetAttribute("primvars:arnold:visibility:camera").Get() if prim.HasAttribute("primvars:arnold:visibility:camera") else None',
            '    if cur_a_cam != target_a_cam:',
            '        prim.CreateAttribute("primvars:arnold:visibility:camera", Sdf.ValueTypeNames.Bool, False).Set(target_a_cam)',
            '        prim.CreateAttribute("arnold:visibility:camera", Sdf.ValueTypeNames.Int, False).Set(1 if target_a_cam else 0)',
            '        prim.CreateAttribute("primvars:redshift:object:MESHFLAG_PRIMARYRAYVISIBLE", Sdf.ValueTypeNames.Bool, False).Set(target_a_cam)',
            '        prim.CreateAttribute("redshift:object:MESHFLAG_PRIMARYRAYVISIBLE", Sdf.ValueTypeNames.Bool, False).Set(target_a_cam)',
            '        prim.CreateAttribute("primvars:redshift:object:MESHFLAG_PRIMARYRAYVIS", Sdf.ValueTypeNames.Bool, False).Set(target_a_cam)',
            '        prim.CreateAttribute("redshift:object:MESHFLAG_PRIMARYRAYVIS", Sdf.ValueTypeNames.Bool, False).Set(target_a_cam)',
            '    target_shd = bool(room_shadows)',
            '    cur_shd = prim.GetAttribute("primvars:arnold:visibility:shadow").Get() if prim.HasAttribute("primvars:arnold:visibility:shadow") else None',
            '    if cur_shd != target_shd:',
            '        prim.CreateAttribute("primvars:arnold:visibility:shadow", Sdf.ValueTypeNames.Bool, False).Set(target_shd)',
            '        prim.CreateAttribute("arnold:visibility:shadow", Sdf.ValueTypeNames.Int, False).Set(1 if target_shd else 0)',
            '        prim.CreateAttribute("primvars:arnold:opaque", Sdf.ValueTypeNames.Bool, False).Set(True)',
            '        prim.CreateAttribute("arnold:opaque", Sdf.ValueTypeNames.Bool, False).Set(True)',
            '        prim.CreateAttribute("primvars:redshift:object:MESHFLAG_SHADOWCASTER", Sdf.ValueTypeNames.Bool, False).Set(target_shd)',
            '        prim.CreateAttribute("redshift:object:MESHFLAG_SHADOWCASTER", Sdf.ValueTypeNames.Bool, False).Set(target_shd)',
            '    if mat_mode_val in ("emissive", "pbr_emissive"):',
            '        prim.CreateAttribute("primvars:karma:object:treat_as_lightsource", Sdf.ValueTypeNames.Int, False).Set(1)',
            '        prim.CreateAttribute("primvars:karma:object:lightsource:samplingquality", Sdf.ValueTypeNames.Float, False).Set(1.0)',
            '        prim.CreateAttribute("primvars:karma:object:lightsource:doublesided", Sdf.ValueTypeNames.Int, False).Set(1 if double_sided else 0)',
            '        prim.CreateAttribute("primvars:arnold:mesh_light", Sdf.ValueTypeNames.Bool, False).Set(True)',
            '        prim.CreateAttribute("primvars:redshift:object:MESHFLAG_GICASTER", Sdf.ValueTypeNames.Bool, False).Set(True)',
            '    elif prim.HasAttribute("primvars:karma:object:treat_as_lightsource"):',
            '        prim.CreateAttribute("primvars:karma:object:treat_as_lightsource", Sdf.ValueTypeNames.Int, False).Set(0)',
            '',
            '# Shared material when not in planar mode to minimize shader compilation and USD prim graph creation',
            'shared_mat = None',
            'if not use_planar:',
            '    shared_mat_path = Sdf.Path("/environment/ground_dome/mats/room_mat")',
            '    if not stage.GetPrimAtPath(shared_mat_path).IsValid():',
            '        shared_mat = create_native_material(stage, shared_mat_path, tex_file, roughness_val, mat_mode_val, emissive_mult_val, "st", renderer_target)',
            '    else:',
            '        shared_mat = UsdShade.Material(stage.GetPrimAtPath(shared_mat_path))',
            '',
            '# --- 1. Floor ---',
            'if inc_floor:',
            '    c_floor = [',
            '        np.array([x_min, y_min, z_max]),',
            '        np.array([x_max, y_min, z_max]),',
            '        np.array([x_max, y_min, z_min]),',
            '        np.array([x_min, y_min, z_min]),',
            '    ]',
            '    fl_mesh = build_rect_grid_mesh("/environment/ground_dome/floor", c_floor, np.array([0.0, 1.0, 0.0]), is_planar=use_planar)',
            '    if not use_planar and shared_mat:',
            '        UsdShade.MaterialBindingAPI(fl_mesh.GetPrim()).Bind(shared_mat)',
            '    else:',
            '        fl_mat_path = Sdf.Path("/environment/ground_dome/mats/floor_mat")',
            '        if not stage.GetPrimAtPath(fl_mat_path).IsValid():',
            '            fl_tex = planar_textures.get("floor", tex_file) if (use_planar and planar_textures.get("floor")) else tex_file',
            '            fl_mat = create_native_material(stage, fl_mat_path, fl_tex, roughness_val, mat_mode_val, emissive_mult_val, "st", renderer_target)',
            '            UsdShade.MaterialBindingAPI(fl_mesh.GetPrim()).Bind(fl_mat)',
            '    apply_surface_attrs(fl_mesh.GetPrim())',
            'else:',
            '    fl_prim = stage.GetPrimAtPath("/environment/ground_dome/floor")',
            '    if fl_prim.IsValid():',
            '        fl_prim.SetActive(False)',
            '',
            '# --- 2. Ceiling ---',
            'if inc_ceil:',
            '    c_ceil = [',
            '        np.array([x_min, y_max, z_min]),',
            '        np.array([x_max, y_max, z_min]),',
            '        np.array([x_max, y_max, z_max]),',
            '        np.array([x_min, y_max, z_max]),',
            '    ]',
            '    cl_mesh = build_rect_grid_mesh("/environment/ground_dome/ceiling", c_ceil, np.array([0.0, -1.0, 0.0]), is_planar=use_planar)',
            '    if not use_planar and shared_mat:',
            '        UsdShade.MaterialBindingAPI(cl_mesh.GetPrim()).Bind(shared_mat)',
            '    else:',
            '        cl_mat_path = Sdf.Path("/environment/ground_dome/mats/ceiling_mat")',
            '        if not stage.GetPrimAtPath(cl_mat_path).IsValid():',
            '            cl_tex = planar_textures.get("ceiling", tex_file) if (use_planar and planar_textures.get("ceiling")) else tex_file',
            '            cl_mat = create_native_material(stage, cl_mat_path, cl_tex, roughness_val, mat_mode_val, emissive_mult_val, "st", renderer_target)',
            '            UsdShade.MaterialBindingAPI(cl_mesh.GetPrim()).Bind(cl_mat)',
            '    apply_surface_attrs(cl_mesh.GetPrim())',
            'else:',
            '    cl_prim = stage.GetPrimAtPath("/environment/ground_dome/ceiling")',
            '    if cl_prim.IsValid():',
            '        cl_prim.SetActive(False)',
            '',
            '# --- 3-6. Walls ---',
            'wall_specs = [',
            '    ("wall_north", [np.array([x_min, y_min, z_min]), np.array([x_max, y_min, z_min]), np.array([x_max, y_max, z_min]), np.array([x_min, y_max, z_min])], np.array([0.0, 0.0, 1.0])),',
            '    ("wall_south", [np.array([x_max, y_min, z_max]), np.array([x_min, y_min, z_max]), np.array([x_min, y_max, z_max]), np.array([x_max, y_max, z_max])], np.array([0.0, 0.0, -1.0])),',
            '    ("wall_east",  [np.array([x_max, y_min, z_min]), np.array([x_max, y_min, z_max]), np.array([x_max, y_max, z_max]), np.array([x_max, y_max, z_min])], np.array([-1.0, 0.0, 0.0])),',
            '    ("wall_west",  [np.array([x_min, y_min, z_max]), np.array([x_min, y_min, z_min]), np.array([x_min, y_max, z_min]), np.array([x_min, y_max, z_max])], np.array([1.0, 0.0, 0.0])),',
            ']',
            'if inc_walls:',
            '    for w_name, w_corners, w_normal in wall_specs:',
            '        w_path = f"/environment/ground_dome/walls/{w_name}"',
            '        w_mesh = build_rect_grid_mesh(w_path, w_corners, w_normal, is_planar=use_planar)',
            '        if not use_planar and shared_mat:',
            '            UsdShade.MaterialBindingAPI(w_mesh.GetPrim()).Bind(shared_mat)',
            '        else:',
            '            w_mat_path = Sdf.Path(f"/environment/ground_dome/mats/{w_name}_mat")',
            '            if not stage.GetPrimAtPath(w_mat_path).IsValid():',
            '                w_tex = planar_textures.get(w_name, tex_file) if (use_planar and planar_textures.get(w_name)) else tex_file',
            '                w_mat = create_native_material(stage, w_mat_path, w_tex, roughness_val, mat_mode_val, emissive_mult_val, "st", renderer_target)',
            '                UsdShade.MaterialBindingAPI(w_mesh.GetPrim()).Bind(w_mat)',
            '        apply_surface_attrs(w_mesh.GetPrim())',
            'else:',
            '    for w_name, _, _ in wall_specs:',
            '        w_prim = stage.GetPrimAtPath(f"/environment/ground_dome/walls/{w_name}")',
            '        if w_prim.IsValid():',
            '            w_prim.SetActive(False)',
            '',
            'proj_type_str = "Planar High-Detail Rectilinear" if use_planar else "Equirectangular Camera Projection"',
            '# Note: avoid spamming console on 60 FPS slider moves',
        ])
        return "\n".join(lines)

    # Mode: ground_disc (vectorized & cached)
    tripod_h = float(p.get("tripod_height", 1.5))
    ground_r = float(p.get("ground_radius", 10.0))
    feather = float(p.get("ground_feather", 0.15))
    roughness = 1.0 if renderer_target == "all" else float(p.get("ground_roughness", 1.0))
    ior_val = 1.0 if roughness >= 0.999 else 1.5
    spec_val = 0.0 if roughness >= 0.999 else max(0.0, min(1.0, 1.0 - roughness))
    disc_proj_method = p.get("disc_proj_method", "top_view")
    ground_planar_tex = (p.get("planar_textures", {}).get("ground") or p.get("ground_planar_texture") or "").replace(chr(92), "/")
    if disc_proj_method == "top_view" and ground_planar_tex and os.path.isfile(ground_planar_tex):
        disc_tex = ground_planar_tex
        is_top_view = True
    else:
        disc_tex = tex_file
        is_top_view = (disc_proj_method == "top_view")

    lines = [
        'import os, sys, math, numpy as np',
        'import hou',
        'from pxr import Usd, UsdGeom, UsdShade, Gf, Vt, Sdf',
        '',
        'for cand in [os.environ.get("HDRI_MATCH_PLATE_ROOT", ""), "E:/PROJECTS/HDRI_Match_Plate", "e:/PROJECTS/HDRI_Match_Plate"]:',
        '    if cand and os.path.isdir(cand) and cand not in sys.path:',
        '        sys.path.insert(0, cand)',
        'try:',
        '    from hdri_match.io.loader import load_exr_to_numpy',
        'except Exception:',
        '    load_exr_to_numpy = None',
        '',
        'def project_ground_coords(px_list, py_list, pz_list, h=1.5):',
        '    dx = np.asarray(px_list, dtype=np.float32)',
        '    dy = np.asarray(py_list, dtype=np.float32) - float(h)',
        '    dz = np.asarray(pz_list, dtype=np.float32)',
        '    dist = np.maximum(np.sqrt(dx * dx + dy * dy + dz * dz), 1e-6)',
        '    u = (np.arctan2(-dx / dist, dz / dist) / (2.0 * math.pi)) % 1.0',
        '    v = 0.5 - (np.arcsin(np.clip(dy / dist, -1.0, 1.0)) / math.pi)',
        '    return u, v',
        '',
        'node = hou.pwd()',
        'stage = node.editableStage()',
        '',
        'tripod_height = float(node.parm("tripod_h").eval() if node.parm("tripod_h") else 1.5)',
        'ground_radius = float(node.parm("ground_r").eval() if node.parm("ground_r") else 10.0)',
        'feather = float(node.parm("feather").eval() if node.parm("feather") else 0.15)',
        'yaw = float(node.parm("yaw").eval() if node.parm("yaw") else 0.0)',
        f'tex_file = (node.parm("tex_file").eval() if node.parm("tex_file") else r"{disc_tex}") or r"{disc_tex}"',
        'is_top_view = bool((node.parm("disc_proj_method").eval() if node.parm("disc_proj_method") else "top_view") == "top_view")',
        'roughness_val = float(node.parm("roughness").eval() if node.parm("roughness") else 1.0)',
        'mat_mode_val = str(node.parm("mat_mode").eval() if node.parm("mat_mode") else "pbr")',
        'emissive_mult_val = float(node.parm("emissive_mult").eval() if node.parm("emissive_mult") else 1.0)',
        'renderer_target = str(node.parm("renderer_target").eval() if node.parm("renderer_target") else "all")',
        '',
        '# Deactivate room box prims if switching to ground disc',
        'for r_path in ["/environment/ground_dome/floor", "/environment/ground_dome/ceiling", "/environment/ground_dome/walls"]:',
        '    _rp = stage.GetPrimAtPath(r_path)',
        '    if _rp.IsValid():',
        '        _rp.SetActive(False)',
        '',
    ]
    # Embed the full multi-renderer create_native_material() function
    # from renderer_materials.py, respecting the selected renderer target.
    if _rmat is not None:
        mat_fn_lines = _rmat.gen_create_material_function()
        lines.extend(mat_fn_lines)
    else:
        lines.extend([
            'def create_native_material(stage, mat_path, tex_file_path, roughness_val=0.85, mat_mode_val="pbr", emissive_mult_val=1.0, st_varname="st", renderer_target="all", opacity_primvar=None):',
            '    """Fallback: UsdPreviewSurface-only material."""',
            '    if "all" in str(renderer_target).lower():',
            '        roughness_val = 1.0',
            '    _mat = UsdShade.Material.Define(stage, mat_path)',
            '    _ups = UsdShade.Shader.Define(stage, mat_path.AppendChild("PBRShader"))',
            '    _ups.CreateIdAttr("UsdPreviewSurface")',
            '    _ups_out = _ups.CreateOutput("surface", Sdf.ValueTypeNames.Token)',
            '    _ups.CreateInput("roughness", Sdf.ValueTypeNames.Float).Set(roughness_val)',
            '    if tex_file_path and os.path.isfile(tex_file_path):',
            '        _tex = UsdShade.Shader.Define(stage, mat_path.AppendChild("TextureSampler"))',
            '        _tex.CreateIdAttr("UsdUVTexture")',
            '        _tex.CreateInput("file", Sdf.ValueTypeNames.Asset).Set(Sdf.AssetPath(tex_file_path))',
            '        _tex.CreateInput("sourceColorSpace", Sdf.ValueTypeNames.Token).Set("raw")',
            '        _ups.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).ConnectToSource(_tex.ConnectableAPI(), "rgb")',
            '    else:',
            '        _ups.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(0.5, 0.5, 0.5))',
            '    if opacity_primvar:',
            '        _op = UsdShade.Shader.Define(stage, mat_path.AppendChild("PreviewOpacityReader"))',
            '        _op.CreateIdAttr("UsdPrimvarReader_float")',
            '        _op.CreateInput("varname", Sdf.ValueTypeNames.Token).Set(opacity_primvar)',
            '        _ups.CreateInput("opacity", Sdf.ValueTypeNames.Float).ConnectToSource(_op.ConnectableAPI(), "result")',
            '    _mat.CreateSurfaceOutput().ConnectToSource(_ups_out)',
            '    return _mat',
        ])
    lines.extend([
        '# Ensure /environment is a Scope and /environment/ground_dome is a transformable Xform',
        'if not stage.GetPrimAtPath("/environment").IsValid():',
        '    UsdGeom.Scope.Define(stage, "/environment")',
        'gd_prim = stage.GetPrimAtPath("/environment/ground_dome")',
        'if not gd_prim.IsValid():',
        '    gd_xform = UsdGeom.Xform.Define(stage, "/environment/ground_dome")',
        'else:',
        '    if gd_prim.GetTypeName() != "Xform":',
        '        gd_prim.SetTypeName("Xform")',
        '    gd_xform = UsdGeom.Xform(gd_prim)',
        'xfm = UsdGeom.XformCommonAPI(gd_xform)',
        'xfm.SetRotate(Gf.Vec3f(0.0, yaw, 0.0))',
        '',
        'mesh_path = "/environment/ground_dome/ground_plane"',
        'mesh = UsdGeom.Mesh.Define(stage, mesh_path)',
        'mesh.GetPrim().SetActive(True)',
        '',
        '# Cached HDRI array in hou.session for zero disk I/O on 60 FPS slider tweaks',
        'if not hasattr(hou.session, "_hdri_match_cache"):',
        '    hou.session._hdri_match_cache = {}',
        '',
        'hdri_arr = None',
        'if tex_file and os.path.isfile(tex_file):',
        '    try:',
        '        mtime = os.path.getmtime(tex_file)',
        '        cached = hou.session._hdri_match_cache.get(tex_file)',
        '        if cached and cached[0] == mtime:',
        '            hdri_arr = cached[1]',
        '        elif load_exr_to_numpy is not None:',
        '            hdri_arr = load_exr_to_numpy(tex_file)',
        '            hou.session._hdri_match_cache[tex_file] = (mtime, hdri_arr)',
        '    except Exception:',
        '        hdri_arr = None',
        '',
        '# Vectorized concentric circular ground disc',
        'rings = 32',
        'sectors = 64',
        'r_idx = np.arange(1, rings + 1, dtype=np.float32) / float(rings)',
        'rad_vals = r_idx * ground_radius',
        's_idx = np.arange(sectors, dtype=np.float32) / float(sectors)',
        'angles = s_idx * (2.0 * math.pi)',
        '',
        'PX = rad_vals[:, None] * np.sin(angles)[None, :]',
        'PZ = -rad_vals[:, None] * np.cos(angles)[None, :]',
        'flat_px = np.concatenate([[0.0], PX.flatten()]).astype(np.float32)',
        'flat_pz = np.concatenate([[0.0], PZ.flatten()]).astype(np.float32)',
        'flat_pts = np.stack([flat_px, np.zeros_like(flat_px), flat_pz], axis=-1)',
        'points = Vt.Vec3fArray.FromNumpy(flat_pts)',
        '',
        'if is_top_view:',
        '    U = (flat_px + ground_radius) / (2.0 * ground_radius)',
        '    V = (ground_radius - flat_pz) / (2.0 * ground_radius)',
        'else:',
        '    U_s, V_s = project_ground_coords(flat_px, np.zeros_like(flat_px), flat_pz, h=tripod_height)',
        '    U = U_s',
        '    V = 1.0 - V_s',
        'uvs = Vt.Vec2fArray.FromNumpy(np.stack([U.astype(np.float32), V.astype(np.float32)], axis=-1))',
        '',
        'f_start = (1.0 - feather) * ground_radius',
        'dist_from_center = np.sqrt(flat_px * flat_px + flat_pz * flat_pz)',
        'if feather > 1e-4:',
        '    opacities = np.clip((ground_radius - dist_from_center) / max(1e-4, ground_radius - f_start), 0.0, 1.0).astype(np.float32)',
        'else:',
        '    opacities = np.ones_like(flat_px, dtype=np.float32)',
        '',
        'if hdri_arr is not None:',
        '    img_h, img_w = hdri_arr.shape[:2]',
        '    px_x = np.clip((U * (img_w - 1)).astype(np.int32), 0, img_w - 1)',
        '    px_y = np.clip((((1.0 - V) if is_top_view else V) * (img_h - 1)).astype(np.int32), 0, img_h - 1)',
        '    sampled = np.nan_to_num(hdri_arr[px_y, px_x, :3]).astype(np.float32)',
        '    colors = Vt.Vec3fArray.FromNumpy(sampled)',
        'else:',
        '    colors = Vt.Vec3fArray.FromNumpy(np.full((len(flat_px), 3), 0.5, dtype=np.float32))',
        '',
        'face_counts = np.concatenate([np.full(sectors, 3, dtype=np.int32), np.full((rings - 1) * sectors, 4, dtype=np.int32)])',
        'f_cnts = Vt.IntArray.FromNumpy(face_counts)',
        'f_idxs = []',
        'for s in range(sectors):',
        '    s_next = (s + 1) % sectors',
        '    f_idxs.extend([0, 1 + s_next, 1 + s])',
        'for r in range(rings - 1):',
        '    r0 = 1 + r * sectors',
        '    r1 = 1 + (r + 1) * sectors',
        '    for s in range(sectors):',
        '        s_next = (s + 1) % sectors',
        '        f_idxs.extend([r0 + s, r0 + s_next, r1 + s_next, r1 + s])',
        'face_indices = Vt.IntArray.FromNumpy(np.array(f_idxs, dtype=np.int32))',
        '',
        'mesh.CreatePointsAttr(points)',
        'mesh.CreateFaceVertexCountsAttr(f_cnts)',
        'mesh.CreateFaceVertexIndicesAttr(face_indices)',
        'mesh.CreateNormalsAttr(Vt.Vec3fArray.FromNumpy(np.tile(np.array([0.0, 1.0, 0.0], dtype=np.float32), (len(flat_px), 1))))',
        'mesh.SetNormalsInterpolation(UsdGeom.Tokens.vertex)',
        'mesh.CreateDoubleSidedAttr().Set(True)',
        'mesh.CreateExtentAttr([Gf.Vec3f(-ground_radius, -0.01, -ground_radius), Gf.Vec3f(ground_radius, 0.01, ground_radius)])',
        '',
        'is_opaque = (feather <= 1e-4)',
        'mesh.GetPrim().CreateAttribute("primvars:arnold:opaque", Sdf.ValueTypeNames.Bool, False).Set(is_opaque)',
        'mesh.GetPrim().CreateAttribute("arnold:opaque", Sdf.ValueTypeNames.Bool, False).Set(is_opaque)',
        'mesh.GetPrim().CreateAttribute("primvars:redshift:object:MESHFLAG_OPAQUE", Sdf.ValueTypeNames.Bool, False).Set(is_opaque)',
        'mesh.GetPrim().CreateAttribute("redshift:object:MESHFLAG_OPAQUE", Sdf.ValueTypeNames.Bool, False).Set(is_opaque)',
        'mesh.GetPrim().CreateAttribute("primvars:karma:object:unpremult", Sdf.ValueTypeNames.Int, False).Set(1)',
        '',
        'prim_disc = mesh.GetPrim()',
        'if mat_mode_val in ("emissive", "pbr_emissive"):',
        '    prim_disc.CreateAttribute("primvars:karma:object:treat_as_lightsource", Sdf.ValueTypeNames.Int, False).Set(1)',
        '    prim_disc.CreateAttribute("primvars:karma:object:lightsource:samplingquality", Sdf.ValueTypeNames.Float, False).Set(1.0)',
        '    prim_disc.CreateAttribute("primvars:karma:object:lightsource:doublesided", Sdf.ValueTypeNames.Int, False).Set(1)',
        '    prim_disc.CreateAttribute("primvars:arnold:mesh_light", Sdf.ValueTypeNames.Bool, False).Set(True)',
        '    prim_disc.CreateAttribute("primvars:redshift:object:MESHFLAG_GICASTER", Sdf.ValueTypeNames.Bool, False).Set(True)',
        'else:',
        '    if prim_disc.HasAttribute("primvars:karma:object:treat_as_lightsource"):',
        '        prim_disc.CreateAttribute("primvars:karma:object:treat_as_lightsource", Sdf.ValueTypeNames.Int, False).Set(0)',
        '',
        'pv_api = UsdGeom.PrimvarsAPI(mesh.GetPrim())',
        'st_pv = pv_api.CreatePrimvar("st", Sdf.ValueTypeNames.TexCoord2fArray, UsdGeom.Tokens.vertex)',
        'st_pv.Set(uvs)',
        'uv_pv = pv_api.CreatePrimvar("uv", Sdf.ValueTypeNames.TexCoord2fArray, UsdGeom.Tokens.vertex)',
        'uv_pv.Set(uvs)',
        '',
        'op_pv = mesh.CreateDisplayOpacityPrimvar(UsdGeom.Tokens.vertex)',
        'op_pv.Set(Vt.FloatArray.FromNumpy(opacities))',
        'op_pv2 = pv_api.CreatePrimvar("displayOpacity", Sdf.ValueTypeNames.FloatArray, UsdGeom.Tokens.vertex)',
        'op_pv2.Set(Vt.FloatArray.FromNumpy(opacities))',
        'op_colors = Vt.Vec3fArray.FromNumpy(np.stack([opacities, opacities, opacities], axis=-1))',
        'op_col_pv = pv_api.CreatePrimvar("opacity", Sdf.ValueTypeNames.Color3fArray, UsdGeom.Tokens.vertex)',
        'op_col_pv.Set(op_colors)',
        'op_ai_pv = pv_api.CreatePrimvar("arnold:opacity", Sdf.ValueTypeNames.Color3fArray, UsdGeom.Tokens.vertex)',
        'op_ai_pv.Set(op_colors)',
        '',
        'col_pv = mesh.CreateDisplayColorPrimvar(UsdGeom.Tokens.vertex)',
        'col_pv.Set(colors)',
        '',
        'mat_path = Sdf.Path("/environment/ground_dome/ground_mat")',
        'if not stage.GetPrimAtPath(mat_path).IsValid():',
        '    op_pv_name = "displayOpacity" if (feather > 1e-4) else None',
        '    mat = create_native_material(stage, mat_path, tex_file, roughness_val, mat_mode_val, emissive_mult_val, "st", renderer_target, opacity_primvar=op_pv_name)',
        '    UsdShade.MaterialBindingAPI(mesh.GetPrim()).Bind(mat)',
    ])
    return "\n".join(lines)



def createInterface():
    """Factory function called by Houdini to create the panel widget."""
    try:
        return HdriMatchSolarisPanel()
    except Exception as e:
        import traceback
        err_box = QtWidgets.QTextEdit()
        err_box.setReadOnly(True)
        err_box.setStyleSheet(
            "background-color: #2b0000; color: #ff6666; font-family: monospace; font-size: 12px; padding: 8px;"
        )
        err_box.setText("Error creating HDRI Match Solaris panel:\n\n" + traceback.format_exc())
        return err_box


# Backward compatibility and modern branding alias
DomeBreakerPanel = HdriMatchSolarisPanel

__all__ = [
    "HdriMatchSolarisPanel",
    "DomeBreakerPanel",
    "createInterface",
]
