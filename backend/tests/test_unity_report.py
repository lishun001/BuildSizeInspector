import json
import zipfile
from pathlib import Path

import pytest

from buildsize.analyzers.apk_zip import analyze_apk
from buildsize.analyzers.unity_report import parse_unity
from buildsize.comparison.unity import compare_unity
from buildsize.domain.contracts import AssetImporters, UnityReport
from buildsize.domain.errors import BuildSizeError

ROOT = Path(__file__).resolve().parents[2]
GUID = "a" * 32


def report(entries=None, containers=None):
    value = json.loads((ROOT / "examples/unity-report.json").read_text())
    if entries is not None:
        value["containers"][0]["entries"] = entries
    if containers is not None:
        value["containers"] = containers
    return UnityReport.model_validate(value)


def record(index=0, guid=GUID, path="Assets/A.png", size=10, kind="Texture2D"):
    return {
        "record_index": index,
        "source_guid": guid,
        "source_path": path,
        "asset_type": kind,
        "packed_bytes": size,
    }


def apk(tmp_path):
    path = tmp_path / "fixture.zip"
    with zipfile.ZipFile(path, "w") as output:
        output.writestr("assets/bin/Data/sharedassets0.assets", b"synthetic ZIP")
        output.writestr("assets/bin/Data/foo/sharedassets0.assets", b"other")
    return analyze_apk(path)


def test_guid_rename_multiple_objects_and_independent_metrics(tmp_path: Path) -> None:
    snapshot = apk(tmp_path)
    old = parse_unity(
        report([record(0), record(1, size=20), record(2, kind="Sprite", size=5)]), snapshot
    )
    new = parse_unity(
        report(
            [
                record(0, path="Assets/Renamed.png", size=15),
                record(1, path="Assets/Renamed.png", size=30),
                record(2, path="Assets/Renamed.png", kind="Sprite", size=5),
            ]
        ),
        snapshot,
    )
    assert len(old.groups) == 2
    assert sum(group.record_count for group in old.groups) == 3
    assert old.summary["unity_packed_bytes"] == 35
    assert old.summary["unity_reported_total_bytes"] != snapshot.apk_file_bytes
    result = compare_unity(old, new)
    assert result["metric"] == "unity_packed_bytes" and not result["additive_to_apk"]
    assert result["delta_packed_bytes"] == 15
    assert all(row["renamed"] for row in result["changes"])
    assert all(row["status"] == "CHANGED" for row in result["changes"])
    assert sum(row["delta_packed_bytes"] for row in result["changes"]) == 15
    assert all(row["source_guid"] == GUID for row in result["changes"])


def test_generated_and_path_fallback_preserve_records(tmp_path: Path) -> None:
    value = report(
        [
            record(0, guid="0" * 32, path=None, size=8),
            record(1, guid=None, path=None, size=12),
            record(2, guid=None, path="Assets/no-guid.txt", size=5),
        ]
    )
    result = parse_unity(value, apk(tmp_path))
    assert result.report.containers[0].entries[0].source_guid == "0" * 32
    assert result.summary["report_record_count"] == 3
    assert result.summary["unattributed_packed_bytes"] == 20
    assert result.summary["guid_coverage_ratio"] == 0
    groups = {row.identity_kind: row for row in result.groups}
    assert groups["generated"].record_count == 2
    assert groups["path_fallback"].source_paths == ("Assets/no-guid.txt",)
    assert compare_unity(None, result)["delta_packed_bytes"] is None


def test_duplicate_container_names_do_not_create_object_matches(tmp_path: Path) -> None:
    containers = [
        {
            "container_index": index,
            "short_path": "sharedassets0.assets",
            "overhead_bytes": 0,
            "entries": [record(0, guid=None, path=None, size=10 + index)],
        }
        for index in range(2)
    ]
    value = parse_unity(report(containers=containers), apk(tmp_path))
    assert all(row.state == "ambiguous" for row in value.mappings)
    assert all(not row.matchable for row in value.groups)
    result = compare_unity(value, value)
    assert result["changes"] == []
    assert result["unmatched_generated_old"] == 21


def test_full_path_mapping_never_uses_basename(tmp_path: Path) -> None:
    containers = [
        {
            "container_index": 0,
            "short_path": "foo/sharedassets0.assets",
            "overhead_bytes": 0,
            "entries": [],
        },
        {
            "container_index": 1,
            "short_path": "bar/sharedassets0.assets",
            "overhead_bytes": 0,
            "entries": [],
        },
    ]
    value = parse_unity(report(containers=containers), apk(tmp_path))
    assert value.mappings[0].apk_paths == ("assets/bin/Data/foo/sharedassets0.assets",)
    assert value.mappings[1].state == "unmapped"
    assert value.mappings[1].apk_paths == ()
    assert value.summary["guid_coverage_ratio"] is None


def test_report_truncation_is_not_a_complete_diff(tmp_path: Path) -> None:
    raw = report([record()]).model_dump(mode="json")
    old = parse_unity(UnityReport.model_validate(raw), apk(tmp_path))
    raw["warnings"] = ["BUILDSIZE_TRUNCATED: records reached limit"]
    partial = parse_unity(UnityReport.model_validate(raw), apk(tmp_path))
    assert partial.summary["state"] == "failed"
    assert compare_unity(old, partial)["status"] == "unavailable"
    assert compare_unity(old, partial)["changes"] == []


def test_importer_validation_and_settings_changes(tmp_path: Path) -> None:
    raw = json.loads((ROOT / "examples/asset-importers.json").read_text())
    old = parse_unity(report(), apk(tmp_path), AssetImporters.model_validate(raw))
    assert old.summary["importers"]["ratio"] == 1
    raw["assets"][0]["settings"]["android"]["format"] = "Automatic"
    raw["assets"][0]["settings"]["future_field"] = 1
    new = parse_unity(report(), apk(tmp_path), AssetImporters.model_validate(raw))
    assert "future_field" in new.importer_rows[0]["unsupported_fields"]
    assert new.importer_rows[0]["settings"]["android"]["format"] == "Automatic"
    changes = compare_unity(old, new)["importer_changes"]
    assert changes[0]["fields"] == ["settings"]
    assert changes[0]["confidence"] == "observed"
    raw["assets"][0]["settings"]["mipmap_enabled"] = "false"
    broken = parse_unity(report(), apk(tmp_path), AssetImporters.model_validate(raw))
    assert broken.summary["importers"]["state"] == "failed"
    assert broken.summary["importers"]["collected_count"] == 0
    assert broken.importer_rows[0]["state"] == "failed"


def test_importer_version_mismatch_and_integer_overflow(tmp_path: Path) -> None:
    raw = json.loads((ROOT / "examples/asset-importers.json").read_text())
    raw["unity_version"] = "different"
    with pytest.raises(BuildSizeError):
        parse_unity(report(), apk(tmp_path), AssetImporters.model_validate(raw))
    with pytest.raises(BuildSizeError):
        parse_unity(report([record(0, size=9007199254740991), record(1)]), apk(tmp_path))


def test_incomplete_known_importer_settings_are_not_complete(tmp_path: Path) -> None:
    raw = json.loads((ROOT / "examples/asset-importers.json").read_text())
    raw["assets"][0]["settings"] = {}
    value = parse_unity(report(), apk(tmp_path), AssetImporters.model_validate(raw))
    assert value.summary["importers"]["state"] == "failed"
    assert value.importer_rows[0]["state"] == "failed"
