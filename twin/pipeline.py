"""Pipeline steps. Each step reads its inputs from data/<aoi>/ and is re-runnable."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
from PIL import Image
from rasterio.features import rasterize
from rasterio.warp import Resampling

from . import datum, flood, qc, sources
from .config import AOI
from .sources import Grid

log = logging.getLogger(__name__)


def _write_cog(path, arr, grid: Grid, **tags):
    prof = grid.profile(driver="COG", compress="DEFLATE", predictor=3, BIGTIFF="IF_SAFER")
    with rasterio.open(path, "w", **prof) as dst:
        dst.write(arr.astype("float32"), 1)
        dst.update_tags(**{k: str(v) for k, v in tags.items()})


def _read(path):
    with rasterio.open(path) as ds:
        a = ds.read(1, masked=False).astype("float32")
        g = Grid(ds.crs, ds.transform, ds.width, ds.height)
        return a, g


def _lineage(aoi: AOI) -> dict:
    p = aoi.data_dir / "lineage.json"
    return json.loads(p.read_text()) if p.exists() else {}


def _save_lineage(aoi: AOI, **kw):
    lin = _lineage(aoi)
    lin.update(kw)
    (aoi.data_dir / "lineage.json").write_text(json.dumps(lin, indent=2, default=str))


# --- 1. Lidar surfaces --------------------------------------------------------------

def lidar(aoi: AOI) -> None:
    """HRDEM 1 m lidar DTM + DSM over the core AOI, resampled to dem_res_m in UTM (CGVD2013)."""
    grid = Grid.from_bbox(aoi.bbox, aoi.utm, aoi.dem_res_m)
    dtm, ids = sources.nrcan_surface(grid, "dtm")
    dsm, _ = sources.nrcan_surface(grid, "dsm")
    ndsm = np.clip(dsm - dtm, 0, None)
    tags = dict(VERTICAL_DATUM="CGVD2013 (EPSG:6647)", SOURCE="NRCan HRDEM mosaic 1m via STAC")
    _write_cog(aoi.data_dir / "dtm_cgvd2013.tif", dtm, grid, **tags)
    _write_cog(aoi.data_dir / "dsm_cgvd2013.tif", dsm, grid, **tags)
    _write_cog(aoi.data_dir / "ndsm.tif", ndsm, grid, SOURCE="DSM - DTM")
    _save_lineage(aoi, hrdem_items=ids)


# --- 2. Elevation QC ----------------------------------------------------------------

def elevation_qc(aoi: AOI) -> dict:
    """Score Copernicus GLO-30 and NRCan MRDEM-30 against lidar, after datum alignment."""
    grid = Grid.from_bbox(aoi.terrain_bbox, aoi.utm, 30.0)
    lidar_dsm, _ = sources.nrcan_surface(grid, "dsm")
    lidar_dtm, _ = sources.nrcan_surface(grid, "dtm")
    # The DSM read with max-resampling is a roof envelope; for a 30 m DSM
    # comparison we want the cell mean, which is what radar DEMs approximate.
    lidar_dsm_mean = np.full(grid.shape, np.nan, "float32")
    for it in sources.nrcan_items("hrdem-mosaic-1m", grid.bbox_lonlat()):
        sources.read_into(it["assets"]["dsm"]["href"], grid, Resampling.average, out=lidar_dsm_mean)

    glo, glo_ids = sources.copernicus_dem(grid)                     # EGM2008, a DSM
    mrdem, _ = sources.nrcan_surface(grid, "dtm", "mrdem-30")       # CGVD2013, a DTM
    to_cgvd = datum.shift_grid("EGM2008", "CGVD2013", grid.crs, grid.transform, grid.shape)
    to_ell = datum.shift_grid("EGM2008", "WGS84", grid.crs, grid.transform, grid.shape)
    glo_cgvd = glo + to_cgvd

    # Land only: lidar mosaics flatten water near 0 m, which would flatter the scores.
    land = np.isfinite(lidar_dtm) & (lidar_dtm > 1.0)
    open_ground = land & ((lidar_dsm - lidar_dtm) < 1.0)  # no buildings or canopy in the 30 m cell
    res = 30.0
    comps = {
        "Copernicus GLO-30 vs lidar DSM": dict(
            description="Copernicus DEM is an X-band radar surface model (EGM2008). Compared with the HRDEM "
                        "lidar DSM averaged to 30 m, after converting EGM2008 to CGVD2013. Land cells only.",
            result=qc.compare(glo_cgvd, lidar_dsm_mean, res, land), gated=True),
        "Copernicus GLO-30 vs lidar DTM, open ground": dict(
            description="Cells where lidar sees no buildings or canopy (max DSM-DTM < 1 m), so surface and "
                        "terrain coincide. This isolates sensor error from land-cover effects.",
            result=qc.compare(glo_cgvd, lidar_dtm, res, open_ground), gated=True),
        "NRCan MRDEM-30 DTM vs lidar DTM": dict(
            description="NRCan's 30 m bare-earth model, already in CGVD2013, against the lidar DTM averaged "
                        "to 30 m. Like for like: both are terrain models on the same vertical datum.",
            result=qc.compare(mrdem, lidar_dtm, res, land), gated=True),
        "Datum error demo: GLO-30 heights used as ellipsoidal": dict(
            description="The classic 3D-globe bug: orthometric EGM2008 heights handed to a WGS 84 "
                        "ellipsoid renderer (CesiumJS, 3D Tiles) without a geoid correction. Compared "
                        "with lidar converted to ellipsoidal heights, so the bias is the geoid undulation "
                        "plus GLO-30's own bias. The gates must fail, and they do.",
            result=qc.compare(glo, lidar_dsm_mean + datum.shift_grid(
                "CGVD2013", "WGS84", grid.crs, grid.transform, grid.shape), res, land), gated=True),
    }
    for c in comps.values():
        c["gates"] = [qc.gate(c["result"], s) for s in aoi.specs] if c.pop("gated") else []

    lon0 = (aoi.bbox[0] + aoi.bbox[2]) / 2
    lat0 = (aoi.bbox[1] + aoi.bbox[3]) / 2
    sh = lambda a, b: float(datum.shift_at(a, b, lon0, lat0))
    import pyproj
    ballpark = pyproj.Transformer.from_crs("EPSG:4617+6647", "EPSG:4979", always_xy=True).transform(lon0, lat0, 0)[2]
    audit = {
        "EGM2008 -> WGS 84 ellipsoid (geoid undulation N)": sh("EGM2008", "WGS84"),
        "CGVD2013 -> WGS 84 ellipsoid (CGG2013a + NAD83(CSRS) frame)": sh("CGVD2013", "WGS84"),
        "CGVD2013 -> WGS 84, PROJ default compound pipeline (ballpark frame)": float(ballpark),
        "  error of the PROJ default": float(ballpark) - sh("CGVD2013", "WGS84"),
        "EGM2008 -> CGVD2013": sh("EGM2008", "CGVD2013"),
        "EGM2008 -> EGM96 (DTED)": sh("EGM2008", "EGM96"),
    }
    report = {"aoi": aoi.name, "generated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
              "grid": {"crs": grid.crs.to_string(), "res_m": res, "shape": grid.shape},
              "sources": {"copernicus": glo_ids}, "datum_audit": audit, "comparisons": comps,
              "sanity": _sea_checks(aoi, grid, glo, lidar_dtm)}
    _save_lineage(aoi, copernicus_items=glo_ids)
    _write_cog(aoi.data_dir / "glo30_egm2008_qcgrid.tif", glo, grid, VERTICAL_DATUM="EGM2008")
    qc.write_report(report, aoi.data_dir)
    for f in ("qc_report.json", "QC_REPORT.md"):  # the viewer links to the report it was built with
        (aoi.web_dir / f).write_text((aoi.data_dir / f).read_text())
    return report


def _sea_checks(aoi: AOI, grid: Grid, glo, lidar_dtm) -> list[dict]:
    """Open water should sit at ~0 m on both orthometric datums (mean sea level)."""
    import pyproj
    fwd = pyproj.Transformer.from_crs("EPSG:4326", grid.crs, always_xy=True)
    out = []
    for name, lon, lat in aoi.sea_points:
        r, c = rasterio.transform.rowcol(grid.transform, *fwd.transform(lon, lat))
        for label, arr in (("GLO-30 (EGM2008)", glo), ("lidar DTM (CGVD2013)", lidar_dtm)):
            v = float(arr[r, c])
            if np.isfinite(v):
                out.append({"check": f"{name}: sea surface, {label}", "value": v,
                            "expected": "0 ± 1.5 m", "pass": abs(v) <= 1.5})
    return out


# --- 3. Web terrain (WGS 84 ellipsoidal heights for CesiumJS) -----------------------

def _geo_grid(bbox, res_m):
    lat = (bbox[1] + bbox[3]) / 2
    dlat = res_m / 111_320
    dlon = res_m / (111_320 * np.cos(np.radians(lat)))
    from rasterio.transform import from_origin
    w, s, e, n = bbox
    width, height = int(np.ceil((e - w) / dlon)), int(np.ceil((n - s) / dlat))
    return Grid(rasterio.crs.CRS.from_epsg(4326), from_origin(w, n, dlon, dlat), width, height)


def terrain(aoi: AOI) -> dict:
    """Two heightmap grids (context + core) in WGS 84 ellipsoidal metres, int16 decimetres."""
    meta = {"vertical": "WGS 84 ellipsoidal height (EPSG:4979), int16 decimetres",
            "sources": "NRCan HRDEM lidar DTM (CGVD2013) where available, else Copernicus GLO-30 "
                       "(EGM2008); sea filled with the geoid (mean sea level)", "grids": {}}
    water_gdf = sources.overture_water(aoi.overture_release, aoi.terrain_bbox)
    piers_gdf = sources.overture_marine_structures(aoi.overture_release, aoi.terrain_bbox)
    for name, bbox, res in (("context", aoi.terrain_bbox, 20.0), ("core", aoi.bbox, aoi.terrain_res_m)):
        g = _geo_grid(bbox, res)
        dtm = np.full(g.shape, np.nan, "float32")
        for it in sources.nrcan_items("hrdem-mosaic-1m", bbox):
            sources.read_into(it["assets"]["dtm"]["href"], g, Resampling.average, out=dtm)
        # Lidar mosaics flatten sea to ~0 m; treat that as water so the surge plane reads correctly.
        lidar_ell = dtm + datum.shift_grid("CGVD2013", "WGS84", g.crs, g.transform, g.shape)
        glo, _ = sources.copernicus_dem(g)
        glo_ell = glo + datum.shift_grid("EGM2008", "WGS84", g.crs, g.transform, g.shape)
        sea_ell = datum.shift_grid("EGM2008", "WGS84", g.crs, g.transform, g.shape)
        # Water mask from OSM-derived Overture polygons. Lidar DTMs interpolate across water
        # (TIN fans a metre or more above sea level), so inside water we keep lidar only on
        # mapped piers and breakwaters.
        burn = lambda gdf: rasterize(((geom, 1) for geom in gdf.geometry), out_shape=g.shape,
                                     transform=g.transform, fill=0, dtype="uint8").astype(bool)
        water = burn(water_gdf) & ~burn(piers_gdf)
        h = np.where(np.isfinite(lidar_ell), lidar_ell, glo_ell)
        h = np.where(np.isfinite(h) & ~water, h, sea_ell)
        dm = np.round(h * 10).astype("<i2")
        (aoi.web_dir / f"terrain_{name}.bin").write_bytes(dm.tobytes())
        w, s, e, n = g.bounds
        meta["grids"][name] = {"file": f"terrain_{name}.bin", "west": w, "south": s, "east": e,
                               "north": n, "width": g.width, "height": g.height, "scale": 0.1}
        # Orthometric (CGVD2013) copy of the core for the planner: flood levels are quoted vs CGVD2013.
        if name == "core":
            cg = np.where(np.isfinite(dtm), dtm, glo + datum.shift_grid("EGM2008", "CGVD2013", g.crs, g.transform, g.shape))
            cg = np.where(np.isfinite(cg) & ~water, cg, -1.0)  # water below the land threshold
            meta["water_fraction_core"] = float(water.mean())
            (aoi.web_dir / "ground_cgvd2013_core.bin").write_bytes(np.round(cg * 10).astype("<i2").tobytes())
            lvl = flood.fill_level(cg, flood.seeds(cg))
            (aoi.web_dir / "flood_level_core.bin").write_bytes(
                np.round(np.clip(lvl, -50, 3000) * 10).astype("<i2").tobytes())
            np.save(aoi.cache_dir / "flood_level_core.npy", lvl)
        log.info("terrain %s: %dx%d, %.0f..%.0f m ellipsoidal", name, g.width, g.height, np.nanmin(h), np.nanmax(h))
    c = meta["grids"]["core"]
    lon0, lat0 = (c["west"] + c["east"]) / 2, (c["south"] + c["north"]) / 2
    meta["geoid_cgvd2013_to_wgs84"] = float(datum.shift_at("CGVD2013", "WGS84", lon0, lat0))
    meta["geoid_egm2008_to_wgs84"] = float(datum.shift_at("EGM2008", "WGS84", lon0, lat0))
    (aoi.web_dir / "terrain.json").write_text(json.dumps(meta, indent=1))
    return meta


# --- 4. Imagery ------------------------------------------------------------------------

def imagery(aoi: AOI) -> dict:
    g = _geo_grid(aoi.terrain_bbox, 10.0)
    rgb, meta = sources.sentinel2_visual(g)
    # Gentle stretch: L2A 'visual' is scaled conservatively and reads dark on screen.
    lo, hi = np.percentile(rgb[:, (rgb > 0).any(axis=0)], [1, 99.5])
    rgb = (np.clip((rgb.astype("float32") - lo) / (hi - lo), 0, 1) ** 0.8 * 255).astype("uint8")
    Image.fromarray(np.moveaxis(rgb, 0, -1)).save(aoi.web_dir / "sentinel2.jpg", quality=88, optimize=True)
    w, s, e, n = g.bounds
    meta.update(west=w, south=s, east=e, north=n)
    (aoi.web_dir / "sentinel2.json").write_text(json.dumps(meta))
    _save_lineage(aoi, sentinel2=meta)
    return meta


# --- 5. Buildings ------------------------------------------------------------------------

def buildings(aoi: AOI) -> dict:
    """Overture footprints + heights measured from lidar (DSM - DTM)."""
    from . import tiles3d

    b = sources.overture_buildings(aoi.overture_release, aoi.bbox)
    b = b[b.geometry.geom_type.isin(["Polygon", "MultiPolygon"])].to_crs(aoi.utm).reset_index(drop=True)
    dtm, grid = _read(aoi.data_dir / "dtm_cgvd2013.tif")
    dsm, _ = _read(aoi.data_dir / "dsm_cgvd2013.tif")
    labels = rasterize(((geom, i + 1) for i, geom in enumerate(b.geometry)), out_shape=grid.shape,
                       transform=grid.transform, fill=0, dtype="int32", all_touched=False)
    m = (labels > 0) & np.isfinite(dtm) & np.isfinite(dsm)
    df = pd.DataFrame({"i": labels[m] - 1, "dtm": dtm[m], "dsm": dsm[m]})
    g = df.groupby("i")
    stats = pd.DataFrame({"px": g.size(), "roof": g.dsm.quantile(0.9), "ground_in": g.dtm.quantile(0.05)})

    # Ground: lowest terrain in a 4 m ring around the footprint (the DTM under a roof is interpolated).
    ring = b.geometry.buffer(4).difference(b.geometry.buffer(0.5))
    rlab = rasterize(((geom, i + 1) for i, geom in enumerate(ring) if not geom.is_empty),
                     out_shape=grid.shape, transform=grid.transform, fill=0, dtype="int32")
    rm = (rlab > 0) & np.isfinite(dtm)
    ground_ring = pd.Series(dtm[rm]).groupby(rlab[rm] - 1).quantile(0.1)

    b = b.join(stats).rename(columns={"height": "overture_height"})
    b["ground_cgvd2013"] = ground_ring.reindex(b.index).fillna(b.ground_in)
    b["lidar_height"] = b.roof - b.ground_cgvd2013
    px_min = 6 / aoi.dem_res_m ** 2  # at least ~6 m² of returns
    lidar_ok = (b.px >= px_min) & b.lidar_height.between(2.0, 400)
    has_ov, has_fl = b.overture_height.notna(), b.num_floors.notna()
    b["height_source"] = np.select([lidar_ok, has_ov, has_fl], ["lidar", "overture", "floors"], "default")
    b["height"] = np.select([lidar_ok, has_ov, has_fl],
                            [b.lidar_height, b.overture_height, b.num_floors * 3.2], 6.0).astype("float32")
    b = b[b.ground_cgvd2013.notna()].copy()  # footprints outside the lidar grid
    b["roof_cgvd2013"] = b.ground_cgvd2013 + b.height

    # Water level at which the sea first reaches the building (min fill level on its 4 m ring).
    meta = json.loads((aoi.web_dir / "terrain.json").read_text())["grids"]["core"]
    lvl = np.load(aoi.cache_dir / "flood_level_core.npy")
    from rasterio.transform import from_bounds
    cg = Grid(rasterio.crs.CRS.from_epsg(4326), from_bounds(meta["west"], meta["south"], meta["east"], meta["north"],
                                                            meta["width"], meta["height"]), meta["width"], meta["height"])
    ring_ll = gpd.GeoSeries(ring, crs=aoi.utm).to_crs("EPSG:4326")
    glab = rasterize(((geom, i + 1) for i, geom in enumerate(ring_ll) if not geom.is_empty),
                     out_shape=cg.shape, transform=cg.transform, fill=0, dtype="int32", all_touched=True)
    gm = glab > 0
    b["flood_level_cgvd2013"] = pd.Series(lvl[gm]).groupby(glab[gm] - 1).min().reindex(b.index)

    # Flag, don't silently resolve, large disagreements between independent height sources.
    # Typical causes: OSM placeholder heights on towers, or a small footprint picking up the
    # roof of an adjacent tower in the DSM. Floors give a third opinion (~3.2 m/floor).
    floors_h = b.num_floors * 3.2
    ref = b.overture_height.fillna(floors_h)
    diff = (b.height - ref).abs()
    b["height_flag"] = (b.height_source == "lidar") & ref.notna() & (diff > np.maximum(10, 0.5 * ref))
    b["height_flag"] = b.height_flag.fillna(False).astype(bool)

    # Height QC: lidar-measured vs Overture-published height, where both exist.
    both = b[lidar_ok.reindex(b.index, fill_value=False) & b.overture_height.notna()]
    hq = qc.metrics((both.overture_height - both.lidar_height).to_numpy("float32"))

    keep = ["id", "name", "class", "subtype", "source", "num_floors", "overture_height", "height",
            "height_source", "height_flag", "flood_level_cgvd2013", "ground_cgvd2013", "roof_cgvd2013", "lidar_height", "px", "geometry"]
    out = b[keep]
    out.to_crs("EPSG:4326").to_parquet(aoi.data_dir / "buildings.parquet")

    geoid = lambda lon, lat: datum.shift_at("CGVD2013", "WGS84", lon, lat)
    tinfo = tiles3d.write_tileset(out, aoi.utm, geoid, aoi.web_dir / "buildings")
    summary = {"buildings": len(out), "by_height_source": out.height_source.value_counts().to_dict(),
               "height_flags": int(out.height_flag.sum()),
               "overture_minus_lidar_height": vars(hq) if hq else None,
               "overture_minus_lidar_by_source": both.assign(d=both.overture_height - both.lidar_height)
                   .groupby("source").d.median().round(2).to_dict(), **tinfo}
    _save_lineage(aoi, buildings=summary)
    fl = out.flood_level_cgvd2013.dropna().round(2).sort_values().tolist()
    (aoi.web_dir / "buildings_summary.json").write_text(json.dumps(
        {**summary, "flood_levels_sorted": fl}, default=float))
    log.info("buildings: %s", summary)
    return summary
