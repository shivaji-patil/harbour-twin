AOI ?= vancouver
PY  ?= .venv/bin/python
TWIN = .venv/bin/twin

.PHONY: setup i3s-deps data record passes ais test validate serve image publish stack clean

setup:            ## virtualenv + Python deps + glTF validator + I3S converter (then `make i3s-deps`)
	python3 -m venv .venv && .venv/bin/pip install -q -e '.[dev]' jsonschema
	cd tools && npm ci --ignore-scripts

i3s-deps:         ## EGM96 geoid grid (GeographicLib, 5') for the I3S converter in export3d
	mkdir -p tools/deps && curl -sfL -o tools/deps/egm96-5.zip https://sourceforge.net/projects/geographiclib/files/geoids-distrib/egm96-5.zip/download
	$(PY) -c "import zipfile; z = zipfile.ZipFile('tools/deps/egm96-5.zip'); open('tools/deps/egm96-5.pgm', 'wb').write(z.read('geoids/egm96-5.pgm'))" && rm tools/deps/egm96-5.zip

data:             ## rebuild every product for $(AOI) from upstream sources (~2 min)
	$(TWIN) $(AOI) lidar qc terrain imagery buildings export3d ais sar tides package catalog

record:           ## capture live AIS for $(AOI) (needs AISSTREAM_API_KEY), e.g. make record MIN=120
	$(TWIN) $(AOI) record --minutes $(or $(MIN),60)

passes:           ## next Sentinel-1 overpasses of $(AOI): record AIS across one for the SAR cross-check
	$(TWIN) $(AOI) passes

ais:              ## rebuild tracks, SAR cross-check, tides and packages after a capture
	$(TWIN) $(AOI) ais sar tides package catalog

test:             ## unit tests + product acceptance tests
	TWIN_AOI=$(AOI) .venv/bin/pytest -q

validate:         ## Khronos glTF validation of every 3D Tiles content file
	node tools/validate_glb.mjs web/data/$(AOI)/buildings/tiles

serve:            ## open http://127.0.0.1:8765/?aoi=$(AOI)
	$(PY) -m http.server 8765 --directory web --bind 127.0.0.1

image:            ## the pipeline as a container image
	docker build -t harbour-twin .

publish:          ## upload $(AOI) products + viewer to S3-compatible storage, e.g. make publish TO=s3://bucket
	$(TWIN) $(AOI) publish --to $(TO)

stack:            ## local object storage + OGC API Features + STAC Browser (see compose.yaml)
	docker compose up --build
