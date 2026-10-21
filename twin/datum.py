"""Vertical datum handling.

Every height surface in this project is labelled with its vertical reference,
and conversions go through explicit PROJ pipelines that name the geoid grid.
We deliberately avoid letting PROJ pick a transformation for compound CRSs:
when a grid is missing it silently falls back to a "ballpark" transform that
returns the input height unchanged, which is a 10-40 m error in Canada.

Supported references (all heights in metres):
    EGM2008     orthometric, Copernicus DEM GLO-30           (EPSG:3855)
    EGM96       orthometric, DTED / MIL-PRF-89020B           (EPSG:5773)
    CGVD2013    orthometric, NRCan HRDEM / MRDEM, Canadian survey datum (EPSG:6647)
    WGS84       ellipsoidal, what CesiumJS and 3D Tiles expect (EPSG:4979)
"""
from __future__ import annotations

from functools import lru_cache

import numpy as np
import pyproj
from rasterio.transform import Affine
from rasterio.warp import Resampling, reproject

pyproj.network.set_network_enabled(True)

# Geoid grids from the PROJ CDN (https://cdn.proj.org), fetched on first use.
GEOID_GRIDS = {
    "EGM2008": "us_nga_egm08_25.tif",
    "EGM96": "us_nga_egm96_15.tif",
    "CGVD2013": "ca_nrc_CGG2013an83.tif",  # CGG2013a geoid, relative to NAD83(CSRS) ellipsoid
}
VERTICAL_EPSG = {"EGM2008": 3855, "EGM96": 5773, "CGVD2013": 6647, "WGS84": 4979}

# NAD83(CSRS) -> WGS 84, the EPSG "NAD83(CSRS) to WGS 84 (2)" Helmert (1 m accuracy),
# copied from PROJ's own definition and checked in tests/test_datum.py.
# CGG2013a is defined on the NAD83(CSRS) ellipsoid, so this step matters for
# heights handed to Cesium (WGS 84). It is ~1 m vertically in eastern Canada.
_CSRS_TO_WGS84 = (
    "+step +proj=cart +ellps=GRS80 "
    "+step +proj=helmert +x=-0.991 +y=1.9072 +z=0.5129 "
    "+rx=-0.0257899075194932 +ry=-0.0096500989602704 +rz=-0.0116599432323421 "
    "+s=0 +convention=coordinate_frame "
    "+step +inv +proj=cart +ellps=WGS84 "
)


def _to_ellipsoid_steps(ref: str) -> str:
    """Pipeline steps (radians in/out) taking `ref` orthometric heights to WGS 84 ellipsoidal."""
    if ref == "WGS84":
        return ""
    steps = f"+step +proj=vgridshift +grids={GEOID_GRIDS[ref]} +multiplier=1 "
    if ref == "CGVD2013":
        steps += _CSRS_TO_WGS84
    return steps


@lru_cache(maxsize=None)
def pipeline(src: str, dst: str) -> pyproj.Transformer:
    """Transformer for (lon, lat, h) in degrees from `src` heights to `dst` heights."""
    if src == dst:
        raise ValueError("source and destination datum are the same")
    fwd = _to_ellipsoid_steps(src)
    inv = _to_ellipsoid_steps(dst)
    p = "+proj=pipeline +step +proj=unitconvert +xy_in=deg +xy_out=rad "
    p += fwd
    p += _invert(inv)
    p += "+step +proj=unitconvert +xy_in=rad +xy_out=deg"
    return pyproj.Transformer.from_pipeline(p)


def _invert(steps: str) -> str:
    """Invert a list of '+step ...' fragments (reverse order, toggle +inv)."""
    if not steps:
        return ""
    parts = [s.strip() for s in steps.split("+step") if s.strip()]
    out = []
    for s in reversed(parts):
        out.append(s.replace("+inv ", "", 1) if s.startswith("+inv ") else "+inv " + s)
    return "".join(f"+step {s} " for s in out)


def shift_at(src: str, dst: str, lon, lat) -> np.ndarray:
    """Height difference dst - src at the given points (add it to src heights)."""
    lon = np.asarray(lon, dtype="float64")
    lat = np.asarray(lat, dtype="float64")
    _, _, h = pipeline(src, dst).transform(lon, lat, np.zeros_like(lon), errcheck=True)
    if not np.all(np.isfinite(h)):
        raise RuntimeError(f"{src}->{dst}: geoid grid did not cover every point")
    return h


def shift_grid(src: str, dst: str, crs, transform: Affine, shape: tuple[int, int], step: int = 16) -> np.ndarray:
    """Vertical shift raster on a (possibly projected) grid.

    Geoid undulation is smooth at these scales, so we evaluate it on a grid
    `step` times coarser and bilinearly resample to the target grid.
    """
    h, w = shape
    ch, cw = max(2, h // step + 2), max(2, w // step + 2)
    coarse_t = transform * Affine.scale(w / (cw - 1), h / (ch - 1)) * Affine.translation(-0.5, -0.5)
    cols, rows = np.meshgrid(np.arange(cw) + 0.5, np.arange(ch) + 0.5)
    xs, ys = coarse_t * (cols, rows)
    lon, lat = pyproj.Transformer.from_crs(crs, "EPSG:4326", always_xy=True).transform(xs, ys)
    coarse = shift_at(src, dst, lon, lat).reshape(ch, cw).astype("float32")
    out = np.empty(shape, dtype="float32")
    reproject(coarse, out, src_transform=coarse_t, src_crs=crs, dst_transform=transform,
              dst_crs=crs, resampling=Resampling.bilinear)
    return out
