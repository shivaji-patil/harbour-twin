AOI ?= vancouver
PY  ?= .venv/bin/python
TWIN = .venv/bin/twin

.PHONY: setup data record passes ais test validate serve clean

setup:            ## virtualenv + Python deps + glTF validator
	python3 -m venv .venv && .venv/bin/pip install -q -e '.[dev]' jsonschema
	cd tools && npm ci --ignore-scripts

data:             ## rebuild every product for $(AOI) from upstream sources (~2 min)
	$(TWIN) $(AOI) lidar qc terrain imagery buildings ais sar tides package catalog

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
