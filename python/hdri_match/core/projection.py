import numpy as np
try:
    import cv2
    HAS_CV2 = True
except ImportError:
    HAS_CV2 = False

def _bilinear_remap(img, u, v, mode='wrap'):
    """Bilinear remap in pure numpy or OpenCV if available."""
    if HAS_CV2:
        border_mode = cv2.BORDER_WRAP if mode == 'wrap' else cv2.BORDER_TRANSPARENT
        return cv2.remap(
            img.astype(np.float32),
            u.astype(np.float32),
            v.astype(np.float32),
            interpolation=cv2.INTER_LINEAR,
            borderMode=border_mode
        )
    h, w = img.shape[:2]
    if mode == 'wrap':
        u_mod = u % float(w)
    else:
        u_mod = np.clip(u, 0.0, float(w - 1))
    v_clamped = np.clip(v, 0.0, float(h - 1))

    u0 = np.floor(u_mod).astype(np.int64)
    u1 = (u0 + 1) % w if mode == 'wrap' else np.clip(u0 + 1, 0, w - 1)
    v0 = np.floor(v_clamped).astype(np.int64)
    v1 = np.clip(v0 + 1, 0, h - 1)

    du = (u_mod - u0.astype(np.float32))[..., np.newaxis]
    dv = (v_clamped - v0.astype(np.float32))[..., np.newaxis]

    ia = img[v0, u0]
    ib = img[v0, u1]
    ic = img[v1, u0]
    id_ = img[v1, u1]

    wa = (1.0 - du) * (1.0 - dv)
    wb = du * (1.0 - dv)
    wc = (1.0 - du) * dv
    wd = du * dv

    out = ia * wa + ib * wb + ic * wc + id_ * wd
    if mode != 'wrap':
        invalid = (u < 0) | (u >= w) | (v < 0) | (v >= h)
        out[invalid] = 0.0
    return out.astype(img.dtype)

class SphericalProjector:
    @staticmethod
    def equi_to_rect(equi_img, cx, cy, pw_eq, ph_eq):
        """Extract a rectilinear (perspective) patch from an equirectangular image."""
        H, W = equi_img.shape[:2]
        
        f = W / (2 * np.pi)
        lambda_0 = (cx / W - 0.5) * 2 * np.pi
        phi_1 = (0.5 - cy / H) * np.pi
        
        # Calculate required rectilinear dimensions to encompass the equirectangular ROI
        lon_min = lambda_0 - (pw_eq / W) * np.pi
        lon_max = lambda_0 + (pw_eq / W) * np.pi
        lat_min = (0.5 - (cy + ph_eq/2) / H) * np.pi
        lat_max = (0.5 - (cy - ph_eq/2) / H) * np.pi
        
        # Sample points along the 4 edges of the bounding box
        edge_pts = 50
        lons = np.concatenate([
            np.linspace(lon_min, lon_max, edge_pts),
            np.linspace(lon_min, lon_max, edge_pts),
            np.full(edge_pts, lon_min),
            np.full(edge_pts, lon_max)
        ])
        lats = np.concatenate([
            np.full(edge_pts, lat_min),
            np.full(edge_pts, lat_max),
            np.linspace(lat_min, lat_max, edge_pts),
            np.linspace(lat_min, lat_max, edge_pts)
        ])
        
        cos_c = np.sin(phi_1) * np.sin(lats) + np.cos(phi_1) * np.cos(lats) * np.cos(lons - lambda_0)
        valid = cos_c > 1e-5
        
        if np.any(valid):
            x = (f * np.cos(lats[valid]) * np.sin(lons[valid] - lambda_0)) / cos_c[valid]
            y = (f * (np.cos(phi_1) * np.sin(lats[valid]) - np.sin(phi_1) * np.cos(lats[valid]) * np.cos(lons[valid] - lambda_0))) / cos_c[valid]
            
            max_x = min(np.max(np.abs(x)), W * 2)
            max_y = min(np.max(np.abs(y)), H * 2)
        else:
            max_x = pw_eq / 2
            max_y = ph_eq / 2
            
        pw = int(np.ceil(max_x * 2)) + 4
        ph = int(np.ceil(max_y * 2)) + 4
        
        # Ensure minimum size
        pw = max(pw, int(pw_eq))
        ph = max(ph, int(ph_eq))

        u, v = np.meshgrid(np.arange(pw), np.arange(ph))
        x = u - pw / 2
        y = ph / 2 - v
        rho = np.sqrt(x**2 + y**2)
        c = np.arctan2(rho, f)
        
        sin_c = np.sin(c)
        cos_c = np.cos(c)
        
        safe_rho = np.where(rho == 0, 1e-5, rho)
        
        lat = np.arcsin(cos_c * np.sin(phi_1) + y * sin_c * np.cos(phi_1) / safe_rho)
        lon = lambda_0 + np.arctan2(x * sin_c, safe_rho * np.cos(phi_1) * cos_c - y * np.sin(phi_1) * sin_c)
        
        # Normalize lon to [-pi, pi]
        lon = (lon + np.pi) % (2 * np.pi) - np.pi
        
        equi_u = (lon / (2 * np.pi) + 0.5) * W
        equi_v = (0.5 - lat / np.pi) * H
        
        rect_img = cv2.remap(equi_img, equi_u.astype(np.float32), equi_v.astype(np.float32), interpolation=cv2.INTER_LINEAR, borderMode=cv2.BORDER_WRAP)
        return rect_img

    @staticmethod
    def rect_to_equi_roi(rect_img, cx, cy, out_h, out_w, px1, px2, py1, py2):
        """Warp a rectilinear patch back into an equirectangular ROI bounding box."""
        ph, pw = rect_img.shape[:2]
        lambda_0 = (cx / out_w - 0.5) * 2 * np.pi
        phi_1 = (0.5 - cy / out_h) * np.pi
        f = out_w / (2 * np.pi)
        
        roi_w = px2 - px1
        roi_h = py2 - py1
        
        if roi_w <= 0 or roi_h <= 0:
            return np.zeros((roi_h, roi_w, rect_img.shape[2]), dtype=rect_img.dtype)
            
        equi_u, equi_v = np.meshgrid(np.arange(px1, px2), np.arange(py1, py2))
        lon = (equi_u / out_w - 0.5) * 2 * np.pi
        lat = (0.5 - equi_v / out_h) * np.pi
        
        cos_c = np.sin(phi_1) * np.sin(lat) + np.cos(phi_1) * np.cos(lat) * np.cos(lon - lambda_0)
        valid = cos_c > 0
        
        safe_cos_c = np.where(cos_c == 0, 1e-5, cos_c)
        x = (f * np.cos(lat) * np.sin(lon - lambda_0)) / safe_cos_c
        y = (f * (np.cos(phi_1) * np.sin(lat) - np.sin(phi_1) * np.cos(lat) * np.cos(lon - lambda_0))) / safe_cos_c
        
        u = x + pw / 2
        v = ph / 2 - y
        
        u[~valid] = -1
        v[~valid] = -1
        
        roi_proj = _bilinear_remap(rect_img, u.astype(np.float32), v.astype(np.float32), mode='transparent')
        return roi_proj


class GroundProjector:
    """
    Tripod-based Ground / Local IBL Projector for VFX LookDev and Lighting.
    Remaps and projects equirectangular HDRI environments onto a horizontal ground plane
    at Y = 0 assuming the panoramic nodal point was mounted at height H (tripod height).
    """

    @staticmethod
    def project_ground_coordinates(x, y, z, tripod_height=1.5):
        """
        Given 3D world coordinates P = (x, y, z), compute equirectangular UV in [0, 1]
        as seen from panoramic nodal point (0, tripod_height, 0).
        """
        dx = np.asarray(x, dtype=np.float32)
        dy = np.asarray(y, dtype=np.float32) - float(tripod_height)
        dz = np.asarray(z, dtype=np.float32)
        dist = np.sqrt(dx * dx + dy * dy + dz * dz)
        dist = np.maximum(dist, 1e-6)

        dir_x = dx / dist
        dir_y = dy / dist
        dir_z = dz / dist

        # Azimuth / Longitude: [-pi, pi] -> [0, 1]
        u = (np.arctan2(dir_x, -dir_z) / (2.0 * np.pi)) + 0.5
        # Elevation / Latitude: [-pi/2, pi/2] -> [0, 1]
        v = 0.5 - (np.arcsin(np.clip(dir_y, -1.0, 1.0)) / np.pi)
        return u, v

    @staticmethod
    def warp_equirectangular_ground(equi_img, tripod_height=1.5, ground_radius=10.0, feather=0.15):
        """
        Warp an equirectangular image so the ground hemisphere is re-projected
        with finite ground plane perspective and blends smoothly into the distant horizon.

        Args:
            equi_img: (H, W, 3) float32 equirectangular image
            tripod_height: float, height of capture camera above ground in meters (e.g. 1.5m)
            ground_radius: float, radius of the local ground plane in meters (e.g. 10m)
            feather: float, edge blend feather fraction [0.01, 1.0]

        Returns:
            (H, W, 3) float32 warped equirectangular image
        """
        if equi_img is None:
            return None
        h, w = equi_img.shape[:2]
        H = max(0.1, float(tripod_height))
        R = max(0.5, float(ground_radius))

        # Generate pixel coordinates for target equirectangular output
        u_out, v_out = np.meshgrid(
            np.linspace(0.0, 1.0, w, endpoint=False, dtype=np.float32),
            np.linspace(0.0, 1.0, h, endpoint=False, dtype=np.float32)
        )

        # Output spherical angles: azimuth phi in [-pi, pi], elevation theta in [pi/2, -pi/2]
        phi = (u_out - 0.5) * (2.0 * np.pi)
        theta = (0.5 - v_out) * np.pi

        # Unit ray directions
        cos_theta = np.cos(theta)
        dx = np.sin(phi) * cos_theta
        dy = np.sin(theta)
        dz = -np.cos(phi) * cos_theta

        # Downward rays (dy < 0) intersect ground plane Y = 0 at distance t = -H / dy
        is_ground = dy < -1e-4
        t = np.where(is_ground, -H / np.minimum(dy, -1e-5), R * 100.0)

        gx = dx * t
        gz = dz * t
        r_ground = np.sqrt(gx * gx + gz * gz)

        # Transition blend from local ground (r_ground <= R) to distant sky/horizon
        # Feather region around R
        f_width = max(0.01, float(feather)) * R
        ground_weight = np.clip((R + f_width - r_ground) / max(1e-4, f_width), 0.0, 1.0)
        ground_mask = np.where(is_ground, ground_weight, 0.0)

        # For ground points, reproject to original camera perspective
        # Original ray from camera center (0, H, 0) to ground point (gx, 0, gz):
        cam_dx = gx
        cam_dy = -H
        cam_dz = gz
        cam_dist = np.sqrt(cam_dx * cam_dx + cam_dy * cam_dy + cam_dz * cam_dz)
        cam_dist = np.maximum(cam_dist, 1e-6)

        orig_dir_x = cam_dx / cam_dist
        orig_dir_y = cam_dy / cam_dist
        orig_dir_z = cam_dz / cam_dist

        u_ground = ((np.arctan2(orig_dir_x, -orig_dir_z) / (2.0 * np.pi)) + 0.5) * float(w)
        v_ground = (0.5 - (np.arcsin(np.clip(orig_dir_y, -1.0, 1.0)) / np.pi)) * float(h)

        # Identity coordinates for sky / distant areas
        u_ident = u_out * float(w)
        v_ident = v_out * float(h)

        # Blend sampling map
        u_sample = u_ground * ground_mask + u_ident * (1.0 - ground_mask)
        v_sample = v_ground * ground_mask + v_ident * (1.0 - ground_mask)

        warped = _bilinear_remap(
            equi_img.astype(np.float32),
            u_sample.astype(np.float32),
            v_sample.astype(np.float32),
            mode='wrap'
        )
        return warped

