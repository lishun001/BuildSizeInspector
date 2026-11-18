from pathlib import Path

import pytest

from buildsize.analyzers.android_tools import SandboxedRunner, inspect_android
from buildsize.domain.errors import BuildSizeError


def stub_sandbox_selection(monkeypatch) -> None:
    actual_is_file = Path.is_file
    monkeypatch.setattr("buildsize.analyzers.android_tools.sys.platform", "darwin")
    monkeypatch.setattr(
        Path, "is_file", lambda path: str(path) == "/usr/bin/sandbox-exec" or actual_is_file(path)
    )


def tool_fixture(tmp_path: Path) -> Path:
    root = tmp_path / "sdk tools"
    (root / "bin").mkdir(parents=True)
    (root / "source.properties").write_text("Pkg.Revision = fixture-version\n")
    executable = root / "bin/apkanalyzer"
    executable.write_text("test fixture; never executed")
    executable.chmod(0o700)
    return executable


def test_missing_tools_do_not_break_basic_analysis(tmp_path: Path) -> None:
    assert inspect_android(tmp_path / "a.apk").status == "not_requested"
    assert inspect_android(tmp_path / "a.apk", tmp_path / "missing").status == "unavailable"


def test_argument_arrays_and_verified_values(tmp_path: Path) -> None:
    executable = tool_fixture(tmp_path)
    calls = []

    class FixtureRunner:
        def run(self, command: list[str]) -> str:
            calls.append(command)
            return {
                "application-id": "com.fixture.app",
                "version-code": "123",
                "version-name": "1.2.3",
                "min-sdk": "23",
                "target-sdk": "36",
            }[command[2]]

    apk = tmp_path / "中文 空格 ; $(must-not-execute).apk"
    metadata = inspect_android(apk, executable, runner=FixtureRunner())
    assert metadata.status == "verified"
    assert metadata.version_code == 123
    assert metadata.sdk_tools_version == "fixture-version"
    assert all(call[-1] == str(apk.resolve()) for call in calls)
    assert calls[0] == [str(executable), "manifest", "application-id", str(apk.resolve())]


def test_failure_and_output_redaction(tmp_path: Path) -> None:
    executable = tool_fixture(tmp_path)

    class FailingRunner:
        def run(self, command: list[str]) -> str:
            raise BuildSizeError("TOOL_TIMEOUT", "untrusted output SECRET")

    metadata = inspect_android(tmp_path / "a.apk", executable, runner=FailingRunner())
    assert metadata.status == "failed"
    assert metadata.error_code == "TOOL_TIMEOUT"
    assert "SECRET" not in repr(metadata)


def test_sandbox_wrapper_fails_closed(monkeypatch) -> None:
    monkeypatch.setattr("buildsize.analyzers.android_tools.sys.platform", "unsupported")
    try:
        SandboxedRunner().run(["/not/executed"])
    except BuildSizeError as error:
        assert error.code == "TOOL_SANDBOX_UNAVAILABLE"
    else:
        raise AssertionError("没有沙箱时不能运行工具")


def test_runner_stdout_is_separate_from_stderr(monkeypatch) -> None:
    import subprocess
    import sys

    actual_popen = subprocess.Popen
    launches = []

    def fixture_process(command, **options):
        # 本测试仅替换 OS 沙箱启动；仍运行真实子进程验证管道处理。
        launches.append(command)
        return actual_popen(
            [
                sys.executable,
                "-c",
                'import sys; print("com.fixture.app"); print("JVM options", file=sys.stderr)',
            ],
            **options,
        )

    monkeypatch.setattr("buildsize.analyzers.android_tools.subprocess.Popen", fixture_process)
    stub_sandbox_selection(monkeypatch)
    value = SandboxedRunner().run(
        ["/configured/tool", "manifest", "application-id", "file with spaces"]
    )
    assert value == "com.fixture.app"
    assert launches[0][-4:] == [
        "/configured/tool",
        "manifest",
        "application-id",
        "file with spaces",
    ]
    assert "shell" not in launches[0]


@pytest.mark.parametrize(
    "mode,code", [("timeout", "TOOL_TIMEOUT"), ("output", "TOOL_OUTPUT_LIMIT")]
)
def test_runner_bounded_process(monkeypatch, mode: str, code: str) -> None:
    import subprocess
    import sys

    actual_popen = subprocess.Popen
    child = []

    def fixture_process(command, **options):
        script = (
            "import time; time.sleep(10)"
            if mode == "timeout"
            else ('import sys; sys.stderr.write("x" * 10000); sys.stderr.flush()')
        )
        process = actual_popen([sys.executable, "-c", script], **options)
        child.append(process)
        return process

    monkeypatch.setattr("buildsize.analyzers.android_tools.subprocess.Popen", fixture_process)
    stub_sandbox_selection(monkeypatch)
    with pytest.raises(BuildSizeError) as error:
        SandboxedRunner(timeout_seconds=0.2, max_output_bytes=100).run(["/configured/tool"])
    assert error.value.code == code
    assert child[0].poll() is not None
