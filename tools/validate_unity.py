"""在仓库专属验证工程运行真实 Unity 测试/构建，不访问用户游戏工程。"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / ".buildsize/unity"
PROJECT = WORK / "BuildSizeValidation"
PACKAGE = ROOT / "integrations/unity/com.company.buildsize"
VERSION = "6000.0.76f1"


def command(
    args: list[str], output: Path, environment: dict[str, str], timeout: int = 300
) -> dict[str, Any]:
    with output.open("wb") as stdout, output.with_suffix(".stderr.log").open("wb") as stderr:
        result = subprocess.run(
            ["unity", *args, "--format", "json"],
            env=environment,
            stdout=stdout,
            stderr=stderr,
            cwd=ROOT,
            timeout=timeout,
        )
    value: dict[str, Any] = json.loads(output.read_text())
    for notification in value.get("notifications", []):
        print(notification.get("message", notification.get("code", "Unity 通知")), file=sys.stderr)
    if result.returncode or not value.get("success"):
        raise RuntimeError("Unity 命令失败；查看 .buildsize/unity 下对应结果和日志")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Unity 6000.0.76f1 独立验证；不升级 Editor、不关联云服务"
    )
    parser.add_argument("--build", action="store_true", help="测试通过后实际构建两份 Android APK")
    args = parser.parse_args()
    environment = {
        **os.environ,
        "UNITY_NO_CRASH_REPORT": "1",
        "UNITY_NO_CLI_INVOKED_TELEMETRY": "1",
    }
    WORK.mkdir(parents=True, exist_ok=True)
    try:
        running = command(["editors", "running"], WORK / "running.json", environment)
        rows = running.get("data", {}).get("items", [])
        if (
            any(Path(row.get("projectPath", "")).resolve() == PROJECT for row in rows)
            or (PROJECT / "Temp/UnityLockfile").exists()
        ):
            raise RuntimeError("验证工程正被 Editor 占用；不会关闭或移除用户锁文件")
        marker = PROJECT / ".buildsize-managed-validation"
        if PROJECT.is_symlink() or (PROJECT.exists() and not marker.is_file()):
            raise RuntimeError("已有目录不是本工具登记的验证工程，不覆盖其中内容")
        if not PROJECT.exists():
            command(
                [
                    "projects",
                    "create",
                    PROJECT.name,
                    "--output-dir",
                    str(WORK),
                    "--editor-version",
                    VERSION,
                    "--template",
                    "com.unity.template.urp-blank",
                    "--bare",
                    "--no-cloud",
                    "--non-interactive",
                ],
                WORK / "create-result.json",
                environment,
            )
            marker.write_text("BuildSize Inspector managed validation project\n")
        editor_dir = PROJECT / "Assets/Editor"
        editor_dir.mkdir(parents=True, exist_ok=True)
        for filename in ("BuildSizePackageInstaller.cs", "BuildSizeValidation.cs"):
            shutil.copyfile(
                ROOT / "integrations/unity/validation" / filename, editor_dir / filename
            )
        installed = command(["editors", "--installed"], WORK / "installed.json", environment)
        match = next((row for row in installed["data"]["items"] if row["version"] == VERSION), None)
        if match is None:
            raise RuntimeError("缺少固定版本 Editor；不会自动安装或升级")
        location = Path(match["location"])
        binary = (
            location / "Contents/MacOS/Unity"
            if sys.platform == "darwin"
            else location / "Editor/Unity"
        )
        environment["BUILDSIZE_PACKAGE_PATH"] = str(PACKAGE)
        # 新建/闭合的独立工程，无 Pipeline；UPM 异步安装需保持 Editor 运行，不注入 -quit。
        result = subprocess.run(
            [
                str(binary),
                "-batchmode",
                "-nographics",
                "-projectPath",
                str(PROJECT),
                "-executeMethod",
                "BuildSizePackageInstaller.Install",
                "-logFile",
                str(WORK / "install.log"),
            ],
            env=environment,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=300,
        )
        if result.returncode:
            raise RuntimeError("UPM 安装未成功；查看 install.log，不视为编译通过")
        tests = PROJECT / "Assets/BuildSizeTests"
        tests.mkdir(exist_ok=True)
        shutil.copyfile(PACKAGE / "Tests/Editor/ExporterTests.cs", tests / "ExporterTests.cs")
        (tests / "BuildSize.Validation.Tests.asmdef").write_text(
            json.dumps(
                {
                    "name": "BuildSize.Validation.Tests",
                    "references": ["BuildSize.Editor"],
                    "includePlatforms": ["Editor"],
                    "optionalUnityReferences": ["TestAssemblies"],
                }
            )
        )
        # 本地包不一定在默认 testables 中；只在自建工程显式加载同一测试源，不手改 manifest。
        output = WORK / "editmode.xml"
        output.unlink(missing_ok=True)
        with (
            (WORK / "test-result.json").open("wb") as stdout,
            (WORK / "test.stderr.log").open("wb") as stderr,
        ):
            result = subprocess.run(
                [
                    "unity",
                    "test",
                    str(PROJECT),
                    "--editor-version",
                    VERSION,
                    "--mode",
                    "EditMode",
                    "--filter",
                    "BuildSize.Tests",
                    "--output",
                    str(output),
                    "--timeout",
                    "240",
                    "--format",
                    "json",
                    "--",
                    "-logFile",
                    str(WORK / "editmode.log"),
                ],
                env=environment,
                stdout=stdout,
                stderr=stderr,
                timeout=270,
            )
        if result.returncode or not output.exists():
            raise RuntimeError("Unity 测试未通过；保留失败结果，不当作成功")
        report = ElementTree.parse(output).getroot()
        count = int(report.get("total", "0"))
        if count < 8 or int(report.get("failed", "0")) or int(report.get("passed", "0")) != count:
            raise RuntimeError("Unity 报告没有完整执行至少 8 项验证测试")
        summary: dict[str, Any] = {"unity_version": VERSION, "editmode_passed": count}
        if args.build:
            evidence = WORK / "runs" / uuid.uuid4().hex
            evidence.mkdir(parents=True)
            environment["BUILDSIZE_VALIDATION_OUTPUT"] = str(evidence)
            command(
                [
                    "build",
                    str(PROJECT),
                    "--editor-version",
                    VERSION,
                    "--target",
                    "Android",
                    "--execute-method",
                    "BuildSizeValidation.Run",
                    "--output",
                    str(evidence),
                    "--timeout",
                    "900",
                    "--log-file",
                    str(WORK / "build.log"),
                    "--no-tail",
                ],
                WORK / "build-result.json",
                environment,
                timeout=930,
            )
            for label in ("a", "b"):
                if (
                    not (evidence / label / "fixture.apk").is_file()
                    or not (evidence / label / "capture-complete.json").is_file()
                ):
                    raise RuntimeError("构建没有生成完整 APK/采集回执")
            (WORK / "current-evidence.txt").write_text(str(evidence))
            summary["evidence"] = str(evidence)
        (WORK / "validation.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))
        print(json.dumps(summary, ensure_ascii=False))
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print(str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
