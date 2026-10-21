"""AIS cleaning and DTED cell geometry."""
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from twin import ais, package


def track(points, t0=datetime(2026, 10, 21, 19, tzinfo=timezone.utc)):
    return pd.DataFrame([{"mmsi": 1, "time": t0 + timedelta(seconds=s), "lon": lo, "lat": la}
                         for s, lo, la in points])


def test_clean_drops_position_jumps():
    # 10 kn steady track with one fix teleported 20 km away
    p = track([(0, -123.10, 49.30), (60, -123.098, 49.30), (120, -122.80, 49.30), (180, -123.094, 49.30)])
    c = ais.clean(p)
    assert len(c) == 3 and c.lon.max() < -123.0


def test_clean_drops_null_island_and_duplicates():
    p = track([(0, 0.0, 0.0), (0, -123.1, 49.3), (0, -123.1, 49.3), (60, -123.099, 49.3)])
    assert len(ais.clean(p)) == 2


def test_clean_splits_on_gaps():
    p = track([(0, -123.1, 49.3), (60, -123.099, 49.3), (3600, -123.09, 49.3), (3660, -123.089, 49.3)])
    assert ais.clean(p).segment.tolist() == [0, 0, 1, 1]


def test_ship_categories():
    assert ais.ship_category(70) == "cargo" and ais.ship_category(84) == "tanker"
    assert ais.ship_category(52) == "tug" and ais.ship_category(None) == "unknown"


def test_haversine_one_minute_of_latitude():
    assert ais.haversine_m(0, 49, 0, 49 + 1 / 60) == pytest.approx(1853, rel=0.01)


def test_dted_level2_grid_below_50N():
    g, step = package.dted_grid(-124, 49)
    assert (g.width, g.height, step) == (3601, 3601, 1)
    # first and last posts sit exactly on the cell edges
    x0, y0 = g.transform * (0.5, 0.5)
    x1, y1 = g.transform * (g.width - 0.5, g.height - 0.5)
    assert (x0, y0, x1, y1) == pytest.approx((-124, 50, -123, 49), abs=1e-9)


@pytest.mark.parametrize("lat,step", [(55, 2), (72, 3), (77, 4), (85, 6), (-50, 1), (-51, 2)])
def test_dted_longitude_spacing_zones(lat, step):
    assert package.dted_grid(0, lat)[1] == step


def test_dted_names_and_cells():
    assert package.dted_name(-124, 49) == "w124/n49.dt2"
    assert package.dted_name(5, -3) == "e005/s03.dt2"
    assert package.dted_cells((-123.15, 49.27, -123.07, 49.32)) == [(-124, 49)]
