import numpy as np

try:
    import PyOpenColorIO as OCIO
except ImportError:
    OCIO = None


class ColorSpaceManager:
    """Manages OCIO color space and OCIOv2 Display/View transformations."""

    BUILTIN_SPACES = [
        "Linear - sRGB",
        "ACEScg",
        "ACES2065-1",
        "sRGB",
        "Rec.709",
        "ACEScc",
        "ACEScct",
    ]

    DEFAULT_DISPLAY_VIEWS = [
        "ACES 1.0 - SDR Video (Rec.1886 Rec.709 - Display)",
        "ACES 1.0 - SDR Video (sRGB - Display)",
        "Un-tone-mapped (Rec.1886 Rec.709 - Display)",
        "Un-tone-mapped (sRGB - Display)",
        "Raw (Rec.1886 Rec.709 - Display)",
        "Raw (sRGB - Display)",
    ]

    def __init__(self, config_path=None):
        self.config_path = config_path
        self._printed_errors = set()
        if OCIO is None:
            self.config = None
            print("PyOpenColorIO not found. Running in fallback mode (No color management).")
        else:
            try:
                if config_path:
                    self.config = OCIO.Config.CreateFromFile(config_path)
                else:
                    self.config = OCIO.GetCurrentConfig()

                # Reject dummy fallback config from OCIO
                if self.config:
                    spaces = list(self.config.getColorSpaces())
                    if len(spaces) == 1 and spaces[0].getName() == "raw":
                        print("Detected PyOpenColorIO dummy config. Falling back to internal color management.")
                        self.config = None

            except Exception as e:
                print(f"Failed to load OCIO config: {e}")
                self.config = None

        if self.config is None:
            print("[ColorSpaceManager] Using Built-In mathematical fallback (ACEScg, sRGB, Rec.709, ACES SDR Video).")
            self._builtin_spaces = list(self.BUILTIN_SPACES)
        else:
            self._builtin_spaces = []

    def _iter_displays(self):
        if not self.config:
            return []
        displays = []
        try:
            raw = self.config.getDisplays()
            if isinstance(raw, str):
                displays = [d.strip() for d in raw.split(",") if d.strip()]
            else:
                displays = [str(d) for d in raw if str(d).strip()]
        except Exception:
            pass
        if not displays and hasattr(self.config, "getNumDisplays") and hasattr(self.config, "getDisplay"):
            try:
                displays = [
                    str(self.config.getDisplay(i))
                    for i in range(int(self.config.getNumDisplays()))
                ]
            except Exception:
                pass
        return displays

    def _iter_views(self, display: str):
        if not self.config or not display:
            return []
        try:
            views_raw = self.config.getViews(display)
            if isinstance(views_raw, str):
                return [v.strip() for v in views_raw.split(",") if v.strip()]
            return [str(v) for v in views_raw if str(v).strip()]
        except Exception:
            return []

    def get_color_spaces(self):
        """Returns a list of available color spaces."""
        if not self.config:
            return list(self._builtin_spaces)
        return [cs.getName() for cs in self.config.getColorSpaces()]

    def get_display_views(self):
        """Returns a list of views for the default display."""
        if not self.config:
            return ["sRGB", "Raw"]

        try:
            display = self.config.getDefaultDisplay()
            views = self._iter_views(display)
            if not views:
                spaces = [cs.getName() for cs in self.config.getColorSpaces()]
                outputs = [s for s in spaces if "Output" in s or "sRGB" in s]
                return outputs if outputs else ["sRGB", "Raw"]
            return views
        except Exception as e:
            print(f"Error getting display views: {e}")
            return ["sRGB", "Raw"]

    def get_all_display_views(self):
        """Returns all OCIOv2 Display/View pairs formatted as 'View (Display)'."""
        if not self.config:
            return list(self.DEFAULT_DISPLAY_VIEWS)
        pairs = []
        for disp in self._iter_displays():
            for view in self._iter_views(disp):
                label = f"{view} ({disp})"
                if label not in pairs:
                    pairs.append(label)
        return pairs if pairs else list(self.DEFAULT_DISPLAY_VIEWS)

    def get_available_input_spaces(self):
        """Returns deduplicated OCIO Display/View pairs and ColorSpaces."""
        seen = set()
        result = []
        for name in self.get_all_display_views() + self.get_color_spaces():
            if name and name not in seen:
                seen.add(name)
                result.append(name)
        return result

    def _match_config_display_view(self, space_name: str):
        """Match an exact (display, view) pair registered in the active OCIO config."""
        if not self.config or not space_name or not isinstance(space_name, str):
            return None
        s = space_name.strip()
        if not s:
            return None
        try:
            if self.config.getColorSpace(s) is not None:
                return None
        except Exception:
            pass

        s_low = s.lower()
        displays = self._iter_displays()
        for disp in displays:
            for view in self._iter_views(disp):
                label = f"{view} ({disp})"
                if s == label or s_low == label.lower():
                    return (disp, view)

        # Fallback: exact structural split if one side matches an OCIO display/view
        if " (" in s and s.endswith(")"):
            inner_idx = s.rfind(" (")
            while inner_idx > 0:
                cand_view = s[:inner_idx].strip()
                cand_disp = s[inner_idx + 2:-1].strip()
                for disp in displays:
                    if disp.lower() == cand_disp.lower():
                        for view in self._iter_views(disp):
                            if view.lower() == cand_view.lower():
                                return (disp, view)
                inner_idx = s.rfind(" (", 0, inner_idx)
        return None

    def parse_display_view(self, space_name: str):
        """Parse 'View (Display)' into (display, view), or return None for ColorSpaces."""
        if not space_name or not isinstance(space_name, str):
            return None
        s = space_name.strip()
        if not s:
            return None

        matched = self._match_config_display_view(s)
        if matched is not None:
            return matched

        if self.config:
            try:
                if self.config.getColorSpace(s) is not None:
                    return None
            except Exception:
                pass

        if " (" in s and s.endswith(")"):
            split_idx = s.rfind(" (")
            view = s[:split_idx].strip()
            disp = s[split_idx + 2:-1].strip()
            if view and disp:
                disp_low = disp.lower()
                view_low = view.lower()
                if (
                    "display" in disp_low
                    or "rec.1886" in disp_low
                    or "rec.2100" in disp_low
                    or "sdr video" in view_low
                    or "hdr video" in view_low
                    or "un-tone-mapped" in view_low
                    or (view_low == "raw" and disp_low != "raw")
                ):
                    return (disp, view)
        return None

    def is_display_referred(self, space_name: str) -> bool:
        """Return True if space_name represents a display-referred / OETF encoding."""
        dv = self.parse_display_view(space_name)
        if dv is not None:
            _disp, view = dv
            return view.strip().lower() != "raw"

        s = (space_name or "").strip().lower()
        if not s:
            return False
        if "linear" in s or "acescg" in s or "2065" in s or "acescc" in s or s == "raw" or s == "scene_linear":
            return False
        return any(
            tok in s
            for tok in ("srgb", "709", "1886", "gamma", "output", "display", "sdr", "2100", "2020")
        )

    def _build_display_view_processor(self, src_color_space: str, display: str, view: str, inverse: bool = False):
        direction = (
            getattr(OCIO, "TRANSFORM_DIR_INVERSE", 1)
            if inverse
            else getattr(OCIO, "TRANSFORM_DIR_FORWARD", 0)
        )
        try:
            dt = OCIO.DisplayViewTransform()
            dt.setSrc(src_color_space)
            dt.setDisplay(display)
            dt.setView(view)
            if hasattr(dt, "setDirection"):
                dt.setDirection(direction)
        except AttributeError:
            dt = OCIO.DisplayTransform()
            dt.setInputColorSpaceName(src_color_space)
            dt.setDisplay(display)
            dt.setView(view)
            if hasattr(dt, "setDirection"):
                dt.setDirection(direction)
        return self.config.getProcessor(dt)

    def _apply_ocio_processor(self, processor, image: np.ndarray) -> np.ndarray:
        cpu = processor.getDefaultCPUProcessor()
        arr = np.asarray(image, dtype=np.float32)
        if arr.ndim != 3:
            return arr

        h, w, c = arr.shape
        if c == 3:
            img_flat = np.ascontiguousarray(arr, dtype=np.float32).flatten()
            try:
                cpu.applyRGB(img_flat)
                out = img_flat.reshape((h, w, 3))
            except AttributeError:
                rgba = np.ones((h * w, 4), dtype=np.float32)
                rgba[:, :3] = arr.reshape(-1, 3)
                flat_rgba = rgba.flatten()
                cpu.applyRGBA(flat_rgba)
                out = flat_rgba.reshape((h, w, 4))[:, :, :3].copy()
            np.nan_to_num(out, copy=False, nan=0.0, posinf=65504.0, neginf=0.0)
            return np.ascontiguousarray(out, dtype=np.float32)
        elif c == 4:
            img_flat = np.ascontiguousarray(arr, dtype=np.float32).flatten()
            cpu.applyRGBA(img_flat)
            out = img_flat.reshape((h, w, 4))
            np.nan_to_num(out, copy=False, nan=0.0, posinf=65504.0, neginf=0.0)
            return np.ascontiguousarray(out, dtype=np.float32)
        return arr

    def apply_display_transform(self, image: np.ndarray, src_space: str, view: str = "sRGB", display: str = None) -> np.ndarray:
        """Applies a display transform using OCIO (e.g. RRT+ODT for ACES) or fallback."""
        dv = self.parse_display_view(view)
        if dv is not None:
            disp_name, view_name = dv
        else:
            disp_name = display
            view_name = view

        if not self.config:
            if view_name == "sRGB" and "acescg" in (src_space or "").lower() and disp_name is None:
                # Built-in ACES filmic fallback
                img = np.array(image, dtype=np.float32, copy=True)
                a, b, c, d, e = 2.51, 0.03, 2.43, 0.59, 0.14
                img = (img * (a * img + b)) / (img * (c * img + d) + e)
                img = np.clip(img, 0.0, 1.0)
                np.power(img, 1.0 / 2.2, out=img)
                return img
            if view_name in ("Raw", "Linear"):
                return np.clip(image, 0.0, 1.0)
            dst_label = f"{view_name} ({disp_name})" if disp_name else ("srgb_display" if view_name == "sRGB" else view_name)
            return self._fallback_transform(image, src_space, dst_label)

        try:
            target_display = disp_name or self.config.getDefaultDisplay()
            processor = self._build_display_view_processor(src_space, target_display, view_name, inverse=False)
            return self._apply_ocio_processor(processor, image)
        except Exception as e:
            print(f"DisplayViewTransform failed, falling back to direct transform: {e}")
            dst_label = f"{view_name} ({disp_name})" if disp_name else view_name
            return self.transform_image(image, src_space, dst_label)

    def transform_image(self, image: np.ndarray, src_space: str, dst_space: str) -> np.ndarray:
        """
        Transforms a numpy array from src_space to dst_space using OCIO or Built-in Math.
        Supports standard OCIO ColorSpaces, Roles, and OCIOv2 Display/View pairs ('View (Display)').
        """
        if image is None:
            return None
        if src_space == dst_space:
            return image

        if not self.config:
            return self._fallback_transform(image, src_space, dst_space)

        real_dst = None
        try:
            def resolve_space(space_name):
                if not space_name:
                    return None
                cs = self.config.getColorSpace(space_name)
                if cs is not None:
                    return cs.getName() if hasattr(cs, "getName") else space_name

                aliases = {
                    "Linear": [
                        "scene_linear", "Linear", "linear",
                        "Linear Rec.709 (sRGB)", "Linear - sRGB",
                        "Utility - Linear - sRGB", "Utility - Linear - Rec.709",
                        "lin_srgb", "ACEScg", "ACES - ACEScg",
                    ],
                    "Linear - sRGB": [
                        "Linear Rec.709 (sRGB)", "Linear - sRGB",
                        "Utility - Linear - sRGB", "Utility - Linear - Rec.709",
                        "lin_srgb", "scene_linear",
                    ],
                    "Linear Rec.709 (sRGB)": [
                        "Linear Rec.709 (sRGB)", "Linear - sRGB",
                        "Utility - Linear - sRGB", "Utility - Linear - Rec.709",
                        "lin_srgb", "scene_linear",
                    ],
                    "sRGB": [
                        "sRGB - Texture", "sRGB Encoded Rec.709 (sRGB)",
                        "Utility - sRGB - Texture", "Output - sRGB",
                        "sRGB - Display", "srgb",
                    ],
                    "Rec709": [
                        "Gamma 2.4 Rec.709 - Texture", "Rec.1886 Rec.709 - Display",
                        "Utility - Rec.709 - Texture", "Output - Rec.709",
                        "Rec.709 - Texture", "rec709",
                    ],
                    "Rec.709": [
                        "Gamma 2.4 Rec.709 - Texture", "Rec.1886 Rec.709 - Display",
                        "Utility - Rec.709 - Texture", "Output - Rec.709",
                        "Rec.709 - Texture", "rec709",
                    ],
                    "Rec.1886 Rec.709 - Display": [
                        "Rec.1886 Rec.709 - Display", "Gamma 2.4 Rec.709 - Texture",
                        "Output - Rec.709", "Rec.709", "rec709",
                    ],
                    "ACES 1.0 - SDR Video (Rec.1886 Rec.709 - Display)": [
                        "Output - Rec.709", "Output - sRGB",
                    ],
                    "ACES 1.0 - SDR Video (sRGB - Display)": [
                        "Output - sRGB", "sRGB - Display",
                    ],
                    "Un-tone-mapped (Rec.1886 Rec.709 - Display)": [
                        "Rec.1886 Rec.709 - Display", "Gamma 2.4 Rec.709 - Texture",
                    ],
                    "Un-tone-mapped (sRGB - Display)": [
                        "sRGB - Display", "sRGB - Texture", "sRGB Encoded Rec.709 (sRGB)",
                    ],
                    "ACEScg": ["ACEScg", "ACES - ACEScg", "acescg", "cg"],
                    "ACES2065-1": ["ACES2065-1", "ACES - ACES2065-1", "aces2065-1"],
                    "ACEScc": ["ACEScc", "ACES - ACEScc", "acescc"],
                    "ACEScct": ["ACEScct", "ACES - ACEScct", "acescct"],
                }

                for attempt in aliases.get(space_name, []):
                    cs_a = self.config.getColorSpace(attempt)
                    if cs_a is not None:
                        return cs_a.getName() if hasattr(cs_a, "getName") else attempt

                # Case-insensitive name scan
                low = space_name.strip().lower()
                for cs_item in self.config.getColorSpaces():
                    name = cs_item.getName()
                    if name.lower() == low:
                        return name
                return None

            src_dv = self._match_config_display_view(src_space)
            dst_dv = self._match_config_display_view(dst_space)

            real_src = None if src_dv else resolve_space(src_space)
            real_dst = None if dst_dv else resolve_space(dst_space)

            if src_dv and dst_dv:
                if src_dv == dst_dv:
                    return np.ascontiguousarray(image, dtype=np.float32)
                working_cs = resolve_space("Linear")
                if not working_cs:
                    raise ValueError("Cannot resolve scene_linear working color space.")
                proc_inv = self._build_display_view_processor(working_cs, src_dv[0], src_dv[1], inverse=True)
                lin_img = self._apply_ocio_processor(proc_inv, image)
                proc_fwd = self._build_display_view_processor(working_cs, dst_dv[0], dst_dv[1], inverse=False)
                return self._apply_ocio_processor(proc_fwd, lin_img)

            if src_dv and real_dst:
                processor = self._build_display_view_processor(real_dst, src_dv[0], src_dv[1], inverse=True)
                return self._apply_ocio_processor(processor, image)

            if real_src and dst_dv:
                processor = self._build_display_view_processor(real_src, dst_dv[0], dst_dv[1], inverse=False)
                return self._apply_ocio_processor(processor, image)

            if not real_src or not real_dst:
                missing = src_space if not (src_dv or real_src) else dst_space
                if missing not in self._printed_errors:
                    print(f"OCIO Bypassed: Color space '{missing}' not found in current config; using built-in transform.")
                    self._printed_errors.add(missing)
                return self._fallback_transform(image, src_space, real_dst or dst_space)

            if real_src == real_dst:
                return np.ascontiguousarray(image, dtype=np.float32)

            processor = self.config.getProcessor(real_src, real_dst)
            return self._apply_ocio_processor(processor, image)

        except Exception as e:
            error_msg = str(e)
            if error_msg not in self._printed_errors:
                print(f"OCIO Transform failed: {error_msg}")
                self._printed_errors.add(error_msg)
            return self._fallback_transform(image, src_space, real_dst or dst_space)

    def _fallback_transform(self, image: np.ndarray, src: str, dst: str) -> np.ndarray:
        """Built-in mathematical fallback for ACEScg, ACES2065-1, ACEScc/cct, sRGB, Rec709, and ACES Display/Views."""
        if image is None:
            return None
        if src == dst:
            return image

        def norm(s):
            s_low = (s or "").strip().lower()
            if s_low == "raw" or s_low.startswith("raw ("):
                return "raw"
            if "un-tone-mapped" in s_low:
                if "1886" in s_low or "709" in s_low:
                    return "rec1886_display"
                return "srgb_display"
            if (
                "sdr video" in s_low
                or "aces 1.0" in s_low
                or "output - rec.709" in s_low
                or "output - srgb" in s_low
            ):
                if "srgb" in s_low and "709" not in s_low and "1886" not in s_low:
                    return "aces_sdr_srgb"
                return "aces_sdr_rec1886"
            if "acescct" in s_low:
                return "acescct"
            if "acescc" in s_low:
                return "acescc"
            if "acescg" in s_low:
                return "acescg"
            if "2065" in s_low or (
                "aces" in s_low
                and "cg" not in s_low
                and "cc" not in s_low
                and "display" not in s_low
                and "output" not in s_low
            ):
                return "aces2065-1"
            if "1886" in s_low or "2.4" in s_low:
                return "rec1886_display"
            if "2.2" in s_low:
                return "gamma22_display"
            if "srgb" in s_low and "linear" not in s_low:
                return "srgb_display"
            if "709" in s_low and "linear" not in s_low:
                return "rec709_display"
            return "linear_srgb"

        src_n = norm(src)
        dst_n = norm(dst)

        if src_n == "raw" or dst_n == "raw":
            return np.array(image, dtype=np.float32, copy=True)
        if src_n == dst_n:
            return np.array(image, dtype=np.float32, copy=True)

        # Constants & matrices (AP1/AP0 <-> Linear sRGB with Bradford D60->D65)
        M_ACESCG_TO_SRGB = np.array([
            [ 1.705051, -0.621864, -0.083187],
            [-0.130256,  1.140803, -0.010547],
            [-0.024007, -0.128968,  1.152975],
        ], dtype=np.float32)
        M_SRGB_TO_ACESCG = np.array([
            [0.613097, 0.339523, 0.047379],
            [0.070194, 0.916354, 0.013452],
            [0.020616, 0.109570, 0.869815],
        ], dtype=np.float32)
        M_AP0_TO_SRGB = np.array([
            [ 2.521686, -1.134131, -0.387555],
            [-0.276480,  1.372719, -0.096239],
            [-0.015378, -0.152975,  1.168353],
        ], dtype=np.float32)
        M_SRGB_TO_AP0 = np.array([
            [0.439633, 0.382989, 0.177378],
            [0.089776, 0.813441, 0.096783],
            [0.017541, 0.111546, 0.870913],
        ], dtype=np.float32)

        def _inv_narkowicz_aces(y):
            a, b, c, d, e = 2.51, 0.03, 2.43, 0.59, 0.14
            yc = np.clip(y, 0.0, 1.0)
            A = a - c * yc
            B = b - d * yc
            disc = B * B + 4.0 * A * e * yc
            return (-B + np.sqrt(np.maximum(disc, 0.0))) / np.maximum(2.0 * A, 1e-6)

        def _fwd_narkowicz_aces(x):
            a, b, c, d, e = 2.51, 0.03, 2.43, 0.59, 0.14
            xc = np.maximum(x, 0.0)
            return np.clip((xc * (a * xc + b)) / np.maximum(xc * (c * xc + d) + e, 1e-6), 0.0, 1.0)

        out = np.array(image, dtype=np.float32, copy=True)
        rgb = out[..., :3]

        # 1. Convert source -> Linear sRGB
        if src_n == "acescg":
            rgb = np.dot(rgb, M_ACESCG_TO_SRGB.T)
        elif src_n == "aces2065-1":
            rgb = np.dot(rgb, M_AP0_TO_SRGB.T)
        elif src_n == "acescc":
            v = np.minimum(rgb, 1.468)
            ap1 = np.where(
                v <= -0.30136986301369863,
                (np.power(2.0, v * 17.52 - 9.72) - (2.0 ** -16)) * 2.0,
                np.power(2.0, v * 17.52 - 9.72),
            )
            rgb = np.dot(ap1, M_ACESCG_TO_SRGB.T)
        elif src_n == "acescct":
            v = np.minimum(rgb, 1.468)
            ap1 = np.where(
                v <= 0.155251141552511,
                (v - 0.0729055341958355) / 10.5402377416545,
                np.power(2.0, v * 17.52 - 9.72),
            )
            rgb = np.dot(ap1, M_ACESCG_TO_SRGB.T)
        elif src_n == "srgb_display":
            rgb = np.where(rgb <= 0.04045, rgb / 12.92, np.power(np.clip((rgb + 0.055) / 1.055, 0.0, None), 2.4))
        elif src_n == "rec709_display":
            rgb = np.where(rgb < 0.081, rgb / 4.5, np.power(np.clip((rgb + 0.099) / 1.099, 0.0, None), 1.0 / 0.45))
        elif src_n == "rec1886_display":
            rgb = np.power(np.clip(rgb, 0.0, None), 2.4)
        elif src_n == "gamma22_display":
            rgb = np.power(np.clip(rgb, 0.0, None), 2.2)
        elif src_n == "aces_sdr_rec1886":
            y = np.power(np.clip(rgb, 0.0, 1.0), 2.4)
            rgb = _inv_narkowicz_aces(y)
        elif src_n == "aces_sdr_srgb":
            y = np.where(rgb <= 0.04045, rgb / 12.92, np.power(np.clip((rgb + 0.055) / 1.055, 0.0, 1.0), 2.4))
            rgb = _inv_narkowicz_aces(y)

        # 2. Convert Linear sRGB -> destination
        if dst_n == "acescg":
            rgb = np.dot(rgb, M_SRGB_TO_ACESCG.T)
        elif dst_n == "aces2065-1":
            rgb = np.dot(rgb, M_SRGB_TO_AP0.T)
        elif dst_n == "acescc":
            ap1 = np.dot(rgb, M_SRGB_TO_ACESCG.T)
            rgb = np.where(
                ap1 <= 0.0,
                (np.log2(2.0 ** -16) + 9.72) / 17.52,
                np.where(
                    ap1 < (2.0 ** -15),
                    (np.log2(2.0 ** -16 + ap1 * 0.5) + 9.72) / 17.52,
                    (np.log2(np.maximum(ap1, 2.0 ** -16)) + 9.72) / 17.52,
                ),
            )
        elif dst_n == "acescct":
            ap1 = np.dot(rgb, M_SRGB_TO_ACESCG.T)
            rgb = np.where(
                ap1 <= 0.0078125,
                10.5402377416545 * ap1 + 0.0729055341958355,
                (np.log2(np.maximum(ap1, 2.0 ** -16)) + 9.72) / 17.52,
            )
        elif dst_n == "srgb_display":
            rgb = np.where(rgb <= 0.0031308, rgb * 12.92, 1.055 * np.power(np.clip(rgb, 0.0, None), 1.0 / 2.4) - 0.055)
        elif dst_n == "rec709_display":
            rgb = np.where(rgb < 0.018, rgb * 4.5, 1.099 * np.power(np.clip(rgb, 0.0, None), 0.45) - 0.099)
        elif dst_n == "rec1886_display":
            rgb = np.power(np.clip(rgb, 0.0, None), 1.0 / 2.4)
        elif dst_n == "gamma22_display":
            rgb = np.power(np.clip(rgb, 0.0, None), 1.0 / 2.2)
        elif dst_n == "aces_sdr_rec1886":
            y = _fwd_narkowicz_aces(rgb)
            rgb = np.power(y, 1.0 / 2.4)
        elif dst_n == "aces_sdr_srgb":
            y = _fwd_narkowicz_aces(rgb)
            rgb = np.where(y <= 0.0031308, y * 12.92, 1.055 * np.power(np.clip(y, 0.0, 1.0), 1.0 / 2.4) - 0.055)

        out[..., :3] = np.nan_to_num(rgb, nan=0.0, posinf=65504.0, neginf=0.0)
        return np.ascontiguousarray(out, dtype=np.float32)
