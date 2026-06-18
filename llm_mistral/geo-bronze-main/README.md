# geo-bronze

A multi-agent geospatial data collection system that uses an LLM to decompose high-level collection tasks into protocol-specific subtasks, execute them in parallel, validate results, and store raw data with rich metadata sidecars in an S3-compatible object store (MinIO).

This is the **bronze layer** of a geospatial data lake: raw, unprocessed data preserved exactly as received from the source, ready for downstream silver/gold transformation pipelines.

---

## Architecture

```
CollectionTask (YAML or CLI)
        │
        ▼
  ManagerAgent  ──── LLM ────► Source registry match
        │                      Task decomposition → Subtasks
        │
        ▼  (parallel)
  ┌─────┴──────────────────────┐
  │  Collectors                │
  │  ├─ HTTPCollector          │
  │  ├─ TileCollector          │
  │  ├─ ArcGISCollector        │
  │  └─ OGCCollector (WFS)     │
  └─────┬──────────────────────┘
        │
        ▼
  ValidationAgent  (format, size, spatial checks)
        │
        ▼
  BronzeWriter ──► MinIO  {source}/{YYYY}/{MM}/{DD}/{task_id}/{subtask_id}.ext
                           {source}/{YYYY}/{MM}/{DD}/{task_id}/{subtask_id}.sidecar.json
```

**Agents:**
- **ManagerAgent** — sends the task to the LLM to select sources and build concrete subtask parameters, then fans out execution
- **ValidationAgent** — deterministic checks: HTML error page detection, JSON header validation, size bounds (100 B – 2 GB), spatial overlap with AOI
- **Collectors** — protocol adapters: generic HTTP/REST, XYZ/MVT tiles, ArcGIS FeatureServer (paginated), OGC WFS

**Storage:** every object is written alongside a `.sidecar.json` that captures the source, request params, response headers, SHA-256 hash, AOI, decoder hint, and any warnings.

---

## Quick start

### Prerequisites

- Python 3.11+
- Docker and Docker Compose
- An Anthropic API key **or** a running [Ollama](https://ollama.com/) instance

### 1. Clone and install

```bash
git clone https://github.com/stu-project/geo-bronze.git
cd geo-bronze
pip install -e ".[dev]"
```

### 2. Configure environment

```bash
cp .env.example .env
```

Edit `.env`:

```env
# Choose "anthropic" or "ollama"
LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-...
ANTHROPIC_MODEL=claude-sonnet-4-5

# MinIO
MINIO_ENDPOINT=localhost:9000
MINIO_ACCESS_KEY=minioadmin
MINIO_SECRET_KEY=minioadmin
MINIO_BUCKET=geo-bronze
```

### 3. Start MinIO

```bash
make up
```

### 4. Initialise the bucket

```bash
geo-bronze init
```

### 5. Run a collection task

```bash
geo-bronze run --task-file examples/tasks/osm_landfills_chernihiv.yaml
```

Or with the shorthand Make target:

```bash
make run-example
```

---

## CLI reference

```
geo-bronze init                          Initialise the MinIO bucket
geo-bronze sources list                  List registered data sources
geo-bronze sources test <source-id>      Test connectivity to a source
geo-bronze run --task-file <path>        Run a task from a YAML file
geo-bronze run --entity-types <t> ...   Run a task from inline params
geo-bronze bronze list                   Browse stored objects
geo-bronze bronze inspect <key>          View sidecar metadata for an object
```

---

## Task YAML format

```yaml
entity_types:
  - landslide_hazard_zones
  - mine_clearance_areas

aoi:
  type: named           # named | bbox | polygon
  name: Chernihiv       # for named AOI
  # bbox: [minx, miny, maxx, maxy]   # for bbox AOI
  # geometry: {GeoJSON}              # for polygon AOI

time_window:            # optional
  start: "2024-01-01T00:00:00Z"
  end:   "2024-12-31T23:59:59Z"

params: {}              # optional task-specific overrides
```

See [examples/tasks/](examples/tasks/) for working examples.

---

## Registered data sources

Sources are declared in [src/geo_bronze/registry/sources.yaml](src/geo_bronze/registry/sources.yaml). The current catalogue includes:

| Source | Protocol | Coverage |
|--------|----------|----------|
| DSNS mine clearance | HTTP + VectorTile | Ukraine |
| Ukrhydrometcentr weather stations | HTTP | Ukraine |
| Ukrhydrometcentr fire danger | HTTP + PNG raster | Ukraine |
| Visicom basemap | XYZ tiles | Ukraine |
| OpenStreetMap Overpass API | HTTP POST (Overpass QL) | Global |
| OSM tiles | XYZ raster | Global |
| OSM Geofabrik bulk extracts | HTTP streaming | Global |

To add a source, append an entry to `sources.yaml` following the existing schema.

---

## Supported data formats

The `DecoderHint` enum (contract with the silver layer) covers:

- GeoJSON, generic JSON, JSON points, Overpass JSON
- GML, ArcGIS JSON (single response + paginated JSONL)
- XYZ raster tiles, Mapbox Vector Tiles (MVT)
- GeoTIFF, PNG (with/without georef)
- Shapefiles, OSM PBF, CSV

---

## Local LLM (Ollama)

To run without an Anthropic API key:

```bash
# Start Ollama alongside MinIO
make up-llm

# Set in .env
LLM_PROVIDER=ollama
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=llama3.1:8b
```

---

## Development

```bash
make install       # install in editable mode with dev extras
make format        # ruff format + autofix
make lint          # ruff check
make typecheck     # mypy
make test          # pytest
make test-cov      # pytest with HTML coverage report
make clean         # remove caches and build artefacts
```

Tests use `respx` for HTTP mocking and `moto[s3]` for S3/MinIO mocking — no live services required.

---

## Object storage layout

```
geo-bronze/                           ← MinIO bucket
└── {source_id}/
    └── {YYYY}/
        └── {MM}/
            └── {DD}/
                └── {task_id}/
                    ├── {subtask_id}.geojson
                    └── {subtask_id}.sidecar.json
```

Each `.sidecar.json` contains: source ID, request parameters, HTTP response metadata, SHA-256 hash, AOI, decoder hint for the silver layer, and any validation warnings.

---

## Configuration reference

| Variable | Default | Description |
|---|---|---|
| `LLM_PROVIDER` | `ollama` | `anthropic` or `ollama` |
| `ANTHROPIC_API_KEY` | — | Required when `LLM_PROVIDER=anthropic` |
| `ANTHROPIC_MODEL` | `claude-sonnet-4-5` | Claude model ID |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama endpoint |
| `OLLAMA_MODEL` | `llama3.1:8b` | Ollama model name |
| `MINIO_ENDPOINT` | `localhost:9000` | MinIO host:port |
| `MINIO_ACCESS_KEY` | `minioadmin` | MinIO access key |
| `MINIO_SECRET_KEY` | `minioadmin` | MinIO secret key |
| `MINIO_BUCKET` | `geo-bronze` | Target bucket name |
| `MINIO_SECURE` | `false` | Use TLS for MinIO |
| `STREAMING_THRESHOLD_BYTES` | `52428800` | Switch to streaming above this size (50 MB) |
| `STREAMING_CHUNK_SIZE_BYTES` | `5242880` | Multipart chunk size (5 MB) |
| `VISICOM_API_KEY` | — | Optional Visicom basemap key |
| `LOG_LEVEL` | `INFO` | Logging level |
| `LOG_FORMAT` | `json` | `json` or `console` |
