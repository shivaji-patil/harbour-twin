"""CityJSON export: geometry, CRS and the validator itself (it must reject broken input)."""
import copy
import json

import geopandas as gpd
import pytest
from shapely.geometry import MultiPolygon, Polygon, box

from test_products import DATA, _need
from twin import export3d

UTM = 3157  # NAD83(CSRS) / UTM zone 10N


def _buildings():
    courtyard = Polygon(box(0, 0, 40, 30).exterior.coords, [box(10, 10, 30, 20).exterior.coords])
    rows = [("a", box(100, 0, 120, 20), 2.0, 32.0),            # simple block
            ("b", courtyard, 1.0, 13.0),                       # footprint with a hole
            ("c", MultiPolygon([box(200, 0, 210, 10), box(220, 0, 230, 10)]), 3.0, 9.0)]  # two parts
    g = gpd.GeoDataFrame({"id": [r[0] for r in rows], "ground_cgvd2013": [r[2] for r in rows],
                          "roof_cgvd2013": [r[3] for r in rows], "height": [r[3] - r[2] for r in rows],
                          "height_source": "lidar", "height_flag": False, "flood_level_cgvd2013": 4.2,
                          "name": None, "class": None},
                         geometry=[r[1] for r in rows], crs=UTM)
    return g.to_crs(4326)


@pytest.fixture(scope="module")
def doc():
    return export3d.cityjson(_buildings(), UTM, "test")


def test_valid_and_typed(doc):
    assert export3d.validate_cityjson(doc) == []
    objs = doc["CityObjects"]
    assert sorted(o["type"] for o in objs.values()) == ["Building"] * 3 + ["BuildingPart"] * 2
    assert objs["c"]["children"] == ["c-0", "c-1"] and objs["c-0"]["parents"] == ["c"]
    assert doc["metadata"]["referenceSystem"].endswith("/EPSG/0/6653")  # UTM 10N + CGVD2013 height


def test_heights_and_courtyard(doc):
    s, t = doc["transform"]["scale"], doc["transform"]["translate"]
    zs = sorted({round(v[2] * s[2] + t[2], 3) for v in doc["vertices"]})
    assert zs == [1.0, 2.0, 3.0, 9.0, 13.0, 32.0]
    floor = doc["CityObjects"]["b"]["geometry"][0]["boundaries"][0][0]
    assert len(floor) == 2  # outer ring + courtyard hole
    walls = doc["CityObjects"]["b"]["geometry"][0]["semantics"]["values"][0].count(2)
    assert walls == 8       # 4 outside + 4 around the courtyard


def test_validator_rejects_inward_face(doc):
    bad = copy.deepcopy(doc)
    roof = bad["CityObjects"]["a"]["geometry"][0]["boundaries"][0][1]
    roof[0].reverse()  # roof now faces down: shell no longer closed consistently
    assert any(e.startswith("a:") for e in export3d.validate_cityjson(bad))


def test_validator_rejects_schema_violation(doc):
    bad = copy.deepcopy(doc)
    bad["CityObjects"]["a"]["type"] = "Skyscraper"
    assert any(e.startswith("schema:") for e in export3d.validate_cityjson(bad))


def test_published_cityjson():
    path = DATA / "buildings.city.json"
    _need(path)
    d = json.loads(path.read_text())
    assert export3d.validate_cityjson(d) == []
    n = sum(o["type"] == "Building" for o in d["CityObjects"].values())
    assert n == len(gpd.read_parquet(DATA / "buildings.parquet"))
