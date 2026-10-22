"""Offline elevation packages for platforms that can't phone home.

DTED Level 2 (MIL-PRF-89020B): 1 x 1 degree cells, 1 arc-second posting
below 50 deg latitude, heights in whole metres above EGM96. Built from
Copernicus GLO-30, whose native grid is also 1 arc-second.

GeoPackage (OGC 12-128r18, as profiled by DGIWG): one file carrying a tiled
float32 elevation coverage (2D Gridded Coverage extension) and the vector
layers an operator needs on the move: buildings with heights and AIS tracks.
"""
from __future__ import annotations

import json
import logging
import math

import geopandas as gpd
import numpy as np
import rasterio
from rasterio.transform import from_origin
from rasterio.warp import Resampling

from . import datum, sources
from .config import AOI
from .sources import Grid

log = logging.getLogger(__name__)

ARCSEC = 1 / 3600


def dted_cells(bbox) -> list[tuple[int, int]]:
    """(lon, lat) south-west corners of the 1-degree cells covering `bbox`."""
    w, s, e, n = bbox
    return [(lo, la) for lo in range(math.floor(w), math.ceil(e)) for la in range(math.floor(s), math.ceil(n))]


def dted_name(lon: int, lat: int) -> str:
    return f"{'e' if lon >= 0 else 'w'}{abs(lon):03d}/{'n' if lat >= 0 else 's'}{abs(lat):02d}.dt2"


def dted_grid(lon: int, lat: int) -> tuple[Grid, int]:
    """Level 2 posting grid for the cell with south-west corner (lon, lat).

    Posting is 1" in latitude; longitude spacing widens poleward (MIL-PRF-89020B
    table). Posts sit on whole-arc-second lines, so the cell edges are pixel centres.
    """
    a = abs(lat if lat >= 0 else lat + 1)  # zone is decided by the cell's equator-side edge
    lon_step = 1 if a < 50 else 2 if a < 70 else 3 if a < 75 else 4 if a < 80 else 6
    nx, ny = 3600 // lon_step + 1, 3601
    t = from_origin(lon - lon_step * ARCSEC / 2, lat + 1 + ARCSEC / 2, lon_step * ARCSEC, ARCSEC)
    return Grid(rasterio.crs.CRS.from_epsg(4326), t, nx, ny), lon_step


def dted_level2(lon: int, lat: int, out_dir, le90_m: float | None = None) -> dict:
    """One DTED Level 2 cell. Posting is 1" in latitude; longitude spacing widens poleward."""
    grid, lon_step = dted_grid(lon, lat)
    nx, ny, t = grid.width, grid.height, grid.transform
    z, ids = sources.copernicus_dem(grid)
    z = z + datum.shift_grid("EGM2008", "EGM96", grid.crs, grid.transform, grid.shape, step=60)
    voids = int(np.isnan(z).sum())
    z = np.where(np.isnan(z), 0, z)  # Copernicus is void-free over land; NaN only beyond coverage (sea)
    path = out_dir / dted_name(lon, lat)
    path.parent.mkdir(parents=True, exist_ok=True)
    from datetime import date
    # Header fields per MIL-PRF-89020B. Absolute vertical accuracy is the LE90 our QC measured
    # against lidar, not a number copied from a brochure.
    acc = f"{int(math.ceil(le90_m)):04d}" if le90_m is not None else "NA"
    with rasterio.open(path, "w", driver="DTED", width=nx, height=ny, count=1, dtype="int16",
                       crs="EPSG:4326", transform=t) as dst:
        dst.write(np.round(z).astype("int16"), 1)
        dst.update_tags(DTED_VerticalAccuracy_UHL=acc, DTED_VerticalAccuracy_ACC=acc,
                        DTED_Producer="HRBRTWIN", DTED_DigitizingSystem="COPDEMGLO30",
                        DTED_CompilationDate=date.today().strftime("%y%m"), DTED_SecurityCode_UHL="U",
                        DTED_SecurityCode_DSI="U")
    with rasterio.open(path) as ds:  # read back: the GDAL driver rewrites the header from the grid
        tags = ds.tags()
    log.info("DTED2 %s: %dx%d, %d voids filled", path.name, nx, ny, voids)
    return {"file": str(path.relative_to(out_dir.parent)), "cell": [lon, lat], "posting_arcsec": [lon_step, 1],
            "shape": [ny, nx], "vertical_datum": "EGM96", "source": ids, "voids_filled": voids,
            "min_m": int(z.min()), "max_m": int(z.max()), "header": {k: v for k, v in tags.items() if k.startswith("DTED_")}}


def geopackage(aoi: AOI, out_path, ais_tracks=None) -> dict:
    """Elevation coverage (lidar DTM, EGM96) + buildings + AIS tracks in one GeoPackage."""
    if out_path.exists():
        out_path.unlink()
    with rasterio.open(aoi.data_dir / "dtm_cgvd2013.tif") as src:
        dtm = src.read(1).astype("float32")
        grid = Grid(src.crs, src.transform, src.width, src.height)
    # 5 m is plenty for navigation and keeps the package small.
    g5 = Grid.from_bbox(aoi.bbox, aoi.utm, 5.0)
    dtm5 = np.full(g5.shape, np.nan, "float32")
    rasterio.warp.reproject(dtm, dtm5, src_transform=grid.transform, src_crs=grid.crs, dst_transform=g5.transform,
                            dst_crs=g5.crs, resampling=Resampling.average, src_nodata=np.nan, dst_nodata=np.nan)
    dtm5 += datum.shift_grid("CGVD2013", "EGM96", g5.crs, g5.transform, g5.shape)
    with rasterio.open(out_path, "w", driver="GPKG", width=g5.width, height=g5.height, count=1,
                       dtype="float32", crs=g5.crs, transform=g5.transform, nodata=-9999,
                       RASTER_TABLE="elevation_egm96", RASTER_IDENTIFIER="Lidar DTM, EGM96 heights",
                       RASTER_DESCRIPTION="NRCan HRDEM lidar DTM resampled to 5 m; heights converted "
                                          "CGVD2013 -> EGM96 to match DTED", TILE_FORMAT="TIFF",
                       TILING_SCHEME="CUSTOM", BLOCKXSIZE=256, BLOCKYSIZE=256) as dst:
        dst.write(np.where(np.isfinite(dtm5), dtm5, -9999), 1)

    b = gpd.read_parquet(aoi.data_dir / "buildings.parquet")
    c = b.geometry.representative_point()  # lon/lat inside each footprint; fine for a smooth geoid
    b = b.assign(ground_egm96=b.ground_cgvd2013 + datum.shift_at("CGVD2013", "EGM96", c.x.to_numpy(), c.y.to_numpy()))
    b.to_crs(aoi.utm).to_file(out_path, layer="buildings", driver="GPKG")
    layers = ["elevation_egm96", "buildings"]
    if ais_tracks is not None and ais_tracks.exists():
        tr = gpd.read_parquet(ais_tracks)
        tr[["start", "end"]] = tr[["start", "end"]].astype(str)
        tr.to_file(out_path, layer="ais_tracks", driver="GPKG")
        layers.append("ais_tracks")
    log.info("geopackage %s: %s", out_path.name, layers)
    return {"file": out_path.name, "layers": layers, "size_mb": round(out_path.stat().st_size / 1e6, 1)}


def features(aoi: AOI) -> dict:
    """All vector products in one WGS 84 GeoPackage, for serving as OGC API Features (deploy/)."""
    path = aoi.data_dir / "features.gpkg"
    if path.exists():
        path.unlink()
    layers = {}
    b = gpd.read_parquet(aoi.data_dir / "buildings.parquet").drop(columns=["px"], errors="ignore")
    layers["buildings"] = b
    for name in ("ais_tracks", "ais_positions", "sar_detections"):
        f = aoi.data_dir / f"{name}.parquet"
        if f.exists():
            layers[name] = gpd.read_parquet(f)
    for name, g in layers.items():
        g = g.to_crs(4326).reset_index(drop=True)
        if name != "buildings":  # OGC API items need a unique id; buildings have Overture's
            g.insert(0, "feature_id", range(1, len(g) + 1))
        for col in g.columns:  # GeoPackage has no timezone-aware or nullable-int types
            if str(g[col].dtype).startswith(("datetime64", "Int")) or g[col].dtype == object and col != "geometry":
                g[col] = g[col].astype(str).replace({"<NA>": None, "None": None, "NaT": None})
        g.to_file(path, layer=name, driver="GPKG")
    log.info("features %s: %s", path.name, {k: len(v) for k, v in layers.items()})
    return {"file": path.name, "layers": {k: len(v) for k, v in layers.items()}}


def run(aoi: AOI) -> dict:
    out = aoi.data_dir / "offline"
    out.mkdir(exist_ok=True)
    qc = json.loads((aoi.data_dir / "qc_report.json").read_text())
    le90 = qc["comparisons"]["Copernicus GLO-30 vs lidar DSM"]["result"]["overall"]["le90_m"]
    cells = [dted_level2(lo, la, out / "dted", le90) for lo, la in dted_cells(aoi.bbox)]
    gpkg = geopackage(aoi, out / f"{aoi.slug}_offline.gpkg", aoi.data_dir / "ais_tracks.parquet")
    manifest = {"dted": cells, "geopackage": gpkg, "features": features(aoi)}
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2))
    return manifest
