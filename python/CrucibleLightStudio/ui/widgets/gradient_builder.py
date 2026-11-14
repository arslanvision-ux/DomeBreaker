import ctypes
import math
import os
import random
import sys
try:
    from PySide6 import QtWidgets, QtCore, QtGui
except ImportError:
    from PySide2 import QtWidgets, QtCore, QtGui
    
from CrucibleLightStudio.core import preset_manager

class GradientBuilderDialog(QtWidgets.QDialog):
    texture_generated = QtCore.Signal(str)
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Procedural Gradient Builder")
        self.setMinimumSize(500, 400)
        
        layout = QtWidgets.QVBoxLayout(self)
        
        # Preview
        self.preview_lbl = QtWidgets.QLabel()
        self.preview_lbl.setFixedSize(256, 256)
        self.preview_lbl.setStyleSheet("background-color: black; border: 1px solid #444;")
        self.preview_lbl.setAlignment(QtCore.Qt.AlignCenter)
        
        preview_layout = QtWidgets.QHBoxLayout()
        preview_layout.addStretch()
        preview_layout.addWidget(self.preview_lbl)
        preview_layout.addStretch()
        layout.addLayout(preview_layout)
        
        # Controls
        form = QtWidgets.QFormLayout()
        
        self.library_combo = QtWidgets.QComboBox()
        self.library_combo.addItem("-- New Custom Gradient --")
        self._db_presets = preset_manager.get_gradient_presets()
        for p in sorted(self._db_presets.keys()):
            self.library_combo.addItem(p)
        self.library_combo.currentIndexChanged.connect(self._on_library_selected)
        form.addRow("Library:", self.library_combo)
        
        self.name_edit = QtWidgets.QLineEdit()
        self.name_edit.setPlaceholderText("custom_gradient")
        form.addRow("Preset Name:", self.name_edit)
        
        self.type_combo = QtWidgets.QComboBox()
        self.type_combo.addItems([
            "Radial Softbox", "Linear Ramp", "Bilateral Softbox", "Box Softbox",
            "Ring Light", "Tube Light", "Window Blinds", "Octabox",
            "Tracing Paper", "Half Moon", "Grid", "Spotlight", "Multi-Tube",
            "Polka Dots", "Bricks", "Checkers",
            "Clouds", "Leaves / Foliage", "Tree Branches", "Curved Ribbon",
            "Noise"
        ])
        form.addRow("Type:", self.type_combo)
        
        self.lbl_noise = QtWidgets.QLabel("Noise Type:")
        self.noise_combo = QtWidgets.QComboBox()
        self.noise_combo.addItems(["White Noise", "Cellular", "Fractal / Cloudy", "Caustics"])
        form.addRow(self.lbl_noise, self.noise_combo)
        
        self.lbl_softness = QtWidgets.QLabel("Edge Softness:")
        self.softness_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.softness_slider.setRange(1, 100)
        self.softness_slider.setValue(50)
        form.addRow(self.lbl_softness, self.softness_slider)
        
        self.lbl_bias = QtWidgets.QLabel("Center Bias:")
        self.bias_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.bias_slider.setRange(1, 100)
        self.bias_slider.setValue(50)
        form.addRow(self.lbl_bias, self.bias_slider)
        
        self.lbl_width = QtWidgets.QLabel("Width / Aspect:")
        self.width_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.width_slider.setRange(1, 100)
        self.width_slider.setValue(50)
        form.addRow(self.lbl_width, self.width_slider)
        
        self.lbl_count = QtWidgets.QLabel("Count / Repeat:")
        self.count_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.count_slider.setRange(1, 100)
        self.count_slider.setValue(50)
        form.addRow(self.lbl_count, self.count_slider)
        
        layout.addLayout(form)
        
        # Buttons
        btns = QtWidgets.QHBoxLayout()
        self.btn_apply = QtWidgets.QPushButton("Save && Apply")
        self.btn_apply.setObjectName("accent_btn")
        self.btn_apply.setDefault(True)
        
        self.btn_cancel = QtWidgets.QPushButton("Cancel")
        btns.addStretch()
        btns.addWidget(self.btn_cancel)
        btns.addWidget(self.btn_apply)
        layout.addLayout(btns)
        
        # Connections
        self.name_edit.returnPressed.connect(self._on_save)
        self.type_combo.currentIndexChanged.connect(self.update_preview)
        self.noise_combo.currentIndexChanged.connect(self.update_preview)
        self.softness_slider.valueChanged.connect(self.update_preview)
        self.bias_slider.valueChanged.connect(self.update_preview)
        self.width_slider.valueChanged.connect(self.update_preview)
        self.count_slider.valueChanged.connect(self.update_preview)
        self.btn_cancel.clicked.connect(self.reject)
        self.btn_apply.clicked.connect(self._on_save)
        
        self.current_img_path = ""
        self._current_img = None
        self.update_preview()
        
    def update_preview(self):
        t = self.type_combo.currentIndex()
        
        # Visibility controls
        def show_softness(show):
            self.lbl_softness.setVisible(show)
            self.softness_slider.setVisible(show)
            
        def show_bias(show):
            self.lbl_bias.setVisible(show)
            self.bias_slider.setVisible(show)
            
        def show_width(show):
            self.lbl_width.setVisible(show)
            self.width_slider.setVisible(show)
            
        def show_count(show):
            self.lbl_count.setVisible(show)
            self.count_slider.setVisible(show)
            
        def show_noise_type(show):
            self.lbl_noise.setVisible(show)
            self.noise_combo.setVisible(show)
            
        # Defaults
        show_softness(True)
        show_bias(True)
        show_width(True)
        show_count(True)
        show_noise_type(False)
        
        if t == 0: # Radial
            show_width(False); show_count(False)
        elif t == 1: # Linear
            show_softness(False); show_width(False); show_count(False)
        elif t == 2: # Bilateral
            show_width(False); show_count(False)
        elif t == 3: # Box Softbox
            show_count(False)
        elif t == 4: # Ring Light
            show_count(False)
        elif t == 5: # Tube Light
            show_count(False)
        elif t == 7: # Octabox
            show_count(False)
        elif t == 10: # Grid
            show_softness(False)
        elif t == 11: # Spotlight
            show_count(False)
        elif t == 15: # Checkers
            show_softness(False)
        elif t in [16, 17, 18, 19]: # Clouds, Leaves, Branches, Curved Ribbon
            pass # Show all
        elif t == 20: # Noise
            show_noise_type(True)
            
        # Generate procedural texture using QImage
        w, h = 512, 512
        img = QtGui.QImage(w, h, QtGui.QImage.Format_ARGB32)
        img.fill(QtCore.Qt.black)
        
        painter = QtGui.QPainter(img)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        
        t = self.type_combo.currentIndex()
        softness = self.softness_slider.value() / 100.0
        bias = self.bias_slider.value() / 100.0
        aspect = self.width_slider.value() / 100.0
        count_val = self.count_slider.value() / 100.0
        
        painter.fillRect(0, 0, w, h, QtCore.Qt.black)
        
        if t == 0: # Radial
            grad = QtGui.QRadialGradient(w/2, h/2, w/2)
            grad.setColorAt(0.0, QtGui.QColor(255, 255, 255, 255))
            grad.setColorAt(bias, QtGui.QColor(255, 255, 255, int(255 * (1.0 - softness))))
            grad.setColorAt(1.0, QtGui.QColor(0, 0, 0, 255))
            painter.fillRect(0, 0, w, h, grad)
            
        elif t == 1: # Linear
            grad = QtGui.QLinearGradient(0, 0, w, 0)
            grad.setColorAt(0.0, QtGui.QColor(255, 255, 255, 255))
            grad.setColorAt(bias, QtGui.QColor(128, 128, 128, 255))
            grad.setColorAt(1.0, QtGui.QColor(0, 0, 0, 255))
            painter.fillRect(0, 0, w, h, grad)
            
        elif t == 2: # Bilateral
            grad = QtGui.QLinearGradient(0, 0, w, 0)
            grad.setColorAt(0.0, QtGui.QColor(0, 0, 0, 255))
            grad.setColorAt(0.5 - (bias*0.4), QtGui.QColor(255, 255, 255, int(255 * softness)))
            grad.setColorAt(0.5, QtGui.QColor(255, 255, 255, 255))
            grad.setColorAt(0.5 + (bias*0.4), QtGui.QColor(255, 255, 255, int(255 * softness)))
            grad.setColorAt(1.0, QtGui.QColor(0, 0, 0, 255))
            painter.fillRect(0, 0, w, h, grad)
            
        elif t == 3: # Box Softbox
            steps = 60
            for i in range(steps):
                t_val = i / float(steps)
                painter.setPen(QtCore.Qt.NoPen)
                painter.setBrush(QtGui.QColor(255, 255, 255, int(255/steps * 2.5)))
                
                aw = aspect * 2.0
                ah = (1.0 - aspect) * 2.0
                core_w = w * bias * aw
                core_h = h * bias * ah
                
                curr_w = core_w + (w * softness * (1.0 - t_val))
                curr_h = core_h + (h * softness * (1.0 - t_val))
                
                rad = 5 + (100 * softness * (1.0 - t_val))
                painter.drawRoundedRect(QtCore.QRectF((w - curr_w)/2, (h - curr_h)/2, curr_w, curr_h), rad, rad)
                
        elif t == 4: # Ring Light
            steps = 50
            for i in range(steps):
                t_val = i / float(steps)
                aw = aspect * 2.0
                ah = (1.0 - aspect) * 2.0
                
                outer_rad = (w/2) * (0.4 + bias * 0.45)
                inner_rad = (w/2) * (0.2 + bias * 0.3)
                if inner_rad >= outer_rad: inner_rad = outer_rad - 10
                
                blur_amt = w * softness * 0.25 * (1.0 - t_val)
                
                pen = QtGui.QPen(QtGui.QColor(255, 255, 255, int(255/steps * 3.5)))
                pen.setWidthF((outer_rad - inner_rad) + blur_amt)
                pen.setCapStyle(QtCore.Qt.RoundCap)
                painter.setPen(pen)
                painter.setBrush(QtCore.Qt.NoBrush)
                mid_rad = (outer_rad + inner_rad) / 2.0
                painter.drawEllipse(QtCore.QPointF(w/2, h/2), mid_rad * aw, mid_rad * ah)

        elif t == 5: # Tube Light
            steps = 50
            for i in range(steps):
                t_val = i / float(steps)
                painter.setPen(QtCore.Qt.NoPen)
                painter.setBrush(QtGui.QColor(255, 255, 255, int(255/steps * 3.5)))
                
                length = w * (0.4 + bias * 0.5)
                thickness = h * 0.03 * (aspect * 2.0)
                
                curr_l = length + (w * softness * 0.3 * (1.0 - t_val))
                curr_t = thickness + (h * softness * 0.3 * (1.0 - t_val))
                
                painter.drawRoundedRect(QtCore.QRectF((w - curr_l)/2, (h - curr_t)/2, curr_l, curr_t), curr_t/2, curr_t/2)

        elif t == 6: # Window Blinds
            num_blinds = max(2, int(count_val * 40))
            blind_h = h / num_blinds
            gap = blind_h * aspect * 0.95
            
            for i in range(num_blinds):
                y = i * blind_h
                grad = QtGui.QLinearGradient(0, y, 0, y + blind_h - gap)
                grad.setColorAt(0.0, QtGui.QColor(255, 255, 255, int(255 * bias)))
                grad.setColorAt(0.5, QtGui.QColor(255, 255, 255, 255))
                grad.setColorAt(1.0, QtGui.QColor(0, 0, 0, int(255 * (1.0 - softness))))
                painter.fillRect(QtCore.QRectF(0, y, w, max(1, blind_h - gap)), grad)
                
        elif t == 7: # Octabox
            steps = 40
            for i in range(steps):
                t_val = i / float(steps)
                painter.setPen(QtCore.Qt.NoPen)
                painter.setBrush(QtGui.QColor(255, 255, 255, int(255/steps * 3.5)))
                
                rad = (w/2) * (0.2 + bias * 0.7) + (w * softness * 0.25 * (1.0 - t_val))
                
                polygon = QtGui.QPolygonF()
                for j in range(8):
                    angle = j * (math.pi / 4.0) + (math.pi / 8.0)
                    px = w/2 + (rad * math.cos(angle)) * (aspect * 2.0)
                    py = h/2 + (rad * math.sin(angle)) * ((1.0 - aspect) * 2.0)
                    polygon.append(QtCore.QPointF(px, py))
                painter.drawPolygon(polygon)
                
        elif t == 8: # Tracing Paper (Noise)
            random.seed(int(count_val * 100))
            steps = int(40 + aspect * 100)
            for i in range(steps):
                px = random.uniform(0, w)
                py = random.uniform(0, h)
                rad = random.uniform(10, 50 + softness * 100)
                alpha = int(random.uniform(10, 50) * bias)
                grad = QtGui.QRadialGradient(px, py, rad)
                grad.setColorAt(0, QtGui.QColor(255, 255, 255, alpha))
                grad.setColorAt(1, QtGui.QColor(255, 255, 255, 0))
                painter.fillRect(0, 0, w, h, grad)
                
        elif t == 9: # Half Moon
            steps = 40
            for i in range(steps):
                t_val = i / float(steps)
                painter.setPen(QtCore.Qt.NoPen)
                painter.setBrush(QtGui.QColor(255, 255, 255, int(255/steps * 3.5)))
                
                rad = (w/2) * bias + (w * softness * (1.0 - t_val))
                offset = (aspect - 0.5) * w
                
                painter.drawEllipse(QtCore.QPointF(w/2 + offset, h/2), rad, rad)
                
            # Cutout
            painter.setCompositionMode(QtGui.QPainter.CompositionMode_DestinationOut)
            painter.setBrush(QtCore.Qt.black)
            cut_offset = (aspect - 0.5) * w + (w * 0.2 * count_val)
            painter.drawEllipse(QtCore.QPointF(w/2 + cut_offset, h/2), rad * 0.9, rad * 0.9)
            painter.setCompositionMode(QtGui.QPainter.CompositionMode_SourceOver)
            
        elif t == 10: # Grid
            num_lines = max(2, int(count_val * 30))
            spacing = w / num_lines
            line_w = spacing * aspect * 0.9
            
            painter.setPen(QtCore.Qt.NoPen)
            for i in range(num_lines + 1):
                pos = i * spacing
                # Verticals
                grad = QtGui.QLinearGradient(pos, 0, pos + line_w, 0)
                grad.setColorAt(0.0, QtGui.QColor(255, 255, 255, 0))
                grad.setColorAt(0.5, QtGui.QColor(255, 255, 255, int(255 * bias)))
                grad.setColorAt(1.0, QtGui.QColor(255, 255, 255, 0))
                painter.fillRect(QtCore.QRectF(pos, 0, max(1, line_w), h), grad)
                # Horizontals
                grad2 = QtGui.QLinearGradient(0, pos, 0, pos + line_w)
                grad2.setColorAt(0.0, QtGui.QColor(255, 255, 255, 0))
                grad2.setColorAt(0.5, QtGui.QColor(255, 255, 255, int(255 * bias)))
                grad2.setColorAt(1.0, QtGui.QColor(255, 255, 255, 0))
                painter.fillRect(QtCore.QRectF(0, pos, w, max(1, line_w)), grad2)
                
        elif t == 11: # Spotlight
            grad = QtGui.QRadialGradient(w/2, h/2, w/2 * aspect)
            grad.setColorAt(0.0, QtGui.QColor(255, 255, 255, 255))
            grad.setColorAt(1.0 - softness, QtGui.QColor(255, 255, 255, int(255 * bias)))
            grad.setColorAt(1.0, QtGui.QColor(0, 0, 0, 255))
            painter.fillRect(0, 0, w, h, grad)
            
        elif t == 12: # Multi-Tube
            num_tubes = max(1, int(count_val * 10))
            spacing = w / (num_tubes + 1)
            
            steps = 20
            for j in range(num_tubes):
                tx = spacing * (j + 1)
                for i in range(steps):
                    t_val = i / float(steps)
                    painter.setPen(QtCore.Qt.NoPen)
                    painter.setBrush(QtGui.QColor(255, 255, 255, int(255/steps * 3.5)))
                    
                    length = h * (0.4 + bias * 0.5)
                    thickness = w * 0.02 * (aspect * 2.0)
                    
                    curr_l = length + (h * softness * 0.3 * (1.0 - t_val))
                    curr_t = thickness + (w * softness * 0.3 * (1.0 - t_val))
                    
                    painter.drawRoundedRect(QtCore.QRectF(tx - curr_t/2, (h - curr_l)/2, curr_t, curr_l), curr_t/2, curr_t/2)
                    
        elif t == 13: # Polka Dots
            num_dots = max(2, int(count_val * 20))
            spacing = w / num_dots
            rad_base = spacing * aspect * 0.5
            
            steps = 20
            for i in range(num_dots):
                for j in range(num_dots):
                    cx = i * spacing + spacing/2
                    cy = j * spacing + spacing/2
                    
                    # Offset every other row
                    if j % 2 == 1:
                        cx += spacing/2 * bias
                        
                    for s in range(steps):
                        t_val = s / float(steps)
                        rad = rad_base + (spacing * softness * (1.0 - t_val))
                        painter.setPen(QtCore.Qt.NoPen)
                        painter.setBrush(QtGui.QColor(255, 255, 255, int(255/steps * 3.5)))
                        painter.drawEllipse(QtCore.QPointF(cx, cy), rad, rad)
                        
        elif t == 14: # Bricks
            num_rows = max(2, int(count_val * 20))
            row_h = h / num_rows
            brick_w = row_h * (aspect * 4.0)
            
            steps = 10
            for r in range(num_rows):
                cy = r * row_h
                offset = (brick_w * bias) if r % 2 == 1 else 0
                
                num_cols = int(w / brick_w) + 2
                for c in range(num_cols):
                    cx = c * brick_w - offset
                    
                    for s in range(steps):
                        t_val = s / float(steps)
                        curr_w = brick_w * 0.95 + (brick_w * softness * 0.5 * (1.0 - t_val))
                        curr_h = row_h * 0.95 + (row_h * softness * 0.5 * (1.0 - t_val))
                        
                        painter.setPen(QtCore.Qt.NoPen)
                        painter.setBrush(QtGui.QColor(255, 255, 255, int(255/steps * 3.5)))
                        rad = 2 + (row_h * softness * 0.5 * (1.0 - t_val))
                        painter.drawRoundedRect(QtCore.QRectF(cx + (brick_w - curr_w)/2, cy + (row_h - curr_h)/2, curr_w, curr_h), rad, rad)
                        
        elif t == 15: # Checkers
            num_squares = max(2, int(count_val * 20))
            sq_w = w / num_squares
            sq_h = sq_w * (aspect * 2.0)
            
            painter.setPen(QtCore.Qt.NoPen)
            painter.setBrush(QtGui.QColor(255, 255, 255, int(255 * bias)))
            
            for r in range(int(h / sq_h) + 1):
                for c in range(num_squares + 1):
                    if (r + c) % 2 == 0:
                        painter.drawRect(QtCore.QRectF(c * sq_w, r * sq_h, sq_w, sq_h))
                        
        elif t == 16: # Clouds
            random.seed(int(count_val * 100))
            num_clouds = max(5, int(count_val * 100))
            
            for i in range(num_clouds):
                cx = random.uniform(0, w)
                cy = random.uniform(0, h)
                rad_base = random.uniform(20, 80 + aspect * 100)
                
                steps = 15
                for s in range(steps):
                    t_val = s / float(steps)
                    rad = rad_base + (rad_base * softness * (1.0 - t_val))
                    painter.setPen(QtCore.Qt.NoPen)
                    alpha = int(random.uniform(5, 20) * bias)
                    painter.setBrush(QtGui.QColor(255, 255, 255, int((alpha / steps) * 4.0)))
                    painter.drawEllipse(QtCore.QPointF(cx, cy), rad * (aspect * 2.0), rad * ((1.0 - aspect) * 2.0))
                    
        elif t == 17: # Leaves / Foliage
            random.seed(int(count_val * 100))
            num_leaves = max(10, int(count_val * 150))
            
            for i in range(num_leaves):
                cx = random.uniform(-w*0.1, w*1.1)
                cy = random.uniform(-h*0.1, h*1.1)
                angle = random.uniform(0, 360)
                size = random.uniform(10, 40 + aspect * 50)
                
                painter.save()
                painter.translate(cx, cy)
                painter.rotate(angle)
                
                steps = 10
                for s in range(steps):
                    t_val = s / float(steps)
                    rad_x = size + (size * softness * (1.0 - t_val))
                    rad_y = size * 0.4 + (size * 0.4 * softness * (1.0 - t_val))
                    
                    painter.setPen(QtCore.Qt.NoPen)
                    painter.setBrush(QtGui.QColor(255, 255, 255, int(255/steps * 3.5 * bias)))
                    painter.drawEllipse(QtCore.QPointF(0, 0), rad_x, rad_y)
                painter.restore()
                
        elif t == 18: # Tree Branches
            random.seed(int(count_val * 100))
            num_trees = max(1, int(count_val * 8))
            
            def draw_branch(p, start_x, start_y, angle, length, depth):
                if depth == 0: return
                end_x = start_x + length * math.cos(math.radians(angle))
                end_y = start_y + length * math.sin(math.radians(angle))
                
                steps = 5
                for s in range(steps):
                    t_val = s / float(steps)
                    blur = w * softness * 0.05 * (1.0 - t_val)
                    pen = QtGui.QPen(QtGui.QColor(255, 255, 255, int(255/steps * 3.5 * bias)))
                    pen.setWidthF(depth * (aspect * 3.0) + blur)
                    pen.setCapStyle(QtCore.Qt.RoundCap)
                    p.setPen(pen)
                    p.drawLine(QtCore.QPointF(start_x, start_y), QtCore.QPointF(end_x, end_y))
                    
                # Recursive branches
                num_branches = random.randint(1, 3)
                for _ in range(num_branches):
                    new_angle = angle + random.uniform(-45, 45)
                    new_length = length * random.uniform(0.6, 0.9)
                    draw_branch(p, end_x, end_y, new_angle, new_length, depth - 1)
                    
            for i in range(num_trees):
                start_x = random.uniform(0, w)
                draw_branch(painter, start_x, h + 10, -90 + random.uniform(-20, 20), random.uniform(50, 150), int(4 + aspect * 4))
                
        elif t == 19: # Curved Ribbon
            # A smooth bezier curve mimicking HDR Light Studio's curved lights
            path = QtGui.QPainterPath()
            
            # Count controls the curvature/bend
            bend = (count_val - 0.5) * 2.0  # -1.0 to 1.0
            
            # Aspect controls length
            length = w * aspect
            start_x = (w - length) / 2
            end_x = start_x + length
            
            # Center Y
            cy = h / 2
            
            # Control points for the bezier bend
            cp1_x = start_x + length * 0.33
            cp1_y = cy - (bend * h * 0.4)
            cp2_x = start_x + length * 0.66
            cp2_y = cy - (bend * h * 0.4)
            
            path.moveTo(start_x, cy)
            path.cubicTo(cp1_x, cp1_y, cp2_x, cp2_y, end_x, cy)
            
            steps = 40
            for s in range(steps):
                t_val = s / float(steps)
                
                blur_amt = (w * softness * 0.3) * (1.0 - t_val)
                thickness = max(2, (w * 0.05) + blur_amt)
                
                pen = QtGui.QPen(QtGui.QColor(255, 255, 255, int(255/steps * 4.0 * bias)))
                pen.setWidthF(thickness)
                pen.setCapStyle(QtCore.Qt.RoundCap)
                painter.setPen(pen)
                painter.setBrush(QtCore.Qt.NoBrush)
                
                painter.drawPath(path)
                
        elif t == 20: # Noise
            noise_idx = self.noise_combo.currentIndex()
            random.seed(int(count_val * 100))
            
            if noise_idx == 0: # White Noise
                # Fast byte array random noise
                # We do a quick noise on a smaller image and scale it up to avoid Python UI locks
                nw, nh = max(4, int(w * aspect)), max(4, int(h * aspect))
                n_img = QtGui.QImage(nw, nh, QtGui.QImage.Format_Grayscale8)
                b = bytearray(os.urandom(nw * nh))
                
                # Apply bias mapping manually to bytes
                b_mult = bias * 2.0
                for i in range(len(b)):
                    val = int(b[i] * b_mult)
                    b[i] = min(255, max(0, val))
                    
                ptr = ctypes.c_char.from_buffer(b)
                n_img = QtGui.QImage(ptr, nw, nh, nw, QtGui.QImage.Format_Grayscale8).copy()
                
                # Convert back to ARGB32
                n_img = n_img.convertToFormat(QtGui.QImage.Format_ARGB32)
                
                # Draw scaled
                if softness > 0.05:
                    painter.setRenderHint(QtGui.QPainter.SmoothPixmapTransform)
                painter.drawImage(QtCore.QRect(0, 0, w, h), n_img)
                
            elif noise_idx == 1: # Cellular
                num_cells = max(10, int(aspect * 200))
                painter.setCompositionMode(QtGui.QPainter.CompositionMode_Plus)
                
                for i in range(num_cells):
                    cx = random.uniform(-w*0.1, w*1.1)
                    cy = random.uniform(-h*0.1, h*1.1)
                    rad = random.uniform(w*0.1, w*0.4) * (softness + 0.1)
                    
                    grad = QtGui.QRadialGradient(cx, cy, rad)
                    alpha = int(random.uniform(50, 150) * bias)
                    grad.setColorAt(0.0, QtGui.QColor(255, 255, 255, alpha))
                    grad.setColorAt(1.0, QtGui.QColor(0, 0, 0, 0))
                    
                    painter.setPen(QtCore.Qt.NoPen)
                    painter.setBrush(grad)
                    painter.drawEllipse(QtCore.QPointF(cx, cy), rad, rad)
                    
            elif noise_idx == 2: # Fractal / Cloudy
                painter.setCompositionMode(QtGui.QPainter.CompositionMode_Plus)
                # Draw layers of noise circles at different frequencies
                octaves = 3
                for oct in range(octaves):
                    freq = (oct + 1) * 2
                    num_spots = int(freq * freq * aspect * 20)
                    
                    for i in range(num_spots):
                        cx = random.uniform(0, w)
                        cy = random.uniform(0, h)
                        rad = (w / freq) * (softness + 0.2) * 1.5
                        
                        grad = QtGui.QRadialGradient(cx, cy, rad)
                        alpha = int((20 / freq) * bias)
                        grad.setColorAt(0.0, QtGui.QColor(255, 255, 255, alpha))
                        grad.setColorAt(1.0, QtGui.QColor(0, 0, 0, 0))
                        
                        painter.setPen(QtCore.Qt.NoPen)
                        painter.setBrush(grad)
                        painter.drawEllipse(QtCore.QPointF(cx, cy), rad, rad)
                        
            elif noise_idx == 3: # Caustics
                num_pts = max(10, int(aspect * 150))
                painter.setCompositionMode(QtGui.QPainter.CompositionMode_Plus)
                
                # Generate random points
                pts = []
                for i in range(num_pts):
                    pts.append((random.uniform(-w*0.1, w*1.1), random.uniform(-h*0.1, h*1.1)))
                    
                # Draw connections to nearest neighbors (simulates voronoi web)
                for i, p1 in enumerate(pts):
                    # Find 2 nearest neighbors
                    dists = []
                    for j, p2 in enumerate(pts):
                        if i == j: continue
                        dist = math.hypot(p1[0]-p2[0], p1[1]-p2[1])
                        dists.append((dist, p2))
                    
                    dists.sort(key=lambda x: x[0])
                    nearest = dists[:2]
                    
                    for dist, p2 in nearest:
                        # Draw soft glowing lines
                        steps = 8
                        for s in range(steps):
                            t_val = s / float(steps)
                            alpha = int((30 / steps) * bias)
                            pen = QtGui.QPen(QtGui.QColor(255, 255, 255, alpha))
                            # Thickness expands based on softness
                            thickness = max(1.0, (w * 0.05 * softness) * (1.0 - t_val) + 1.0)
                            pen.setWidthF(thickness)
                            pen.setCapStyle(QtCore.Qt.RoundCap)
                            painter.setPen(pen)
                            painter.drawLine(QtCore.QPointF(p1[0], p1[1]), QtCore.QPointF(p2[0], p2[1]))

        painter.end()
        
        self._current_img = img
        
        # Update UI
        pixmap = QtGui.QPixmap.fromImage(img).scaled(256, 256, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation)
        self.preview_lbl.setPixmap(pixmap)
        
    def _on_library_selected(self, index):
        if index == 0:
            return
        name = self.library_combo.currentText()
        if name in self._db_presets:
            cfg = self._db_presets[name]
            self.name_edit.setText(name)
            
            self.type_combo.blockSignals(True)
            self.noise_combo.blockSignals(True)
            self.softness_slider.blockSignals(True)
            self.bias_slider.blockSignals(True)
            self.width_slider.blockSignals(True)
            self.count_slider.blockSignals(True)
            
            self.type_combo.setCurrentIndex(cfg.get("type", 0))
            self.noise_combo.setCurrentIndex(cfg.get("noise_type", 0))
            self.softness_slider.setValue(cfg.get("softness", 50))
            self.bias_slider.setValue(cfg.get("bias", 50))
            self.width_slider.setValue(cfg.get("width", 50))
            self.count_slider.setValue(cfg.get("count", 50))
            
            self.type_combo.blockSignals(False)
            self.noise_combo.blockSignals(False)
            self.softness_slider.blockSignals(False)
            self.bias_slider.blockSignals(False)
            self.width_slider.blockSignals(False)
            self.count_slider.blockSignals(False)
            
            self.update_preview()

    def _on_save(self):
        name = self.name_edit.text().strip()
        if not name:
            name = "custom_gradient"
        name = name.replace(" ", "_").lower()
        if name.endswith(".png"):
            preset_name = name[:-4]
        else:
            preset_name = name
            name += ".png"
            
        # Save config to database
        cfg = {
            "type": self.type_combo.currentIndex(),
            "noise_type": self.noise_combo.currentIndex(),
            "softness": self.softness_slider.value(),
            "bias": self.bias_slider.value(),
            "width": self.width_slider.value(),
            "count": self.count_slider.value()
        }
        preset_manager.save_gradient_preset(preset_name, cfg)
            
        save_path = os.path.join(preset_manager.SCRIM_DIR, name)
        if self._current_img:
            self._current_img.save(save_path)
            self.current_img_path = save_path
            
        self.accept()
        
    def get_texture_path(self):
        return self.current_img_path
