"""Water levels from the Canadian Hydrographic Service (CHS IWLS API).

CHS reports levels above chart datum. The station metadata publishes the
chart-datum to CGVD2013 offset, so every level here is converted to CGVD2013,
the same datum as the lidar terrain the flood planner floods.
"""
from __future__ import annotations

import json
import logging
import urllib.request
from datetime import datetime, timedelta, timezone

log = logging.getLogger(__name__)
API = "https://api-iwls.dfo-mpo.gc.ca/api/v1"
LEVELS = ["MWL", "MHHW", "HHWLT", "HAT", "HRWL"]


def _get(url):
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=60))


def _iso(t: datetime) -> str:
    return t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def build(station_code: str, start: datetime, end: datetime, web_dir) -> dict:
    st = _get(f"{API}/stations?code={station_code}")[0]
    meta = _get(f"{API}/stations/{st['id']}/metadata")
    offset = next(d["offset"] for d in meta["datums"] if d["code"] == "CGVD2013")
    types = {h["id"]: h for h in _get(f"{API}/height-types")}
    levels = {}
    for h in meta.get("heights", []):
        code = types.get(h["heightTypeId"], {}).get("code")
        if code in LEVELS:
            levels[code] = {"name": types[h["heightTypeId"]]["nameEn"], "chart_datum_m": h["value"],
                            "cgvd2013_m": round(h["value"] + offset, 2), "date": h.get("date")}

    series = []
    for code in ("wlo", "wlp"):  # observed if available, else predicted
        t = start
        while t < end:
            t2 = min(end, t + timedelta(days=6))
            rows = _get(f"{API}/stations/{st['id']}/data?time-series-code={code}&from={_iso(t)}&to={_iso(t2)}")
            series += [[r["eventDate"], round(r["value"] + offset, 3)] for r in rows if r.get("value") is not None]
            t = t2
        if series:
            kind = code
            break
    out = {"station": {"code": station_code, "name": meta["officialName"], "lon": meta["longitude"],
                       "lat": meta["latitude"]},
           "chart_datum_to_cgvd2013_m": offset, "levels": levels, "series_kind": kind,
           "series": sorted(series), "source": "Fisheries and Oceans Canada, CHS IWLS API"}
    (web_dir / "tide.json").write_text(json.dumps(out))
    log.info("tides %s: offset %.2f, %d levels, %d %s samples", station_code, offset, len(levels), len(series), kind)
    return {k: v for k, v in out.items() if k != "series"}
