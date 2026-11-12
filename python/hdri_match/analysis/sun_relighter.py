"""
Interactive HDRI Sun Relighting Engine.

Extracts the sun disc from an equirectangular HDRI, removes it via
inpainting, and composites it at a new spherical position. Preserves
HDR energy, handles wrap-around, and accounts for equirectangular
latitude distortion.

All operations are scene-linear (no clamping, no gamma).
"""
import math
import sys
from typing import Tuple, Optional

import numpy as np


def _torch_cuda_available() -> bool:
    """Lazily check CUDA availability without initializing Torch/CUDA at import time."""
    try:
        import torch
        return bool(torch.cuda.is_available())
    except Exception:
        return False


def _shift_vertical_no_wrap(arr: np.ndarray, shift_y: int) -> np.ndarray:
    """Shift an array along axis=0 without wrapping across zenith/nadir poles."""
    if shift_y == 0:
        return arr
    h = arr.shape[0]
    out = np.zeros_like(arr)
    if abs(shift_y) >= h:
        return out
    if shift_y > 0:
        out[shift_y:] = arr[:h - shift_y]
    else:
        s = -shift_y
        out[:h - s] = arr[s:]
    return out


def _blur_numpy_separable(arr: np.ndarray, sigma: float) -> np.ndarray:
    """Pure-NumPy separable 2D Gaussian blur (horizontal wrap, vertical edge clamp)."""
    sigma = max(float(sigma), 0.5)
    radius = max(1, min(int(math.ceil(sigma * 2.5)), 24))
    x = np.arange(-radius, radius + 1, dtype=np.float32)
    kernel = np.exp(-0.5 * (x / sigma) ** 2)
    kernel /= np.sum(kernel)

    # Horizontal pass with equirectangular wrap
    padded_x = np.pad(arr, ((0, 0), (radius, radius), (0, 0)), mode="wrap")
    tmp = np.zeros_like(arr, dtype=np.float32)
    for idx, w in enumerate(kernel):
        tmp += padded_x[:, idx:idx + arr.shape[1], :] * w

    # Vertical pass with pole clamp (never wraps zenith <-> nadir)
    padded_y = np.pad(tmp, ((radius, radius), (0, 0), (0, 0)), mode="edge")
    out = np.zeros_like(arr, dtype=np.float32)
    for idx, w in enumerate(kernel):
        out += padded_y[idx:idx + arr.shape[0], :, :] * w
    return out


def _resize_bilinear_np(arr: np.ndarray, out_h: int, out_w: int) -> np.ndarray:
    """Pure-NumPy bilinear resize for (H, W, C) arrays."""
    in_h, in_w = arr.shape[:2]
    if (in_h, in_w) == (out_h, out_w):
        return arr.astype(np.float32, copy=False)
    y = np.linspace(0.0, max(in_h - 1, 0), out_h, dtype=np.float32)
    x = np.linspace(0.0, max(in_w - 1, 0), out_w, dtype=np.float32)
    y0 = np.floor(y).astype(np.int64)
    x0 = np.floor(x).astype(np.int64)
    y1 = np.clip(y0 + 1, 0, in_h - 1)
    x1 = np.clip(x0 + 1, 0, in_w - 1)
    wy = (y - y0)[:, None, None]
    wx = (x - x0)[None, :, None]

    ia = arr[np.ix_(y0, x0)]
    ib = arr[np.ix_(y0, x1)]
    ic = arr[np.ix_(y1, x0)]
    id_ = arr[np.ix_(y1, x1)]
    return (
        ia * (1.0 - wx) * (1.0 - wy)
        + ib * wx * (1.0 - wy)
        + ic * (1.0 - wx) * wy
        + id_ * wx * wy
    ).astype(np.float32)


class SunRelighter:
    """Moves the dominant light source in an equirectangular HDRI."""

    @staticmethod
    def detect_sun(image: np.ndarray) -> Tuple[float, float, float]:
        """
        Detect the brightest point in the HDRI.

        Returns:
            (u, v, peak_luminance) where u,v are normalized 0-1 coordinates.
        """
        luma = np.dot(image[..., :3], [0.2126, 0.7152, 0.0722])
        flat_idx = int(np.argmax(luma))
        h, w = image.shape[:2]
        py, px = divmod(flat_idx, w)
        u = (px + 0.5) / w
        v = (py + 0.5) / h
        return u, v, float(luma[py, px])

    @staticmethod
    def _build_radial_mask(h: int, w: int,
                           center_u: float, center_v: float,
                           radius_norm: float,
                           feather_norm: float = 0.02) -> np.ndarray:
        """
        Build a smooth radial mask in equirectangular space.
        Handles horizontal wrap-around.

        Args:
            h, w:           Image dimensions.
            center_u/v:     Normalised center (0-1).
            radius_norm:    Normalised radius (0-1 of image width).
            feather_norm:   Normalised feather width for smooth falloff.

        Returns:
            (H, W) float32 array, 1.0 inside, smooth falloff to 0.0 outside.
        """
        y_coords = np.arange(h, dtype=np.float32) / h
        x_coords = np.arange(w, dtype=np.float32) / w

        # Horizontal distance with wrap-around
        dx = np.abs(x_coords[np.newaxis, :] - center_u)
        dx = np.minimum(dx, 1.0 - dx)

        # Vertical distance (no wrap)
        dy = np.abs(y_coords[:, np.newaxis] - center_v)

        # Account for equirectangular latitude compression:
        # At higher latitudes, a given pixel-width covers a larger angular span.
        # Scale horizontal distance by cos(latitude) to get angular distance.
        lat = (0.5 - center_v) * math.pi  # latitude of the center
        cos_lat = max(math.cos(lat), 0.1)  # Clamp to avoid division issues at poles
        dx_angular = dx / cos_lat

        # Euclidean distance in normalised space
        dist = np.sqrt(dx_angular**2 + dy**2)

        # Smooth falloff using hermite interpolation
        inner = radius_norm
        outer = radius_norm + max(feather_norm, 0.001)

        alpha = np.clip((outer - dist) / (outer - inner + 1e-8), 0.0, 1.0)
        # Smoothstep
        alpha = alpha * alpha * (3.0 - 2.0 * alpha)

        return alpha.astype(np.float32)

    @staticmethod
    def extract_sun_stamp(image: np.ndarray,
                          sun_u: float, sun_v: float,
                          radius_norm: float = 0.03,
                          feather_norm: float = 0.015) -> Tuple[np.ndarray, np.ndarray]:
        """
        Extract the sun region as a premultiplied RGB stamp + alpha mask.

        Args:
            image:        Linear float32 HDRI (H, W, 3).
            sun_u, sun_v: Normalised sun center coordinates.
            radius_norm:  Normalised radius of extraction area.
            feather_norm: Feather width for smooth extraction.

        Returns:
            (stamp, alpha) where stamp is (H, W, 3) premultiplied by alpha,
            and alpha is (H, W) float32.
        """
        h, w = image.shape[:2]
        alpha = SunRelighter._build_radial_mask(
            h, w, sun_u, sun_v, radius_norm, feather_norm)

        stamp = image * alpha[..., np.newaxis]
        return stamp, alpha

    @staticmethod
    def inpaint_sun(image: np.ndarray, alpha: np.ndarray,
                    blur_radius_mult: float = 3.0) -> np.ndarray:
        """
        Remove the sun by replacing the masked region with blurred
        surrounding sky pixels. Uses iterative Gaussian fill.

        Args:
            image:             Linear float32 HDRI (H, W, 3).
            alpha:             (H, W) float32 mask (1.0 = sun, 0.0 = keep).
            blur_radius_mult:  Multiplier for blur kernel relative to sun size.

        Returns:
            Inpainted image with the sun region smoothly filled.
        """
        h, w = image.shape[:2]

        # Estimate kernel size from the mask extent
        mask_pixels = np.sum(alpha > 0.5)
        effective_radius = max(int(math.sqrt(mask_pixels / math.pi)), 5)
        ksize = int(effective_radius * blur_radius_mult) * 2 + 1
        ksize = max(ksize, 5)
        if ksize % 2 == 0:
            ksize += 1

        result = image.copy()
        inv_alpha = 1.0 - alpha

        used_cv2 = False
        if "hou" not in sys.modules:
            try:
                import cv2
                if max(h, w) > 1000:
                    scale = 1000.0 / max(h, w)
                    small_ksize = max(int(ksize * scale), 5)
                    if small_ksize % 2 == 0:
                        small_ksize += 1
                    small_img = cv2.resize(result, (0, 0), fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
                    for _ in range(4):
                        small_img = cv2.GaussianBlur(small_img, (small_ksize, small_ksize), small_ksize / 4.0)
                    blurred = cv2.resize(small_img, (w, h), interpolation=cv2.INTER_LINEAR)
                    result = result * inv_alpha[..., np.newaxis] + blurred * alpha[..., np.newaxis]
                else:
                    for _ in range(4):
                        blurred = cv2.GaussianBlur(result, (ksize, ksize), ksize / 4.0)
                        result = result * inv_alpha[..., np.newaxis] + blurred * alpha[..., np.newaxis]
                used_cv2 = True
            except ImportError:
                used_cv2 = False

        if not used_cv2:
            # SciPy or Pure-NumPy fallback (fast downsampled multi-pass inside Houdini)
            try:
                from scipy.ndimage import gaussian_filter
                if max(h, w) > 1000:
                    scale = 1000.0 / max(h, w)
                    sh = max(int(round(h * scale)), 16)
                    sw = max(int(round(w * scale)), 32)
                    small_img = _resize_bilinear_np(result, sh, sw)
                    small_sigma = max((ksize * scale) / 4.0, 1.0)
                    for _ in range(4):
                        small_img = gaussian_filter(small_img, sigma=(small_sigma, small_sigma, 0))
                    blurred = _resize_bilinear_np(small_img, h, w)
                    result = result * inv_alpha[..., np.newaxis] + blurred * alpha[..., np.newaxis]
                else:
                    for _ in range(4):
                        blurred = gaussian_filter(result, sigma=(ksize // 4, ksize // 4, 0))
                        result = result * inv_alpha[..., np.newaxis] + blurred * alpha[..., np.newaxis]
            except ImportError:
                scale = min(1.0, 512.0 / max(h, w))
                if scale < 1.0:
                    sh = max(int(round(h * scale)), 16)
                    sw = max(int(round(w * scale)), 32)
                    small_img = _resize_bilinear_np(result, sh, sw)
                    small_sigma = max((ksize * scale) / 4.0, 1.0)
                    for _ in range(4):
                        small_img = _blur_numpy_separable(small_img, small_sigma)
                    blurred = _resize_bilinear_np(small_img, h, w)
                    result = result * inv_alpha[..., np.newaxis] + blurred * alpha[..., np.newaxis]
                else:
                    for _ in range(4):
                        blurred = _blur_numpy_separable(result, max(ksize / 4.0, 1.0))
                        result = result * inv_alpha[..., np.newaxis] + blurred * alpha[..., np.newaxis]

        return result

    @staticmethod
    def place_sun(base_image: np.ndarray,
                  stamp: np.ndarray, stamp_alpha: np.ndarray,
                  source_u: float, source_v: float,
                  target_u: float, target_v: float) -> np.ndarray:
        """
        Composite the sun stamp at a new position.
        Handles horizontal wrap-around via np.roll and clamps vertically without pole wrapping.

        Args:
            base_image:       Inpainted HDRI without sun (H, W, 3).
            stamp:            Premultiplied sun stamp (H, W, 3).
            stamp_alpha:      Alpha mask of the stamp (H, W).
            source_u/v:       Original sun position (normalised).
            target_u/v:       New sun position (normalised).

        Returns:
            New HDRI with sun at the target position.
        """
        h, w = base_image.shape[:2]

        # Compute pixel shift
        du = target_u - source_u
        dv = target_v - source_v

        shift_x = int(round(du * w))
        shift_y = int(round(dv * h))

        # Horizontal wrap around 0/1 panorama seam; vertical shift NEVER wraps zenith <-> nadir
        shifted_stamp = np.roll(stamp, shift_x, axis=1)
        shifted_stamp = _shift_vertical_no_wrap(shifted_stamp, shift_y)
        shifted_alpha = np.roll(stamp_alpha, shift_x, axis=1)
        shifted_alpha = _shift_vertical_no_wrap(shifted_alpha, shift_y)

        # Additive composite (sun adds light on top of the base)
        result = base_image + shifted_stamp

        return result

    @staticmethod
    def relight(image: np.ndarray,
                source_u: float, source_v: float,
                target_u: float, target_v: float,
                radius_norm: float = 0.03,
                feather_norm: float = 0.015) -> np.ndarray:
        """
        Full sun relighting pipeline: extract -> remove -> place.

        Args:
            image:              Original linear HDRI (H, W, 3).
            source_u, source_v: Original sun position (normalised 0-1).
            target_u, target_v: Target sun position (normalised 0-1).
            radius_norm:        Normalised extraction radius.
            feather_norm:       Normalised feather width.

        Returns:
            New HDRI with sun moved to the target position.
        """
        if abs(source_u - target_u) < 1e-5 and abs(source_v - target_v) < 1e-5:
            return image  # No movement needed

        if _torch_cuda_available():
            try:
                import torch
                import torchvision.transforms.functional as TF
                import torch.nn.functional as F

                with torch.no_grad():
                    h, w = image.shape[:2]
                    alpha_np = SunRelighter._build_radial_mask(h, w, source_u, source_v, radius_norm, feather_norm)

                    t_img = torch.from_numpy(image.astype(np.float32)).cuda()
                    t_alpha = torch.from_numpy(alpha_np).cuda()

                    t_stamp = t_img * t_alpha.unsqueeze(2)

                    mask_pixels = torch.sum(t_alpha > 0.5).item()
                    effective_radius = max(int(math.sqrt(mask_pixels / math.pi)), 5)
                    ksize = int(effective_radius * 3.0) * 2 + 1
                    ksize = max(ksize, 5)
                    if ksize % 2 == 0:
                        ksize += 1

                    t_result = t_img.clone()
                    t_inv_alpha = 1.0 - t_alpha

                    if max(h, w) > 1000:
                        scale = 1000.0 / max(h, w)
                        small_ksize = max(int(ksize * scale), 5)
                        if small_ksize % 2 == 0:
                            small_ksize += 1

                        t_small = t_result.permute(2, 0, 1).unsqueeze(0)
                        t_small = F.interpolate(t_small, scale_factor=scale, mode='area')

                        for _ in range(4):
                            t_small = TF.gaussian_blur(t_small, [small_ksize, small_ksize])

                        t_blurred = F.interpolate(t_small, size=(h, w), mode='bilinear', align_corners=False)
                        t_blurred = t_blurred.squeeze(0).permute(1, 2, 0)

                        t_result = t_result * t_inv_alpha.unsqueeze(2) + t_blurred * t_alpha.unsqueeze(2)
                    else:
                        t_temp = t_result.permute(2, 0, 1).unsqueeze(0)
                        for _ in range(4):
                            t_temp = TF.gaussian_blur(t_temp, [ksize, ksize])
                        t_blurred = t_temp.squeeze(0).permute(1, 2, 0)
                        t_result = t_result * t_inv_alpha.unsqueeze(2) + t_blurred * t_alpha.unsqueeze(2)

                    du = target_u - source_u
                    dv = target_v - source_v
                    shift_x = int(round(du * w))
                    shift_y = int(round(dv * h))

                    # Horizontal roll + non-wrapping vertical slice
                    t_shifted_stamp = torch.roll(t_stamp, shifts=shift_x, dims=1)
                    if shift_y != 0:
                        t_padded = torch.zeros_like(t_shifted_stamp)
                        if abs(shift_y) < h:
                            if shift_y > 0:
                                t_padded[shift_y:] = t_shifted_stamp[:h - shift_y]
                            else:
                                s = -shift_y
                                t_padded[:h - s] = t_shifted_stamp[s:]
                        t_shifted_stamp = t_padded

                    t_final = t_result + t_shifted_stamp
                    return t_final.cpu().numpy()
            except Exception as e:
                print(f"[SunRelighter] PyTorch GPU acceleration failed, falling back to CPU: {e}")
            finally:
                try:
                    import torch
                    if torch.cuda.is_available():
                        torch.cuda.empty_cache()
                except Exception:
                    pass

        # CPU Fallback Pipeline
        # 1. Extract sun stamp
        stamp, alpha = SunRelighter.extract_sun_stamp(
            image, source_u, source_v, radius_norm, feather_norm)

        # 2. Remove sun from original
        base = SunRelighter.inpaint_sun(image, alpha)

        # 3. Place sun at new position
        result = SunRelighter.place_sun(
            base, stamp, alpha,
            source_u, source_v,
            target_u, target_v)

        return result

    @staticmethod
    def uv_to_spherical(u: float, v: float) -> Tuple[float, float]:
        """Convert normalised UV to azimuth/elevation in degrees."""
        azimuth = (u * 360.0) - 180.0    # -180 to +180
        elevation = 90.0 - (v * 180.0)   # +90 (zenith) to -90 (nadir)
        return azimuth, elevation

    @staticmethod
    def spherical_to_uv(azimuth_deg: float, elevation_deg: float) -> Tuple[float, float]:
        """Convert azimuth/elevation in degrees to normalised UV."""
        u = (azimuth_deg + 180.0) / 360.0
        v = (90.0 - elevation_deg) / 180.0
        return max(0.0, min(1.0, u)), max(0.0, min(1.0, v))

    @staticmethod
    def uv_to_direction(u: float, v: float) -> Tuple[float, float, float]:
        """Convert normalised UV to a 3D direction vector (Y-up, Z-forward)."""
        azimuth, elevation = SunRelighter.uv_to_spherical(u, v)
        theta = math.radians(azimuth)
        phi = math.radians(elevation)
        dir_x = math.cos(phi) * math.sin(theta)
        dir_y = math.sin(phi)
        dir_z = math.cos(phi) * math.cos(theta)
        return dir_x, dir_y, dir_z
