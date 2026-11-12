# -*- coding: utf-8 -*-
"""
Crucible Light Studio - Asset Library Widget
==============================================
Tabbed visual browser for HDRIs, Gobos, Scrim textures, and Light
Presets. Supports thumbnail grid view, drag-and-drop to lights,
and audition-on-hover (preview without committing).
"""

try:
    from PySide2 import QtWidgets, QtCore, QtGui
except ImportError:
    from PySide6 import QtWidgets, QtCore, QtGui

import os
import shutil
import subprocess
import logging

from CrucibleLightStudio.core import preset_manager

logger = logging.getLogger("CrucibleLightStudio.ui.asset_library")

# Module-level registry keeping still-running cancelled workers alive until OS exit
# so Qt never invokes QThread::~QThread() while a subprocess call is mid-flight.
_ACTIVE_THUMBNAIL_WORKERS = set()


class ThumbnailWorker(QtCore.QThread):
    thumbnail_ready = QtCore.Signal(int, str, int)  # row_index, proxy_path, generation_id

    def __init__(self, assets, generation_id=0, parent=None):
        super().__init__(parent)
        self.assets = assets
        self.generation_id = int(generation_id)
        self.is_cancelled = False

    def cancel(self):
        self.is_cancelled = True
        try:
            self.requestInterruption()
        except Exception:
            pass

    def _should_stop(self):
        if self.is_cancelled:
            return True
        try:
            return bool(self.isInterruptionRequested())
        except Exception:
            return False

    def run(self):
        kwargs = {
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
            "timeout": 4,
        }
        if os.name == 'nt' and hasattr(subprocess, 'CREATE_NO_WINDOW'):
            kwargs['creationflags'] = subprocess.CREATE_NO_WINDOW

        hoiiotool_bin = None
        if "HFS" in os.environ:
            cand = os.path.join(os.environ["HFS"], "bin", "hoiiotool.exe" if os.name == "nt" else "hoiiotool")
            if os.path.isfile(cand):
                hoiiotool_bin = cand
        if not hoiiotool_bin:
            hoiiotool_bin = shutil.which("hoiiotool")
        iconvert_bin = shutil.which("iconvert")

        for i, asset in self.assets:
            if self._should_stop():
                break
            if asset.get("is_folder", False):
                continue
            filepath = asset.get("path")
            if not filepath or not os.path.isfile(filepath):
                continue

            ext = os.path.splitext(filepath)[1].lower()
            if ext in {".exr", ".hdr", ".hdri", ".tx"}:
                proxy_path = filepath + ".thumb.jpg"
                if not os.path.exists(proxy_path):
                    try:
                        ret = -1
                        if hoiiotool_bin:
                            ret = subprocess.run(
                                [hoiiotool_bin, filepath, "--resize", "256x256", "-o", proxy_path],
                                check=False,
                                **kwargs
                            ).returncode
                        if not self._should_stop() and ret != 0 and iconvert_bin:
                            subprocess.run(
                                [iconvert_bin, filepath, proxy_path],
                                check=False,
                                **kwargs
                            )
                    except Exception:
                        pass
                if not self._should_stop() and os.path.exists(proxy_path):
                    try:
                        self.thumbnail_ready.emit(i, proxy_path, self.generation_id)
                    except Exception:
                        pass
            elif ext in {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}:
                if not self._should_stop():
                    try:
                        self.thumbnail_ready.emit(i, filepath, self.generation_id)
                    except Exception:
                        pass

class AssetGridWidget(QtWidgets.QListWidget):
    """
    Icon-mode list widget for browsing image assets with thumbnails.
    Supports audition-on-hover and drag-and-drop.
    """

    asset_selected = QtCore.Signal(dict)  # {"name", "path", "ext"}
    send_to_match_requested = QtCore.Signal(dict)
    assign_to_crucible_light_requested = QtCore.Signal(dict)
    asset_hovered = QtCore.Signal(dict)   # For audition preview
    asset_hover_left = QtCore.Signal()
    asset_modified = QtCore.Signal()      # For renaming/deleting
    folder_entered = QtCore.Signal(str)   # Emitted when a folder item is double-clicked

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setViewMode(QtWidgets.QListView.IconMode)
        self.setIconSize(QtCore.QSize(96, 64))
        self.setResizeMode(QtWidgets.QListView.Adjust)
        self.setSpacing(6)
        self.setMovement(QtWidgets.QListView.Static)
        self.setWrapping(True)
        self.setDragEnabled(True)
        self.setMouseTracking(True)

        self._assets = []
        self._last_hovered = None
        self._worker = None
        self._worker_gen = 0

        self.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_context_menu)
        self.itemDoubleClicked.connect(self._on_double_click)

    def mimeData(self, items):
        """Provide standard file URLs and file path text in MIME data for cross-widget drag-and-drop."""
        mime = super().mimeData(items)
        if mime is None:
            mime = QtCore.QMimeData()
        urls = []
        for item in items:
            data = item.data(QtCore.Qt.UserRole)
            if data and isinstance(data, dict):
                fpath = data.get("path")
                if fpath and os.path.isfile(fpath):
                    urls.append(QtCore.QUrl.fromLocalFile(os.path.normpath(fpath)))
        if urls:
            mime.setUrls(urls)
            mime.setText(urls[0].toLocalFile())
        return mime

    def startDrag(self, supportedActions):
        """Start drag with thumbnail pixmap feedback."""
        items = self.selectedItems()
        if not items:
            return
        drag = QtGui.QDrag(self)
        mime = self.mimeData(items)
        drag.setMimeData(mime)
        icon = items[0].icon()
        if not icon.isNull():
            pixmap = icon.pixmap(QtCore.QSize(80, 54))
            if not pixmap.isNull():
                drag.setPixmap(pixmap)
                drag.setHotSpot(QtCore.QPoint(40, 27))
        drag.exec_(QtCore.Qt.CopyAction)

    def _retire_worker(self, worker):
        """Disconnect UI signals and keep running worker referenced until OS thread exits."""
        if worker is None:
            return
        try:
            worker.thumbnail_ready.disconnect(self._on_thumbnail_ready)
        except Exception:
            pass
        worker.cancel()
        if worker.isRunning():
            _ACTIVE_THUMBNAIL_WORKERS.add(worker)
            try:
                worker.quit()
                worker.wait(50)
            except Exception:
                pass
            if not worker.isRunning():
                _ACTIVE_THUMBNAIL_WORKERS.discard(worker)
                try:
                    worker.deleteLater()
                except Exception:
                    pass
        else:
            _ACTIVE_THUMBNAIL_WORKERS.discard(worker)
            try:
                worker.deleteLater()
            except Exception:
                pass

    def stop_worker(self):
        self._worker_gen += 1
        worker = getattr(self, '_worker', None)
        self._worker = None
        self._retire_worker(worker)

    def load_assets(self, asset_list):
        """Populate grid from a list of asset dicts asynchronously."""
        self.stop_worker()
        self.clear()
        self._assets = asset_list
        current_gen = self._worker_gen

        worker_queue = []
        for i, asset in enumerate(asset_list):
            item = QtWidgets.QListWidgetItem(asset["name"])
            item.setData(QtCore.Qt.UserRole, asset)
            item.setToolTip(asset["path"])
            if asset.get("is_folder", False):
                item.setIcon(self._folder_icon())
            else:
                fpath = asset.get("path", "")
                ext = asset.get("ext", "").lower()
                # If it's a standard lightweight image, load icon directly without thread overhead
                if ext in {".png", ".jpg", ".jpeg", ".webp"} and fpath and os.path.isfile(fpath):
                    pixmap = QtGui.QPixmap(fpath)
                    if not pixmap.isNull():
                        item.setIcon(QtGui.QIcon(pixmap.scaled(96, 64, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation)))
                    else:
                        item.setIcon(self._placeholder_icon(ext))
                else:
                    item.setIcon(self._placeholder_icon(ext))
                    worker_queue.append((i, asset))
            self.addItem(item)

        if worker_queue:
            worker = ThumbnailWorker(worker_queue, generation_id=current_gen, parent=None)
            _ACTIVE_THUMBNAIL_WORKERS.add(worker)
            worker.finished.connect(lambda w=worker: (_ACTIVE_THUMBNAIL_WORKERS.discard(w), w.deleteLater()))
            worker.thumbnail_ready.connect(self._on_thumbnail_ready)
            self._worker = worker
            worker.start()

    def _on_thumbnail_ready(self, idx, filepath, generation_id=None):
        if generation_id is not None and generation_id != self._worker_gen:
            return
        item = self.item(idx)
        if item:
            pixmap = QtGui.QPixmap(filepath)
            if not pixmap.isNull():
                scaled = pixmap.scaled(96, 64, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation)
                item.setIcon(QtGui.QIcon(scaled))

    def _folder_icon(self):
        """Generate a lightweight folder icon for folder items."""
        pixmap = QtGui.QPixmap(64, 64)
        pixmap.fill(QtCore.Qt.transparent)
        painter = QtGui.QPainter(pixmap)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)

        folder_color = QtGui.QColor(230, 126, 34)
        tab_color = QtGui.QColor(211, 84, 0)

        # Tab
        painter.setPen(QtCore.Qt.NoPen)
        painter.setBrush(tab_color)
        painter.drawRoundedRect(5, 10, 22, 10, 2, 2)

        # Body
        painter.setBrush(folder_color)
        painter.drawRoundedRect(5, 16, 54, 39, 3, 3)

        # Front Flap Highlight
        painter.setBrush(QtGui.QColor(243, 156, 18))
        painter.drawRoundedRect(5, 25, 54, 30, 3, 3)

        painter.end()
        return QtGui.QIcon(pixmap)

    def _placeholder_icon(self, ext):
        """Generate a lightweight coloured placeholder icon based on file type."""
        pixmap = QtGui.QPixmap(96, 64)

        color_map = {
            ".exr": QtGui.QColor(60, 80, 100),
            ".hdr": QtGui.QColor(80, 60, 100),
            ".hdri": QtGui.QColor(80, 60, 100),
            ".tx": QtGui.QColor(60, 100, 80),
            ".builder": QtGui.QColor(220, 100, 20),
        }
        color = color_map.get(ext, QtGui.QColor(50, 50, 50))
        pixmap.fill(color)

        # Draw extension text
        painter = QtGui.QPainter(pixmap)
        painter.setPen(QtGui.QColor(200, 200, 200))
        font = painter.font()
        font.setPointSize(9)
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(pixmap.rect(), QtCore.Qt.AlignCenter,
                          ext.upper().replace(".", ""))
        painter.end()

        return QtGui.QIcon(pixmap)

    def mouseMoveEvent(self, event):
        """Emit audition signal on hover over different items."""
        item = self.itemAt(event.pos())
        if item and item != self._last_hovered:
            self._last_hovered = item
            data = item.data(QtCore.Qt.UserRole)
            if data:
                self.asset_hovered.emit(data)
        elif not item and self._last_hovered:
            self._last_hovered = None
            self.asset_hover_left.emit()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):
        self._last_hovered = None
        self.asset_hover_left.emit()
        super().leaveEvent(event)

    def _show_context_menu(self, pos):
        item = self.itemAt(pos)
        if not item: return
        data = item.data(QtCore.Qt.UserRole)
        if not data or not data.get("path"): return
        if data.get("is_folder", False):
            menu = QtWidgets.QMenu(self)
            action_open = menu.addAction("Open Folder")
            action_open.triggered.connect(lambda: self.folder_entered.emit(data["path"]))
            menu.exec_(self.mapToGlobal(pos))
            return
        if data.get("ext") == ".builder": return

        menu = QtWidgets.QMenu(self)
        ext = data.get("ext", "").lower()
        action_match = None
        action_assign = None
        if ext in [".exr", ".hdr", ".hdri"]:
            action_match = menu.addAction("Send to HDRI Match (Source HDRI)")
            action_assign = menu.addAction("Assign to Selected Crucible Light")
            menu.addSeparator()
        elif ext in [".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".tx"]:
            action_assign = menu.addAction("Assign to Selected Crucible Light")
            menu.addSeparator()
        action_rename = menu.addAction("Rename")
        action_delete = menu.addAction("Delete")

        action = menu.exec_(self.mapToGlobal(pos))
        if action_match and action == action_match:
            self.send_to_match_requested.emit(data)
        elif action_assign and action == action_assign:
            self.assign_to_crucible_light_requested.emit(data)
        elif action == action_rename:
            new_name, ok = QtWidgets.QInputDialog.getText(
                self, "Rename Asset", "New name:", text=item.text()
            )
            if ok and new_name:
                old_path = data["path"]
                dir_path = os.path.dirname(old_path)
                ext = data["ext"]
                # Append extension if not provided
                if not new_name.lower().endswith(ext.lower()):
                    new_name += ext
                new_path = os.path.join(dir_path, new_name)
                try:
                    os.rename(old_path, new_path)
                    # Also try to rename thumbnail if it exists
                    thumb_old = old_path + ".thumb.jpg"
                    if os.path.exists(thumb_old):
                        os.rename(thumb_old, new_path + ".thumb.jpg")
                    self.asset_modified.emit()
                except Exception as e:
                    QtWidgets.QMessageBox.warning(self, "Error", f"Failed to rename:\n{e}")
                    
        elif action == action_delete:
            resp = QtWidgets.QMessageBox.question(
                self, "Delete Asset", f"Are you sure you want to delete {item.text()}?",
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No
            )
            if resp == QtWidgets.QMessageBox.Yes:
                try:
                    os.remove(data["path"])
                    thumb_old = data["path"] + ".thumb.jpg"
                    if os.path.exists(thumb_old):
                        os.remove(thumb_old)
                    self.asset_modified.emit()
                except Exception as e:
                    QtWidgets.QMessageBox.warning(self, "Error", f"Failed to delete:\n{e}")

    def _on_double_click(self, item):
        data = item.data(QtCore.Qt.UserRole)
        if data:
            if data.get("is_folder", False):
                self.folder_entered.emit(data["path"])
                return
            ext = data.get("ext", "").lower()
            if ext in [".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".exr", ".hdr"]:
                from CrucibleLightStudio.ui.widgets.image_editor import ImageEditorDialog
                dlg = ImageEditorDialog(data["path"], self)
                if dlg.exec_() == QtWidgets.QDialog.Accepted:
                    edited_path = dlg.get_edited_path()
                    if edited_path != data["path"]:
                        data["path"] = edited_path
                        self.asset_modified.emit() # Refresh library to show the new edited file
                    self.assign_to_crucible_light_requested.emit(data)
                    self.asset_selected.emit(data)
                return
            
            # For non-images or builder, emit both
            self.assign_to_crucible_light_requested.emit(data)
            self.asset_selected.emit(data)


class PresetListWidget(QtWidgets.QTreeWidget):
    """Tree widget for light presets with folder support and audition-on-hover."""

    preset_selected = QtCore.Signal(str, dict)  # name, preset_data
    asset_modified = QtCore.Signal()            # Emit when tree structure changes

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.itemDoubleClicked.connect(self._on_double_click)
        self.setHeaderHidden(True)
        self.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._on_context_menu)
        
        # Enable drag and drop
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDragDropMode(QtWidgets.QAbstractItemView.InternalMove)

    def dropEvent(self, event):
        import os
        import shutil
        from CrucibleLightStudio.core import preset_manager
        
        target_item = self.itemAt(event.pos())
        
        target_rel_path = ""
        if target_item:
            data = target_item.data(0, QtCore.Qt.UserRole)
            if data is None and target_item.text(0) != "Built-in":
                # Target is a folder
                target_rel_path = target_item.data(0, QtCore.Qt.UserRole + 1) or ""
            else:
                # Target is a file, use its parent's folder
                parent = target_item.parent()
                if parent and parent.text(0) != "Built-in":
                    target_rel_path = parent.data(0, QtCore.Qt.UserRole + 1) or ""
                    
        # Block dropping onto Built-in group
        if target_item and (target_item.text(0) == "Built-in" or 
            (target_item.parent() and target_item.parent().text(0) == "Built-in")):
            event.ignore()
            return
            
        dest_dir = os.path.join(preset_manager.RIG_DIR, target_rel_path)
        os.makedirs(dest_dir, exist_ok=True)
        moved_anything = False
        
        for item in self.selectedItems():
            # Don't move Built-in items
            if item.text(0) == "Built-in" or (item.parent() and item.parent().text(0) == "Built-in"):
                continue
                
            data = item.data(0, QtCore.Qt.UserRole)
            if data and "_filepath" in data:
                # It's a file
                src_path = data["_filepath"]
                if os.path.exists(src_path):
                    dst_path = os.path.join(dest_dir, os.path.basename(src_path))
                    if src_path != dst_path:
                        try:
                            shutil.move(src_path, dst_path)
                            moved_anything = True
                        except Exception as e: 
                            QtWidgets.QMessageBox.warning(self, "Move Error", f"Failed to move file:\n{e}")
            elif data is None:
                # It's a folder
                rel_path = item.data(0, QtCore.Qt.UserRole + 1)
                if rel_path:
                    src_path = os.path.join(preset_manager.RIG_DIR, rel_path)
                    if os.path.exists(src_path):
                        dst_path = os.path.join(dest_dir, os.path.basename(src_path))
                        # Prevent moving into itself
                        if not dst_path.startswith(src_path):
                            try:
                                shutil.move(src_path, dst_path)
                                moved_anything = True
                            except Exception as e: 
                                QtWidgets.QMessageBox.warning(self, "Move Error", f"Failed to move folder:\n{e}")
                            
        if moved_anything:
            # We must ignore the event so Qt doesn't do its own visual move which conflicts with our refresh
            event.ignore()
            self.asset_modified.emit()
        else:
            event.ignore()

    def load_presets(self):
        """Load all built-in and user presets."""
        self.clear()
        presets = preset_manager.get_light_presets()
        self._populate_tree(self.invisibleRootItem(), presets, "")
        self.expandAll()

    def _populate_tree(self, parent_item, data_dict, current_path):
        for name, data in sorted(data_dict.items()):
            item = QtWidgets.QTreeWidgetItem(parent_item, [name])
            if isinstance(data, dict) and "light_type" not in data and "lights" not in data:
                # It's a folder
                # Set flags to make folder droppable
                item.setFlags(item.flags() | QtCore.Qt.ItemIsDropEnabled)
                item.setIcon(0, self.style().standardIcon(QtWidgets.QStyle.SP_DirIcon))
                folder_path = os.path.join(current_path, name) if current_path else name
                item.setData(0, QtCore.Qt.UserRole + 1, folder_path) # Store relative folder path
                self._populate_tree(item, data, folder_path)
            else:
                # It's a preset or rig
                # Disable drop onto files so they act only as draggable leaves
                item.setFlags(item.flags() & ~QtCore.Qt.ItemIsDropEnabled)
                item.setData(0, QtCore.Qt.UserRole, data)
                light_type = data.get("light_type", "Rig")
                item.setToolTip(0, f"Type: {light_type}")
                # Color-code icon by light type
                icon = self._type_icon(light_type)
                item.setIcon(0, icon)

    def _type_icon(self, light_type):
        """Small icon representing the light type."""
        pixmap = QtGui.QPixmap(20, 20)
        colors = {
            "RectLight": QtGui.QColor(230, 126, 34),
            "DiskLight": QtGui.QColor(52, 152, 219),
            "SphereLight": QtGui.QColor(46, 204, 113),
            "CylinderLight": QtGui.QColor(155, 89, 182),
            "DistantLight": QtGui.QColor(241, 196, 15),
            "DomeLight": QtGui.QColor(149, 165, 166),
            "Rig": QtGui.QColor(255, 100, 100),
        }
        pixmap.fill(colors.get(light_type, QtGui.QColor(100, 100, 100)))
        return QtGui.QIcon(pixmap)

    def _on_context_menu(self, pos):
        item = self.itemAt(pos)
        menu = QtWidgets.QMenu(self)
        
        action_new_folder = menu.addAction("New Folder")
        action_new_folder.triggered.connect(lambda: self._on_new_folder(item))
        
        has_deletable = False
        for sel in self.selectedItems():
            data = sel.data(0, QtCore.Qt.UserRole)
            if data and "_filepath" in data:
                has_deletable = True
                break
                
        if has_deletable:
            action_delete = menu.addAction("Delete Selected Presets")
            action_delete.triggered.connect(self._on_delete_selected)
            
        if item:
            data = item.data(0, QtCore.Qt.UserRole)
            if data is None and item.text(0) != "Built-in":
                menu.addSeparator()
                action_apply_folder = menu.addAction("Apply All Lights in Folder")
                action_apply_folder.triggered.connect(lambda: self._on_apply_folder(item))
                
                menu.addSeparator()
                action_rename = menu.addAction("Rename Folder")
                action_rename.triggered.connect(lambda: self._on_rename_folder(item))
                
                action_delete_folder = menu.addAction("Delete Folder")
                action_delete_folder.triggered.connect(lambda: self._on_delete_folder(item))
                
        menu.exec_(self.mapToGlobal(pos))

    def _on_apply_folder(self, item):
        rel_path = item.data(0, QtCore.Qt.UserRole + 1)
        if not rel_path: return
        import os, glob, json
        from CrucibleLightStudio.core import preset_manager
        folder_path = os.path.join(preset_manager.RIG_DIR, rel_path)
        if not os.path.isdir(folder_path): return
        
        # Look for rig json first
        rig_files = glob.glob(os.path.join(folder_path, "*_Rig.json")) + glob.glob(os.path.join(folder_path, "All_Lights*.json"))
        if rig_files:
            try:
                with open(rig_files[0], "r", encoding="utf-8") as f:
                    data = json.load(f)
                self.preset_selected.emit(item.text(0), data)
                return
            except: pass
            
        # Otherwise collect all .json files in folder
        lights = []
        for jf in sorted(glob.glob(os.path.join(folder_path, "*.json"))):
            try:
                with open(jf, "r", encoding="utf-8") as f:
                    ldata = json.load(f)
                if isinstance(ldata, dict) and ("light_type" in ldata or "name" in ldata):
                    lights.append(ldata)
            except: pass
        if lights:
            self.preset_selected.emit(item.text(0), {"rig_name": item.text(0), "lights": lights})

    def _on_new_folder(self, item):
        import os
        from CrucibleLightStudio.core import preset_manager
        
        base_dir = preset_manager.RIG_DIR
        if item and item.data(0, QtCore.Qt.UserRole + 1):
            base_dir = os.path.join(base_dir, item.data(0, QtCore.Qt.UserRole + 1))
            
        name, ok = QtWidgets.QInputDialog.getText(self, "New Folder", "Folder Name:")
        if ok and name:
            os.makedirs(os.path.join(base_dir, name), exist_ok=True)
            self.asset_modified.emit()

    def _on_delete_selected(self):
        import os
        paths_to_delete = []
        for item in self.selectedItems():
            data = item.data(0, QtCore.Qt.UserRole)
            if data and "_filepath" in data:
                paths_to_delete.append(data["_filepath"])
                
        if not paths_to_delete: return
        
        reply = QtWidgets.QMessageBox.question(
            self, "Delete Presets", 
            f"Are you sure you want to delete {len(paths_to_delete)} preset(s)?\nThis cannot be undone.",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No
        )
        if reply == QtWidgets.QMessageBox.Yes:
            for p in paths_to_delete:
                try: os.remove(p)
                except: pass
            self.asset_modified.emit()

    def _on_rename_folder(self, item):
        import os
        import shutil
        from CrucibleLightStudio.core import preset_manager
        old_rel_path = item.data(0, QtCore.Qt.UserRole + 1)
        if not old_rel_path: return
        
        old_full_path = os.path.join(preset_manager.RIG_DIR, old_rel_path)
        new_name, ok = QtWidgets.QInputDialog.getText(self, "Rename Folder", "New Name:", text=item.text(0))
        if ok and new_name and new_name != item.text(0):
            parent_dir = os.path.dirname(old_full_path)
            new_full_path = os.path.join(parent_dir, new_name)
            try:
                os.rename(old_full_path, new_full_path)
                self.asset_modified.emit()
            except Exception as e:
                QtWidgets.QMessageBox.warning(self, "Error", f"Could not rename folder: {e}")

    def _on_delete_folder(self, item):
        import os
        import shutil
        from CrucibleLightStudio.core import preset_manager
        rel_path = item.data(0, QtCore.Qt.UserRole + 1)
        if not rel_path: return
        
        full_path = os.path.join(preset_manager.RIG_DIR, rel_path)
        reply = QtWidgets.QMessageBox.question(
            self, "Delete Folder",
            f"Are you sure you want to delete the folder '{item.text(0)}' and ALL its contents?",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No
        )
        if reply == QtWidgets.QMessageBox.Yes:
            try:
                shutil.rmtree(full_path)
                self.asset_modified.emit()
            except Exception as e:
                QtWidgets.QMessageBox.warning(self, "Error", f"Could not delete folder: {e}")

    def _on_double_click(self, item, column):
        data = item.data(0, QtCore.Qt.UserRole)
        if data:
            self.preset_selected.emit(item.text(0), data)


class AssetLibraryWidget(QtWidgets.QWidget):
    """
    Tabbed asset library with dedicated tabs for:
    - HDRIs
    - Gobos (dedicated tab as requested)
    - Scrim Lights
    - Light Presets
    - Lighting Rigs
    """

    asset_applied = QtCore.Signal(str, str)  # category, path
    send_to_match_requested = QtCore.Signal(dict)
    assign_to_crucible_light_requested = QtCore.Signal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()

    def _build_ui(self):
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Header
        header = QtWidgets.QHBoxLayout()
        title = QtWidgets.QLabel("Asset Library")
        title.setObjectName("section_title")
        header.addWidget(title)

        self.btn_set_paths = QtWidgets.QPushButton("Set Paths...")
        self.btn_set_paths.setToolTip("Configure library directories")
        self.btn_set_paths.setFixedHeight(22)
        
        path_menu = QtWidgets.QMenu(self.btn_set_paths)
        action_hdri = path_menu.addAction("Set HDRI Directory...")
        action_hdri.triggered.connect(self._on_set_hdri_clicked)
        action_gobo = path_menu.addAction("Set Gobo Directory...")
        action_gobo.triggered.connect(self._on_set_gobo_clicked)
        action_scrim = path_menu.addAction("Set Scrim Directory...")
        action_scrim.triggered.connect(self._on_set_scrim_clicked)
        action_preset = path_menu.addAction("Set Presets Directory...")
        action_preset.triggered.connect(self._on_set_preset_clicked)
        self.btn_set_paths.setMenu(path_menu)
        
        # Force the menu to show when clicking the button body, not just the arrow
        self.btn_set_paths.clicked.connect(self.btn_set_paths.showMenu)
        
        # Zoom Slider
        self.zoom_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.zoom_slider.setRange(40, 512)
        self.zoom_slider.setValue(96)
        self.zoom_slider.setFixedWidth(100)
        self.zoom_slider.setToolTip("Resize thumbnails")
        self.zoom_slider.valueChanged.connect(self._on_zoom_changed)
        
        header.addStretch()
        header.addWidget(QtWidgets.QLabel("Zoom:"))
        header.addWidget(self.zoom_slider)
        # add a little spacing before the paths button
        header.addSpacing(10)
        header.addWidget(self.btn_set_paths)
        layout.addLayout(header)

        # Tabs
        self.tabs = QtWidgets.QTabWidget()

        # HDRI tab container with folder navigation & splitter
        self._current_hdri_dir = preset_manager.HDRI_DIR
        self.hdri_container = QtWidgets.QWidget()
        hdri_vbox = QtWidgets.QVBoxLayout(self.hdri_container)
        hdri_vbox.setContentsMargins(6, 6, 6, 4)
        hdri_vbox.setSpacing(6)

        # Folder Navigation Bar with comfortable spacing and clear button visibility
        self.hdri_nav_widget = QtWidgets.QWidget()
        self.hdri_nav_widget.setFixedHeight(34)
        self.hdri_nav_widget.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        self.hdri_nav_bar = QtWidgets.QHBoxLayout(self.hdri_nav_widget)
        self.hdri_nav_bar.setContentsMargins(2, 3, 2, 3)
        self.hdri_nav_bar.setSpacing(8)

        nav_btn_style = """
            QPushButton {
                background-color: #2a2a2a;
                color: #dddddd;
                border: 1px solid #484848;
                border-radius: 4px;
                padding: 0 11px;
                font-size: 11px;
                font-weight: 500;
            }
            QPushButton:hover {
                background-color: #383838;
                border-color: #666666;
                color: #ffffff;
            }
            QPushButton:checked {
                background-color: #3a2818;
                border: 1px solid #e67e22;
                color: #f39c12;
                font-weight: bold;
            }
            QPushButton:pressed {
                background-color: #1f1f1f;
            }
        """

        self.btn_toggle_tree = QtWidgets.QPushButton("Folders")
        self.btn_toggle_tree.setCheckable(True)
        self.btn_toggle_tree.setChecked(True)
        self.btn_toggle_tree.setFixedHeight(26)
        self.btn_toggle_tree.setStyleSheet(nav_btn_style)
        self.btn_toggle_tree.setToolTip("Show / Hide folder tree sidebar")
        self.btn_toggle_tree.toggled.connect(self._toggle_hdri_tree)
        self.hdri_nav_bar.addWidget(self.btn_toggle_tree)

        self.btn_hdri_root = QtWidgets.QPushButton("All")
        self.btn_hdri_root.setFixedHeight(26)
        self.btn_hdri_root.setStyleSheet(nav_btn_style)
        self.btn_hdri_root.setToolTip("Return to root HDRI directory")
        self.btn_hdri_root.clicked.connect(self._on_hdri_root_clicked)
        self.hdri_nav_bar.addWidget(self.btn_hdri_root)

        self.btn_hdri_up = QtWidgets.QPushButton("Up")
        self.btn_hdri_up.setFixedHeight(26)
        self.btn_hdri_up.setStyleSheet(nav_btn_style)
        self.btn_hdri_up.setToolTip("Navigate up to parent folder")
        self.btn_hdri_up.clicked.connect(self._on_hdri_up_clicked)
        self.hdri_nav_bar.addWidget(self.btn_hdri_up)

        self.hdri_nav_bar.addSpacing(6)

        self.lbl_hdri_path = QtWidgets.QLabel("HDRIs")
        self.lbl_hdri_path.setStyleSheet("font-weight: bold; color: #e67e22; padding-left: 8px;")
        self.lbl_hdri_path.setFixedHeight(26)
        self.hdri_nav_bar.addWidget(self.lbl_hdri_path, 1)

        hdri_vbox.addWidget(self.hdri_nav_widget, 0)

        # Splitter: Tree (left) + Grid (right) - freely adjustable
        self.hdri_splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        self.hdri_splitter.setChildrenCollapsible(False)
        self.hdri_splitter.setHandleWidth(6)
        self.hdri_splitter.setStyleSheet("""
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

        self.hdri_tree = QtWidgets.QTreeWidget()
        self.hdri_tree.setHeaderLabel("HDRI Folders")
        self.hdri_tree.setMinimumWidth(100)
        # Fully adjustable: no setMaximumWidth limit!
        self.hdri_tree.setStyleSheet("""
            QTreeWidget {
                background-color: #1a1a1a;
                color: #cccccc;
                border: 1px solid #333333;
                border-radius: 3px;
                font-size: 11px;
            }
            QTreeWidget::item {
                height: 22px;
                padding: 2px 4px;
            }
            QTreeWidget::item:hover {
                background-color: #2a2a2a;
                color: #ffffff;
            }
            QTreeWidget::item:selected {
                background-color: #d35400;
                color: #ffffff;
                font-weight: bold;
            }
            QHeaderView::section {
                background-color: #222222;
                color: #888888;
                border: none;
                border-bottom: 1px solid #333333;
                padding: 4px;
                font-size: 11px;
            }
        """)
        self.hdri_tree.itemClicked.connect(self._on_hdri_tree_clicked)
        self.hdri_splitter.addWidget(self.hdri_tree)

        self.hdri_grid = AssetGridWidget()
        self.hdri_grid.asset_modified.connect(self.refresh_library)
        self.hdri_grid.send_to_match_requested.connect(self.send_to_match_requested.emit)
        self.hdri_grid.assign_to_crucible_light_requested.connect(self.assign_to_crucible_light_requested.emit)
        self.hdri_grid.asset_selected.connect(
            lambda a: self.asset_applied.emit("hdri", a["path"]))
        self.hdri_grid.folder_entered.connect(self._on_hdri_folder_entered)

        self.hdri_splitter.addWidget(self.hdri_grid)
        self.hdri_splitter.setStretchFactor(0, 0)
        self.hdri_splitter.setStretchFactor(1, 1)
        self.hdri_splitter.setSizes([220, 650])

        hdri_vbox.addWidget(self.hdri_splitter, 1)
        self.tabs.addTab(self.hdri_container, "HDRIs")

        # Gobo tab (dedicated as requested)
        self.gobo_grid = AssetGridWidget()
        self.gobo_grid.asset_modified.connect(self.refresh_library)
        self.gobo_grid.setIconSize(QtCore.QSize(80, 80))
        self.gobo_grid.assign_to_crucible_light_requested.connect(self.assign_to_crucible_light_requested.emit)
        self.gobo_grid.asset_selected.connect(
            lambda a: self.asset_applied.emit("gobo", a["path"]))
        self.tabs.addTab(self.gobo_grid, "Gobos")

        # Scrim tab
        self.scrim_grid = AssetGridWidget()
        self.scrim_grid.asset_modified.connect(self.refresh_library)
        self.scrim_grid.assign_to_crucible_light_requested.connect(self.assign_to_crucible_light_requested.emit)
        self.scrim_grid.asset_selected.connect(
            lambda a: self.asset_applied.emit("scrim", a["path"]))
        self.tabs.addTab(self.scrim_grid, "Scrims")

        # Presets tab
        self.preset_list = PresetListWidget()
        self.preset_list.preset_selected.connect(self._on_preset_selected)
        self.preset_list.asset_modified.connect(self.refresh_library)
        self.tabs.addTab(self.preset_list, "Presets")

        layout.addWidget(self.tabs, 1)

    def stop_workers(self):
        for grid in (getattr(self, "hdri_grid", None), getattr(self, "gobo_grid", None), getattr(self, "scrim_grid", None)):
            if grid and hasattr(grid, "stop_worker"):
                grid.stop_worker()

    def show_presets_tab(self):
        """Switch active tab to Presets."""
        if hasattr(self, "preset_list") and hasattr(self, "tabs"):
            self.tabs.setCurrentWidget(self.preset_list)

    def _on_zoom_changed(self, value):
        """Dynamically resize the icons across all grids."""
        rect_size = QtCore.QSize(value, int(value * 0.66))
        square_size = QtCore.QSize(value, value)
        
        self.hdri_grid.setIconSize(rect_size)
        self.hdri_grid.setGridSize(QtCore.QSize(value + 16, int(value * 0.66) + 32))
        
        self.gobo_grid.setIconSize(square_size)
        self.gobo_grid.setGridSize(QtCore.QSize(value + 16, value + 32))
        
        self.scrim_grid.setIconSize(rect_size)
        self.scrim_grid.setGridSize(QtCore.QSize(value + 16, int(value * 0.66) + 32))

    def refresh_library(self):
        """Scan all library directories and populate grids."""
        self._populate_hdri_tree()
        if not hasattr(self, '_current_hdri_dir') or not os.path.isdir(self._current_hdri_dir):
            self._current_hdri_dir = preset_manager.HDRI_DIR
        self._on_hdri_folder_entered(self._current_hdri_dir)
        self.gobo_grid.load_assets(preset_manager.get_gobos())
        
        # Inject custom builder into scrims
        scrims = preset_manager.get_scrims()
        scrims.insert(0, {
            "name": "+ Custom Gradient",
            "path": "__BUILDER__",
            "ext": ".builder"
        })
        self.scrim_grid.load_assets(scrims)
        
        self.preset_list.load_presets()

    def _on_preset_selected(self, name, data):
        """When a preset is double-clicked, emit for the main panel."""
        import json
        self.asset_applied.emit("preset", json.dumps(data))
        
    def _on_set_hdri_clicked(self):
        """Open a dialog to select the HDRI directory."""
        dir_path = QtWidgets.QFileDialog.getExistingDirectory(
            self, "Select HDRI Library Folder", preset_manager.HDRI_DIR)
            
        if dir_path:
            preset_manager.set_hdri_dir(dir_path)
            self.refresh_library()
            
    def _on_set_gobo_clicked(self):
        """Open a dialog to select the Gobo directory."""
        dir_path = QtWidgets.QFileDialog.getExistingDirectory(
            self, "Select Gobo Library Folder", preset_manager.GOBO_DIR)
            
        if dir_path:
            preset_manager.set_gobo_dir(dir_path)
            self.refresh_library()
            
    def _on_set_scrim_clicked(self):
        """Open a dialog to select the Scrim directory."""
        dir_path = QtWidgets.QFileDialog.getExistingDirectory(
            self, "Select Scrim Library Folder", preset_manager.SCRIM_DIR)
            
        if dir_path:
            preset_manager.set_scrim_dir(dir_path)
            self.refresh_library()
            
    def _on_set_preset_clicked(self):
        """Open a dialog to select the Presets directory."""
        dir_path = QtWidgets.QFileDialog.getExistingDirectory(
            self, "Select Presets Library Folder", preset_manager.RIG_DIR)
            
        if dir_path:
            preset_manager.set_rig_dir(dir_path)
            self.refresh_library()

    def _toggle_hdri_tree(self, checked):
        self.hdri_tree.setVisible(checked)

    def _on_hdri_root_clicked(self):
        self._on_hdri_folder_entered(preset_manager.HDRI_DIR)

    def _on_hdri_up_clicked(self):
        cur = os.path.normpath(self._current_hdri_dir)
        root = os.path.normpath(preset_manager.HDRI_DIR)
        if cur != root and cur.startswith(root):
            parent = os.path.dirname(cur)
            if len(parent) >= len(root):
                self._on_hdri_folder_entered(parent)
                return
        self._on_hdri_folder_entered(root)

    def _on_hdri_folder_entered(self, folder_path):
        if not os.path.isdir(folder_path):
            folder_path = preset_manager.HDRI_DIR
        self._current_hdri_dir = folder_path

        # Update breadcrumb label
        root = os.path.normpath(preset_manager.HDRI_DIR)
        cur = os.path.normpath(folder_path)
        if cur == root:
            self.lbl_hdri_path.setText("HDRIs (Root)")
        else:
            rel = os.path.relpath(cur, root).replace("\\", " / ")
            self.lbl_hdri_path.setText(f"HDRIs / {rel}")

        # Load grid assets for this folder
        items = preset_manager.get_hdris(folder_path)
        self.hdri_grid.load_assets(items)

        # Select corresponding item in tree if found
        self._select_tree_path(folder_path)

    def _on_hdri_tree_clicked(self, item, column):
        path = item.data(0, QtCore.Qt.UserRole)
        if path:
            self._on_hdri_folder_entered(path)

    def _populate_hdri_tree(self):
        self.hdri_tree.clear()
        root_dir = preset_manager.HDRI_DIR
        root_item = QtWidgets.QTreeWidgetItem(self.hdri_tree, ["All HDRIs"])
        root_item.setData(0, QtCore.Qt.UserRole, root_dir)
        root_item.setIcon(0, self.style().standardIcon(QtWidgets.QStyle.SP_DirIcon))

        tree_data = preset_manager.get_hdri_folder_tree(root_dir)
        self._populate_hdri_tree_recursive(root_item, tree_data, root_dir)
        self.hdri_tree.expandItem(root_item)

    def _populate_hdri_tree_recursive(self, parent_item, tree_dict, current_dir):
        for name, sub_dict in sorted(tree_dict.items()):
            full_path = os.path.join(current_dir, name)
            item = QtWidgets.QTreeWidgetItem(parent_item, [name])
            item.setData(0, QtCore.Qt.UserRole, full_path)
            item.setIcon(0, self.style().standardIcon(QtWidgets.QStyle.SP_DirIcon))
            if sub_dict:
                self._populate_hdri_tree_recursive(item, sub_dict, full_path)

    def _select_tree_path(self, target_path):
        target_norm = os.path.normpath(target_path)
        def find_item(parent_item):
            for i in range(parent_item.childCount()):
                child = parent_item.child(i)
                cpath = child.data(0, QtCore.Qt.UserRole)
                if cpath and os.path.normpath(cpath) == target_norm:
                    self.hdri_tree.setCurrentItem(child)
                    return True
                if find_item(child):
                    return True
            return False

        root = self.hdri_tree.topLevelItem(0)
        if root:
            if os.path.normpath(root.data(0, QtCore.Qt.UserRole) or "") == target_norm:
                self.hdri_tree.setCurrentItem(root)
            else:
                find_item(root)
