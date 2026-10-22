"""Automated elevation QC.

Accuracy is reported the way elevation specs state it: bias, RMSE, NMAD and
LE90 (the 90th percentile of absolute error, as in MIL-PRF-89020B for DTED and
the Copernicus DEM product handbook), stratified by slope because almost every
DEM spec relaxes on steep terrain.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np

SLOPE_CLASSES = [(0, 5), (5, 15), (15, 30), (30, 90)]


@dataclass
class Metrics:
    n: int
    bias_m: float
    std_m: float
    rmse_m: float
    mae_m: float
    nmad_m: float
    le90_m: float
    le95_m: float


def metrics(err: np.ndarray) -> Metrics | None:
    """Accuracy of an error array in metres, NaNs ignored: bias, std, RMSE, MAE, NMAD (robust sigma)
    and LE90/LE95 (90th/95th percentile of absolute error). None for fewer than 10 values."""
    e = err[np.isfinite(err)]
    if e.size < 10:
        return None
    a = np.abs(e)
    med = np.median(e)
    return Metrics(
        n=int(e.size),
        bias_m=float(e.mean()),
        std_m=float(e.std()),
        rmse_m=float(np.sqrt(np.mean(e ** 2))),
        mae_m=float(a.mean()),
        nmad_m=float(1.4826 * np.median(np.abs(e - med))),
        le90_m=float(np.percentile(a, 90)),
        le95_m=float(np.percentile(a, 95)),
    )


def slope_deg(z: np.ndarray, res: float) -> np.ndarray:
    """Terrain slope in degrees from a height grid with `res`-metre cells."""
    gy, gx = np.gradient(z.astype("float64"), res)
    return np.degrees(np.arctan(np.hypot(gx, gy))).astype("float32")


def compare(test: np.ndarray, ref: np.ndarray, res: float, mask: np.ndarray | None = None) -> dict:
    """Error statistics of `test` against `ref` (same grid and vertical datum)."""
    err = (test - ref).astype("float32")
    valid = np.isfinite(err)
    if mask is not None:
        valid &= mask
    err = np.where(valid, err, np.nan)
    slope = slope_deg(ref, res)
    by_slope = {}
    for lo, hi in SLOPE_CLASSES:
        m = metrics(err[(slope >= lo) & (slope < hi)])
        if m:
            by_slope[f"{lo}-{hi} deg"] = asdict(m)
    overall = metrics(err)
    return {
        "overall": asdict(overall) if overall else None,
        "by_slope": by_slope,
        "void_fraction": float(np.isnan(test).mean()),
        "histogram": _hist(err),
    }


def _hist(err, edges=np.arange(-10, 10.5, 0.5)):
    e = np.clip(err[np.isfinite(err)], edges[0], edges[-1])
    counts, _ = np.histogram(e, bins=edges)
    return {"edges_m": edges.round(2).tolist(), "counts": counts.tolist()}


def gate(result: dict, spec) -> dict:
    """Pass/fail checks of one comparison against one spec."""
    o = result["overall"]
    checks = [
        ("LE90", o["le90_m"], "<=", spec.le90_m),
        ("|bias|", abs(o["bias_m"]), "<=", spec.max_bias_m),
        ("void fraction", result["void_fraction"], "<=", spec.max_void_fraction),
    ]
    rows = [{"check": c, "value": round(v, 3), "limit": lim, "pass": bool(v <= lim)} for c, v, _, lim in checks]
    return {"spec": spec.name, "pass": all(r["pass"] for r in rows), "checks": rows}


def write_report(report: dict, out_dir: Path) -> None:
    """Write qc_report.json and the human-readable QC_REPORT.md to `out_dir`."""
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "qc_report.json").write_text(json.dumps(report, indent=2))
    (out_dir / "QC_REPORT.md").write_text(render_markdown(report))


def _fmt(v, nd=2):
    return f"{v:.{nd}f}" if isinstance(v, float) else str(v)


def render_markdown(r: dict) -> str:
    """The QC report as Markdown: datum audit, then per comparison the overall and by-slope
    metrics and each spec gate's verdict, then the sanity checks."""
    L = [f"# Elevation QC report: {r['aoi']}", "",
         f"Generated {r['generated']} by `twin qc`. Every number below is recomputed on each pipeline run.", ""]
    L += ["## Vertical datum audit", "",
          "Height offsets at the AOI centre. These are the errors you get by mixing datums or "
          "letting PROJ choose a ballpark transform.", "",
          "| Conversion | Offset (m) |", "|---|---|"]
    L += [f"| {k} | {_fmt(v, 3)} |" for k, v in r["datum_audit"].items()]
    L += [""]
    for name, c in r["comparisons"].items():
        o = c["result"]["overall"]
        L += [f"## {name}", "", c["description"], "",
              "| n | bias | std | RMSE | NMAD | LE90 | LE95 |", "|---|---|---|---|---|---|---|",
              f"| {o['n']:,} | {_fmt(o['bias_m'])} | {_fmt(o['std_m'])} | {_fmt(o['rmse_m'])} | "
              f"{_fmt(o['nmad_m'])} | {_fmt(o['le90_m'])} | {_fmt(o['le95_m'])} |", "",
              "By slope:", "", "| slope | n | bias | RMSE | LE90 |", "|---|---|---|---|---|"]
        for k, m in c["result"]["by_slope"].items():
            L += [f"| {k} | {m['n']:,} | {_fmt(m['bias_m'])} | {_fmt(m['rmse_m'])} | {_fmt(m['le90_m'])} |"]
        L += [""]
        for g in c.get("gates", []):
            L += [f"**{g['spec']}: {'PASS' if g['pass'] else 'FAIL'}**", "",
                  "| check | value | limit | |", "|---|---|---|---|"]
            L += [f"| {x['check']} | {x['value']} | {x['limit']} | {'✅' if x['pass'] else '❌'} |" for x in g["checks"]]
            L += [""]
    if r.get("sanity"):
        L += ["## Sanity checks", "", "| check | value | expected | |", "|---|---|---|---|"]
        L += [f"| {s['check']} | {_fmt(s['value'])} | {s['expected']} | {'✅' if s['pass'] else '❌'} |" for s in r["sanity"]]
        L += [""]
    return "\n".join(L)
