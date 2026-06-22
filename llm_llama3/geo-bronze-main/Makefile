.PHONY: install format lint typecheck test test-cov up up-llm down clean run-example

install:
	pip install -e ".[dev]"

format:
	ruff format src tests
	ruff check --fix src tests

lint:
	ruff check src tests

typecheck:
	mypy src/geo_bronze

test:
	pytest tests/ -v

test-cov:
	pytest tests/ -v --cov=geo_bronze --cov-report=term-missing --cov-report=html

up:
    minio server D:\універ\gis\bronze
	docker compose up minio -d

up-llm:
	docker compose --profile local-llm up -d

down:
	docker compose down

clean:
	find . -type f -name "*.pyc" -delete
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	rm -rf .pytest_cache .mypy_cache htmlcov .coverage

run-example:
	python -m geo_bronze run --task-file examples/tasks/osm_landfills_chernihiv.yaml
