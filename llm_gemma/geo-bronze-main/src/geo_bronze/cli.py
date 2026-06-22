"""CLI for geo-bronze using Typer."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Optional

import typer

app = typer.Typer(
    name="geo-bronze",
    help="Multi-agent geospatial data collection into the bronze layer.",
    no_args_is_help=True,
)

sources_app = typer.Typer(help="Manage data sources.")
bronze_app = typer.Typer(help="Inspect bronze storage.")
app.add_typer(sources_app, name="sources")
app.add_typer(bronze_app, name="bronze")


@app.command("init")
def init() -> None:
    """Check MinIO connectivity and create the bronze bucket if needed."""
    from geo_bronze.config import get_settings
    from geo_bronze.storage.bronze import BronzeWriter

    settings = get_settings()
    writer = BronzeWriter(settings)
    writer.ensure_bucket()
    typer.echo(f"Bronze bucket '{settings.minio_bucket}' is ready at {settings.minio_endpoint}.")


@sources_app.command("list")
def sources_list(
    all_sources: bool = typer.Option(False, "--all", help="Include disabled sources."),
) -> None:
    """List registered data sources."""
    from geo_bronze.registry.registry import SourceRegistry

    registry = SourceRegistry()
    sources = registry.all_sources(include_disabled=all_sources)
    for s in sources:
        status = "✓" if s.enabled else "✗"
        typer.echo(f"  [{status}] {s.source_id} ({s.protocol_family}) — {s.name}")
    typer.echo(f"\nTotal: {len(sources)} sources")


@sources_app.command("test")
def sources_test(source_id: str = typer.Argument(..., help="Source ID to test.")) -> None:
    """Test connectivity to a specific source."""
    import httpx

    from geo_bronze.registry.registry import SourceRegistry

    registry = SourceRegistry()
    try:
        source = registry.get(source_id)
    except Exception as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1)

    endpoint = source.endpoint
    if "{subdomain}" in endpoint:
        subdomains = source.metadata.get("subdomains", ["www"])
        endpoint = endpoint.replace("{subdomain}", subdomains[0])

    try:
        resp = httpx.get(endpoint, timeout=10.0, follow_redirects=True)
        typer.echo(f"[OK] {source_id}: HTTP {resp.status_code} ({endpoint})")
    except Exception as exc:
        typer.echo(f"[FAIL] {source_id}: {exc}", err=True)
        raise typer.Exit(1)


@app.command("run")
def run(
    task_file: Optional[Path] = typer.Option(None, "--task-file", help="Path to task YAML file."),
    aoi: Optional[str] = typer.Option(None, "--aoi", help="Bounding box: 'minx,miny,maxx,maxy'"),
    entity_types: Optional[str] = typer.Option(None, "--entity-types", help="Comma-separated entity types."),
    area_name: Optional[str] = typer.Option(None, "--area-name", help="Named area (e.g. 'Chernihiv region')."),
) -> None:
    """Run a data collection task."""
    from geo_bronze.models.task import AOI, CollectionTask
    from geo_bronze.scheduler.runner import run_task, run_task_from_file

    async def _run() -> None:
        if task_file:
            report = await run_task_from_file(task_file)
        else:
            if not entity_types:
                typer.echo("Error: --entity-types required when not using --task-file", err=True)
                raise typer.Exit(1)

            if aoi:
                coords = [float(x) for x in aoi.split(",")]
                aoi_obj = AOI(type="bbox", bbox=coords)
            elif area_name:
                aoi_obj = AOI(type="named", name=area_name)
            else:
                typer.echo("Error: --aoi or --area-name required", err=True)
                raise typer.Exit(1)

            task = CollectionTask(
                entity_types=[e.strip() for e in entity_types.split(",")],
                aoi=aoi_obj,
            )
            report = await run_task(task)

        typer.echo(f"\nTask {report.task_id} complete:")
        typer.echo(f"  Subtasks: {report.subtasks_success}/{report.subtasks_total} succeeded")
        if report.errors:
            typer.echo(f"  Errors ({len(report.errors)}):")
            for e in report.errors[:5]:
                typer.echo(f"    - {e}")
        if report.written_keys:
            typer.echo(f"  Written {len(report.written_keys)} objects to bronze")

    asyncio.run(_run())


@bronze_app.command("list")
def bronze_list(
    source_id: Optional[str] = typer.Option(None, "--source-id", help="Filter by source ID."),
    date: Optional[str] = typer.Option(None, "--date", help="Filter by date (YYYY-MM-DD)."),
) -> None:
    """List objects in bronze storage."""
    from geo_bronze.config import get_settings
    from minio import Minio

    settings = get_settings()
    client = Minio(
        endpoint=settings.minio_endpoint,
        access_key=settings.minio_access_key,
        secret_key=settings.minio_secret_key,
        secure=settings.minio_secure,
    )

    prefix = ""
    if source_id:
        prefix = source_id + "/"
        if date:
            date_path = date.replace("-", "/")
            prefix += date_path + "/"

    try:
        objects = list(client.list_objects(settings.minio_bucket, prefix=prefix, recursive=True))
        if not objects:
            typer.echo("No objects found.")
            return
        for obj in objects:
            size = f"{obj.size:,}" if obj.size else "?"
            typer.echo(f"  {obj.object_name}  ({size} bytes)")
        typer.echo(f"\nTotal: {len(objects)} objects")
    except Exception as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1)


@bronze_app.command("inspect")
def bronze_inspect(key: str = typer.Argument(..., help="Object key to inspect (sidecar).")) -> None:
    """Show sidecar metadata for a bronze object."""
    from geo_bronze.config import get_settings
    from minio import Minio

    settings = get_settings()
    client = Minio(
        endpoint=settings.minio_endpoint,
        access_key=settings.minio_access_key,
        secret_key=settings.minio_secret_key,
        secure=settings.minio_secure,
    )

    sidecar_key = key if key.endswith(".sidecar.json") else key.rsplit(".", 1)[0] + ".sidecar.json"
    try:
        response = client.get_object(settings.minio_bucket, sidecar_key)
        data = json.loads(response.read())
        response.close()
        typer.echo(json.dumps(data, indent=2, ensure_ascii=False))
    except Exception as exc:
        typer.echo(f"Error reading sidecar '{sidecar_key}': {exc}", err=True)
        raise typer.Exit(1)


if __name__ == "__main__":
    run(Path("examples/tasks/dsns_mine_chernihiv.yaml"))
