# geo-bronze (Mistral)

A multi-agent geospatial data collection system that uses a **local LLM (Mistral via Ollama)** to decompose high-level collection tasks into protocol-specific subtasks, execute them in parallel, validate results, and store raw data with metadata in an S3-compatible storage (MinIO).

This project represents the **bronze layer** of a geospatial data lake: raw, unprocessed data preserved exactly as received, ready for further transformation (silver/gold layers).

## Architecture
CollectionTask (YAML or CLI)
│
ManagerAgent  ──── LLM (Mistral via Ollama)
│              
Task decomposition → Subtasks
┌─────┴──────────────────────┐
│  Collectors                │
│  ├─ HTTPCollector          │
│  ├─ TileCollector          │
│  ├─ ArcGISCollector        │
│  └─ OGCCollector (WFS)     │
└─────┬──────────────────────┘
│
ValidationAgent (format, size, spatial checks)
│
BronzeWriter ──► MinIO

## Key Components

- **ManagerAgent**  
  Uses a local LLM (Mistral) to:
  - select data sources
  - generate fully executable subtasks

- **ValidationAgent**  
  Performs deterministic checks:
  - JSON validity
  - HTML error detection
  - size limits
  - spatial consistency

- **Collectors**  
  Protocol adapters:
  - HTTP / REST
  - Tile (XYZ / MVT)
  - ArcGIS FeatureServer
  - OGC (WFS)

- **BronzeWriter**  
  Stores raw data and metadata in MinIO.

## Features

-Local LLM (no external APIs required)
-Parallel execution of data collection
-Validation layer for robustness
-Raw + metadata (sidecar JSON)
-Extensible via source registry

## Quick Start

### Prerequisites

- Python 3.11+
- Docker + Docker Compose
- https://ollama.com/ installed

### 1. Clone & install

git clone https://github.com/your-repository/name_file.git
cd name_file
pip install -e ".[dev]"

### 2. Setup environment

cp .env.example .env

### 3. Start services

make up

### 4. Pull Mistral model

ollama pull mistral

### 5. Initialize storage

geo-bronze init

### 6. Run a task

geo-bronze run --task-file link and name your file.yaml

### Storage Structure

geo-bronze/
└── {source_id}/
    └── {YYYY}/{MM}/{DD}/{task_id}/
        ├── data file
        └── .sidecar.json

Each .sidecar.json includes:

request params
response metadata
hash
AOI
decoder hint

### Limitations

Open-source LLMs (Mistral) may produce unstable output format
Requires prompt engineering for reliability
Some public APIs may return HTML instead of JSON
Streaming large files may require additional tuning
