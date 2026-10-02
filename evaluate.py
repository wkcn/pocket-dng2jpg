"""Evaluate convert.py on DNG+JPG pairs shot by the camera (DNG+JPG mode).

For every X.DNG that has a matching X.JPG in the same directory, convert the DNG
and compare it with the in-camera JPG:
  dE   - mean CIE76 colour difference on images downscaled to 384 px wide
  PSNR - full resolution PSNR (dB)
"""
import argparse
import glob
import os

import cv2
import numpy as np

from compare import delta_e, read_rgb, small
from convert import convert, read_preview
from dngmeta import read_meta


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("directory", help="directory containing X.DNG + X.JPG pairs")
    ap.add_argument("--save-dir", help="optionally save the converted images here")
    args = ap.parse_args()

    dngs = [d for d in sorted(glob.glob(os.path.join(args.directory, "*.DNG")))
            if os.path.exists(d[:-3] + "JPG")]
    if not dngs:
        raise SystemExit("no DNG+JPG pairs found")
    if args.save_dir:
        os.makedirs(args.save_dir, exist_ok=True)
    metas = read_meta(dngs)
    des, psnrs, prev_des = [], [], []
    for d in dngs:
        name = os.path.basename(d)[:-4]
        out, info = convert(d, metas[d])
        jpg = read_rgb(d[:-3] + "JPG")
        if out.shape != jpg.shape:
            out = cv2.resize(out, (jpg.shape[1], jpg.shape[0]), interpolation=cv2.INTER_AREA)
        de = delta_e(small(out), small(jpg)).mean()
        psnr = 10 * np.log10(1.0 / ((out - jpg) ** 2).mean())
        prev_de = delta_e(small(read_preview(d)), small(jpg)).mean()
        des.append(de)
        psnrs.append(psnr)
        prev_des.append(prev_de)
        print(f"{name}  zoom={info['zoom']:.3f}  dE={de:.2f}  PSNR={psnr:.2f} dB  "
              f"(embedded preview vs JPG: dE={prev_de:.2f})", flush=True)
        if args.save_dir:
            cv2.imwrite(os.path.join(args.save_dir, name + ".JPG"),
                        (np.clip(out, 0, 1)[..., ::-1] * 255 + 0.5).astype(np.uint8))
    print(f"\n{len(dngs)} pairs: mean dE={np.mean(des):.2f}  mean PSNR={np.mean(psnrs):.2f} dB  "
          f"(embedded preview mean dE={np.mean(prev_des):.2f})")


if __name__ == "__main__":
    main()
