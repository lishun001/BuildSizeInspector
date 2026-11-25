# BuildSize Inspector

BuildSize Inspector 用于分析 Unity Android APK 的包体变化、文件差异和 Unity 资源差异。已在 GemCrew 的实际 Jenkins 任务和内网 Linux 服务上验证蒲公英 APK 自动下载、摘要校验和同分支自动比较。[实施状态](docs/IMPLEMENTATION_STATUS.md)

Mac 上的 Unity 插件导出构建报告。Jenkins Shell 经 HTTPS 上报报告、构建元数据、最终 APK 摘要和下载直链。Linux 平台在后台从运维批准的主机下载 APK，校验大小和 SHA-256 后，汇总包体增量、资源变化与证据。

## 接入与配置

### 1. Linux 部署与授权

在 Linux 安装 Docker 和 Compose 插件。从仓库根目录运行下面的命令，替换 BuildSize 的 HTTPS 入口和 Jenkins 实际主机名。HTTPS 入口需能从 Mac 构建节点访问，并转发到 Linux 本机的 `127.0.0.1:8080`。

```bash
bash deploy/deploy.sh \
  --public-url https://buildsize.example.com \
  --jenkins-host jenkins.example.com \
  --apk-download-hosts cos3.pgyer.com,pgyer-apps-cfw.appmeme.com
```

首次运行会生成私有 `.env.deploy`、启动服务、执行迁移并检查就绪状态。已有部署在 `.env.deploy` 修改相应字段后，再运行 `bash deploy/deploy.sh`。[Linux 部署和 HTTPS 说明](deploy/README.md#linux-一键部署)

| Linux 配置 | 填写内容 |
|---|---|
| `PUBLIC_BASE_URL` | Mac 上报和浏览器访问使用的 BuildSize HTTPS 地址，含实际端口 |
| `TRUSTED_LINK_HOSTS` | Jenkins `BUILD_URL` 里的主机名或 IP，省略协议、端口和路径 |
| `APK_DOWNLOAD_HOSTS` | 已核实的 APK 文件主机和跳转主机，逗号分隔。上面的两个蒲公英主机来自实际发布 |

按 [管理员引导](deploy/README.md#管理员引导) 创建网页账号、项目并授权。为同一项目签发 `builds:read` 和 `builds:submit` 权限的 CI Token，将它配置为 Jenkins 上报步骤的 `BUILDSIZE_API_TOKEN`。

```bash
docker compose --env-file .env.deploy -p buildsize -f deploy/compose.yaml \
  exec api buildsize-admin create-token my-game \
  --scope builds:read --scope builds:submit
```

把 `my-game` 换成创建的项目标识，`prepare --project` 也使用同一个值。

### 2. 准备游戏仓库和 Unity 工程

- 把 [unity-report-handoff.py](integrations/jenkins/unity-report-handoff.py) 和 [submit-pgyer.sh](integrations/jenkins/submit-pgyer.sh) 放到游戏 Git 仓库的 `ci/buildsize/`。
- 从 [配置模板](integrations/jenkins/buildsize-ci.example.json) 生成游戏仓库根目录的 `buildsize-ci.json`。单仓任务保留 `path` 为 `.` 的主仓项。`ci.instance_id` 标识 Jenkins 实例，`metadata.channel` 标记构建渠道。分支来自 `BRANCH_NAME`、显式配置或 `GIT_BRANCH`。
- 将 [Unity 导出包](integrations/unity/com.company.buildsize/) 放入 Unity 工程的 `Packages/com.company.buildsize/`，保留 `.meta` 并确认 Git 会检出该目录。在现有 Android 构建入口收到 `BUILDSIZE_EXPORT_MODE=callback` 时，启用 `BuildOptions.DetailedBuildReport`。[Unity 配置](integrations/unity/com.company.buildsize/README.md)
- 将 `.buildsize/` 加入游戏仓库的 `.gitignore`。内网 CA 的公开 PEM 可放在 `ci/buildsize/buildsize-linux-ca.pem`，供 Jenkins 检出使用。

### 3. 配置 Jenkins 的两个 Shell 步骤

**原打包 Shell** 在启动 Unity 前运行 `prepare` 并导出报告目录相关变量。`prepare` 和原 Unity 命令在同一个 Shell 内执行。最终 APK 签名及原打包流程成功后，记录本次 UTC 完成时间到 `completed-at.txt`。[准备代码和时间记录](integrations/jenkins/README.md#mac-构建前生成上下文并导出报告)

**发布后的上报 Shell** 放在 `unity-jenkins-cmds` 的原蒲公英发布步骤之后。中台生成 `.jenkins/pgyer_upload.log` 和 `.env.sh`，上报脚本用其中的本次 `buildKey` 与已有蒲公英凭据取得 APK 文件直链。

下例以 GemCrew 在 `GemCrew/` 下的 Unity 工程为例。Jenkins 先注入 `BUILDSIZE_API_TOKEN`。使用内部 CA 时，`SSL_CERT_FILE` 指向上一步放入游戏仓库的公开 PEM。

```bash
#!/bin/bash
set +x
export BUILDSIZE_API_URL='https://buildsize.example.com'
export SSL_CERT_FILE="$WORKSPACE/ci/buildsize/buildsize-linux-ca.pem"

bash "$WORKSPACE/ci/buildsize/submit-pgyer.sh" \
  "$WORKSPACE/.buildsize/ci/$BUILD_NUMBER/android-release-arm64" \
  "$WORKSPACE/GemCrew/BuildOutput/Android"
```

第一个参数必须与 `prepare` 时使用的构建目录一致。第二个参数填最终 APK 的父目录，该目录直属层需要恰好一个本次发布的 APK。签名链接经 HTTPS 发送到 BuildSize，临时链接文件会在上报后清理。[上报与兼容接入](integrations/jenkins/README.md)

### 4. 验收和清理

先从 Mac 确认 BuildSize HTTPS 可访问。使用上报 Shell 中的地址和内网 CA，预期返回 `{"status":"ready"}`。

```bash
curl -fsS --cacert "$SSL_CERT_FILE" "$BUILDSIZE_API_URL/api/v1/health/ready"
```

1. 跑一次产出 APK 的构建，确认工作区报告目录生成 `capture-complete.json`，Jenkins 日志出现「BuildSize 已接收」。
2. 在 BuildSize 项目页确认 APK 已封存，并检查分析状态。`PARTIAL` 时打开数据完整性查看缺失的分析层。[状态说明](docs/DEVELOPMENT_SPEC.md)
3. 跑第二次同分支、同任务、同兼容配置的构建，验证自动选到基线。构建详情的「Unity 资源」展示当前包的资源清单，比较页的「Unity 资源」展示两包的资源变化。

确认 APK 已封存且分析结束后，可以清理 Jenkins 工作区的临时 APK。Linux 中的归档按 [保留策略](deploy/README.md#清理先计划再复核应用) 管理。

## 本地开发

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

## 基于规格继续开发

平台工程和游戏工程分别维护。修改平台前先阅读 [开发规格](docs/DEVELOPMENT_SPEC.md) 和 [开发任务](docs/TASKS.md)，也可以用 [开发提示词](CODEX_START_PROMPT.md) 启动新的开发会话。

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

本项目从开发规格包开始实施，原始规格和示例继续保留。已验证的接入范围、测试命令及待完成的验收以 [实施状态](docs/IMPLEMENTATION_STATUS.md) 为准。
