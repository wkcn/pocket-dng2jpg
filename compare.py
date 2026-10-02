"""Utilities to compare a rendering with the camera JPG."""
import cv2
import numpy as np


def read_rgb(path):
    im = cv2.imread(path, cv2.IMREAD_COLOR | cv2.IMREAD_IGNORE_ORIENTATION)
    return cv2.cvtColor(im, cv2.COLOR_BGR2RGB).astype(np.float64) / 255.0


def center_crop_zoom(img, zoom):
    if zoom is None or zoom <= 1.001:
        return img
    h, w = img.shape[:2]
    ch, cw = h / zoom, w / zoom
    y0, x0 = (h - ch) / 2, (w - cw) / 2
    return img[int(round(y0)):int(round(y0 + ch)), int(round(x0)):int(round(x0 + cw))]


def small(img, width=384):
    h, w = img.shape[:2]
    return cv2.resize(img.astype(np.float32), (width, int(round(h * width / w))),
                      interpolation=cv2.INTER_AREA).astype(np.float64)


def to_lab(srgb):
    return cv2.cvtColor(np.clip(srgb, 0, 1).astype(np.float32), cv2.COLOR_RGB2Lab).astype(np.float64)


def delta_e(a, b):
    return np.sqrt(((to_lab(a) - to_lab(b)) ** 2).sum(-1))


def report(name, a, b):
    la, lb = to_lab(a), to_lab(b)
    de = np.sqrt(((la - lb) ** 2).sum(-1))
    ca = np.hypot(la[..., 1], la[..., 2]).mean()
    cb = np.hypot(lb[..., 1], lb[..., 2]).mean()
    return (f"{name:12s} dE={de.mean():5.2f} L={la[...,0].mean():5.1f}/{lb[...,0].mean():5.1f} "
            f"C={ca:5.1f}/{cb:5.1f} a={la[...,1].mean():5.1f}/{lb[...,1].mean():5.1f} "
            f"b={la[...,2].mean():5.1f}/{lb[...,2].mean():5.1f}")
