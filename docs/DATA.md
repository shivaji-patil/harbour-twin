# Data sources

Every input is someone else's maintained product, reached through its own API. The pipeline records the upstream item ids it used in `data/<aoi>/lineage.json` and the STAC catalog.

| Source | Reached through | Used by | Format and datum | Licence |
|---|---|---|---|---|
| NRCan HRDEM lidar mosaic, 1 m (`hrdem-mosaic-1m`) | NRCan STAC `datacube.services.geo.ca/stac/api` | `lidar`, `qc`, `terrain` | COG, DTM + DSM, CGVD2013 | Open Government Licence – Canada |
| NRCan MRDEM-30 (`mrdem-30`) | NRCan STAC | `qc` | COG, DTM, CGVD2013 | OGL – Canada |
| Copernicus DEM GLO-30 (`cop-dem-glo-30`) | Earth Search STAC `earth-search.aws.element84.com/v1` | `qc`, `terrain`, `package` | COG, radar DSM, EGM2008 | © DLR e.V. 2010–2014 and © Airbus Defence and Space GmbH 2014–2018, provided under COPERNICUS by the EU and ESA |
| Sentinel-2 L2A (`sentinel-2-c1-l2a`) | Earth Search STAC | `imagery` | COG, `visual` true colour | Copernicus Sentinel data, free and open |
| Sentinel-1 RTC (`sentinel-1-rtc`) | Microsoft Planetary Computer STAC `planetarycomputer.microsoft.com/api/stac/v1`, anonymous SAS token | `sar`, `passes` | COG, gamma0 (linear), 10 m, VV/VH or HH/HV | Copernicus Sentinel data; RTC processing CC BY 4.0 |
| Overture Maps buildings, water, infrastructure (release in `config/<aoi>.yaml`) | Overture STAC `stac.overturemaps.org` → GeoParquet on S3 (us-west-2) via DuckDB | `buildings`, `terrain`, `sar` | GeoParquet 1.1, WGS 84 | ODbL (OSM-derived) / CDLA-Permissive-2.0, per theme |
| AIS | aisstream.io WebSocket (free API key in `.env`) | `record` | JSON messages, WGS 84 | aisstream.io terms |
| Water levels and station datums | DFO-CHS IWLS REST `api-iwls.dfo-mpo.gc.ca/api/v1` | `tides` | JSON, chart datum (converted to CGVD2013) | © Fisheries and Oceans Canada |
| Geoid grids (EGM2008, EGM96, CGG2013a) | PROJ CDN `cdn.proj.org`, fetched on first use | `datum.py` | GeoTIFF grids | per grid; see PROJ-data |
| Weather radar | ECCC MSC GeoMet WMS (viewer only, live) | viewer | WMS PNG | ECCC open data licence |
| Current weather | Open-Meteo API (viewer only, live) | viewer | JSON | CC BY 4.0 |
| Basemap | CARTO dark tiles (viewer only) | viewer | XYZ PNG | © OpenStreetMap contributors © CARTO |

## Access notes

- **Nothing is bulk-downloaded.** COGs are read by window over HTTP range requests (`sources.read_into`, choosing the COG overview that matches the target grid), and Overture GeoParquet is filtered by bounding box inside DuckDB, so only the bytes for the AOI cross the network.
- **NRCan STAC ignores `bbox` on `/search`** (checked October 2026), so `sources.nrcan_items` pages the collection and filters client side.
- **Planetary Computer blobs need a token.** `sar._token` fetches an anonymous read token (valid about a day) and appends it to each asset URL.
- **Sentinel-1 polarisation varies by region.** Over most seas it is VV+VH; off Newfoundland it is mostly HH+HV, the ice-monitoring mode. `sar.read_scene` handles both.
- **Some CHS stations publish no CGVD2013 offset.** See [ARCHITECTURE.md §3](ARCHITECTURE.md#3-heights-and-datums).

## When a source fails

The pipeline does not retry. A failed upstream read stops the step with the upstream error (for example `Could not resolve host`), and earlier products are left as they were. Re-running the step is safe because every step overwrites its own outputs. In the viewer, the live layers (weather, radar) and the optional products (AIS, tides, SAR) degrade to a note instead of breaking the page.
