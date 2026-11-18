import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session
from test_pipeline import Harness, admin_login, result, run, submission, submit
from test_pipeline import pipeline as pipeline

from buildsize.db.models import AuditEvent, FindingRow, Job
from buildsize.domain.contracts import Finding


def test_rules_frozen_review_and_project_permissions(pipeline: Harness):
    h = pipeline
    old, job = submit(h, *submission("old", {"assets/x": b"a"}, age=120))
    run(h, job)
    path = "/api/v1/projects/demo-game/rules"
    current = h.client.get(path, headers=h.headers)
    assert current.status_code == 200 and current.headers["etag"] == '"0"'
    settings = current.json()["settings"]
    settings.update(growth_bytes=1, growth_basis_points=0, absolute_budget_bytes=1)
    assert (
        h.client.put(path, json=settings, headers={**h.headers, "If-Match": '"0"'}).status_code
        == 404
    )
    web = admin_login(h)
    assert h.client.put(path, json=settings, headers={**web, "If-Match": '"0"'}).status_code == 200
    assert h.client.put(path, json=settings, headers={**web, "If-Match": '"0"'}).status_code == 409
    assert h.client.put(path, json=settings, headers={"If-Match": '"1"'}).status_code == 403
    new, job = submit(
        h, *submission("new", {"assets/x": b"a" * 100, "lib/arm64-v8a/new.so": b"native"}, age=60)
    )
    run(h, job)
    comparison = result(h, new)["result"]["comparison_id"]
    value = h.client.get(f"/api/v1/comparisons/{comparison}", headers=h.headers).json()
    assert value["result"]["quality_gate"] == "WARN"
    listed = h.client.get(f"/api/v1/comparisons/{comparison}/findings?limit=1", headers=h.headers)
    assert listed.status_code == 200 and listed.json()["next_offset"] == 1
    item = listed.json()["items"][0]
    Finding.model_validate(item["finding"])
    assert (
        h.client.get(f"/api/v1/findings/{item['id']}", headers=h.other_headers).status_code == 404
    )
    detail = h.client.get(f"/api/v1/findings/{item['id']}", headers=h.headers).json()
    assert detail["evidence"][0]["sources"]
    review = f"/api/v1/findings/{item['id']}/review"
    assert (
        h.client.put(
            review, json={"review_state": "confirmed"}, headers={**h.headers, "If-Match": '"0"'}
        ).status_code
        == 404
    )
    assert (
        h.client.put(
            review, json={"review_state": "confirmed"}, headers={**web, "If-Match": '"0"'}
        ).status_code
        == 200
    )
    assert (
        h.client.put(
            review, json={"review_state": "rejected"}, headers={**web, "If-Match": '"0"'}
        ).status_code
        == 409
    )
    settings["growth_bytes"] = 100000000
    assert h.client.put(path, json=settings, headers={**web, "If-Match": '"1"'}).status_code == 200
    frozen = h.client.get(f"/api/v1/comparisons/{comparison}/findings", headers=h.headers).json()
    assert frozen["rules_version"] == 1 and frozen["rules_snapshot"]["growth_bytes"] == 1
    assert (
        h.client.get(f"/api/v1/comparisons/{comparison}", headers=h.headers).json()["result"]
        == value["result"]
    )
    with Session(h.engine) as db:
        row = db.get(FindingRow, uuid.UUID(item["id"]))
        assert row.payload["review_state"] == "unreviewed" and row.review_state == "confirmed"
        actions = db.scalars(select(AuditEvent.action)).all()
        assert "rules.update" in actions and "finding.review.confirmed" in actions
    # 创建时冻结配置，不以 Worker 启动时的新阈值重写队列里的比较。
    created = h.client.post(
        "/api/v1/comparisons",
        json={"base_build_id": old, "target_build_id": new},
        headers={**h.headers, "Idempotency-Key": str(uuid.uuid4())},
    ).json()["comparison_id"]
    settings["growth_bytes"] = 1
    assert h.client.put(path, json=settings, headers={**web, "If-Match": '"2"'}).status_code == 200
    with Session(h.engine) as db:
        job_id = db.scalar(select(Job.id).where(Job.comparison_id == uuid.UUID(created)))
    run(h, str(job_id))
    assert (
        h.client.get(f"/api/v1/comparisons/{created}", headers=h.headers).json()["result"][
            "quality_gate"
        ]
        == "PASS"
    )
    admin_login(h, "viewer")
    assert (
        h.client.put(
            path, json=settings, headers={**admin_login(h, "viewer"), "If-Match": '"3"'}
        ).status_code
        == 404
    )
    assert h.client.get("/api/v1/projects/demo-game/audit").status_code == 404


def test_trend_does_not_mix_groups_and_has_unknown_points(pipeline: Harness):
    h = pipeline
    old, job = submit(h, *submission("arm64", age=120))
    run(h, job)
    other, job = submit(h, *submission("other", age=90, settings={"development_build": True}))
    run(h, job)
    current, job = submit(h, *submission("new", age=60))
    run(h, job)
    result_page = h.client.get(
        "/api/v1/projects/demo-game/trend", params={"reference_build_id": old}, headers=h.headers
    ).json()
    assert {row["build_id"] for row in result_page["items"]} == {old, current}
    assert other not in {row["build_id"] for row in result_page["items"]}
    assert (
        h.client.get("/api/v1/projects/demo-game/trend", headers=h.other_headers).status_code == 404
    )
    assert (
        h.client.get(
            "/api/v1/projects/other-game/trend",
            params={"reference_build_id": old},
            headers=h.headers,
        ).status_code
        == 404
    )


def test_concurrent_rule_updates_keep_one_version(pipeline: Harness):
    from concurrent.futures import ThreadPoolExecutor

    import pytest
    from sqlalchemy import func

    from buildsize.db.models import RuleConfigRow

    h = pipeline
    if h.engine.dialect.name != "postgresql":
        pytest.skip("NOT_VERIFIED：规则并发更新需 PostgreSQL 行锁")
    headers = {**admin_login(h), "If-Match": '"0"'}
    settings = h.client.get("/api/v1/projects/demo-game/rules").json()["settings"]

    def update(value):
        return h.client.put(
            "/api/v1/projects/demo-game/rules",
            json={**settings, "growth_bytes": value},
            headers=headers,
        ).status_code

    with ThreadPoolExecutor(max_workers=2) as executor:
        assert sorted(executor.map(update, [10, 20])) == [200, 409]
    assert h.client.get("/api/v1/projects/demo-game/rules").json()["version"] == 1
    with Session(h.engine) as db:
        assert db.scalar(select(func.count()).select_from(RuleConfigRow)) == 1
