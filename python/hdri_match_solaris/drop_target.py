# -*- coding: utf-8 -*-
"""
HDRI Match Solaris - Drop Target Widgets.

Provides drag-and-drop enabled input fields for HDRIs, plates, and textures.
Supports native OS file drag, PySide MIME URLs, and internal Crucible asset library drags.
"""

import os

try:
    from PySide6 import QtWidgets, QtCore, QtGui
except ImportError:
    from PySide2 import QtWidgets, QtCore, QtGui


class HDRIDropTarget(QtWidgets.QLineEdit):
    """
    QLineEdit with drag-and-drop support and visual highlight feedback.
    """

    file_dropped = QtCore.Signal(str)

    VALID_EXTENSIONS = {
        ".exr", ".hdr", ".hdri", ".tx", ".rat",
        ".tif", ".tiff", ".png", ".jpg", ".jpeg", ".pic"
    }

    def __init__(self, placeholder="Drop HDRI here or click ...", parent=None):
        super().__init__(parent)
        self.setPlaceholderText(placeholder)
        self.setAcceptDrops(True)
        self._normal_style = (
            "QLineEdit { background: #1e1e1e; color: #eee; border: 1px solid #444; "
            "border-radius: 3px; padding: 4px; font-size: 11px; } "
            "QLineEdit:focus { border-color: #00d2ff; }"
        )
        self._drag_active_style = (
            "QLineEdit { background: #1a2f26; color: #ffffff; border: 2px dashed #00ff88; "
            "border-radius: 3px; padding: 3px; font-weight: bold; font-size: 11px; }"
        )
        self.setStyleSheet(self._normal_style)

    def _extract_valid_path(self, mime_data):
        """Extract first valid image file path from QMimeData."""
        if not mime_data:
            return None

        # Check URLs (e.g. from Windows Explorer or AssetGridWidget)
        if mime_data.hasUrls():
            for url in mime_data.urls():
                local_path = url.toLocalFile()
                if local_path and os.path.isfile(local_path):
                    ext = os.path.splitext(local_path)[1].lower()
                    if ext in self.VALID_EXTENSIONS:
                        return local_path

        # Check text
        if mime_data.hasText():
            text = mime_data.text().strip().strip("\"'")
            if text and os.path.isfile(text):
                ext = os.path.splitext(text)[1].lower()
                if ext in self.VALID_EXTENSIONS:
                    return text

        return None

    def dragEnterEvent(self, event):
        path = self._extract_valid_path(event.mimeData())
        if path:
            event.acceptProposedAction()
            self.setStyleSheet(self._drag_active_style)
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        path = self._extract_valid_path(event.mimeData())
        if path:
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragLeaveEvent(self, event):
        self.setStyleSheet(self._normal_style)
        super().dragLeaveEvent(event)

    def dropEvent(self, event):
        self.setStyleSheet(self._normal_style)
        path = self._extract_valid_path(event.mimeData())
        if path:
            event.acceptProposedAction()
            clean_path = os.path.normpath(path).replace("\\", "/")
            self.setText(clean_path)
            self.file_dropped.emit(clean_path)
        else:
            event.ignore()
