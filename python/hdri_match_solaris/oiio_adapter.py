"""
OpenImageIO Adapter for HDRI Match in Houdini.

Provides high-performance native image I/O via OpenImageIO (shipped with Houdini).
Seamlessly patches hdri_match.io.loader and hdri_match.io.exporter so that all
EXR, HDR, TIFF, PNG, and JPEG loading/exporting works out-of-the-box in Houdini
without requiring external cv2 or imageio packages.
"""

import os
import numpy as np

try:
    import OpenImageIO as oiio
    HAS_OIIO = True
except ImportError:
    HAS_OIIO = False


def load_image_with_oiio(file_path: str) -> np.ndarray:
    """Read any image supported by OpenImageIO into float32 RGB."""
    if not HAS_OIIO:
        raise ImportError("OpenImageIO is not available.")

    inp = oiio.ImageInput.open(file_path)
    if not inp:
        raise IOError(f"OpenImageIO failed to open '{file_path}': {oiio.geterror()}")

    try:
        spec = inp.spec()
        arr = inp.read_image(format=oiio.FLOAT)
        if arr is None:
            raise IOError(f"OpenImageIO read_image returned None for '{file_path}'")
        
        # Take first 3 channels (RGB)
        if len(arr.shape) == 3 and arr.shape[2] >= 3:
            arr = arr[..., :3]
        elif len(arr.shape) == 2:
            arr = np.stack([arr] * 3, axis=-1)
            
        return arr.astype(np.float32)
    finally:
        inp.close()


def save_image_with_oiio(image_array: np.ndarray, file_path: str):
    """Save a float32 RGB image array using OpenImageIO with strict fail-closed checks."""
    if not HAS_OIIO:
        raise ImportError("OpenImageIO is not available.")

    arr = np.asarray(image_array, dtype=np.float32)
    if arr.ndim == 2:
        arr = np.stack([arr] * 3, axis=-1)
    elif arr.ndim != 3:
        raise ValueError(f"Expected a 2D or 3D image array, got shape {arr.shape}.")

    if arr.shape[2] >= 3:
        arr = arr[..., :3]
    else:
        raise ValueError(f"Expected at least 3 channels (RGB), got shape {arr.shape}.")

    h, w, _ = arr.shape
    clean = np.nan_to_num(arr, nan=0.0, posinf=65504.0, neginf=0.0)
    contiguous = np.ascontiguousarray(clean, dtype=np.float32)

    out_dir = os.path.dirname(os.path.abspath(file_path))
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    spec = oiio.ImageSpec(w, h, 3, oiio.FLOAT)
    out = oiio.ImageOutput.create(file_path)
    if not out:
        raise IOError(f"OpenImageIO could not create output for '{file_path}': {oiio.geterror()}")

    opened = False
    try:
        opened = bool(out.open(file_path, spec))
        if not opened:
            err = out.geterror() if hasattr(out, "geterror") else oiio.geterror()
            raise IOError(f"OpenImageIO failed to open '{file_path}' for writing: {err}")

        ok = bool(out.write_image(contiguous))
        if not ok:
            err = out.geterror() if hasattr(out, "geterror") else oiio.geterror()
            raise IOError(f"OpenImageIO failed to write pixels to '{file_path}': {err}")
    finally:
        if opened:
            out.close()


def install_adapter():
    """Monkey-patch hdri_match.io to prepend OpenImageIO handlers."""
    if not HAS_OIIO:
        return

    try:
        import hdri_match.io.loader as loader
        orig_loader = loader.load_exr_to_numpy

        def patched_load(file_path: str) -> np.ndarray:
            try:
                return load_image_with_oiio(file_path)
            except Exception as e:
                return orig_loader(file_path)

        loader.load_exr_to_numpy = patched_load
        loader.load_image_to_numpy = patched_load
    except Exception:
        pass

    try:
        import hdri_match.io.exporter as exporter
        orig_saver = exporter.save_numpy_to_image

        def patched_save(image_array: np.ndarray, file_path: str):
            try:
                save_image_with_oiio(image_array, file_path)
            except Exception:
                orig_saver(image_array, file_path)

        exporter.save_numpy_to_image = patched_save
    except Exception:
        pass


# Automatically install adapter when imported
install_adapter()
