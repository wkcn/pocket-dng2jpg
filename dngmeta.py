"""Read the DNG color-related tags via exiftool."""
import base64
import json
import subprocess

import numpy as np

TAGS = [
    "ColorMatrix1", "ColorMatrix2", "ForwardMatrix1", "ForwardMatrix2",
    "CameraCalibration1", "CameraCalibration2", "AnalogBalance",
    "CalibrationIlluminant1", "CalibrationIlluminant2", "AsShotNeutral",
    "BaselineExposure", "BaselineExposureOffset", "ProfileHueSatMapDims",
    "ProfileHueSatMapData1", "ProfileHueSatMapData2", "ProfileLookTableDims",
    "ProfileLookTableData", "ProfileToneCurve", "WhiteLevel", "BlackLevel",
    "DigitalZoomRatio", "ISO", "ExposureTime", "WhiteBalanceCCT",
]


def _arr(v):
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return np.array([float(v)])
    if isinstance(v, str) and v.startswith("base64:"):
        # binary float arrays; the file is little-endian (II)
        return np.frombuffer(base64.b64decode(v[7:]), "<f4").astype(np.float64)
    return np.array([float(x) for x in str(v).split()])


def read_meta(paths):
    """Return {path: dict} for a list of DNG paths."""
    if isinstance(paths, str):
        paths = [paths]
    out = subprocess.run(
        ["exiftool", "-j", "-n", "-m", "-b"] + ["-" + t for t in TAGS] + list(paths),
        capture_output=True, text=True, check=True).stdout
    res = {}
    for d in json.loads(out):
        m = {}
        for k in ("ColorMatrix1", "ColorMatrix2", "ForwardMatrix1", "ForwardMatrix2",
                  "CameraCalibration1", "CameraCalibration2"):
            a = _arr(d.get(k))
            m[k] = None if a is None else a.reshape(3, 3)
        m["AnalogBalance"] = _arr(d.get("AnalogBalance"))
        m["AsShotNeutral"] = _arr(d.get("AsShotNeutral"))
        m["ProfileHueSatMapDims"] = _arr(d.get("ProfileHueSatMapDims"))
        for k in ("ProfileHueSatMapData1", "ProfileHueSatMapData2", "ProfileLookTableData"):
            a = _arr(d.get(k))
            m[k] = a
        m["ProfileLookTableDims"] = _arr(d.get("ProfileLookTableDims"))
        tc = _arr(d.get("ProfileToneCurve"))
        m["ProfileToneCurve"] = None if tc is None else tc.reshape(-1, 2)
        for k in ("CalibrationIlluminant1", "CalibrationIlluminant2", "BaselineExposure",
                  "BaselineExposureOffset", "WhiteLevel", "DigitalZoomRatio", "ISO",
                  "ExposureTime", "WhiteBalanceCCT"):
            m[k] = d.get(k)
        res[d["SourceFile"]] = m
    return res
