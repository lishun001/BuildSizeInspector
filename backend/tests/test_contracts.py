import copy
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker
from pydantic import ValidationError

from buildsize.domain.contracts import (
    AssetImporters,
    BuildMeta,
    Finding,
    GitChanges,
    Submission,
    UnityReport,
)
from buildsize.domain.errors import BuildSizeError
from buildsize.domain.validation import read_json, validate_identity

MODELS = {
    "submission": Submission,
    "build-meta": BuildMeta,
    "unity-report": UnityReport,
    "asset-importers": AssetImporters,
    "git-changes": GitChanges,
    "finding": Finding,
}


@pytest.mark.parametrize("name", MODELS)
def test_six_examples_match_both_contracts(root: Path, name: str) -> None:
    value = read_json(root / "examples" / f"{name}.json")
    schema = read_json(root / "schemas" / f"{name}.schema.json")
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    validator.validate(value)
    model = MODELS[name].model_validate(value)
    validator.validate(model.model_dump(mode="json"))
    assert model.model_dump(mode="json") == value
    value["typo"] = True
    with pytest.raises(ValidationError):
        MODELS[name].model_validate(value)


def test_semantic_identity_and_canonical_manifest(root: Path) -> None:
    raw = read_json(root / "examples/submission.json")
    manifest = Submission.model_validate(raw)
    raw["artifacts"].reverse()
    assert Submission.model_validate(raw).manifest_sha256() == manifest.manifest_sha256()
    assert "demo-game" in manifest.canonical_bytes().decode()
    meta = BuildMeta.model_validate(read_json(root / "examples/build-meta.json"))
    report = UnityReport.model_validate(read_json(root / "examples/unity-report.json"))
    validate_identity(manifest, meta, [report])
    raw["artifacts"][0]["sha256"] = "f" * 64
    changed = Submission.model_validate(raw)
    assert changed.logical_key() == manifest.logical_key()
    assert changed.manifest_sha256() != manifest.manifest_sha256()
    with pytest.raises(BuildSizeError, match="run_id"):
        validate_identity(manifest, meta, [report.model_copy(update={"run_id": "bad"})])
    with pytest.raises(BuildSizeError, match="版本"):
        validate_identity(manifest, meta, [report.model_copy(update={"unity_version": "other"})])


@pytest.mark.parametrize("change", ["role", "missing", "uuid", "path", "float", "boolean", "limit"])
def test_invalid_submission(root: Path, change: str) -> None:
    value = read_json(root / "examples/submission.json")
    if change == "role":
        value["artifacts"][1]["role"] = "apk"
    elif change == "missing":
        value["artifacts"] = value["artifacts"][1:]
    elif change == "uuid":
        value["run_id"] = "not-a-uuid"
    elif change == "path":
        value["artifacts"][0]["filename"] = "../game.apk"
    elif change == "float":
        value["artifacts"][0]["bytes"] = 1.5
    elif change == "boolean":
        value["schema_version"] = True
    elif change == "limit":
        value["artifacts"][0]["bytes"] = 2 * 1024**3 + 1
    with pytest.raises(ValidationError):
        Submission.model_validate(value)


@pytest.mark.parametrize("change", ["container", "record", "guid", "zero_guid", "missing_null"])
def test_report_semantics(root: Path, change: str) -> None:
    report = read_json(root / "examples/unity-report.json")
    importers = read_json(root / "examples/asset-importers.json")
    model = UnityReport
    value = report
    if change == "container":
        value["containers"].append(copy.deepcopy(value["containers"][0]))
    elif change == "record":
        value["containers"][0]["entries"].append(value["containers"][0]["entries"][0])
    elif change in {"guid", "zero_guid"}:
        model, value = AssetImporters, importers
        if change == "guid":
            value["assets"].append(copy.deepcopy(value["assets"][0]))
        else:
            value["assets"][0]["source_guid"] = "0" * 32
    else:
        del value["unity_reported_total_bytes"]
    with pytest.raises(ValidationError):
        model.model_validate(value)


@pytest.mark.parametrize(
    "payload", [b'{"a":1,"a":2}', b'{"x":NaN}', b'{"x":1e999}', b"[]", b"\xff"]
)
def test_json_invalid(tmp_path: Path, payload: bytes) -> None:
    path = tmp_path / "report.json"
    path.write_bytes(payload)
    with pytest.raises(BuildSizeError) as error:
        read_json(path)
    assert error.value.code == "INVALID_JSON"


def test_json_limits(tmp_path: Path) -> None:
    path = tmp_path / "report.json"
    path.write_text(json.dumps({"value": "a" * 10}))
    with pytest.raises(BuildSizeError) as error:
        read_json(path, max_bytes=5)
    assert error.value.code == "JSON_LIMIT_EXCEEDED"
    path.write_text('{"x":' * 30 + "0" + "}" * 30)
    with pytest.raises(BuildSizeError):
        read_json(path)
