# -*- coding: utf-8 -*-
"""
SPLATFORGE UI Tab — PySide2 Panel for the DomeBreaker Interface.

Provides the artist-facing controls for the SPLATFORGE pipeline:
PLY loading, room analysis, reconstruction, texture baking, lighting,
and USD assembly.  Integrates as a new tab in the existing DomeBreaker panel.

This module defines the widget class; it is instantiated and added to the
main panel by the panel_ui module.
"""

import os
import traceback

try:
    from PySide2 import QtWidgets, QtCore, QtGui
except ImportError:
    from PySide6 import QtWidgets, QtCore, QtGui


class SplatForgeTab(QtWidgets.QWidget):
    """Main SPLATFORGE pipeline control tab.

    Sections:
        1. Input — PLY file selection and loading
        2. Analysis — Scene stats and room detection
        3. Reconstruction — Room shell and prop meshes
        4. Lighting — HDRI and light extraction
        5. Assembly — Solaris USD output
    """

    def __init__(self, parent=None, stage_node_getter=None):
        """
        Args:
            parent:             Parent widget.
            stage_node_getter:  Callable returning the active /stage LOP node.
        """
        super().__init__(parent)
        self._stage_node_getter = stage_node_getter
        self._splat_data = None
        self._scene_analysis = None
        self._room_data = None
        self._light_analysis = None
        self._hdri_result = None

        self._build_ui()

    # ------------------------------------------------------------------
    # UI Construction
    # ------------------------------------------------------------------

    def _build_ui(self):
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)

        # Header
        header = QtWidgets.QLabel("SPLATFORGE")
        header.setStyleSheet(
            "font-size: 14px; font-weight: bold; "
            "color: #b8e986; padding: 4px;"
        )
        header.setAlignment(QtCore.Qt.AlignCenter)
        layout.addWidget(header)

        subtitle = QtWidgets.QLabel("Gaussian Splat → VFX Environment")
        subtitle.setStyleSheet(
            "font-size: 10px; color: #888; padding: 0px;"
        )
        subtitle.setAlignment(QtCore.Qt.AlignCenter)
        layout.addWidget(subtitle)

        layout.addSpacing(6)

        # Scroll area for sections
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        scroll_widget = QtWidgets.QWidget()
        scroll_layout = QtWidgets.QVBoxLayout(scroll_widget)
        scroll_layout.setContentsMargins(0, 0, 0, 0)
        scroll_layout.setSpacing(6)

        # Section 1: Input
        scroll_layout.addWidget(self._build_input_section())

        # Section 2: Analysis
        scroll_layout.addWidget(self._build_analysis_section())

        # Section 3: Reconstruction
        scroll_layout.addWidget(self._build_reconstruction_section())

        # Section 4: Lighting
        scroll_layout.addWidget(self._build_lighting_section())

        # Section 5: Assembly
        scroll_layout.addWidget(self._build_assembly_section())

        scroll_layout.addStretch()
        scroll.setWidget(scroll_widget)
        layout.addWidget(scroll)

        # Status bar
        self._status_label = QtWidgets.QLabel("Ready")
        self._status_label.setStyleSheet(
            "font-size: 9px; color: #666; padding: 2px;"
        )
        layout.addWidget(self._status_label)

        # Progress bar
        self._progress_bar = QtWidgets.QProgressBar()
        self._progress_bar.setMaximumHeight(6)
        self._progress_bar.setTextVisible(False)
        self._progress_bar.setValue(0)
        layout.addWidget(self._progress_bar)

    def _build_section(self, title, color="#555"):
        """Create a collapsible section group box."""
        group = QtWidgets.QGroupBox(title)
        group.setCheckable(False)
        group.setStyleSheet(
            f"QGroupBox {{ font-weight: bold; border: 1px solid {color}; "
            f"border-radius: 4px; margin-top: 6px; padding-top: 14px; }}"
            f"QGroupBox::title {{ subcontrol-origin: margin; left: 10px; "
            f"padding: 0 4px; color: {color}; }}"
        )
        inner = QtWidgets.QVBoxLayout()
        inner.setContentsMargins(8, 4, 8, 8)
        inner.setSpacing(4)
        group.setLayout(inner)
        return group

    # ------------------------------------------------------------------
    # Section builders
    # ------------------------------------------------------------------

    def _build_input_section(self):
        section = self._build_section("① Input", "#b8e986")
        layout = section.layout()

        # PLY file picker
        row = QtWidgets.QHBoxLayout()
        self._ply_path_edit = QtWidgets.QLineEdit()
        self._ply_path_edit.setPlaceholderText("Path to Gaussian Splat .ply file...")
        row.addWidget(self._ply_path_edit, stretch=1)

        browse_btn = QtWidgets.QPushButton("...")
        browse_btn.setFixedWidth(30)
        browse_btn.clicked.connect(self._on_browse_ply)
        row.addWidget(browse_btn)
        layout.addLayout(row)

        # Load button
        self._load_btn = QtWidgets.QPushButton("Load Gaussian Splat")
        self._load_btn.setStyleSheet(
            "QPushButton { background-color: #3a6e3a; color: white; "
            "padding: 6px; border-radius: 3px; }"
            "QPushButton:hover { background-color: #4a8e4a; }"
        )
        self._load_btn.clicked.connect(self._on_load_splat)
        layout.addWidget(self._load_btn)

        # Info display
        self._splat_info_label = QtWidgets.QLabel("")
        self._splat_info_label.setStyleSheet("font-size: 9px; color: #aaa;")
        self._splat_info_label.setWordWrap(True)
        layout.addWidget(self._splat_info_label)

        return section

    def _build_analysis_section(self):
        section = self._build_section("② Analysis", "#86b8e9")
        layout = section.layout()

        # Coordinate options
        opts_row = QtWidgets.QHBoxLayout()
        self._flip_y_cb = QtWidgets.QCheckBox("Flip Y (COLMAP→Houdini)")
        self._flip_y_cb.setChecked(True)
        opts_row.addWidget(self._flip_y_cb)

        opts_row.addWidget(QtWidgets.QLabel("Scale:"))
        self._scale_spin = QtWidgets.QDoubleSpinBox()
        self._scale_spin.setRange(0.001, 100.0)
        self._scale_spin.setValue(1.0)
        self._scale_spin.setDecimals(4)
        self._scale_spin.setSingleStep(0.01)
        opts_row.addWidget(self._scale_spin)
        layout.addLayout(opts_row)

        # Analyze buttons
        btn_row = QtWidgets.QHBoxLayout()
        self._analyze_scene_btn = QtWidgets.QPushButton("Analyze Scene")
        self._analyze_scene_btn.clicked.connect(self._on_analyze_scene)
        btn_row.addWidget(self._analyze_scene_btn)

        self._detect_room_btn = QtWidgets.QPushButton("Detect Room")
        self._detect_room_btn.clicked.connect(self._on_detect_room)
        btn_row.addWidget(self._detect_room_btn)
        layout.addLayout(btn_row)

        # Analysis results
        self._analysis_text = QtWidgets.QTextEdit()
        self._analysis_text.setReadOnly(True)
        self._analysis_text.setMaximumHeight(120)
        self._analysis_text.setStyleSheet(
            "font-family: monospace; font-size: 9px; "
            "background-color: #1a1a1a; color: #ccc;"
        )
        layout.addWidget(self._analysis_text)

        return section

    def _build_reconstruction_section(self):
        section = self._build_section("③ Reconstruction", "#e9b886")
        layout = section.layout()

        self._reconstruct_room_btn = QtWidgets.QPushButton("Reconstruct Room Shell")
        self._reconstruct_room_btn.clicked.connect(self._on_reconstruct_room)
        layout.addWidget(self._reconstruct_room_btn)

        self._recon_info_label = QtWidgets.QLabel("")
        self._recon_info_label.setStyleSheet("font-size: 9px; color: #aaa;")
        self._recon_info_label.setWordWrap(True)
        layout.addWidget(self._recon_info_label)

        return section

    def _build_lighting_section(self):
        section = self._build_section("④ Lighting & Radiance Extraction", "#e986b8")
        layout = section.layout()

        # Row 1: HDRI Resolution Selector & Bake HDRI Button
        hdri_row = QtWidgets.QHBoxLayout()
        hdri_lbl = QtWidgets.QLabel("HDRI Res:")
        hdri_lbl.setStyleSheet("color: #ccc; font-size: 10px; font-weight: bold;")
        self._hdri_res_combo = QtWidgets.QComboBox()
        self._hdri_res_combo.addItems([
            "1024 x 512 (Fast Preview)",
            "2048 x 1024 (Standard / Default)",
            "4096 x 2048 (Production 4K)",
            "8192 x 4096 (Ultra 8K)",
        ])
        self._hdri_res_combo.setCurrentIndex(1)  # 2048x1024 default
        self._hdri_res_combo.setStyleSheet(
            "QComboBox { background-color: #2b2b2b; color: #eee; padding: 3px 6px; border: 1px solid #444; border-radius: 2px; }"
            "QComboBox::drop-down { border: none; }"
        )

        self._bake_hdri_btn = QtWidgets.QPushButton("Bake HDRI")
        self._bake_hdri_btn.setStyleSheet(
            "QPushButton { background-color: #5a2c42; color: white; padding: 4px 12px; font-weight: bold; border-radius: 2px; }"
            "QPushButton:hover { background-color: #7a3c5a; }"
        )
        self._bake_hdri_btn.clicked.connect(self._on_bake_hdri)

        hdri_row.addWidget(hdri_lbl)
        hdri_row.addWidget(self._hdri_res_combo, 1)
        hdri_row.addWidget(self._bake_hdri_btn)
        layout.addLayout(hdri_row)

        # Row 2: Window Apertures & Practical Light Extraction Controls
        light_row = QtWidgets.QHBoxLayout()

        self._match_windows_cb = QtWidgets.QCheckBox("Window Portals")
        self._match_windows_cb.setChecked(True)
        self._match_windows_cb.setToolTip("Create RectLights matching the exact dimensions and positions of detected window openings.")
        self._match_windows_cb.setStyleSheet("color: #ddd; font-size: 10px;")

        max_l_lbl = QtWidgets.QLabel("Max Practicals:")
        max_l_lbl.setStyleSheet("color: #ccc; font-size: 10px;")
        self._max_lights_spin = QtWidgets.QSpinBox()
        self._max_lights_spin.setRange(0, 32)
        self._max_lights_spin.setValue(8)
        self._max_lights_spin.setToolTip("Maximum number of interior practical lights to extract.")
        self._max_lights_spin.setStyleSheet("background-color: #2b2b2b; color: #eee;")

        self._extract_lights_btn = QtWidgets.QPushButton("Extract Lights")
        self._extract_lights_btn.setStyleSheet(
            "QPushButton { background-color: #4a3e20; color: white; padding: 4px 12px; font-weight: bold; border-radius: 2px; }"
            "QPushButton:hover { background-color: #6a5830; }"
        )
        self._extract_lights_btn.clicked.connect(self._on_extract_lights)

        light_row.addWidget(self._match_windows_cb)
        light_row.addWidget(max_l_lbl)
        light_row.addWidget(self._max_lights_spin)
        light_row.addWidget(self._extract_lights_btn)
        layout.addLayout(light_row)

        self._lighting_info_label = QtWidgets.QLabel("HDRI: Not baked | Lights: Not extracted")
        self._lighting_info_label.setStyleSheet(
            "font-size: 9px; color: #bbb; background-color: #1a1a1a; padding: 5px; "
            "border: 1px solid #333; border-radius: 3px;"
        )
        self._lighting_info_label.setWordWrap(True)
        layout.addWidget(self._lighting_info_label)

        return section

    def _build_assembly_section(self):
        section = self._build_section("⑤ Solaris Assembly", "#b886e9")
        layout = section.layout()

        self._assemble_btn = QtWidgets.QPushButton("Assemble USD Scene")
        self._assemble_btn.setStyleSheet(
            "QPushButton { background-color: #5a3a6e; color: white; "
            "padding: 8px; border-radius: 3px; font-weight: bold; }"
            "QPushButton:hover { background-color: #7a5a8e; }"
        )
        self._assemble_btn.clicked.connect(self._on_assemble_usd)
        layout.addWidget(self._assemble_btn)

        self._assembly_info_label = QtWidgets.QLabel("")
        self._assembly_info_label.setStyleSheet("font-size: 9px; color: #aaa;")
        self._assembly_info_label.setWordWrap(True)
        layout.addWidget(self._assembly_info_label)

        return section

    # ------------------------------------------------------------------
    # Progress / status helpers
    # ------------------------------------------------------------------

    def _set_status(self, message):
        self._status_label.setText(message)
        QtWidgets.QApplication.processEvents()

    def _set_progress(self, percent, message=""):
        self._progress_bar.setValue(int(percent))
        if message:
            self._set_status(message)

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------

    def _on_browse_ply(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Select Gaussian Splat PLY",
            "", "PLY Files (*.ply);;All Files (*)"
        )
        if path:
            self._ply_path_edit.setText(path.replace("\\", "/"))

    def _on_load_splat(self):
        ply_path = self._ply_path_edit.text().strip()
        if not ply_path:
            self._set_status("No PLY file specified.")
            return

        try:
            from hdri_match_solaris.splatforge.io.splat_loader import load_splat
            self._splat_data = load_splat(
                ply_path, progress_callback=self._set_progress
            )
            summary = self._splat_data.summary()
            info = (
                f"Loaded: {summary['count']:,} splats\n"
                f"Center: [{summary['center'][0]:.2f}, "
                f"{summary['center'][1]:.2f}, "
                f"{summary['center'][2]:.2f}]\n"
                f"Radius: {summary['radius']:.2f}\n"
                f"Mean opacity: {summary['mean_opacity']:.3f}\n"
                f"Max luminance: {summary['max_luminance']:.3f}"
            )
            self._splat_info_label.setText(info)
            self._set_status(f"Loaded {summary['count']:,} splats.")
        except Exception as e:
            self._set_status(f"Load error: {e}")
            traceback.print_exc()

    def _on_analyze_scene(self):
        if self._splat_data is None:
            self._set_status("Load a Gaussian Splat first.")
            return

        try:
            from hdri_match_solaris.splatforge.analysis.scene_analyzer import analyze_scene
            self._scene_analysis = analyze_scene(
                self._splat_data,
                flip_y=self._flip_y_cb.isChecked(),
                scene_scale=self._scale_spin.value(),
                progress_callback=self._set_progress,
            )
            d = self._scene_analysis.to_dict()
            text_lines = [
                f"Splats:    {d['count']:,}",
                f"BBox:      {d['bbox_size'][0]:.2f} × {d['bbox_size'][1]:.2f} × {d['bbox_size'][2]:.2f}",
                f"Center:    [{d['center'][0]:.2f}, {d['center'][1]:.2f}, {d['center'][2]:.2f}]",
                f"Floor Y:   {d['raw_floor_y']:.3f}",
                f"Ceil  Y:   {d['raw_ceil_y']:.3f}",
                f"Height:    {d['raw_height']:.3f}",
                f"Est.Scale: {d['estimated_scale']:.4f}",
                f"Bright:    {d['bright_splat_count']:,} splats above energy threshold",
            ]
            self._analysis_text.setPlainText("\n".join(text_lines))
            self._set_status("Scene analysis complete.")
        except Exception as e:
            self._set_status(f"Analysis error: {e}")
            traceback.print_exc()

    def _on_detect_room(self):
        if self._splat_data is None:
            self._set_status("Load a Gaussian Splat first.")
            return

        try:
            from hdri_match_solaris.splatforge.analysis.room_detector import detect_room
            self._room_data = detect_room(
                self._splat_data,
                flip_y=self._flip_y_cb.isChecked(),
                scene_scale=self._scale_spin.value(),
                extract_props=True,
                progress_callback=self._set_progress,
            )
            rd = self._room_data
            text_lines = [
                f"Floor Y:    {rd.floor_y:.3f}",
                f"Ceil Y:     {rd.ceil_y:.3f}",
                f"Room:       {rd.width:.2f} W × {rd.depth:.2f} D × {rd.height:.2f} H",
                f"Windows:    {len(rd.windows)}",
                f"Doors:      {len(rd.doors)}",
                f"Props:      {len(rd.props)}",
                f"Planes:     {len(rd.planes)}",
                f"Scale:      {rd.estimated_scale:.4f}",
            ]
            self._analysis_text.setPlainText("\n".join(text_lines))
            self._set_status(f"Room detected: {len(rd.windows)} windows, "
                             f"{len(rd.doors)} doors, {len(rd.props)} props.")
        except Exception as e:
            self._set_status(f"Room detection error: {e}")
            traceback.print_exc()

    def _on_reconstruct_room(self):
        if self._room_data is None:
            self._set_status("Detect room first.")
            return

        try:
            from hdri_match_solaris.splatforge.reconstruction.room_reconstructor import (
                reconstruct_room_shell,
                create_room_sop_geometry,
            )
            mesh = reconstruct_room_shell(
                self._room_data,
                include_openings=True,
                progress_callback=self._set_progress,
            )
            self._room_mesh = mesh

            # Create or update Houdini SOP geometry in /obj safely
            sop_info = ""
            try:
                from hdri_match_solaris.splatforge.solaris.scene_builder import build_room_sop_node
                geo_node = build_room_sop_node(mesh, self._splat_data)
                if geo_node:
                    sop_info = " (SOP: /obj/splatforge_room_geo)"
            except Exception as e:
                print(f"[SPLATFORGE] SOP warning: {e}")

            self._recon_info_label.setText(
                f"Room shell: {mesh.vertex_count} vertices, "
                f"{mesh.face_count} faces{sop_info}"
            )
            self._set_status(f"Room shell reconstructed ({mesh.face_count} faces).")
        except Exception as e:
            self._set_status(f"Reconstruction error: {e}")
            traceback.print_exc()

    def _update_lighting_display(self, defs=None):
        hdri_info = "HDRI: Not baked"
        if self._hdri_result:
            path = self._hdri_result.get("hdri_path", "")
            res = self._hdri_result.get("resolution", (2048, 1024))
            cached = self._hdri_result.get("cached", False)
            hdri_info = f"HDRI: {os.path.basename(path)} ({res[0]}x{res[1]}{', cached' if cached else ''})"

        lights_info = "Lights: Not extracted"
        if defs is not None or self._light_analysis is not None:
            if defs is None:
                from hdri_match_solaris.splatforge.lighting.light_extractor import extract_light_definitions
                room_d = self._room_data
                defs = extract_light_definitions(self._light_analysis, self._hdri_result, room_data=room_d)
            rect_defs = [ld for ld in defs if ld.light_type == "rect"]
            lights_info = f"Lights ({len(rect_defs)} total):\n"
            for ld in rect_defs:
                dim_str = f"{ld.width:.1f} x {ld.height:.1f}"
                pos_str = f"({ld.position[0]:.1f}, {ld.position[1]:.1f}, {ld.position[2]:.1f})"
                lights_info += f"  • {ld.name}: {dim_str} at {pos_str}\n"

        self._lighting_info_label.setText(f"{hdri_info}\n{lights_info}".strip())

    def _on_bake_hdri(self):
        if self._splat_data is None:
            self._set_status("Load a Gaussian Splat first.")
            return

        try:
            # Parse selected resolution from combobox
            w, h = 2048, 1024
            if hasattr(self, "_hdri_res_combo"):
                res_text = self._hdri_res_combo.currentText()
                try:
                    parts = res_text.split("x")
                    w = int(parts[0].strip())
                    h = int(parts[1].split()[0].strip())
                except Exception:
                    pass

            from hdri_match_solaris.splatforge.lighting.hdri_generator import generate_hdri
            self._hdri_result = generate_hdri(
                self._splat_data,
                width=w,
                height=h,
                flip_y=self._flip_y_cb.isChecked(),
                scene_scale=self._scale_spin.value(),
                progress_callback=self._set_progress,
            )
            path = self._hdri_result.get("hdri_path", "")
            self._update_lighting_display()
            self._set_status(f"HDRI baked ({w}x{h}).")

            # Update existing splatforge_dome LOP texture parm if present in /stage
            try:
                import hou
                stage_node = hou.node("/stage")
                if stage_node:
                    dome = stage_node.node("splatforge_dome")
                    if dome:
                        norm_p = path.replace("\\", "/")
                        for p_name in ["xn__inputstexturefile_51a", "texturefile", "texture"]:
                            p = dome.parm(p_name)
                            if p:
                                p.set(norm_p)
                                break
            except Exception:
                pass
        except Exception as e:
            self._set_status(f"HDRI error: {e}")
            traceback.print_exc()

    def _on_extract_lights(self):
        if self._splat_data is None:
            self._set_status("Load a Gaussian Splat first.")
            return

        try:
            if self._room_data is None:
                self._on_detect_room()

            from hdri_match_solaris.splatforge.analysis.light_analyzer import analyze_lights
            from hdri_match_solaris.splatforge.lighting.light_extractor import (
                extract_light_definitions,
            )

            max_l = self._max_lights_spin.value() if hasattr(self, "_max_lights_spin") else 8
            room_d = self._room_data

            self._light_analysis = analyze_lights(
                self._splat_data,
                flip_y=self._flip_y_cb.isChecked(),
                scene_scale=self._scale_spin.value(),
                max_lights=max_l,
                room_data=room_d,
                progress_callback=self._set_progress,
            )
            defs = extract_light_definitions(
                self._light_analysis, self._hdri_result, room_data=room_d
            )
            self._update_lighting_display(defs)

            rect_defs = [ld for ld in defs if ld.light_type == "rect"]
            self._set_status(f"Extracted {len(rect_defs)} light source(s).")

            # If Solaris stage already has nodes, dynamically update the lights in the stream!
            try:
                import hou
                stage_node = hou.node("/stage")
                if stage_node and (stage_node.node("splatforge_room") or stage_node.node("splatforge_dome")):
                    from hdri_match_solaris.splatforge.solaris.scene_builder import update_scene_lights
                    update_scene_lights(stage_node, self._light_analysis, self._hdri_result, room_data=room_d)
            except Exception as e:
                print(f"[SPLATFORGE] Light sync warning: {e}")
        except Exception as e:
            self._set_status(f"Light extraction error: {e}")
            traceback.print_exc()

    def _on_assemble_usd(self):
        if self._splat_data is None:
            self._set_status("Load a Gaussian Splat first.")
            return

        try:
            import hou
        except ImportError:
            hou = None

        if not hou:
            self._set_status("Error: Houdini Python (hou) not available.")
            return

        try:
            # 1. Auto-detect room if needed
            if self._room_data is None:
                self._on_detect_room()

            # 2. Auto-reconstruct room shell if needed
            if getattr(self, "_room_mesh", None) is None and self._room_data is not None:
                self._on_reconstruct_room()

            # 3. Auto-bake HDRI if needed
            if self._hdri_result is None:
                self._on_bake_hdri()

            # 4. Auto-extract lights if needed
            if self._light_analysis is None:
                self._on_extract_lights()

            # 5. Resolve stage node
            stage_node = None
            if callable(self._stage_node_getter):
                try:
                    stage_node = self._stage_node_getter()
                except Exception:
                    stage_node = None
            if stage_node is None:
                stage_node = hou.node("/stage")
                if stage_node is None:
                    obj = hou.node("/obj")
                    if obj:
                        stage_node = obj.createNode("lopnet", "stage")

            from hdri_match_solaris.splatforge.solaris.scene_builder import assemble_splatforge_scene

            result = assemble_splatforge_scene(
                stage_node=stage_node,
                splat_data=self._splat_data,
                room_data=self._room_data,
                room_mesh=getattr(self, "_room_mesh", None),
                light_analysis=self._light_analysis,
                hdri_result=self._hdri_result,
                progress_callback=self._set_progress,
            )

            prims_str = ", ".join(result.get("created_prims", []))
            self._assembly_info_label.setText(
                f"Scene Assembled in {stage_node.path()}:\n"
                f"• Prims: {prims_str}\n"
                f"• Dome Light: {'Active' if result.get('dome_light') else 'None'}\n"
                f"• Rect Lights: {result.get('rect_lights', 0)}\n"
                f"• SOP Geo: /obj/splatforge_room_geo"
            )
            self._set_status(f"USD Scene assembled in {stage_node.path()} successfully!")
        except Exception as e:
            self._set_status(f"USD Assembly error: {e}")
            traceback.print_exc()
