"""AOI configuration: one YAML file describes a city twin end to end."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml
from pyproj import CRS
from pyproj.aoi import AreaOfInterest
from pyproj.database import query_utm_crs_info

ROOT = Path(__file__).resolve().parents[1]


@dataclass
class Spec:
    """Accuracy thresholds a product must meet before it is published."""
    name: str
    le90_m: float          # absolute vertical accuracy, 90% linear error
    max_bias_m: float      # mean error after datum alignment
    max_void_fraction: float


@dataclass
class AOI:
    slug: str
    name: str
    bbox: tuple[float, float, float, float]          # core twin area, lon/lat (W, S, E, N)
    terrain_bbox: tuple[float, float, float, float]  # wider terrain/AIS context
    ais_bbox: tuple[float, float, float, float]
    dem_res_m: float = 2.0         # working resolution for lidar DEM/DSM
    terrain_res_m: float = 10.0    # web terrain grid resolution
    overture_release: str = "2026-10-21.0"
    specs: list[Spec] = field(default_factory=list)
    tide_station: str | None = None  # CHS station code
    sea_points: list[tuple[str, float, float]] = field(default_factory=list)  # (name, lon, lat) open water

    @property
    def utm(self) -> CRS:
        w, s, e, n = self.bbox
        info = query_utm_crs_info(datum_name="NAD83(CSRS)", area_of_interest=AreaOfInterest(w, s, e, n))
        return CRS.from_epsg(info[0].code)

    @property
    def data_dir(self) -> Path:
        return ROOT / "data" / self.slug

    @property
    def cache_dir(self) -> Path:
        return ROOT / "data" / "cache" / self.slug

    @property
    def web_dir(self) -> Path:
        return ROOT / "web" / "data" / self.slug


def load(slug: str) -> AOI:
    raw = yaml.safe_load((ROOT / "config" / f"{slug}.yaml").read_text())
    raw["specs"] = [Spec(**s) for s in raw.get("specs", [])]
    for k in ("bbox", "terrain_bbox", "ais_bbox"):
        raw[k] = tuple(raw[k])
    aoi = AOI(slug=slug, **raw)
    for d in (aoi.data_dir, aoi.cache_dir, aoi.web_dir):
        d.mkdir(parents=True, exist_ok=True)
    return aoi
