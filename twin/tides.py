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

from . import datum

log = logging.getLogger(__name__)
API = "https://api-iwls.dfo-mpo.gc.ca/api/v1"
LEVELS = ["MWL", "MHHW", "HHWLT", "HAT", "HRWL"]


def _get(url):
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=60))


def _iso(t: datetime) -> str:
    return t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def chart_datum_in_cgvd2013(meta: dict) -> tuple[float, str]:
    """Height of the station's chart datum in CGVD2013, and how it was obtained.

    Most stations publish the CGVD2013 offset directly. Some (St. John's, 00905)
    publish only CGVD28 and NAD83(CSRS) ellipsoidal offsets; there we go through
    the CGG2013a geoid, which defines CGVD2013 on that ellipsoid.
    """
    offsets = {d["code"]: d["offset"] for d in meta.get("datums", [])}
    if "CGVD2013" in offsets:
        return offsets["CGVD2013"], "CHS station metadata (CGVD2013)"
    if "NAD83_CSRS" in offsets:
        h = datum.nad83csrs_to_cgvd2013(meta["longitude"], meta["latitude"], offsets["NAD83_CSRS"])
        return round(h, 3), "CHS NAD83(CSRS) offset through the CGG2013a geoid"
    raise ValueError(f"station publishes no CGVD2013 or NAD83(CSRS) offset: {sorted(offsets)}")


def build(station_code: str, start: datetime, end: datetime, web_dir) -> dict:
    """CHS station water levels in CGVD2013: the published levels (MWL, MHHW, HAT, record high) and
    the observed series from `start` to `end` (predicted if none observed), written to
    `web_dir`/tide.json. Returns everything but the series."""
    st = _get(f"{API}/stations?code={station_code}")[0]
    meta = _get(f"{API}/stations/{st['id']}/metadata")
    offset, offset_from = chart_datum_in_cgvd2013(meta)
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
           "chart_datum_to_cgvd2013_m": offset, "offset_from": offset_from, "levels": levels, "series_kind": kind,
           "series": sorted(series), "source": "Fisheries and Oceans Canada, CHS IWLS API"}
    (web_dir / "tide.json").write_text(json.dumps(out))
    log.info("tides %s: offset %.2f, %d levels, %d %s samples", station_code, offset, len(levels), len(series), kind)
    return {k: v for k, v in out.items() if k != "series"}
