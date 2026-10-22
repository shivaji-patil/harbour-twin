"""Acceptance checks on the products the pipeline actually published.

These run against data/<aoi>/ and web/data/<aoi>/ and are skipped if the
pipeline has not been run. They are the gate between "the code ran" and
"the products are fit to ship".
"""
import json
import os
from pathlib import Path

import numpy as np
import pystac
import pytest
import rasterio

ROOT = Path(__file__).resolve().parents[1]
AOI = os.environ.get("TWIN_AOI", "vancouver")  # make test AOI=<aoi>
DATA, WEB = ROOT / "data" / AOI, ROOT / "web" / "data" / AOI
pytestmark = pytest.mark.skipif(not (DATA / "qc_report.json").exists(), reason="pipeline not run")

# Rasters, DTED and GeoPackage are git-ignored (rebuilt by `make data`, or copied in from products/).
LARGE = "large products not present: run `make data` or copy products/data into data/"


def _need(*paths):
    if not all(Path(x).exists() for x in paths):
        pytest.skip(LARGE)


@pytest.fixture(scope="module")
def qc():
    return json.loads((DATA / "qc_report.json").read_text())


def test_datum_demo_is_caught_by_gates(qc):
    """The deliberately wrong datum handling must fail every spec; otherwise the gates are blind."""
    demo = next(v for k, v in qc["comparisons"].items() if k.startswith("Datum error demo"))
    assert demo["gates"] and not any(g["pass"] for g in demo["gates"])


def test_aligned_dems_have_small_bias_on_flat_open_ground(qc):
    flat = qc["comparisons"]["NRCan MRDEM-30 DTM vs lidar DTM"]["result"]["by_slope"]["0-5 deg"]
    assert abs(flat["bias_m"]) < 0.5
    og = qc["comparisons"]["Copernicus GLO-30 vs lidar DTM, open ground"]["result"]["overall"]
    assert abs(og["bias_m"]) < 1.0


def test_sea_surface_sanity(qc):
    assert qc["sanity"] and all(s["pass"] for s in qc["sanity"])


def test_web_terrain_puts_the_sea_on_the_geoid():
    meta = json.loads((WEB / "terrain.json").read_text())
    g = meta["grids"]["context"]
    h = np.fromfile(WEB / g["file"], "<i2").reshape(g["height"], g["width"]) * g["scale"]
    # Sea cells are the most common height on the grid; they must sit at N, not at 0.
    vals, counts = np.unique(np.round(h), return_counts=True)
    assert vals[counts.argmax()] == pytest.approx(meta["geoid_egm2008_to_wgs84"], abs=1.0)


def test_tileset_and_tiles():
    ts = json.loads((WEB / "buildings" / "tileset.json").read_text())
    assert ts["asset"]["version"] == "1.1"
    uris = [c["content"]["uri"] for c in ts["root"]["children"]]
    assert uris and all((WEB / "buildings" / u).exists() for u in uris)


def test_building_heights_mostly_from_lidar():
    s = json.loads((WEB / "buildings_summary.json").read_text())
    assert s["by_height_source"]["lidar"] / s["buildings"] > 0.9


def test_dted_header_and_grid():
    m = json.loads((DATA / "offline" / "manifest.json").read_text())
    _need(*(DATA / "offline" / c["file"] for c in m["dted"]))
    for cell in m["dted"]:
        with rasterio.open(DATA / "offline" / cell["file"]) as ds:
            assert ds.driver == "DTED" and ds.shape == (3601, 3601)
            assert ds.tags()["DTED_VerticalAccuracy_UHL"].strip().isdigit()
            assert ds.tags()["DTED_HorizontalDatum"] == "WGS84"


def test_geopackage_layers():
    import pyogrio
    m = json.loads((DATA / "offline" / "manifest.json").read_text())
    _need(DATA / "offline" / m["geopackage"]["file"])
    layers = {name for name, _ in pyogrio.list_layers(DATA / "offline" / m["geopackage"]["file"])}
    assert {"buildings"} <= layers


def test_stac_catalog_resolves():
    cat = pystac.Catalog.from_file(str(DATA / "stac" / "catalog.json"))
    items = list(cat.get_items())
    assert {"lidar-dtm-dsm", "buildings-3d"} <= {i.id for i in items}
    _need(*(a.get_absolute_href() for it in items for a in it.assets.values() if a.href.endswith((".tif", ".dt2", ".gpkg"))))
    for it in items:
        for a in it.assets.values():
            assert Path(a.get_absolute_href()).exists(), f"{it.id}: {a.href}"
