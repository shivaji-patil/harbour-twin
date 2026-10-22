"""The same buildings in the 3D formats other platforms ingest: CityJSON and I3S.

3D Tiles (tiles3d.py) is what CesiumJS streams. City and planning tools read
CityJSON (the JSON encoding of CityGML); ArcGIS publishes I3S scene layers.
Both are written from the same products, so heights, flags and flood levels
are identical across formats.

CityJSON 2.0, LoD1: one closed solid per footprint (floor, roof, walls with
outward normals and semantic surface types), in UTM NAD83(CSRS) with CGVD2013
heights (a compound EPSG CRS, e.g. 6653). Multi-part footprints become a
Building with BuildingPart children, as the spec requires. Every structural
variant is checked against the official schema; every solid is checked closed
and outward-facing.

I3S (SLPK, 3DObject layer) comes from the loaders.gl tile-converter, which
needed three things to produce a correct package:
- release 4.3.4: in 4.4 and 4.5 its Node polyfills import `./buffer/btoa.node`
  without an extension, so it cannot read any local tile;
- a 3D Tiles 1.0 (b3dm + batch table) copy of our tileset, since 4.3.4 predates
  EXT_mesh_features; non-finite values must be JSON null (NaN broke 14 tiles);
- `--no-draco` (4.3.4 looks for its Draco worker at a wrong path) and the
  EGM96 geoid model, which is what the layer's height label says it uses.
`verify_slpk` reads the package back and fails the step if it is not right.
"""
from __future__ import annotations

import gzip
import json
import logging
import shutil
import struct
import subprocess
import zipfile
from datetime import date
from pathlib import Path

import geopandas as gpd
import numpy as np
from shapely.geometry import MultiPolygon, Polygon
from shapely.geometry.polygon import orient
from pyproj import CRS
from pyproj.database import query_crs_info

from .config import AOI, ROOT

log = logging.getLogger(__name__)

SCHEMA = ROOT / "tools" / "schemas" / "cityjson-2.0.1.min.schema.json"
ATTRS = ["height", "height_source", "height_flag", "flood_level_cgvd2013", "name", "class"]
SCALE = 0.001  # 1 mm vertex quantization
TOOLS = ROOT / "tools"
EGM96 = TOOLS / "deps" / "egm96-5.pgm"  # GeographicLib's EGM96 5' grid: `make i3s-deps`


# --- CityJSON -----------------------------------------------------------------------

def _rings(poly: Polygon):
    """Exterior CCW and holes CW (seen from above), without the closing vertex."""
    p = orient(poly, 1.0)
    return [list(p.exterior.coords)[:-1]] + [list(r.coords)[:-1] for r in p.interiors]


def lod1_solid(poly: Polygon, z0: float, z1: float, vertex) -> tuple[list, list]:
    """Boundaries of one extruded solid and their semantic surface indices.

    `vertex(x, y, z)` returns the index of a vertex in the shared vertex list.
    Faces follow CityJSON's rule: vertices counter-clockwise seen from outside.
    """
    rings = _rings(poly)
    bottom = [[vertex(x, y, z0) for x, y, *_ in r] for r in rings]
    top = [[vertex(x, y, z1) for x, y, *_ in r] for r in rings]
    faces, sem = [], []
    faces.append([list(reversed(r)) for r in bottom])  # floor: normal down
    sem.append(0)
    faces.append(top)                                   # roof: normal up
    sem.append(1)
    for rb, rt in zip(bottom, top):                     # walls: normal out of the solid
        n = len(rb)
        for i in range(n):
            j = (i + 1) % n
            faces.append([[rb[i], rb[j], rt[j], rt[i]]])
            sem.append(2)
    return [faces], sem  # one outer shell


def _parts(geom):
    if isinstance(geom, Polygon):
        geoms = [geom]
    elif isinstance(geom, MultiPolygon):
        geoms = list(geom.geoms)
    else:
        return []
    out = []
    for g in geoms:
        g = g.buffer(0)  # repairs self-touching rings from OSM
        out += [g] if isinstance(g, Polygon) else list(getattr(g, "geoms", []))
    return [g for g in out if not g.is_empty and g.area > 0.5]


def _clean(v):
    if v is None or (isinstance(v, float) and not np.isfinite(v)):
        return None
    if isinstance(v, (np.bool_, bool)):
        return bool(v)
    if isinstance(v, (np.floating,)):
        return round(float(v), 2)
    if isinstance(v, (np.integer,)):
        return int(v)
    return v


def compound_epsg(epsg_utm: int) -> int:
    """EPSG code of '<NAD83(CSRS) UTM zone> + CGVD2013 height' (e.g. 3157 -> 6653)."""
    name = f"{CRS.from_epsg(epsg_utm).name} + CGVD2013 height"
    hits = [c for c in query_crs_info(auth_name="EPSG", pj_types=["COMPOUND_CRS"]) if c.name == name]
    if not hits:
        raise ValueError(f"no EPSG compound CRS named {name!r}")
    return int(hits[0].code)


SURFACES = [{"type": "GroundSurface"}, {"type": "RoofSurface"}, {"type": "WallSurface"}]


def cityjson(buildings: gpd.GeoDataFrame, epsg_utm: int, title: str) -> dict:
    """CityJSON 2.0 document: LoD1 Building per footprint, heights in CGVD2013."""
    b = buildings.to_crs(epsg_utm)
    verts, index = [], {}

    def vertex(x, y, z):
        """Index of (x, y, z) in the shared vertex list, deduplicated at the 1 mm quantization."""
        key = (round(x / SCALE), round(y / SCALE), round(z / SCALE))
        if key not in index:
            index[key] = len(verts)
            verts.append(key)
        return index[key]

    objects = {}
    for row in b.itertuples():
        z0, z1 = float(row.ground_cgvd2013), float(row.roof_cgvd2013)
        parts = _parts(row.geometry)
        if not parts or not np.isfinite([z0, z1]).all() or z1 - z0 < 0.5:
            continue
        geoms = []
        for p in parts:
            shell, sem = lod1_solid(p, z0, z1, vertex)
            geoms.append({"type": "Solid", "lod": "1", "boundaries": shell,
                          "semantics": {"surfaces": SURFACES, "values": [sem]}})
        attrs = {k: _clean(getattr(row, k, None)) for k in ATTRS}
        attrs = {k: v for k, v in attrs.items() if v is not None}
        attrs["overture_id"] = row.id
        attrs["ground_cgvd2013"], attrs["roof_cgvd2013"] = round(z0, 2), round(z1, 2)
        if len(geoms) == 1:
            objects[row.id] = {"type": "Building", "attributes": attrs, "geometry": geoms}
        else:  # separate footprints of one building: BuildingParts, since a Building holds one solid
            kids = [f"{row.id}-{i}" for i in range(len(geoms))]
            objects[row.id] = {"type": "Building", "attributes": attrs, "geometry": [], "children": kids}
            for k, g in zip(kids, geoms):
                objects[k] = {"type": "BuildingPart", "parents": [row.id], "geometry": [g]}

    v = np.array(verts, dtype="int64")
    lo = v.min(axis=0)
    hi = v.max(axis=0)
    crs = f"https://www.opengis.net/def/crs/EPSG/0/{compound_epsg(epsg_utm)}"
    return {
        "type": "CityJSON", "version": "2.0",
        "transform": {"scale": [SCALE] * 3, "translate": (lo * SCALE).round(3).tolist()},
        "metadata": {"title": title, "referenceDate": date.today().isoformat(),
                     "referenceSystem": crs,
                     "geographicalExtent": np.concatenate([lo * SCALE, hi * SCALE]).round(3).tolist()},
        "CityObjects": objects,
        "vertices": (v - lo).tolist(),
    }


def _variant(obj: dict, objects: dict) -> tuple:
    """What the schema can tell apart: object type, parts, holes, attribute names and types."""
    geoms = obj.get("geometry") or [g for k in obj.get("children", []) for g in objects.get(k, {}).get("geometry", [])]
    holes = any(len(face) > 1 for g in geoms for face in g["boundaries"][0])
    attrs = tuple(sorted((k, type(v).__name__) for k, v in obj.get("attributes", {}).items()))
    return obj["type"], len(obj.get("children", [])) > 0, holes, attrs


def validate_cityjson(doc: dict) -> list[str]:
    """Official CityJSON 2.0.1 schema on one building of each structural variant (validating
    all 5,000+ costs minutes and adds nothing: they differ only in numbers), plus closed,
    outward-facing solids on every building."""
    import jsonschema
    objs = doc["CityObjects"]
    sample = {}
    for oid, obj in objs.items():
        if not obj.get("parents"):  # every top-level object, whatever its type; parts come with it
            sample.setdefault(_variant(obj, objs), oid)
    keep = {}
    for oid in sample.values():
        keep[oid] = objs[oid]
        keep.update({k: objs[k] for k in objs[oid].get("children", [])})
    errors = [f"schema: {e.message}" for e in jsonschema.Draft7Validator(json.loads(SCHEMA.read_text()))
              .iter_errors({**doc, "CityObjects": keep})][:20]
    s, t = np.array(doc["transform"]["scale"]), np.array(doc["transform"]["translate"])
    vx = np.array(doc["vertices"]) * s + t
    for oid, obj in objs.items():
        for g in obj.get("geometry", []):
            err = _check_shell(g["boundaries"][0], vx)
            if err:
                errors.append(f"{oid}: {err}")
        for k in obj.get("children", []):
            if oid not in objs.get(k, {}).get("parents", []):
                errors.append(f"{oid}: child {k} missing or not linked back")
    return errors


def _check_shell(shell, vx) -> str | None:
    """Closed 2-manifold (each edge used once in each direction) with positive volume."""
    edges = {}
    vol = 0.0
    for face in shell:
        for ring in face:
            for a, b in zip(ring, ring[1:] + ring[:1]):
                edges[(a, b)] = edges.get((a, b), 0) + 1
        outer = vx[face[0]]
        for k in range(1, len(outer) - 1):  # fan triangulation: divergence theorem
            vol += np.dot(outer[0], np.cross(outer[k], outer[k + 1])) / 6
        for hole in face[1:]:
            h = vx[hole]
            for k in range(1, len(h) - 1):
                vol += np.dot(h[0], np.cross(h[k], h[k + 1])) / 6
    if any(n != 1 or edges.get((b, a)) != 1 for (a, b), n in edges.items()):
        return "shell not closed"
    if vol <= 0:
        return f"inward-facing shell (volume {vol:.1f})"
    return None



# --- I3S --------------------------------------------------------------------------------

def _read_glb(path: Path) -> tuple[dict, bytes]:
    b = path.read_bytes()
    jl = struct.unpack_from("<I", b, 12)[0]
    bl = struct.unpack_from("<I", b, 20 + jl)[0]
    return json.loads(b[20:20 + jl]), b[28 + jl:28 + jl + bl]


def _property_table(gltf: dict, bin_: bytes) -> tuple[int, dict]:
    """EXT_structural_metadata property table as plain lists (non-finite numbers -> None)."""
    def view(i):
        """Bytes of glTF buffer view `i`."""
        v = gltf["bufferViews"][i]
        return bin_[v.get("byteOffset", 0):v.get("byteOffset", 0) + v["byteLength"]]
    meta = gltf["extensions"]["EXT_structural_metadata"]
    table = meta["propertyTables"][0]
    cls = meta["schema"]["classes"][table["class"]]["properties"]
    n, out = table["count"], {}
    for name, prop in table["properties"].items():
        raw = view(prop["values"])
        if cls[name]["type"] == "STRING":
            offs = np.frombuffer(view(prop["stringOffsets"]), "<u4")
            out[name] = [raw[offs[i]:offs[i + 1]].decode() for i in range(n)]
        elif cls[name]["type"] == "BOOLEAN":
            out[name] = [bool(x) for x in np.unpackbits(np.frombuffer(raw, "u1"), bitorder="little")[:n]]
        else:
            out[name] = [round(float(x), 3) if np.isfinite(x) else None for x in np.frombuffer(raw, "<f4")[:n]]
    return n, out


def _pad8(b: bytes, used: int) -> bytes:
    return b + b" " * ((-used) % 8)


def glb_to_b3dm(src: Path, dst: Path) -> int:
    """One 3D Tiles 1.1 glb (EXT_mesh_features) as a 3D Tiles 1.0 b3dm with a JSON batch table."""
    gltf, bin_ = _read_glb(src)
    n, table = _property_table(gltf, bin_)
    prim = gltf["meshes"][0]["primitives"][0]
    prim["attributes"]["_BATCHID"] = prim["attributes"].pop("_FEATURE_ID_0")
    for d in (prim, gltf):
        d.pop("extensions", None)
    gltf.pop("extensionsUsed", None)
    js = json.dumps(gltf, separators=(",", ":")).encode()
    js += b" " * ((-len(js)) % 4)
    bn = bin_ + b"\x00" * ((-len(bin_)) % 4)
    glb = (struct.pack("<III", 0x46546C67, 2, 28 + len(js) + len(bn))
           + struct.pack("<II", len(js), 0x4E4F534A) + js + struct.pack("<II", len(bn), 0x004E4942) + bn)
    ft = json.dumps({"BATCH_LENGTH": n}).encode()
    ft = _pad8(ft, 28 + len(ft))
    bt = json.dumps(table, separators=(",", ":"), allow_nan=False).encode()
    bt = _pad8(bt, 28 + len(ft) + len(bt))
    total = 28 + len(ft) + len(bt) + len(glb)
    dst.write_bytes(b"b3dm" + struct.pack("<IIIIII", 1, total, len(ft), 0, len(bt), 0) + ft + bt + glb)
    return n


def tileset_1_0(src_dir: Path, out_dir: Path) -> int:
    """A 3D Tiles 1.0 copy of our tileset (same tiles and transforms, b3dm content)."""
    (out_dir / "tiles").mkdir(parents=True, exist_ok=True)
    ts = json.loads((src_dir / "tileset.json").read_text())
    ts["asset"]["version"] = "1.0"
    ts.pop("schema", None)
    n = 0
    for child in ts["root"]["children"]:
        uri = child["content"]["uri"]
        child["content"]["uri"] = uri.replace(".glb", ".b3dm")
        n += glb_to_b3dm(src_dir / uri, out_dir / child["content"]["uri"])
    (out_dir / "tileset.json").write_text(json.dumps(ts))
    return n


def slpk(tileset: Path, out: Path) -> Path:
    """Convert to an I3S scene layer package with the pinned loaders.gl tile-converter."""
    if not EGM96.exists() or not (TOOLS / "node_modules" / ".bin" / "tile-converter").exists():
        raise FileNotFoundError("I3S needs `make i3s-deps` (tile-converter 4.3.4 and the EGM96 geoid)")
    work = out.parent / "i3s_build"
    shutil.rmtree(work, ignore_errors=True)
    legacy = work / "tiles_1_0"
    tileset_1_0(tileset.parent, legacy)
    cmd = ["npx", "--no-install", "tile-converter", "--input-type", "3DTILES",
           "--tileset", str((legacy / "tileset.json").resolve()), "--name", out.stem,
           "--output", str((work / "out").resolve()), "--egm", str(EGM96), "--slpk", "--no-draco"]
    run = subprocess.run(cmd, cwd=TOOLS, capture_output=True, text=True)
    failed = run.stdout.count("Failed to load")
    if run.returncode or failed:
        raise RuntimeError(f"tile-converter: exit {run.returncode}, {failed} tiles failed to load\n{run.stderr[-2000:]}")
    next((work / "out").rglob("*.slpk")).replace(out)
    shutil.rmtree(work)
    return out


def verify_slpk(path: Path, expected_nodes: int) -> dict:
    """Read the package back: a 3DObject layer with every tile's geometry and the building attributes."""
    z = zipfile.ZipFile(path)
    names = set(z.namelist())

    def load(name):
        """A JSON entry of the package, gunzipped if needed."""
        raw = z.read(name)
        return json.loads(gzip.decompress(raw) if raw[:2] == b"\x1f\x8b" else raw)
    layer = load("3dSceneLayer.json.gz")
    nodes = [nd for n in sorted(x for x in names if x.startswith("nodepages/")) for nd in load(n)["nodes"]]
    meshes = [nd for nd in nodes if nd.get("mesh")]
    fields = [f["name"] for f in layer.get("fields", [])]
    geoms = sum("/geometries/" in n for n in names)
    problems = []
    if layer.get("layerType") != "3DObject":
        problems.append(f"layerType {layer.get('layerType')}")
    if len(meshes) != expected_nodes or geoms != expected_nodes:
        problems.append(f"{len(meshes)} mesh nodes, {geoms} geometry buffers, expected {expected_nodes}")
    if not {"id", "height", "flood_level_cgvd2013", "height_flag"} <= set(fields):
        problems.append(f"missing fields: {fields}")
    if layer.get("heightModelInfo", {}).get("vertCRS") != "EGM96_Geoid":
        problems.append(f"height model {layer.get('heightModelInfo')}")
    if problems:
        raise ValueError(f"{path.name} is not a usable I3S layer: " + "; ".join(problems))
    return {"layerType": "3DObject", "nodes": len(meshes), "fields": len(fields), "vertical": "EGM96 (gravity-related)"}

# --- step -----------------------------------------------------------------------------

def run(aoi: AOI) -> dict:
    """The `export3d` step: buildings.city.json (validated CityJSON) and buildings.slpk (verified I3S)
    in data/<aoi>/; raises if either check fails."""
    b = gpd.read_parquet(aoi.data_dir / "buildings.parquet")
    doc = cityjson(b, aoi.utm.to_epsg(), f"Harbour Twin buildings, {aoi.name}")
    errors = validate_cityjson(doc)
    if errors:
        raise ValueError("CityJSON failed validation:\n" + "\n".join(errors[:10]))
    cj = aoi.data_dir / "buildings.city.json"
    cj.write_text(json.dumps(doc, separators=(",", ":")))
    n = sum(o["type"] == "Building" for o in doc["CityObjects"].values())
    out = {"cityjson": {"file": cj.name, "buildings": n, "crs": doc["metadata"]["referenceSystem"],
                        "size_mb": round(cj.stat().st_size / 1e6, 1), "valid": True}}
    tileset = aoi.web_dir / "buildings" / "tileset.json"
    pkg = slpk(tileset, aoi.data_dir / "buildings.slpk")
    tiles = len(json.loads(tileset.read_text())["root"]["children"])
    out["i3s"] = {"file": pkg.name, "size_mb": round(pkg.stat().st_size / 1e6, 1), **verify_slpk(pkg, tiles)}
    log.info("export3d: %s", out)
    return out
