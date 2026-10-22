"""Object-storage publishing and the OGC API feature package."""
from pathlib import Path

import geopandas as gpd
import pyogrio
import pytest

from test_products import AOI, DATA, _need
from twin import config, publish


@pytest.mark.parametrize("name,ctype", [
    ("buildings.city.json", "application/city+json"),
    ("dtm_cgvd2013.tif", "image/tiff; application=geotiff; profile=cloud-optimized"),
    ("buildings.parquet", "application/vnd.apache.parquet"),
    ("815_9096.glb", "model/gltf-binary"),
    ("vancouver_offline.gpkg", "application/geopackage+sqlite3"),
    ("catalog.json", "application/json"),
    ("index.html", "text/html"),
])
def test_content_types(name, ctype):
    assert publish.content_type(Path(name)) == ctype


def test_publish_set_is_products_and_viewer_only():
    aoi = config.load(AOI)
    keys = [p.relative_to(config.ROOT).as_posix() for p in publish.files(aoi)]
    assert "web/index.html" in keys and f"data/{AOI}/stac/catalog.json" in keys
    assert not any(k.startswith(("data/cache/", "application/", ".")) or k.endswith((".env", ".aux.xml")) for k in keys)
    assert all(k.startswith((f"data/{AOI}/", "web/")) for k in keys)


def test_destination_must_be_s3():
    with pytest.raises(ValueError):
        publish.run(config.load(AOI), "https://example.com/bucket")


def test_features_package_matches_sources():
    path = DATA / "features.gpkg"
    _need(path)
    layers = dict(pyogrio.list_layers(path))
    assert layers["buildings"] in ("Polygon", "MultiPolygon")
    assert len(gpd.read_file(path, layer="buildings")) == len(gpd.read_parquet(DATA / "buildings.parquet"))
    for name in ("ais_tracks", "sar_detections"):
        if (DATA / f"{name}.parquet").exists():
            g = gpd.read_file(path, layer=name)
            assert len(g) == len(gpd.read_parquet(DATA / f"{name}.parquet"))
            assert g.feature_id.is_unique and g.crs.to_epsg() == 4326


def test_api_config_matches_data_and_tiles():
    """Every OGC API collection points at a layer that exists, and the tile settings match deploy/tiles.sh."""
    import re
    import yaml
    root = config.ROOT
    cfg = yaml.safe_load((root / "deploy" / "pygeoapi.config.yml").read_text())
    script = (root / "deploy" / "tiles.sh").read_text()
    zmin = int(re.search(r"--minimum-zoom=(\d+)", script).group(1))
    zmax = int(re.search(r"--maximum-zoom=(\d+)", script).group(1))
    for name, res in cfg["resources"].items():
        for prov in res["providers"]:
            if prov["type"] == "feature":
                gpkg = root / "data" / Path(prov["data"]["source"]).relative_to("/data")
                if gpkg.exists():
                    assert prov["layer"] in dict(pyogrio.list_layers(gpkg)), (name, prov["layer"])
            elif prov["type"] == "tile":
                assert prov["data"].endswith("/vtiles/buildings"), name
                assert (prov["options"]["zoom"]["min"], prov["options"]["zoom"]["max"]) == (zmin, zmax), name
