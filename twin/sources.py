"""Upstream data providers, discovered through STAC and read as cloud-native formats.

Nothing here downloads whole files: COGs are read by window over HTTP range
requests, and Overture GeoParquet is filtered by bbox inside DuckDB, so only
the bytes for the AOI cross the network.
"""
from __future__ import annotations

import concurrent.futures as cf
import json
import logging
import urllib.request
from dataclasses import dataclass

import duckdb
import geopandas as gpd
import numpy as np
import rasterio
from pyproj import CRS
from pystac_client import Client
from rasterio.transform import Affine, from_origin
from rasterio.vrt import WarpedVRT
from rasterio.warp import Resampling, transform_bounds
from shapely import wkb

log = logging.getLogger(__name__)

EARTH_SEARCH = "https://earth-search.aws.element84.com/v1"
NRCAN_STAC = "https://datacube.services.geo.ca/stac/api"
OVERTURE_STAC = "https://stac.overturemaps.org"

GDAL_ENV = dict(
    GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR",
    GDAL_HTTP_MULTIRANGE="YES",
    GDAL_HTTP_MERGE_CONSECUTIVE_RANGES="YES",
    GDAL_CACHEMAX=512,
    AWS_NO_SIGN_REQUEST="YES",
)


@dataclass(frozen=True)
class Grid:
    """A raster grid every source is resampled onto."""
    crs: CRS
    transform: Affine
    width: int
    height: int

    @classmethod
    def from_bbox(cls, bbox_lonlat, crs, res: float) -> "Grid":
        crs = CRS.from_user_input(crs)
        w, s, e, n = transform_bounds("EPSG:4326", crs, *bbox_lonlat, densify_pts=21)
        if crs.is_geographic:
            w, s, e, n = bbox_lonlat
        x0, y1 = np.floor(w / res) * res, np.ceil(n / res) * res
        width = int(np.ceil((e - x0) / res))
        height = int(np.ceil((y1 - s) / res))
        return cls(crs, from_origin(x0, y1, res, res), width, height)

    @property
    def shape(self):
        return (self.height, self.width)

    @property
    def bounds(self):
        x0, y1 = self.transform.c, self.transform.f
        return (x0, y1 + self.transform.e * self.height, x0 + self.transform.a * self.width, y1)

    def bbox_lonlat(self):
        return transform_bounds(self.crs, "EPSG:4326", *self.bounds, densify_pts=21)

    def profile(self, **kw):
        p = dict(driver="GTiff", crs=self.crs, transform=self.transform, width=self.width,
                 height=self.height, count=1, dtype="float32", nodata=np.nan)
        p.update(kw)
        return p


def read_into(href: str, grid: Grid, resampling=Resampling.bilinear, band: int = 1, out=None) -> np.ndarray:
    """Read one COG/VRT into `grid`, filling only cells that are still NaN in `out`."""
    if out is None:
        out = np.full(grid.shape, np.nan, dtype="float32")
    with rasterio.Env(**GDAL_ENV):
        level = _overview_level(href, grid)
        src = rasterio.open(href, overview_level=level) if level is not None else rasterio.open(href)
    with rasterio.Env(**GDAL_ENV), src:
        with WarpedVRT(src, crs=grid.crs, transform=grid.transform, width=grid.width,
                       height=grid.height, resampling=resampling, src_nodata=src.nodata,
                       nodata=np.nan, dtype="float32") as vrt:
            a = vrt.read(band)
    fill = np.isnan(out) & np.isfinite(a)
    out[fill] = a[fill]
    return out


def _overview_level(href: str, grid: Grid) -> int | None:
    """Coarsest COG overview that is still at least as fine as `grid`.

    WarpedVRT always reads full resolution; for a 30 m grid over 1 m lidar
    that is ~900x more bytes than needed, so we pick the overview ourselves.
    """
    with rasterio.open(href) as src:
        factors = src.overviews(1)
        if not factors:
            return None
        w, s, e, n = transform_bounds(grid.crs, src.crs, *grid.bounds)
        target = min((e - w) / grid.width, (n - s) / grid.height)
        level = None
        for i, f in enumerate(factors):
            if src.res[0] * f <= target:
                level = i
        return level


def _intersects(b1, b2) -> bool:
    return b1[0] < b2[2] and b1[2] > b2[0] and b1[1] < b2[3] and b1[3] > b2[1]


# --- NRCan HRDEM / MRDEM (CGVD2013) ------------------------------------------------

def nrcan_items(collection: str, bbox) -> list[dict]:
    """Items of an NRCan STAC collection whose footprint covers `bbox`.

    The NRCan API accepts but does not apply `bbox` on /search (checked
    2026-10), so we page the collection and filter client side.
    """
    url = f"{NRCAN_STAC}/collections/{collection}/items?limit=100"
    found = []
    while url:
        page = json.load(urllib.request.urlopen(url, timeout=60))
        found += [f for f in page["features"] if _intersects(f["bbox"], bbox)]
        url = next((l["href"] for l in page.get("links", []) if l["rel"] == "next"), None)
    return found


def nrcan_surface(grid: Grid, kind: str = "dtm", collection: str = "hrdem-mosaic-1m") -> tuple[np.ndarray, list[str]]:
    """Lidar DTM or DSM (CGVD2013 heights) resampled onto `grid`."""
    bbox = grid.bbox_lonlat()
    out = np.full(grid.shape, np.nan, dtype="float32")
    used = []
    for it in nrcan_items(collection, bbox):
        href = it["assets"][kind]["href"]
        # Max keeps roofs in a DSM when downsampling; average for bare earth.
        rs = Resampling.max if kind == "dsm" else Resampling.average
        read_into(href, grid, rs, out=out)
        used.append(it["id"])
        if not np.isnan(out).any():
            break
    log.info("%s %s: %s, %.1f%% valid", collection, kind, used, 100 * np.isfinite(out).mean())
    return out, used


# --- Copernicus DEM GLO-30 (EGM2008) ----------------------------------------------

def copernicus_dem(grid: Grid, collection: str = "cop-dem-glo-30") -> tuple[np.ndarray, list[str]]:
    items = list(Client.open(EARTH_SEARCH).search(collections=[collection], bbox=grid.bbox_lonlat()).items())
    out = np.full(grid.shape, np.nan, dtype="float32")
    for it in items:
        read_into(it.assets["data"].href, grid, Resampling.bilinear, out=out)
    log.info("%s: %d tiles, %.1f%% valid", collection, len(items), 100 * np.isfinite(out).mean())
    return out, [it.id for it in items]


# --- Sentinel-2 L2A true colour ---------------------------------------------------

def sentinel2_visual(grid: Grid, months=("06", "07", "08", "09"), max_cloud=5, max_scenes=6) -> tuple[np.ndarray, dict]:
    """Cloud-free Sentinel-2 L2A true colour ('visual' COG) mosaicked onto `grid`.

    Starts from the clearest recent summer scene and fills swath-edge gaps from
    other scenes, preferring the same overpass date so there are no seams.
    """
    client = Client.open(EARTH_SEARCH)
    bbox = grid.bbox_lonlat()
    items = []
    for year in ("2026", "2025"):
        items += list(client.search(collections=["sentinel-2-c1-l2a"], bbox=bbox,
                                    datetime=f"{year}-{months[0]}-01/{year}-{months[-1]}-30",
                                    query={"eo:cloud_cover": {"lt": max_cloud}}, max_items=100).items())
    # Rank by AOI coverage first (swath edges cut scenes diagonally), then by cloud.
    # Coverage comes from a ~250 m read of the COG overviews, so this is cheap.
    probe = Grid.from_bbox(bbox, grid.crs, abs(grid.transform.a) * 25) if not grid.crs.is_geographic else \
        Grid(grid.crs, grid.transform * Affine.scale(25), max(1, grid.width // 25), max(1, grid.height // 25))
    items = sorted(items, key=lambda it: it.properties["eo:cloud_cover"])[:20]

    def coverage(it):
        a = read_into(it.assets["visual"].href, probe, Resampling.nearest)
        return float((np.nan_to_num(a) > 0).mean())

    with cf.ThreadPoolExecutor(8) as ex:
        cov = dict(zip([it.id for it in items], ex.map(coverage, items)))
    items.sort(key=lambda it: (-round(cov[it.id], 2), it.properties["eo:cloud_cover"]))
    rgb = np.zeros((3, *grid.shape), dtype="uint8")
    used = []
    for it in items[:max_scenes]:
        empty = (rgb == 0).all(axis=0)
        if not empty.any():
            break
        with rasterio.Env(**GDAL_ENV), rasterio.open(it.assets["visual"].href) as src:
            with WarpedVRT(src, crs=grid.crs, transform=grid.transform, width=grid.width,
                           height=grid.height, resampling=Resampling.bilinear) as vrt:
                a = vrt.read([1, 2, 3])
        fill = empty & (a > 0).any(axis=0)
        if fill.any():
            rgb[:, fill] = a[:, fill]
            used.append({"id": it.id, "datetime": it.properties["datetime"],
                         "cloud_cover": it.properties["eo:cloud_cover"], "aoi_coverage": round(cov[it.id], 3),
                         "filled_pct": round(100 * float(fill.mean()), 1)})
    meta = {"scenes": used, "empty_pct": round(100 * float((rgb == 0).all(axis=0).mean()), 2)}
    log.info("sentinel-2 mosaic: %s", meta)
    return rgb, meta


def _contains(outer, inner) -> bool:
    return outer[0] <= inner[0] and outer[1] <= inner[1] and outer[2] >= inner[2] and outer[3] >= inner[3]


# --- Overture Maps GeoParquet -------------------------------------------------------

def _get_json(url):
    return json.load(urllib.request.urlopen(url, timeout=60))


def overture_files(release: str, theme: str, type_: str, bbox) -> list[str]:
    """S3 paths of the Overture GeoParquet partitions whose STAC item bbox meets `bbox`."""
    col = _get_json(f"{OVERTURE_STAC}/{release}/{theme}/{type_}/collection.json")
    hrefs = [l["href"] for l in col["links"] if l["rel"] == "item"]
    with cf.ThreadPoolExecutor(32) as ex:
        items = list(ex.map(_get_json, hrefs))
    return [it["assets"]["aws"]["alternate"]["s3"]["href"] for it in items if _intersects(it["bbox"], bbox)]


def overture_buildings(release: str, bbox) -> gpd.GeoDataFrame:
    w, s, e, n = bbox
    files = overture_files(release, "buildings", "building", bbox)
    con = duckdb.connect()
    con.execute("INSTALL httpfs; LOAD httpfs; SET s3_region='us-west-2';")
    df = con.execute(f"""
        SELECT id, height, num_floors, class, subtype, names.primary AS name,
               sources[1].dataset AS source, geometry AS geom  -- WKB, GeoParquet 1.1
        FROM read_parquet({files})
        WHERE bbox.xmin < {e} AND bbox.xmax > {w} AND bbox.ymin < {n} AND bbox.ymax > {s}
    """).df()
    geom = [wkb.loads(bytes(g)) for g in df.pop("geom")]
    gdf = gpd.GeoDataFrame(df, geometry=geom, crs="EPSG:4326")
    log.info("overture buildings: %d from %d partition(s)", len(gdf), len(files))
    return gdf


def overture_water(release: str, bbox) -> gpd.GeoDataFrame:
    """Open-water polygons (OSM coastline-derived ocean, lakes, rivers, ponds) from Overture base/water."""
    w, s, e, n = bbox
    files = overture_files(release, "base", "water", bbox)
    con = duckdb.connect()
    con.execute("INSTALL httpfs; LOAD httpfs; SET s3_region='us-west-2';")
    df = con.execute(f"""
        SELECT subtype, class, names.primary AS name, geometry AS geom
        FROM read_parquet({files})
        WHERE bbox.xmin < {e} AND bbox.xmax > {w} AND bbox.ymin < {n} AND bbox.ymax > {s}
          AND (subtype IN ('ocean', 'lake', 'river', 'pond', 'reservoir', 'canal')
               OR (subtype = 'water' AND class = 'water')
               -- OSM often maps harbours as natural=bay/strait areas rather than coastline ocean
               OR (subtype = 'physical' AND class IN ('bay', 'strait', 'sound', 'fjord')))
    """).df()
    gdf = gpd.GeoDataFrame(df.drop(columns="geom"), geometry=[wkb.loads(bytes(g)) for g in df.geom], crs="EPSG:4326")
    gdf = gdf[gdf.geometry.geom_type.isin(["Polygon", "MultiPolygon"])]
    log.info("overture water: %d polygons (%s)", len(gdf), gdf.subtype.value_counts().to_dict())
    return gdf


def overture_marine_structures(release: str, bbox, line_buffer_m: float = 3.0) -> gpd.GeoDataFrame:
    """Piers, breakwaters and bridges (Overture base/infrastructure): solid ground inside water polygons."""
    w, s, e, n = bbox
    files = overture_files(release, "base", "infrastructure", bbox)
    con = duckdb.connect()
    con.execute("INSTALL httpfs; LOAD httpfs; SET s3_region='us-west-2';")
    df = con.execute(f"""
        SELECT subtype, class, geometry AS geom FROM read_parquet({files})
        WHERE bbox.xmin < {e} AND bbox.xmax > {w} AND bbox.ymin < {n} AND bbox.ymax > {s}
          AND class IN ('pier', 'breakwater', 'quay', 'dock')
    """).df()
    gdf = gpd.GeoDataFrame(df.drop(columns="geom"), geometry=[wkb.loads(bytes(g)) for g in df.geom], crs="EPSG:4326")
    # Pier centre-lines become thin polygons; buffer in metres, in a local UTM zone.
    utm = gdf.estimate_utm_crs()
    g = gdf.to_crs(utm)
    lines = g.geometry.geom_type.isin(["LineString", "MultiLineString"])
    g.loc[lines, "geometry"] = g.loc[lines].buffer(line_buffer_m)
    g = g[g.geometry.geom_type.isin(["Polygon", "MultiPolygon"])].to_crs("EPSG:4326")
    log.info("overture marine structures: %d (%s)", len(g), g["class"].value_counts().to_dict())
    return g
