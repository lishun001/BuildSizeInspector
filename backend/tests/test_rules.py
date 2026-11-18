"""先约束规则语义：精确物理账、非相加证据、缺失与跨组不触发增长门禁。"""

import copy
import io
import json
import zipfile
from pathlib import Path

from buildsize.analyzers.apk_zip import analyze_apk
from buildsize.domain.contracts import BuildMeta
from buildsize.rules.config import RuleSettings
from buildsize.rules.engine import RuleInput, evaluate

ROOT = Path(__file__).resolve().parents[2]


def snapshot(tmp_path, name, contents, compression=zipfile.ZIP_STORED):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=compression) as output:
        for path, content in contents.items():
            output.writestr(path, content)
    path = tmp_path / name
    path.write_bytes(stream.getvalue())
    return analyze_apk(path)


def context(tmp_path):
    meta = BuildMeta.model_validate(json.loads((ROOT / "examples/build-meta.json").read_text()))
    old = snapshot(tmp_path, "old.apk", {"assets/a": b"x" * 1000})
    new = snapshot(
        tmp_path, "new.apk", {"assets/a": b"y" * 2000, "lib/arm64-v8a/libnew.so": b"new"}
    )
    return RuleInput(
        "11111111-1111-4111-8111-111111111111",
        "22222222-2222-4222-8222-222222222222",
        old,
        new,
        meta,
        meta,
        True,
        {"unity_report": "missing"},
        [],
        [],
        {},
    )


def test_thresholds_and_non_additive_evidence(tmp_path):
    value = context(tmp_path)
    findings = list(
        evaluate(
            value, RuleSettings(growth_bytes=1, growth_basis_points=1, absolute_budget_bytes=1)
        )
    )
    ids = {row.rule_id for row in findings}
    assert {
        "APK_GROWTH_THRESHOLD",
        "APK_ABSOLUTE_BUDGET",
        "NATIVE_LIBRARY_ADDED",
        "NATIVE_GROWTH_UNEXPLAINED",
        "REPORT_DATA_INCOMPLETE",
    } <= ids
    assert all(row.additive is False and row.evidence for row in findings)
    assert all(
        e.artifact_sha256 or e.locator.startswith("analysis_revision:")
        for row in findings
        for e in row.evidence
    )
    assert (
        next(row for row in findings if row.rule_id == "NATIVE_GROWTH_UNEXPLAINED").confidence
        == "hypothesis"
    )


def test_no_baseline_cross_group_and_and_threshold(tmp_path):
    from dataclasses import replace

    value = context(tmp_path)
    for changed in (
        replace(value, base=None, base_meta=None, base_revision=None),
        replace(value, comparable=False),
    ):
        assert "APK_GROWTH_THRESHOLD" not in {
            row.rule_id for row in evaluate(changed, RuleSettings(growth_bytes=1))
        }
    for settings in (
        RuleSettings(growth_bytes=100000000, growth_basis_points=0),
        RuleSettings(growth_bytes=0, growth_basis_points=1000000),
    ):
        assert "APK_GROWTH_THRESHOLD" not in {row.rule_id for row in evaluate(value, settings)}


def test_unity_correlated_known_settings_only(tmp_path):
    value = context(tmp_path)
    value.unity_changes.extend(
        [
            {
                "key": "texture",
                "source_guid": "a" * 32,
                "identity_kind": "guid",
                "asset_type": "Texture2D",
                "status": "CHANGED",
                "delta_packed_bytes": 2048,
            },
            {
                "key": "audio",
                "source_guid": "b" * 32,
                "identity_kind": "guid",
                "asset_type": "AudioClip",
                "status": "CHANGED",
                "delta_packed_bytes": 4096,
            },
            {
                "key": "entered",
                "source_guid": "c" * 32,
                "identity_kind": "guid",
                "asset_type": "Texture2D",
                "status": "ADDED_TO_BUILD",
                "delta_packed_bytes": 100,
            },
        ]
    )
    for guid in ("a" * 32, "b" * 32):
        value.importer_changes.append(
            {
                "source_guid": guid,
                "fields": ["settings"],
                "old": {
                    "importer_type": "TextureImporter" if guid == "a" * 32 else "AudioImporter",
                    "settings": {"mipmap_enabled": True, "force_to_mono": True, "unknown": None},
                },
                "new": {
                    "importer_type": "TextureImporter" if guid == "a" * 32 else "AudioImporter",
                    "settings": {"mipmap_enabled": False, "force_to_mono": False, "unknown": 5},
                },
            }
        )
    findings = list(evaluate(value, RuleSettings()))
    correlated = [row for row in findings if row.confidence == "correlated"]
    assert {row.rule_id for row in correlated} == {
        "UNITY_TEXTURE_IMPORT_CHANGED",
        "UNITY_AUDIO_IMPORT_CHANGED",
    }
    assert all(row.metric == "unity_packed_bytes" and not row.additive for row in correlated)
    assert (
        next(row for row in findings if row.rule_id == "UNITY_SOURCE_ENTERED_BUILD").confidence
        == "observed"
    )
    value.importer_changes[0]["old"]["settings"]["mipmap_enabled"] = None
    assert "UNITY_TEXTURE_IMPORT_CHANGED" not in {
        row.rule_id for row in evaluate(value, RuleSettings())
    }
    value.unity_changes.append(
        {
            "key": "prior-type",
            "source_guid": "c" * 32,
            "identity_kind": "guid",
            "asset_type": "Mesh",
            "status": "UNCHANGED",
            "delta_packed_bytes": 0,
        }
    )
    assert "UNITY_SOURCE_ENTERED_BUILD" not in {
        row.rule_id for row in evaluate(value, RuleSettings())
    }


def test_packaging_duplicates_and_declared_config(tmp_path):
    from dataclasses import replace

    value = context(tmp_path)
    old = snapshot(tmp_path, "stored.apk", {"assets/x": b"a" * 1000})
    new = snapshot(
        tmp_path,
        "deflated.apk",
        {"assets/x": b"a" * 1000, "assets/y": b"a" * 1000},
        zipfile.ZIP_DEFLATED,
    )
    meta = copy.deepcopy(value.target_meta.model_dump())
    meta["settings"]["architectures"] = ["armeabi-v7a"]
    meta["settings"]["unity_version"] = "6000.0.99f1"
    value = replace(value, base=old, target=new, target_meta=BuildMeta.model_validate(meta))
    ids = {row.rule_id for row in evaluate(value, RuleSettings())}
    assert {
        "PACKAGING_CHANGED",
        "DUPLICATE_ENTRY_CONTENT",
        "ABI_SET_CHANGED",
        "UNITY_VERSION_CHANGED",
    } <= ids
