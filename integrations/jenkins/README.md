# Jenkins / CLI 接入

GemCrew 的自由风格 Jenkins 任务已验证 Unity 报告上报、蒲公英 APK 自动下载、SHA-256 校验和同分支自动比较。[主 README](../../README.md#接入与配置) 提供完整准备清单。使用 Pipeline 时，可将 `Jenkinsfile.example` 中的接入阶段合并到已有流水线，并映射到实际构建和发布入口。

## 现行流程：Jenkins 上报报告和直链，BuildSize 自动下载 APK

```mermaid
flowchart LR
    unity["Mac · Unity Editor 导出本次报告"] --> report["Jenkins Shell · 报告、APK 摘要、下载直链"]
    release["原发布流程 · 本次最终 APK"] --> report
    report -->|"HTTPS"| api["Linux · BuildSize API · 校验报告并排队"]
    api --> worker["Worker · 下载并核对 APK"]
    cdn["已批准的 HTTPS APK 文件主机"] --> worker
    worker --> result["网页 · APK 增量、资源差异、证据与候选原因"]
```

### Linux 一次性配置

1. 在部署配置填写 `APK_DOWNLOAD_HOSTS`，逗号分隔，列出原始直链及其跳转所需的精确主机名。实测蒲公英文件主机有 `cos3.pgyer.com` 和 `pgyer-apps-cfw.appmeme.com`。空值时远程下载关闭。Jenkins 的 `BUILD_URL` 主机单独加入 `TRUSTED_LINK_HOSTS`，填写时省略端口和路径。
2. 在 Linux 执行 `bash deploy/deploy.sh` 加载部署配置并运行迁移。将 Mac 可访问的 BuildSize HTTPS 入口配置为 `PUBLIC_BASE_URL`。[部署参数](../../deploy/README.md#linux-一键部署)
3. 为目标项目签发 `builds:read` 和 `builds:submit` 权限令牌。Jenkins 在任务构建环境用 **Secret text** 绑定为 `BUILDSIZE_API_TOKEN`。把平台 HTTPS 地址配置为 `BUILDSIZE_API_URL`。使用内网 CA 时，在上报 Shell 里导出 `SSL_CERT_FILE`，脚本会显式加载该 PEM 文件进行 TLS 校验。凭据在上传脚本中使用，无需传给 Unity 插件。

```bash
docker compose --env-file .env.deploy -p buildsize -f deploy/compose.yaml \
  exec api buildsize-admin create-token my-game --scope builds:read --scope builds:submit
```

### Mac 构建前：生成上下文并导出报告

Mac Agent 使用 Python 3.8+、Git 和随仓 `unity-report-handoff.py`。将配置模板按实际目录和分支保存为游戏仓库的 `buildsize-ci.json`，模板里的美术仓只是示例。安装 Editor-only Unity 导出包，在原构建入口启用 `BuildOptions.DetailedBuildReport`。自由风格任务把下面的准备代码放在原 Unity 打包 Shell 开头。原 Unity 命令要在**同一步 Shell**中执行。

上报优先读取 Multibranch 的 `BRANCH_NAME`。没有该变量和显式分支配置时，读取 Jenkins Git 插件的 `GIT_BRANCH`，保留 `origin/main` 这类实际值供自动选择基线。[Jenkins Git 插件分支变量](https://plugins.jenkins.io/git/#branch-variables)

```bash
set +x
report_script="$WORKSPACE/ci/buildsize/unity-report-handoff.py"
ci_dir="$WORKSPACE/.buildsize/ci/$BUILD_NUMBER/android-release-arm64"

if prepared=$(python3 "$report_script" prepare --directory "$ci_dir" \
  --workspace "$WORKSPACE" --project my-game --variant android-release-arm64 \
  --config "$WORKSPACE/buildsize-ci.json") &&
  BUILDSIZE_RUN_ID=$(printf '%s' "$prepared" | python3 -c 'import json,sys; print(json.load(sys.stdin)["run_id"])') &&
  BUILDSIZE_REPORT_DIR=$(printf '%s' "$prepared" | python3 -c 'import json,sys; print(json.load(sys.stdin)["report_directory"])'); then
  export BUILDSIZE_RUN_ID BUILDSIZE_REPORT_DIR
  export BUILDSIZE_EXPORT_MODE=callback BUILDSIZE_IMPORTER_COLLECTION=true
else
  status=$?
  case "$status" in 130|143) exit "$status" ;; esac
  printf '%s\n' 'BuildSize 报告准备失败，继续原打包步骤' >&2
fi
```

将 `--project my-game` 改成在 Linux 创建的项目标识。最终签名和后处理成功后，在原打包 Shell 追加时间记录。如果使用封装构建发布的 `jenkins_builder`，等它成功返回后写入。重试上报时使用原记录。

```bash
ci_dir="$WORKSPACE/.buildsize/ci/$BUILD_NUMBER/android-release-arm64"
if [ -s "$ci_dir/context.json" ] && [ ! -s "$ci_dir/completed-at.txt" ]; then
  date -u '+%Y-%m-%dT%H:%M:%SZ' > "$ci_dir/completed-at.txt" ||
    printf '%s\n' 'BuildSize 完成时间写入失败' >&2
fi
```

### Mac 发布后：上报本次报告与下载直链

使用 `unity-jenkins-cmds` 的蒲公英任务，可以在发布成功后的独立 Shell 调用 `submit-pgyer.sh`。将脚本和 `unity-report-handoff.py` 放在游戏仓库同一目录。它读取本次 `.jenkins/pgyer_upload.log` 的 `buildKey`，在子进程中读取中台生成的 `.env.sh`，使用已有 `PGY_API_KEY` 获取同一版本的 APK 文件直链，再提交报告和下载请求。APK 父目录下需要有且仅有本次发布的一个 APK。

```bash
set +x
# 本步骤使用任务里已配置的 BUILDSIZE_API_URL、BUILDSIZE_API_TOKEN 和 SSL_CERT_FILE。
bash "$WORKSPACE/ci/buildsize/submit-pgyer.sh" \
  "$WORKSPACE/.buildsize/ci/$BUILD_NUMBER/android-release-arm64" \
  "$WORKSPACE/GemCrew/BuildOutput/Android"
```

根据游戏的实际输出位置替换父目录。签名直链仅存入本次构建目录下权限为 `0600` 的临时文件，上报结束后清理。Linux 下载白名单需包含实测文件主机。桌面 Linux 与 Mac 环境已实测可获取跳转，但蒲公英官方文档仍将安装 API 限定为 iOS 和 Android，桌面调用应由部署方按实际环境验证。[蒲公英安装 API](https://www.pgyer.com/doc/view/api_install)

其他发布器也可以自行向上报脚本提供文件直链。

现有发布步骤提供**本次最终 APK 的 HTTPS 文件直链**。用原发布逻辑取得实际值并作为 `PGYER_APK_DIRECT_URL`。蒲公英网页安装链接、短链接和仅移动端支持的 `/apiv2/app/install` 不能替代文件直链。Linux 要能在链接有效期内直接读取 APK。不要按应用短链猜测最新版。

发布确认后保存链接文件，关闭 Shell 命令回显。保留本次构建的工作区隔离。

```bash
set +x
ci_dir="$WORKSPACE/.buildsize/ci/$BUILD_NUMBER/android-release-arm64"
if [ -s "$ci_dir/context.json" ] && [ -n "${PGYER_APK_DIRECT_URL:-}" ]; then
  (umask 077; printf '%s\n' "$PGYER_APK_DIRECT_URL" > "$ci_dir/apk-download-url.txt")
fi
```

新增一个「执行 Shell」步骤，替换脚本路径和最终 APK 路径。Jenkins 负责传报告和摘要，APK 字节由后台 Worker 拉取。HTTP 202 表示后台已接受任务。Pipeline 用户可合并 `Jenkinsfile.example`。

```bash
set +x
report_script="$WORKSPACE/ci/buildsize/unity-report-handoff.py"
ci_dir="$WORKSPACE/.buildsize/ci/$BUILD_NUMBER/android-release-arm64"
final_apk="$WORKSPACE/BuildOutput/game.apk"

if python3 "$report_script" submit --directory "$ci_dir" --apk "$final_apk" \
  --apk-url-file "$ci_dir/apk-download-url.txt" \
  --completed-at-file "$ci_dir/completed-at.txt" --receipt "$ci_dir/receipt.json"; then
  printf '%s\n' '报告已上报，服务器已接管 APK 下载'
else
  status=$?
  case "$status" in 130|143) exit "$status" ;; esac
  printf '%s\n' 'BuildSize 上报失败，保留重试资料' >&2
fi
```

允许归档为空，归档本次 `receipt.json`，下载链接文件不归档。该文件可能含临时签名，需要按凭据处理。网页先显示等待下载或下载中，校验完成后继续分析。链接过期或失效时，更新本次链接文件，再执行同一上报步骤。保持报告、APK 摘要、完成时间和构建身份一致；下载失败的任务复用原构建重新排队。相同构建重复上报不会重复创建任务。下载或校验失败时，构建详情会显示对应错误。

### 兼容手工补交

省略 `--apk-url-file` 并提供 `--pgyer-build-key` 可继续用原报告上报、Linux `buildsize complete-apk --receipt /path/to/receipt.json --apk /path/to/downloaded.apk` 的手工流程。两种方式均按本次最终 APK 的长度和 SHA-256 校验。完整 Git 文件差异仍可用已安装 CLI 的兼容采集方式。

## 兼容旧流程：直接提交 APK 和报告

以下示例假设已经通过凭据管理设置 BUILDSIZE_API_URL 和 BUILDSIZE_API_TOKEN，不在 shell 历史里赋令牌值：

```bash
buildsize prepare --project demo-game --variant android-release-arm64 --config buildsize-ci.json --output .buildsize/ci/123/context.json
buildsize collect --context .buildsize/ci/123/context.json --apk "BuildOutput/final signed.apk" --completed-at-file .buildsize/ci/123/completed-at.txt --output .buildsize/ci/123/submission
buildsize submit --directory .buildsize/ci/123/submission --receipt .buildsize/ci/123/receipt.json
buildsize status --receipt .buildsize/ci/123/receipt.json --output .buildsize/ci/123/status.json
buildsize link-pgyer --receipt .buildsize/ci/123/receipt.json --result .buildsize/ci/123/pgyer-safe-result.json
```

prepare 从配置/Jenkins 环境合并 CI 身份，读取各仓实际 HEAD 和 dirty。context 包含传给 Unity 的 environment 信息，不含 API token。相同 logical key 稳定派生 UUIDv5 run_id，context 原子创建、拒绝覆盖不同身份/配置。UUID 只作标识，不作授权凭据。新构建必须使用新目录，不能删除旧 context 然后向同一冻结目录重新采集。

collect 只读本 run_id 的 capture-complete.json 列出文件；核对每份文件的长度、SHA 和 run_id，并在复制后复核。回执缺失时不扫描旧报告；可以只依靠明确受控元数据提交 APK，缺失 Unity 层会降级。无报告时至少须明确 application_id/version_name/version_code/channel/settings.export_mode/build_completed_at；其他未知设置为 null。配置与实际 Unity 设置冲突时拒绝，不静默覆盖。原游戏失败不得调用 collect；已知 Failed/Cancelled 报告也会被拒绝。

完成时间使用带时区的最终 APK/CI 时间，参数 --completed-at 或 --completed-at-file（二选一）、BUILDSIZE_BUILD_COMPLETED_AT、受控配置之间必须一致。最终 CI 时间可晚于 Unity Player 完成时间；原 Unity 时间保留在 unity-settings.json，并记录 warning。文件参数有界读取，避免 shell 引号和换行问题。

第一次 collect 将 APK、build-meta、Git 和回执所列报告复制到独立目录，最后发布 submission.json/collection.json。已冻结目录只能核验复用；重试不会刷新 metadata、run_id、时间戳和哈希。源 APK 改变会冲突，不能复用原身份伪装新内容。不要为“重试”重跑 collect 来补报告；sealed 原始产物不可改写。

submit 每次先完整校验冻结清单与文件，初始化后只传未 verified 的角色；每个未完成文件从头重传，没有字节续传。finalize 返回 202 即退出，不等 Worker。相同提交重试不新增构建/任务。回执绑定准确 API 地址和 build_id；status/link-pgyer 缺少回执即失败，绝不选最近构建。迁移 API 地址后需要人工核对回执，不能把旧回执直接发送到新环境。

stdout 为机器 JSON 或 --output 文件，stderr 为脱敏错误。退出码：0 成功、2 本地数据/参数、3 权限/配置、4 网络/服务、5 哈希/协议冲突；SIGINT=130，SIGTERM=143。网络命令可用 --timeout 调整默认 900 秒总期限；连接/408/429/5xx 最多 3 次，指数退避加 jitter，Retry-After 最多等 30 秒且不得越过总期限。401/403/409/413/422 不盲目重试，不跟随任何重定向。

## Git 证据

采集器只运行本地只读 Git，禁用 hooks/fsmonitor、external diff/textconv 和交互式凭据提示；不 fetch，不上传作者、email、remote、提交正文、源码内容或整个环境。仅在小于等于 8 KiB 的 .meta blob 中读取 GUID。NUL 分隔解析保留中文、空格和 rename 的旧/新路径，额外保留 blob ID。每仓有界 30 秒/64 MiB、最多 100,000 个文件差异和 10,000 个提交；配置最多 200 仓、key 最长 128 字符。不能完整采集时非零退出或明确 failed，不把截断当完整结果。

collect 查询同配置/分支/可信 CI 来源的候选基线；浅克隆缺失对象为 missing_base，不自动访问私有 Git。没有配置 API 时为 failed 加 warning，和确实没有前版的 no_baseline 分开。prepare 后 HEAD 改变拒绝收集；dirty 状态保留，但未提交内容不包含在提交差异里。

服务端冻结比较时再次检查实际 base/head，不依赖客户端的 requested_base_build_id。手动换基线或候选期间历史构建晚到时，范围不匹配的旧证据不能复用。提交列表只说明范围，不能证明某开发者导致增长。建议在缺基线时由原检出流程补齐需要的历史对象，再重跑未冻结的采集，或明确接受 Git 缺失。

## 蒲公英结果

使用 `link-pgyer` 为构建关联安装页时，由发布流程生成 `$BUILDSIZE_CI_DIR/pgyer-safe-result.json`。参考 `pgyer-safe-result.example.json`，文件只包含 `publish_state`、`build_key`、`build_version`、`install_page_url` 和 `published_at`，缺失值为 `null`。最终发布成功时记录 `published`，并提供版本标识、可信安装页和带时区的发布时间。发布失败记录 `failed`。

CLI 从输入提取白名单；API 拒绝未知字段。URL 禁止 userinfo、token/API key/签名参数，不保存临时带凭据下载地址。不要把蒲公英原始响应重定向到归档目录。普通未成功状态可更新为最终成功；已 published 的同内容请求幂等返回，不同结果返回 409，避免重试覆盖另一个发布版本。

示例只归档 context、完成时间和 receipt。分步提交第一次冻结时需读取 Mac 最终 APK，重试报告上报使用已冻结的报告目录，Linux 补交使用回执和下载的 APK。旧流程重试完整 submit 需要整份冻结目录及原 APK。重试关联只需 receipt 和安全结果。禁止归档令牌、原始蒲公英响应或整个环境。

## 外部验收待办

GemCrew 的实际自由风格任务已通过 HTTPS 上报、后台下载和同分支比较验收。其他 Jenkins 项目接入时，仍需检查节点、插件或 Groovy sandbox、不同 variant 和构建号的目录隔离，以及 API 断开、凭据错误、上传中断、原构建或发布失败、手动中止和超时的行为。验收时核对日志和归档中的凭据处理。
