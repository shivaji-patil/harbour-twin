"""Sentinel-1 ship detection, cross-checked against AIS (dark-vessel screening).

Ships are bright point targets on dark sea in C-band SAR. We read terrain-
corrected backscatter (gamma0 RTC COGs on Microsoft Planetary Computer, found
through its STAC API) over the AIS area, keep open water only, and run a
two-parameter CFAR detector: a pixel is a target when it stands k standard
deviations above the sea clutter in a ring around it. Detections are matched to
AIS positions interpolated to the overpass time.

A ship SAR sees but AIS does not report is a dark-vessel *candidate* for an
analyst, not a finding: small craft without AIS, receiver gaps and SAR
artefacts (sidelobes, azimuth ambiguities) all produce them too.
"""
from __future__ import annotations

import json
import logging
import urllib.request
from datetime import datetime, timedelta, timezone
from functools import lru_cache

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
from PIL import Image
from pystac_client import Client
from rasterio.features import rasterize, shapes
from rasterio.warp import Resampling, reproject
from shapely.geometry import shape

from . import sources
from .config import AOI
from .sources import Grid

log = logging.getLogger(__name__)

PC_STAC = "https://planetarycomputer.microsoft.com/api/stac/v1"
COLLECTION = "sentinel-1-rtc"
REPEAT_DAYS = 12        # orbit repeat of each Sentinel-1 satellite

RES_M = 10.0            # native IW GRD/RTC pixel
SHORE_M = 40.0          # keep detections this far off the coast and piers (shore clutter)
GUARD_PX = 15           # 310 m guard box: the largest ships stay out of their own clutter estimate
BACKGROUND_PX = 40      # 810 m background box
K_SIGMA = 5.0           # CFAR threshold on cross-pol (VH or HV) backscatter in dB
MIN_PX = 3              # 300 m²: smaller blobs are speckle
MERGE_M = 100.0         # fragments of one ship (sidelobe cross) closer than this are one target

GATE_M = 300.0          # AIS-to-SAR match radius for a stationary ship
R_OVER_V_S = 115.0      # slant range / platform speed: a ship moving at v (m/s) along the line of
                        # sight shows up R/V * v metres along azimuth (the "train off the track" shift)


# --- Scenes -----------------------------------------------------------------------------

def scenes(bbox, start: datetime, end: datetime) -> list:
    """Sentinel-1 RTC items over `bbox`, oldest first."""
    found = Client.open(PC_STAC).search(collections=[COLLECTION], bbox=list(bbox),
                                         datetime=f"{_iso(start)}/{_iso(end)}").items()
    return sorted(found, key=lambda it: it.datetime)


def next_passes(bbox, now: datetime | None = None, n: int = 4) -> list[dict]:
    """Predicted overpasses: each (satellite, relative orbit) repeats every 12 days to the minute."""
    now = now or datetime.now(timezone.utc)
    latest = {}
    for it in scenes(bbox, now - timedelta(days=3 * REPEAT_DAYS), now):
        p = it.properties
        latest[(p.get("platform"), p.get("sat:relative_orbit"))] = it
    out = []
    for (platform, orbit), it in latest.items():
        t = it.datetime
        while t <= now:
            t += timedelta(days=REPEAT_DAYS)
        out.append({"time": _iso(t), "platform": platform, "relative_orbit": orbit,
                    "orbit_state": it.properties.get("sat:orbit_state"), "last_seen": it.id})
    return sorted(out, key=lambda p: p["time"])[:n]


@lru_cache(maxsize=1)
def _token() -> str:
    """Anonymous read token for the Planetary Computer blob container (valid ~1 day)."""
    url = f"https://planetarycomputer.microsoft.com/api/sas/v1/token/{COLLECTION}"
    return json.load(urllib.request.urlopen(url, timeout=60))["token"]


def same_pass(item, items) -> list:
    """`item` plus the adjacent slices of the same overpass (an AOI can straddle two)."""
    p = item.properties
    return [it for it in items if it.properties.get("platform") == p.get("platform")
            and it.properties.get("sat:relative_orbit") == p.get("sat:relative_orbit")
            and abs((it.datetime - item.datetime).total_seconds()) < 120]


def read_scene(items, grid: Grid) -> dict[str, np.ndarray]:
    """Linear gamma0 on `grid`: 'co' (VV or HH) and 'cross' (VH or HV), mosaicked over the slices.

    Sentinel-1 records VV+VH over most seas but HH+HV where ice is monitored
    (Newfoundland, the Arctic); ships are bright in either pair.
    """
    out = {}
    for it in items:
        co, cross = ("vv", "vh") if "vv" in it.assets else ("hh", "hv")
        for key, pol in (("co", co), ("cross", cross)):
            out[key] = sources.read_into(f"{it.assets[pol].href}?{_token()}", grid, Resampling.bilinear,
                                         out=out.get(key))
            out[f"{key}_pol"] = pol.upper()
    return out


def water_mask(aoi: AOI, grid: Grid, shore_m: float = SHORE_M) -> np.ndarray:
    """Open water at least `shore_m` from land, piers, breakwaters and bridges (Overture / OSM)."""
    water = sources.overture_water(aoi.overture_release, aoi.ais_bbox).to_crs(grid.crs)
    piers = sources.overture_marine_structures(aoi.overture_release, aoi.ais_bbox, classes=(
        "pier", "breakwater", "quay", "dock", "bridge")).to_crs(grid.crs)  # bridges are bright SAR targets
    # Bridges image as several parallel lines (direct + multipath returns) offset up to ~100 m from the deck.
    clear = piers.buffer(np.where(piers["class"] == "bridge", 150.0, 10.0))
    geom = water.union_all().difference(clear.union_all()).buffer(-shore_m)
    if geom.is_empty:
        return np.zeros(grid.shape, bool)
    return rasterize([(geom, 1)], out_shape=grid.shape, transform=grid.transform, fill=0, dtype="uint8").astype(bool)


# --- Detection -----------------------------------------------------------------------------

def box_sum(a: np.ndarray, r: int) -> np.ndarray:
    """Sum over the (2r+1)² box centred on each cell, zero outside the array (integral image)."""
    c = np.pad(a.astype("float64"), ((r + 1, r), (r + 1, r))).cumsum(0).cumsum(1)
    return c[2 * r + 1:, 2 * r + 1:] - c[:-2 * r - 1, 2 * r + 1:] - c[2 * r + 1:, :-2 * r - 1] + c[:-2 * r - 1, :-2 * r - 1]


def cfar(x: np.ndarray, valid: np.ndarray, guard: int = GUARD_PX, background: int = BACKGROUND_PX,
         k: float = K_SIGMA, min_fill: float = 0.25) -> np.ndarray:
    """Two-parameter CFAR: x > mean + k * std of the valid cells in the guard-to-background ring.

    Only `valid` (water) cells enter the clutter estimate, so the shore does not
    raise the threshold. Cells whose ring is mostly land are not tested.
    """
    w = valid.astype("float64")
    xv = np.where(valid, x, 0.0)
    ring = lambda a: box_sum(a, background) - box_sum(a, guard)
    n = ring(w)
    mean = ring(xv) / np.maximum(n, 1)
    std = np.sqrt(np.maximum(ring(xv * xv) / np.maximum(n, 1) - mean * mean, 0))
    full = (2 * background + 1) ** 2 - (2 * guard + 1) ** 2
    return valid & (n >= min_fill * full) & (x > mean + k * std)


def _db(a):
    return 10 * np.log10(np.maximum(np.nan_to_num(a, nan=0.0), 1e-6))


def detect(co: np.ndarray, cross: np.ndarray, water: np.ndarray, grid: Grid) -> gpd.GeoDataFrame:
    """Ship targets: CFAR on cross-pol (best ship-to-sea contrast), one point per target at its brightest pixel."""
    cross_db, co_db = _db(cross), _db(co)
    hit = cfar(cross_db, water & np.isfinite(cross))
    cols = ["peak_cross_db", "peak_co_db", "n_px", "geometry"]
    blobs = [shape(s) for s, _ in shapes(hit.astype("uint8"), mask=hit, transform=grid.transform, connectivity=8)]
    if not blobs:
        return gpd.GeoDataFrame(columns=cols, geometry="geometry", crs=grid.crs)
    targets = list(gpd.GeoSeries(blobs).buffer(MERGE_M / 2).union_all().geoms
                   if len(blobs) > 1 else gpd.GeoSeries(blobs).buffer(MERGE_M / 2))
    lab = rasterize(((g, i + 1) for i, g in enumerate(targets)), out_shape=grid.shape,
                    transform=grid.transform, fill=0, dtype="int32")
    rows, cs = np.nonzero(hit & (lab > 0))
    df = pd.DataFrame({"t": lab[rows, cs], "r": rows, "c": cs, "db": cross_db[rows, cs]})
    peak = df.loc[df.groupby("t").db.idxmax()].set_index("t")
    peak["n_px"] = df.groupby("t").size()
    peak = peak[peak.n_px >= MIN_PX]
    x, y = rasterio.transform.xy(grid.transform, peak.r.to_numpy(), peak.c.to_numpy())
    return gpd.GeoDataFrame({"peak_cross_db": peak.db.round(1).to_numpy(),
                             "peak_co_db": co_db[peak.r, peak.c].round(1),
                             "n_px": peak.n_px.to_numpy()},
                            geometry=gpd.points_from_xy(x, y), crs=grid.crs).reset_index(drop=True)


# --- AIS cross-check ----------------------------------------------------------------------

def ais_covers(positions: pd.DataFrame, t: datetime, margin_s: float = 600) -> bool:
    return len(positions) > 0 and (positions.time.min() - timedelta(seconds=margin_s)
                                   <= t <= positions.time.max() + timedelta(seconds=margin_s))


def ais_at(positions: pd.DataFrame, t: datetime, max_gap_s: float = 1800, max_age_s: float = 600,
           hold_s: float = 3 * 3600, still_kn: float = 0.5) -> pd.DataFrame:
    """Each vessel's position at time `t`.

    Interpolated between the fixes either side of `t` when they are at most
    `max_gap_s` apart; otherwise the nearest fix if it is within `max_age_s`,
    or within `hold_s` for a vessel that is not moving (at anchor or moored).
    Vessels with no usable fix are left out: AIS cannot say where they were.
    """
    out = []
    for mmsi, g in positions.sort_values("time").groupby("mmsi"):
        dt = (g.time - t).dt.total_seconds().to_numpy()
        before, after = np.flatnonzero(dt <= 0), np.flatnonzero(dt >= 0)
        r = g.iloc[(before[-1] if len(before) else after[0])]
        sog = float(r.sog) if pd.notna(r.sog) else np.nan
        if len(before) and len(after) and dt[after[0]] - dt[before[-1]] <= max_gap_s:
            a, b = g.iloc[before[-1]], g.iloc[after[0]]
            f = 0.0 if after[0] == before[-1] else -dt[before[-1]] / (dt[after[0]] - dt[before[-1]])
            lon, lat, age = a.lon + f * (b.lon - a.lon), a.lat + f * (b.lat - a.lat), 0.0
        else:
            i = before[-1] if not len(after) or (len(before) and -dt[before[-1]] < dt[after[0]]) else after[0]
            r, age = g.iloc[i], abs(dt[i])
            still = pd.notna(r.sog) and r.sog <= still_kn
            if age > (hold_s if still else max_age_s):
                continue
            lon, lat, sog = r.lon, r.lat, float(r.sog) if pd.notna(r.sog) else np.nan
        out.append({"mmsi": int(mmsi), "name": r.get("name"), "category": r.get("category"),
                    "length_m": r.get("length_m"), "lon": lon, "lat": lat, "sog_kn": sog, "age_s": age})
    return pd.DataFrame(out, columns=["mmsi", "name", "category", "length_m", "lon", "lat", "sog_kn", "age_s"])


def match(dets: gpd.GeoDataFrame, ais: pd.DataFrame, gate_m: float = GATE_M,
          r_over_v_s: float = R_OVER_V_S) -> tuple[gpd.GeoDataFrame, pd.DataFrame]:
    """Greedy nearest-first one-to-one matching of SAR detections to AIS positions.

    The gate widens with speed: a moving ship is displaced along azimuth by up to
    R/V times its speed, and a held fix may have drifted by speed x age.
    Both inputs must be in the same projected CRS (metres); `ais` needs x, y.
    """
    dets = dets.copy()
    ais = ais.copy()
    dets["status"], dets["mmsi"], dets["match_m"] = "dark", pd.NA, np.nan
    ais["seen"] = False
    if len(dets) and len(ais):
        dx = dets.geometry.x.to_numpy()[:, None] - ais.x.to_numpy()[None, :]
        dy = dets.geometry.y.to_numpy()[:, None] - ais.y.to_numpy()[None, :]
        d = np.hypot(dx, dy)
        v = np.nan_to_num(ais.sog_kn.to_numpy(), nan=0.0) * 0.514444
        allowed = gate_m + v * (r_over_v_s + ais.age_s.to_numpy())
        used_d, used_a = set(), set()
        for i, j in zip(*np.unravel_index(np.argsort(d, axis=None), d.shape)):
            if d[i, j] > allowed[j]:
                if d[i, j] > allowed.max():
                    break
                continue
            if i in used_d or j in used_a:
                continue
            used_d.add(i), used_a.add(j)
            dets.loc[dets.index[i], ["status", "mmsi", "match_m"]] = ["ais", int(ais.mmsi.iloc[j]), round(float(d[i, j]))]
            ais.loc[ais.index[j], "seen"] = True
    return dets, ais


# --- Step ----------------------------------------------------------------------------------

def build(aoi: AOI, scene_id: str | None = None) -> dict:
    """Detect ships in the best Sentinel-1 scene and cross-check them against the AIS capture.

    Scene choice: `scene_id` if given; else the latest scene inside the AIS
    capture window; else the latest scene of the past month (detections only).
    """
    from .pipeline import _save_lineage

    pos_path = aoi.data_dir / "ais_positions.parquet"
    pos = pd.read_parquet(pos_path) if pos_path.exists() else pd.DataFrame(columns=["time"])
    now = datetime.now(timezone.utc)
    items = scenes(aoi.ais_bbox, now - timedelta(days=30), now)
    if len(pos):
        seen = {it.id for it in items}
        items += [it for it in scenes(aoi.ais_bbox, pos.time.min().to_pydatetime() - timedelta(hours=1),
                                      pos.time.max().to_pydatetime() + timedelta(hours=1)) if it.id not in seen]
    if scene_id:
        items = [it for it in items if it.id == scene_id] or list(Client.open(PC_STAC).get_collection(
            COLLECTION).get_items(scene_id))
    if not items:
        raise RuntimeError(f"no {COLLECTION} scene found for {aoi.slug}")
    covered = [it for it in items if ais_covers(pos, it.datetime)]
    item = (covered or sorted(items, key=lambda it: it.datetime))[-1]
    t = item.datetime

    grid = Grid.from_bbox(aoi.ais_bbox, aoi.utm, RES_M)
    bands = read_scene(same_pass(item, items), grid)
    water = water_mask(aoi, grid)
    dets = detect(bands["co"], bands["cross"], water, grid)

    have_ais = ais_covers(pos, t)
    unseen = []
    if have_ais:
        ais = ais_at(pos, t)
        xy = gpd.GeoSeries(gpd.points_from_xy(ais.lon, ais.lat), crs="EPSG:4326").to_crs(grid.crs)
        ais["x"], ais["y"] = xy.x.to_numpy(), xy.y.to_numpy()
        r, c = rasterio.transform.rowcol(grid.transform, ais.x.to_numpy(), ais.y.to_numpy())
        r, c = np.asarray(r), np.asarray(c)
        inside = (r >= 0) & (r < grid.height) & (c >= 0) & (c < grid.width)
        ais["in_water"] = False
        ais.loc[inside, "in_water"] = water[r[inside], c[inside]]
        # Only vessels on the analysed open water are expected in the detections;
        # ships alongside a berth sit inside the shore buffer and are not tested.
        dets, tested = match(dets, ais[ais.in_water])
        unseen = tested[~tested.seen].drop(columns=["x", "y", "in_water", "seen"]).to_dict("records")
        n_expected = int(ais.in_water.sum())
    else:
        dets["status"], dets["mmsi"], dets["match_m"] = "no-ais", pd.NA, np.nan
        n_expected = 0

    dets = dets.to_crs("EPSG:4326")
    names = pos.drop_duplicates("mmsi", keep="last").set_index("mmsi")["name"].to_dict() if len(pos) else {}
    dets["name"] = [names.get(m) if pd.notna(m) else None for m in dets.mmsi]
    dets["scene"], dets["time"] = item.id, t
    dets.to_parquet(aoi.data_dir / "sar_detections.parquet")
    _quicklook(bands["co"], grid, aoi.web_dir / "sar.jpg")

    lon0, lat0, lon1, lat1 = grid.bbox_lonlat()
    counts = dets.status.value_counts().to_dict()
    summary = {
        "scene": item.id, "time": _iso(t), "platform": item.properties.get("platform"),
        "orbit_state": item.properties.get("sat:orbit_state"), "polarisations": [bands["co_pol"], bands["cross_pol"]],
        "relative_orbit": item.properties.get("sat:relative_orbit"),
        "source": f"Sentinel-1 RTC (gamma0), {COLLECTION} on Microsoft Planetary Computer",
        "ais_coverage": have_ais, "detections": len(dets), "by_status": counts,
        "ais_on_open_water": n_expected, "ais_not_seen": len(unseen),
        "params": {"k_sigma": K_SIGMA, "guard_m": (2 * GUARD_PX + 1) * RES_M,
                   "background_m": (2 * BACKGROUND_PX + 1) * RES_M, "min_px": MIN_PX,
                   "shore_buffer_m": SHORE_M, "gate_m": GATE_M, "r_over_v_s": R_OVER_V_S},
        "next_passes": next_passes(aoi.ais_bbox, now),
    }
    web = {**summary, "image": {"file": "sar.jpg", "west": lon0, "south": lat0, "east": lon1, "north": lat1},
           "targets": [{"lon": round(p.x, 6), "lat": round(p.y, 6), "status": s,
                        "mmsi": None if pd.isna(m) else int(m), "name": n, "peak_cross_db": float(db), "n_px": int(px),
                        "match_m": None if pd.isna(mm) else float(mm)}
                       for p, s, m, n, db, px, mm in zip(dets.geometry, dets.status, dets.mmsi, dets.name,
                                                          dets.peak_cross_db, dets.n_px, dets.match_m)],
           "ais_unseen": [{k: (None if pd.isna(v) else v) for k, v in u.items()} for u in unseen]}
    (aoi.web_dir / "sar.json").write_text(json.dumps(web, default=str))
    _save_lineage(aoi, sar={k: v for k, v in summary.items() if k != "next_passes"})
    log.info("sar %s: %d detections %s, AIS coverage: %s", item.id, len(dets), counts, have_ais)
    return summary


def _quicklook(co: np.ndarray, grid: Grid, path) -> None:
    """Co-pol backscatter as a greyscale JPEG on a lon/lat grid, for the viewer's imagery layer.

    JPEG, not PNG: speckle makes a lossless image ~6x larger for no visible gain.
    Outside the scene is black, which reads as calm sea.
    """
    w, s, e, n = grid.bbox_lonlat()
    h = grid.height
    width = int(round(h * (e - w) / (n - s) * np.cos(np.radians((s + n) / 2))))
    from rasterio.transform import from_bounds
    dst = np.full((h, width), np.nan, "float32")
    reproject(co.astype("float32"), dst, src_transform=grid.transform, src_crs=grid.crs,
              dst_transform=from_bounds(w, s, e, n, width, h), dst_crs="EPSG:4326",
              resampling=Resampling.bilinear, src_nodata=np.nan, dst_nodata=np.nan)
    grey = np.clip((_db(dst) + 25) / 30, 0, 1) * 255  # -25..+5 dB
    Image.fromarray(grey.astype("uint8"), "L").save(path, quality=85, optimize=True)


def _iso(t: datetime) -> str:
    return t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
