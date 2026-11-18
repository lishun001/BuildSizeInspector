"""Unity 层的输入、存储和比较；事务由 Worker 的 fencing 发布边界控制。"""

from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError
from sqlalchemy import insert, select
from sqlalchemy.orm import Session

from buildsize.analyzers.unity_report import parse_importers, parse_unity
from buildsize.comparison.unity import compare_unity
from buildsize.db.models import (
    AnalysisRevision,
    ArtifactBlob,
    AssetImporterRow,
    Build,
    BuildArtifact,
    Comparison,
    Entity,
    UnityChangeRow,
    UnityContainerRow,
    UnityGroupRow,
    UnityRecordRow,
)
from buildsize.domain.apk import ApkSnapshot
from buildsize.domain.contracts import AssetImporters, UnityReport
from buildsize.domain.errors import BuildSizeError
from buildsize.domain.unity import AssetGroup, UnitySnapshot
from buildsize.domain.validation import read_json
from buildsize.storage.local import BlobStorage


@dataclass(frozen=True)
class Layers:
    snapshot: UnitySnapshot | None
    importer_rows: tuple[dict[str, Any], ...]
    summary: dict[str, Any]


def read_layers(db: Session, storage: BlobStorage, build: Build, apk: ApkSnapshot) -> Layers:
    rows = {
        row.role: row
        for row in db.scalars(select(BuildArtifact).where(BuildArtifact.build_id == build.id))
    }
    values = {}
    for role in ("unity_report", "asset_importers"):
        row = rows.get(role)
        if row is None or row.schema_state != "complete":
            continue
        blob = db.get(ArtifactBlob, row.blob_id)
        assert blob is not None and blob.project_id == build.project_id
        with storage.read_path(blob.storage_key) as path:
            values[role] = read_json(path)

    def absent(role: str) -> str:
        row = rows.get(role)
        return (
            "missing"
            if row is None
            else "unsupported"
            if row.schema_state == "unsupported_schema"
            else "failed"
        )

    importers = (
        AssetImporters.model_validate(values["asset_importers"])
        if "asset_importers" in values
        else None
    )
    if "unity_report" not in values:
        importer_rows, importer_summary = parse_importers(importers)
        if importers is None:
            importer_summary["state"] = absent("asset_importers")
        return Layers(
            None, importer_rows, {"state": absent("unity_report"), "importers": importer_summary}
        )
    try:
        snapshot = parse_unity(UnityReport.model_validate(values["unity_report"]), apk, importers)
    except (BuildSizeError, ValidationError):
        importer_rows, importer_summary = parse_importers(importers)
        return Layers(
            None,
            importer_rows,
            {"state": "failed", "error_code": "UNITY_DATA_INVALID", "importers": importer_summary},
        )
    if importers is None:
        snapshot.summary["importers"]["state"] = absent("asset_importers")
    return Layers(snapshot, snapshot.importer_rows, snapshot.summary)


def _store(db: Session, model: type[Entity], rows: list[dict[str, Any]]) -> None:
    for start in range(0, len(rows), 1000):
        db.execute(insert(model), rows[start : start + 1000])


def persist_layers(db: Session, revision: AnalysisRevision, layers: Layers) -> None:
    snapshot = layers.snapshot
    if snapshot and snapshot.report:
        mappings = {row.container_index: row for row in snapshot.mappings}
        records = []
        for position, container in enumerate(snapshot.report.containers):
            mapped = mappings[container.container_index]
            row = UnityContainerRow(
                revision_id=revision.id,
                container_index=container.container_index,
                short_path=container.short_path,
                overhead_bytes=container.overhead_bytes,
                mapping_state=mapped.state,
                apk_paths=list(mapped.apk_paths),
                mapper_version=mapped.mapper_version,
                evidence=mapped.evidence,
            )
            db.add(row)
            db.flush()
            for record_position, record in enumerate(container.entries):
                records.append(
                    {
                        **record.model_dump(),
                        "container_id": row.id,
                        "json_pointer": f"/containers/{position}/entries/{record_position}",
                    }
                )
        _store(db, UnityRecordRow, records)
        for scope, groups in (
            ("project", snapshot.groups),
            ("container", snapshot.container_groups),
        ):
            _store(
                db,
                UnityGroupRow,
                [
                    {
                        "revision_id": revision.id,
                        "scope": scope,
                        "group_key": group.key,
                        "identity_kind": group.identity_kind,
                        "source_guid": group.source_guid,
                        "source_path": group.source_paths[0] if group.source_paths else None,
                        "asset_type": group.asset_type,
                        "source_paths": list(group.source_paths),
                        "container_paths": list(group.container_paths),
                        "packed_bytes": group.packed_bytes,
                        "record_count": group.record_count,
                        "matchable": group.matchable,
                    }
                    for group in groups
                ],
            )
    _store(
        db,
        AssetImporterRow,
        [
            {
                "revision_id": revision.id,
                "source_guid": row["source_guid"],
                "source_path": row["source_path"],
                "importer_type": row["importer_type"],
                "state": row["state"],
                "payload": row,
                "json_pointer": f"/assets/{index}",
            }
            for index, row in enumerate(layers.importer_rows)
        ],
    )


def load_layers(db: Session, revision: AnalysisRevision) -> UnitySnapshot | None:
    summary = (revision.summary or {}).get("unity")
    if (
        not summary
        or summary.get("state") not in {"complete", "failed"}
        or "unity_packed_bytes" not in summary
    ):
        return None
    groups = tuple(
        AssetGroup(
            row.group_key,
            row.identity_kind,
            row.source_guid,
            row.asset_type,
            tuple(row.source_paths),
            tuple(row.container_paths),
            row.packed_bytes,
            row.record_count,
            row.matchable,
        )
        for row in db.scalars(
            select(UnityGroupRow).where(
                UnityGroupRow.revision_id == revision.id, UnityGroupRow.scope == "project"
            )
        )
    )
    importers = tuple(
        row.payload
        for row in db.scalars(
            select(AssetImporterRow)
            .where(AssetImporterRow.revision_id == revision.id)
            .order_by(AssetImporterRow.json_pointer)
        )
    )
    return UnitySnapshot(None, None, groups, (), (), summary, importers)


def persist_comparison(
    db: Session,
    comparison: Comparison,
    base: AnalysisRevision | None,
    target: UnitySnapshot | None,
    target_revision: AnalysisRevision,
) -> None:
    value: dict[str, Any]
    if target is None:
        value = {
            "status": "unavailable",
            "metric": "unity_packed_bytes",
            "delta_packed_bytes": None,
            "additive_to_apk": False,
            "changes": [],
            "importer_changes": [],
        }
    else:
        old = load_layers(db, base) if base else None
        value = compare_unity(old, target)
        if base is not None and old is None:
            value["status"] = "unavailable"
    rows = [
        {
            "comparison_id": comparison.id,
            "kind": "asset",
            "group_key": row["key"],
            "source_guid": row["source_guid"],
            "asset_type": row["asset_type"],
            "status": row["status"],
            "old_packed_bytes": row["old_packed_bytes"],
            "new_packed_bytes": row["new_packed_bytes"],
            "delta_packed_bytes": row["delta_packed_bytes"],
            "details": row,
        }
        for row in value.pop("changes")
    ]
    for row in value.pop("importer_changes"):
        rows.append(
            {
                "comparison_id": comparison.id,
                "kind": "importer",
                "group_key": row["source_guid"],
                "source_guid": row["source_guid"],
                "asset_type": None,
                "status": "CHANGED",
                "old_packed_bytes": None,
                "new_packed_bytes": None,
                "delta_packed_bytes": None,
                "details": row,
            }
        )
    _store(db, UnityChangeRow, rows)
    value["change_count"] = sum(
        row["status"] != "UNCHANGED" and row["kind"] == "asset" for row in rows
    )
    value["importer_change_count"] = (
        sum(row["kind"] == "importer" for row in rows)
        if value.get("importer_comparison_status") in {"complete", "partial"}
        else None
    )
    value["base_revision"] = str(base.id) if base else None
    value["target_revision"] = str(target_revision.id)
    summary = dict(comparison.summary or {})
    summary["unity"] = value
    summary["unity_analysis_status"] = value["status"]
    comparison.summary = summary
