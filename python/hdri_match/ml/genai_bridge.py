import io
import os
import site
import sys
import numpy as np
from PIL import Image

try:
    _RESAMPLE_LANCZOS = Image.Resampling.LANCZOS
except AttributeError:
    _RESAMPLE_LANCZOS = Image.LANCZOS


class GenAIBridge:
    def __init__(self, api_key=""):
        self.api_key = api_key
        self.backend = "Google GenAI (Nano Banana)"

    def generate_inpaint(self, hdri_array, mask_array, prompt, neg_prompt=None, ckpt=None, steps=None, cfg=None):
        try:
            user_site = site.getusersitepackages()
            if user_site not in sys.path:
                sys.path.append(user_site)

            # Force add user site to google namespace path if host already imported google.protobuf
            if 'google' in sys.modules:
                google_user_path = os.path.join(user_site, 'google')
                if os.path.exists(google_user_path):
                    google_mod = sys.modules['google']
                    if hasattr(google_mod, '__path__') and google_user_path not in google_mod.__path__:
                        google_mod.__path__.append(google_user_path)

            from google import genai
            from google.genai import types  # noqa: F401
        except ImportError:
            python_exe = sys.executable
            if "nuke" in os.path.basename(python_exe).lower():
                python_exe = os.path.join(os.path.dirname(python_exe), "python.exe")
            return hdri_array.copy(), (
                "google-genai module is not installed in this Python environment.\n"
                "Automatic runtime pip installation is disabled for DCC stability.\n"
                f"To install manually in an offline/external shell, run:\n\"{python_exe}\" -m pip install --user google-genai"
            )

        try:
            print(f"[{self.backend}] Initializing GenAI with provided API key...")
            client = genai.Client(api_key=self.api_key)

            # Convert linear array to 8-bit sRGB for Nano Banana
            srgb_array = np.clip(hdri_array, 0, 1) ** (1.0 / 2.2)
            img_u8 = (srgb_array * 255).astype(np.uint8)
            mask_u8 = (np.clip(mask_array, 0, 1) * 255).astype(np.uint8)

            # Convert to PIL Images
            original_img = Image.fromarray(img_u8)
            mask_img = Image.fromarray(mask_u8)

            print(f"[{self.backend}] Prompt: '{prompt}'")

            response = client.models.generate_content(
                model="gemini-3.1-flash-image",
                contents=[
                    original_img,
                    mask_img,
                    prompt
                ]
            )

            if response.candidates and response.candidates[0].content.parts:
                for part in response.candidates[0].content.parts:
                    if part.inline_data:
                        generated_bytes = part.inline_data.data
                        pil_out = Image.open(io.BytesIO(generated_bytes)).convert("RGB")
                        req_h, req_w = hdri_array.shape[:2]
                        if pil_out.size != (req_w, req_h):
                            pil_out = pil_out.resize((req_w, req_h), _RESAMPLE_LANCZOS)
                        rgb_out = np.asarray(pil_out, dtype=np.float32) / 255.0
                        linear_out = rgb_out ** 2.2
                        print(f"[{self.backend}] Inpainting generation successful!")
                        return linear_out, None

            return hdri_array.copy(), "Google GenAI API returned empty or invalid output."

        except Exception as e:
            print(f"[{self.backend}] Request failed: {e}")
            return hdri_array.copy(), f"Google GenAI API Request Failed:\n{e}"
