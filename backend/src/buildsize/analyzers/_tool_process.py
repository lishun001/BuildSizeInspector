"""受控工具的资源限制启动器；不接受来自上传文件的命令。"""

import os
import resource
import sys


def main() -> None:
    memory, cpu = int(sys.argv[1]), int(sys.argv[2])
    try:
        resource.setrlimit(resource.RLIMIT_AS, (memory, memory))
        resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu))
        resource.setrlimit(resource.RLIMIT_FSIZE, (16 * 1024**2, 16 * 1024**2))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    except (ValueError, OSError):
        # 平台不支持硬资源限制时拒绝执行，不静默退回无限制模式。
        raise SystemExit(78) from None
    os.execv(sys.argv[3], sys.argv[3:])


if __name__ == "__main__":
    main()
