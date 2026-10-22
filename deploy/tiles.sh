#!/bin/sh
# Buildings of each AOI as Mapbox Vector Tiles, zoom 12-16, in tippecanoe's directory layout (uncompressed, as pygeoapi serves them).
set -eu
for aoi in "$@"; do
  src=/data/$aoi/features.gpkg
  out=/data/$aoi/vtiles/buildings
  [ -f "$src" ] || { echo "skip $aoi: no $src"; continue; }
  rm -rf "$out" && mkdir -p "$(dirname "$out")"
  ogr2ogr -f GeoJSONSeq /vsistdout/ "$src" buildings \
    -select id,name,height,height_source,height_flag,flood_level_cgvd2013 \
  | tippecanoe --quiet --force --layer=buildings --minimum-zoom=12 --maximum-zoom=16 \
      --no-tile-compression --no-feature-limit --no-tile-size-limit --output-to-directory="$out"
  chown -R "$(stat -c %u:%g /data/$aoi)" /data/$aoi/vtiles  # files belong to whoever owns the data, not root
  echo "$aoi: $(find "$out" -name '*.pbf' | wc -l) tiles"
done
