# BuildSize Inspector

当前已实现 M0–M6 本地核心：协议、鉴权、流式 APK 分析、幂等上传、Celery 恢复、Unity Editor 导出与资源比较、CI CLI、多仓 Git、蒲公英结果关联、12 类证据规则、真实 Web 页面、完整本地容器栈和保留/备份恢复。已完成受控真实 Android 构建及本机 CI 数据流验证；用户实际工程、真实 Jenkins/蒲公英、外部生产部署和指定硬件性能仍待验收，见 [实施状态](docs/IMPLEMENTATION_STATUS.md)。

Mac 上的 Unity 插件导出构建报告。Jenkins Shell 经 HTTPS 上报报告、构建元数据、最终 APK 摘要和下载直链。Linux 平台在后台从运维批准的主机下载 APK，校验大小和 SHA-256 后，汇总包体增量、资源变化与证据。

## 运行当前实现

Linux 部署用 `bash deploy/deploy.sh`。脚本会生成私有配置、启动 Compose 栈并检查就绪状态。给 Mac Jenkins 接入时，可指定已有 HTTPS 入口及 APK 下载主机。[Linux 部署参数](deploy/README.md#linux-一键部署)

本地开发需要 Python 3.12、uv、Node 和 pnpm。后端使用项目独立虚拟环境，不依赖 conda。

完整集成与端到端验证还需要运行中的 Docker；不具备专用服务时，普通 make test 会明确跳过对应场景。

```bash
make install
make test
make lint
make typecheck
make test-postgres-container
make e2e
make demo
make build
make browser-test
make stack-build
make stack-check
```

演示结果位于 `.buildsize/demo/comparison.json`，明确标识为合成 ZIP。真实文件可以离线比较：

```bash
uv run --project backend --frozen buildsize compare /path/to/old.apk /path/to/new.apk --output .buildsize/comparison.json
```

API、Worker、scheduler 启动及备份说明见 [开发与运维](docs/OPERATIONS.md)，接口见 [API](docs/API.md)，可靠性决策见 [ADR-002](docs/adr/ADR-002-upload-and-job-pipeline.md)。Compose 提供开发用 PostgreSQL/Redis；完整应用编排已提供独立 deploy/compose.yaml。

Unity 包位于 integrations/unity/com.company.buildsize，接入与限制见[导出器说明](integrations/unity/com.company.buildsize/README.md)。本机已有 Unity 6000.0.76f1 时，make unity-test 运行 8 项 EditMode，make unity-validate 另实际生成两份 Android APK；不会升级 Editor 或连接云项目。

Shell 上报、下载主机配置和 Jenkins 合并示例见 [CI 接入说明](integrations/jenkins/README.md)。直接上传和 Linux `complete-apk` 仍可用于兼容接入。各路线均按完整 logical key 复用构建身份。

本地页面运行方式见 [Web 说明](frontend/README.md)：设置同源 PUBLIC_BASE_URL 后分别启动 make dev、make worker、make scheduler 和 make web，访问 http://127.0.0.1:5173。页面仅显示已授权项目的真实 API 数据；不含默认账号、CI token 或业务 mock。

完整 Compose 启动、离线备份/空卷恢复与保守清理见 [部署说明](deploy/README.md)。`make benchmark` 可复现明确合成的 500 MiB / 20,000 条目 / 50,000 Unity 记录测量；不将样本结果当作生产 SLA。

## 从这里开始

把本包内容放到目标项目仓库。已有同名文件时合并，不直接覆盖。先阅读 `docs/DEVELOPMENT_SPEC.md`，然后将 `CODEX_START_PROMPT.md` 中的提示词发送给 Codex。

推荐把本包放在新的平台代码仓库，而不是直接放进游戏的 Assets。Unity 使用独立 Editor-only UPM 包接入，CI 示例按实际流程合并。

## 文件说明

| 文件 | 用途 |
|---|---|
| `docs/DEVELOPMENT_SPEC.md` | 主开发规格；范围、架构、算法、数据、接口、Unity/CI、页面、安全、验收 |
| `docs/TASKS.md` | M0–M6 的 35 项开发任务与阶段出口 |
| `AGENTS.md` | 给代码代理的工程约定和不可破坏的数据规则 |
| `CODEX_START_PROMPT.md` | 可直接复制到 Codex 的启动提示词 |
| `schemas/` | 6 份 JSON Schema，固定采集与分析协议 |
| `examples/` | 6 份格式示例，不是真实构建数据 |
| `docs/REFERENCE_SOURCES.md` | 官方 API/工具资料与适配边界 |
| `tools/validate_contracts.py` | 本文档包的 Schema、示例和文件引用校验工具 |
| `docs/VALIDATION_REPORT.md` | 本文档包实际完成的校验，以及未执行的产品验证 |

## 关键决定

首版重点是 APK 精确对账、Unity 报告和 Jenkins 接入，不先做 AI、AAB、IPA 或完整 YooAsset 解析。平台默认 advisory，检测异常不改变原有发包结果。

自动基线必须兼容；Unity packedSize 与 APK 压缩字节必须分层；原因必须有证据；同构建重试必须幂等。

## 校验文档数据

在已安装 `jsonschema` 4.x 的 Python 环境运行：

```bash
python tools/validate_contracts.py
```

该命令只检查开发包的数据协议、样例和引用，不会启动平台，不会连接 Jenkins、Unity 或蒲公英。

示例 submission 中的 APK 长度与全零 SHA-256 是刻意保留的占位值；本包不包含 APK。其他已附 JSON 文件的长度和 SHA-256 与清单一致。不能直接拿该示例提交真实平台。

## 原始文档包与后续边界

本项目从开发规格包开始实施，原始规格和示例继续保留。真实 PostgreSQL/Redis/Celery、受控 Unity APK 和本机 CLI/HTTP 已验证；生产 APK、实际 Jenkins/蒲公英、生产 Web 与性能验收仍待完成。完整 M0–M6 状态以实施状态表为准。
