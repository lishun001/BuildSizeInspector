import os
import subprocess
import sys
import tempfile
from pathlib import Path


def test_failed_reports_redact_credentials_without_hiding_failure() -> None:
    sentinel = "temporary-redaction-sentinel-123456"
    with tempfile.TemporaryDirectory(prefix=".redaction-", dir=Path(__file__).parent) as directory:
        root = Path(directory)
        (root / "test_failure.py").write_text(
            "import os, pytest\ndef test_fails():\n"
            '    pytest.fail(os.environ["SESSION_SECRET"] + " bsi_" + "x" * 43)\n'
        )
        report = root / "report.xml"
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                str(root / "test_failure.py"),
                "-q",
                "--tb=short",
                "--junitxml=" + str(report),
            ],
            env={**os.environ, "SESSION_SECRET": sentinel},
            capture_output=True,
            text=True,
        )
        assert result.returncode == 1
        assert sentinel not in result.stdout + result.stderr
        assert sentinel not in report.read_text()
        assert "bsi_" + "x" * 43 not in result.stdout + result.stderr + report.read_text()
        assert "[REDACTED]" in result.stdout
