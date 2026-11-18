"""完整 Compose 栈的离线备份/空目标恢复；凭据仅由指定环境文件传入 Compose。"""

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="离线备份/恢复，先停止 web/api/worker/scheduler；不覆盖非空数据"
    )
    parser.add_argument("command", choices=["backup", "restore"])
    parser.add_argument("--project", required=True)
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--directory", type=Path, required=True)
    args = parser.parse_args()
    compose = [
        "docker",
        "compose",
        "--project-name",
        args.project,
        "--env-file",
        str(args.env_file.resolve()),
        "-f",
        str(ROOT / "deploy/compose.yaml"),
    ]

    def run(arguments: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(
            compose + arguments, check=True, stderr=subprocess.PIPE, timeout=900, **kwargs
        )

    try:
        configuration = json.loads(
            run(["config", "--format", "json"], stdout=subprocess.PIPE).stdout
        )
        app_env = configuration["services"]["api"]["environment"]
        db_env = configuration["services"]["postgres"]["environment"]
        url = make_url(app_env["DATABASE_URL"])
        if (
            url.host != "postgres"
            or url.port not in {None, 5432}
            or url.database != "buildsize"
            or url.username != "buildsize"
            or url.password != db_env["POSTGRES_PASSWORD"]
        ):
            raise ValueError("备份仅支持与内置 PostgreSQL 完全一致的连接配置")
        # NDJSON 与 JSON array 均受当前 Compose 支持，避免打印容器环境。
        raw = (
            run(["ps", "--status", "running", "--format", "json"], stdout=subprocess.PIPE)
            .stdout.decode()
            .strip()
        )
        rows = (
            json.loads(raw)
            if raw.startswith("[")
            else [json.loads(line) for line in raw.splitlines() if line]
        )
        if any(row["Service"] in {"api", "web", "worker", "scheduler"} for row in rows):
            raise ValueError("先停止应用服务并暂停外部写入")
        directory = args.directory.resolve()
        if args.command == "backup":
            directory.mkdir(mode=0o700, parents=True, exist_ok=False)
            with (directory / "database.dump").open("xb") as output:
                run(
                    [
                        "exec",
                        "-T",
                        "postgres",
                        "pg_dump",
                        "-U",
                        "buildsize",
                        "-d",
                        "buildsize",
                        "--format=custom",
                        "--no-owner",
                        "--no-privileges",
                    ],
                    stdout=output,
                )
            with (directory / "blobs.tar").open("xb") as output:
                run(
                    [
                        "run",
                        "--rm",
                        "--no-deps",
                        "-T",
                        "api",
                        "python",
                        "-m",
                        "buildsize.backup",
                        "pack",
                    ],
                    stdout=output,
                )
            metadata = {
                name: hashlib.file_digest((directory / name).open("rb"), "sha256").hexdigest()
                for name in ("database.dump", "blobs.tar")
            }
            (directory / "backup.json").write_text(
                json.dumps({"schema_version": 1, "sha256": metadata})
            )
        else:
            metadata = json.loads((directory / "backup.json").read_text())
            if metadata.get("schema_version") != 1:
                raise ValueError("备份版本无效")
            for name in ("database.dump", "blobs.tar"):
                path = directory / name
                if (
                    path.is_symlink()
                    or hashlib.file_digest(path.open("rb"), "sha256").hexdigest()
                    != metadata["sha256"][name]
                ):
                    raise ValueError("备份哈希不符")
            run(["up", "-d", "--wait", "postgres", "redis"], stdout=subprocess.DEVNULL)
            count = run(
                [
                    "exec",
                    "-T",
                    "postgres",
                    "psql",
                    "-U",
                    "buildsize",
                    "-d",
                    "buildsize",
                    "-Atc",
                    "SELECT count(*) FROM pg_tables WHERE schemaname='public'",
                ],
                stdout=subprocess.PIPE,
            ).stdout.strip()
            if count != b"0":
                raise ValueError("目标数据库必须为空，不执行覆盖恢复")
            run(
                [
                    "run",
                    "--rm",
                    "--no-deps",
                    "-T",
                    "api",
                    "python",
                    "-m",
                    "buildsize.backup",
                    "check-empty",
                ],
                stdout=subprocess.DEVNULL,
            )
            with (directory / "database.dump").open("rb") as source:
                run(
                    [
                        "exec",
                        "-T",
                        "postgres",
                        "pg_restore",
                        "-U",
                        "buildsize",
                        "-d",
                        "buildsize",
                        "--exit-on-error",
                        "--no-owner",
                        "--no-privileges",
                    ],
                    stdin=source,
                    stdout=subprocess.DEVNULL,
                )
            with (directory / "blobs.tar").open("rb") as source:
                run(
                    [
                        "run",
                        "--rm",
                        "--no-deps",
                        "-T",
                        "api",
                        "python",
                        "-m",
                        "buildsize.backup",
                        "restore",
                    ],
                    stdin=source,
                    stdout=subprocess.DEVNULL,
                )
        print(json.dumps({"status": "ok", "operation": args.command, "directory": str(directory)}))
        return 0
    except (OSError, ValueError, KeyError, subprocess.SubprocessError):
        print(
            "BACKUP_STACK_FAILED：检查应用已停止、备份完整性及目标为空；未执行覆盖恢复",
            file=sys.stderr,
        )
        return 4


if __name__ == "__main__":
    raise SystemExit(main())
