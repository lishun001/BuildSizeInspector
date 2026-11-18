"""只读 Git 采集，NUL 分隔路径，无网络获取、外部 diff 或源码上传。"""

import os
import re
import selectors
import signal
import subprocess
import time
from pathlib import Path
from typing import Any

from buildsize.client.files import ClientError

REVISION = re.compile(r"^(?:[a-f0-9]{40}|[a-f0-9]{64})$")
MAX_OUTPUT = 64 * 1024**2


def git(
    path: Path,
    args: list[str],
    *,
    data: bytes | None = None,
    limit: int = MAX_OUTPUT,
    timeout: int = 30,
) -> tuple[int, bytes]:
    environment = {
        key: os.environ[key]
        for key in ("PATH", "HOME", "SYSTEMROOT", "TMPDIR", "TEMP")
        if key in os.environ
    }
    environment.update(
        GIT_TERMINAL_PROMPT="0",
        GIT_NO_LAZY_FETCH="1",
        GIT_NO_REPLACE_OBJECTS="1",
        GIT_OPTIONAL_LOCKS="0",
        GIT_PAGER="cat",
        LC_ALL="C.UTF-8",
    )
    command = [
        "git",
        "--no-pager",
        "--literal-pathspecs",
        "-c",
        "core.fsmonitor=false",
        "-c",
        "core.hooksPath=/dev/null",
        *args,
    ]
    # batch 输入只含已验证对象 ID，临时文件避免 stdin/stdout 管道相互阻塞。
    import tempfile

    with tempfile.TemporaryFile() as input_stream:
        if data:
            input_stream.write(data)
        input_stream.seek(0)
        with subprocess.Popen(
            command,
            cwd=path,
            env=environment,
            stdin=input_stream,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        ) as process:
            assert process.stdout is not None
            output = bytearray()
            deadline = time.monotonic() + timeout
            try:
                with selectors.DefaultSelector() as selector:
                    selector.register(process.stdout, selectors.EVENT_READ)
                    while selector.get_map():
                        if time.monotonic() >= deadline:
                            raise ClientError("GIT_TIMEOUT", "本地 Git 采集超过期限")
                        for key, _ in selector.select(0.1):
                            chunk = os.read(key.fd, 65536)
                            if not chunk:
                                selector.unregister(key.fileobj)
                                continue
                            output.extend(chunk)
                            if len(output) > limit:
                                raise ClientError(
                                    "GIT_OUTPUT_LIMIT", "Git 输出超过预算，未当作完整差异"
                                )
                process.wait(timeout=max(0.001, deadline - time.monotonic()))
            except BaseException as error:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()
                if isinstance(error, subprocess.TimeoutExpired):
                    raise ClientError("GIT_TIMEOUT", "本地 Git 采集超过期限") from None
                raise
            return process.returncode, bytes(output)


def snapshot(path: Path, repo_key: str, kind: str, relative_path: str) -> dict[str, Any]:
    code, raw = git(path, ["rev-parse", "--verify", "HEAD"])
    revision = raw.decode("ascii").strip()
    if code or not REVISION.fullmatch(revision):
        raise ClientError("GIT_REVISION_UNAVAILABLE", "配置的仓库没有可读取的实际 HEAD")
    code, dirty = git(path, ["status", "--porcelain=v1", "-z", "--untracked-files=normal"])
    if code:
        raise ClientError("GIT_STATUS_FAILED", "无法读取本地仓库状态")
    return {
        "repo_key": repo_key,
        "revision": revision,
        "dirty": bool(dirty),
        "relative_path": relative_path,
        "kind": kind,
    }


def parse_name_status(raw: bytes) -> list[dict[str, Any]]:
    tokens = raw.split(b"\0")
    if tokens[-1] != b"":
        raise ClientError("GIT_DIFF_INVALID", "Git 路径输出缺少完整 NUL 终止")
    tokens.pop()
    changes = []
    index = 0
    while index < len(tokens):
        code = tokens[index].decode("ascii")
        index += 1
        status = code[:1]
        if status not in {"A", "M", "D", "R", "C", "T", "U", "X"}:
            raise ClientError("GIT_DIFF_INVALID", "Git 差异状态无效")
        count = 2 if status in {"R", "C"} else 1
        paths = [item.decode("utf-8") for item in tokens[index : index + count]]
        if len(paths) != count or any(not value for value in paths):
            raise ClientError("GIT_DIFF_INVALID", "Git 差异路径缺失")
        index += count
        changes.append(
            {
                "status": status,
                "old_path": None if status == "A" else paths[0],
                "new_path": None if status == "D" else paths[-1],
                "old_blob": None,
                "new_blob": None,
                "source_guid": None,
            }
        )
    if len(changes) > 100000:
        raise ClientError("GIT_CHANGE_LIMIT", "Git 差异条目超过预算")
    return changes


def tree(path: Path, revision: str) -> dict[str, tuple[str, str, str]]:
    code, raw = git(path, ["ls-tree", "-r", "-z", "--full-tree", revision])
    if code:
        raise ClientError("GIT_TREE_FAILED", "无法读取 Git 树")
    result = {}
    for item in raw.split(b"\0"):
        if not item:
            continue
        header, name = item.split(b"\t", 1)
        mode, kind, sha = header.decode("ascii").split()
        result[name.decode("utf-8")] = (mode, kind, sha)
    return result


def meta_guids(path: Path, objects: set[str]) -> dict[str, str]:
    if not objects:
        return {}
    # 先查大小，仅发送最多 8 KiB 的 .meta 内容；不读取或上传源码正文。
    ordered = sorted(objects)
    data = "".join(sha + "\n" for sha in ordered).encode()
    code, raw = git(path, ["cat-file", "--batch-check"], data=data)
    if code:
        return {}
    small = []
    for line in raw.splitlines():
        parts = line.split()
        if len(parts) == 3 and parts[1] == b"blob" and int(parts[2]) <= 8192:
            small.append(parts[0].decode())
    if not small:
        return {}
    code, raw = git(
        path, ["cat-file", "--batch"], data="".join(sha + "\n" for sha in small).encode()
    )
    if code:
        return {}
    result = {}
    cursor = 0
    for sha in small:
        end = raw.index(b"\n", cursor)
        header = raw[cursor:end].split()
        size = int(header[2])
        content = raw[end + 1 : end + 1 + size]
        cursor = end + 1 + size + 1
        values = re.findall(rb"(?m)^guid:\s*([a-f0-9]{32})\s*$", content)
        if len(values) == 1 and values[0] != b"0" * 32:
            result[sha] = values[0].decode()
    return result


def changes(
    path: Path, repo: dict[str, Any], base: str | None, *, no_baseline: bool = False
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "repo_key": repo["repo_key"],
        "base_revision": base,
        "head_revision": repo["revision"],
        "range_status": "no_baseline" if no_baseline else "failed",
        "is_ancestor": None,
        "changes": [],
        "commit_ids": [],
    }
    if base is None:
        return result
    if not REVISION.fullmatch(base) or not REVISION.fullmatch(repo["revision"]):
        raise ClientError("GIT_REVISION_INVALID", "基线或 HEAD 不是完整对象 ID")
    code, _ = git(path, ["cat-file", "-e", base + "^{commit}"])
    if code:
        result["range_status"] = "missing_base"
        return result
    code, _ = git(path, ["merge-base", "--is-ancestor", base, repo["revision"]])
    if code not in (0, 1):
        return result
    ancestor = code == 0
    code, raw = git(
        path,
        [
            "diff",
            "--no-ext-diff",
            "--no-textconv",
            "--name-status",
            "-z",
            "-M",
            base,
            repo["revision"],
            "--",
        ],
    )
    if code:
        return result
    rows = parse_name_status(raw)
    old, new = tree(path, base), tree(path, repo["revision"])
    meta_objects = set()
    for row in rows:
        for label, data in (("old", old), ("new", new)):
            name = row[label + "_path"]
            entry = data.get(name) if name else None
            row[label + "_blob"] = entry[2] if entry and entry[1] == "blob" else None
            meta = (
                data.get(name if name and name.endswith(".meta") else str(name) + ".meta")
                if name
                else None
            )
            if meta and meta[1] == "blob":
                meta_objects.add(meta[2])
    guids = meta_guids(path, meta_objects)
    for row in rows:
        observed = set()
        for label, data in (("old", old), ("new", new)):
            name = row[label + "_path"]
            entry = (
                data.get(name if name and name.endswith(".meta") else str(name) + ".meta")
                if name
                else None
            )
            if entry and entry[2] in guids:
                observed.add(guids[entry[2]])
        if len(observed) == 1:
            row["source_guid"] = observed.pop()
    code, commits = git(
        path, ["log", "--format=%H", "--max-count=10001", f"{base}..{repo['revision']}"]
    )
    if code or len(commits.splitlines()) > 10000:
        return result
    result.update(
        range_status="complete" if ancestor else "unrelated_history",
        is_ancestor=ancestor,
        changes=rows,
        commit_ids=[value.decode("ascii") for value in commits.splitlines()],
    )
    return result
