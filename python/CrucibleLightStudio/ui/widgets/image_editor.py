# -*- coding: utf-8 -*-
"""
Crucible Light Studio - Image Editor
======================================
Dialog for manipulating Gobos and Scrims (Blur, Contrast, Gamma).
"""

try:
    from PySide2 import QtWidgets, QtCore, QtGui
except ImportError:
    from PySide6 import QtWidgets, QtCore, QtGui

import os
import math

class ImageEditorDialog(QtWidgets.QDialog):
    def __init__(self, filepath, parent=None):
        super().__init__(parent)
        self.original_path = filepath
        self.setWindowTitle(f"Image Editor - {os.path.basename(filepath)}")
        self.resize(600, 700)
        
        self.original_image = QtGui.QImage(filepath)
        if self.original_image.isNull():
            # If it's EXR/HDR, try to load the proxy
            proxy = filepath + ".thumb.jpg"
            if os.path.exists(proxy):
                self.original_image = QtGui.QImage(proxy)
                
        # Ensure it's in a format we can easily manipulate (32-bit ARGB)
        self.original_image = self.original_image.convertToFormat(QtGui.QImage.Format_ARGB32)
        self.processed_image = QtGui.QImage(self.original_image)
        
        self._build_ui()
        self._update_preview()
        
    def _build_ui(self):
        layout = QtWidgets.QVBoxLayout(self)
        
        # Image Preview
        self.view = QtWidgets.QGraphicsView()
        self.scene = QtWidgets.QGraphicsScene()
        self.view.setScene(self.scene)
        self.pixmap_item = QtWidgets.QGraphicsPixmapItem()
        self.scene.addItem(self.pixmap_item)
        layout.addWidget(self.view)
        
        # Controls Layout
        controls_layout = QtWidgets.QGridLayout()
        
        # Blur
        self.sld_blur = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.sld_blur.setRange(0, 50)
        self.sld_blur.setValue(0)
        self.sld_blur.valueChanged.connect(self._apply_filters)
        controls_layout.addWidget(QtWidgets.QLabel("Blur:"), 0, 0)
        controls_layout.addWidget(self.sld_blur, 0, 1)
        
        # Contrast
        self.sld_contrast = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.sld_contrast.setRange(-100, 100)
        self.sld_contrast.setValue(0)
        self.sld_contrast.valueChanged.connect(self._apply_filters)
        controls_layout.addWidget(QtWidgets.QLabel("Contrast:"), 1, 0)
        controls_layout.addWidget(self.sld_contrast, 1, 1)
        
        # Gamma
        self.sld_gamma = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.sld_gamma.setRange(10, 300) # 0.1 to 3.0
        self.sld_gamma.setValue(100) # 1.0
        self.sld_gamma.valueChanged.connect(self._apply_filters)
        controls_layout.addWidget(QtWidgets.QLabel("Gamma:"), 2, 0)
        controls_layout.addWidget(self.sld_gamma, 2, 1)
        
        layout.addLayout(controls_layout)
        
        # Buttons
        btn_layout = QtWidgets.QHBoxLayout()
        self.btn_save = QtWidgets.QPushButton("Apply to Selected Light")
        self.btn_save.setMinimumHeight(30)
        self.btn_save.setObjectName("primary")
        self.btn_save.clicked.connect(self._save_and_apply)
        self.btn_cancel = QtWidgets.QPushButton("Cancel")
        self.btn_cancel.clicked.connect(self.reject)
        
        btn_layout.addStretch()
        btn_layout.addWidget(self.btn_cancel)
        btn_layout.addWidget(self.btn_save)
        layout.addLayout(btn_layout)
        
    def _apply_filters(self):
        blur = self.sld_blur.value()
        contrast = self.sld_contrast.value()
        gamma = self.sld_gamma.value() / 100.0
        
        # We manipulate a copy
        img = QtGui.QImage(self.original_image)
        
        # Simple contrast and gamma per pixel using QImage bits
        if contrast != 0 or gamma != 1.0:
            c = (100.0 + contrast) / 100.0
            c = c * c # curve
            
            ptr = img.bits()
            # If PySide2/PySide6 compatibility issues with bits(), we use QPainter for gamma/contrast?
            # QImage pixel manipulation can be slow in Python.
            # Instead, we can use a lookup table (LUT) to make it instantaneous.
            lut = []
            for i in range(256):
                # Normalize
                v = i / 255.0
                
                # Contrast
                v = ((v - 0.5) * c) + 0.5
                v = max(0.0, min(1.0, v))
                
                # Gamma
                if v > 0:
                    v = math.pow(v, 1.0 / gamma)
                
                lut.append(int(v * 255.0))
                
            # Apply LUT using safe python array
            import array
            try:
                # PySide6 memoryview
                arr = memoryview(img.bits())
                for i in range(0, len(arr), 4):
                    arr[i] = lut[arr[i]]       # B
                    arr[i+1] = lut[arr[i+1]]   # G
                    arr[i+2] = lut[arr[i+2]]   # R
            except:
                pass # If bits() is not writable or fails, skip color adjust for preview to prevent crash
        
        self.processed_image = img
        self._update_preview(blur_radius=blur)
        
    def _update_preview(self, blur_radius=0):
        pixmap = QtGui.QPixmap.fromImage(self.processed_image)
        self.pixmap_item.setPixmap(pixmap)
        
        # Apply blur via QGraphicsBlurEffect for super-fast preview
        if blur_radius > 0:
            blur_fx = QtWidgets.QGraphicsBlurEffect()
            blur_fx.setBlurRadius(blur_radius)
            self.pixmap_item.setGraphicsEffect(blur_fx)
        else:
            self.pixmap_item.setGraphicsEffect(None)
            
        self.scene.setSceneRect(self.pixmap_item.boundingRect())
        self.view.fitInView(self.scene.sceneRect(), QtCore.Qt.KeepAspectRatio)

    def _save_and_apply(self):
        blur = self.sld_blur.value()
        contrast = self.sld_contrast.value()
        gamma = self.sld_gamma.value()
        
        # If no edits were made, just return the original
        if blur == 0 and contrast == 0 and gamma == 100:
            self.new_path = self.original_path
            self.accept()
            return
            
        base, ext = os.path.splitext(self.original_path)
        ext_lower = ext.lower()
        
        # Preserve 32-bit HDR data using OpenImageIO for EXR/HDR
        if ext_lower in [".exr", ".hdr"]:
            new_path = f"{base}_edited{ext}"
            try:
                import subprocess
                import os
                
                hoiiotool_cmd = "hoiiotool"
                if "HFS" in os.environ:
                    hoiiotool_cmd = os.path.join(os.environ["HFS"], "bin", "hoiiotool")
                    if os.name == "nt":
                        hoiiotool_cmd += ".exe"
                        
                cmd = [hoiiotool_cmd, self.original_path]
                
                if blur > 0:
                    cmd.extend(["--blur", f"{blur}x{blur}"])
                    
                if contrast != 0 or gamma != 100:
                    # Nuke-style contrast: (val / 0.18) ** contrast * 0.18
                    c_val = 1.0 + (contrast / 100.0)
                    g_val = gamma / 100.0
                    
                    if c_val != 1.0:
                        cmd.extend(["--mulc", "5.55555"])
                        cmd.extend(["--powc", f"{c_val},{c_val},{c_val},1.0"])
                        cmd.extend(["--mulc", "0.18"])
                        
                    if g_val != 1.0 and g_val > 0.01:
                        p_val = 1.0 / g_val
                        cmd.extend(["--powc", f"{p_val},{p_val},{p_val},1.0"])
                        
                    # Fix any NaNs resulting from power functions on negatives
                    cmd.extend(["--fixnan", "black"])
                    
                cmd.extend(["-o", new_path])
                ret = subprocess.run(cmd, creationflags=subprocess.CREATE_NO_WINDOW, check=False).returncode
                if ret == 0:
                    self.new_path = new_path
                    self.accept()
                    return
            except Exception as e:
                import logging
                logging.getLogger("CrucibleLightStudio").warning(f"OIIO failed: {e}. Falling back to 8-bit QImage.")

        # Fallback for LDR images or if OIIO fails
        final_img = self.processed_image
        
        if blur > 0:
            # We must render the scene to get the blurred image
            final_img = QtGui.QImage(self.processed_image.size(), QtGui.QImage.Format_ARGB32)
            final_img.fill(QtCore.Qt.transparent)
            painter = QtGui.QPainter(final_img)
            self.scene.render(painter)
            painter.end()
            
        # Save next to original
        new_path = f"{base}_edited.png"
        final_img.save(new_path)
        self.new_path = new_path
        self.accept()

    def get_edited_path(self):
        return self.new_path
