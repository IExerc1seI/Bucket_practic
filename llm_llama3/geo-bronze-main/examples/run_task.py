"""Example: programmatic invocation of a collection task."""
import asyncio
from pathlib import Path

from geo_bronze.scheduler.runner import run_task_from_file


async def main() -> None:
    task_file = Path(__file__).parent / "tasks" / "osm_landfills_chernihiv.yaml"
    report = await run_task_from_file(task_file)
    print(f"Task {report.task_id}: {report.subtasks_success}/{report.subtasks_total} succeeded")
    if report.errors:
        for err in report.errors:
            print(f"  ERROR: {err}")


if __name__ == "__main__":
    asyncio.run(main())
