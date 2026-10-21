"""Vertical datum conversions: the numbers every other product depends on."""
import numpy as np
import pyproj
import pytest
from pyproj.transformer import TransformerGroup
from rasterio.transform import from_origin

from twin import datum

VANCOUVER = (-123.1125, 49.295)
ST_JOHNS = (-52.71, 47.567)


@pytest.mark.parametrize("lon,lat", [VANCOUVER, ST_JOHNS])
def test_egm2008_matches_epsg_pipeline(lon, lat):
    """Our explicit EGM2008 step agrees with PROJ's EPSG 'WGS 84 to EGM2008 height (1)'."""
    ref = pyproj.Transformer.from_crs("EPSG:4979", "EPSG:4326+3855", always_xy=True).transform(lon, lat, 0)[2]
    assert datum.shift_at("EGM2008", "WGS84", lon, lat) == pytest.approx(-ref, abs=0.01)


def test_geoid_undulation_values():
    # Sanity anchors: N is about -19 m in Vancouver and about +10 m in St. John's.
    assert datum.shift_at("EGM2008", "WGS84", *VANCOUVER) == pytest.approx(-19.05, abs=0.2)
    assert datum.shift_at("EGM2008", "WGS84", *ST_JOHNS) == pytest.approx(9.69, abs=0.2)


def test_csrs_frame_step_is_the_epsg_helmert():
    """The NAD83(CSRS) -> WGS 84 parameters we hard-code equal PROJ's EPSG definition."""
    t = TransformerGroup("EPSG:4955", "EPSG:4979", always_xy=True).transformers[0]
    assert "NAD83(CSRS) to WGS 84 (2)" in t.description
    for token in ("x=-0.991", "y=1.9072", "z=0.5129", "rx=-0.0257899075194932",
                  "ry=-0.0096500989602704", "rz=-0.0116599432323421"):
        assert token in t.definition and token in datum._CSRS_TO_WGS84


def test_proj_default_compound_is_a_ballpark():
    """Why we build pipelines by hand: PROJ's default CGVD2013 -> WGS 84 skips the frame shift."""
    lon, lat = VANCOUVER
    ballpark = pyproj.Transformer.from_crs("EPSG:4617+6647", "EPSG:4979", always_xy=True).transform(lon, lat, 0)[2]
    ours = datum.shift_at("CGVD2013", "WGS84", lon, lat)
    assert abs(ballpark - ours) > 0.2  # ~0.3 m in Vancouver, ~1.05 m in St. John's


@pytest.mark.parametrize("a,b", [("EGM2008", "CGVD2013"), ("CGVD2013", "EGM96"), ("WGS84", "EGM2008")])
def test_round_trip(a, b):
    # Not exactly zero: the NAD83(CSRS) Helmert moves the point ~1 m horizontally, so the
    # return leg samples the geoid a metre away. That is micrometres; 0.1 mm is the bound.
    lon, lat = VANCOUVER
    assert datum.shift_at(a, b, lon, lat) + datum.shift_at(b, a, lon, lat) == pytest.approx(0, abs=1e-4)


def test_composition():
    lon, lat = VANCOUVER
    direct = datum.shift_at("EGM2008", "CGVD2013", lon, lat)
    via = datum.shift_at("EGM2008", "WGS84", lon, lat) + datum.shift_at("WGS84", "CGVD2013", lon, lat)
    assert direct == pytest.approx(via, abs=1e-6)


def test_shift_grid_matches_points():
    """The coarse-then-resample shortcut stays within a centimetre of exact per-point values."""
    crs = "EPSG:3157"  # NAD83(CSRS) / UTM 10N
    t = from_origin(488000, 5462000, 10, 10)
    shape = (300, 400)
    grid = datum.shift_grid("EGM2008", "WGS84", crs, t, shape)
    rows, cols = np.array([5, 150, 290]), np.array([7, 200, 390])
    xs, ys = t * (cols + 0.5, rows + 0.5)
    lon, lat = pyproj.Transformer.from_crs(crs, "EPSG:4326", always_xy=True).transform(xs, ys)
    exact = datum.shift_at("EGM2008", "WGS84", lon, lat)
    assert np.abs(grid[rows, cols] - exact).max() < 0.01


def test_same_datum_is_an_error():
    with pytest.raises(ValueError):
        datum.pipeline("EGM2008", "EGM2008")
