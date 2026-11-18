"""真实 PostgreSQL 16 验证。缺连接时显式跳过，不用 SQLite 冒充。"""

import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config as AlembicConfig
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from buildsize.api.app import create_app
from buildsize.auth.service import create_user, issue_token
from buildsize.config import Config
from buildsize.db.models import Build, Membership, Project


def test_postgresql_migration_and_unique_race(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    database_url = os.getenv("BUILDSIZE_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("NOT_VERIFIED：未配置 PostgreSQL 16 测试连接")
    schema = "bsi_test_" + uuid.uuid4().hex
    administrative = create_engine(database_url, hide_parameters=True)
    with administrative.begin() as connection:
        version = int(connection.scalar(text("SHOW server_version_num")))
        assert 160000 <= version < 170000, "此合同测试要求 PostgreSQL 16"
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    url = make_url(database_url).update_query_dict({"options": f"-csearch_path={schema}"})
    scoped_url = url.render_as_string(hide_password=False)
    engine = create_engine(scoped_url, hide_parameters=True)
    try:
        monkeypatch.setenv("DATABASE_URL", scoped_url)
        alembic = AlembicConfig(str(root / "backend/alembic.ini"))
        command.upgrade(alembic, "head")
        with Session(engine) as db:
            project = Project(key="race-test", name="隔离测试项目")
            db.add(project)
            db.commit()
            project_id = project.id
            user = create_user(db, "pg-user", "postgres-fixture-only-password")
            db.add(Membership(user_id=user.id, project_id=project_id, role="viewer"))
            config = Config(scoped_url, "http://testserver", "s" * 48, "p" * 48, app_env="test")
            _, raw_token = issue_token(db, config, project, ["builds:read"], 3600)
            db.commit()

        with TestClient(create_app(config, engine)) as client:
            assert client.get("/api/v1/health/ready").status_code == 200
            login = client.post(
                "/api/v1/auth/login",
                json={"username": "pg-user", "password": "postgres-fixture-only-password"},
                headers={"origin": config.origin},
            )
            assert login.status_code == 200
            assert client.get("/api/v1/projects/race-test").status_code == 200
            assert client.get("/api/v1/projects/other-project").status_code == 404
            assert (
                client.post(
                    "/api/v1/auth/logout",
                    headers={"origin": config.origin, "x-csrf-token": login.json()["csrf_token"]},
                ).status_code
                == 200
            )
            assert (
                client.get(
                    "/api/v1/projects/race-test", headers={"authorization": "Bearer " + raw_token}
                ).status_code
                == 200
            )

        def insert() -> str:
            try:
                with Session(engine) as db:
                    db.add(
                        Build(
                            project_id=project_id,
                            run_id=uuid.uuid4(),
                            ci_instance_id="ci",
                            ci_job_full_name="job",
                            ci_build_number="1",
                            variant="release",
                            manifest_sha256="a" * 64,
                            manifest={"中文": "值"},
                        )
                    )
                    db.commit()
                return "created"
            except IntegrityError:
                return "conflict"

        with ThreadPoolExecutor(max_workers=2) as executor:
            assert sorted(executor.map(lambda _: insert(), range(2))) == ["conflict", "created"]
        with Session(engine) as db:
            row = db.scalar(select(Build))
            assert row.manifest == {"中文": "值"}
        command.downgrade(alembic, "base")
        command.upgrade(alembic, "head")
    finally:
        engine.dispose()
        with administrative.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        administrative.dispose()
