"""
Offline Architecture, USD/Solaris, and PCoIP :100 Safety Invariant Test Suite
for DomeBreaker.

Runs completely offline (no live Houdini / USD / CUDA / network required)
via:
    pytest -v tests/test_architecture_invariants.py
"""

import ast
import importlib
import importlib.abc
import inspect
import io
import json
import math
import os
import subprocess
import sys
import types
import xml.etree.ElementTree as ET
import zipfile

import numpy as np
import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PYTHON_ROOT = os.path.join(REPO_ROOT, "python")
SCRIPTS_ROOT = os.path.join(REPO_ROOT, "scripts")
HOUDINI_ROOT = os.path.join(REPO_ROOT, "houdini")

if PYTHON_ROOT not in sys.path:
    sys.path.insert(0, PYTHON_ROOT)


def _iter_source_files(roots, exts):
    for root in roots:
        if not os.path.isdir(root):
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in ("__pycache__", ".git", ".pytest_cache")]
            for fname in sorted(filenames):
                if any(fname.endswith(ext) for ext in exts):
                    yield os.path.join(dirpath, fname)


class _BlockingModuleFinder(importlib.abc.MetaPathFinder):
    """Fail immediately if a forbidden module (e.g. cv2 inside Houdini) is imported."""

    def __init__(self, forbidden_roots):
        self.forbidden_roots = tuple(forbidden_roots)
        self.attempts = []

    def find_spec(self, fullname, path=None, target=None):
        for mod_name in self.forbidden_roots:
            if fullname == mod_name or fullname.startswith(mod_name + "."):
                self.attempts.append(fullname)
                raise AssertionError(f"Forbidden runtime import attempted: '{fullname}'")
        return None


def _ensure_fake_hou_module():
    hou = types.ModuleType("hou")
    hou.getenv = lambda name, default="": ""
    hou.expandString = lambda s: "" if s is None else str(s)
    hou.node = lambda path: None
    hou.NodeError = RuntimeError
    hou.severityType = types.SimpleNamespace(Message=0, Warning=1, Error=2)
    hou.ui = types.SimpleNamespace(displayMessage=lambda *a, **kw: None)
    hou.hipFile = types.SimpleNamespace(
        path=lambda: "",
        addEventCallback=lambda *a, **kw: None,
        removeEventCallback=lambda *a, **kw: None,
    )
    hou.hipFileEventType = types.SimpleNamespace(BeforeSave=1, AfterSave=2, AfterLoad=3)
    return hou


def _ensure_fake_qt_if_absent(monkeypatch):
    try:
        import PySide6  # noqa: F401
        return
    except ImportError:
        pass
    try:
        import PySide2  # noqa: F401
        return
    except ImportError:
        pass

    class _DummySignal:
        def __init__(self, *args):
            self._slots = []

        def connect(self, fn):
            self._slots.append(fn)

        def disconnect(self, fn=None):
            if fn is None:
                self._slots.clear()
            elif fn in self._slots:
                self._slots.remove(fn)

        def emit(self, *args):
            for fn in list(self._slots):
                fn(*args)

    class _DummyBase:
        def __init__(self, *args, **kwargs):
            pass

        def __getattr__(self, name):
            return lambda *a, **k: None

    qt_core = types.ModuleType("PySide6.QtCore")
    qt_core.QObject = _DummyBase
    qt_core.QThread = _DummyBase
    qt_core.QTimer = _DummyBase
    qt_core.QMutex = _DummyBase
    qt_core.Signal = _DummySignal
    qt_core.Qt = types.SimpleNamespace(AlignCenter=0, KeepAspectRatio=0, SmoothTransformation=0)

    qt_gui = types.ModuleType("PySide6.QtGui")
    qt_gui.QIcon = _DummyBase
    qt_gui.QPixmap = _DummyBase
    qt_gui.QImage = _DummyBase
    qt_gui.QColor = _DummyBase

    qt_widgets = types.ModuleType("PySide6.QtWidgets")
    for cls_name in (
        "QWidget", "QDialog", "QFrame", "QLabel", "QPushButton", "QCheckBox",
        "QComboBox", "QVBoxLayout", "QHBoxLayout", "QGridLayout", "QTabWidget",
        "QScrollArea", "QSplitter", "QTableWidget", "QListWidget", "QApplication",
        "QGroupBox", "QProgressBar", "QLineEdit", "QDoubleSpinBox", "QSpinBox",
        "QSlider", "QTextEdit", "QFileDialog", "QMessageBox", "QToolButton",
        "QHeaderView", "QAbstractItemView", "QSizePolicy",
    ):
        setattr(qt_widgets, cls_name, _DummyBase)

    pyside6 = types.ModuleType("PySide6")
    pyside6.QtCore = qt_core
    pyside6.QtGui = qt_gui
    pyside6.QtWidgets = qt_widgets

    monkeypatch.setitem(sys.modules, "PySide6", pyside6)
    monkeypatch.setitem(sys.modules, "PySide6.QtCore", qt_core)
    monkeypatch.setitem(sys.modules, "PySide6.QtGui", qt_gui)
    monkeypatch.setitem(sys.modules, "PySide6.QtWidgets", qt_widgets)


# ---------------------------------------------------------------------------
# 1. Static Repo & PCoIP :100 Safety Scans
# ---------------------------------------------------------------------------


def test_version_consistency():
    with open(os.path.join(REPO_ROOT, "VERSION"), "r", encoding="utf-8") as f:
        assert f.read().strip() == "v2.0.5"

    with open(os.path.join(PYTHON_ROOT, "hdri_match_solaris", "__init__.py"), "r", encoding="utf-8") as f:
        sol_init = f.read()
    assert '__version__ = "2.0.5"' in sol_init
    assert '__houdini_min__ = "20.0"' in sol_init

    with open(os.path.join(PYTHON_ROOT, "domebreaker", "__init__.py"), "r", encoding="utf-8") as f:
        db_init = f.read()
    assert '__version__ = "2.0.5"' in db_init

    with open(os.path.join(PYTHON_ROOT, "hdri_match_solaris", "panel_ui.py"), "r", encoding="utf-8") as f:
        panel_src = f.read()
    assert "Loading v2.0.5..." in panel_src
    assert "v2.0.5 - Solaris USD Lighting & Environment Suite" in panel_src


def test_no_c1_mojibake_orphan_fe0f_or_4byte_emojis():
    """Enforce PCoIP virtual X11 (:100) glyph safety across code, XML, and shell installers."""
    files = list(_iter_source_files([PYTHON_ROOT, SCRIPTS_ROOT, HOUDINI_ROOT], (".py", ".sh", ".shelf", ".pypanel")))
    for sh_name in ("install.sh", "uninstall.sh", "update.sh"):
        sh_path = os.path.join(REPO_ROOT, sh_name)
        if os.path.isfile(sh_path):
            files.append(sh_path)

    assert files, "No source files found to scan"

    strict_bmp_symbol_files = {
        os.path.normpath(os.path.join(PYTHON_ROOT, "hdri_match_solaris", "panel_ui.py")),
        os.path.normpath(os.path.join(PYTHON_ROOT, "hdri_match_solaris", "splatforge", "__init__.py")),
        os.path.normpath(os.path.join(REPO_ROOT, "install.sh")),
        os.path.normpath(os.path.join(REPO_ROOT, "uninstall.sh")),
        os.path.normpath(os.path.join(REPO_ROOT, "update.sh")),
    }

    for path in files:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()

        c1_hits = [hex(ord(c)) for c in text if 0x0080 <= ord(c) <= 0x009F]
        assert not c1_hits, f"C1 mojibake bytes {c1_hits[:5]} found in {path}"
        assert "\ufe0f" not in text, f"Orphan variation selector U+FE0F found in {path}"

        supp_hits = [hex(ord(c)) for c in text if ord(c) >= 0x10000]
        assert not supp_hits, f"4-byte supplementary emoji {supp_hits[:5]} found in {path}"

        if os.path.normpath(path) in strict_bmp_symbol_files:
            sym_hits = [hex(ord(c)) for c in text if 0x2600 <= ord(c) <= 0x27BF]
            assert not sym_hits, f"Misc Symbol/Dingbat glyphs {sym_hits[:5]} found in {path}"


def test_no_hardcoded_developer_drive_paths():
    """Ensure no developer-specific Windows drive paths remain in source files."""
    forbidden_tokens = (
        "E:/" + "PROJECTS",
        "E:\\" + "PROJECTS",
        "e:/" + "PROJECTS",
        "D:\\" + "PROJECTS",
        "C:\\" + "PROJECTS",
        "HDRI_MATCH_SOLARIS/" + "scenes",
    )
    files = list(_iter_source_files([PYTHON_ROOT, SCRIPTS_ROOT, HOUDINI_ROOT], (".py", ".shelf", ".pypanel")))
    for path in files:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
        for tok in forbidden_tokens:
            assert tok not in text, f"Hardcoded developer path '{tok}' found in {path}"


def test_no_runtime_importlib_reload():
    """Verify no runtime importlib.reload() calls exist in Python runtime or script modules."""
    for path in _iter_source_files([PYTHON_ROOT, SCRIPTS_ROOT], (".py",)):
        with open(path, "r", encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=path)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Attribute) and func.attr == "reload":
                    pytest.fail(f"Unsafe runtime reload() call in {path}:{node.lineno}")
                if isinstance(func, ast.Name) and func.id == "reload":
                    pytest.fail(f"Unsafe builtin reload() call in {path}:{node.lineno}")


# ---------------------------------------------------------------------------
# 2. OpenImageIO Fail-Closed Checks & Contiguous Sanitization
# ---------------------------------------------------------------------------


def test_oiio_adapter_fail_closed_and_sanitization(monkeypatch, tmp_path):
    from hdri_match_solaris import oiio_adapter

    events = []

    def _make_fake_oiio(open_ok=True, write_ok=True, capture=None):
        class _FakeOutput:
            def open(self, path, spec):
                events.append(("open", path, spec))
                return open_ok

            def write_image(self, arr):
                events.append(("write", arr.shape, str(arr.dtype), arr.flags["C_CONTIGUOUS"]))
                if capture is not None:
                    capture.append(arr.copy())
                return write_ok

            def close(self):
                events.append(("close",))

            def geterror(self):
                return "mock oiio error"

        return types.SimpleNamespace(
            FLOAT="FLOAT",
            ImageSpec=lambda w, h, c, fmt: (w, h, c, fmt),
            ImageOutput=types.SimpleNamespace(create=lambda p: _FakeOutput()),
            geterror=lambda: "mock global oiio error",
        )

    out_file = str(tmp_path / "test_out.exr")
    img = np.zeros((4, 6, 3), dtype=np.float32)

    # Case A: open() fails -> must raise IOError and never call close()
    events.clear()
    monkeypatch.setattr(oiio_adapter, "HAS_OIIO", True)
    monkeypatch.setattr(oiio_adapter, "oiio", _make_fake_oiio(open_ok=False, write_ok=True), raising=False)
    with pytest.raises(IOError, match="failed to open"):
        oiio_adapter.save_image_with_oiio(img, out_file)
    assert ("close",) not in events

    # Case B: write_image() fails -> must raise IOError and must call close()
    events.clear()
    monkeypatch.setattr(oiio_adapter, "oiio", _make_fake_oiio(open_ok=True, write_ok=False), raising=False)
    with pytest.raises(IOError, match="failed to write"):
        oiio_adapter.save_image_with_oiio(img, out_file)
    assert ("close",) in events

    # Case C: Non-contiguous strided RGBA array with NaN/Inf -> sanitized C-contiguous float32 RGB
    events.clear()
    captured = []
    monkeypatch.setattr(oiio_adapter, "oiio", _make_fake_oiio(open_ok=True, write_ok=True, capture=captured), raising=False)
    dirty = np.ones((8, 12, 4), dtype=np.float64)[::2, ::-2, :]  # non-contiguous view
    dirty[0, 0, 0] = np.nan
    dirty[0, 1, 1] = np.inf
    dirty[0, 2, 2] = -np.inf
    assert not dirty.flags["C_CONTIGUOUS"]

    oiio_adapter.save_image_with_oiio(dirty, out_file)
    assert captured and captured[0].shape == (4, 6, 3)
    assert captured[0].dtype == np.float32
    assert np.all(np.isfinite(captured[0]))
    assert any(e[0] == "write" and e[3] is True for e in events)
    assert ("close",) in events


# ---------------------------------------------------------------------------
# 3. Houdini Runtime Isolation: Zero cv2 & Zero subprocess pip install
# ---------------------------------------------------------------------------


def test_cv2_never_imported_when_hou_present(monkeypatch):
    """When 'hou' is loaded in sys.modules, Houdini-safe modules must never import cv2."""
    blocker = _BlockingModuleFinder(("cv2",))
    monkeypatch.setattr(sys, "meta_path", [blocker] + list(sys.meta_path))
    monkeypatch.delitem(sys.modules, "cv2", raising=False)
    monkeypatch.setitem(sys.modules, "hou", _ensure_fake_hou_module())

    # Cold-import splatforge and confirm HAS_CV2 is False without triggering import cv2
    for key in list(sys.modules):
        if key == "hdri_match_solaris.splatforge" or key.startswith("hdri_match_solaris.splatforge."):
            monkeypatch.delitem(sys.modules, key, raising=False)
    import hdri_match_solaris.splatforge as sf
    assert sf.HAS_CV2 is False

    # autocrop
    from hdri_match.calibration import autocrop
    img = np.full((32, 64, 3), 0.2, dtype=np.float32)
    assert autocrop.AutoCropper.autocrop_ball(img) is img
    assert autocrop.AutoCropper.autocrop_macbeth(img) is img

    # sun_relighter CPU path
    from hdri_match.analysis import sun_relighter
    monkeypatch.setattr(sun_relighter, "_torch_cuda_available", lambda: False)
    relit = sun_relighter.SunRelighter.relight(img, 0.5, 0.25, 0.6, 0.4, radius_norm=0.05)
    assert relit.shape == img.shape

    # lop_light_extract fallback
    from hdri_match_solaris.lop_light_extract import _fallback_extract_lights
    hdri_emit = np.full((32, 64, 3), 0.05, dtype=np.float32)
    hdri_emit[14:18, 28:32, :] = 25.0
    lights = _fallback_extract_lights(hdri_emit, max_lights=4, threshold_ev=1.0)
    assert len(lights) >= 1

    # comfyui_bridge pure-NumPy helpers
    from hdri_match.ml.comfyui_bridge import ComfyUIBridge, _resize_bilinear_np
    resized = _resize_bilinear_np(img, 32, 16)
    assert resized.shape == (16, 32, 3)
    mask = np.zeros((32, 64), dtype=np.float32)
    mask[10:15, 20:25] = 1.0
    sim = ComfyUIBridge()._simulate_inpainting(img, mask)
    assert sim.shape == img.shape

    # html_report
    from hdri_match.io import html_report
    assert html_report._safe_get_cv2() is None
    _ = html_report.array_to_base64_jpg(img)

    assert not blocker.attempts


def test_genai_bridge_never_runs_subprocess_pip(monkeypatch):
    genai_path = os.path.join(PYTHON_ROOT, "hdri_match", "ml", "genai_bridge.py")
    with open(genai_path, "r", encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename=genai_path)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert all(alias.name != "subprocess" for alias in node.names)
        if isinstance(node, ast.ImportFrom):
            assert node.module != "subprocess"

    def _forbid_subproc(*args, **kwargs):
        raise AssertionError("Runtime pip subprocess must never be executed!")

    monkeypatch.setattr(subprocess, "check_call", _forbid_subproc)
    monkeypatch.setattr(subprocess, "run", _forbid_subproc)
    monkeypatch.setattr(subprocess, "Popen", _forbid_subproc)
    monkeypatch.setitem(sys.modules, "google.genai", None)
    monkeypatch.setitem(sys.modules, "google", None)

    from hdri_match.ml.genai_bridge import GenAIBridge
    img = np.zeros((16, 32, 3), dtype=np.float32)
    mask = np.ones((16, 32), dtype=np.float32)
    out, err = GenAIBridge(api_key="dummy").generate_inpaint(img, mask, "clear sky")
    assert out.shape == img.shape
    assert err is not None and "google-genai" in err


# ---------------------------------------------------------------------------
# 4. SunRelighter & Light Extraction Equirectangular Math
# ---------------------------------------------------------------------------


def test_sun_relighter_lazy_torch_and_no_vertical_pole_wrap(monkeypatch):
    # Ensure importing sun_relighter does NOT invoke torch.cuda.is_available() at module import time
    fake_torch = types.ModuleType("torch")
    cuda_called = []

    def _boom_cuda():
        cuda_called.append(True)
        raise AssertionError("torch.cuda.is_available() must not run at import time!")

    fake_torch.cuda = types.SimpleNamespace(is_available=_boom_cuda)
    monkeypatch.setitem(sys.modules, "torch", fake_torch)
    monkeypatch.delitem(sys.modules, "hdri_match.analysis.sun_relighter", raising=False)

    from hdri_match.analysis import sun_relighter
    assert not cuda_called

    # Now disable CUDA cleanly and verify moving zenith sun up does NOT wrap to nadir
    monkeypatch.setattr(sun_relighter, "_torch_cuda_available", lambda: False)
    h, w = 64, 128
    img = np.zeros((h, w, 3), dtype=np.float32)
    img[2:6, 60:68, :] = 60.0

    relit = sun_relighter.SunRelighter.relight(
        img, source_u=0.5, source_v=0.06, target_u=0.5, target_v=0.01, radius_norm=0.06, feather_norm=0.02
    )
    assert np.max(relit[-12:, :, :]) < 1e-4, "Sun stamp wrapped vertically across zenith to nadir!"


def test_fallback_light_extractor_seam_wrap_and_solid_angle():
    from hdri_match_solaris.lop_light_extract import _fallback_extract_lights

    # 1. Seam-straddling bright region across u=0 / u=1 must merge into 1 cluster
    img_seam = np.full((64, 128, 3), 0.05, dtype=np.float32)
    img_seam[30:35, :3, :] = 30.0
    img_seam[30:35, -3:, :] = 30.0
    lights_seam = _fallback_extract_lights(img_seam, max_lights=4, threshold_ev=2.0)
    assert len(lights_seam) == 1, f"Expected 1 seam-merged light, got {len(lights_seam)}"

    # 2. Equal pixel-patch near zenith vs equator: zenith patch subtends smaller solid angle
    img_zenith = np.full((64, 128, 3), 0.05, dtype=np.float32)
    img_zenith[1:5, 60:64, :] = 30.0
    img_eq = np.full((64, 128, 3), 0.05, dtype=np.float32)
    img_eq[30:34, 60:64, :] = 30.0

    lz = _fallback_extract_lights(img_zenith, max_lights=2, threshold_ev=2.0)
    le = _fallback_extract_lights(img_eq, max_lights=2, threshold_ev=2.0)
    assert len(lz) == 1 and len(le) == 1
    assert lz[0]["angular_size_deg"] < le[0]["angular_size_deg"]


def test_lop_light_extract_cook_clears_stale_children(monkeypatch, tmp_path):
    from hdri_match_solaris import lop_light_extract

    exr_path = str(tmp_path / "env.exr")
    with open(exr_path, "wb") as f:
        f.write(b"dummy")

    class _FakeChild:
        def __init__(self, path):
            self._path = path

        def GetPath(self):
            return self._path

    class _FakePrim:
        def __init__(self, valid=True, children=None):
            self._valid = valid
            self._children = list(children or [])

        def IsValid(self):
            return self._valid

        def GetChildren(self):
            return list(self._children)

    removed_paths = []
    defined_spheres = []

    class _FakeStage:
        def __init__(self):
            self.prims = {
                "/lights": _FakePrim(True),
                "/lights/extracted": _FakePrim(
                    True,
                    [_FakeChild("/lights/extracted/stale_01"), _FakeChild("/lights/extracted/stale_02")],
                ),
            }

        def GetPrimAtPath(self, path):
            return self.prims.get(path, _FakePrim(False))

        def RemovePrim(self, path):
            removed_paths.append(str(path))
            # Reflect removal of child under /lights/extracted
            parent = self.prims.get("/lights/extracted")
            if parent:
                parent._children = [c for c in parent._children if c.GetPath() != str(path)]

    stage = _FakeStage()
    parms = {
        "hdri_path": exr_path,
        "max_lights": 2,
        "threshold_ev": 3.0,
        "min_angular_size": 0.5,
        "light_distance": 50.0,
        "sun_as_distant": False,
    }
    fake_node = types.SimpleNamespace(
        editableStage=lambda: stage,
        parm=lambda name: types.SimpleNamespace(eval=lambda n=name: parms[n]) if name in parms else None,
    )

    monkeypatch.setitem(sys.modules, "hou", _ensure_fake_hou_module())
    loader_mod = types.ModuleType("hdri_match.io.loader")
    # Zero image -> no lights pass threshold -> still must clear stale /lights/extracted children!
    loader_mod.load_exr_to_numpy = lambda p: np.zeros((16, 32, 3), dtype=np.float32)
    monkeypatch.setitem(sys.modules, "hdri_match.io.loader", loader_mod)
    le_mod = types.ModuleType("hdri_match.analysis.light_extractor")
    le_mod.LightExtractor = types.SimpleNamespace(extract_lights=lambda *a, **kw: [])
    monkeypatch.setitem(sys.modules, "hdri_match.analysis.light_extractor", le_mod)

    from hdri_match_solaris import usd_helpers
    monkeypatch.setattr(usd_helpers, "ensure_scope", lambda st, p: None)
    monkeypatch.setattr(
        usd_helpers,
        "set_sphere_light",
        lambda st, path, **kw: defined_spheres.append(path),
    )

    lop_light_extract.cook(fake_node)
    assert removed_paths == ["/lights/extracted/stale_01", "/lights/extracted/stale_02"]
    assert defined_spheres == []


# ---------------------------------------------------------------------------
# 5. Calibration No Double-Application & No Recursive LOP Cook Loops
# ---------------------------------------------------------------------------


def test_lop_cook_files_have_no_modal_ui_or_parm_set():
    """Python LOP cook modules must never pop modal UI dialogs or call parm.set() inside cook()."""
    for rel in ("lop_calibrate.py", "lop_sun.py", "lop_light_extract.py", "lop_domelight.py"):
        path = os.path.join(PYTHON_ROOT, "hdri_match_solaris", rel)
        with open(path, "r", encoding="utf-8") as f:
            src = f.read()
        assert "displayMessage" not in src, f"Modal hou.ui.displayMessage found in {rel}"
        assert "sync_pipeline_to_parms" not in src, f"Recursive sync_pipeline_to_parms found in {rel}"

        tree = ast.parse(src, filename=path)
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "cook":
                for inner in ast.walk(node):
                    if isinstance(inner, ast.Call) and isinstance(inner.func, ast.Attribute):
                        if inner.func.attr == "set":
                            sub_src = ast.get_source_segment(src, inner) or ""
                            if ".parm(" in sub_src:
                                pytest.fail(f"Recursive parm.set() inside {rel}:cook(): {sub_src}")


def test_lop_calibrate_and_panel_gen_code_neutral_domelight(monkeypatch, tmp_path):
    from hdri_match_solaris import lop_calibrate, hda_utils, usd_helpers

    hdri_file = str(tmp_path / "raw_env.exr")
    with open(hdri_file, "wb") as f:
        f.write(b"exr")
    out_file = str(tmp_path / "raw_env_calibrated.exr")

    monkeypatch.setitem(sys.modules, "hou", _ensure_fake_hou_module())

    class _FakeState:
        ev_offset = 2.5
        black_offset = 0.01
        temperature = 1.2
        tint = -0.3
        hdri_yaw = 45.0

    class _FakePipeline:
        def __init__(self):
            self.state = _FakeState()

        def load_inputs(self, *a, **kw):
            pass

        def compute_calibration(self, **kw):
            pass

        def process_hdri(self, use_proxy=False):
            return np.ones((8, 16, 3), dtype=np.float32) * 0.5

    pipe_mod = types.ModuleType("hdri_match.core.pipeline")
    pipe_mod.CalibrationPipeline = _FakePipeline
    loader_mod = types.ModuleType("hdri_match.io.loader")
    loader_mod.load_exr_to_numpy = lambda p: np.ones((8, 16, 3), dtype=np.float32)
    exp_mod = types.ModuleType("hdri_match.io.exporter")
    exp_mod.save_numpy_to_image = lambda arr, path: None
    monkeypatch.setitem(sys.modules, "hdri_match.core.pipeline", pipe_mod)
    monkeypatch.setitem(sys.modules, "hdri_match.io.loader", loader_mod)
    monkeypatch.setitem(sys.modules, "hdri_match.io.exporter", exp_mod)

    monkeypatch.setattr(
        hda_utils,
        "read_calibration_parms",
        lambda n: {
            "hdri_path": hdri_file,
            "plate_path": "",
            "input_colorspace": "Linear",
            "working_colorspace": "Linear",
            "auto_calibrate": False,
            "protect_sun": True,
        },
    )
    monkeypatch.setattr(hda_utils, "build_output_path", lambda n, p: out_file)
    monkeypatch.setattr(hda_utils, "sync_parms_to_pipeline", lambda n, p: None)

    recorded_dome = {}
    recorded_meta = {}
    monkeypatch.setattr(usd_helpers, "ensure_scope", lambda st, p: None)

    def _fake_set_dome_light(stage, path, texture_path, exposure=0.0, color=(1.0, 1.0, 1.0), yaw_degrees=0.0, **kw):
        recorded_dome.update(exposure=exposure, color=color, yaw_degrees=yaw_degrees, texture_path=texture_path)
        return types.SimpleNamespace(GetPrim=lambda: "FAKE_DOME_PRIM")

    monkeypatch.setattr(usd_helpers, "set_dome_light", _fake_set_dome_light)
    monkeypatch.setattr(usd_helpers, "write_calibration_metadata", lambda prim, d: recorded_meta.update(d))

    fake_node = types.SimpleNamespace(editableStage=lambda: object())
    lop_calibrate.cook(fake_node)

    assert recorded_dome["exposure"] == 0.0
    assert recorded_dome["color"] == (1.0, 1.0, 1.0)
    assert recorded_dome["yaw_degrees"] == 0.0
    assert recorded_meta["baked_in_texture"] is True
    assert recorded_meta["ev_offset"] == 2.5

    # Verify panel_ui._gen_calibrate_code matches the same neutral DomeLight invariant
    _ensure_fake_qt_if_absent(monkeypatch)
    monkeypatch.delitem(sys.modules, "hdri_match_solaris.panel_ui", raising=False)
    from hdri_match_solaris import panel_ui

    code = panel_ui._gen_calibrate_code(
        {
            "hdri_path": hdri_file.replace("\\", "/"),
            "plate_path": "",
            "input_cs": "Linear",
            "sky_mode": "Physical",
            "ev": 2.5,
            "black": 0.0,
            "temp": 0.8,
            "tint": -0.2,
            "yaw": 35.0,
            "horizon_en": False,
            "horizon_h": 0.5,
            "horizon_f": 0.1,
            "sky_ev": 0.0,
            "sky_sat": 1.0,
            "ground_ev": 0.0,
            "ground_sat": 1.0,
            "softclip_en": False,
            "softclip_t": 1.0,
            "softclip_r": 0.5,
            "auto_cal": False,
            "protect_sun": True,
        }
    )
    assert "CreateExposureAttr().Set(0.0)" in code
    assert "Gf.Vec3f(1.0, 1.0, 1.0)" in code
    assert "ClearXformOpOrder()" in code
    assert '"baked_in_texture": True' in code
    assert "resolve_workspace_dir" in code


# ---------------------------------------------------------------------------
# 6. renderer_materials & usd_helpers
# ---------------------------------------------------------------------------


def test_renderer_materials_emissive_separation_and_surface_outputs():
    from hdri_match_solaris import renderer_materials as rm

    ups_lines = "\n".join(rm.gen_usd_preview_surface_code("mat_path", "tex_file", "rough", "mat_mode", "em_mult"))
    assert "PreviewEmissiveTexture" in ups_lines
    assert 'elif mat_mode == "pbr_emissive":' in ups_lines

    fn_lines = "\n".join(rm.gen_create_material_function())
    assert "PreviewEmissiveTexture" in fn_lines

    out_lines = "\n".join(rm.gen_material_output_connections("mat_path"))
    assert 'CreateSurfaceOutput().ConnectToSource(_ups_out)' in out_lines
    assert 'CreateSurfaceOutput("arnold").ConnectToSource(_ai_out)' in out_lines
    assert 'CreateSurfaceOutput("karma").ConnectToSource(_km_out)' in out_lines
    assert 'CreateSurfaceOutput("mtlx").ConnectToSource(_km_out)' in out_lines
    assert 'CreateSurfaceOutput("redshift").ConnectToSource(_rs_out)' in out_lines


def test_usd_helpers_workspace_stage_and_distant_light(monkeypatch, tmp_path):
    from hdri_match_solaris import usd_helpers

    # 1. resolve_workspace_dir skips ".", "", and houdini_temp
    fake_hou = _ensure_fake_hou_module()
    fake_hou.getenv = lambda var, d="": "C:/Temp/houdini_temp" if var == "HIP" else str(tmp_path / "job_dir")
    monkeypatch.setitem(sys.modules, "hou", fake_hou)

    resolved = usd_helpers.resolve_workspace_dir("hdri_match")
    assert "houdini_temp" not in resolved.lower()
    assert resolved != "."
    assert resolved.endswith("/job_dir/hdri_match")

    # 2. get_or_create_solaris_stage_node(create=False) does not create /obj/stage
    created = []
    nodes = {"/obj": types.SimpleNamespace(createNode=lambda typ, name: created.append((typ, name)) or "NEW_STAGE")}
    fake_hou.node = lambda p: nodes.get(p)
    assert usd_helpers.get_or_create_solaris_stage_node(create=False) is None
    assert created == []
    assert usd_helpers.get_or_create_solaris_stage_node(create=True) == "NEW_STAGE"
    assert created == [("lopnet", "stage")]

    # Existing /stage or /obj/stage takes precedence
    nodes["/obj/stage"] = "OBJ_STAGE"
    assert usd_helpers.get_or_create_solaris_stage_node(create=False) == "OBJ_STAGE"
    nodes["/stage"] = "ROOT_STAGE"
    assert usd_helpers.get_or_create_solaris_stage_node(create=False) == "ROOT_STAGE"

    # 3. set_distant_light zeroes translation so DistantLight gizmo remains at origin
    recorded_xforms = []

    class _FakeVec3d(tuple):
        def __new__(cls, x, y, z):
            return tuple.__new__(cls, (x, y, z))

        def __mul__(self, scalar):
            return _FakeVec3d(self[0] * scalar, self[1] * scalar, self[2] * scalar)

        def __rmul__(self, scalar):
            return self.__mul__(scalar)

    class _FakeMatrix4d:
        def __init__(self):
            self.translation = (999.0, 999.0, 999.0)

        def SetLookAt(self, eye, center, up):
            self.translation = tuple(eye)

        def GetInverse(self):
            inv = _FakeMatrix4d()
            inv.translation = self.translation
            return inv

        def SetTranslateOnly(self, v):
            self.translation = tuple(v)

    monkeypatch.setattr(usd_helpers, "Gf", types.SimpleNamespace(Vec3d=_FakeVec3d, Vec3f=_FakeVec3d, Matrix4d=_FakeMatrix4d))
    monkeypatch.setattr(
        usd_helpers,
        "UsdLux",
        types.SimpleNamespace(
            DistantLight=types.SimpleNamespace(
                Define=lambda st, p: types.SimpleNamespace(
                    CreateIntensityAttr=lambda: types.SimpleNamespace(Set=lambda v: None),
                    CreateColorAttr=lambda: types.SimpleNamespace(Set=lambda v: None),
                    CreateAngleAttr=lambda: types.SimpleNamespace(Set=lambda v: None),
                    CreateExposureAttr=lambda: types.SimpleNamespace(Set=lambda v: None),
                    GetPrim=lambda: "SUN_PRIM",
                )
            )
        ),
    )
    monkeypatch.setattr(
        usd_helpers,
        "UsdGeom",
        types.SimpleNamespace(
            Xformable=lambda prim: types.SimpleNamespace(
                ClearXformOpOrder=lambda: None,
                AddTransformOp=lambda: types.SimpleNamespace(Set=lambda mat: recorded_xforms.append(mat)),
            )
        ),
    )

    usd_helpers.set_distant_light(object(), "/lights/sun_direct", azimuth_deg=45.0, elevation_deg=30.0)
    assert len(recorded_xforms) == 1
    assert recorded_xforms[0].translation == (0.0, 0.0, 0.0)


# ---------------------------------------------------------------------------
# 7. Crucible Light Studio Presets, Solo State, & Qt Fallbacks
# ---------------------------------------------------------------------------


def test_preset_manager_deepcopy_and_rig_dir(monkeypatch):
    monkeypatch.setenv("CRUCIBLE_RIG_PATH", "/custom/studio/rigs")
    monkeypatch.delitem(sys.modules, "CrucibleLightStudio.core.preset_manager", raising=False)
    from CrucibleLightStudio.core import preset_manager

    assert preset_manager.RIG_DIR == "/custom/studio/rigs"

    p1 = preset_manager.get_light_presets()
    orig_val = p1["Built-in"]["Golden Hour"]["intensity"]
    p1["Built-in"]["Golden Hour"]["intensity"] = 9999.0

    p2 = preset_manager.get_light_presets()
    assert p2["Built-in"]["Golden Hour"]["intensity"] == orig_val


def test_light_manager_solo_preserves_pre_muted_lights(monkeypatch):
    _ensure_fake_qt_if_absent(monkeypatch)
    from CrucibleLightStudio.ui.widgets.light_manager import LightManagerWidget

    class _LightData:
        def __init__(self, prim_path, enabled):
            self.prim_path = prim_path
            self.enabled = enabled

    lights = {
        "/lights/key": _LightData("/lights/key", True),
        "/lights/muted_rim": _LightData("/lights/muted_rim", False),
        "/lights/fill": _LightData("/lights/fill", True),
    }
    backend = types.SimpleNamespace(
        discover_lights=lambda: list(lights.values()),
        set_light_enabled=lambda p, val: setattr(lights[p], "enabled", val),
    )
    fake_mgr = types.SimpleNamespace(
        backend=backend,
        _pre_solo_state=None,
        _soloed_path=None,
        refresh_lights=lambda: None,
    )

    # 1. Solo key light
    LightManagerWidget._on_solo_toggled(fake_mgr, "/lights/key", True)
    assert lights["/lights/key"].enabled is True
    assert lights["/lights/muted_rim"].enabled is False
    assert lights["/lights/fill"].enabled is False

    # 2. Switch Solo directly to fill light (A -> B transition must keep muted_rim pre-solo False)
    LightManagerWidget._on_solo_toggled(fake_mgr, "/lights/fill", True)
    assert lights["/lights/key"].enabled is False
    assert lights["/lights/muted_rim"].enabled is False
    assert lights["/lights/fill"].enabled is True

    # 3. Un-solo -> must restore pre-solo states (muted_rim stays False!)
    LightManagerWidget._on_solo_toggled(fake_mgr, "/lights/fill", False)
    assert lights["/lights/key"].enabled is True
    assert lights["/lights/muted_rim"].enabled is False
    assert lights["/lights/fill"].enabled is True


def test_crucible_qt_import_resilience_and_worker_registry():
    # gradient_builder must try PySide6/PySide2 cleanly via ImportError (not sys.modules check)
    gb_path = os.path.join(PYTHON_ROOT, "CrucibleLightStudio", "ui", "widgets", "gradient_builder.py")
    with open(gb_path, "r", encoding="utf-8") as f:
        gb_src = f.read()
    assert '"PySide2" in sys.modules' not in gb_src
    assert "from PySide6 import" in gb_src and "from PySide2 import" in gb_src

    al_path = os.path.join(PYTHON_ROOT, "CrucibleLightStudio", "ui", "widgets", "asset_library.py")
    with open(al_path, "r", encoding="utf-8") as f:
        al_src = f.read()
    assert "_ACTIVE_THUMBNAIL_WORKERS" in al_src
    assert "generation_id" in al_src
    assert "def stop_workers(" in al_src


# ---------------------------------------------------------------------------
# 8. Shelf, Python Panels, Installer & Updater Security
# ---------------------------------------------------------------------------


def test_shelf_and_pypanel_xml_valid_and_targets_correct():
    shelf_path = os.path.join(HOUDINI_ROOT, "toolbar", "domebreaker.shelf")
    tree = ET.parse(shelf_path)
    tools = {el.attrib.get("name"): el for el in tree.findall(".//tool")}
    assert "domebreaker_open_panel" in tools
    assert "splatforge_open_panel" in tools

    sf_script = tools["splatforge_open_panel"].findtext("script") or ""
    assert 'target_interfaces = ["splatforge_panel"]' in sf_script
    assert "domebreaker_panel" not in sf_script

    for fname in os.listdir(os.path.join(HOUDINI_ROOT, "python_panels")):
        if fname.endswith(".pypanel"):
            ET.parse(os.path.join(HOUDINI_ROOT, "python_panels", fname))

    sf_panel_path = os.path.join(HOUDINI_ROOT, "python_panels", "splatforge.pypanel")
    with open(sf_panel_path, "r", encoding="utf-8") as f:
        assert "get_or_create_solaris_stage_node" in f.read()


def test_installer_version_and_json_preservation(monkeypatch):
    monkeypatch.syspath_prepend(SCRIPTS_ROOT)
    import install as inst

    assert inst._is_supported_houdini_version("20.0") is True
    assert inst._is_supported_houdini_version("20.5.332") is True
    assert inst._is_supported_houdini_version("20.5_backup") is True
    assert inst._is_supported_houdini_version("21.0.512") is True
    assert inst._is_supported_houdini_version("19.5") is False
    assert inst._is_supported_houdini_version("backup_folder") is False
    assert inst._is_supported_houdini_version("") is False

    # Confirm install.py does NOT overwrite the git-tracked domebreaker.json template
    pkg_json_path = os.path.join(HOUDINI_ROOT, "packages", "domebreaker.json")
    with open(pkg_json_path, "r", encoding="utf-8") as f:
        pkg_data = json.load(f)
    assert any("$HOUDINI_PACKAGE_PATH" in str(item) for item in pkg_data.get("env", []))


def test_updater_zip_slip_blocked_and_safe_extract_works(monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(SCRIPTS_ROOT)
    import update as upd

    # 1. Malicious Zip-Slip member must raise RuntimeError and never escape extract_dir
    evil_zip = tmp_path / "evil.zip"
    extract_dir = tmp_path / "extract_target"
    escaped_file = tmp_path / "escaped.txt"
    with zipfile.ZipFile(evil_zip, "w") as zf:
        zf.writestr("../escaped.txt", "pwned")

    with pytest.raises(RuntimeError, match="path traversal"):
        upd._safe_extract_zip(str(evil_zip), str(extract_dir))
    assert not escaped_file.exists()

    # 2. Normal safe archive extracts properly
    good_zip = tmp_path / "good.zip"
    safe_dir = tmp_path / "safe_out"
    with zipfile.ZipFile(good_zip, "w") as zf:
        zf.writestr("DomeBreaker-main/VERSION", "v2.0.5\n")
    upd._safe_extract_zip(str(good_zip), str(safe_dir))
    assert (safe_dir / "DomeBreaker-main" / "VERSION").read_text(encoding="utf-8").strip() == "v2.0.5"
