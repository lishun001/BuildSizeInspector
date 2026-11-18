# Jenkins / CLI 接入

当前 CLI 与本机 HTTP、PostgreSQL、Git 集成可验证；真实 Jenkins 控制器、节点、脚本审批和蒲公英任务尚未验证。Jenkinsfile.example 是合并参考，不能覆盖现有 Jenkinsfile。它保留两个原脚本入口，接入时替换为原流水线对应步骤，不创建假的游戏构建或发布脚本。

## 接入顺序

1. 节点安装 Python 3.12 和已锁定的本项目 CLI。开发环境可运行 `uv sync --project backend --frozen`，将示例中的 `buildsize` 替换成项目绝对路径的 `uv run --project /path/to/BuildSizeInspector/backend --frozen buildsize`。不要在游戏仓里自动安装或升级 Unity。
2. 复制 buildsize-ci.example.json 为游戏的 buildsize-ci.json，改为实际仓库 key/路径、channel、CI 实例标识。所有仓（包括非 submodule 美术仓）先检出，再 prepare。仓库路径相对 --workspace，默认当前目录。
3. API 管理员为项目签发 `builds:read`、`builds:submit`、`distribution:write` scope 的 token，保存到 Jenkins Secret text Credentials。只由 Credentials Binding 注入 BUILDSIZE_API_TOKEN；不设命令行 token 参数，不将值写入配置、回执或归档。
4. API 设置 TRUSTED_LINK_HOSTS 为实际 Jenkins 和蒲公英安装页的精确主机名，例如 `jenkins.example.invalid,www.pgyer.com`。客户端 BUILDSIZE_API_URL 是平台 HTTPS origin（也支持以 /api/v1 结尾）；HTTP 仅限本机测试。
5. 安装同项目的 Editor-only Unity 包，按包 README 接入回调或显式导出。示例从 prepare 的 context 读取 run_id/report_directory，传给原 Unity 进程，无需 jq。DetailedBuildReport 仍需原构建入口显式启用。
6. 原签名/Gradle/后处理结束后指定唯一最终 APK；原蒲公英流程取得最终发布结果后，额外生成白名单结果文件。分析失败仅告警；原游戏/蒲公英失败与用户中止保持原行为。

示例为每个 BUILD_NUMBER/variant 使用独立目录，重复执行同一任务复用它。多 variant 时同时修改 CLI variant 和目录；不要让它们写入同一 context。建议通过游戏仓现有忽略规则排除 .buildsize/，否则报告输出会被正确记录为 dirty；本项目不自动改写游戏仓的忽略规则。

## 命令与冻结语义

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

现有发布流程写入 `$BUILDSIZE_CI_DIR/pgyer-safe-result.json`。参考 pgyer-safe-result.example.json；只有五个允许字段：publish_state/build_key/build_version/install_page_url/published_at，缺失值显式 null。publish_state 为 publishing/published/failed/not_published；只有最终发布成功才能写 published，且必须有 build_key、可信安装页和带时区发布时间。传输成功后发布失败应写 failed，不提供安装成功链接或发布时间。

CLI 从输入提取白名单；API 拒绝未知字段。URL 禁止 userinfo、token/API key/签名参数，不保存临时带凭据下载地址。不要把蒲公英原始响应重定向到归档目录。普通未成功状态可更新为最终成功；已 published 的同内容请求幂等返回，不同结果返回 409，避免重试覆盖另一个发布版本。

示例只归档 context、完成时间、receipt 和 submission 中 JSON；最终 APK 按原有产物制度保存。重试 submit 必须恢复整份冻结目录及原 APK，重试关联只需 receipt 和安全结果。禁止归档令牌、原始蒲公英响应或整个环境。

## 外部验收待办

在实际 Jenkins 上验证控制器/插件/Groovy sandbox、两个 variant/构建号的目录隔离、两次真实任务及以下故障：API 断开、错误 token、上传中断重试、凭据绑定失败、原游戏失败、原发布失败、手动中止和 timeout。核对原发布行为不变，日志/归档无凭据。当前本机 CLI/HTTP/数据库测试不能代替这些验收。
