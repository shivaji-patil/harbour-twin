"""AIS: record live vessel positions from aisstream.io, then build clean tracks.

`record` appends raw messages to JSONL so a long capture survives crashes and
reconnects. `build` turns them into cleaned tracks: GeoParquet for analysts,
GeoPackage for offline use, and CZML for time-dynamic replay in CesiumJS.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import LineString

log = logging.getLogger(__name__)

STREAM = "wss://stream.aisstream.io/v0/stream"
POSITION_TYPES = {"PositionReport", "StandardClassBPositionReport", "ExtendedClassBPositionReport"}
STATIC_TYPES = {"ShipStaticData", "StaticDataReport"}

# ITU-R M.1371 ship type codes, grouped the way a harbour master reads them.
def ship_category(code) -> str:
    try:
        c = int(code)
    except (TypeError, ValueError):
        return "unknown"
    if c == 30: return "fishing"
    if c in (31, 32, 52): return "tug"
    if c in (35,): return "military"
    if c in (36, 37): return "pleasure"
    if c in (50, 51, 53, 54, 55, 58, 59): return "service"
    if 60 <= c <= 69: return "passenger"
    if 70 <= c <= 79: return "cargo"
    if 80 <= c <= 89: return "tanker"
    return "other"


def api_key() -> str:
    if os.environ.get("AISSTREAM_API_KEY"):
        return os.environ["AISSTREAM_API_KEY"]
    env = Path(__file__).resolve().parents[1] / ".env"
    for line in env.read_text().splitlines():
        if line.startswith("AISSTREAM_API_KEY="):
            return line.split("=", 1)[1].strip()
    raise RuntimeError("Set AISSTREAM_API_KEY (free key from https://aisstream.io)")


async def _record(bbox, out: Path, seconds: float):
    import websockets

    w, s, e, n = bbox
    sub = {"APIKey": api_key(), "BoundingBoxes": [[[s, w], [n, e]]],
           "FilterMessageTypes": sorted(POSITION_TYPES | STATIC_TYPES)}
    end = time.time() + seconds
    count = 0
    with out.open("a") as f:
        while time.time() < end:
            try:
                async with websockets.connect(STREAM, ping_interval=20) as ws:
                    await ws.send(json.dumps(sub))
                    while time.time() < end:
                        try:
                            raw = await asyncio.wait_for(ws.recv(), timeout=30)
                        except asyncio.TimeoutError:
                            continue
                        f.write(raw if isinstance(raw, str) else raw.decode())
                        f.write("\n")
                        count += 1
                        if count % 500 == 0:
                            f.flush()
                            log.info("ais: %d messages", count)
            except Exception as exc:  # network drops are normal on long captures
                log.warning("ais stream dropped (%s); reconnecting in 5 s", exc)
                await asyncio.sleep(5)
    log.info("ais: recorded %d messages to %s", count, out)


def record(bbox, out: Path, minutes: float) -> None:
    asyncio.run(_record(bbox, out, minutes * 60))


def _parse_time(t: str) -> datetime:
    # "2026-10-21 19:30:12.123456789 +0000 UTC"
    return datetime.strptime(t[:26].rstrip(), "%Y-%m-%d %H:%M:%S.%f").replace(tzinfo=timezone.utc)


def load_raw(path: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    pos, static = [], {}
    for line in path.read_text().splitlines():
        try:
            m = json.loads(line)
        except json.JSONDecodeError:
            continue
        typ, md = m.get("MessageType"), m.get("MetaData", {})
        body = m.get("Message", {}).get(typ, {})
        if typ in POSITION_TYPES:
            pos.append({
                "mmsi": md["MMSI"], "time": _parse_time(md["time_utc"]),
                "lon": md["longitude"], "lat": md["latitude"],
                "sog": body.get("Sog"), "cog": body.get("Cog"), "heading": body.get("TrueHeading"),
                "nav_status": body.get("NavigationalStatus"), "name": (md.get("ShipName") or "").strip(),
            })
        elif typ in STATIC_TYPES:
            d = body.get("Dimension") or body.get("ReportB", {}).get("Dimension") or {}
            rec = static.setdefault(md["MMSI"], {})
            rec.update({k: v for k, v in {
                "name": (body.get("Name") or md.get("ShipName") or "").strip() or None,
                "ship_type": body.get("Type") or body.get("ReportB", {}).get("ShipType"),
                "destination": (body.get("Destination") or "").strip() or None,
                "length_m": (d.get("A", 0) + d.get("B", 0)) or None,
                "beam_m": (d.get("C", 0) + d.get("D", 0)) or None,
            }.items() if v})
    p = pd.DataFrame(pos)
    st = pd.DataFrame.from_dict(static, orient="index").rename_axis("mmsi").reset_index()
    return p, st


def haversine_m(lon1, lat1, lon2, lat2):
    lon1, lat1, lon2, lat2 = map(np.radians, (lon1, lat1, lon2, lat2))
    a = np.sin((lat2 - lat1) / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2
    return 2 * 6371008.8 * np.arcsin(np.sqrt(a))


def clean(p: pd.DataFrame, max_speed_kn: float = 45.0, gap_min: float = 20.0) -> pd.DataFrame:
    """Drop invalid fixes and duplicates, remove position jumps, split tracks on gaps."""
    p = p[(p.lat.abs() <= 90) & (p.lon.abs() <= 180) & ~((p.lat == 0) & (p.lon == 0))]
    p = p.sort_values(["mmsi", "time"]).drop_duplicates(["mmsi", "time"])
    keep = []
    for _, g in p.groupby("mmsi", sort=False):
        g = g.copy()
        # Iteratively drop fixes implying impossible speed from the previous kept fix.
        ok = np.ones(len(g), bool)
        last = 0
        for i in range(1, len(g)):
            dt = (g.time.iloc[i] - g.time.iloc[last]).total_seconds()
            d = haversine_m(g.lon.iloc[last], g.lat.iloc[last], g.lon.iloc[i], g.lat.iloc[i])
            if dt > 0 and d / dt * 1.94384 > max_speed_kn:
                ok[i] = False
            else:
                last = i
        g = g[ok]
        gap = g.time.diff().dt.total_seconds().fillna(0) > gap_min * 60
        g["segment"] = gap.cumsum()
        keep.append(g)
    return pd.concat(keep) if keep else p.iloc[0:0]


def build(raw: Path, out_dir: Path, web_dir: Path) -> dict:
    p, st = load_raw(raw)
    n_raw = len(p)
    p = clean(p)
    p = p.merge(st, on="mmsi", how="left", suffixes=("", "_static"))
    p["name"] = p["name_static"].fillna(p["name"]) if "name_static" in p else p["name"]
    p["category"] = p.get("ship_type", pd.Series(index=p.index, dtype=float)).map(ship_category)

    pts = gpd.GeoDataFrame(p.drop(columns=[c for c in p.columns if c.endswith("_static")]),
                           geometry=gpd.points_from_xy(p.lon, p.lat), crs="EPSG:4326")
    pts.to_parquet(out_dir / "ais_positions.parquet")

    tracks = []
    for (mmsi, seg), g in pts.groupby(["mmsi", "segment"]):
        if len(g) < 2:
            continue
        r = g.iloc[-1]
        tracks.append({"mmsi": int(mmsi), "segment": int(seg), "name": r["name"], "category": r["category"],
                       "start": g.time.iloc[0], "end": g.time.iloc[-1], "fixes": len(g),
                       "geometry": LineString(zip(g.lon, g.lat))})
    tr = gpd.GeoDataFrame(tracks, crs="EPSG:4326")
    tr.to_parquet(out_dir / "ais_tracks.parquet")

    czml = to_czml(pts)
    (web_dir / "ais.czml").write_text(json.dumps(czml, separators=(",", ":")))
    summary = {"raw_positions": n_raw, "clean_positions": len(pts), "vessels": int(pts.mmsi.nunique()),
               "tracks": len(tr), "start": str(pts.time.min()), "end": str(pts.time.max()),
               "by_category": pts.drop_duplicates("mmsi").category.value_counts().to_dict()}
    log.info("ais build: %s", summary)
    return summary


CATEGORY_COLORS = {  # RGBA
    "cargo": [46, 134, 222, 255], "tanker": [214, 69, 65, 255], "passenger": [38, 166, 91, 255],
    "tug": [240, 160, 30, 255], "fishing": [142, 68, 173, 255], "pleasure": [200, 200, 200, 255],
    "service": [0, 180, 200, 255], "military": [90, 90, 40, 255], "other": [150, 150, 150, 255],
    "unknown": [150, 150, 150, 255],
}


def to_czml(pts: gpd.GeoDataFrame) -> list:
    t0, t1 = pts.time.min(), pts.time.max()
    iso = lambda t: t.strftime("%Y-%m-%dT%H:%M:%SZ")
    doc = [{"id": "document", "name": "AIS replay", "version": "1.0",
            "clock": {"interval": f"{iso(t0)}/{iso(t1)}", "currentTime": iso(t0), "multiplier": 60,
                      "range": "LOOP_STOP", "step": "SYSTEM_CLOCK_MULTIPLIER"}}]
    for mmsi, g in pts.groupby("mmsi"):
        if len(g) < 2:
            continue
        start = g.time.iloc[0]
        cart = []
        for t, lo, la in zip(g.time, g.lon, g.lat):
            cart += [round((t - start).total_seconds(), 1), round(lo, 6), round(la, 6), 0]
        r = g.iloc[-1]
        cat = r["category"] if isinstance(r["category"], str) else "unknown"
        col = CATEGORY_COLORS.get(cat, CATEGORY_COLORS["other"])
        desc = (f"<table><tr><td>MMSI</td><td>{mmsi}</td></tr><tr><td>Type</td><td>{cat}</td></tr>"
                f"<tr><td>Destination</td><td>{r.get('destination') or '-'}</td></tr>"
                f"<tr><td>Length</td><td>{r.get('length_m') or '-'} m</td></tr></table>")
        doc.append({
            "id": f"vessel-{mmsi}", "name": r["name"] or f"MMSI {mmsi}", "description": desc,
            "properties": {"mmsi": int(mmsi), "category": cat},
            "availability": f"{iso(start)}/{iso(g.time.iloc[-1])}",
            "position": {"epoch": iso(start), "cartographicDegrees": cart,
                         "interpolationAlgorithm": "LINEAR", "forwardExtrapolationType": "HOLD"},
            "point": {"pixelSize": 9, "color": {"rgba": col}, "outlineColor": {"rgba": [255, 255, 255, 255]},
                      "outlineWidth": 1.5, "heightReference": "CLAMP_TO_GROUND",
                      "disableDepthTestDistance": 5e4},
            "path": {"leadTime": 0, "trailTime": 1800, "width": 2, "resolution": 30,
                     "material": {"solidColor": {"color": {"rgba": col[:3] + [150]}}}},
        })
    return doc
