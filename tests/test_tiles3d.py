"""3D Tiles 1.1 writer: parse our own glb back and check geometry lands where it should."""
import json
import struct

import geopandas as gpd
import numpy as np
import pyproj
import pytest
from shapely.geometry import box

from twin import tiles3d

UTM = "EPSG:3157"


def read_glb(path):
    data = path.read_bytes()
    magic, version, length = struct.unpack_from("<III", data, 0)
    assert (magic, version, length) == (0x46546C67, 2, len(data))
    jlen, jtype = struct.unpack_from("<II", data, 12)
    assert jtype == 0x4E4F534A and jlen % 4 == 0
    gltf = json.loads(data[20:20 + jlen])
    blen, btype = struct.unpack_from("<II", data, 20 + jlen)
    assert btype == 0x004E4942 and blen % 4 == 0
    return gltf, data[28 + jlen:28 + jlen + blen]


def view(gltf, binary, idx, dtype):
    v = gltf["bufferViews"][idx]
    assert v["byteOffset"] % 8 == 0  # EXT_structural_metadata requires 8-byte alignment
    return np.frombuffer(binary, dtype, v["byteLength"] // np.dtype(dtype).itemsize, v["byteOffset"])


@pytest.fixture
def tileset(tmp_path):
    x, y = 491000.0, 5460000.0  # downtown Vancouver, UTM 10N
    b = gpd.GeoDataFrame({
        "id": ["a", "b"], "name": ["Tower", None], "class": ["office", None],
        "height_source": ["lidar", "overture"], "height": [100.0, 10.0],
        "ground_cgvd2013": [20.0, 5.0], "roof_cgvd2013": [120.0, 15.0],
        "flood_level_cgvd2013": [20.0, 2.5], "height_flag": [True, False],
    }, geometry=[box(x, y, x + 30, y + 30), box(x + 60, y, x + 80, y + 20)], crs=UTM)
    shift = lambda lon, lat: np.full(np.shape(lon), -19.0)  # constant geoid for the test
    info = tiles3d.write_tileset(b, UTM, shift, tmp_path)
    return tmp_path, info


def test_tileset_json(tileset):
    out, info = tileset
    ts = json.loads((out / "tileset.json").read_text())
    assert ts["asset"]["version"] == "1.1"
    assert ts["root"]["refine"] == "ADD"
    child = ts["root"]["children"][0]
    m = np.array(child["transform"]).reshape(4, 4).T
    r = m[:3, :3]
    assert np.allclose(r @ r.T, np.eye(3), atol=1e-12)  # pure rotation + translation
    w, s, e, n, hmin, hmax = child["boundingVolume"]["region"]
    assert w < e and s < n and hmin < 0 < hmax          # ellipsoidal heights: ground ~ +1 m, tops ~ +101 m


def test_glb_structure_and_metadata(tileset):
    out, _ = tileset
    ts = json.loads((out / "tileset.json").read_text())
    gltf, binary = read_glb(out / ts["root"]["children"][0]["content"]["uri"])
    prim = gltf["meshes"][0]["primitives"][0]
    fids = view(gltf, binary, gltf["accessors"][prim["attributes"]["_FEATURE_ID_0"]]["bufferView"], "float32")
    assert set(np.unique(fids)) == {0.0, 1.0}
    table = gltf["extensions"]["EXT_structural_metadata"]["propertyTables"][0]
    assert table["count"] == 2

    props = table["properties"]
    heights = view(gltf, binary, props["height"]["values"], "float32")
    assert heights.tolist() == [100.0, 10.0]
    offs = view(gltf, binary, props["height_source"]["stringOffsets"], "uint32")
    raw = view(gltf, binary, props["height_source"]["values"], "uint8").tobytes()
    assert [raw[offs[i]:offs[i + 1]].decode() for i in range(2)] == ["lidar", "overture"]
    flags = np.unpackbits(view(gltf, binary, props["height_flag"]["values"], "uint8"), bitorder="little")
    assert flags[:2].tolist() == [1, 0]
    # every primitive index addresses a real vertex
    idx = gltf["accessors"][prim["indices"]]
    pos = gltf["accessors"][prim["attributes"]["POSITION"]]
    ind = view(gltf, binary, idx["bufferView"], "uint32")
    assert ind.max() < pos["count"]


def test_roof_lands_at_ellipsoidal_height(tileset):
    """Take the highest vertex, apply the tile transform and convert ECEF back: roof = 120 - 19 m."""
    out, _ = tileset
    ts = json.loads((out / "tileset.json").read_text())
    child = ts["root"]["children"][0]
    gltf, binary = read_glb(out / child["content"]["uri"])
    acc = gltf["accessors"][gltf["meshes"][0]["primitives"][0]["attributes"]["POSITION"]]
    p = view(gltf, binary, acc["bufferView"], "float32").reshape(-1, 3)
    top = p[p[:, 1].argmax()]                             # glTF y-up
    enu = np.array([top[0], -top[2], top[1], 1.0])        # back to east, north, up
    ecef = np.array(child["transform"]).reshape(4, 4).T @ enu
    lon, lat, h = pyproj.Transformer.from_crs("EPSG:4978", "EPSG:4979", always_xy=True).transform(*ecef[:3])
    assert h == pytest.approx(120.0 - 19.0, abs=0.05)
