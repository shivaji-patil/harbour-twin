# The twin pipeline as an image. Data stays outside: mount data/ and web/.
#   docker build -t harbour-twin .
#   docker run --rm -v "$PWD/data:/app/data" -v "$PWD/web:/app/web" harbour-twin vancouver all
FROM python:3.12.14-slim-bookworm
WORKDIR /app
# libexpat: the GDAL inside the rasterio/pyogrio wheels links it; the slim image leaves it out
RUN apt-get update && apt-get install -y --no-install-recommends libexpat1 && rm -rf /var/lib/apt/lists/*
ENV PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1 PROJ_NETWORK=ON
COPY pyproject.toml README.md ./
COPY twin ./twin
RUN pip install -e . && rm -rf /root/.cache
COPY config ./config
COPY tools/schemas ./tools/schemas
ENTRYPOINT ["twin"]
CMD ["--help"]
