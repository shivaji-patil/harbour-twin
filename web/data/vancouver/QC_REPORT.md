# Elevation QC report: Vancouver Harbour, BC

Generated 2026-10-21 19:19 UTC by `twin qc`. Every number below is recomputed on each pipeline run.

## Vertical datum audit

Height offsets at the AOI centre. These are the errors you get by mixing datums or letting PROJ choose a ballpark transform.

| Conversion | Offset (m) |
|---|---|
| EGM2008 -> WGS 84 ellipsoid (geoid undulation N) | -19.051 |
| CGVD2013 -> WGS 84 ellipsoid (CGG2013a + NAD83(CSRS) frame) | -19.116 |
| CGVD2013 -> WGS 84, PROJ default compound pipeline (ballpark frame) | -18.814 |
|   error of the PROJ default | 0.302 |
| EGM2008 -> CGVD2013 | 0.065 |
| EGM2008 -> EGM96 (DTED) | 0.728 |

## Copernicus GLO-30 vs lidar DSM

Copernicus DEM is an X-band radar surface model (EGM2008). Compared with the HRDEM lidar DSM averaged to 30 m, after converting EGM2008 to CGVD2013. Land cells only.

| n | bias | std | RMSE | NMAD | LE90 | LE95 |
|---|---|---|---|---|---|---|
| 403,743 | -2.36 | 5.41 | 5.90 | 2.00 | 6.69 | 9.01 |

By slope:

| slope | n | bias | RMSE | LE90 |
|---|---|---|---|---|
| 0-5 deg | 160,021 | -2.19 | 3.14 | 4.27 |
| 5-15 deg | 142,472 | -3.03 | 4.92 | 6.64 |
| 15-30 deg | 73,903 | -2.87 | 6.64 | 8.53 |
| 30-90 deg | 25,523 | -0.23 | 10.24 | 13.39 |

**Copernicus DEM GLO-30 spec: FAIL**

| check | value | limit | |
|---|---|---|---|
| LE90 | 6.691 | 4.0 | ❌ |
| |bias| | 2.356 | 1.0 | ❌ |
| void fraction | 0.0 | 0.01 | ✅ |

**DTED Level 2 (MIL-PRF-89020B): PASS**

| check | value | limit | |
|---|---|---|---|
| LE90 | 6.691 | 18.0 | ✅ |
| |bias| | 2.356 | 5.0 | ✅ |
| void fraction | 0.0 | 0.0 | ✅ |

## Copernicus GLO-30 vs lidar DTM, open ground

Cells where lidar sees no buildings or canopy (max DSM-DTM < 1 m), so surface and terrain coincide. This isolates sensor error from land-cover effects.

| n | bias | std | RMSE | NMAD | LE90 | LE95 |
|---|---|---|---|---|---|---|
| 9,499 | -0.15 | 9.69 | 9.69 | 1.57 | 4.64 | 4.66 |

By slope:

| slope | n | bias | RMSE | LE90 |
|---|---|---|---|---|
| 0-5 deg | 8,996 | -1.23 | 3.11 | 4.64 |
| 5-15 deg | 133 | 3.65 | 16.12 | 17.16 |
| 15-30 deg | 20 | 22.14 | 39.84 | 63.04 |

**Copernicus DEM GLO-30 spec: FAIL**

| check | value | limit | |
|---|---|---|---|
| LE90 | 4.639 | 4.0 | ❌ |
| |bias| | 0.148 | 1.0 | ✅ |
| void fraction | 0.0 | 0.01 | ✅ |

**DTED Level 2 (MIL-PRF-89020B): PASS**

| check | value | limit | |
|---|---|---|---|
| LE90 | 4.639 | 18.0 | ✅ |
| |bias| | 0.148 | 5.0 | ✅ |
| void fraction | 0.0 | 0.0 | ✅ |

## NRCan MRDEM-30 DTM vs lidar DTM

NRCan's 30 m bare-earth model, already in CGVD2013, against the lidar DTM averaged to 30 m. Like for like: both are terrain models on the same vertical datum.

| n | bias | std | RMSE | NMAD | LE90 | LE95 |
|---|---|---|---|---|---|---|
| 403,743 | 0.35 | 4.06 | 4.08 | 0.72 | 4.04 | 6.54 |

By slope:

| slope | n | bias | RMSE | LE90 |
|---|---|---|---|---|
| 0-5 deg | 215,781 | 0.02 | 0.85 | 0.69 |
| 5-15 deg | 104,868 | -0.05 | 2.49 | 2.73 |
| 15-30 deg | 59,861 | 0.21 | 4.90 | 6.72 |
| 30-90 deg | 21,325 | 4.22 | 9.24 | 13.83 |

**Copernicus DEM GLO-30 spec: FAIL**

| check | value | limit | |
|---|---|---|---|
| LE90 | 4.04 | 4.0 | ❌ |
| |bias| | 0.349 | 1.0 | ✅ |
| void fraction | 0.0 | 0.01 | ✅ |

**DTED Level 2 (MIL-PRF-89020B): PASS**

| check | value | limit | |
|---|---|---|---|
| LE90 | 4.04 | 18.0 | ✅ |
| |bias| | 0.349 | 5.0 | ✅ |
| void fraction | 0.0 | 0.0 | ✅ |

## Datum error demo: GLO-30 heights used as ellipsoidal

The classic 3D-globe bug: orthometric EGM2008 heights handed to a WGS 84 ellipsoid renderer (CesiumJS, 3D Tiles) without a geoid correction. Compared with lidar converted to ellipsoidal heights, so the bias is the geoid undulation plus GLO-30's own bias. The gates must fail, and they do.

| n | bias | std | RMSE | NMAD | LE90 | LE95 |
|---|---|---|---|---|---|---|
| 403,743 | 16.52 | 5.38 | 17.38 | 2.16 | 19.15 | 21.05 |

By slope:

| slope | n | bias | RMSE | LE90 |
|---|---|---|---|---|
| 0-5 deg | 160,008 | 16.95 | 17.10 | 18.69 |
| 5-15 deg | 142,469 | 15.82 | 16.28 | 18.62 |
| 15-30 deg | 73,920 | 15.68 | 16.76 | 20.65 |
| 30-90 deg | 25,522 | 18.17 | 20.83 | 27.37 |

**Copernicus DEM GLO-30 spec: FAIL**

| check | value | limit | |
|---|---|---|---|
| LE90 | 19.147 | 4.0 | ❌ |
| |bias| | 16.523 | 1.0 | ❌ |
| void fraction | 0.0 | 0.01 | ✅ |

**DTED Level 2 (MIL-PRF-89020B): FAIL**

| check | value | limit | |
|---|---|---|---|
| LE90 | 19.147 | 18.0 | ❌ |
| |bias| | 16.523 | 5.0 | ❌ |
| void fraction | 0.0 | 0.0 | ✅ |

## Sanity checks

| check | value | expected | |
|---|---|---|---|
| English Bay: sea surface, GLO-30 (EGM2008) | 0.00 | 0 ± 1.5 m | ✅ |
| Burrard Inlet: sea surface, GLO-30 (EGM2008) | 0.00 | 0 ± 1.5 m | ✅ |
| Strait of Georgia: sea surface, GLO-30 (EGM2008) | 0.00 | 0 ± 1.5 m | ✅ |
