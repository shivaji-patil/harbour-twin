"""OGC 3D Tiles 1.1 writer for extruded buildings.

Each tile is a binary glTF with one mesh. Every vertex carries a feature ID
(EXT_mesh_features) that indexes a property table (EXT_structural_metadata),
so CesiumJS can pick and style individual buildings by their attributes
without a sidecar file. Geometry is stored in a local east-north-up frame at
the tile centre (float32-safe) and placed on the WGS 84 ellipsoid by the
tile transform.
"""
from __future__ import annotations

import json
import math
import struct
from pathlib import Path

import numpy as np
import pyproj
import trimesh
from shapely.geometry import MultiPolygon, Polygon

ECEF = pyproj.Transformer.from_crs("EPSG:4979", "EPSG:4978", always_xy=True)

STRING_PROPS = ["id", "name", "height_source", "class"]
BOOL_PROPS = ["height_flag"]
FLOAT_PROPS = ["height", "ground_cgvd2013", "roof_cgvd2013", "flood_level_cgvd2013"]


def enu_frame(lon: float, lat: float, h: float = 0.0):
    """ECEF origin and rotation (rows = east, north, up) at a point."""
    o = np.array(ECEF.transform(lon, lat, h))
    lo, la = math.radians(lon), math.radians(lat)
    east = np.array([-math.sin(lo), math.cos(lo), 0.0])
    north = np.array([-math.sin(la) * math.cos(lo), -math.sin(la) * math.sin(lo), math.cos(la)])
    up = np.array([math.cos(la) * math.cos(lo), math.cos(la) * math.sin(lo), math.sin(la)])
    return o, np.vstack([east, north, up])


def enu_to_ecef_matrix(origin, rot) -> list[float]:
    m = np.eye(4)
    m[:3, :3] = rot.T
    m[:3, 3] = origin
    return m.T.flatten().tolist()  # column-major, as 3D Tiles expects


def _polygons(geom):
    if isinstance(geom, Polygon):
        return [geom]
    if isinstance(geom, MultiPolygon):
        return list(geom.geoms)
    return []


def building_mesh(geom_utm, bottom: float, top: float) -> trimesh.Trimesh | None:
    """Extruded prism in UTM x/y with absolute z from `bottom` to `top`."""
    parts = []
    for poly in _polygons(geom_utm):
        poly = poly.buffer(0)
        if poly.is_empty or poly.area < 4 or top - bottom < 0.5:
            continue
        try:
            m = trimesh.creation.extrude_polygon(poly, top - bottom, engine="earcut")
        except Exception:
            continue
        m.apply_translation([0, 0, bottom])
        parts.append(m)
    if not parts:
        return None
    return trimesh.util.concatenate(parts)


def _pad(b: bytes, fill: bytes = b"\x00", align: int = 8) -> bytes:
    return b + fill * ((-len(b)) % align)


class _Bin:
    """Accumulates the glTF binary buffer and its bufferViews."""

    def __init__(self):
        self.data = bytearray()
        self.views = []

    def add(self, arr: bytes | np.ndarray, target: int | None = None) -> int:
        raw = arr.tobytes() if isinstance(arr, np.ndarray) else arr
        self.data += b"\x00" * ((-len(self.data)) % 8)
        view = {"buffer": 0, "byteOffset": len(self.data), "byteLength": len(raw)}
        if target:
            view["target"] = target
        self.data += raw
        self.views.append(view)
        return len(self.views) - 1


def write_glb(path: Path, positions, normals, feature_ids, indices, props: dict, schema: dict) -> None:
    b = _Bin()
    accessors = []

    def accessor(arr, ctype, typ, target, minmax=False):
        a = {"bufferView": b.add(arr, target), "componentType": ctype, "count": len(arr), "type": typ}
        if minmax:
            a["min"], a["max"] = arr.min(axis=0).tolist(), arr.max(axis=0).tolist()
        accessors.append(a)
        return len(accessors) - 1

    ARRAY, ELEMENT = 34962, 34963
    pos = accessor(positions.astype("float32"), 5126, "VEC3", ARRAY, minmax=True)
    nor = accessor(normals.astype("float32"), 5126, "VEC3", ARRAY)
    fid = accessor(feature_ids.astype("float32"), 5126, "SCALAR", ARRAY)
    idx = accessor(indices.astype("uint32").ravel(), 5125, "SCALAR", ELEMENT)

    n_features = len(next(iter(props.values())))
    table_props = {}
    for name in FLOAT_PROPS:
        table_props[name] = {"values": b.add(np.asarray(props[name], dtype="float32"))}
    for name in BOOL_PROPS:  # BOOLEAN properties are bitstreams, LSB first
        bits = np.packbits(np.asarray(props[name], dtype=bool), bitorder="little")
        table_props[name] = {"values": b.add(bits.tobytes())}
    for name in STRING_PROPS:
        enc = [str(v if v is not None else "").encode() for v in props[name]]
        offsets = np.cumsum([0] + [len(e) for e in enc]).astype("uint32")
        table_props[name] = {"values": b.add(b"".join(enc)), "stringOffsets": b.add(offsets),
                             "stringOffsetType": "UINT32"}

    gltf = {
        "asset": {"version": "2.0", "generator": "harbour-twin tiles3d.py"},
        "extensionsUsed": ["EXT_mesh_features", "EXT_structural_metadata"],
        "scene": 0, "scenes": [{"nodes": [0]}], "nodes": [{"mesh": 0}],
        "meshes": [{"primitives": [{
            "attributes": {"POSITION": pos, "NORMAL": nor, "_FEATURE_ID_0": fid},
            "indices": idx, "material": 0, "mode": 4,
            "extensions": {"EXT_mesh_features": {"featureIds": [
                {"featureCount": n_features, "attribute": 0, "propertyTable": 0}]}},
        }]}],
        "materials": [{"pbrMetallicRoughness": {"baseColorFactor": [0.92, 0.91, 0.88, 1.0],
                                                "metallicFactor": 0.0, "roughnessFactor": 0.9}}],
        "accessors": accessors,
        "bufferViews": b.views,
        "buffers": [{"byteLength": len(_pad(bytes(b.data), align=4))}],
        "extensions": {"EXT_structural_metadata": {
            "schema": schema,
            "propertyTables": [{"class": "building", "count": n_features, "properties": table_props}],
        }},
    }
    js = _pad(json.dumps(gltf, separators=(",", ":")).encode(), b" ", 4)
    bin_ = _pad(bytes(b.data), align=4)
    total = 12 + 8 + len(js) + 8 + len(bin_)
    with open(path, "wb") as f:
        f.write(struct.pack("<III", 0x46546C67, 2, total))
        f.write(struct.pack("<II", len(js), 0x4E4F534A) + js)
        f.write(struct.pack("<II", len(bin_), 0x004E4942) + bin_)


SCHEMA = {
    "id": "harbour_twin",
    "classes": {"building": {
        "name": "Building",
        "properties": {
            "id": {"type": "STRING", "description": "Overture Maps GERS id"},
            "name": {"type": "STRING"},
            "class": {"type": "STRING"},
            "height_source": {"type": "STRING",
                              "description": "lidar (HRDEM DSM-DTM), overture, floors, or default"},
            "height": {"type": "SCALAR", "componentType": "FLOAT32", "description": "metres above ground"},
            "ground_cgvd2013": {"type": "SCALAR", "componentType": "FLOAT32",
                                "description": "lowest ground at footprint, CGVD2013 metres"},
            "roof_cgvd2013": {"type": "SCALAR", "componentType": "FLOAT32"},
            "flood_level_cgvd2013": {"type": "SCALAR", "componentType": "FLOAT32",
                                     "description": "still-water level (CGVD2013 m) at which the sea "
                                                    "first reaches the footprint, by priority-flood"},
            "height_flag": {"type": "BOOLEAN",
                            "description": "lidar height disagrees with Overture/floors by >10 m and >50%"},
        },
    }},
}


def write_tileset(buildings, utm_crs, geoid_shift, out_dir: Path, tile_size: float = 600.0) -> dict:
    """Write tileset.json + one glb per tile.

    `buildings`: GeoDataFrame in `utm_crs` with columns id, name, class,
    height_source, height, ground_cgvd2013. `geoid_shift(lon, lat)` returns the
    CGVD2013 -> WGS 84 ellipsoidal offset; 3D Tiles heights are ellipsoidal.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "tiles").mkdir(exist_ok=True)
    to_ll = pyproj.Transformer.from_crs(utm_crs, "EPSG:4326", always_xy=True)

    cx = buildings.geometry.centroid
    buildings = buildings.assign(_tx=(cx.x // tile_size).astype(int), _ty=(cx.y // tile_size).astype(int))
    children, all_regions = [], []

    for (tx, ty), grp in buildings.groupby(["_tx", "_ty"]):
        x0, y0 = (tx + 0.5) * tile_size, (ty + 0.5) * tile_size
        lon0, lat0 = to_ll.transform(x0, y0)
        origin, rot = enu_frame(lon0, lat0, 0.0)
        P, N, F, I, props = [], [], [], [], {k: [] for k in STRING_PROPS + FLOAT_PROPS + BOOL_PROPS}
        voff = 0
        for _, b in grp.iterrows():
            m = building_mesh(b.geometry, b.ground_cgvd2013 - 2.0, b.ground_cgvd2013 + b.height)
            if m is None:
                continue
            m = m.copy()
            m.unmerge_vertices()  # flat shading: one normal per face corner
            v = m.vertices
            lon, lat = to_ll.transform(v[:, 0], v[:, 1])
            h_ell = v[:, 2] + geoid_shift(lon, lat)
            ecef = np.column_stack(ECEF.transform(lon, lat, h_ell))
            enu = (ecef - origin) @ rot.T
            # After unmerge, vertices are faces flattened in order: one face normal per corner.
            nrm = np.repeat(m.face_normals, 3, axis=0)  # UTM x/y/z ~ east/north/up locally
            fid = len(props["id"])
            P.append(np.column_stack([enu[:, 0], enu[:, 2], -enu[:, 1]]))  # ENU -> glTF y-up
            N.append(np.column_stack([nrm[:, 0], nrm[:, 2], -nrm[:, 1]]))
            F.append(np.full(len(v), fid))
            I.append(m.faces + voff)
            voff += len(v)
            for k in STRING_PROPS + FLOAT_PROPS + BOOL_PROPS:
                props[k].append(b.get(k))
        if not P:
            continue
        name = f"tiles/{tx}_{ty}.glb"
        write_glb(out_dir / name, np.vstack(P), np.vstack(N), np.concatenate(F), np.vstack(I), props, SCHEMA)

        w, s, e, n = grp.to_crs("EPSG:4326").total_bounds
        shift = float(geoid_shift(np.array([lon0]), np.array([lat0]))[0])
        hmin = float(grp.ground_cgvd2013.min()) - 2 + shift - 1
        hmax = float((grp.ground_cgvd2013 + grp.height).max()) + shift + 1
        region = [math.radians(w), math.radians(s), math.radians(e), math.radians(n), hmin, hmax]
        all_regions.append(region)
        children.append({"boundingVolume": {"region": region}, "geometricError": 0,
                         "transform": enu_to_ecef_matrix(origin, rot), "content": {"uri": name}})

    r = np.array(all_regions)
    root_region = [r[:, 0].min(), r[:, 1].min(), r[:, 2].max(), r[:, 3].max(), r[:, 4].min(), r[:, 5].max()]
    tileset = {
        "asset": {"version": "1.1", "generator": "harbour-twin"},
        "geometricError": 2000,
        "schema": SCHEMA,
        "root": {"boundingVolume": {"region": root_region}, "geometricError": 400,
                 "refine": "ADD", "children": children},
    }
    (out_dir / "tileset.json").write_text(json.dumps(tileset))
    return {"tiles": len(children), "buildings": int(sum(1 for _ in buildings.itertuples()))}
