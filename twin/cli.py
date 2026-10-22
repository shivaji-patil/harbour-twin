"""twin <aoi> <step...>   e.g.  twin vancouver all   |   twin vancouver lidar qc"""
from __future__ import annotations

import argparse
import json
import logging
import time

from . import ais, catalog, config, export3d, package, pipeline, publish, sar, tides

STEPS = ["lidar", "qc", "terrain", "imagery", "buildings", "export3d", "ais", "sar", "tides", "package", "catalog"]


def _ais_window(aoi):
    """Tide window = AIS replay window (so water level and vessels share a clock), else last 24 h."""
    import pandas as pd
    from datetime import datetime, timedelta, timezone
    p = aoi.data_dir / "ais_positions.parquet"
    if p.exists():
        t = pd.read_parquet(p, columns=["time"]).time
        return t.min().to_pydatetime() - timedelta(hours=1), t.max().to_pydatetime() + timedelta(hours=1)
    now = datetime.now(timezone.utc)
    return now - timedelta(days=1), now


def main(argv=None):
    """`twin <aoi> <steps...>`: run the named steps (or all of STEPS) in order, logging each step's time."""
    ap = argparse.ArgumentParser(prog="twin", description=__doc__)
    ap.add_argument("aoi", help="config/<aoi>.yaml")
    ap.add_argument("steps", nargs="+", help=f"any of {STEPS}, 'all', 'record', 'passes' or 'publish'")
    ap.add_argument("--minutes", type=float, default=120, help="AIS capture length for 'record'")
    ap.add_argument("--to", help="destination for 'publish', e.g. s3://bucket/prefix")
    ap.add_argument("--scene", help="Sentinel-1 RTC item id for 'sar' (default: best match to the AIS capture)")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    aoi = config.load(a.aoi)
    raw = aoi.cache_dir / "ais_raw.jsonl"

    run = {
        "lidar": lambda: pipeline.lidar(aoi),
        "qc": lambda: pipeline.elevation_qc(aoi),
        "terrain": lambda: pipeline.terrain(aoi),
        "imagery": lambda: pipeline.imagery(aoi),
        "buildings": lambda: pipeline.buildings(aoi),
        "export3d": lambda: export3d.run(aoi),
        "ais": lambda: ais.build(raw, aoi.data_dir, aoi.web_dir) if raw.exists() else None,
        "sar": lambda: sar.build(aoi, a.scene),
        "tides": lambda: tides.build(aoi.tide_station, *_ais_window(aoi), aoi.web_dir) if aoi.tide_station else None,
        "package": lambda: package.run(aoi),
        "catalog": lambda: catalog.build(aoi),
        "record": lambda: ais.record(aoi.ais_bbox, raw, a.minutes),
        "publish": lambda: publish.run(aoi, a.to),
        "passes": lambda: {"next_sentinel1_passes": sar.next_passes(aoi.ais_bbox)},
    }
    steps = STEPS if a.steps == ["all"] else a.steps
    for s in steps:
        t = time.time()
        result = run[s]()
        logging.info("step %s done in %.0f s", s, time.time() - t)
        if isinstance(result, dict) and s not in ("qc",):
            print(json.dumps(result, indent=1, default=str)[:2000])


if __name__ == "__main__":
    main()
