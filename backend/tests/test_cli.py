import json
import subprocess
import sys
from pathlib import Path


def run(*args: str):
    return subprocess.run(
        [sys.executable, "-m", "buildsize", *args], capture_output=True, text=True
    )


def test_offline_flow_and_atomic_failure(tmp_path: Path) -> None:
    directory = tmp_path / "演示 空格"
    demo = run("demo", "--directory", str(directory))
    assert demo.returncode == 0
    assert json.loads(demo.stdout)["demo"]
    result = json.loads((directory / "comparison.json").read_text())
    assert result["sample_type"] == "synthetic_zip_not_installable_apk"
    output = tmp_path / "result.json"
    analyzed = run("analyze", str(directory / "old.zip"), "--output", str(output))
    assert analyzed.returncode == 0
    assert analyzed.stdout == ""
    assert json.loads(output.read_text())["android_metadata"]["status"] == "not_requested"
    comparison = run("compare", str(directory / "old.zip"), str(directory / "new.zip"))
    assert comparison.returncode == 0
    assert json.loads(comparison.stdout) == result["comparison"]
    original = output.read_bytes()
    invalid = tmp_path / "bad.zip"
    invalid.write_bytes(b"not a zip")
    failure = run("analyze", str(invalid), "--output", str(output))
    assert failure.returncode == 2
    assert failure.stdout == ""
    assert json.loads(failure.stderr)["error"]["code"] == "ZIP_INVALID_STRUCTURE"
    assert output.read_bytes() == original
    assert not list(tmp_path.glob("*.part"))
    assert run("analyze", str(invalid), "--output", str(invalid)).returncode == 2
    assert invalid.read_bytes() == b"not a zip"


def test_contract_validate_and_help(root: Path) -> None:
    for kind in (
        "submission",
        "build-meta",
        "unity-report",
        "asset-importers",
        "git-changes",
        "finding",
    ):
        response = run("validate", kind, str(root / "examples" / f"{kind}.json"))
        assert response.returncode == 0
        assert json.loads(response.stdout)["status"] == "valid"
    assert run("--help").returncode == 0
    for name in ("analyze", "compare", "validate", "demo"):
        assert run(name, "--help").returncode == 0
    assert run("submit", "--directory", "missing").returncode == 2
