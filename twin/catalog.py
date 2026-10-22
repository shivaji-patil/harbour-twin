"""Publish pipeline outputs as a static STAC catalog.

Each product becomes a STAC Item with its footprint, projection, file
checksums, vertical datum, lineage back to the upstream STAC items it was
made from, and the QC verdict, so a marketplace or a customer's catalog can
ingest it without reading our code.
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone

import pystac
import rasterio
from shapely.geometry import box, mapping

from .config import AOI

EXT = {
    "proj": "https://stac-extensions.github.io/projection/v2.0.0/schema.json",
    "file": "https://stac-extensions.github.io/file/v2.1.0/schema.json",
    "processing": "https://stac-extensions.github.io/processing/v1.2.0/schema.json",
}


def _sha256(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return "1220" + h.hexdigest()  # multihash prefix for sha2-256, per the file extension


def _asset(path, root, media, roles, title, **extra):
    a = pystac.Asset(href=str(path.relative_to(root)), media_type=media, roles=roles, title=title)
    a.extra_fields.update({"file:size": path.stat().st_size, "file:checksum": _sha256(path), **extra})
    return a


def build(aoi: AOI) -> dict:
    root = aoi.data_dir
    lineage = json.loads((root / "lineage.json").read_text())
    qc = json.loads((root / "qc_report.json").read_text()) if (root / "qc_report.json").exists() else {}
    now = datetime.now(timezone.utc)
    footprint = mapping(box(*aoi.bbox))
    cat = pystac.Catalog(id=f"harbour-twin-{aoi.slug}", description=f"3D harbour twin products for {aoi.name}",
                         title=f"Harbour twin: {aoi.name}")

    def item(id_, title, bbox=aoi.bbox):
        it = pystac.Item(id=id_, geometry=mapping(box(*bbox)), bbox=list(bbox), datetime=now,
                         properties={"title": title, "processing:software": {"harbour-twin": "0.1.0"},
                                     "processing:lineage": ""}, stac_extensions=list(EXT.values()))
        return it

    dtm = item("lidar-dtm-dsm", "Lidar terrain and surface models, 2 m, CGVD2013")
    with rasterio.open(root / "dtm_cgvd2013.tif") as ds:
        dtm.properties.update({"proj:code": f"EPSG:{ds.crs.to_epsg()}", "proj:shape": list(ds.shape),
                               "proj:transform": list(ds.transform)[:6], "vertical_datum": "CGVD2013 (EPSG:6647)"})
    dtm.properties["processing:lineage"] = ("NRCan HRDEM 1 m mosaic COGs, windowed read via NRCan STAC, "
                                            "average (DTM) / max (DSM) resampling")
    dtm.properties["source_items"] = lineage.get("hrdem_items", [])
    for f, t in (("dtm_cgvd2013.tif", "DTM"), ("dsm_cgvd2013.tif", "DSM"), ("ndsm.tif", "Normalized DSM")):
        dtm.add_asset(f.split(".")[0], _asset(root / f, root, pystac.MediaType.COG, ["data"], t))
    if qc:
        dtm.add_asset("qc", _asset(root / "QC_REPORT.md", root, "text/markdown", ["metadata"], "QC report"))
        dtm.properties["qc:gates"] = {name: [{"spec": g["spec"], "pass": g["pass"]} for g in c["gates"]]
                                      for name, c in qc["comparisons"].items()}
    cat.add_item(dtm)

    b = item("buildings-3d", "Buildings with lidar-measured heights: GeoParquet + 3D Tiles 1.1")
    b.properties.update(lineage.get("buildings", {}))
    b.properties["processing:lineage"] = (f"Overture Maps {aoi.overture_release} footprints (GeoParquet, "
                                          "bbox-filtered in DuckDB); height = p90(DSM) - p10(ground ring DTM)")
    b.add_asset("geoparquet", _asset(root / "buildings.parquet", root, "application/vnd.apache.parquet",
                                     ["data"], "Buildings (GeoParquet)"))
    b.add_asset("3dtiles", pystac.Asset(href=os.path.relpath(aoi.web_dir / "buildings" / "tileset.json", root),
                                        media_type="application/json", roles=["data", "visual"],
                                        title="OGC 3D Tiles 1.1 tileset (EXT_mesh_features + EXT_structural_metadata)"))
    cat.add_item(b)

    off = root / "offline" / "manifest.json"
    if off.exists():
        m = json.loads(off.read_text())
        o = item("offline-elevation", "DTED Level 2 + GeoPackage for offline platforms",
                 bbox=(min(c["cell"][0] for c in m["dted"]), min(c["cell"][1] for c in m["dted"]),
                       max(c["cell"][0] for c in m["dted"]) + 1, max(c["cell"][1] for c in m["dted"]) + 1))
        o.properties.update({"vertical_datum": "EGM96 (EPSG:5773)", "dted": m["dted"]})
        for c in m["dted"]:
            o.add_asset(c["file"].replace("/", "_"), _asset(root / "offline" / c["file"], root,
                                                           "application/octet-stream", ["data"], "DTED Level 2"))
        o.add_asset("geopackage", _asset(root / "offline" / m["geopackage"]["file"], root,
                                         "application/geopackage+sqlite3", ["data"], "GeoPackage"))
        cat.add_item(o)

    if (root / "ais_tracks.parquet").exists():
        a = item("ais-tracks", "AIS vessel tracks (aisstream.io), cleaned", bbox=aoi.ais_bbox)
        a.add_asset("tracks", _asset(root / "ais_tracks.parquet", root, "application/vnd.apache.parquet",
                                     ["data"], "Tracks (GeoParquet)"))
        a.add_asset("positions", _asset(root / "ais_positions.parquet", root, "application/vnd.apache.parquet",
                                        ["data"], "Positions (GeoParquet)"))
        cat.add_item(a)

    if (root / "sar_detections.parquet").exists() and "sar" in lineage:
        s = lineage["sar"]
        d = item("sar-ship-detections", "Sentinel-1 ship detections cross-checked against AIS", bbox=aoi.ais_bbox)
        d.datetime = datetime.fromisoformat(s["time"].replace("Z", "+00:00"))
        d.properties.update({k: s[k] for k in ("scene", "platform", "orbit_state", "polarisations", "ais_coverage",
                                                "detections", "by_status", "ais_not_seen", "params") if k in s})
        d.properties["processing:lineage"] = (f"{s['source']}; two-parameter CFAR on cross-pol dB over Overture/OSM "
                                              "open water; greedy AIS match with a speed-dependent gate")
        d.properties["source_items"] = [s["scene"]]
        d.add_asset("detections", _asset(root / "sar_detections.parquet", root, "application/vnd.apache.parquet",
                                         ["data"], "Detections (GeoParquet)"))
        cat.add_item(d)

    cat.normalize_hrefs(str(root / "stac"))
    for it in cat.get_items():  # asset paths are relative to data/<aoi>/; items sit two levels down
        for a in it.assets.values():
            if not a.href.startswith("http"):
                a.href = "../../" + a.href
    cat.save(catalog_type=pystac.CatalogType.SELF_CONTAINED)
    cat.validate_all()
    return {"items": [i.id for i in cat.get_items()]}
