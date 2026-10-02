"""Reference DNG renderer following the Adobe DNG SDK pipeline.

camera RGB (linear) --WB/ColorMatrix--> XYZ(D50) --> linear ProPhoto
  --> ProfileHueSatMap --> exposure (BaselineExposure) --> ProfileToneCurve
  (Adobe hue-preserving RGB tone) --> linear sRGB --> sRGB gamma
"""
import numpy as np
import rawpy

from dngmeta import read_meta

# ---------------------------------------------------------------- colorimetry
D50 = np.array([0.9642, 1.0, 0.8249])
XYZ_TO_PROPHOTO = np.linalg.inv(np.array([
    [0.7976749, 0.1351917, 0.0313534],
    [0.2880402, 0.7118741, 0.0000857],
    [0.0000000, 0.0000000, 0.8252100]]))
PROPHOTO_TO_XYZ = np.linalg.inv(XYZ_TO_PROPHOTO)
XYZD50_TO_SRGB = np.array([  # Bradford adapted D50 -> sRGB(D65)
    [3.1338561, -1.6168667, -0.4906146],
    [-0.9787684, 1.9161415, 0.0334540],
    [0.0719453, -0.2289914, 1.4052427]])
BRADFORD = np.array([[0.8951, 0.2664, -0.1614],
                     [-0.7502, 1.7135, 0.0367],
                     [0.0389, -0.0685, 1.0296]])
ILLUM_TEMP = {17: 2856.0, 21: 6504.0, 23: 5003.0, 20: 5503.0, 22: 7504.0}


def xy_to_xyz(xy):
    x, y = xy
    return np.array([x / y, 1.0, (1 - x - y) / y])


def xyz_to_xy(XYZ):
    s = XYZ.sum()
    return XYZ[:2] / s


def xy_to_temp(xy):
    """McCamy approximation is enough for interpolating the two calibrations."""
    x, y = xy
    n = (x - 0.3320) / (0.1858 - y)
    return 449 * n ** 3 + 3525 * n ** 2 + 6823.3 * n + 5520.33


def bradford(src_xyz, dst_xyz):
    s = BRADFORD @ src_xyz
    d = BRADFORD @ dst_xyz
    return np.linalg.inv(BRADFORD) @ np.diag(d / s) @ BRADFORD


def interp_weight(meta, temp):
    t1 = ILLUM_TEMP.get(meta["CalibrationIlluminant1"], 2856.0)
    t2 = ILLUM_TEMP.get(meta["CalibrationIlluminant2"], 6504.0)
    if temp <= t1:
        return 1.0
    if temp >= t2:
        return 0.0
    return (1 / temp - 1 / t2) / (1 / t1 - 1 / t2)


def camera_to_xyz_d50(meta):
    """Return (3x3 matrix camera->XYZ_D50, weight g of illuminant 1, white xy)."""
    neutral = meta["AsShotNeutral"]
    cm1, cm2 = meta["ColorMatrix1"], meta["ColorMatrix2"]
    xy = np.array([0.3457, 0.3585])
    for _ in range(30):  # iterate white point <-> matrix interpolation
        g = interp_weight(meta, xy_to_temp(xy))
        cm = g * cm1 + (1 - g) * cm2
        new_xy = xyz_to_xy(np.linalg.inv(cm) @ neutral)
        if np.abs(new_xy - xy).max() < 1e-7:
            break
        xy = new_xy
    g = interp_weight(meta, xy_to_temp(xy))
    cm = g * cm1 + (1 - g) * cm2
    white = xy_to_xyz(xy)
    # DNG spec (no ForwardMatrix): normalise so that camera neutral -> white
    cam_to_xyz = np.linalg.inv(cm)
    scale = (cam_to_xyz @ neutral)[1]
    cam_to_xyz = cam_to_xyz / scale
    return bradford(white, D50) @ cam_to_xyz, g, xy


# -------------------------------------------------------------- HSV helpers
def rgb_to_hsv(rgb):
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    mx = rgb.max(-1)
    mn = rgb.min(-1)
    gap = mx - mn
    v = mx
    s = np.where(mx > 0, gap / np.maximum(mx, 1e-12), 0)
    h = np.zeros_like(mx)
    nz = gap > 0
    safe = np.maximum(gap, 1e-12)
    hr = (g - b) / safe
    hg = (b - r) / safe + 2
    hb = (r - g) / safe + 4
    h = np.where(mx == r, hr, np.where(mx == g, hg, hb))
    h = np.where(h < 0, h + 6, h)
    h = np.where(nz, h, 0)
    return h, s, v  # h in [0,6)


def hsv_to_rgb(h, s, v):
    h = np.mod(h, 6.0)
    i = np.floor(h).astype(int)
    f = h - i
    p = v * (1 - s)
    q = v * (1 - s * f)
    t = v * (1 - s * (1 - f))
    out = np.empty(h.shape + (3,), dtype=np.float64)
    choices = [(v, t, p), (q, v, p), (p, v, t), (p, q, v), (t, p, v), (v, p, q)]
    for k, (a, b, c) in enumerate(choices):
        m = i == k
        out[m, 0] = a[m]
        out[m, 1] = b[m]
        out[m, 2] = c[m]
    return out


def apply_hue_sat_map(rgb, table, dims):
    """DNG HueSatMap (2D: hue x sat, val divisions == 1), rgb linear ProPhoto."""
    hd, sd, vd = int(dims[0]), int(dims[1]), int(dims[2])
    assert vd == 1
    t = table.reshape(vd, hd, sd, 3)[0]
    h, s, v = rgb_to_hsv(np.clip(rgb, 0, None))
    hs = h * (hd / 6.0)
    ss = np.clip(s, 0, 1) * (sd - 1)
    h0 = np.floor(hs).astype(int) % hd
    h1 = (h0 + 1) % hd
    fh = hs - np.floor(hs)
    s0 = np.minimum(np.floor(ss).astype(int), sd - 2)
    fs = ss - s0
    s1 = s0 + 1

    def lerp(idx_h, idx_s):
        return t[idx_h, idx_s]
    a = lerp(h0, s0) * (1 - fs)[..., None] + lerp(h0, s1) * fs[..., None]
    b = lerp(h1, s0) * (1 - fs)[..., None] + lerp(h1, s1) * fs[..., None]
    e = a * (1 - fh)[..., None] + b * fh[..., None]
    h = h + e[..., 0] * (6.0 / 360.0)
    s = np.clip(s * e[..., 1], 0, 1)
    v = v * e[..., 2]
    return hsv_to_rgb(h, s, v)


# --------------------------------------------------------------- tone curve
def rgb_tone(rgb, curve_fn):
    """Adobe RefBaselineRGBTone: hue preserving tone curve."""
    out = np.empty_like(rgb)
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    perms = [(0, 1, 2), (0, 2, 1), (1, 0, 2), (1, 2, 0), (2, 0, 1), (2, 1, 0)]
    # for each pixel find the order of channels: large >= mid >= small
    idx = np.argsort(-rgb, axis=-1)
    lg = np.take_along_axis(rgb, idx[..., 0:1], -1)[..., 0]
    md = np.take_along_axis(rgb, idx[..., 1:2], -1)[..., 0]
    sm = np.take_along_axis(rgb, idx[..., 2:3], -1)[..., 0]
    lg2 = curve_fn(lg)
    sm2 = curve_fn(sm)
    md2 = sm2 + (lg2 - sm2) * (md - sm) / np.maximum(lg - sm, 1e-12)
    md2 = np.where(lg > sm, md2, lg2)
    vals = np.stack([lg2, md2, sm2], -1)
    np.put_along_axis(out, idx, vals, -1)
    return out


def make_curve(points):
    x, y = points[:, 0], points[:, 1]
    return lambda v: np.interp(np.clip(v, 0, 1), x, y)


def srgb_encode(x):
    x = np.clip(x, 0, 1)
    return np.where(x <= 0.0031308, 12.92 * x, 1.055 * np.power(x, 1 / 2.4) - 0.055)


def srgb_decode(x):
    x = np.clip(x, 0, 1)
    return np.where(x <= 0.04045, x / 12.92, np.power((x + 0.055) / 1.055, 2.4))


# ------------------------------------------------------------------- render
def load_camera_rgb(path, half=False):
    """Demosaiced, linear, un-white-balanced camera RGB normalised to [0,1]."""
    with rawpy.imread(path) as r:
        rgb = r.postprocess(
            gamma=(1, 1), no_auto_bright=True, output_bps=16, use_camera_wb=False,
            user_wb=[1.0, 1.0, 1.0, 1.0], output_color=rawpy.ColorSpace.raw,
            user_black=0, user_sat=int(r.white_level), half_size=half,
            highlight_mode=rawpy.HighlightMode.Clip,
            demosaic_algorithm=rawpy.DemosaicAlgorithm.AHD)
    return rgb.astype(np.float64) / 65535.0


def render(path, meta=None, cam=None, use_hsm=True, use_tone=True, exposure_extra=0.0,
           half=False, return_stages=False):
    if meta is None:
        meta = read_meta(path)[path]
    if cam is None:
        cam = load_camera_rgb(path, half=half)
    m, g, xy = camera_to_xyz_d50(meta)
    # camera RGB -> XYZ D50 (white balance is implied by the matrix) -> ProPhoto
    M = XYZ_TO_PROPHOTO @ m
    pp = cam @ M.T
    stages = {"prophoto_lin": pp}
    if use_hsm and meta["ProfileHueSatMapData1"] is not None:
        tbl = g * meta["ProfileHueSatMapData1"] + (1 - g) * meta["ProfileHueSatMapData2"]
        pp = apply_hue_sat_map(pp, tbl, meta["ProfileHueSatMapDims"])
    ev = (meta["BaselineExposure"] or 0) + (meta["BaselineExposureOffset"] or 0) + exposure_extra
    pp = pp * (2.0 ** ev)
    pp = np.clip(pp, 0, 1)
    if use_tone and meta["ProfileToneCurve"] is not None:
        pp = rgb_tone(pp, make_curve(meta["ProfileToneCurve"]))
    stages["prophoto_toned"] = pp
    srgb_lin = pp @ (XYZD50_TO_SRGB @ PROPHOTO_TO_XYZ).T
    out = srgb_encode(srgb_lin)
    if return_stages:
        return out, stages
    return out
