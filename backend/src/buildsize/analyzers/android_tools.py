"""可选 apkanalyzer。没有禁网沙箱时不执行，仅返回不可核验状态。"""

import json
import math
import os
import re
import selectors
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from buildsize.domain.contracts import SAFE_INTEGER
from buildsize.domain.errors import BuildSizeError


class Runner(Protocol):
    def run(self, command: list[str]) -> str: ...


@dataclass(frozen=True)
class SandboxedRunner:
    timeout_seconds: float = 30
    max_output_bytes: int = 64 * 1024
    memory_bytes: int = 2 * 1024**3

    def run(self, command: list[str]) -> str:
        if not command or not Path(command[0]).is_absolute():
            raise ValueError("工具必须使用部署者配置的绝对路径")
        if self.timeout_seconds <= 0 or self.max_output_bytes <= 0 or self.memory_bytes <= 0:
            raise ValueError("工具资源限制必须为正数")
        with tempfile.TemporaryDirectory(prefix="buildsize-tool-") as temporary:
            if sys.platform == "darwin" and Path("/usr/bin/sandbox-exec").is_file():
                policy = "(version 1)(allow default)(deny network*)(deny file-write*)"
                policy += (
                    f"(allow file-write* (subpath {json.dumps(str(Path(temporary).resolve()))}))"
                )
                wrapped = ["/usr/bin/sandbox-exec", "-p", policy, *command]
            elif sys.platform.startswith("linux") and (bwrap := shutil.which("bwrap")):
                wrapped = [
                    bwrap,
                    "--die-with-parent",
                    "--unshare-net",
                    "--unshare-pid",
                    "--ro-bind",
                    "/",
                    "/",
                    "--dev",
                    "/dev",
                    "--proc",
                    "/proc",
                    "--bind",
                    temporary,
                    temporary,
                    "--",
                    *command,
                ]
            else:
                raise BuildSizeError("TOOL_SANDBOX_UNAVAILABLE", "缺少可用的禁网工具沙箱")
            launcher = [
                sys.executable,
                "-m",
                "buildsize.analyzers._tool_process",
                str(self.memory_bytes),
                str(math.ceil(self.timeout_seconds)),
                *wrapped,
            ]
            environment = {
                key: os.environ[key] for key in ("PATH", "JAVA_HOME") if key in os.environ
            }
            environment.update(
                {
                    "HOME": temporary,
                    "TMPDIR": temporary,
                    "LANG": "C.UTF-8",
                    "JAVA_TOOL_OPTIONS": f"-Xmx256m -Djava.io.tmpdir={temporary}",
                }
            )
            output = bytearray()
            output_bytes = 0
            deadline = time.monotonic() + self.timeout_seconds
            with subprocess.Popen(
                launcher,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=environment,
                cwd=temporary,
                start_new_session=True,
            ) as process:
                assert process.stdout is not None and process.stderr is not None
                try:
                    with selectors.DefaultSelector() as selector:
                        selector.register(process.stdout, selectors.EVENT_READ, "stdout")
                        selector.register(process.stderr, selectors.EVENT_READ, "stderr")
                        while selector.get_map():
                            remaining = deadline - time.monotonic()
                            if remaining <= 0:
                                raise BuildSizeError("TOOL_TIMEOUT", "Android 工具超过执行期限")
                            for key, _ in selector.select(min(remaining, 0.1)):
                                data = os.read(key.fd, min(4096, self.max_output_bytes + 1))
                                if not data:
                                    selector.unregister(key.fileobj)
                                    continue
                                output_bytes += len(data)
                                if output_bytes > self.max_output_bytes:
                                    raise BuildSizeError(
                                        "TOOL_OUTPUT_LIMIT", "Android 工具输出超限"
                                    )
                                if key.data == "stdout":
                                    output.extend(data)
                        process.wait(timeout=max(0.001, deadline - time.monotonic()))
                    if process.returncode == 78:
                        raise BuildSizeError(
                            "TOOL_RESOURCE_LIMIT_UNAVAILABLE", "无法设置工具进程硬资源限制"
                        )
                    if process.returncode:
                        raise BuildSizeError("TOOL_FAILED", "Android 工具执行失败")
                except (BaseException, subprocess.TimeoutExpired):
                    # 终止整个进程组，避免 Java/脚本子进程残留。
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    process.wait()
                    raise
            return output.decode("utf-8", errors="strict").strip()


@dataclass(frozen=True)
class AndroidMetadata:
    status: str
    sdk_tools_version: str | None = None
    application_id: str | None = None
    version_code: int | None = None
    version_name: str | None = None
    min_sdk: int | None = None
    target_sdk: int | None = None
    error_code: str | None = None


def inspect_android(
    path: Path, executable: Path | None = None, *, runner: Runner | None = None
) -> AndroidMetadata:
    if executable is None:
        return AndroidMetadata("not_requested")
    executable = executable.resolve()
    if not executable.is_file() or not os.access(executable, os.X_OK):
        return AndroidMetadata("unavailable", error_code="TOOL_NOT_FOUND")
    version = None
    try:
        with (executable.parent.parent / "source.properties").open("rb") as stream:
            properties = stream.read(65537)
        if len(properties) > 65536:
            raise ValueError("SDK 属性文件过大")
        matched = re.search(r"^Pkg.Revision\s*=\s*([^\r\n]+)", properties.decode("utf-8"), re.M)
        if matched is None:
            return AndroidMetadata("unavailable", error_code="TOOL_VERSION_UNKNOWN")
        version = matched[1].strip()
        runner = runner or SandboxedRunner()
        values = {
            field: runner.run([str(executable), "manifest", field, str(path.resolve())])
            for field in ("application-id", "version-code", "version-name", "min-sdk", "target-sdk")
        }
        numbers = {field: int(values[field]) for field in ("version-code", "min-sdk", "target-sdk")}
        if any(value < 0 or value > SAFE_INTEGER for value in numbers.values()):
            raise ValueError("Android 数值超出范围")
        if not values["application-id"] or not values["version-name"]:
            raise ValueError("Android 标识缺失")
        return AndroidMetadata(
            "verified",
            version,
            values["application-id"],
            numbers["version-code"],
            values["version-name"],
            numbers["min-sdk"],
            numbers["target-sdk"],
        )
    except BuildSizeError as exc:
        unavailable = {"TOOL_SANDBOX_UNAVAILABLE", "TOOL_RESOURCE_LIMIT_UNAVAILABLE"}
        return AndroidMetadata(
            "unavailable" if exc.code in unavailable else "failed",
            sdk_tools_version=version,
            error_code=exc.code,
        )
    except (OSError, ValueError, UnicodeError, subprocess.SubprocessError):
        return AndroidMetadata("failed", sdk_tools_version=version, error_code="TOOL_FAILED")
