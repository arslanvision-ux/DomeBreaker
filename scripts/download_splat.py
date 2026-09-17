"""
SuperSplat Scene Downloader & PLY Converter
Supports both standard single SOG scenes (meta.json) and Octree Multi-LOD scenes (lod-meta.json).

Usage:
    uv run --with pillow,numpy python scripts/download_splat.py <scene_url_or_hash> [output_dir] [--level LOD_LEVEL]

Examples:
    # Single SOG scene:
    uv run --with pillow,numpy python scripts/download_splat.py https://superspl.at/scene/8bfb3a06 scenes/splats

    # Octree Multi-LOD scene (full resolution Level 0):
    uv run --with pillow,numpy python scripts/download_splat.py https://superspl.at/scene/2243cd29 scenes/splats
"""

import os
import sys
import re
import json
import time
import shutil
import urllib.request
import urllib.error
import zipfile
import concurrent.futures
import numpy as np
from PIL import Image

def parse_scene_hash(input_str: str) -> str:
    m = re.search(r'([a-f0-9]{8})', input_str.lower())
    if not m:
        raise ValueError(f"Could not extract 8-character scene hash from: {input_str}")
    return m.group(1)

def download_file_with_retry(url: str, dest_path: str, retries: int = 3, timeout: int = 15):
    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
    if os.path.exists(dest_path) and os.path.getsize(dest_path) > 0:
        return
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                with open(dest_path, 'wb') as f:
                    shutil.copyfileobj(resp, f)
            return
        except Exception as e:
            if attempt == retries - 1:
                raise RuntimeError(f"Failed to download {url} after {retries} attempts: {e}")
            time.sleep(1.0 * (attempt + 1))

def decompress_chunk_data(meta: dict, chunk_dir: str):
    count = meta.get("count", 0)
    mins, maxs = meta["means"]["mins"], meta["means"]["maxs"]
    f = mins[0]; p = maxs[0] - mins[0] or 1.0
    m = mins[1]; g = maxs[1] - mins[1] or 1.0
    _ = mins[2]; v = maxs[2] - mins[2] or 1.0

    means_l_img = Image.open(os.path.join(chunk_dir, "means_l.webp")).convert("RGBA")
    means_u_img = Image.open(os.path.join(chunk_dir, "means_u.webp")).convert("RGBA")
    c = np.array(means_l_img, dtype=np.uint16).reshape(-1, 4)[:count]
    d = np.array(means_u_img, dtype=np.uint16).reshape(-1, 4)[:count]

    n_val = c[:, 0] | (d[:, 0] << 8)
    s_val = c[:, 1] | (d[:, 1] << 8)
    r_val = c[:, 2] | (d[:, 2] << 8)

    def NP_vec(t):
        e = np.abs(t)
        n = np.exp(e) - 1.0
        return np.where(t < 0, -n, n)

    x = NP_vec(f + p * (n_val.astype(np.float32) / 65535.0)).astype(np.float32)
    y = NP_vec(m + g * (s_val.astype(np.float32) / 65535.0)).astype(np.float32)
    z = NP_vec(_ + v * (r_val.astype(np.float32) / 65535.0)).astype(np.float32)
    del c, d, n_val, s_val, r_val

    # Quats (Rotations: rot_0=w, rot_1=x, rot_2=y, rot_3=z)
    quats_img = Image.open(os.path.join(chunk_dir, "quats.webp")).convert("RGBA")
    S = np.array(quats_img, dtype=np.uint8).reshape(-1, 4)[:count]
    S0 = S[:, 0].astype(np.float32)
    S1 = S[:, 1].astype(np.float32)
    S2 = S[:, 2].astype(np.float32)
    S3 = S[:, 3]

    zP = np.sqrt(2.0, dtype=np.float32)
    a = (S0 / 255.0 * 2.0 - 1.0) / zP
    r = (S1 / 255.0 * 2.0 - 1.0) / zP
    o = (S2 / 255.0 * 2.0 - 1.0) / zP
    c_sq = 1.0 - (a*a + r*r + o*o)
    fourth = np.sqrt(np.maximum(0.0, c_sq))

    rot_0 = np.zeros(count, dtype=np.float32)
    rot_1 = np.zeros(count, dtype=np.float32)
    rot_2 = np.zeros(count, dtype=np.float32)
    rot_3 = np.zeros(count, dtype=np.float32)

    mask0 = (S3 == 252)
    rot_1[mask0] = a[mask0]; rot_2[mask0] = r[mask0]; rot_3[mask0] = o[mask0]; rot_0[mask0] = fourth[mask0]
    mask1 = (S3 == 253)
    rot_0[mask1] = a[mask1]; rot_2[mask1] = r[mask1]; rot_3[mask1] = o[mask1]; rot_1[mask1] = fourth[mask1]
    mask2 = (S3 == 254)
    rot_0[mask2] = a[mask2]; rot_1[mask2] = r[mask2]; rot_3[mask2] = o[mask2]; rot_2[mask2] = fourth[mask2]
    mask3 = (S3 == 255)
    rot_0[mask3] = a[mask3]; rot_1[mask3] = r[mask3]; rot_2[mask3] = o[mask3]; rot_3[mask3] = fourth[mask3]
    mask_other = (S3 < 252) | (S3 > 255)
    rot_0[mask_other] = 1.0
    del S, S0, S1, S2, S3, a, r, o, c_sq, fourth

    # Scales
    scales_img = Image.open(os.path.join(chunk_dir, "scales.webp")).convert("RGBA")
    w = np.array(scales_img, dtype=np.uint8).reshape(-1, 4)[:count]
    T = np.array(meta["scales"]["codebook"], dtype=np.float32)
    scale_0 = T[w[:, 0]]
    scale_1 = T[w[:, 1]]
    scale_2 = T[w[:, 2]]
    del w, T

    # SH0 and Opacity
    sh0_img = Image.open(os.path.join(chunk_dir, "sh0.webp")).convert("RGBA")
    C = np.array(sh0_img, dtype=np.uint8).reshape(-1, 4)[:count]
    E = np.array(meta["sh0"]["codebook"], dtype=np.float32)
    f_dc_0 = E[C[:, 0]]
    f_dc_1 = E[C[:, 1]]
    f_dc_2 = E[C[:, 2]]
    u_op = np.clip(C[:, 3].astype(np.float32) / 255.0, 1e-6, 0.999999)
    opacity = np.log(u_op / (1.0 - u_op)).astype(np.float32)
    del C, E, u_op

    # SH Rest (Degree 1-3)
    has_shN = "shN" in meta and meta["shN"].get("bands", 0) > 0
    if has_shN:
        P = 15
        I_coeffs = 45
        D = np.array(meta["shN"]["codebook"], dtype=np.float32)
        F_count = meta["shN"]["count"]

        shN_centroids_img = Image.open(os.path.join(chunk_dir, "shN_centroids.webp")).convert("RGBA")
        R = np.array(shN_centroids_img, dtype=np.uint8)
        k_height, L_width = R.shape[0], R.shape[1]

        shN_labels_img = Image.open(os.path.join(chunk_dir, "shN_labels.webp")).convert("RGBA")
        M = np.array(shN_labels_img, dtype=np.uint8).reshape(-1, 4)[:count]

        cluster = M[:, 0].astype(np.uint32) | (M[:, 1].astype(np.uint32) << 8)
        valid_mask = (cluster < F_count)
        s_row = np.where(valid_mask, cluster // 64, 0)
        i_col = np.where(valid_mask, (cluster % 64) * P, 0)
        row_valid = valid_mask & (s_row < k_height)

        f_rest = np.zeros((count, I_coeffs), dtype=np.float32)
        for e_idx in range(P):
            col = i_col + e_idx
            col_valid = row_valid & (col < L_width)
            safe_col = np.where(col_valid, col, 0)
            safe_row = np.where(col_valid, s_row, 0)
            pix = R[safe_row, safe_col]
            f_rest[:, e_idx] = np.where(col_valid, D[pix[:, 0]], 0.0)
            f_rest[:, e_idx + P] = np.where(col_valid, D[pix[:, 1]], 0.0)
            f_rest[:, e_idx + 2*P] = np.where(col_valid, D[pix[:, 2]], 0.0)
        del M, R, D, cluster, valid_mask, s_row, i_col, row_valid
    else:
        f_rest = None

    dtype_fields = [
        ("x", "<f4"), ("y", "<f4"), ("z", "<f4"),
        ("nx", "<f4"), ("ny", "<f4"), ("nz", "<f4"),
        ("f_dc_0", "<f4"), ("f_dc_1", "<f4"), ("f_dc_2", "<f4")
    ]
    if has_shN:
        for i in range(45):
            dtype_fields.append((f"f_rest_{i}", "<f4"))
    dtype_fields.extend([
        ("opacity", "<f4"),
        ("scale_0", "<f4"), ("scale_1", "<f4"), ("scale_2", "<f4"),
        ("rot_0", "<f4"), ("rot_1", "<f4"), ("rot_2", "<f4"), ("rot_3", "<f4")
    ])

    vertex_data = np.empty(count, dtype=np.dtype(dtype_fields))
    vertex_data["x"] = x; vertex_data["y"] = y; vertex_data["z"] = z
    vertex_data["nx"] = 0; vertex_data["ny"] = 0; vertex_data["nz"] = 0
    vertex_data["f_dc_0"] = f_dc_0; vertex_data["f_dc_1"] = f_dc_1; vertex_data["f_dc_2"] = f_dc_2
    if has_shN:
        for i in range(45):
            vertex_data[f"f_rest_{i}"] = f_rest[:, i]
    vertex_data["opacity"] = opacity
    vertex_data["scale_0"] = scale_0; vertex_data["scale_1"] = scale_1; vertex_data["scale_2"] = scale_2
    vertex_data["rot_0"] = rot_0; vertex_data["rot_1"] = rot_1; vertex_data["rot_2"] = rot_2; vertex_data["rot_3"] = rot_3

    return vertex_data, dtype_fields

def get_ply_header(total_count: int, has_shN: bool) -> bytes:
    dtype_fields = [
        ("x", "<f4"), ("y", "<f4"), ("z", "<f4"),
        ("nx", "<f4"), ("ny", "<f4"), ("nz", "<f4"),
        ("f_dc_0", "<f4"), ("f_dc_1", "<f4"), ("f_dc_2", "<f4")
    ]
    if has_shN:
        for i in range(45):
            dtype_fields.append((f"f_rest_{i}", "<f4"))
    dtype_fields.extend([
        ("opacity", "<f4"),
        ("scale_0", "<f4"), ("scale_1", "<f4"), ("scale_2", "<f4"),
        ("rot_0", "<f4"), ("rot_1", "<f4"), ("rot_2", "<f4"), ("rot_3", "<f4")
    ])

    header_lines = [
        "ply",
        "format binary_little_endian 1.0",
        f"element vertex {total_count}"
    ]
    for name, _ in dtype_fields:
        header_lines.append(f"property float {name}")
    header_lines.append("end_header\n")
    return "\n".join(header_lines).encode("ascii")

def download_and_convert(scene_hash: str, output_dir: str, lod_level: int = 0):
    s3_base = f"https://s3-eu-west-1.amazonaws.com/splats.playcanvas.com/{scene_hash}/v1/"
    os.makedirs(output_dir, exist_ok=True)
    temp_dir = os.path.join(output_dir, f"_temp_{scene_hash}")
    os.makedirs(temp_dir, exist_ok=True)

    print(f"=== SuperSplat Downloader: Scene {scene_hash} ===")
    print(f"Endpoint: {s3_base}")

    # Check if scene is standard SOG (meta.json) or Octree LOD (lod-meta.json)
    is_lod = False
    meta_path = os.path.join(temp_dir, "meta.json")
    lod_meta_path = os.path.join(temp_dir, "lod-meta.json")

    try:
        req = urllib.request.Request(s3_base + "meta.json", headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=10) as resp:
            with open(meta_path, 'wb') as f:
                shutil.copyfileobj(resp, f)
        print("Detected format: Standard Single-SOG scene (meta.json)")
    except urllib.error.HTTPError as e:
        if e.code in (403, 404):
            print(f"meta.json not found (HTTP {e.code}). Checking for Octree LOD (lod-meta.json)...")
            is_lod = True
            req = urllib.request.Request(s3_base + "lod-meta.json", headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=15) as resp:
                with open(lod_meta_path, 'wb') as f:
                    shutil.copyfileobj(resp, f)
            print("Detected format: Octree Multi-LOD scene (lod-meta.json)!")
        else:
            raise

    ply_path = os.path.join(output_dir, f"{scene_hash}.ply")

    if not is_lod:
        # Standard Single SOG
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)
        count = meta.get("count", 0)
        print(f"Total Gaussians: {count:,}")

        files = ["meta.json"]
        for key in ["means", "scales", "quats", "sh0", "shN"]:
            if key in meta and "files" in meta[key]:
                files.extend(meta[key]["files"])
        unique_files = list(dict.fromkeys(files))

        print(f"Downloading {len(unique_files)} asset files...")
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
            futures = [executor.submit(download_file_with_retry, s3_base + fn, os.path.join(temp_dir, fn)) for fn in unique_files]
            concurrent.futures.wait(futures)

        # Create SOG archive
        sog_path = os.path.join(output_dir, f"{scene_hash}.sog")
        print(f"Packaging SOG: {sog_path}")
        with zipfile.ZipFile(sog_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for fn in unique_files:
                zf.write(os.path.join(temp_dir, fn), arcname=fn)

        print(f"Converting to standard 3DGS PLY: {ply_path}...")
        vdata, _ = decompress_chunk_data(meta, temp_dir)
        has_shN = "shN" in meta and meta["shN"].get("bands", 0) > 0
        header = get_ply_header(count, has_shN)
        with open(ply_path, "wb") as f_out:
            f_out.write(header)
            vdata.tofile(f_out)

    else:
        # Octree Multi-LOD Scene
        with open(lod_meta_path, "r", encoding="utf-8") as f:
            lod_meta = json.load(f)

        counts = lod_meta.get("counts", [])
        filenames = lod_meta.get("filenames", [])
        print(f"LOD Levels available: {len(counts)} (Splats per level: {counts})")
        print(f"Selected LOD Level: {lod_level} ({counts[lod_level]:,} splats)")

        # Collect chunk meta files for the target level (e.g. level 0 -> prefix '0_')
        prefix = f"{lod_level}_"
        chunk_metas = [fn for fn in filenames if fn.startswith(prefix)]
        chunk_folders = [fn.split("/")[0] for fn in chunk_metas]
        total_splats = counts[lod_level]

        print(f"Target level {lod_level} has {len(chunk_folders)} chunk partitions.")

        # Download sample chunk meta to check shN presence
        first_chunk_meta_url = s3_base + chunk_metas[0]
        req = urllib.request.Request(first_chunk_meta_url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=10) as resp:
            first_meta = json.loads(resp.read().decode('utf-8'))
        has_shN = "shN" in first_meta and first_meta["shN"].get("bands", 0) > 0

        # Initialize PLY file with complete vertex count
        print(f"Streaming full-resolution PLY: {ply_path} ({total_splats:,} vertices)...")
        header = get_ply_header(total_splats, has_shN)
        t0 = time.time()
        written_splats = 0

        with open(ply_path, "wb") as f_out:
            f_out.write(header)

            for idx, c_name in enumerate(chunk_folders):
                c_t0 = time.time()
                c_dir = os.path.join(temp_dir, c_name)
                os.makedirs(c_dir, exist_ok=True)
                c_s3_base = s3_base + c_name + "/"

                # Download chunk meta
                c_meta_path = os.path.join(c_dir, "meta.json")
                download_file_with_retry(c_s3_base + "meta.json", c_meta_path)
                with open(c_meta_path, "r", encoding="utf-8") as f:
                    c_meta = json.load(f)

                # Determine chunk files
                c_files = []
                for k in ["means", "scales", "quats", "sh0", "shN"]:
                    if k in c_meta and "files" in c_meta[k]:
                        c_files.extend(c_meta[k]["files"])
                c_files = list(dict.fromkeys(c_files))

                # Download chunk textures concurrently
                with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
                    futures = [executor.submit(download_file_with_retry, c_s3_base + fn, os.path.join(c_dir, fn)) for fn in c_files]
                    concurrent.futures.wait(futures)

                # Decompress chunk into vertex data
                vdata, _ = decompress_chunk_data(c_meta, c_dir)
                vdata.tofile(f_out)
                c_count = len(vdata)
                written_splats += c_count
                pct = (written_splats / total_splats) * 100.0

                print(f"  [{idx+1:02d}/{len(chunk_folders)}] Chunk {c_name:5s}: +{c_count:,} splats (Total: {written_splats:,}/{total_splats:,} [{pct:.1f}%]) in {time.time() - c_t0:.2f}s")

                # Clean up chunk textures to save RAM and disk
                shutil.rmtree(c_dir, ignore_errors=True)

        print(f"Total time: {time.time() - t0:.2f}s")

    # Clean up temp folder
    shutil.rmtree(temp_dir, ignore_errors=True)

    ply_size_mb = os.path.getsize(ply_path) / (1024 * 1024)
    print(f"\nSuccessfully generated 3DGS PLY: {ply_path}")
    print(f"File Size: {ply_size_mb:.2f} MB")
    print("Done!")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: uv run --with pillow,numpy python scripts/download_splat.py <scene_url_or_hash> [output_dir] [--level LOD_LEVEL]")
        sys.exit(1)

    scene_hash = parse_scene_hash(sys.argv[1])
    out_dir = sys.argv[2] if len(sys.argv) > 2 and not sys.argv[2].startswith("--") else "scenes/splats"
    
    level = 0
    if "--level" in sys.argv:
        l_idx = sys.argv.index("--level")
        if l_idx + 1 < len(sys.argv):
            level = int(sys.argv[l_idx + 1])

    download_and_convert(scene_hash, out_dir, lod_level=level)
