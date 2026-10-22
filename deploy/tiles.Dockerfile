# Vector tiles for OGC API - Tiles: GDAL reads features.gpkg, tippecanoe writes the z/x/y.pbf tree pygeoapi serves.
FROM ubuntu:24.04
RUN apt-get update && apt-get install -y --no-install-recommends gdal-bin tippecanoe && rm -rf /var/lib/apt/lists/*
COPY tiles.sh /usr/local/bin/tiles.sh
ENTRYPOINT ["sh", "/usr/local/bin/tiles.sh"]
