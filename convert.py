"""DJI Osmo Pocket 4P: DNG -> JPG that reproduces the in-camera rendering.

Every DNG from the camera embeds a 1280x720 preview that *is* the in-camera JPG
(same colour, tone and local tone mapping; dE < 1 vs the real JPG).  We render the
DNG linearly at full resolution, fit a global cubic colour mapping plus a smooth
local affine correction (guided filter) that maps the linear render onto the
preview at low resolution, and apply the upsampled mapping at full resolution.
"""
import argparse
import glob
import os
import subprocess

import cv2
import numpy as np

from dngmeta import read_meta
from render import XYZ_TO_PROPHOTO, camera_to_xyz_d50, load_camera_rgb


# ----------------------------------------------------------------- helpers
def read_preview(dng):
    data = subprocess.run(["exiftool", "-b", "-PreviewImage", dng],
                          capture_output=True, check=True).stdout
    im = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    return cv2.cvtColor(im, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0


def resize(img, size):
    return cv2.resize(img.astype(np.float32), size, interpolation=cv2.INTER_AREA)


def box(x, r):
    return cv2.boxFilter(x, -1, (2 * r + 1, 2 * r + 1), borderType=cv2.BORDER_REFLECT)


def linear_prophoto(dng, meta, half=False):
    cam = load_camera_rgb(dng, half=half).astype(np.float32)
    m, _, _ = camera_to_xyz_d50(meta)
    M = (XYZ_TO_PROPHOTO @ m).astype(np.float32) * np.float32(2.0 ** (meta["BaselineExposure"] or 0))
    return cam @ M.T


def encode(lin):
    """Log-like encoding of the HDR linear data (keeps highlights > 1)."""
    return np.log2(np.maximum(lin, 0) + 1.0 / 256).astype(np.float32) / 8.0 + 1.0


def crop_box(h, w, zoom, cx=0.5, cy=0.5):
    ch, cw = h / zoom, w / zoom
    y0 = int(round(cy * h - ch / 2))
    x0 = int(round(cx * w - cw / 2))
    return y0, x0, int(round(ch)), int(round(cw))


def estimate_zoom(guide, preview):
    """Find the digital-zoom centre crop of the DNG that matches the preview."""
    def prep(x):
        g = x.mean(-1) if x.ndim == 3 else x
        g = cv2.resize(g.astype(np.float32), (128, int(round(128 * g.shape[0] / g.shape[1]))),
                       interpolation=cv2.INTER_AREA)
        g = cv2.Laplacian(cv2.GaussianBlur(g, (0, 0), 1.0), cv2.CV_32F)
        return (g - g.mean()) / (g.std() + 1e-6)
    p = prep(preview)
    h, w = guide.shape[:2]

    def score(z):
        y0, x0, ch, cw = crop_box(h, w, z)
        c = prep(guide[y0:y0 + ch, x0:x0 + cw])
        c = cv2.resize(c, (p.shape[1], p.shape[0]))
        return float((c * p).mean())
    zs = np.arange(1.0, 3.21, 0.02)
    s = [score(z) for z in zs]
    z0 = zs[int(np.argmax(s))]
    fine = np.arange(max(1.0, z0 - 0.03), z0 + 0.031, 0.002)
    sf = [score(z) for z in fine]
    return float(fine[int(np.argmax(sf))]), float(max(sf))


# ----------------------------------------------- local affine (guided filter)
def fit_local_affine(I, P, r, eps):
    """Colour guided filter (He et al.): P ~ A(x) I + b(x).  I,P: HxWx3."""
    mean_I = box(I, r)
    mean_P = box(P, r)
    h, w = I.shape[:2]
    cov = np.empty((h, w, 3, 3), np.float32)
    for i in range(3):
        for j in range(i, 3):
            c = box(I[..., i] * I[..., j], r) - mean_I[..., i] * mean_I[..., j]
            cov[..., i, j] = cov[..., j, i] = c
    cov += eps * np.eye(3, dtype=np.float32)
    cross = np.empty((h, w, 3, 3), np.float32)  # [..., out, in]
    for k in range(3):
        for i in range(3):
            cross[..., k, i] = box(P[..., k] * I[..., i], r) - mean_P[..., k] * mean_I[..., i]
    A = np.linalg.solve(cov, cross.transpose(0, 1, 3, 2)).transpose(0, 1, 3, 2)  # out x in
    b = mean_P - np.einsum("hwki,hwi->hwk", A, mean_I)
    # average the coefficients over windows (standard guided filter step)
    A = box(A.reshape(h, w, 9), r).reshape(h, w, 3, 3)
    b = box(b, r)
    return A, b


def apply_affine(A, b, I):
    h, w = I.shape[:2]
    A = cv2.resize(A.reshape(A.shape[0], A.shape[1], 9), (w, h), interpolation=cv2.INTER_LINEAR)
    b = cv2.resize(b, (w, h), interpolation=cv2.INTER_LINEAR)
    out = np.empty_like(I)
    for k in range(3):
        out[..., k] = (A[..., 3 * k:3 * k + 3] * I).sum(-1) + b[..., k]
    return out


def _poly_feats(x):
    x = x.reshape(-1, 3).astype(np.float64)
    r, g, b = x[:, 0], x[:, 1], x[:, 2]
    f = [np.ones_like(r), r, g, b, r * r, g * g, b * b, r * g, r * b, g * b,
         r ** 3, g ** 3, b ** 3, r * r * g, r * r * b, g * g * r, g * g * b, b * b * r, b * b * g, r * g * b]
    return np.stack(f, 1)


def fit_global(I, P, lam=1e-5):
    """Global cubic-polynomial colour mapping I -> P."""
    F = _poly_feats(I)
    coef = np.linalg.solve(F.T @ F / len(F) + lam * np.eye(F.shape[1]), F.T @ P.reshape(-1, 3) / len(F))

    def f(x):
        out = np.empty(x.shape, np.float32)
        flat, o = x.reshape(-1, 3), out.reshape(-1, 3)
        for i in range(0, len(flat), 1 << 20):
            o[i:i + (1 << 20)] = _poly_feats(flat[i:i + (1 << 20)]) @ coef
        return out
    return f


# ------------------------------------------------------------------ convert
def convert(dng, meta=None, radius_frac=1 / 160, eps=1e-4, keep_fov=False, zoom=None, half=False):
    """Return an sRGB float image (HxWx3, [0,1]) and info dict."""
    if meta is None:
        meta = read_meta(dng)[dng]
    I = encode(linear_prophoto(dng, meta, half=half))
    prev = read_preview(dng)
    ph, pw = prev.shape[:2]
    H, W = I.shape[:2]
    if zoom is None:
        zoom, score = estimate_zoom(I, prev)
        if zoom < 1.01:
            zoom = 1.0
    else:
        score = None
    y0, x0, ch, cw = crop_box(H, W, zoom)
    Ic = I[y0:y0 + ch, x0:x0 + cw]
    I_low = resize(Ic, (pw, ph))
    r = max(2, int(round(max(pw, ph) * radius_frac)))
    # 1) global colour/tone mapping (per-pixel, keeps fine chroma detail)
    g = fit_global(I_low, prev)
    G_low = g(I_low)
    # 2) local, smooth correction of the residual (local tone mapping, vignetting...)
    A, b = fit_local_affine(G_low, prev - G_low, r, eps)
    info = {"zoom": zoom, "zoom_score": score, "radius": r}
    Gc = g(Ic)
    out = Gc + apply_affine(A, b, Gc)
    if keep_fov and zoom > 1.0:
        full = g(I)  # outside the preview FOV only the global mapping is available
        full[y0:y0 + ch, x0:x0 + cw] = out
        out = full
    elif zoom > 1.0:  # the camera upsamples the crop back to full size
        out = cv2.resize(out, (W, H), interpolation=cv2.INTER_CUBIC)
    return np.clip(out, 0, 1), info


def save_jpg(path, img, src_dng=None, quality=95):
    bgr = (np.clip(img, 0, 1)[..., ::-1] * 255 + 0.5).astype(np.uint8)
    cv2.imwrite(path, bgr, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if src_dng:  # copy EXIF (date, exposure, ...) from the DNG
        subprocess.run(["exiftool", "-q", "-m", "-overwrite_original", "-TagsFromFile", src_dng,
                        "-all:all", "-unsafe", "-ThumbnailImage=", "-PreviewImage=",
                        "-Orientation=1", "-n", path], capture_output=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("inputs", nargs="+", help="DNG files or directories")
    ap.add_argument("-o", "--outdir", required=True, help="output directory for the JPGs")
    ap.add_argument("--keep-fov", action="store_true",
                    help="for digitally zoomed shots keep the full sensor FOV instead of cropping like the camera")
    ap.add_argument("-q", "--quality", type=int, default=95, help="JPEG quality (default: 95)")
    ap.add_argument("--overwrite", action="store_true", help="re-convert files that already exist in OUTDIR")
    args = ap.parse_args()
    files = []
    for p in args.inputs:
        files += sorted(glob.glob(os.path.join(p, "*.DNG"))) if os.path.isdir(p) else [p]
    os.makedirs(args.outdir, exist_ok=True)
    for i in range(0, len(files), 32):
        chunk = files[i:i + 32]
        metas = read_meta(chunk)
        for f in chunk:
            out = os.path.join(args.outdir, os.path.splitext(os.path.basename(f))[0] + ".JPG")
            if os.path.exists(out) and not args.overwrite:
                continue
            img, info = convert(f, metas[f], keep_fov=args.keep_fov)
            save_jpg(out, img, f, args.quality)
            print(f"{os.path.basename(f)} -> {out}  zoom={info['zoom']:.3f}", flush=True)


if __name__ == "__main__":
    main()
