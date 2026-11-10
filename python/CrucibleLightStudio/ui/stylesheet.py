# -*- coding: utf-8 -*-
"""
Crucible Light Studio - Dark Theme Stylesheet
===============================================
Professional dark UI theme matching DCC conventions.
Uses orange accent colors consistent with the Crucible brand.
"""

CRUCIBLE_STYLESHEET = """
/* ===== Base ===== */
QWidget {
    background-color: #1e1e1e;
    color: #cccccc;
    font-family: "Segoe UI", "Inter", "Roboto", sans-serif;
    font-size: 12px;
}

QMainWindow {
    background-color: #1a1a1a;
}

/* ===== Splitters ===== */
QSplitter::handle {
    background-color: #2b2b2b;
    height: 6px;
    width: 6px;
    border-left: 1px solid #3c3c3c;
    border-right: 1px solid #1c1c1c;
}
QSplitter::handle:hover, QSplitter::handle:pressed {
    background-color: #e67e22;
    border-color: #f39c12;
}

/* ===== Tab Widget ===== */
QTabWidget::pane {
    border: 1px solid #333333;
    border-radius: 3px;
    background-color: #1e1e1e;
}
QTabBar::tab {
    background-color: #2a2a2a;
    color: #999999;
    padding: 7px 16px;
    border: 1px solid #333333;
    border-bottom: none;
    border-top-left-radius: 4px;
    border-top-right-radius: 4px;
    margin-right: 2px;
    min-width: 60px;
}
QTabBar::tab:selected {
    background-color: #1e1e1e;
    color: #e67e22;
    font-weight: bold;
    border-bottom: 2px solid #e67e22;
}
QTabBar::tab:hover:!selected {
    background-color: #333333;
    color: #dddddd;
}

/* ===== Buttons ===== */
QPushButton {
    background-color: #2d2d2d;
    color: #cccccc;
    border: 1px solid #3a3a3a;
    padding: 6px 14px;
    border-radius: 4px;
    min-height: 20px;
}
QPushButton:hover {
    background-color: #3a3a3a;
    border-color: #e67e22;
    color: #ffffff;
}
QPushButton:pressed {
    background-color: #e67e22;
    color: #1a1a1a;
}
QPushButton:disabled {
    background-color: #222222;
    color: #555555;
    border-color: #2a2a2a;
}
QPushButton#accent_btn {
    background-color: #e67e22;
    color: #1a1a1a;
    font-weight: bold;
    border: none;
}
QPushButton#accent_btn:hover {
    background-color: #f39c12;
}
QPushButton#danger_btn {
    background-color: #444444;
    color: #e74c3c;
    border-color: #e74c3c;
}
QPushButton#danger_btn:hover {
    background-color: #e74c3c;
    color: #ffffff;
}
QPushButton#solo_btn {
    background-color: #2d2d2d;
    color: #f1c40f;
    font-weight: bold;
    min-width: 24px;
    max-width: 24px;
    padding: 2px;
    border-radius: 3px;
}
QPushButton#solo_btn:checked {
    background-color: #f1c40f;
    color: #1a1a1a;
}

/* ===== Table ===== */
QTableWidget {
    background-color: #1a1a1a;
    alternate-background-color: #1f1f1f;
    gridline-color: #2a2a2a;
    border: 1px solid #2a2a2a;
    border-radius: 3px;
    selection-background-color: #3a3018;
    selection-color: #e67e22;
}
QTableWidget::item {
    padding: 4px;
    border-bottom: 1px solid #252525;
}
QTableWidget::item:selected {
    background-color: #3a3018;
}
QHeaderView::section {
    background-color: #252525;
    color: #888888;
    padding: 5px 8px;
    border: none;
    border-right: 1px solid #333333;
    border-bottom: 1px solid #333333;
    font-weight: bold;
    font-size: 11px;
    text-transform: uppercase;
}

/* ===== Sliders ===== */
QSlider::groove:horizontal {
    height: 4px;
    background: #333333;
    border-radius: 2px;
}
QSlider::handle:horizontal {
    background: #e67e22;
    width: 14px;
    height: 14px;
    margin: -5px 0;
    border-radius: 7px;
}
QSlider::handle:horizontal:hover {
    background: #f39c12;
}
QSlider::sub-page:horizontal {
    background: #e67e22;
    border-radius: 2px;
}

/* ===== Spinboxes ===== */
QDoubleSpinBox, QSpinBox {
    background-color: #252525;
    color: #cccccc;
    border: 1px solid #3a3a3a;
    border-radius: 3px;
    padding: 3px 6px;
    min-width: 50px;
}
QDoubleSpinBox:focus, QSpinBox:focus {
    border-color: #e67e22;
}

/* ===== Combo Boxes ===== */
QComboBox {
    background-color: #252525;
    color: #cccccc;
    border: 1px solid #3a3a3a;
    border-radius: 3px;
    padding: 4px 8px;
    min-width: 80px;
}
QComboBox:hover {
    border-color: #e67e22;
}
QComboBox::drop-down {
    border: none;
    width: 20px;
}
QComboBox QAbstractItemView {
    background-color: #252525;
    color: #cccccc;
    selection-background-color: #e67e22;
    selection-color: #1a1a1a;
    border: 1px solid #3a3a3a;
}

/* ===== Checkboxes ===== */
QCheckBox::indicator {
    width: 16px;
    height: 16px;
    border: 1px solid #555555;
    border-radius: 3px;
    background-color: #252525;
}
QCheckBox::indicator:checked {
    background-color: #e67e22;
    border-color: #e67e22;
}

/* ===== List Widget (Icon Mode for Libraries) ===== */
QListWidget {
    background-color: #1a1a1a;
    border: 1px solid #2a2a2a;
    border-radius: 3px;
}
QListWidget::item {
    background-color: #252525;
    border: 1px solid #333333;
    border-radius: 4px;
    padding: 4px;
    margin: 3px;
    color: #aaaaaa;
}
QListWidget::item:selected {
    background-color: #3a3018;
    border-color: #e67e22;
    color: #ffffff;
}
QListWidget::item:hover {
    border-color: #e67e22;
    color: #dddddd;
}

/* ===== Scroll Bars ===== */
QScrollBar:vertical {
    background: #1a1a1a;
    width: 10px;
    border: none;
}
QScrollBar::handle:vertical {
    background: #3a3a3a;
    min-height: 30px;
    border-radius: 5px;
}
QScrollBar::handle:vertical:hover {
    background: #e67e22;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0px;
}
QScrollBar:horizontal {
    background: #1a1a1a;
    height: 10px;
    border: none;
}
QScrollBar::handle:horizontal {
    background: #3a3a3a;
    min-width: 30px;
    border-radius: 5px;
}
QScrollBar::handle:horizontal:hover {
    background: #e67e22;
}

/* ===== Group Box ===== */
QGroupBox {
    border: 1px solid #333333;
    border-radius: 4px;
    margin-top: 12px;
    padding-top: 16px;
    font-weight: bold;
    color: #e67e22;
}
QGroupBox::title {
    subcontrol-origin: margin;
    padding: 0 8px;
}

/* ===== Labels ===== */
QLabel#section_title {
    color: #e67e22;
    font-size: 13px;
    font-weight: bold;
    padding: 4px 0;
}
QLabel#subtle {
    color: #666666;
    font-size: 11px;
}

/* ===== Line Edit ===== */
QLineEdit {
    background-color: #252525;
    color: #cccccc;
    border: 1px solid #3a3a3a;
    border-radius: 3px;
    padding: 4px 8px;
}
QLineEdit:focus {
    border-color: #e67e22;
}

/* ===== Tool Tips ===== */
QToolTip {
    background-color: #2a2a2a;
    color: #e67e22;
    border: 1px solid #e67e22;
    padding: 4px;
    border-radius: 3px;
}
"""
