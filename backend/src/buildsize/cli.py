"""离线分析与 CI 采集/上传；stdout 为 JSON，stderr 为脱敏错误。"""

import argparse
import json
import sys
import zipfile
from dataclasses import asdict
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from buildsize.analyzers.android_tools import inspect_android
from buildsize.analyzers.apk_zip import Limits, analyze_apk
from buildsize.client.context import prepare
from buildsize.client.files import ClientError, write_json
from buildsize.client.submission import collect, complete_apk, link_pgyer, status, submit
from buildsize.comparison.apk import compare_apks
from buildsize.domain.contracts import (
    AssetImporters,
    BuildMeta,
    Contract,
    Finding,
    GitChanges,
    Submission,
    UnityReport,
)
from buildsize.domain.errors import BuildSizeError
from buildsize.domain.validation import read_json

MODELS: dict[str, type[Contract]] = {
    "submission": Submission,
    "build-meta": BuildMeta,
    "unity-report": UnityReport,
    "asset-importers": AssetImporters,
    "git-changes": GitChanges,
    "finding": Finding,
}


def _limits(args: argparse.Namespace) -> Limits:
    return Limits(
        max_apk_bytes=args.max_apk_bytes,
        max_entries=args.max_entries,
        max_entry_bytes=args.max_entry_bytes,
        max_total_bytes=args.max_total_bytes,
        timeout_seconds=args.timeout,
    )


def _demo(directory: Path) -> dict[str, Any]:
    directory.mkdir(parents=True, exist_ok=True)
    old = {"assets/bin/Data/main.assets": b"demo-old" * 100, "assets/removed.txt": b"demo"}
    new = {
        "assets/bin/Data/main.assets": b"demo-new" * 200,
        "lib/arm64-v8a/libdemo.so": b"DEMO-ONLY" * 50,
    }
    for name, payload in (("old.zip", old), ("new.zip", new)):
        with zipfile.ZipFile(directory / name, "w", compression=zipfile.ZIP_DEFLATED) as output:
            for path, data in payload.items():
                info = zipfile.ZipInfo(path)
                info.compress_type = zipfile.ZIP_DEFLATED
                output.writestr(info, data)
    comparison = compare_apks(
        analyze_apk(directory / "old.zip"), analyze_apk(directory / "new.zip")
    )
    result = {
        "demo": True,
        "sample_type": "synthetic_zip_not_installable_apk",
        "notice": "演示数据；仅验证 ZIP 算法，不代表真实 APK 或 Unity 构建。",
        "comparison": asdict(comparison),
    }
    write_json(result, directory / "comparison.json")
    return {
        "demo": True,
        "output": str((directory / "comparison.json").resolve()),
        "delta_apk_bytes": comparison.delta_apk_bytes,
    }


def main() -> int:
    import signal

    signal.signal(signal.SIGTERM, lambda signum, frame: sys.exit(143))
    parser = argparse.ArgumentParser(description="BuildSize Inspector 离线分析与 CI 上传工具")
    commands = parser.add_subparsers(dest="command", required=True)
    for name, description in (
        ("analyze", "分析一个本地 ZIP/APK"),
        ("compare", "对比两个本地 ZIP/APK"),
    ):
        command = commands.add_parser(name, help=description)
        if name == "analyze":
            command.add_argument("apk", type=Path)
            command.add_argument(
                "--apkanalyzer", type=Path, help="可选，部署者配置的固定版本 SDK 工具绝对路径"
            )
        else:
            command.add_argument("base", type=Path)
            command.add_argument("target", type=Path)
        command.add_argument("--output", type=Path)
        command.add_argument("--max-apk-bytes", type=int, default=Limits().max_apk_bytes)
        command.add_argument("--max-entries", type=int, default=Limits().max_entries)
        command.add_argument("--max-entry-bytes", type=int, default=Limits().max_entry_bytes)
        command.add_argument("--max-total-bytes", type=int, default=Limits().max_total_bytes)
        command.add_argument("--timeout", type=float, default=Limits().timeout_seconds)
    validate = commands.add_parser("validate", help="校验一份协议 JSON")
    validate.add_argument("kind", choices=sorted(MODELS))
    validate.add_argument("file", type=Path)
    validate.add_argument("--output", type=Path)
    demo = commands.add_parser("demo", help="生成显著标识的独立 ZIP 演示数据")
    demo.add_argument("--directory", type=Path, default=Path(".buildsize/demo"))
    prep = commands.add_parser("prepare", help="准备并复用本次 logical key 上下文")
    prep.add_argument("--project", required=True)
    prep.add_argument("--variant", required=True)
    prep.add_argument("--config", type=Path, default=Path("buildsize-ci.json"))
    prep.add_argument("--workspace", type=Path, default=Path.cwd())
    prep.add_argument("--output", type=Path, required=True)
    gathering = commands.add_parser("collect", help="校验本 run_id 采集回执并冻结产物")
    gathering.add_argument("--context", type=Path, required=True)
    gathering.add_argument("--apk", type=Path, required=True)
    gathering.add_argument("--output", type=Path, required=True)
    gathering.add_argument("--completed-at")
    gathering.add_argument("--completed-at-file", type=Path)
    gathering.add_argument(
        "--pgyer-build-key", help="仅冻结最终 APK 的大小和 SHA-256，供 Linux 补交"
    )
    gathering.add_argument("--timeout", type=float, default=900)
    sending = commands.add_parser("submit", help="恢复角色上传并封存，不等待分析")
    sending.add_argument("--directory", type=Path, required=True)
    sending.add_argument("--receipt", type=Path, required=True)
    sending.add_argument("--reports-only", action="store_true", help="上传报告并登记待补交的 APK")
    sending.add_argument("--timeout", type=float, default=900)
    completing = commands.add_parser("complete-apk", help="核验下载的 APK，按报告回执补交并封存")
    completing.add_argument("--receipt", type=Path, required=True)
    completing.add_argument("--apk", type=Path, required=True)
    completing.add_argument("--timeout", type=float, default=900)
    polling = commands.add_parser("status", help="按明确回执查询当前状态")
    polling.add_argument("--receipt", type=Path, required=True)
    polling.add_argument("--output", type=Path)
    polling.add_argument("--timeout", type=float, default=900)
    linking = commands.add_parser("link-pgyer", help="只关联既有发布流程的最终安全结果")
    linking.add_argument("--receipt", type=Path, required=True)
    linking.add_argument("--result", type=Path, required=True)
    linking.add_argument("--output", type=Path)
    linking.add_argument("--timeout", type=float, default=900)
    args = parser.parse_args()
    try:
        result: dict[str, Any]
        output = getattr(args, "output", None)
        inputs = [getattr(args, name, None) for name in ("apk", "base", "target", "file")]
        if output is not None and any(
            path and path.resolve() == output.resolve() for path in inputs
        ):
            raise BuildSizeError("OUTPUT_CONFLICT", "输出文件不能覆盖输入产物")
        if args.command == "prepare":
            prepare(args.project, args.variant, args.output, args.config, args.workspace)
            return 0
        elif args.command == "collect":
            completed = args.completed_at
            if args.completed_at_file:
                if completed is not None:
                    raise ClientError("COMPLETION_FILE_INVALID", "两种完成时间参数不能同时提供")
                with args.completed_at_file.open("rb") as source:
                    raw = source.read(129)
                if len(raw) > 128:
                    raise ClientError("COMPLETION_FILE_INVALID", "完成时间文件无效")
                completed = raw.decode().strip()
            result = collect(
                args.context,
                args.apk,
                args.output,
                completed,
                args.timeout,
                pgyer_build_key=args.pgyer_build_key,
            )
            write_json(result, None)
            return 0
        elif args.command == "submit":
            result = submit(
                args.directory, args.receipt, args.timeout, reports_only=args.reports_only
            )
            write_json(result, None)
            return 0
        elif args.command == "complete-apk":
            result = complete_apk(args.receipt, args.apk, args.timeout)
        elif args.command == "status":
            result = status(args.receipt, args.timeout)
        elif args.command == "link-pgyer":
            result = link_pgyer(args.receipt, args.result, args.timeout)
        elif args.command == "analyze":
            result = {
                "snapshot": asdict(analyze_apk(args.apk, limits=_limits(args))),
                "android_metadata": asdict(inspect_android(args.apk, args.apkanalyzer)),
            }
        elif args.command == "compare":
            limits = _limits(args)
            result = asdict(
                compare_apks(
                    analyze_apk(args.base, limits=limits), analyze_apk(args.target, limits=limits)
                )
            )
        elif args.command == "validate":
            MODELS[args.kind].model_validate(read_json(args.file))
            result = {"status": "valid", "kind": args.kind}
        else:
            result = _demo(args.directory)
        write_json(result, output)
        return 0
    except BuildSizeError as exc:
        print(
            json.dumps({"error": {"code": exc.code, "message": exc.message}}, ensure_ascii=False),
            file=sys.stderr,
        )
        return exc.exit_code if isinstance(exc, ClientError) else 2
    except (ValueError, ValidationError):
        print('{"error":{"code":"INVALID_INPUT","message":"参数或协议数据无效"}}', file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130
    except OSError:
        print('{"error":{"code":"IO_ERROR","message":"本地文件或 Git 读写失败"}}', file=sys.stderr)
        return 2
