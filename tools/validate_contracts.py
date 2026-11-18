#!/usr/bin/env python3
"""Validate the specification bundle, not application runtime behavior."""
from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

try:
    from jsonschema import Draft202012Validator, FormatChecker
except ImportError:
    raise SystemExit("Install jsonschema 4.x in your Python environment first.")

ROOT = Path(__file__).resolve().parents[1]
PAIRS = {
    "build-meta": "build-meta",
    "unity-report": "unity-report",
    "asset-importers": "asset-importers",
    "git-changes": "git-changes",
    "submission": "submission",
    "finding": "finding",
}
ROLE_FILES = {
    "build_meta": "build-meta.json",
    "unity_report": "unity-report.json",
    "asset_importers": "asset-importers.json",
    "git_changes": "git-changes.json",
}


def read_json(path: Path) -> dict:
    def invalid_constant(value: str) -> None:
        raise ValueError(f"Non-finite JSON number: {value}")

    with path.open(encoding="utf-8") as stream:
        data = json.load(stream, parse_constant=invalid_constant)
    if not isinstance(data, dict):
        raise ValueError(f"Expected object: {path}")
    return data


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def resolve_pointer(document: object, pointer: str) -> object:
    if pointer == "":
        return document
    require(pointer.startswith("/"), f"Invalid JSON pointer: {pointer}")
    cursor = document
    for token in pointer[1:].split("/"):
        token = token.replace("~1", "/").replace("~0", "~")
        cursor = cursor[int(token)] if isinstance(cursor, list) else cursor[token]
    return cursor


def main() -> int:
    examples: dict[str, dict] = {}
    for schema_name, example_name in PAIRS.items():
        schema = read_json(ROOT / "schemas" / f"{schema_name}.schema.json")
        example = read_json(ROOT / "examples" / f"{example_name}.json")
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema, format_checker=FormatChecker()).validate(example)
        examples[example_name] = example

    run_ids = {
        data["run_id"] for data in examples.values() if "run_id" in data
    }
    require(len(run_ids) == 1, "Example run_id values do not match.")
    metadata = examples["build-meta"]
    manifest = examples["submission"]
    for field in ("run_id", "project_key", "variant", "ci"):
        require(manifest[field] == metadata[field], f"Identity mismatch: {field}")

    roles = [artifact["role"] for artifact in manifest["artifacts"]]
    require(len(roles) == len(set(roles)), "Duplicate artifact role.")
    require({"apk", "build_meta"} <= set(roles), "Missing required role.")
    artifact_by_role = {artifact["role"]: artifact for artifact in manifest["artifacts"]}
    verified_payloads = 0
    for role, filename in ROLE_FILES.items():
        expected = artifact_by_role[role]
        content = (ROOT / "examples" / filename).read_bytes()
        require(expected["bytes"] == len(content), f"Byte-length mismatch: {role}")
        require(expected["sha256"] == hashlib.sha256(content).hexdigest(),
                f"SHA-256 mismatch: {role}")
        verified_payloads += 1

    report = examples["unity-report"]
    container_ids = [item["container_index"] for item in report["containers"]]
    require(len(container_ids) == len(set(container_ids)), "Duplicate container index.")
    for container in report["containers"]:
        indices = [item["record_index"] for item in container["entries"]]
        require(len(indices) == len(set(indices)), "Duplicate record index.")
    importer_ids = [item["source_guid"] for item in examples["asset-importers"]["assets"]]
    require(len(importer_ids) == len(set(importer_ids)), "Duplicate importer GUID.")

    for evidence in examples["finding"]["evidence"]:
        role = evidence["artifact_role"]
        require(role in ROLE_FILES, "Example evidence role has no local fixture.")
        require(evidence["artifact_sha256"] == artifact_by_role[role]["sha256"],
                "Evidence SHA-256 mismatch.")
        document = read_json(ROOT / "examples" / ROLE_FILES[role])
        resolve_pointer(document, evidence["locator"])

    required_paths = [
        "README.md", "AGENTS.md", "CODEX_START_PROMPT.md",
        "docs/DEVELOPMENT_SPEC.md", "docs/TASKS.md", "docs/REFERENCE_SOURCES.md",
    ]
    for relative in required_paths:
        require((ROOT / relative).is_file(), f"Missing document: {relative}")

    excluded_directories = {
        ".git", ".venv", "node_modules", ".buildsize", "dist",
        "test-results", "playwright-report",
    }
    markdown_files = [
        path for path in ROOT.rglob("*.md")
        if not excluded_directories.intersection(path.relative_to(ROOT).parts)
    ]
    for path in markdown_files:
        text = path.read_text(encoding="utf-8")
        fences = [line for line in text.splitlines() if re.match(r"^\s*```", line)]
        require(len(fences) % 2 == 0, f"Unbalanced code fences: {path}")
        require("\ufffd" not in text, f"Replacement character in document: {path}")
        require("\x00" not in text, f"NUL in document: {path}")
        for language, body in re.findall(r"```([A-Za-z0-9_-]+)\n(.*?)\n```", text, re.S):
            if language.lower() == "json":
                json.loads(body)

    spec = (ROOT / "docs/DEVELOPMENT_SPEC.md").read_text(encoding="utf-8")
    references = (ROOT / "docs/REFERENCE_SOURCES.md").read_text(encoding="utf-8")
    used_references = set(re.findall(r"\[(R\d{2})\]", spec))
    defined_references = set(re.findall(r"^## (R\d{2})", references, re.M))
    require(used_references <= defined_references, "Unresolved source reference.")

    tasks = (ROOT / "docs/TASKS.md").read_text(encoding="utf-8")
    task_ids = re.findall(r"^\| (M\d-\d{2}) \|", tasks, re.M)
    require(len(task_ids) == len(set(task_ids)), "Duplicate task ID.")
    section_ids = re.findall(r"^## (\d+)\.", spec, re.M)
    require(section_ids == [str(i) for i in range(1, 21)],
            "Specification sections are missing or out of order.")
    acceptance_ids = re.findall(r"^\| (A\d{2}) \|", spec, re.M)
    require(acceptance_ids == [f"A{i:02}" for i in range(1, 33)],
            "Acceptance scenario IDs are missing or out of order.")

    summary = {
        "status": "PASS",
        "schemas_validated": len(PAIRS),
        "examples_validated": len(examples),
        "real_example_payload_hashes_verified": verified_payloads,
        "specification_sections": len(section_ids),
        "development_tasks": len(task_ids),
        "acceptance_scenarios": len(acceptance_ids),
        "official_reference_entries": len(defined_references),
        "markdown_files_checked": len(markdown_files),
        "apk_placeholder_not_validated": True,
        "application_implemented_or_tested": False,
    }
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ValueError, KeyError, IndexError, OSError) as exc:
        raise SystemExit(f"VALIDATION FAILED: {exc}") from exc
