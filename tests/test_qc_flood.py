"""Accuracy statistics, spec gates and the connected-flood algorithm."""
import numpy as np
import pytest

from twin import flood, qc
from twin.config import Spec


def test_metrics_of_gaussian_error():
    rng = np.random.default_rng(0)
    e = rng.normal(0.5, 2.0, 200_000).astype("float32")
    m = qc.metrics(e)
    assert m.bias_m == pytest.approx(0.5, abs=0.02)
    assert m.std_m == pytest.approx(2.0, abs=0.02)
    assert m.nmad_m == pytest.approx(2.0, abs=0.03)          # NMAD estimates sigma for normal data
    # LE90 of |N(0.5, 2)|: solve P(|X| < x) = 0.9 numerically
    xs = np.sort(np.abs(rng.normal(0.5, 2.0, 400_000)))
    assert m.le90_m == pytest.approx(xs[int(0.9 * len(xs))], rel=0.01)


def test_nmad_is_robust_to_blunders():
    rng = np.random.default_rng(1)
    e = rng.normal(0, 1, 10_000)
    e[:300] = 80  # 3% blunders (e.g. a building that was demolished)
    m = qc.metrics(e)
    assert m.rmse_m > 10 and m.nmad_m < 1.2


def test_metrics_ignores_nan_and_rejects_tiny_samples():
    assert qc.metrics(np.array([np.nan] * 50)) is None
    assert qc.metrics(np.r_[np.ones(20), [np.nan] * 5]).n == 20


def test_gate():
    spec = Spec("test", le90_m=4.0, max_bias_m=1.0, max_void_fraction=0.01)
    ok = {"overall": {"le90_m": 3.0, "bias_m": -0.4}, "void_fraction": 0.0}
    bad = {"overall": {"le90_m": 3.0, "bias_m": -19.0}, "void_fraction": 0.0}
    assert qc.gate(ok, spec)["pass"]
    g = qc.gate(bad, spec)
    assert not g["pass"] and [c["check"] for c in g["checks"] if not c["pass"]] == ["|bias|"]


def test_compare_by_slope():
    y, x = np.mgrid[0:100, 0:100].astype("float32")
    ref = np.where(x < 50, 0, (x - 50) * 2.0)  # flat half and a ~63 degree ramp at 1 m cells
    test = ref + np.where(x < 50, 0.1, 1.0)
    r = qc.compare(test, ref, 1.0)
    assert r["by_slope"]["0-5 deg"]["bias_m"] == pytest.approx(0.1, abs=1e-5)
    assert r["by_slope"]["30-90 deg"]["bias_m"] == pytest.approx(1.0, abs=1e-5)


def test_priority_flood_respects_barriers():
    """A dyke at 3 m protects a 1 m hollow until the water tops the dyke."""
    g = np.full((5, 7), 1.0, "float32")
    g[:, 0] = 0.0            # sea on the west edge
    g[:, 2] = 3.0            # dyke
    g[:, 4:] = 1.0           # low hollow behind it
    lvl = flood.fill_level(g, flood.seeds(g, sea_max=0.5))
    assert lvl[2, 1] == pytest.approx(1.0)   # seaward of the dyke: floods at its own height
    assert lvl[2, 5] == pytest.approx(3.0)   # behind the dyke: only once the dyke overtops
    # a bathtub threshold at 2 m would wrongly flood the hollow
    assert (g[2, 5] <= 2.0) and not (lvl[2, 5] <= 2.0)


def test_priority_flood_unreachable_basin():
    g = np.full((6, 6), 5.0, "float32")
    g[0, :] = 0.0
    g[3, 3] = -2.0           # inland pit below sea level, ringed by 5 m ground
    lvl = flood.fill_level(g, flood.seeds(g))
    assert lvl[3, 3] == pytest.approx(5.0)


def test_seeds_only_on_edges():
    g = np.zeros((4, 4), "float32")
    s = flood.seeds(g)
    assert s[0].all() and s[-1].all() and not s[1:-1, 1:-1].any()
