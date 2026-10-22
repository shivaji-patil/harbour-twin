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


# --- I3S --------------------------------------------------------------------------------

def _b3dm_parts(path):
    import struct
    b = path.read_bytes()
    assert b[:4] == b"b3dm"
    version, total, ftj, ftb, btj, btb = struct.unpack_from("<IIIIII", b, 4)
    assert version == 1 and total == len(b) and ftb == btb == 0
    assert (28 + ftj) % 8 == 0 and (28 + ftj + btj) % 8 == 0  # glb starts 8-byte aligned, as the spec requires
    ft = json.loads(b[28:28 + ftj])
    bt = json.loads(b[28 + ftj:28 + ftj + btj])  # strict JSON: NaN would fail here
    glb = b[28 + ftj + btj:]
    jl = struct.unpack_from("<I", glb, 12)[0]
    return ft, bt, json.loads(glb[20:20 + jl])


def test_b3dm_copy_of_a_tile(tmp_path):
    import numpy as np
    from twin import tiles3d
    x, y = 491000.0, 5460000.0
    b = gpd.GeoDataFrame({
        "id": ["a", "b"], "name": ["Tower", None], "class": ["office", None],
        "height_source": ["lidar", "overture"], "height": [100.0, 10.0],
        "ground_cgvd2013": [20.0, 5.0], "roof_cgvd2013": [120.0, 15.0],
        "flood_level_cgvd2013": [20.0, np.inf],  # the sea never reaches b
        "height_flag": [True, False],
    }, geometry=[box(x, y, x + 30, y + 30), box(x + 60, y, x + 80, y + 20)], crs=UTM)
    tiles3d.write_tileset(b, UTM, lambda lon, lat: np.full(np.shape(lon), -19.0), tmp_path / "v11")
    n = export3d.tileset_1_0(tmp_path / "v11", tmp_path / "v10")
    assert n == 2
    ts = json.loads((tmp_path / "v10" / "tileset.json").read_text())
    assert ts["asset"]["version"] == "1.0" and "schema" not in ts
    ft, bt, gltf = _b3dm_parts(tmp_path / "v10" / ts["root"]["children"][0]["content"]["uri"])
    assert ft == {"BATCH_LENGTH": 2}
    assert bt["id"] == ["a", "b"] and bt["height_flag"] == [True, False]
    assert bt["flood_level_cgvd2013"] == [20.0, None]  # infinity becomes null, not invalid JSON
    prim = gltf["meshes"][0]["primitives"][0]
    assert "_BATCHID" in prim["attributes"] and "_FEATURE_ID_0" not in prim["attributes"]
    assert "extensions" not in gltf and "extensions" not in prim


def _roof_heights(slpk):
    """Highest vertex of each building in an SLPK, by Overture id (I3S default geometry layout)."""
    import gzip
    import zipfile
    import numpy as np
    z = zipfile.ZipFile(slpk)
    names = set(z.namelist())

    def raw(n):
        r = z.read(n)
        return gzip.decompress(r) if r[:2] == b"\x1f\x8b" else r
    layer = json.loads(raw("3dSceneLayer.json.gz"))
    key = {a["name"]: a["key"] for a in layer["attributeStorageInfo"]}["id"]
    layout = layer["geometryDefinitions"][0]["geometryBuffers"][0]
    nodes = [nd for n in sorted(x for x in names if x.startswith("nodepages/")) for nd in json.loads(raw(n))["nodes"]]
    roofs = {}
    for nd in nodes:
        if not nd.get("mesh"):
            continue
        i = nd["index"]
        a = raw(f"nodes/{i}/attributes/{key}/0.bin.gz")
        count = int(np.frombuffer(a[:4], "<u4")[0])
        lens = np.frombuffer(a[8:8 + 4 * count], "<u4")
        off, ids = 8 + 4 * count, []
        for n in lens:
            ids.append(a[off:off + n].rstrip(b"\x00").decode())
            off += n
        g = raw(f"nodes/{i}/geometries/0.bin.gz")
        vc, fc = np.frombuffer(g[:8], "<u4")
        pos = np.frombuffer(g[8:8 + 12 * vc], "<f4").reshape(-1, 3)
        p = 8 + 24 * vc + (8 * vc if "uv0" in layout else 0) + (4 * vc if "color" in layout else 0) + 8 * fc
        ranges = np.frombuffer(g[p:p + 8 * fc], "<u4").reshape(-1, 2)
        for bid, (lo, hi) in zip(ids, ranges):
            roofs[bid] = nd["obb"]["center"][2] + float(pos[3 * lo:3 * (hi + 1), 2].max())
    return roofs


def test_published_slpk():
    from twin import datum
    path = DATA / "buildings.slpk"
    _need(path)
    tiles = json.loads((DATA.parent.parent / "web" / "data" / DATA.name / "buildings" / "tileset.json").read_text())
    info = export3d.verify_slpk(path, len(tiles["root"]["children"]))
    assert info["layerType"] == "3DObject"
    # The tallest building's roof, decoded from the package, matches the source after CGVD2013 -> EGM96.
    b = gpd.read_parquet(DATA / "buildings.parquet")
    top = b.loc[b.height.idxmax()]
    pt = top.geometry.representative_point()
    expected = top.roof_cgvd2013 + float(datum.shift_at("CGVD2013", "EGM96", pt.x, pt.y))
    assert _roof_heights(path)[top.id] == pytest.approx(expected, abs=0.3)
