"""SAR ship detection (CFAR) and the AIS cross-check, on synthetic scenes."""
from datetime import datetime, timedelta, timezone

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
from pyproj import CRS
from rasterio.transform import from_origin

from twin import sar
from twin.sources import Grid

T0 = datetime(2026, 10, 22, 14, 20, 55, tzinfo=timezone.utc)


def test_box_sum_matches_brute_force():
    rng = np.random.default_rng(0)
    a = rng.random((40, 50))
    r = 3
    pad = np.pad(a, r)
    brute = np.array([[pad[i:i + 2 * r + 1, j:j + 2 * r + 1].sum() for j in range(50)] for i in range(40)])
    assert np.allclose(sar.box_sum(a, r), brute)


def _sea(shape=(400, 400), seed=1):
    """Gamma-distributed sea clutter (4-look speckle) around -22 dB."""
    rng = np.random.default_rng(seed)
    return (rng.gamma(4.0, 1 / 4.0, shape) * 10 ** -2.2).astype("float32")


def _grid(shape):
    return Grid(CRS.from_epsg(32610), from_origin(500_000, 5_460_000, 10, 10), shape[1], shape[0])


def test_cfar_finds_ships_and_few_false_alarms():
    vh = _sea()
    ships = [(100, 100), (200, 300), (320, 60)]
    for r, c in ships:
        vh[r - 1:r + 2, c - 3:c + 4] = 10 ** -0.5   # 3 x 7 px, ~ -5 dB: a small freighter
    water = np.ones(vh.shape, bool)
    dets = sar.detect(vh, vh, water, _grid(vh.shape))
    g = _grid(vh.shape)
    rows = [round((g.transform.f - y) / 10 - 0.5) for y in dets.geometry.y]
    cols = [round((x - g.transform.c) / 10 - 0.5) for x in dets.geometry.x]
    found = {(r, c) for r, c in zip(rows, cols)}
    for r, c in ships:
        assert any(abs(r - fr) <= 1 and abs(c - fc) <= 3 for fr, fc in found), (r, c)
    assert len(dets) == len(ships)           # speckle alone never makes a 3-pixel target at k = 5


def test_cfar_ignores_land_and_shore():
    vh = _sea()
    vh[:, :150] = 10 ** -0.3                  # bright land: buildings, steep slopes
    water = np.zeros(vh.shape, bool)
    water[:, 160:] = True                     # mask keeps a shore buffer
    dets = sar.detect(vh, vh, water, _grid(vh.shape))
    assert len(dets) == 0


def _pos(rows):
    return pd.DataFrame(rows, columns=["mmsi", "time", "lon", "lat", "sog", "name", "category", "length_m"])


def test_ais_at_interpolates_holds_and_drops():
    p = _pos([
        (1, T0 - timedelta(minutes=2), -123.20, 49.30, 10.0, "MOVER", "cargo", 200),
        (1, T0 + timedelta(minutes=2), -123.18, 49.30, 10.0, "MOVER", "cargo", 200),
        (2, T0 - timedelta(hours=2), -123.25, 49.28, 0.1, "ANCHOR", "tanker", 180),   # at anchor: held
        (3, T0 - timedelta(hours=2), -123.10, 49.31, 12.0, "GONE", "passenger", 90),  # moving, stale: dropped
    ])
    a = sar.ais_at(p, T0).set_index("mmsi")
    assert a.loc[1, "lon"] == pytest.approx(-123.19) and a.loc[1, "age_s"] == 0
    assert a.loc[2, "lon"] == -123.25 and a.loc[2, "age_s"] == pytest.approx(7200)
    assert 3 not in a.index


def test_match_marks_dark_targets_and_unseen_ais():
    crs = "EPSG:32610"
    dets = gpd.GeoDataFrame({"n_px": [10, 10, 10]},
                            geometry=gpd.points_from_xy([0, 1000, 5000], [0, 0, 0]), crs=crs)
    ais = pd.DataFrame({"mmsi": [11, 22, 33], "x": [120.0, 1000 + 900, 9000], "y": [0.0, 0, 0],
                        "sog_kn": [0.0, 15.0, 0.0], "age_s": [0.0, 0.0, 0.0]})
    d, a = sar.match(dets, ais)
    # 120 m off and stationary: matched. 900 m off but moving 15 kn: inside the azimuth-shift gate.
    assert d.status.tolist() == ["ais", "ais", "dark"]
    assert d.mmsi.tolist()[:2] == [11, 22]
    assert a.seen.tolist() == [True, True, False]


def test_match_is_one_to_one():
    crs = "EPSG:32610"
    dets = gpd.GeoDataFrame(geometry=gpd.points_from_xy([0, 50], [0, 0]), crs=crs)
    ais = pd.DataFrame({"mmsi": [7], "x": [10.0], "y": [0.0], "sog_kn": [0.0], "age_s": [0.0]})
    d, _ = sar.match(dets, ais)
    assert sorted(d.status) == ["ais", "dark"]


def test_ais_covers_window():
    p = _pos([(1, T0 - timedelta(minutes=30), 0, 0, 0, "", "", 0), (1, T0 + timedelta(minutes=30), 0, 0, 0, "", "", 0)])
    assert sar.ais_covers(p, T0)
    assert not sar.ais_covers(p, T0 + timedelta(hours=2))
    assert not sar.ais_covers(p.iloc[0:0], T0)
