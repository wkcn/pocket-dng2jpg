# pocket-dng2jpg

[中文说明](README.zh-CN.md)

Convert **DJI Osmo Pocket 4P** RAW photos (`.DNG`) into JPGs that look like the camera's own JPGs: same colour, saturation, brightness and local tone mapping.

## The problem

If you shoot RAW-only (DNG without JPG) on the Pocket 4P and convert the DNGs later, the results look clearly different from the camera's JPGs. They come out too dark or too bright, colours and saturation are off, and reds turn magenta. Measured against real in-camera JPGs (mean CIE76 ΔE; below ~1 is invisible, above ~5 is obvious):

| Converter | ΔE vs. in-camera JPG |
|---|---|
| `rawpy` / LibRaw defaults | 3.5 – 37 |
| macOS `sips` (Apple RAW engine) | 3.3 – 23 |
| Full DNG-spec pipeline (ColorMatrix, HueSatMap, ProfileToneCurve, BaselineExposure) | 4.7 – 18 |
| **pocket-dng2jpg** | **0.47 – 2.2 (mean 0.82)** |

Why generic converters can't match the camera:

1. **BaselineExposure changes from shot to shot** (0 – 4 EV). Many tools ignore it.
2. **The camera applies local tone mapping**: it lifts shadows and compresses highlights depending on image content. No fixed colour matrix or tone curve can reproduce that, so even a correct implementation of the DNG spec stays far off.
3. **Digital zoom is not recorded in the DNG** (`DigitalZoomRatio` is always 1), but the JPG is cropped. At ≥2× zoom the DNG is already a sensor crop, so a 3× JPG matches a further 1.5× crop of the DNG.

## How it works

The key observation: **every Pocket 4P DNG embeds a 1280×720 `PreviewImage` that is the in-camera JPG rendering, downscaled** (ΔE 0.55 – 1.3 vs. the real JPG). It is low resolution, but it carries exactly the colour and tone we want. The DNG carries the detail. `convert.py` combines the two:

1. **Linear render.** Demosaic the DNG with LibRaw, then apply the DNG `ColorMatrix1/2` (interpolated by white point), the as-shot white balance and `BaselineExposure`. The result is linear ProPhoto RGB, log-encoded so highlights above 1.0 are kept.
2. **Find the digital zoom.** Search for the centred crop of the render whose edges best match the preview (normalised cross-correlation of Laplacians, coarse then fine).
3. **Learn the camera's look at preview resolution:**
   - a **global cubic polynomial** colour mapping (render → preview), which captures the colour science and tone curve;
   - a **local affine correction** of the residual, fitted with a colour guided filter (He et al.), which captures local tone mapping and vignetting.
4. **Apply at full resolution.** Upsample the mapping and apply it to the full-resolution render, so fine detail comes from the RAW. Then upscale zoomed crops back to full size, as the camera does, write the JPG and copy EXIF from the DNG.

### Results

On 30 DNG+JPG pairs (landscape and portrait, 1×–3× zoom, ISO 50–400, BaselineExposure 0–4 EV):

- mean ΔE **0.82** (about as close as the embedded preview itself, 0.6–1.3);
- mean full-resolution PSNR **37.6 dB**;
- the digital zoom estimate matches the JPG's `DigitalZoomRatio` (e.g. 1.242 vs 1.24, and 1.5 for 3× shots because of the sensor crop).

On a real batch of 467 RAW-only photos, the mean ΔE against each photo's embedded preview was 1.38 (95th percentile 2.25).

## Installation

Requires Python 3.9+ and [ExifTool](https://exiftool.org/) on `PATH`.

```bash
brew install exiftool          # macOS; Linux: apt install libimage-exiftool-perl
pip install -r requirements.txt
```

## Usage

Convert a folder (or individual files):

```bash
python3 convert.py /path/to/DCIM/DJI_001 -o /path/to/output
python3 convert.py a.DNG b.DNG -o out/
```

Options:

| Option | Description |
|---|---|
| `-o, --outdir DIR` | Output directory (required). Output files are named `<name>.JPG`. |
| `-q, --quality N` | JPEG quality, default `95`. The camera uses 100; 95 looks the same at about half the file size. |
| `--keep-fov` | For digitally zoomed shots, keep the full sensor field of view instead of cropping like the camera. Outside the crop only the global mapping is used. |
| `--overwrite` | Re-convert files that already exist. By default they are skipped, so an interrupted run can be resumed. |

A conversion takes about 5 s per photo on Apple Silicon. To use all cores, run several processes:

```bash
ls /path/to/DCIM/DJI_001/*.DNG | xargs -n 20 -P 6 python3 convert.py -o /path/to/output
```

### Evaluate on your own camera

Shoot a few photos in **DNG+JPG** mode, put the pairs in one folder and run:

```bash
python3 evaluate.py /path/to/pairs [--save-dir out/]
```

This prints ΔE and PSNR for each pair, plus how close the embedded preview is to the JPG.

## Files

| File | Purpose |
|---|---|
| `convert.py` | The converter (CLI). |
| `evaluate.py` | Compares conversions with in-camera JPGs. |
| `dngmeta.py` | Reads DNG colour tags with ExifTool. |
| `render.py` | Linear camera → XYZ → ProPhoto rendering, plus a reference DNG-spec renderer (HueSatMap, ProfileToneCurve). |
| `compare.py` | Image I/O and ΔE helpers. |

## Limitations

- **Needs the embedded preview.** It is present in every Pocket 4P DNG tested (firmware 10.02.01.08). The camera's `MISC/THM/*/*.SCR` files contain the same image.
- **Sharpening is not reproduced.** Very fine textures such as foliage look slightly softer and a little less saturated than the camera JPG.
- **Tested only on the Osmo Pocket 4P.** Other cameras that embed a full-colour preview in their DNGs may work too, but they are untested.

## Disclaimer

This is an independent project and is not affiliated with or endorsed by DJI.

## License

[MIT](LICENSE)
