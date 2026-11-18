import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config as AlembicConfig
from alembic.migration import MigrationContext
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from buildsize.api.app import create_app
from buildsize.auth.security import digest
from buildsize.auth.service import Principal, create_user, issue_token, project_access
from buildsize.config import Config
from buildsize.db.models import (
    AnalysisRevision,
    ApiToken,
    Base,
    Build,
    Membership,
    Project,
    WebSession,
)
from buildsize.db.session import make_engine
from buildsize.domain.errors import BuildSizeError

PASSWORD = "fixture-only-password-123"


@pytest.fixture
def foundation(tmp_path: Path, root: Path, monkeypatch: pytest.MonkeyPatch):
    config = Config(
        f"sqlite+pysqlite:///{tmp_path / 'test.sqlite3'}",
        "http://testserver",
        "s" * 48,
        "p" * 48,
        app_env="test",
    )
    monkeypatch.setenv("DATABASE_URL", config.database_url)
    alembic = AlembicConfig(str(root / "backend/alembic.ini"))
    command.upgrade(alembic, "head")
    engine = make_engine(config)
    with Session(engine) as db:
        first = Project(key="game-one", name="项目一")
        second = Project(key="game-two", name="项目二")
        db.add_all([first, second])
        user = create_user(db, "tester", PASSWORD)
        db.flush()
        db.add(Membership(user_id=user.id, project_id=first.id, role="viewer"))
        token, raw = issue_token(db, config, first, ["builds:read", "builds:submit"], 3600)
        db.commit()
        ids = {"project": first.id, "other": second.id, "user": user.id, "token": token.id}
    client = TestClient(create_app(config, engine))
    with client:
        yield config, engine, client, raw, ids, alembic
    engine.dispose()


def login(client: TestClient):
    return client.post(
        "/api/v1/auth/login",
        json={"username": "tester", "password": PASSWORD},
        headers={"origin": "http://testserver"},
    )


def test_migration_roundtrip_schema_and_health(foundation) -> None:
    config, engine, client, _, _, alembic = foundation
    assert client.get("/api/v1/health/live").json() == {"status": "live"}
    assert client.get("/api/v1/health/ready").json() == {"status": "ready"}
    with engine.connect() as connection:
        assert compare_metadata(MigrationContext.configure(connection), Base.metadata) == []
    command.downgrade(alembic, "base")
    assert client.get("/api/v1/health/ready").status_code == 503
    command.upgrade(alembic, "head")
    assert client.get("/api/v1/health/ready").status_code == 200


def test_cookie_csrf_and_project_permissions(foundation) -> None:
    config, engine, client, _, ids, _ = foundation
    assert client.get("/api/v1/projects").status_code == 401
    denied = client.post("/api/v1/auth/login", json={"username": "tester", "password": PASSWORD})
    assert denied.status_code == 403
    response = login(client)
    assert response.status_code == 200
    assert "HttpOnly" in response.headers["set-cookie"]
    assert "SameSite=strict" in response.headers["set-cookie"]
    csrf = response.json()["csrf_token"]
    assert client.get("/api/v1/auth/me").json()["username"] == "tester"
    assert client.get("/api/v1/projects").json()["items"] == [{"key": "game-one", "name": "项目一"}]
    assert client.get("/api/v1/projects/game-one").status_code == 200
    hidden = client.get("/api/v1/projects/game-two")
    missing = client.get("/api/v1/projects/nonexistent")
    assert hidden.status_code == missing.status_code == 404
    assert hidden.json()["error"]["message"] == missing.json()["error"]["message"]
    with Session(engine) as db:
        with pytest.raises(BuildSizeError):
            project_access(db, Principal(user_id=ids["user"]), "game-one", admin=True)
        session = db.scalar(select(WebSession))
        assert session.token_hash != client.cookies.get("buildsize_session")
    assert client.post("/api/v1/auth/logout", headers={"origin": config.origin}).status_code == 403
    assert (
        client.post(
            "/api/v1/auth/logout", headers={"origin": "https://evil.invalid", "x-csrf-token": csrf}
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/api/v1/auth/logout", headers={"origin": config.origin, "x-csrf-token": csrf}
        ).status_code
        == 200
    )
    assert client.get("/api/v1/auth/me").status_code == 401


def test_ci_token_scopes_revocation_and_expiry(foundation) -> None:
    config, engine, client, raw, ids, _ = foundation
    headers = {"authorization": "Bearer " + raw}
    assert client.get("/api/v1/projects/game-one", headers=headers).status_code == 200
    assert client.get("/api/v1/projects/game-two", headers=headers).status_code == 404
    assert client.get("/api/v1/auth/me", headers=headers).status_code == 401
    with Session(engine) as db:
        token = db.get(ApiToken, ids["token"])
        assert token.token_hash == digest(config.token_hash_pepper, "ci", raw)
        assert raw not in token.token_hash
        token.scopes = ["builds:submit"]
        db.commit()
    assert client.get("/api/v1/projects/game-one", headers=headers).status_code == 404
    with Session(engine) as db:
        token = db.get(ApiToken, ids["token"])
        token.scopes = ["builds:read"]
        token.revoked_at = int(time.time())
        db.commit()
    assert client.get("/api/v1/projects", headers=headers).status_code == 401
    with Session(engine) as db:
        token = db.get(ApiToken, ids["token"])
        token.revoked_at = None
        token.expires_at = int(time.time()) - 1
        db.commit()
    assert client.get("/api/v1/projects", headers=headers).status_code == 401


def test_bad_password_rate_limit_and_redaction(foundation, caplog) -> None:
    _, _, client, raw, _, _ = foundation
    caplog.set_level("INFO", logger="buildsize.api")
    invalid = client.post(
        "/api/v1/auth/login", json={"username": "tester", "password": PASSWORD, "typo": raw}
    )
    assert invalid.status_code == 422
    assert PASSWORD not in invalid.text and raw not in invalid.text
    for _ in range(10):
        response = client.post(
            "/api/v1/auth/login",
            json={"username": "tester", "password": "wrong"},
            headers={"origin": "http://testserver"},
        )
        assert response.status_code == 401
    assert login(client).status_code == 429
    assert PASSWORD not in caplog.text and raw not in caplog.text
    assert "request_id" in caplog.text


def build_row(project_id: uuid.UUID, number: str = "1") -> Build:
    return Build(
        project_id=project_id,
        run_id=uuid.uuid4(),
        ci_instance_id="ci",
        ci_job_full_name="job",
        ci_build_number=number,
        variant="release",
        manifest_sha256="a" * 64,
        manifest={},
    )


def test_logical_key_unique_under_concurrent_insert(foundation) -> None:
    _, engine, _, _, ids, _ = foundation

    def insert() -> str:
        try:
            with Session(engine) as db:
                db.add(build_row(ids["project"]))
                db.commit()
            return "created"
        except IntegrityError:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as executor:
        assert sorted(executor.map(lambda _: insert(), range(2))) == ["conflict", "created"]
    with Session(engine) as db:
        build = db.scalar(select(Build))
        db.add(build_row(ids["project"], "2"))  # 不同 CI 身份可对应相同内容。
        db.commit()
        revision = AnalysisRevision(
            build_id=build.id,
            input_sha256="a" * 64,
            parser_version="test",
            status="READY",
            apk_file_bytes=100,
            zip_payload_bytes=90,
            apk_overhead_bytes=11,
        )
        db.add(revision)
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()


def test_config_rejects_default_or_insecure_production() -> None:
    with pytest.raises(ValueError):
        Config("postgresql+psycopg://localhost/db", "http://localhost", "", "")
    with pytest.raises(ValueError):
        Config("sqlite://", "https://host.invalid", "s" * 48, "p" * 48, app_env="production")
    with pytest.raises(ValueError):
        Config(
            "postgresql+psycopg://localhost/db",
            "http://host.invalid",
            "s" * 48,
            "p" * 48,
            app_env="production",
        )


def test_postgresql_offline_migration_sql(
    tmp_path: Path, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import io

    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://localhost/buildsize")
    output = io.StringIO()
    config = AlembicConfig(str(root / "backend/alembic.ini"), output_buffer=output)
    command.upgrade(config, "head", sql=True)
    sql = output.getvalue()
    assert "JSONB" in sql and "BIGINT" in sql
    assert "uq_build_logical_key" in sql
    assert "ALTER TABLE builds ADD CONSTRAINT fk_build_current_analysis" in sql
    assert "REFERENCES analysis_revisions (id)" in sql


def test_secure_cookie_and_expired_session(foundation) -> None:
    from dataclasses import replace

    config, engine, _, _, _, _ = foundation
    secure = replace(config, public_base_url="https://testserver")
    with TestClient(create_app(secure, engine), base_url="https://testserver") as client:
        response = client.post(
            "/api/v1/auth/login",
            json={"username": "tester", "password": PASSWORD},
            headers={"origin": secure.origin},
        )
        assert response.status_code == 200
        assert "Secure" in response.headers["set-cookie"]
        with Session(engine) as db:
            session = db.scalar(select(WebSession))
            session.expires_at = int(time.time()) - 1
            db.commit()
        assert client.get("/api/v1/auth/me").status_code == 401
