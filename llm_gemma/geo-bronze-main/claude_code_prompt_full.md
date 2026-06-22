# Промпт для Claude Code — MVP агентної системи збору геоданих

> Скопіюй увесь блок нижче (від `# Завдання` до самого кінця) у перше повідомлення Claude Code у новій порожній директорії. Це повне технічне завдання — від нього не потрібен попередній контекст.

---

# Завдання

Створи MVP проєкту **`geo-bronze`** — мультиагентної системи автоматизованого збору відкритих геопросторових даних у bronze-шар медальйонного lakehouse. Це реалізація науково-дослідної роботи. Архітектура зафіксована у цьому ТЗ — слідуй їй точно, не імпровізуй з абстракціями.

# Контекст і мотивація

**Проблема:** відкриті геодані продукуються сотнями джерел, кожне зі своїм протоколом (WMS/WFS, ArcGIS REST, XYZ-тайли, довільні REST/файли). Класичний підхід «один ETL-адаптер на джерело» не масштабується: зміна схеми ламає адаптер, нове джерело = новий код, одна відмова стопить весь конвеєр.

**Рішення:** агенти, які маршрутизуються **за родиною протоколів, а не за конкретним джерелом**. Це ключове проєктне рішення — закладай його в код архітектурно.

**Медальйонна архітектура:** bronze (сирі байти + метадані, незмінний архів) → silver (декодування у геометрії, єдиний SRID) → gold (агрегати). **MVP покриває тільки bronze.** Декодування у геометрії свідомо НЕ робимо на bronze — це порушить незмінність архіву.

# Технологічний стек (фіксований)

- **Мова:** Python 3.11+
- **Менеджер пакетів:** `uv` (швидко, сучасно). Якщо `uv` недоступний — fallback на `pip` + `pyproject.toml`.
- **Bronze storage:** MinIO у Docker (S3-сумісний). Клієнт — `minio-py` (простіший за `boto3`).
- **HTTP-клієнт:** `httpx` (async) — потрібен для паралельних викликів у Tile-агенті та стрімінгових завантажень.
- **Конфіги:** Pydantic v2 + `pydantic-settings` для змінних оточення.
- **Логування:** `structlog` зі структурованим JSON-виводом.
- **LLM:** через єдиний абстрактний інтерфейс із двома реалізаціями — Claude API та локальна модель через Ollama.
- **CLI:** `typer`.
- **Тести:** `pytest` + `pytest-asyncio` + `respx` для мокання HTTP + `moto` для мокування S3.
- **Лінтер/формат:** `ruff` (обʼєднує lint + format, нічого більше не потрібно).
- **Оркестрація:** `docker-compose` для MinIO + Ollama (опціонально) + сама апка як окремий сервіс.

**Не використовуй:** LangChain, LlamaIndex, CrewAI, AutoGen — це університетський дослідницький код, нам потрібна прозора реалізація без чорних скриньок. Усі агенти пишемо власноруч на чистих класах.

# Структура репозиторію

```
geo-bronze/
├── pyproject.toml
├── README.md
├── .env.example
├── .gitignore
├── docker-compose.yml
├── Dockerfile
├── Makefile
├── src/
│   └── geo_bronze/
│       ├── __init__.py
│       ├── __main__.py            # точка входу CLI
│       ├── config.py              # Pydantic Settings
│       ├── models/                # Pydantic моделі домена
│       │   ├── __init__.py
│       │   ├── task.py            # CollectionTask, Subtask, AOI, TimeWindow
│       │   ├── source.py          # Source, ProtocolFamily, AuthConfig
│       │   ├── response.py        # CollectorResponse, ValidationResult
│       │   └── sidecar.py         # BronzeSidecar, TileRecord
│       ├── decoders/
│       │   ├── __init__.py
│       │   └── hints.py           # enum DecoderHint — контракт з silver
│       ├── agents/
│       │   ├── __init__.py
│       │   ├── base.py            # BaseAgent — спільний інтерфейс
│       │   ├── manager.py         # ManagerAgent
│       │   ├── validation.py      # ValidationAgent
│       │   └── collectors/
│       │       ├── __init__.py
│       │       ├── base.py        # BaseCollector
│       │       ├── ogc.py         # OGCCollector
│       │       ├── arcgis.py      # ArcGISCollector
│       │       ├── tile.py        # TileCollector
│       │       └── http.py        # HTTPCollector
│       ├── llm/                   # LLM-абстракція
│       │   ├── __init__.py
│       │   ├── base.py            # LLMClient — Protocol
│       │   ├── anthropic_client.py
│       │   ├── ollama_client.py
│       │   └── prompts.py         # Системні промпти агентів
│       ├── registry/
│       │   ├── __init__.py
│       │   ├── registry.py        # SourceRegistry
│       │   └── sources.yaml       # початковий каталог джерел (див. розділ нижче)
│       ├── storage/
│       │   ├── __init__.py
│       │   └── bronze.py          # BronzeWriter (MinIO) — звичайний + стрімінговий upload
│       ├── scheduler/
│       │   ├── __init__.py
│       │   └── runner.py          # запуск collection tasks
│       ├── errors.py              # власні exception-класи
│       └── cli.py                 # команди Typer
├── tests/
│   ├── __init__.py
│   ├── conftest.py                # фікстури (мок MinIO, мок LLM)
│   ├── unit/
│   │   ├── test_manager.py
│   │   ├── test_validation.py
│   │   ├── test_collectors_ogc.py
│   │   ├── test_collectors_arcgis.py
│   │   ├── test_collectors_tile.py
│   │   ├── test_collectors_http.py
│   │   ├── test_bronze_writer.py
│   │   ├── test_sidecar.py
│   │   └── test_registry.py
│   ├── integration/
│   │   └── test_end_to_end.py     # повний прохід з мок-сервером
│   └── fixtures/
│       ├── wfs_capabilities.xml
│       ├── wfs_getfeature.gml
│       ├── arcgis_query.json
│       ├── tile.png
│       ├── tile.pbf
│       ├── overpass_response.json
│       ├── dsns_locations.json
│       └── geojson_sample.json
└── examples/
    ├── run_task.py                # приклад програмного виклику
    └── tasks/
        ├── dsns_mine_chernihiv.yaml
        ├── osm_landfills_chernihiv.yaml
        ├── meteo_stations_country.yaml
        └── osm_full_dump_streaming.yaml
```

# Доменна модель (Pydantic v2)

## `CollectionTask`
Високорівнева задача збору:
- `task_id: str` (UUID)
- `aoi: AOI` — область інтересу: bbox `[minx, miny, maxx, maxy]` у EPSG:4326 АБО GeoJSON Polygon, АБО named area `{"type": "named", "name": "Чернігівська область"}`
- `time_window: TimeWindow` — `start: datetime`, `end: datetime` (опційно)
- `entity_types: list[str]` — наприклад `["osm_landfills", "mine_danger_zones"]`
- `params: dict[str, Any] = {}` — довільні параметри, що можуть знадобитись Manager-агенту (наприклад, city для weather API)
- `created_at: datetime`

## `Subtask`
Результат декомпозиції задачі — одна підзадача = один виклик до одного джерела:
- `subtask_id: str`
- `parent_task_id: str`
- `source_id: str` — посилання на запис у реєстрі
- `protocol_family: Literal["ogc", "arcgis", "tile", "http"]`
- `params: SubtaskParams` — типізовані параметри, специфічні для протоколу (див. далі)

## `SubtaskParams` (дискриминаційна модель за `protocol_family`)

**Для HTTP-родини:**
- `method: Literal["GET", "POST"] = "GET"`
- `path: str = ""` — додається до `source.endpoint`; може бути порожнім, якщо endpoint вже повний
- `query_params: dict[str, str] = {}`
- `headers: dict[str, str] = {}`
- `body: str | bytes | dict | None = None` — для POST. Якщо `dict` — серіалізується як JSON; якщо `str` — `Content-Type: text/plain`; якщо `bytes` — сирий.
- `body_template_vars: dict[str, str] | None = None` — підстановка у `source.metadata.body_template`
- `force_streaming: bool = False` — примусово стрімінговий upload (інакше визначається порогом)

**Для Tile-родини:**
- `zoom: int` — рівень масштабування для збору
- `bbox: tuple[float, float, float, float]` — фрагмент карти у EPSG:4326

**Для ArcGIS-родини:**
- `service_path: str` — наприклад `/FeatureServer/0`
- `where: str = "1=1"`
- `geometry: tuple[float, float, float, float] | None = None` — bbox
- `out_fields: str = "*"`
- `out_sr: int = 4326`

**Для OGC-родини:**
- `service: Literal["WFS", "WMS", "WCS", "CSW"]`
- `version: str = "2.0.0"`
- `operation: str` — наприклад `GetFeature`
- `type_name: str | None = None`
- `bbox: tuple[float, float, float, float] | None = None`
- `output_format: str = "application/json"`

## `Source` (запис реєстру)
- `source_id: str`
- `name: str`
- `protocol_family: Literal["ogc", "arcgis", "tile", "http"]`
- `endpoint: str` — base URL
- `auth: AuthConfig | None`
- `entity_types: list[str]` — які сутності можна отримати з цього джерела
- `spatial_coverage: dict | None` — `{"type": "country", "code": "UA"}` або `{"type": "global"}` або GeoJSON
- `license: str`
- `enabled: bool = True` — дозволяє вимкнути джерело без видалення з YAML
- `metadata: dict` — специфіка джерела (структура залежить від protocol_family, див. далі)

## `CollectorResponse`
- `subtask_id: str`
- `success: bool`
- `raw_bytes: bytes | None` — для звичайного режиму. Для стрімінгового — `None`, тут дані вже в bronze.
- `streamed_to_key: str | None` — для стрімінгового: ключ обʼєкта в bronze
- `content_type: str` (MIME)
- `content_length: int`
- `content_hash: str` — SHA-256 hex
- `source_metadata: dict` — оригінальний URL, заголовки, статус-код
- `decoder_hint: DecoderHint` — enum, контракт з silver
- `extras: dict = {}` — для tile-родини тут список `TileRecord`, для інших — порожньо
- `error: str | None`

## `BronzeSidecar`
JSON-файл, що пишеться поруч із сирими байтами. Поля:
- `source_id: str`
- `request_url: str`
- `request_method: str`
- `request_params: dict`
- `response_status: int`
- `response_headers: dict[str, str]`
- `timestamp: datetime` (UTC, ISO 8601)
- `content_hash: str` (SHA-256 hex)
- `content_type: str`
- `content_length: int`
- `license: str`
- `aoi: dict` (GeoJSON або bbox)
- `agent_version: str`
- `decoder_hint: str` (значення enum DecoderHint)
- `task_id: str`
- `subtask_id: str`
- `requires_manual_georeferencing: bool = False` — для PNG-карт без георефенсу
- `bbox_override: list[float] | None = None` — заздалегідь відома bbox `[minx, miny, maxx, maxy]` у EPSG:4326, прописана в реєстрі
- `source_axis_order: str | None = None` — для tile-джерел: `"zxy"` або `"zyx"`
- `tiles: list[TileRecord] | None = None` — для tile-наборів

## `TileRecord`
```python
class TileRecord(BaseModel):
    z: int
    x: int
    y: int
    content_hash: str
    size_bytes: int
    s3_key: str  # повний ключ обʼєкта у bronze
```

## `DecoderHint` (enum, обовʼязкові значення)

Це **контракт із silver-шаром** — змінювати тільки додаванням, не редагуванням існуючих:

```python
class DecoderHint(str, Enum):
    # GeoJSON / JSON-схожі
    GEOJSON = "geojson"
    JSON_GENERIC = "json-generic"
    JSON_POINTS = "json-points"          # JSON з полями lat/lon верхнього рівня
    JSON_WEATHER_FMI = "json-weather-fmi"  # JSON метеопрогнозу з dataTabs.latlon
    JS_WRAPPED_JSON = "js-wrapped-json"  # JS-файл з `const X = {...};`
    OVERPASS_JSON = "overpass-json"      # JSON-відповідь Overpass API

    # OGC / ArcGIS
    GML = "gml"
    ESRI_JSON = "esri-json"
    ESRI_JSON_JSONL = "esri-json-jsonl"  # пагінований склеєний JSONL

    # Тайли
    XYZ_RASTER_TILE = "xyz-raster-tile"  # PNG/JPG XYZ-тайли
    MVT = "mvt"                          # Mapbox Vector Tiles / PBF

    # Растри з геоприв'язкою або без
    GEOTIFF = "geotiff"
    PNG_WITH_BBOX_METADATA = "png-with-bbox-metadata"
    PNG_GEOREFERENCED_UNKNOWN = "png-georeferenced-unknown"

    # Файлові формати
    SHAPEFILE_ZIP = "shapefile-zip"
    OSM_PBF = "osm-pbf"
    CSV_LATLON = "csv-latlon"
    CSV_GENERIC = "csv-generic"
```

Кожне значення задокументоване docstring-коментарем в Enum. **Жодних сирих рядків `"geojson"` у коді** — тільки `DecoderHint.GEOJSON`.

# Архітектура агентів

## `BaseAgent` (інтерфейс)
Абстрактний клас. Кожен агент має:
- `name: ClassVar[str]`
- `version: ClassVar[str]`
- `async def run(self, *args, **kwargs) -> Any` — головний метод
- доступ до `LLMClient` через DI у конструкторі
- доступ до `structlog.BoundLogger` з контекстом імені агента

## `ManagerAgent`

**Відповідальність:** декомпозиція `CollectionTask` → список `Subtask`, маршрутизація, оркестрація.

**Логіка:**
1. Отримує `CollectionTask`.
2. Запитує `SourceRegistry.find_sources(entity_types, aoi)` — повертає кандидатів (фільтрованих за `enabled=True`).
3. **Виклик LLM:** надсилає список джерел (з їх metadata) + опис задачі і просить LLM вирішити, які джерела використати та які параметри запиту сформулювати. Це задача класифікації + параметризації.
4. Парсить структуровану відповідь LLM у `list[Subtask]`.
5. Маршрутизує кожну `Subtask` до відповідного `Collector` за `protocol_family` через `dict[ProtocolFamily, BaseCollector]` (НЕ if-elif!). Це і є «маршрутизація за протоколом».
6. Очікує всі результати (`asyncio.gather`, з `return_exceptions=True`).
7. Передає кожен успішний `CollectorResponse` у `ValidationAgent`.
8. Валідні відповіді → `BronzeWriter`.
9. Повертає звіт виконання задачі.

**DI:** Manager отримує `dict[ProtocolFamily, BaseCollector]` у конструкторі. Додавання нового колектора — це додавання запису в цей dict при ініціалізації застосунку.

## Колектори (4 штуки)

**`BaseCollector`:**
- `protocol_family: ClassVar[ProtocolFamily]`
- `async def collect(self, subtask: Subtask, source: Source) -> CollectorResponse`
- Спільні утиліти: retry з backoff (3 спроби, експоненційний), таймаути (30 с дефолт), логування

### `OGCCollector` (`protocol_family = "ogc"`)

Обробляє WMS, WFS, WCS, CSW. **MVP-обсяг — тільки WFS GetFeature** з GML або GeoJSON output. WMS/WCS/CSW залишити як `NotImplementedError` зі зрозумілим повідомленням, але `protocol_family` визначеним — щоб маршрутизація працювала. `decoder_hint` визначається з `Content-Type` відповіді:
- `application/json` → `DecoderHint.GEOJSON`
- `application/gml+xml` або `text/xml` → `DecoderHint.GML`

### `ArcGISCollector` (`protocol_family = "arcgis"`)

Обробляє ArcGIS FeatureServer/MapServer `query` endpoint. **Обовʼязкова автоматична пагінація** через `resultOffset` та `resultRecordCount` (стандартний ліміт 1000). Зливає всі сторінки в один JSONL (`DecoderHint.ESRI_JSON_JSONL`). Параметри з `SubtaskParams`: `where`, `geometry`, `out_fields`, `out_sr`.

Алгоритм пагінації:
1. Запит з `resultOffset=0`, `resultRecordCount=1000`.
2. У відповіді читає `exceededTransferLimit` або кількість features.
3. Якщо є ще — `resultOffset += 1000`, повторити.
4. Захист: ліміт 100 сторінок (100k записів) на одну підзадачу, інакше — попередження і завершення.

### `TileCollector` (`protocol_family = "tile"`)

Обробляє XYZ-растри та Mapbox Vector Tiles.

**Конфігурація з `source.metadata`:**
- `tile_url_template: str` — обовʼязковий. Плейсхолдери: `{endpoint}`, `{z}`, `{x}`, `{y}`, опційно `{subdomain}`, `{api_key}`.
- `tile_format: Literal["png", "jpg", "webp", "mvt", "pbf"]` — обовʼязковий.
- `zoom_range: tuple[int, int]` — обовʼязковий.
- `projection: str` — за замовчуванням `"EPSG:3857"`.
- `subdomains: list[str] | None` — для round-robin (Visicom має `tms1/tms2/tms3`).
- `api_key_env: str | None` — назва env-змінної з API-ключем.
- `axis_order: Literal["zxy", "zyx"] = "zxy"` — порядок осей у шаблоні.

**Логіка:**
1. На старті валідація консистентності: якщо `axis_order == "zyx"`, шаблон має містити `{z}/{y}/{x}` (саме в цьому порядку). Інакше — `ConfigurationError` ще до спроби збору.
2. З bbox і zoom обчислити XYZ-сітку (Web Mercator формули — реалізуй власноруч, не тягни залежність). Захист: не більше 1000 тайлів на одну підзадачу за замовчуванням, інакше — попередження.
3. Підставити `{api_key}` із `os.environ[source.metadata.api_key_env]` (якщо задано).
4. Для round-robin: при кожному запиті брати наступний `subdomain` із циклу `itertools.cycle(subdomains)`.
5. Завантажити тайли паралельно через `asyncio.Semaphore(concurrency=10)`.
6. Кожен тайл зберегти в bronze з нормалізованим ключем `{source_id}/{YYYY}/{MM}/{DD}/{task_id}/{z}/{x}/{y}.{ext}` — **завжди `{x}/{y}` у ключі**, незалежно від `axis_order` джерела. Це нормалізована форма для silver.
7. Маp `tile_format` → `decoder_hint`:
   - `png/jpg/webp` → `DecoderHint.XYZ_RASTER_TILE`
   - `mvt/pbf` → `DecoderHint.MVT`
8. Один сайдкар на весь набір тайлів задачі — у ньому масив `tiles: list[TileRecord]` і `source_axis_order` від оригіналу (щоб silver міг відтворити URL-и при потребі).

### `HTTPCollector` (`protocol_family = "http"`)

Найгнучкіший колектор. Підтримує GET і POST з тілом, шаблонізацію тіла з реєстру, стрімінгову передачу великих файлів.

**Конструювання запиту:**
1. URL = `source.endpoint` + `subtask.params.path` (з нормалізацією слешів).
2. Query params з `subtask.params.query_params`, об'єднані з `source.metadata.query_template` (subtask має пріоритет).
3. Headers — обʼєднання дефолтних і `subtask.params.headers`.
4. Body:
   - Якщо `subtask.params.body` задано — використати напряму. `dict` → JSON-серіалізація з `Content-Type: application/json`, `str` → `text/plain`, `bytes` → сирі.
   - Інакше, якщо `source.metadata.body_template` існує — взяти шаблон і виконати `template.format(**subtask.params.body_template_vars)`.
   - Інакше — без тіла.

**Гілка завантаження:**
- **Звичайна:** якщо `subtask.params.force_streaming = False` І `Content-Length` < `settings.streaming_threshold_bytes` (дефолт 50 MB). У пам'ять, потім `BronzeWriter.write()`.
- **Стрімінгова:** `force_streaming=True` АБО `Content-Length` >= порога АБО `Content-Length` невідомий і `streaming` стоїть у `source.metadata`. Алгоритм:
  1. `httpx.AsyncClient.stream("GET" | "POST", url, ...)`.
  2. `BronzeWriter.stream_start(key)` повертає `multipart_upload_id`.
  3. Читання чанків по `settings.streaming_chunk_size_bytes` (дефолт 5 MB).
  4. На кожен чанк — `BronzeWriter.stream_part(upload_id, part_num, chunk)`, паралельно `hashlib.sha256().update(chunk)`.
  5. По завершенні — `BronzeWriter.stream_complete(upload_id, parts)`. Сайдкар пишеться **після** успішного завершення з порахованим `content_hash` і `content_length`.
  6. При помилці — `BronzeWriter.stream_abort(upload_id)`, сайдкар НЕ пишеться, `CollectorResponse.success = False`.

**Визначення `decoder_hint`:**
1. `subtask.params` може містити явний `decoder_hint` — пріоритет №1.
2. `source.metadata.decoder_hint` для цього шляху/файлу — пріоритет №2.
3. Евристика з `Content-Type` + URL extension — пріоритет №3:
   - `application/json` + body містить `"FeatureCollection"` → `GEOJSON`
   - `application/json` + body містить `"elements"` і `"version"` → `OVERPASS_JSON`
   - `application/json` без специфіки → `JSON_GENERIC`
   - URL закінчується `.osm.pbf` → `OSM_PBF`
   - URL закінчується `.zip` + Content-Disposition натякає на shapefile → `SHAPEFILE_ZIP`
   - URL закінчується `.js` → `JS_WRAPPED_JSON`
   - `image/png` + bbox_override у metadata → `PNG_WITH_BBOX_METADATA`
   - `image/png` без bbox → `PNG_GEOREFERENCED_UNKNOWN`
   - Інакше → `JSON_GENERIC` для JSON, або помилка валідації

## `ValidationAgent`

**Без LLM** — три швидкі детерміновані перевірки:

1. **Формат:** перевір, чи `Content-Type` відповідає очікуваному для `decoder_hint`. Окрема пастка — HTML-сторінки помилок: перевір перші 512 байт на `<!DOCTYPE html>` або `<html`. Якщо очікувався не HTML, а отримано HTML — fail. **Для стрімінгових великих файлів** перевіряй тільки перші 512 байт (бо `raw_bytes` = `None`), читаючи їх з bronze через range-get.
2. **Просторовий охват:** для GeoJSON/Esri-JSON швидко прочитай `bbox` поля; для тайлів перевір, що координати в межах `[0, 2^z - 1]`. Якщо bbox явно поза AOI — попередження (не fail), записати в сайдкар як `warnings: ["bbox_outside_aoi"]`.
3. **Розмір:** `content_length > 100` (захист від порожніх); `content_length < 2 * 1024 * 1024 * 1024` (2 GB — захист від rogue endpoint).

Повертає `ValidationResult(passed: bool, checks: dict[str, CheckResult], warnings: list[str])`.

## `BronzeWriter` (сервіс, не агент)

Записує валідовану відповідь у MinIO. Має два режими — звичайний і стрімінговий.

**Структура ключів:**
```
s3://geo-bronze/
  {source_id}/
    {YYYY}/{MM}/{DD}/
      {task_id}/
        {subtask_id}.{ext}              # raw bytes
        {subtask_id}.sidecar.json       # метадані
```

Для тайлів — окремі ключі на кожен тайл (див. TileCollector).

**Звичайний режим:**
- `write(key: str, data: bytes, content_type: str) -> WriteResult` — кладе байти, повертає `etag`.
- `write_sidecar(key: str, sidecar: BronzeSidecar) -> None` — серіалізує модель у JSON і кладе.

**Стрімінговий режим:**
- `stream_start(key: str, content_type: str) -> str` — повертає `upload_id` від MinIO.
- `stream_part(upload_id: str, part_num: int, chunk: bytes) -> str` — повертає `etag` частини.
- `stream_complete(upload_id: str, parts: list[tuple[int, str]]) -> None` — фіналізує upload.
- `stream_abort(upload_id: str) -> None` — скасовує і прибирає part'и.

**Bucket автоматично створюється** при першому запуску, якщо не існує.

Сайдкар пишеться **окремим обʼєктом**, не в metadata MinIO (S3 metadata обмежена 2KB і незручна для запитів).

# LLM-інтеграція

## `LLMClient` (Protocol)

```python
from typing import Protocol

class LLMClient(Protocol):
    async def complete(
        self,
        system: str,
        user: str,
        response_schema: dict | None = None,  # JSON Schema для structured output
        temperature: float = 0.0,
        max_tokens: int = 4096,
    ) -> str: ...
```

## `AnthropicClient`

Через офіційний SDK `anthropic`. Модель — `claude-sonnet-4-5` (актуальна на час написання MVP; перевір документацію — якщо вийшла новіша, став її). API-ключ з `ANTHROPIC_API_KEY`. Structured output через інструктаж у system prompt видавати JSON у тегу `<output>...</output>` + парсинг (Anthropic може вже мати нативний JSON mode — перевір документацію і використовуй, якщо є).

## `OllamaClient`

Через HTTP API `localhost:11434` (стандартний порт Ollama). Модель за замовчуванням — `llama3.1:8b` (вистачає для задач класифікації джерел). Structured output через `format: "json"` в запиті. Назва моделі — зі `OLLAMA_MODEL`.

## Перемикання

У `config.py`:
```python
class Settings(BaseSettings):
    llm_provider: Literal["anthropic", "ollama"] = "ollama"
    anthropic_api_key: str | None = None
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "llama3.1:8b"
    anthropic_model: str = "claude-sonnet-4-5"
```

Фабрика `get_llm_client(settings) -> LLMClient` повертає відповідну реалізацію.

## Де реально потрібен LLM

- **`ManagerAgent.decompose_task`** — вибрати, які джерела з кандидатів використати, і сформувати параметри запиту. Особливо цінно для Overpass API: задача «landfills у Чернігівській області» → LLM формує Overpass-QL запит.
- **Розпізнавання нестандартних JSON-структур** (опціонально, stretch goal) — не обовʼязково для MVP.

**Де LLM НЕ потрібен:** валідація (детермінована), запис у bronze, пагінація ArcGIS, обчислення XYZ-сітки, евристика `decoder_hint` з Content-Type. Не пхай LLM туди, де достатньо коду — це і дешевше, і надійніше.

## Системний промпт `ManagerAgent`

У `llm/prompts.py` створи константу `MANAGER_SYSTEM_PROMPT`, яка пояснює LLM:

1. Його роль: декомпозувати `CollectionTask` на список `Subtask`-ів.
2. Які джерела доступні (передається в user message як JSON-список).
3. Що повертати: JSON у тегу `<output>` зі схемою `{"subtasks": [{...Subtask...}]}`.
4. **Спеціальне правило для Overpass:** якщо обрано джерело `osm-overpass` — переглянь поле `metadata.entity_to_tags`. Якщо `task.entity_types` містить ключ, який є в `entity_to_tags`, використай готовий запит з мапи. Якщо потрібного `entity_type` немає в мапі — згенеруй власний Overpass-запит, базуючись на знаннях про OSM-теги. У відповіді обовʼязково вкажи `body_template_vars` з полями `area_name` (з `task.aoi`) і `tags_query`.
5. Не вигадуй джерела, яких немає у списку. Не вигадуй `protocol_family` поза дозволеними.

Це **гібридний підхід:** відомі типи (landfills, brownfields, ruins) мають готові Overpass-запити в YAML; невідомі — генеруються LLM. YAML-мапа — перевірений безпечний шлях, LLM — fallback для нестандартних запитів.

# Реєстр джерел — `registry/sources.yaml`

Це **обовʼязкова частина MVP** — на цьому реєстрі будуть проганятись end-to-end тести. Точний вміст:

```yaml
# ============================================================
# Реєстр джерел geo-bronze
# Кожен запис — декларативний опис джерела. Жодного коду під
# конкретне джерело: усе керується через protocol_family і
# metadata. Цей файл — єдина точка істини для додавання джерел.
# ============================================================

sources:

  # ---------- ДСНС — Протимінна діяльність ----------

  - source_id: dsns-mine-rest
    name: ДСНС — REST API сервісу протимінної діяльності
    protocol_family: http
    endpoint: https://<DSNS_MINE_BASE>  # TODO: уточнити базовий URL після узгодження
    entity_types: [vnp_reports, vnp_dictionary, service_info]
    license: open-gov-data-ua
    enabled: true
    spatial_coverage: { type: country, code: UA }
    metadata:
      docs: "Три публічні ендпоінти без авторизації"
      endpoints:
        - { path: /api/info/,     entity: service_info,   decoder_hint: json-generic }
        - { path: /api/vnpInfo/,  entity: vnp_dictionary, decoder_hint: json-generic }
        - { path: /api/location/, entity: vnp_reports,    decoder_hint: json-points }
      pagination: none
      notes: "На /api/location/ повертається повний список (>68k записів) одним JSON. Очікувати великий payload."

  - source_id: dsns-mine-tiles
    name: ДСНС — VectorTileServer небезпечних територій
    protocol_family: tile
    endpoint: https://gisportal.fms.dsns.gov.ua/portal/sharing/servers/e528b9c9059a49b2812a647b5f7c06f4/rest/services/Hosted/MineWebOpenPart/VectorTileServer
    entity_types: [mine_danger_zones]
    license: open-gov-data-ua
    enabled: true
    spatial_coverage: { type: country, code: UA }
    metadata:
      tile_format: mvt
      tile_url_template: "{endpoint}/tile/{z}/{y}/{x}.pbf"
      axis_order: zyx  # УВАГА: інверсія осей відносно стандартного XYZ
      zoom_range: [0, 11]
      projection: EPSG:3857
      layers: [general_danger, confirmed_danger]
      notes: "ArcGIS VectorTileServer без авторизації. Шаблон URL використовує {z}/{y}/{x}."

  # ---------- Укргідрометцентр ----------

  - source_id: meteo-stations-js
    name: Укргідрометцентр — мережа станцій (JS-файли)
    protocol_family: http
    endpoint: https://www.meteo.gov.ua/ua
    entity_types: [hydro_posts, meteo_stations, radio_posts]
    license: open-gov-data-ua
    enabled: true
    spatial_coverage: { type: country, code: UA }
    metadata:
      files:
        - { path: /_hydro-posts.js,    entity: hydro_posts,    decoder_hint: js-wrapped-json }
        - { path: /_meteo-stations.js, entity: meteo_stations, decoder_hint: js-wrapped-json }
        - { path: /_radio-posts.js,    entity: radio_posts,    decoder_hint: js-wrapped-json }
      notes: "JS-константи `const X = {...};`. Сирі .js зберігаються в bronze як є; розпакування — на silver."

  - source_id: meteo-weather-api
    name: Укргідрометцентр — погодинний прогноз погоди
    protocol_family: http
    endpoint: https://www.meteo.gov.ua/fmi.json
    entity_types: [hourly_weather_forecast]
    license: open-gov-data-ua
    enabled: true
    spatial_coverage: { type: country, code: UA }
    metadata:
      query_template:
        action: getCityWeather
        lang: ua
      required_params: [city, region]
      decoder_hint: json-weather-fmi
      notes: "city і region — довільні назви, які LLM Manager-агента підбирає за task.aoi."

  - source_id: meteo-fire-rasters
    name: Укргідрометцентр — растрові карти пожежної небезпеки
    protocol_family: http
    endpoint: https://www.meteo.gov.ua
    entity_types: [fire_danger_raster]
    license: open-gov-data-ua
    enabled: true
    spatial_coverage: { type: country, code: UA }
    metadata:
      files:
        - { path: /f/fire/Fire_Current.png,       time_offset_days: 0, kind: actual }
        - { path: /f/fire/Fire_Forecast_now.png,  time_offset_days: 0, kind: forecast }
        - { path: /f/fire/Fire_Forecast_1.png,    time_offset_days: 1, kind: forecast }
        - { path: /f/fire/Fire_Forecast_2.png,    time_offset_days: 2, kind: forecast }
        - { path: /f/fire/Fire_Forecast_3.png,    time_offset_days: 3, kind: forecast }
      decoder_hint: png-with-bbox-metadata
      bbox_override: [22.137, 44.386, 40.220, 52.380]  # bbox України EPSG:4326
      requires_manual_georeferencing: true
      update_schedule: daily
      notes: |
        PNG без вбудованої геоприв'язки. bbox прописана статично — карти охоплюють всю Україну
        у фіксованій рамці. Silver-шар використовує bbox_override для перетворення в GeoTIFF.

  - source_id: visicom-basemap
    name: Visicom — базовий растровий шар (XYZ)
    protocol_family: tile
    endpoint: https://{subdomain}.visicom.ua/2.0.0/world,ua/base
    entity_types: [basemap_raster]
    license: visicom-tos
    enabled: false  # увімкнути після отримання VISICOM_API_KEY
    spatial_coverage: { type: country, code: UA }
    metadata:
      tile_format: png
      tile_url_template: "{endpoint}/{z}/{x}/{y}.png?key={api_key}"
      axis_order: zxy
      zoom_range: [0, 19]
      projection: EPSG:3857
      subdomains: [tms1, tms2, tms3]
      api_key_env: VISICOM_API_KEY
      notes: "Round-robin по трьох субдоменах. Ключ — з env VISICOM_API_KEY."

  # ---------- OpenStreetMap ----------

  - source_id: osm-overpass
    name: OpenStreetMap — Overpass API
    protocol_family: http
    endpoint: https://overpass-api.de/api/interpreter
    entity_types:
      - osm_landfills
      - osm_brownfields
      - osm_abandoned_buildings
      - osm_ruins
      - osm_military
      - osm_farmland
      - osm_commercial
      - osm_custom
    license: ODbL-1.0
    enabled: true
    spatial_coverage: { type: global }
    metadata:
      method: POST
      body_template: |
        [out:json][timeout:60];
        area["name"="{area_name}"]->.searchArea;
        ({tags_query});
        out geom;
      decoder_hint: overpass-json
      rate_limit_warning: "Публічний сервер часто перевантажений. Для продакшну рекомендовано локальне розгортання."
      entity_to_tags:
        osm_landfills: 'way["landuse"="landfill"](area.searchArea); relation["landuse"="landfill"](area.searchArea);'
        osm_brownfields: 'way["landuse"="brownfield"](area.searchArea);'
        osm_abandoned_buildings: 'way["building"="abandoned"](area.searchArea); way["abandoned"="yes"](area.searchArea);'
        osm_ruins: 'way["ruins"="yes"](area.searchArea); way["historic"="ruins"](area.searchArea);'
        osm_military: 'way["landuse"="military"](area.searchArea); relation["landuse"="military"](area.searchArea);'
        osm_farmland: 'way["landuse"="farmland"](area.searchArea); relation["landuse"="farmland"](area.searchArea);'
        osm_commercial: 'way["landuse"="commercial"](area.searchArea); relation["landuse"="commercial"](area.searchArea);'

  - source_id: osm-tiles-ua
    name: OpenStreetMap Ukraine — растрові тайли (osm-bright)
    protocol_family: tile
    endpoint: https://tile.openstreetmap.org.ua/styles/osm-bright
    entity_types: [basemap_raster]
    license: ODbL-1.0
    enabled: true
    spatial_coverage: { type: country, code: UA }
    metadata:
      tile_format: png
      tile_url_template: "{endpoint}/{z}/{x}/{y}.png"
      axis_order: zxy
      zoom_range: [0, 18]
      projection: EPSG:3857

  - source_id: osm-geofabrik-ukraine
    name: OpenStreetMap — Geofabrik bulk extracts (Україна)
    protocol_family: http
    endpoint: https://download.geofabrik.de/europe/ukraine
    entity_types: [osm_full_dump]
    license: ODbL-1.0
    enabled: true
    spatial_coverage: { type: country, code: UA }
    metadata:
      files:
        - { path: -latest.osm.pbf,      decoder_hint: osm-pbf,       kind: pbf }
        - { path: -latest-free.shp.zip, decoder_hint: shapefile-zip, kind: shapefile }
      streaming: true
      update_schedule: daily
      size_estimate_mb: 600
      notes: "Великі вивантаження. HTTPCollector передає стрімінгово в MinIO через multipart upload."
```

# CLI (`typer`)

```
geo-bronze init                          # перевіряє MinIO, створює bucket
geo-bronze sources list                  # показує реєстр (тільки enabled=true)
geo-bronze sources list --all            # включно з вимкненими
geo-bronze sources test <source_id>      # перевіряє доступність джерела
geo-bronze run --task-file task.yaml     # запускає одну collection task
geo-bronze run --aoi "30,50,31,51" --entity-types osm_landfills --area-name "Чернігівська область"
geo-bronze bronze list [--source-id X] [--date YYYY-MM-DD]  # перелік обʼєктів у bronze
geo-bronze bronze inspect <key>          # показує sidecar обʼєкта
```

# Конфігурація (`.env.example`)

```env
# ---------- LLM ----------
LLM_PROVIDER=ollama
ANTHROPIC_API_KEY=
ANTHROPIC_MODEL=claude-sonnet-4-5
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=llama3.1:8b

# ---------- MinIO ----------
MINIO_ENDPOINT=localhost:9000
MINIO_ACCESS_KEY=minioadmin
MINIO_SECRET_KEY=minioadmin
MINIO_BUCKET=geo-bronze
MINIO_SECURE=false

# ---------- Стрімінг великих файлів ----------
STREAMING_THRESHOLD_BYTES=52428800   # 50 MB — поріг переходу на streaming
STREAMING_CHUNK_SIZE_BYTES=5242880   # 5 MB — розмір multipart chunk'а

# ---------- API keys для джерел ----------
VISICOM_API_KEY=                 # ключ Visicom basemap (за бажанням, source enabled=false без нього)

# ---------- Logging ----------
LOG_LEVEL=INFO
LOG_FORMAT=json
```

# `docker-compose.yml`

Три сервіси:

1. **minio** — `minio/minio:latest`, порти 9000 (S3 API), 9001 (console), креди з env. Volume `minio-data` для персистентності.
2. **ollama** — `ollama/ollama:latest`, порт 11434, volume `ollama-data:/root/.ollama`. **Профіль `local-llm`**, щоб піднімалось тільки коли потрібно: `docker compose --profile local-llm up`.
3. **app** — наш Python-додаток, build з локального `Dockerfile`, залежить від minio, монтує `./examples/tasks` у `/tasks`. Профіль `app` (необовʼязково — для CLI зручніше запускати локально).

# Тести

Покриття — **щонайменше 70% за `pytest --cov`**. Конкретні мінімальні набори:

- **`test_manager.py`**: мок реєстру з кількома джерелами, мок LLM-клієнта повертає фіксований план — Manager викликає правильні Collectors з правильними параметрами. Окремий кейс з Overpass: задача «landfills у Чернігівській області», LLM мокається як такий, що повертає підзадачу з готовим `tags_query` із мапи `entity_to_tags`.
- **`test_collectors_ogc.py`**: `respx` мокає WFS GetFeature endpoint, повертає фіксований GML — `CollectorResponse` має `decoder_hint = DecoderHint.GML`.
- **`test_collectors_arcgis.py`**: `respx` мокає 3 сторінки пагінованої відповіді (по 1000 записів) — всі 3 завантажились і склеїлись у JSONL.
- **`test_collectors_tile.py`**: 
  - Стандартні XYZ-тайли — паралельне завантаження, правильні ключі.
  - **Тест із `axis_order: zyx`** — TileCollector коректно будує URL `{z}/{y}/{x}.pbf` для `dsns-mine-tiles`. Ключ у bronze залишається нормалізованим `{z}/{x}/{y}`.
  - **Тест із `subdomains` і `api_key_env`** — round-robin розподіл і коректна підстановка ключа.
  - **Тест на узгодженість `axis_order` із `tile_url_template`** — невідповідність кидає `ConfigurationError` на старті.
  - `tile_format: mvt` → `decoder_hint: MVT` у сайдкарі.
- **`test_collectors_http.py`**:
  - GET з GeoJSON URL → `decoder_hint = GEOJSON`.
  - ZIP з `.shp` → `SHAPEFILE_ZIP`.
  - **POST-запит з `body_template` і `body_template_vars`** (Overpass).
  - **Стрімінгове завантаження файлу > порога** через `moto` (мокаємо MinIO multipart upload). Перевір, що SHA-256 рахується інкрементально і збігається з референсним.
  - **Скасування стріму при помилці посередині** — `stream_abort` викликається, сайдкар НЕ пишеться.
  - `bbox_override` і `requires_manual_georeferencing` коректно потрапляють у сайдкар для пожежних PNG.
- **`test_validation.py`**: HTML disguised as PNG → fail; порожня відповідь → fail; коректний GeoJSON → pass; стрімінговий обʼєкт (raw_bytes=None) — валідація через range-get перших 512 байт.
- **`test_bronze_writer.py`**: `moto` для S3. Перевіряє ключову схему, вміст сайдкара, multipart upload (start/part/complete і start/part/abort).
- **`test_sidecar.py`**: всі обовʼязкові поля присутні, `content_hash` — коректний SHA-256, `timestamp` у UTC, нові поля (`requires_manual_georeferencing`, `bbox_override`, `source_axis_order`, `tiles`) серіалізуються правильно.
- **`test_registry.py`**:
  - Завантаження `sources.yaml` — усі 9 записів парсяться без помилок.
  - Валідація консистентності per protocol_family: для `tile` — обовʼязково `tile_url_template`, `tile_format`, `axis_order`. Для `http` — `endpoint` валідний URL.
  - `find_sources(entity_types=["osm_landfills"])` повертає тільки `osm-overpass`.
  - `find_sources(entity_types=["mine_danger_zones"])` повертає тільки `dsns-mine-tiles`.
  - `find_sources(...)` за замовчуванням повертає тільки `enabled=true` (Visicom фільтрується).
- **`test_end_to_end.py`**: піднімає `respx` для всіх HTTP-викликів, мокає LLM, мокає MinIO через `moto` — запускає `runner.run_task(...)` і перевіряє, що у bucket зʼявились очікувані обʼєкти + сайдкари.

Усі асинхронні тести через `pytest-asyncio` з `asyncio_mode = "auto"`.

# Makefile

Стандартні цілі: `install`, `format`, `lint`, `typecheck`, `test`, `test-cov`, `up` (docker compose up minio), `up-llm` (з профілем local-llm), `down`, `clean`, `run-example` (запускає `examples/tasks/osm_landfills_chernihiv.yaml`).

# Приклади задач

У `examples/tasks/` мають бути чотири YAML-файли, що демонструють end-to-end сценарії:

1. **`dsns_mine_chernihiv.yaml`** — мінні загрози для Чернігівщини. Активує `dsns-mine-rest` (JSON-точки) + `dsns-mine-tiles` (MVT-полігони). Демонструє роботу з `axis_order: zyx`.
2. **`osm_landfills_chernihiv.yaml`** — сміттєзвалища у Чернігівській області через Overpass. Демонструє LLM-формування `tags_query` із `entity_to_tags` мапи.
3. **`meteo_stations_country.yaml`** — мережа метеостанцій по всій Україні. Демонструє роботу з JS-обгорнутим JSON.
4. **`osm_full_dump_streaming.yaml`** — повний дамп Geofabrik. Демонструє стрімінгове завантаження великого файлу.

Кожен YAML — з детальним коментарем, що саме сценарій демонструє.

# README.md

Має містити:

1. Опис проєкту (1 абзац) + посилання на тези НДР.
2. Архітектурна діаграма (ASCII-art або mermaid).
3. **Швидкий старт:** 5–7 команд, після яких рядок «йде збір» зʼявиться в логах. Має працювати end-to-end без жодних API-ключів — використовуючи ДСНС, Укргідрометцентр (крім Visicom) та OSM.
4. Як перемкнути LLM між Claude API та Ollama.
5. Структура bronze-сховища.
6. **Як додати нове джерело:** це має бути 1 запис у YAML — без коду! Наведи приклад додавання реального WFS чи ArcGIS FeatureServer.
7. **Limitations:**
   - SVG-попередження метеоцентру не підтримуються (потребує веб-скрепінгу, поза протокол-орієнтованим збором).
   - Декодування PNG-карт пожежної небезпеки в GeoTIFF — робота silver-шару.
   - Локальне розгортання Overpass рекомендовано для продакшну; MVP використовує публічний сервер з rate limit.
   - OGC і ArcGIS колектори реалізовані, але в початковому реєстрі немає джерел цих родин — додайте за потребою.
8. Roadmap: silver-шар, PostGIS-завантажувач, gold-агрегати — за межами MVP.

# Порядок виконання роботи

Виконуй у цьому порядку, фіксуючи прогрес комітами:

1. **Скелет проєкту:** структура каталогів, `pyproject.toml`, `.env.example`, `.gitignore`, `Makefile`. Комміт.
2. **Доменні моделі** Pydantic (всі `models/*.py`) + `decoders/hints.py`. Юніт-тести валідації. Комміт.
3. **`BronzeWriter` + тести з `moto`** (звичайний + стрімінговий режим). Це фундамент. Комміт.
4. **LLM-абстракція** (інтерфейс + обидві реалізації). Smoke-тести. Комміт.
5. **`SourceRegistry` + повний `sources.yaml` з 9 джерел.** Тести парсингу і `find_sources`. Комміт.
6. **`HTTPCollector`** (найскладніший — стрімінг, POST, body_template). Тести. Комміт.
7. **`TileCollector`** (axis_order, subdomains, api_key, паралельність). Тести. Комміт.
8. **`OGCCollector`** (WFS GetFeature). Тести. Комміт.
9. **`ArcGISCollector`** з пагінацією. Тести. Комміт.
10. **`ValidationAgent`.** Тести. Комміт.
11. **`ManagerAgent`** з LLM-декомпозицією + системний промпт із `entity_to_tags`. Тести з мок-LLM. Комміт.
12. **`scheduler/runner.py`** — склеює все. Чотири `examples/tasks/*.yaml`. Комміт.
13. **CLI на Typer.** Комміт.
14. **`docker-compose.yml` + `Dockerfile`.** Перевір що `make up && make run-example` працює end-to-end. Комміт.
15. **End-to-end тест.** Комміт.
16. **README.md.** Фінальний комміт.

# Правила якості коду

- **Типи скрізь.** Будь-яка функція має повну типізацію. Перевіряй `mypy --strict` (додай у Makefile як `make typecheck`).
- **Docstrings** — формат Google, тільки для публічних класів і функцій.
- **Не дублюй код.** Спільні утиліти (retry, HTTP-клієнт з таймаутами, обчислення хешу) — в `utils/`.
- **Помилки — явні.** Власні exception-класи в `errors.py`: `CollectorError`, `ValidationError`, `BronzeWriteError`, `ConfigurationError`, `StreamingError`. Не лови `Exception` широко.
- **Логування на ключових точках:** старт/кінець кожного агента, успіх/невдача кожного collector, запис кожного обʼєкта в bronze, стрімінгові події. Через `structlog.bind()`.
- **Async-консистентність:** якщо щось async — все по ланцюжку async. Не змішуй sync і async без потреби.
- **Жодного «магічного» рядка.** Літерали типу `"application/json"`, `"geojson"` — у `DecoderHint` enum або константи модуля.

# Що НЕ робити в MVP

- Не реалізовуй silver/gold шари, навіть частково.
- Не пиши власний UI / веб-морду — тільки CLI.
- Не додавай моніторинг (Prometheus тощо).
- Не реалізовуй автентифікацію джерел складніше за Bearer token та basic auth.
- Не оптимізуй передчасно (не вводь кеш запитів, черги завдань, distributed execution).
- Не пиши adapter-pattern всередині collector — ти й так маєш чотири collectors, це і є адаптери.
- Не намагайся «розв'язати» SVG-попередження метеоцентру чи георефенс пожежних PNG — це явно поза межами MVP.

# Критерій готовності

MVP вважається готовим, коли:

1. `make install && make up && make run-example` (з дефолтним `.env`) проходить без помилок.
2. У MinIO console (http://localhost:9001) видно записані файли + сайдкари у правильній структурі.
3. Усі **чотири приклади задач** виконуються успішно (для `osm_full_dump_streaming` достатньо часткового завантаження з timeout — головне, щоб multipart upload працював коректно).
4. `make test` → всі тести зелені, покриття ≥ 70%.
5. `make lint && make typecheck` → без помилок.
6. README дозволяє новому розробнику запустити проєкт за 10 хвилин.

# Кінцева примітка

Це дослідницький MVP, але код має бути продакшн-якості — він стане основою подальшої НДР. Якщо в якомусь місці виникає вибір між «швидко й брудно» та «трохи довше, але чисто» — обирай чисто. Якщо архітектурне рішення в цьому ТЗ суперечить здоровому глузду в конкретному місці — спочатку запитай, перш ніж відхилитися. Якщо щось дрібне неоднозначне (наприклад, точна назва поля) — обери розумно і йди далі, але задокументуй вибір у коментарі.

Перед стартом сформулюй короткий план першого етапу (скелет + моделі + DecoderHint), покажи мені, і починай.
