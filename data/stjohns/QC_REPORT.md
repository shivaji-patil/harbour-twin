# Elevation QC report: St. John's Harbour, NL

Generated 2026-10-21 23:53 UTC by `twin qc`. Every number below is recomputed on each pipeline run.

## Vertical datum audit

Height offsets at the AOI centre. These are the errors you get by mixing datums or letting PROJ choose a ballpark transform.

| Conversion | Offset (m) |
|---|---|
| EGM2008 -> WGS 84 ellipsoid (geoid undulation N) | 9.694 |
| CGVD2013 -> WGS 84 ellipsoid (CGG2013a + NAD83(CSRS) frame) | 9.661 |
| CGVD2013 -> WGS 84, PROJ default compound pipeline (ballpark frame) | 10.714 |
|   error of the PROJ default | 1.053 |
| EGM2008 -> CGVD2013 | 0.033 |
| EGM2008 -> EGM96 (DTED) | -0.073 |

## Copernicus GLO-30 vs lidar DSM

Copernicus DEM is an X-band radar surface model (EGM2008). Compared with the HRDEM lidar DSM averaged to 30 m, after converting EGM2008 to CGVD2013. Land cells only.

| n | bias | std | RMSE | NMAD | LE90 | LE95 |
|---|---|---|---|---|---|---|
| 312,852 | -1.75 | 2.25 | 2.85 | 1.06 | 3.04 | 3.51 |

By slope:

| slope | n | bias | RMSE | LE90 |
|---|---|---|---|---|
| 0-5 deg | 197,529 | -1.62 | 1.99 | 2.89 |
| 5-15 deg | 103,548 | -1.86 | 2.34 | 3.28 |
| 15-30 deg | 9,559 | -1.93 | 5.63 | 4.78 |
| 30-90 deg | 2,211 | -6.65 | 20.10 | 27.14 |

**Copernicus DEM GLO-30 spec: FAIL**

| check | value | limit | |
|---|---|---|---|
| LE90 | 3.043 | 4.0 | ✅ |
| |bias| | 1.747 | 1.0 | ❌ |
| void fraction | 0.0 | 0.01 | ✅ |

**DTED Level 2 (MIL-PRF-89020B): PASS**

| check | value | limit | |
|---|---|---|---|
| LE90 | 3.043 | 18.0 | ✅ |
| |bias| | 1.747 | 5.0 | ✅ |
| void fraction | 0.0 | 0.0 | ✅ |

## Copernicus GLO-30 vs lidar DTM, open ground

Cells where lidar sees no buildings or canopy (max DSM-DTM < 1 m), so surface and terrain coincide. This isolates sensor error from land-cover effects.

| n | bias | std | RMSE | NMAD | LE90 | LE95 |
|---|---|---|---|---|---|---|
| 31,349 | -0.74 | 1.59 | 1.75 | 0.54 | 2.88 | 2.89 |

By slope:

| slope | n | bias | RMSE | LE90 |
|---|---|---|---|---|
| 0-5 deg | 30,475 | -0.76 | 1.70 | 2.88 |
| 5-15 deg | 828 | 0.02 | 2.30 | 3.26 |
| 15-30 deg | 33 | -0.58 | 5.85 | 9.13 |
| 30-90 deg | 10 | 7.53 | 13.86 | 17.01 |

**Copernicus DEM GLO-30 spec: PASS**

| check | value | limit | |
|---|---|---|---|
| LE90 | 2.881 | 4.0 | ✅ |
| |bias| | 0.738 | 1.0 | ✅ |
| void fraction | 0.0 | 0.01 | ✅ |

**DTED Level 2 (MIL-PRF-89020B): PASS**

| check | value | limit | |
|---|---|---|---|
| LE90 | 2.881 | 18.0 | ✅ |
| |bias| | 0.738 | 5.0 | ✅ |
| void fraction | 0.0 | 0.0 | ✅ |

## NRCan MRDEM-30 DTM vs lidar DTM

NRCan's 30 m bare-earth model, already in CGVD2013, against the lidar DTM averaged to 30 m. Like for like: both are terrain models on the same vertical datum.

| n | bias | std | RMSE | NMAD | LE90 | LE95 |
|---|---|---|---|---|---|---|
| 312,852 | 0.00 | 1.28 | 1.28 | 0.61 | 1.63 | 2.32 |

By slope:

| slope | n | bias | RMSE | LE90 |
|---|---|---|---|---|
| 0-5 deg | 218,887 | 0.01 | 0.55 | 0.88 |
| 5-15 deg | 82,789 | -0.01 | 1.50 | 2.31 |
| 15-30 deg | 9,038 | 0.13 | 3.69 | 5.35 |
| 30-90 deg | 2,133 | -1.16 | 8.02 | 12.09 |

**Copernicus DEM GLO-30 spec: PASS**

| check | value | limit | |
|---|---|---|---|
| LE90 | 1.627 | 4.0 | ✅ |
| |bias| | 0.0 | 1.0 | ✅ |
| void fraction | 0.0 | 0.01 | ✅ |

**DTED Level 2 (MIL-PRF-89020B): PASS**

| check | value | limit | |
|---|---|---|---|
| LE90 | 1.627 | 18.0 | ✅ |
| |bias| | 0.0 | 5.0 | ✅ |
| void fraction | 0.0 | 0.0 | ✅ |

## Datum error demo: GLO-30 heights used as ellipsoidal

The classic 3D-globe bug: orthometric EGM2008 heights handed to a WGS 84 ellipsoid renderer (CesiumJS, 3D Tiles) without a geoid correction. Compared with lidar converted to ellipsoidal heights, so the bias is the geoid undulation plus GLO-30's own bias. The gates must fail, and they do.

| n | bias | std | RMSE | NMAD | LE90 | LE95 |
|---|---|---|---|---|---|---|
| 312,852 | -11.37 | 2.25 | 11.59 | 1.06 | 12.62 | 13.06 |

By slope:

| slope | n | bias | RMSE | LE90 |
|---|---|---|---|---|
| 0-5 deg | 197,532 | -11.24 | 11.30 | 12.46 |
| 5-15 deg | 103,543 | -11.50 | 11.59 | 12.88 |
| 15-30 deg | 9,561 | -11.61 | 12.75 | 13.96 |
| 30-90 deg | 2,211 | -16.37 | 25.03 | 35.65 |

**Copernicus DEM GLO-30 spec: FAIL**

| check | value | limit | |
|---|---|---|---|
| LE90 | 12.619 | 4.0 | ❌ |
| |bias| | 11.373 | 1.0 | ❌ |
| void fraction | 0.0 | 0.01 | ✅ |

**DTED Level 2 (MIL-PRF-89020B): FAIL**

| check | value | limit | |
|---|---|---|---|
| LE90 | 12.619 | 18.0 | ✅ |
| |bias| | 11.373 | 5.0 | ❌ |
| void fraction | 0.0 | 0.0 | ✅ |

## Sanity checks

| check | value | expected | |
|---|---|---|---|
| St. John's Harbour: sea surface, GLO-30 (EGM2008) | 0.00 | 0 ± 1.5 m | ✅ |
| St. John's Harbour: sea surface, lidar DTM (CGVD2013) | -0.91 | 0 ± 1.5 m | ✅ |
| Atlantic off Cape Spear: sea surface, GLO-30 (EGM2008) | 0.00 | 0 ± 1.5 m | ✅ |
