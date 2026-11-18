import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from buildsize.client.context import prepare
from buildsize.client.files import ClientError
from buildsize.client.git import changes, snapshot
from buildsize.client.submission import collect, validate_frozen

ROOT = Path(__file__).resolve().parents[2]


def command(path: Path, *args: str) -> bytes:
    result = subprocess.run(
        ["git", "-c", "user.name=BuildSize Test", "-c", "user.email=test@example.invalid", *args],
        cwd=path,
        capture_output=True,
        check=True,
    )
    return result.stdout


def repository(path: Path) -> Path:
    path.mkdir(parents=True)
    command(path, "init", "-b", "main")
    (path / "file.txt").write_text("test")
    command(path, "add", ".")
    command(path, "commit", "-m", "fixture")
    return path


def configuration(path: Path, repos: list[Path]) -> Path:
    value = json.loads((ROOT / "examples/build-meta.json").read_text())
    metadata = {
        key: value[key]
        for key in (
            "application_id",
            "version_name",
            "version_code",
            "channel",
            "branch",
            "settings",
            "toolchain",
            "content_policy",
        )
    }
    metadata["build_completed_at"] = datetime.now(UTC).isoformat()
    result = {
        "schema_version": 1,
        "metadata": metadata,
        "repositories": [
            {"repo_key": "repo" + str(i), "path": str(repo), "kind": "main" if i == 0 else "art"}
            for i, repo in enumerate(repos)
        ],
        "ci": value["ci"],
    }
    path.write_text(json.dumps(result))
    return path


@pytest.fixture
def clean_ci(monkeypatch):
    for key in (
        "BUILDSIZE_CI_INSTANCE_ID",
        "JOB_NAME",
        "BUILD_NUMBER",
        "BUILD_URL",
        "BRANCH_NAME",
        "BUILDSIZE_BUILD_COMPLETED_AT",
        "BUILDSIZE_API_URL",
        "BUILDSIZE_API_TOKEN",
    ):
        monkeypatch.delenv(key, raising=False)


def test_prepare_reuses_identity_and_frozen_collect(tmp_path: Path, clean_ci) -> None:
    repo = repository(tmp_path / "仓库 空格")
    cfg = configuration(tmp_path / "config.json", [repo])
    context = tmp_path / ".buildsize/context.json"
    first = prepare("demo-game", "android-release-arm64", context, cfg, tmp_path)
    second = prepare("demo-game", "android-release-arm64", context, cfg, tmp_path)
    elsewhere = prepare(
        "demo-game", "android-release-arm64", tmp_path / "another/context.json", cfg, tmp_path
    )
    assert first == second
    assert first["run_id"] == elsewhere["run_id"]
    assert first["environment"]["BUILDSIZE_REPORT_DIR"].endswith(first["run_id"])
    with pytest.raises(ClientError) as error:
        prepare("demo-game", "other", context, cfg, tmp_path)
    assert error.value.exit_code == 5
    apk = tmp_path / "final explicit.apk"
    apk.write_bytes(b"synthetic ZIP fixture")
    output = tmp_path / "submission dir"
    assert collect(context, apk, output)["state"] == "frozen"
    before = {p.name: p.read_bytes() for p in output.iterdir()}
    assert collect(context, apk, output)["state"] == "already_frozen"
    assert before == {p.name: p.read_bytes() for p in output.iterdir()}
    manifest = validate_frozen(output)
    assert manifest.run_id == first["run_id"]
    (output / "game.apk").write_bytes(b"changed")
    with pytest.raises(ClientError):
        validate_frozen(output)


def test_multi_repo_names_rename_guid_and_missing_base(tmp_path: Path) -> None:
    repo = repository(tmp_path / "main")
    asset = repo / "中文 old file.txt"
    asset.write_text("a" * 100)
    (repo / "中文 old file.txt.meta").write_text("fileFormatVersion: 2\nguid: " + "a" * 32 + "\n")
    command(repo, "add", ".")
    command(repo, "commit", "-m", "asset")
    base = command(repo, "rev-parse", "HEAD").decode().strip()
    command(repo, "mv", "中文 old file.txt", "中文 new name.txt")
    command(repo, "mv", "中文 old file.txt.meta", "中文 new name.txt.meta")
    (repo / "added space.txt").write_text("new")
    command(repo, "add", ".")
    command(repo, "commit", "-m", "rename")
    current = snapshot(repo, "main", "main", ".")
    result = changes(repo, current, base)
    assert result["range_status"] == "complete"
    renamed = next(row for row in result["changes"] if row["old_path"] == "中文 old file.txt")
    assert renamed["status"] == "R"
    assert renamed["new_path"] == "中文 new name.txt"
    assert renamed["source_guid"] == "a" * 32
    assert renamed["old_blob"] == renamed["new_blob"]
    missing = changes(repo, current, "f" * 40)
    assert missing["range_status"] == "missing_base" and missing["is_ancestor"] is None
    assert result["commit_ids"] == [current["revision"]]
    art = repository(tmp_path / "art")
    cfg = configuration(tmp_path / "config.json", [repo, art])
    context = prepare("demo-game", "variant", tmp_path / "context.json", cfg, tmp_path)
    assert len(context["repositories"]) == 2


def test_head_change_after_prepare_is_conflict(tmp_path: Path, clean_ci) -> None:
    repo = repository(tmp_path / "repo")
    cfg = configuration(tmp_path / "config.json", [repo])
    context = tmp_path / "context.json"
    prepare("demo-game", "variant", context, cfg, tmp_path)
    (repo / "new.txt").write_text("new")
    command(repo, "add", ".")
    command(repo, "commit", "-m", "changed")
    apk = tmp_path / "game.apk"
    apk.write_bytes(b"fixture")
    with pytest.raises(ClientError) as error:
        collect(context, apk, tmp_path / "submission")
    assert error.value.code == "REPOSITORY_CHANGED_DURING_BUILD"
    assert not (tmp_path / "submission").exists()


def test_capture_wrong_run_and_hash_do_not_fallback(tmp_path: Path, clean_ci) -> None:
    repo = repository(tmp_path / "repo")
    cfg = configuration(tmp_path / "config.json", [repo])
    context_file = tmp_path / "context.json"
    context = prepare("demo-game", "variant", context_file, cfg, tmp_path)
    report_dir = Path(context["report_directory"])
    report_dir.mkdir(parents=True)
    (report_dir / "capture-complete.json").write_text(
        json.dumps({"schema_version": 1, "run_id": "wrong", "artifacts": []})
    )
    apk = tmp_path / "game.apk"
    apk.write_bytes(b"fixture")
    with pytest.raises(ClientError) as error:
        collect(context_file, apk, tmp_path / "submission")
    assert error.value.code == "CAPTURE_IDENTITY_CONFLICT"
    assert not (tmp_path / "submission").exists()


def test_all_ci_commands_help_without_credentials(clean_ci) -> None:
    for name in ("prepare", "collect", "submit", "status", "link-pgyer"):
        result = subprocess.run(
            [sys.executable, "-m", "buildsize", name, "--help"], capture_output=True, text=True
        )
        assert result.returncode == 0


def test_shallow_clone_missing_base_and_dirty_snapshot(tmp_path: Path) -> None:
    original = repository(tmp_path / "original")
    base = command(original, "rev-parse", "HEAD").decode().strip()
    (original / "file.txt").write_text("changed")
    command(original, "add", ".")
    command(original, "commit", "-m", "second")
    command(tmp_path, "clone", "--depth=1", original.as_uri(), str(tmp_path / "shallow"))
    shallow = tmp_path / "shallow"
    current = snapshot(shallow, "main", "main", ".")
    result = changes(shallow, current, base)
    assert result["range_status"] == "missing_base"
    assert result["changes"] == [] and result["is_ancestor"] is None
    (shallow / "file.txt").write_text("dirty source stays local")
    assert snapshot(shallow, "main", "main", ".")["dirty"] is True


def captured_fixture(tmp_path: Path):
    import hashlib

    repo = repository(tmp_path / "repo")
    cfg = configuration(tmp_path / "config.json", [repo])
    config = json.loads(cfg.read_text())["metadata"]
    context_file = tmp_path / "context.json"
    context = prepare("demo-game", "variant", context_file, cfg, tmp_path)
    report_dir = Path(context["report_directory"])
    report_dir.mkdir(parents=True)
    report = json.loads((ROOT / "examples/unity-report.json").read_text())
    report["run_id"] = context["run_id"]
    settings = {
        **config,
        "platform": "Android",
        "run_id": context["run_id"],
        "exporter_version": config["toolchain"]["exporter_version"],
    }
    artifacts = []
    for filename, value in (("unity-report.json", report), ("unity-settings.json", settings)):
        raw = json.dumps(value).encode()
        (report_dir / filename).write_bytes(raw)
        artifacts.append(
            {"filename": filename, "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
        )
    (report_dir / "capture-complete.json").write_text(
        json.dumps({"schema_version": 1, "run_id": context["run_id"], "artifacts": artifacts})
    )
    apk = tmp_path / "game.apk"
    apk.write_bytes(b"synthetic ZIP fixture")
    return context_file, report_dir, apk


def test_capture_receipt_hash_and_only_listed_files(tmp_path: Path, clean_ci) -> None:
    context, reports, apk = captured_fixture(tmp_path)
    (reports / "asset-importers.json").write_text("stale not in receipt")
    collect(context, apk, tmp_path / "frozen")
    assert not (tmp_path / "frozen/asset-importers.json").exists()
    (reports / "unity-report.json").write_text("changed after callback")
    with pytest.raises(ClientError) as error:
        collect(context, apk, tmp_path / "other")
    assert error.value.code == "CAPTURE_HASH_MISMATCH"
    assert not (tmp_path / "other").exists()


def test_capture_replacement_during_freeze_rejected(tmp_path: Path, clean_ci, monkeypatch) -> None:
    from buildsize.client import submission

    context, reports, apk = captured_fixture(tmp_path)
    copy_original = submission._copy

    def replaced(source, destination, maximum):
        if source == reports / "unity-report.json":
            value = json.loads(source.read_text())
            value["warnings"].append("replaced")
            source.write_text(json.dumps(value))
        return copy_original(source, destination, maximum)

    monkeypatch.setattr(submission, "_copy", replaced)
    with pytest.raises(ClientError) as error:
        collect(context, apk, tmp_path / "frozen")
    assert error.value.code == "CAPTURE_CHANGED"
    assert not (tmp_path / "frozen").exists()
    assert list(tmp_path.glob(".buildsize-collect-*")) == []
