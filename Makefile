.PHONY: install dev web build browser-test stack-build stack-up stack-stop stack-check benchmark db worker scheduler migrate test test-postgres test-postgres-container contracts lint typecheck e2e demo unity-test unity-validate

install:
	uv sync --project backend --frozen
	pnpm --dir frontend install --frozen-lockfile

dev:
	uv run --project backend --frozen --env-file .env uvicorn buildsize.api.app:create_app --factory --host 127.0.0.1 --port 8000 --no-access-log

web:
	pnpm --dir frontend dev

build:
	pnpm --dir frontend build

browser-test:
	uv run --project backend --frozen python tools/test_postgres_container.py --browser-only

stack-build:
	docker build -f deploy/Dockerfile.api -t buildsize-api:local .
	docker build -f deploy/Dockerfile.web -t buildsize-web:local .

stack-up:
	docker compose --env-file .env.deploy -p buildsize -f deploy/compose.yaml up -d --wait

stack-stop:
	docker compose --env-file .env.deploy -p buildsize -f deploy/compose.yaml stop web api worker scheduler

stack-check:
	uv run --project backend --frozen python tools/validate_stack.py

benchmark:
	uv run --project backend --frozen python tools/validate_stack.py --benchmark

db:
	docker compose --env-file .env up -d postgres redis

worker:
	uv run --project backend --frozen --env-file .env buildsize-worker

scheduler:
	uv run --project backend --frozen --env-file .env buildsize-dispatcher

migrate:
	uv run --project backend --frozen --env-file .env alembic -c backend/alembic.ini upgrade head

contracts:
	uv run --project backend --frozen python tools/validate_contracts.py

test: contracts
	mkdir -p .buildsize
	uv run --project backend --frozen pytest backend/tests --junitxml=.buildsize/test-results.xml

test-postgres:
	@test -n "$$BUILDSIZE_TEST_DATABASE_URL" || (echo '请设置专用 PostgreSQL 16 测试连接 BUILDSIZE_TEST_DATABASE_URL' >&2; exit 2)
	uv run --project backend --frozen pytest backend/tests/test_postgres.py backend/tests/test_pipeline.py -q

test-postgres-container:
	uv run --project backend --frozen python tools/test_postgres_container.py

lint:
	uv run --project backend --frozen ruff check --config backend/pyproject.toml backend integrations/jenkins/unity-report-handoff.py tools/test_postgres_container.py tools/validate_unity.py tools/stack_backup.py tools/validate_stack.py tools/benchmark_stack.py
	uv run --project backend --frozen ruff format --check --config backend/pyproject.toml backend integrations/jenkins/unity-report-handoff.py tools/test_postgres_container.py tools/validate_unity.py tools/stack_backup.py tools/validate_stack.py tools/benchmark_stack.py
	pnpm --dir frontend format:check

typecheck:
	uv run --project backend --frozen mypy --config-file backend/pyproject.toml backend/src/buildsize tools/test_postgres_container.py tools/validate_unity.py tools/stack_backup.py tools/validate_stack.py tools/benchmark_stack.py
	pnpm --dir frontend typecheck
	cd frontend && node scripts/generate-contracts.mjs --check

e2e:
	uv run --project backend --frozen python tools/test_postgres_container.py --e2e

demo:
	uv run --project backend --frozen buildsize demo --directory .buildsize/demo

unity-test:
	uv run --project backend --frozen python tools/validate_unity.py

unity-validate:
	uv run --project backend --frozen python tools/validate_unity.py --build
